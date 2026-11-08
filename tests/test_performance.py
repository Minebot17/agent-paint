"""Performance work must not change pixels: invariants of the optimised paths."""
import os
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from csp_headless import model
from csp_headless.engine import Engine
from csp_headless.render import composite


@pytest.fixture
def e(tmp_path):
    engine = Engine(tmp_path / "state")
    engine.call("paint_document", action="create", width=320, height=240, background="#F0E8D8")
    engine.call("paint_layer", action="create", name="a")
    engine.call("paint_layer", action="create", name="b")
    return engine


def state(e):
    return composite(e.doc).tobytes()


# --- 1. OpenBLAS -------------------------------------------------------------------------------------

@pytest.mark.parametrize("preset,expected", [(None, "1"), ("4", "4")])
def test_openblas_threads_default_to_one(preset, expected):
    env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
    if preset: env["OPENBLAS_NUM_THREADS"] = preset
    code = "import os, csp_headless.engine; print(os.environ['OPENBLAS_NUM_THREADS'])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True).stdout
    assert out.strip() == expected


# --- 2. History from written rectangles --------------------------------------------------------------

def test_in_place_edits_skip_full_layer_comparison(e, monkeypatch):
    def forbidden(*args): raise AssertionError("full-layer comparison for an in-place edit")
    monkeypatch.setattr(model, "_changed_box", forbidden)
    states = [state(e)]
    e.call("paint_strokes", strokes=[{"points": [[20, 20], [120, 90]], "color": "#AA2200"}], brush={"size": 9}, layer_id="a")
    states.append(state(e))
    e.call("paint_strokes", strokes=[{"points": [[30, 60], [200, 60]]}], brush={"kind": "smudge", "size": 16}, layer_id="a")
    states.append(state(e))
    e.call("paint_strokes", strokes=[{"points": [[60, 20], [60, 200]]}], brush={"kind": "eraser", "size": 7}, layer_id="a")
    states.append(state(e))
    e.call("paint_selection", action="rectangle", rectangle=[100, 100, 260, 200])
    e.call("paint_fill", color="#2244AA", opacity=.5, layer_id="b")
    e.call("paint_pattern", kind="hatch", spacing=6, layer_id="b")
    states.append(state(e))
    e.call("paint_shape", shape="ellipse", points=[[10, 150], [90, 230]], fill="#118844", layer_id="a")
    states.append(state(e))
    monkeypatch.undo()
    while e.doc.undo_stack and state(e) != states[0]:
        e.call("paint_history", action="undo")
    assert state(e) == states[0]
    while e.doc.redo_stack:
        e.call("paint_history", action="redo")
    assert state(e) == states[-1]


def test_small_stroke_on_big_layer_keeps_a_small_patch(tmp_path):
    e = Engine(tmp_path / "state")
    e.call("paint_document", action="create", width=2000, height=2000)
    e.call("paint_strokes", strokes=[{"points": [[1000, 1000], [1040, 1010]]}], brush={"size": 8})
    record = e.doc.undo_stack[-1]["layers"][0]._history["image"]
    box, patch = record
    assert patch.width * patch.height < .001 * 2000 * 2000
    assert box[0] <= 1000 - 4 and box[2] >= 1040 + 4


def test_mixed_in_place_and_replacing_writes_in_one_batch(e):
    e.call("paint_strokes", strokes=[{"points": [[40, 40], [200, 160]]}], brush={"size": 12}, layer_id="a")
    before = state(e)
    e.call("paint_batch", actions=[
        {"tool": "paint_strokes", "arguments": {"strokes": [{"points": [[10, 200], [300, 20]]}], "brush": {"size": 6}, "layer_id": "a"}},
        {"tool": "paint_filter", "arguments": {"kind": "blur", "radius": 2, "layer_id": "a"}},
        {"tool": "paint_strokes", "arguments": {"strokes": [{"points": [[150, 10], [150, 230]]}], "color": "#0000FF", "layer_id": "a"}},
        {"tool": "paint_strokes", "arguments": {"strokes": [{"points": [[5, 5], [80, 5]]}], "layer_id": "b"}}])
    after = state(e)
    with pytest.raises(ValueError):
        e.call("paint_batch", actions=[
            {"tool": "paint_strokes", "arguments": {"strokes": [{"points": [[0, 0], [319, 239]]}], "brush": {"size": 30}, "layer_id": "a"}},
            {"tool": "paint_fill", "arguments": {"color": "bad"}}])
    assert state(e) == after
    e.call("paint_history", action="undo")
    assert state(e) == before
    e.call("paint_history", action="redo")
    assert state(e) == after


def test_duplicated_layer_never_shares_pixels(e):
    e.call("paint_strokes", strokes=[{"points": [[20, 20], [100, 100]]}], brush={"size": 10}, layer_id="a")
    copy_id = e.call("paint_layer", action="duplicate", layer_id="a")["id"]
    original = e.doc.layer("a").image.tobytes()
    e.call("paint_strokes", strokes=[{"points": [[150, 20], [150, 200]]}], brush={"size": 10}, layer_id=copy_id)
    assert e.doc.layer("a").image.tobytes() == original
    duplicate = e.doc.layer(copy_id).image.tobytes()
    e.call("paint_strokes", strokes=[{"points": [[200, 20], [300, 200]]}], brush={"size": 10}, layer_id="a")
    assert e.doc.layer(copy_id).image.tobytes() == duplicate
    assert "_written" not in e.doc.layer(copy_id).__dict__


def test_history_cost_is_memoised_per_entry(e):
    for i in range(5):
        e.call("paint_strokes", strokes=[{"points": [[10 + i * 30, 10], [10 + i * 30, 200]]}], layer_id="a")
    for entry in e.doc.undo_stack:
        assert entry[model.HISTORY_COST] == model.Document._history_cost(entry)
    e.call("paint_history", action="undo", steps=2)
    assert model.HISTORY_COST not in vars(e.doc)  # restoring an entry must not leak the memo into state


# --- 3. Region compositing ---------------------------------------------------------------------------

def layered_document(e):
    """Layers with opacity, a mask, clipping, hidden layers and every blend mode."""
    from csp_headless.model import BLENDS
    rng = np.random.default_rng(3)
    for i, mode in enumerate(sorted(BLENDS)):
        lid = e.call("paint_layer", action="create", name=f"m{i}", position="top")["layer_id"]
        e.call("paint_strokes", strokes=[{"points": [[float(rng.uniform(0, 320)), float(rng.uniform(0, 240))] for _ in range(4)],
                                          "color": "#%06X" % int(rng.integers(1 << 24))}],
               brush={"size": float(rng.uniform(8, 40))}, layer_id=lid)
        props = {"blend": mode, "opacity": float(rng.choice([1, .6]))}
        if i % 3 == 1: props["clipping"] = True
        if i % 5 == 4: props["visible"] = False
        e.call("paint_layer", action="update", layer_id=lid, properties=props)
    e.call("paint_selection", action="ellipse", rectangle=[40, 30, 260, 210])
    e.call("paint_layer", action="mask_from_selection", layer_id="m2")
    e.call("paint_selection", action="none")


def test_region_composite_equals_crop_of_full_composite(e):
    from csp_headless.render import composite_region
    layered_document(e)
    full = composite(e.doc)
    for box in [(0, 0, 320, 240), (17, 23, 151, 99), (300, 200, 320, 240), (5, 5, 6, 6)]:
        assert composite(e.doc, box=box).tobytes() == full.crop(box).tobytes()
    for box in [(-30, -10, 50, 40), (290, 220, 400, 300), (-5, -5, 330, 250), (400, 400, 410, 420)]:
        assert composite_region(e.doc, box).tobytes() == full.crop(box).tobytes()
    clear = composite(e.doc, transparent=True)
    assert composite_region(e.doc, (10, 10, 200, 150), transparent=True).tobytes() == clear.crop((10, 10, 200, 150)).tobytes()


def test_snapshot_and_sample_composite_only_what_they_need(e, monkeypatch):
    from csp_headless import engine as engine_module, render
    layered_document(e)
    full = composite(e.doc)
    boxes = []
    def spy(original):
        def wrapper(*args, **kwargs):
            boxes.append(kwargs.get("box")); return original(*args, **kwargs)
        return wrapper
    monkeypatch.setattr(render, "composite", spy(render.composite))
    monkeypatch.setattr(engine_module, "composite", spy(engine_module.composite))
    shot = e.call("paint_snapshot", rectangle=[30, 40, 230, 190])
    assert shot.image.tobytes() == full.crop((30, 40, 230, 190)).tobytes()
    few = [[x, y] for x in range(0, 320, 70) for y in range(0, 240, 70)]
    many = [[x, y] for x in range(0, 320, 23) for y in range(0, 240, 23)]
    for points in (few, many):  # per-pixel and bounding-box paths
        samples = e.call("paint_sample", points=points)["samples"]
        assert [tuple(s["rgba"]) for s in samples] == [full.getpixel(tuple(p)) for p in points]
    assert boxes and None not in boxes
