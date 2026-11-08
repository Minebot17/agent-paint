"""Several agents on one engine: documents are independent, calls on one document stay serialized."""
import random
import threading

import pytest

import csp_headless.engine as engine_module
from csp_headless.engine import Engine
from csp_headless.render import composite


@pytest.fixture
def e(tmp_path):
    return Engine(tmp_path / "state")


def new_document(e, name, size=(160, 120)):
    return e.call("paint_document", action="create", width=size[0], height=size[1], name=name, background="#FFFFFF")["document_id"]


@pytest.fixture
def blocker(monkeypatch):
    """paint_stroke waits for `release` when called from a thread named 'blocker'."""
    entered, release = threading.Event(), threading.Event()
    native = engine_module.paint_stroke
    def maybe_wait(*args, **kwargs):
        if threading.current_thread().name.startswith("blocker"):
            entered.set(); assert release.wait(10)
        return native(*args, **kwargs)
    monkeypatch.setattr(engine_module, "paint_stroke", maybe_wait)
    yield entered, release
    release.set()


def run(target, name="worker"):
    outcome = {}
    def body():
        try: outcome["result"] = target()
        except BaseException as error: outcome["error"] = error
    thread = threading.Thread(target=body, name=name, daemon=True)
    thread.start()
    return thread, outcome


STROKE = [{"points": [[20, 20], [140, 100]], "color": "#AA2200"}]


def test_other_documents_are_not_blocked_by_a_long_operation(e, blocker):
    entered, release = blocker
    a, b = new_document(e, "a"), new_document(e, "b")
    slow, slow_outcome = run(lambda: e.call("paint_strokes", strokes=STROKE, document_id=a), "blocker")
    assert entered.wait(5)
    other, outcome = run(lambda: e.call("paint_strokes", strokes=STROKE, document_id=b))
    other.join(5)
    assert not other.is_alive() and "error" not in outcome, "a call on another document waited for the busy one"
    assert slow.is_alive()
    release.set(); slow.join(5)
    assert "error" not in slow_outcome and e.documents[a].revision == 1 and e.documents[b].revision == 1


def test_calls_on_one_document_still_wait_for_each_other(e, blocker):
    entered, release = blocker
    a = new_document(e, "a")
    slow, _ = run(lambda: e.call("paint_strokes", strokes=STROKE, document_id=a), "blocker")
    assert entered.wait(5)
    queued, outcome = run(lambda: e.call("paint_fill", color="#0000FF", document_id=a))
    queued.join(.3)
    assert queued.is_alive(), "two calls on one document overlapped"
    release.set(); slow.join(5); queued.join(5)
    assert "error" not in outcome and e.documents[a].revision == 2


def test_abort_reaches_every_active_operation(e, blocker):
    entered, release = blocker
    a, b = new_document(e, "a"), new_document(e, "b")
    first, first_outcome = run(lambda: e.call("paint_strokes", strokes=STROKE, document_id=a), "blocker-a")
    assert entered.wait(5); entered.clear()
    second, second_outcome = run(lambda: e.call("paint_strokes", strokes=STROKE, document_id=b), "blocker-b")
    assert entered.wait(5)
    assert e.call("paint_abort")["abort_requested"]
    release.set(); first.join(5); second.join(5)
    for outcome, doc in ((first_outcome, a), (second_outcome, b)):
        assert isinstance(outcome.get("error"), InterruptedError)
        assert e.documents[doc].revision == 0 and e.documents[doc].layers[0].image.getbbox() is None
    e.call("paint_strokes", strokes=STROKE, document_id=a)  # a later call is not aborted
    assert e.documents[a].revision == 1


def test_close_waits_for_the_active_operation(e, blocker):
    entered, release = blocker
    a = new_document(e, "a"); new_document(e, "b")
    slow, slow_outcome = run(lambda: e.call("paint_strokes", strokes=STROKE, document_id=a), "blocker")
    assert entered.wait(5)
    closing, outcome = run(lambda: e.call("paint_document", action="close", document_id=a, force=True))
    closing.join(.3)
    assert closing.is_alive()
    release.set(); slow.join(5); closing.join(5)
    assert "error" not in slow_outcome and "error" not in outcome and a not in e.documents


def test_switching_the_active_document_does_not_redirect_a_running_call(e, blocker):
    entered, release = blocker
    a = new_document(e, "a"); b = new_document(e, "b")
    e.call("paint_document", action="select", document_id=a)
    slow, slow_outcome = run(lambda: e.call("paint_strokes", strokes=STROKE), "blocker")
    assert entered.wait(5)
    switch, outcome = run(lambda: e.call("paint_document", action="select", document_id=b))
    switch.join(5)
    assert not switch.is_alive() and "error" not in outcome
    release.set(); slow.join(5)
    assert e.documents[a].layers[0].image.getbbox() is not None and e.documents[b].layers[0].image.getbbox() is None
    assert e.active_document == b


def agent_plan(seed):
    rng = random.Random(seed); plan = []
    for step in range(30):
        r = rng.random()
        if r < .5:
            plan.append(("paint_strokes", {"strokes": [{"points": [[rng.uniform(0, 160), rng.uniform(0, 120)] for _ in range(3)],
                                                       "color": "#%06X" % rng.randrange(1 << 24)}], "brush": {"size": rng.uniform(3, 30)}}))
        elif r < .6: plan.append(("paint_layer", {"action": "create", "name": f"l{step}"}))
        elif r < .7: plan.append(("paint_fill", {"color": "#%06X" % rng.randrange(1 << 24), "opacity": .25}))
        elif r < .8: plan.append(("paint_filter", {"kind": "blur", "radius": 1.5}))
        elif r < .9: plan.append(("paint_selection", {"action": "rectangle", "rectangle": [10, 10, rng.randrange(20, 160), rng.randrange(20, 120)]}))
        else: plan.append(("paint_selection", {"action": "none"}))
        if rng.random() < .1: plan.append(("paint_history", {"action": "undo"}))
    return plan


def test_parallel_agents_match_a_sequential_replay(tmp_path):
    plans = {f"agent{i}": agent_plan(i) for i in range(4)}
    def replay(engine, name, document):
        for tool, arguments in plans[name]:
            engine.call(tool, document_id=document, **arguments)
    parallel = Engine(tmp_path / "parallel")
    documents = {name: new_document(parallel, name) for name in plans}
    stop = threading.Event(); errors = []
    def observer():  # engine-wide reads while agents paint
        while not stop.is_set():
            try: parallel.call("paint_document", action="list"); parallel.call("paint_state")
            except Exception as error: errors.append(error)
    watcher = threading.Thread(target=observer, daemon=True); watcher.start()
    agents = [threading.Thread(target=replay, args=(parallel, name, documents[name])) for name in plans]
    for thread in agents: thread.start()
    for thread in agents: thread.join(60)
    stop.set(); watcher.join(5)
    assert not errors
    sequential = Engine(tmp_path / "sequential")
    for name in plans:
        document = new_document(sequential, name)
        replay(sequential, name, document)
        assert composite(sequential.documents[document]).tobytes() == composite(parallel.documents[documents[name]]).tobytes(), name
