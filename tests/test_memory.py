"""Memory bounds of a shared server: one history budget for all documents, idle documents parked."""
import threading
import time

import pytest

import csp_headless.engine as engine_module
from csp_headless.engine import Engine
from csp_headless.model import Document, HISTORY_SEQ, ParkedImage
from csp_headless.render import composite


def new_document(e, name, size=(200, 150)):
    return e.call("paint_document", action="create", width=size[0], height=size[1], name=name, background="#FFFFFF")["document_id"]


def full_edit(e, doc, i):
    """Changes the whole layer, so history keeps the full previous layer (200*150*4 = 117 KiB)."""
    e.call("paint_fill", kind="linear", start=[0, 0], end=[199, 149], color="#%06X" % (i * 40503 & 0xFFFFFF),
           color2="#FFFFFF", document_id=doc)


def history_bytes(e):
    return sum(Document._history_cost(entry) for d in e.documents.values() for entry in d.undo_stack + d.redo_stack)


def test_history_budget_is_shared_by_all_documents(tmp_path):
    e = Engine(tmp_path / "state", history_mb=1, park_minutes=0)
    a, b = new_document(e, "a"), new_document(e, "b")
    states = {a: [], b: []}
    for i in range(14):
        for doc in (a, b):
            full_edit(e, doc, i)
            states[doc].append(composite(e.documents[doc]).tobytes())
    assert history_bytes(e) <= 1024 * 1024
    for doc in (a, b):
        stack = e.documents[doc].undo_stack
        assert 1 <= len(stack) < 14  # trimmed, but every document keeps its latest steps
    # The engine-wide oldest steps went first: what remains is the newest part of the joint sequence.
    kept = sorted(entry[HISTORY_SEQ] for d in (a, b) for entry in e.documents[d].undo_stack)
    assert kept == list(range(kept[0], kept[0] + len(kept))) or len(kept) <= 3
    for doc in (a, b):
        steps = len(e.documents[doc].undo_stack)
        e.call("paint_history", action="undo", steps=steps - 1, document_id=doc)
        assert composite(e.documents[doc]).tobytes() == states[doc][-steps]


def test_vector_objects_count_toward_history(tmp_path):
    e = Engine(tmp_path / "state", park_minutes=0)
    doc = new_document(e, "v")
    e.call("paint_layer", action="create", name="tiles", kind="vector", document_id=doc)
    actions = [{"tool": "paint_shape", "arguments": {"shape": "polygon", "layer_id": "tiles", "fill": "#336699",
                "points": [[i % 180, i // 2 % 140], [i % 180 + 9, i // 2 % 140], [i % 180 + 4, i // 2 % 140 + 8]]}} for i in range(250)]
    e.call("paint_batch", actions=actions, document_id=doc)
    e.call("paint_region", name="r", rectangle=[0, 0, 10, 10], document_id=doc)  # no pixel change at all
    entry = e.documents[doc].undo_stack[-1]
    assert Document._history_cost(entry) > 250 * 400


def test_idle_documents_are_parked_and_restored_exactly(tmp_path):
    e = Engine(tmp_path / "state", park_minutes=0)
    doc = new_document(e, "p", size=(320, 240))
    e.call("paint_strokes", strokes=[{"points": [[20, 20], [300, 200]], "color": "#AA3300"}], brush={"size": 30}, document_id=doc)
    e.call("paint_selection", action="ellipse", rectangle=[40, 40, 280, 200], document_id=doc)
    e.call("paint_layer", action="mask_from_selection", document_id=doc)
    e.call("paint_selection", action="none", document_id=doc)
    e.call("paint_layer", action="create", name="vec", kind="vector", document_id=doc)
    e.call("paint_shape", shape="ellipse", points=[[100, 60], [220, 180]], fill="#3366CC", layer_id="vec", document_id=doc)
    e.call("paint_text", text="Привет", x=10, y=200, style={"font_size": 20}, document_id=doc)
    other = new_document(e, "other")
    before, undo = composite(e.documents[doc]).tobytes(), len(e.documents[doc].undo_stack)
    raw = sum(l.image.width * l.image.height * 4 for l in e.documents[doc].layers)
    assert set(e.park_idle(idle_seconds=0)) == {doc, other}
    parked = e.documents[doc]
    assert parked.parked and all(isinstance(l.image, ParkedImage) for l in parked.layers)
    assert isinstance(parked.layers[0].mask, ParkedImage)
    assert sum(len(l.image.data) for l in parked.layers) < raw / 10
    listed = {d["document_id"]: d for d in e.call("paint_document", action="list")["documents"]}
    assert listed[doc]["parked"] and e.call("paint_state")["documents"] and parked.parked  # reading does not unpark
    assert e.call("paint_document", action="info", document_id=doc)["undo"] == undo and parked.parked
    e.call("paint_strokes", strokes=[{"points": [[10, 230], [310, 230]]}], layer_id=parked.layers[0].id,
           document_id=doc)  # unparks transparently
    assert not parked.parked
    e.call("paint_history", action="undo", document_id=doc)
    assert composite(parked).tobytes() == before
    e.call("paint_history", action="undo", steps=3, document_id=doc)  # history kept across parking
    e.call("paint_history", action="redo", steps=3, document_id=doc)
    assert composite(parked).tobytes() == before
    e.call("paint_document", action="save", path=str(tmp_path / "p.apaint"), document_id=other)
    assert not e.documents[other].parked
    e.park_idle(idle_seconds=0)
    e.call("paint_document", action="close", document_id=doc, force=True)
    assert doc not in e.documents


def test_busy_documents_are_not_parked(tmp_path, monkeypatch):
    e = Engine(tmp_path / "state", park_minutes=0)
    doc = new_document(e, "busy")
    entered, release = threading.Event(), threading.Event()
    native = engine_module.paint_stroke
    def slow(*args, **kwargs):
        entered.set(); assert release.wait(10)
        return native(*args, **kwargs)
    monkeypatch.setattr(engine_module, "paint_stroke", slow)
    worker = threading.Thread(target=lambda: e.call("paint_strokes", strokes=[{"points": [[5, 5], [90, 90]]}], document_id=doc))
    worker.start(); assert entered.wait(5)
    assert e.park_idle(idle_seconds=0) == []
    release.set(); worker.join(5)
    assert not e.documents[doc].parked and e.documents[doc].revision == 1


def test_background_parker_parks_idle_documents(tmp_path):
    e = Engine(tmp_path / "state", park_minutes=.002)  # 0.12 s
    doc = new_document(e, "idle")
    deadline = time.monotonic() + 5
    while not e.documents[doc].parked and time.monotonic() < deadline:
        time.sleep(.05)
    assert e.documents[doc].parked
    e.call("paint_fill", color="#123456", document_id=doc)
    assert e.documents[doc].layers[0].image.getpixel((1, 1)) == (0x12, 0x34, 0x56, 255)
    e.shutdown()
