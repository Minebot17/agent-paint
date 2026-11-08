"""Performance and memory audit of the Agent Paint engine.

Every scenario runs in a fresh Python process, so memory numbers are not polluted by earlier
scenarios. Memory is the OS view of the process: working set (physical RAM) and private commit.
Transient peaks are sampled by a background thread every ~0.5 ms (numpy/Pillow release the GIL).

    python scripts/perf_audit.py                    # all scenarios, summary JSON to stdout
    python scripts/perf_audit.py --only overhead,flood --out audit.json
    python scripts/perf_audit.py --scenario flood   # one scenario in this process
"""
from __future__ import annotations

import argparse
import collections
import gc
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


# --- process memory --------------------------------------------------------------------------

if os.name == "nt":
    import ctypes
    import ctypes.wintypes as wt

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]

    _k32 = ctypes.WinDLL("kernel32")
    _k32.GetCurrentProcess.restype = wt.HANDLE
    _psapi = ctypes.WinDLL("psapi")
    _psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
    _HANDLE = _k32.GetCurrentProcess()

    def memory():
        c = _PMC(); c.cb = ctypes.sizeof(c)
        _psapi.GetProcessMemoryInfo(_HANDLE, ctypes.byref(c), c.cb)
        return {"ws": c.WorkingSetSize / 2**20, "private": c.PrivateUsage / 2**20}
else:
    def memory():
        fields = {}
        with open("/proc/self/status") as status:
            for line in status:
                key, _, value = line.partition(":")
                if key in {"VmRSS", "RssAnon"}:
                    fields[key] = int(value.split()[0]) / 1024
        return {"ws": fields.get("VmRSS", 0.0), "private": fields.get("RssAnon", fields.get("VmRSS", 0.0))}


class Peak:
    """Max private memory above the starting level while the block runs."""

    def __enter__(self):
        gc.collect()
        self.base = memory()["private"]; self.max = self.base; self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True); self._thread.start()
        self.started = time.perf_counter()
        return self

    def _loop(self):
        while self._run:
            value = memory()["private"]
            if value > self.max: self.max = value
            time.sleep(.0005)

    def __exit__(self, *exc):
        self.seconds = time.perf_counter() - self.started
        self._run = False; self._thread.join()
        self.after = memory()["private"]; self.max = max(self.max, self.after)

    @property
    def transient_mb(self): return round(self.max - self.base, 1)

    @property
    def retained_mb(self): return round(self.after - self.base, 1)


class Profile:
    """Cumulative wall time of selected engine internals (monkeypatched wrappers)."""

    def __init__(self):
        self.seconds = collections.defaultdict(float); self.calls = collections.Counter()

    def wrap(self, owner, name, key=None):
        original = getattr(owner, name); key = key or name
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try: return original(*args, **kwargs)
            finally: self.seconds[key] += time.perf_counter() - start; self.calls[key] += 1
        setattr(owner, name, wrapper)

    def reset(self):
        self.seconds.clear(); self.calls.clear()

    def ms(self, key, per=1): return round(self.seconds[key] * 1000 / per, 2)


def engine_with_profile():
    from csp_headless import engine as engine_module, model, render
    profile = Profile()
    profile.wrap(model.Document, "snapshot")
    profile.wrap(model.Document, "push")
    profile.wrap(engine_module.Engine, "_audit")
    profile.wrap(render, "own_pixels")
    profile.wrap(engine_module, "composite")
    if hasattr(engine_module, "composite_region"):  # region compositing (snapshots of a rectangle)
        profile.wrap(engine_module, "composite_region", "composite")
    state = tempfile.mkdtemp(prefix="apaint-audit-")
    return engine_module.Engine(state), profile, state


def ms(seconds): return round(seconds * 1000, 2)


def random_stroke(rng, w, h, size=None, points=8, reach=.05):
    x, y = rng.uniform(0, w), rng.uniform(0, h); pts = []
    for _ in range(points):
        pts.append([x, y, rng.uniform(.3, 1)])
        x += rng.uniform(-w * reach, w * reach); y += rng.uniform(-h * reach, h * reach)
    return {"points": pts, "brush": {"size": size or rng.uniform(6, 40)}, "color": "#%06X" % rng.randrange(1 << 24),
            "smooth": True}


def tiny_stroke(rng, w, h):
    x, y = rng.uniform(50, w - 50), rng.uniform(50, h - 50)
    return {"points": [[x, y, .5], [x + 20, y + 8, 1], [x + 40, y + 2, .4]], "brush": {"size": 12}, "color": "#203040"}


def make_doc(e, w, h, layers=10, strokes=20, seed=1):
    doc = e.call("paint_document", action="create", width=w, height=h, name=f"bench {w}x{h}", background="#F4EEE2")["document_id"]
    rng = random.Random(seed)
    for i in range(layers - 1):
        e.call("paint_layer", action="create", name=f"L{i + 2}", position="top", document_id=doc)
    if strokes:
        for layer in list(e.documents[doc].layers):
            e.call("paint_strokes", strokes=[random_stroke(rng, w, h) for _ in range(strokes)], layer_id=layer.id, document_id=doc)
    return doc


# --- scenarios ---------------------------------------------------------------------------------

def scenario_import():
    out = {"openblas_threads": os.environ.get("OPENBLAS_NUM_THREADS", "default")}
    out["python"] = memory()
    start = time.perf_counter(); import csp_headless.engine  # noqa: F401
    out["engine_import_s"] = round(time.perf_counter() - start, 3); out["after_engine"] = memory()
    start = time.perf_counter(); import csp_headless.server  # noqa: F401
    out["server_import_s"] = round(time.perf_counter() - start, 3); out["after_server"] = memory()
    out["threads"] = threading.active_count()
    return out


def scenario_doc_memory():
    from csp_headless.engine import Engine
    e = Engine(tempfile.mkdtemp(prefix="apaint-audit-")); out = []
    for w, h in [(1200, 1600), (2048, 1536), (4096, 4096)]:
        gc.collect(); before = memory()
        doc = e.call("paint_document", action="create", width=w, height=h)["document_id"]
        for _ in range(19): e.call("paint_layer", action="create", position="top", document_id=doc)
        gc.collect(); full = memory()
        e.call("paint_document", action="close", document_id=doc, force=True)
        gc.collect(); after = memory()
        per_layer = (full["private"] - before["private"]) / 20
        out.append({"size": [w, h], "megapixels": round(w * h / 1e6, 2), "layers": 20,
                    "private_mb_20_layers": round(full["private"] - before["private"], 1),
                    "mb_per_layer": round(per_layer, 2), "bytes_per_pixel_per_layer": round(per_layer * 2**20 / (w * h), 2),
                    "released_after_close_mb": round(full["private"] - after["private"], 1)})
    return out


def scenario_overhead():
    """Fixed per-call cost of a tiny edit vs canvas size: snapshot, copy-on-write copy, history diff, audit."""
    from csp_headless import render
    from csp_headless.model import Layer
    from PIL import Image
    e, profile, _ = engine_with_profile(); out = []
    for w, h in [(1200, 1600), (2048, 1536), (4096, 4096)]:
        doc = make_doc(e, w, h, layers=10, strokes=10)
        top = e.documents[doc].layers[-1].id
        rng = random.Random(7); n = 30
        e.call("paint_strokes", strokes=[tiny_stroke(rng, w, h)], layer_id=top, document_id=doc)  # warm-up
        gc.collect(); mem0 = memory()["private"]; profile.reset()
        start = time.perf_counter()
        for _ in range(n): e.call("paint_strokes", strokes=[tiny_stroke(rng, w, h)], layer_id=top, document_id=doc)
        separate = time.perf_counter() - start
        gc.collect(); history_mb = memory()["private"] - mem0
        row = {"size": [w, h], "megapixels": round(w * h / 1e6, 2), "layers": 10,
               "separate_call_ms": ms(separate / n),
               "breakdown_ms_per_call": {"snapshot": profile.ms("snapshot", n), "copy_on_write": profile.ms("own_pixels", n),
                                         "push_history_diff": profile.ms("push", n), "audit_log": profile.ms("_audit", n)},
               "history_mb_for_30_calls": round(history_mb, 1)}
        profile.reset(); start = time.perf_counter()
        e.call("paint_batch", actions=[{"tool": "paint_strokes", "arguments": {"strokes": [tiny_stroke(rng, w, h)], "layer_id": top}}
                                       for _ in range(n)], document_id=doc)
        row["in_one_batch_ms_per_stroke"] = ms((time.perf_counter() - start) / n)
        start = time.perf_counter()
        e.call("paint_strokes", strokes=[tiny_stroke(rng, w, h) for _ in range(n)], layer_id=top, document_id=doc)
        row["in_one_call_ms_per_stroke"] = ms((time.perf_counter() - start) / n)
        scratch = Layer("scratch", Image.new("RGBA", (w, h)))
        brush = render.validate_brush({"size": 12}); start = time.perf_counter()
        for _ in range(n): render.paint_stroke(scratch, None, tiny_stroke(rng, w, h)["points"], brush, "#203040")
        row["pure_render_ms_per_stroke"] = ms((time.perf_counter() - start) / n)
        profile.reset(); start = time.perf_counter()
        for i in range(n): e.call("paint_region", name=f"r{i}", rectangle=[0, 0, 64, 64], document_id=doc)
        row["paint_region_define_ms"] = ms((time.perf_counter() - start) / n)
        row["paint_region_breakdown_ms"] = {"snapshot": profile.ms("snapshot", n), "push": profile.ms("push", n)}
        e.call("paint_document", action="close", document_id=doc, force=True); gc.collect()
        out.append(row)
    return out


def scenario_objects():
    """Vector objects: deep copies on every call, uncounted history, re-rendering everything on edit."""
    from csp_headless.model import Document
    e, profile, _ = engine_with_profile(); out = []
    for count in (300, 1000):
        w, h = 1600, 1200
        doc = make_doc(e, w, h, layers=3, strokes=10)
        tiles = e.call("paint_layer", action="create", name="tiles", kind="vector", position="top", document_id=doc)["layer_id"]
        rng = random.Random(3); actions = []
        for _ in range(count):
            x, y = rng.uniform(0, w - 30), rng.uniform(0, h - 30)
            actions.append({"tool": "paint_shape", "arguments": {"shape": "polygon", "layer_id": tiles,
                            "points": [[x, y], [x + 24, y + 2], [x + 22, y + 21], [x + 1, y + 19]], "fill": "#%06X" % rng.randrange(1 << 24)}})
        for i in range(0, count, 256):
            e.call("paint_batch", actions=actions[i:i + 256], document_id=doc)
        raster = e.documents[doc].layers[0].id
        gc.collect(); mem0 = memory()["private"]; profile.reset(); n = 10
        start = time.perf_counter()
        for _ in range(n): e.call("paint_strokes", strokes=[tiny_stroke(rng, w, h)], layer_id=raster, document_id=doc)
        row = {"vector_objects": count, "canvas": [w, h],
               "tiny_raster_stroke_call_ms": ms((time.perf_counter() - start) / n),
               "snapshot_ms_per_call": profile.ms("snapshot", n)}
        gc.collect(); row["memory_growth_mb_per_call"] = round((memory()["private"] - mem0) / n, 2)
        document = e.documents[doc]
        row["history_budget_counted_mb"] = round(sum(Document._history_cost(s) for s in document.undo_stack) / 2**20, 1)
        row["undo_steps"] = len(document.undo_stack)
        first = document.layer(tiles).objects[0]["id"]
        start = time.perf_counter()
        e.call("paint_object", action="update", layer_id=tiles, object_id=first, changes={"fill": "#FF0000"}, document_id=doc)
        row["object_update_ms"] = ms(time.perf_counter() - start)
        with Peak() as peak:
            e.call("paint_transform", translate=[4, 3], layer_id=tiles, document_id=doc)
        row["vector_layer_transform_ms"] = ms(peak.seconds); row["vector_layer_transform_peak_mb"] = peak.transient_mb
        start = time.perf_counter()
        e.call("paint_object", action="update", layer_id=tiles, object_id=first, changes={"fill": "#00FF00"}, document_id=doc)
        row["object_update_after_transform_ms"] = ms(time.perf_counter() - start)
        e.call("paint_document", action="close", document_id=doc, force=True); gc.collect()
        out.append(row)
    return out


def scenario_text():
    e, _, _ = engine_with_profile()
    doc = e.call("paint_document", action="create", width=1600, height=1200)["document_id"]
    layer = None; timings = {}
    for k in range(1, 31):
        start = time.perf_counter()
        result = e.call("paint_text", text=f"Line {k}: Съешь же ещё этих мягких булок", x=40, y=30 * k,
                        style={"font_size": 22}, layer_id=layer, document_id=doc)
        layer = result["layer_id"]
        if k in (1, 10, 20, 30): timings[f"call_{k}_ms"] = ms(time.perf_counter() - start)
    return timings


def scenario_composite():
    from csp_headless.render import composite
    e, _, _ = engine_with_profile(); out = []
    for (w, h), layers in [((2048, 1536), 20), ((4096, 4096), 10)]:
        doc = make_doc(e, w, h, layers=layers, strokes=12)
        document = e.documents[doc]
        composite(document)  # warm-up
        with Peak() as peak: composite(document)
        row = {"size": [w, h], "layers": layers, "normal_ms": ms(peak.seconds), "normal_peak_mb": peak.transient_mb}
        for layer, mode in zip(document.layers[2:5], ("multiply", "overlay", "screen")):
            layer.blend = mode
        with Peak() as peak: composite(document)
        row["three_blend_layers_ms"] = ms(peak.seconds); row["three_blend_layers_peak_mb"] = peak.transient_mb
        e.call("paint_document", action="close", document_id=doc, force=True); gc.collect()
        out.append(row)
    return out


def scenario_snapshot():
    from PIL import Image
    from csp_headless.storage import png
    import hashlib, io
    e, profile, state = engine_with_profile(); w, h = 2048, 1536
    doc = make_doc(e, w, h, layers=20, strokes=12)
    e.call("paint_snapshot", document_id=doc)  # warm-up
    out = {"size": [w, h], "layers": 20}
    profile.reset(); start = time.perf_counter()
    picture = e.call("paint_snapshot", max_side=1600, document_id=doc)
    out["full_snapshot_ms"] = ms(time.perf_counter() - start); out["of_which_composite_ms"] = profile.ms("composite")
    image = picture.image
    start = time.perf_counter(); data = png(image); out["mcp_png_reencode_ms"] = ms(time.perf_counter() - start)
    out["png_bytes"] = len(data); out["base64_bytes_in_mcp_response"] = (len(data) + 2) // 3 * 4
    buffer = io.BytesIO(); start = time.perf_counter(); image.save(buffer, "PNG", compress_level=1)
    out["png_compress_level1_ms"] = ms(time.perf_counter() - start); out["png_compress_level1_bytes"] = buffer.tell()
    start = time.perf_counter(); hashlib.sha256(image.tobytes()).hexdigest(); out["sha256_ms"] = ms(time.perf_counter() - start)
    profile.reset(); start = time.perf_counter()
    e.call("paint_snapshot", rectangle=[900, 700, 1156, 956], document_id=doc)
    out["region_256px_snapshot_ms"] = ms(time.perf_counter() - start); out["region_of_which_composite_ms"] = profile.ms("composite")
    start = time.perf_counter(); e.call("paint_sample", points=[[100, 100]], document_id=doc)
    out["sample_one_pixel_ms"] = ms(time.perf_counter() - start)
    return out


def scenario_flood():
    e, _, _ = engine_with_profile(); w, h = 2048, 1536; out = {"size": [w, h]}
    doc = e.call("paint_document", action="create", width=w, height=h)["document_id"]
    layer = e.documents[doc].layers[0].id
    e.call("paint_shape", shape="ellipse", points=[[524, 268], [1524, 1268]], outline="#000000", width=8, layer_id=layer, document_id=doc)
    for name, point in (("inside", [1024, 768]), ("outside", [20, 20])):
        with Peak() as peak:
            e.call("paint_fill", kind="flood", point=point, color="#3060C0", layer_id=layer, document_id=doc)
        mask = e._flood(point, 0, False, layer)
        out[f"fill_{name}_ms"] = ms(peak.seconds); out[f"fill_{name}_peak_mb"] = peak.transient_mb
        out[f"fill_{name}_megapixels"] = round(sum(mask.histogram()[128:]) / 1e6, 2)
        e.call("paint_history", action="undo", document_id=doc)
    start = time.perf_counter(); e.call("paint_selection", action="flood", point=[20, 20], layer_id=layer, document_id=doc)
    out["selection_flood_outside_ms"] = ms(time.perf_counter() - start)
    return out


def scenario_save_open():
    e, _, state = engine_with_profile(); w, h = 2048, 1536; out = {"size": [w, h], "layers": 20}
    doc = make_doc(e, w, h, layers=20, strokes=15)
    folder = Path(state)
    for label, name in (("save_apaint", "a.apaint"), ("export_png", "a.png"), ("export_psd", "a.psd"), ("export_ora", "a.ora")):
        action = "save" if name.endswith(".apaint") else "export"
        with Peak() as peak:
            e.call("paint_document", action=action, path=str(folder / name), document_id=doc)
        out[f"{label}_ms"] = ms(peak.seconds); out[f"{label}_peak_mb"] = peak.transient_mb
        out[f"{label}_mb_on_disk"] = round((folder / name).stat().st_size / 2**20, 2)
    e.call("paint_document", action="close", document_id=doc, force=True); gc.collect()
    with Peak() as peak: e.call("paint_document", action="open", path=str(folder / "a.apaint"))
    out["open_apaint_ms"] = ms(peak.seconds); out["open_retained_mb"] = peak.retained_mb
    # How much of the save is the outer ZIP deflate of already-compressed PNG members?
    import zipfile, io
    from csp_headless.storage import png
    layers = e.doc.layers
    start = time.perf_counter(); blobs = [png(l.image) for l in layers]; out["png_encode_all_layers_ms"] = ms(time.perf_counter() - start)
    for label, method in (("deflated", zipfile.ZIP_DEFLATED), ("stored", zipfile.ZIP_STORED)):
        buffer = io.BytesIO(); start = time.perf_counter()
        with zipfile.ZipFile(buffer, "w", compression=method) as archive:
            for i, blob in enumerate(blobs): archive.writestr(f"{i}.png", blob)
        out[f"zip_{label}_ms"] = ms(time.perf_counter() - start); out[f"zip_{label}_mb"] = round(buffer.tell() / 2**20, 2)
    return out


def scenario_real_docs():
    """Open every saved painting in paintings/ and docs/ and measure what agents really produce."""
    from csp_headless.render import composite
    e, _, state = engine_with_profile(); rows = []
    files = sorted((ROOT / "paintings").glob("*.apaint"))
    for path in files:
        gc.collect(); before = memory()["private"]; start = time.perf_counter()
        info = e.call("paint_document", action="open", path=str(path))
        opened = time.perf_counter() - start; gc.collect(); retained = memory()["private"] - before
        document = e.doc
        start = time.perf_counter(); composite(document); comp = time.perf_counter() - start
        start = time.perf_counter(); e.call("paint_document", action="save", path=str(Path(state) / path.name), document_id=document.id)
        saved = time.perf_counter() - start
        rows.append({"file": path.name, "megapixels": round(document.width * document.height / 1e6, 2), "layers": len(document.layers),
                     "objects": sum(len(l.objects) for l in document.layers), "file_mb": round(path.stat().st_size / 2**20, 2),
                     "ram_mb": round(retained, 1), "open_ms": ms(opened), "composite_ms": ms(comp), "save_ms": ms(saved)})
        e.call("paint_document", action="close", document_id=document.id, force=True)
    def stats(key):
        values = sorted(r[key] for r in rows)
        return {"min": values[0], "median": values[len(values) // 2], "max": values[-1]} if values else None
    return {"documents": len(rows), **{k: stats(k) for k in ("megapixels", "layers", "objects", "ram_mb", "open_ms", "composite_ms", "save_ms")},
            "rows": rows}


def scenario_multi_doc():
    """Ten agents on one server: ten documents with their undo history."""
    e, _, _ = engine_with_profile(); w, h = 1200, 1600
    gc.collect(); start_mem = memory()["private"]; docs = []
    for i in range(10):
        doc = make_doc(e, w, h, layers=12, strokes=8, seed=i); docs.append(doc)
        rng = random.Random(i)
        top = e.documents[doc].layers[-1].id
        for _ in range(40):
            e.call("paint_strokes", strokes=[random_stroke(rng, w, h, size=20)], layer_id=top, document_id=doc)
    gc.collect(); total = memory()["private"] - start_mem
    raw = 10 * 12 * w * h * 4 / 2**20
    out = {"documents": 10, "size": [w, h], "layers_each": 12, "calls_each": 40, "total_private_mb": round(total, 1),
           "raw_layer_pixels_mb": round(raw, 1), "history_and_other_mb": round(total - raw, 1)}
    if hasattr(e, "park_idle"):  # engines with idle-document parking: everything idle at once
        start = time.perf_counter(); e.park_idle(idle_seconds=0); out["park_all_s"] = round(time.perf_counter() - start, 2)
        gc.collect(); out["total_after_parking_mb"] = round(memory()["private"] - start_mem, 1)
        start = time.perf_counter(); e.call("paint_document", action="info", document_id=docs[0])
        e.call("paint_strokes", strokes=[random_stroke(random.Random(1), w, h)], document_id=docs[0])
        out["first_call_after_parking_ms"] = ms(time.perf_counter() - start)
    for doc in docs: e.call("paint_document", action="close", document_id=doc, force=True)
    gc.collect(); out["still_held_after_closing_all_mb"] = round(memory()["private"] - start_mem, 1)
    return out


def scenario_concurrency():
    """Four agents, four documents: does the server use more than one core?"""
    e, _, _ = engine_with_profile(); w, h = 2048, 1536
    docs = [make_doc(e, w, h, layers=6, strokes=6, seed=i) for i in range(4)]
    def work(doc, seed):
        rng = random.Random(seed); top = e.documents[doc].layers[-1].id
        for _ in range(12):
            e.call("paint_strokes", strokes=[random_stroke(rng, w, h, size=30)], layer_id=top, document_id=doc)
        e.call("paint_snapshot", document_id=doc)
    start = time.perf_counter()
    for i, doc in enumerate(docs): work(doc, i)
    sequential = time.perf_counter() - start
    threads = [threading.Thread(target=work, args=(doc, i + 10)) for i, doc in enumerate(docs)]
    start = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    parallel = time.perf_counter() - start
    return {"agents": 4, "sequential_s": round(sequential, 2), "four_threads_s": round(parallel, 2),
            "speedup": round(sequential / parallel, 2), "logical_cpus": os.cpu_count()}


def scenario_history():
    """Undo budget: full-layer edits, then undo everything into the (unbudgeted) redo stack."""
    from csp_headless.model import Document
    e, _, _ = engine_with_profile(); w, h = 2048, 1536
    doc = make_doc(e, w, h, layers=5, strokes=0)
    layers = [l.id for l in e.documents[doc].layers]
    gc.collect(); base = memory()["private"]
    for i in range(60):
        e.call("paint_fill", kind="linear", start=[0, 0], end=[w, h], color="#%06X" % (i * 40503 & 0xFFFFFF), color2="#FFFFFF",
               layer_id=layers[i % 5], document_id=doc)
    document = e.documents[doc]; gc.collect()
    out = {"size": [w, h], "full_layer_edits": 60, "undo_steps_kept": len(document.undo_stack),
           "budget_counted_mb": round(sum(Document._history_cost(s) for s in document.undo_stack) / 2**20, 1),
           "private_growth_mb": round(memory()["private"] - base, 1)}
    steps = len(document.undo_stack)
    e.call("paint_history", action="undo", steps=min(100, steps), document_id=doc); gc.collect()
    out["after_undo_all_redo_steps"] = len(document.redo_stack)
    out["redo_counted_mb"] = round(sum(Document._history_cost(s) for s in document.redo_stack) / 2**20, 1)
    out["private_growth_after_undo_mb"] = round(memory()["private"] - base, 1)
    return out


def scenario_audit():
    from csp_headless.engine import Engine
    rng = random.Random(1)
    strokes = [{"points": [[rng.uniform(0, 2000), rng.uniform(0, 2000), .5] for _ in range(50)]} for _ in range(1024)]
    start = time.perf_counter(); encoded = json.dumps({"strokes": strokes}, ensure_ascii=False, default=str)
    return {"big_paint_strokes_args_chars": len(encoded), "json_dumps_ms": ms(time.perf_counter() - start),
            "note": "Engine._audit serialises the full arguments of every call only to measure/hash them"}


def scenario_fonts():
    from csp_headless import render
    from PIL import ImageFont
    out = {}
    start = time.perf_counter(); fonts = render.fonts(); out["font_scan_ms"] = ms(time.perf_counter() - start); out["fonts_found"] = len(fonts)
    biggest = max(fonts, key=lambda p: Path(p).stat().st_size)
    start = time.perf_counter()
    for _ in range(5): ImageFont.truetype(biggest, 24)
    out["biggest_font"] = Path(biggest).name; out["biggest_font_mb"] = round(Path(biggest).stat().st_size / 2**20, 1)
    out["truetype_load_ms_uncached"] = ms((time.perf_counter() - start) / 5)
    e, _, _ = engine_with_profile(); e.call("paint_document", action="create", width=800, height=600)
    start = time.perf_counter(); result = e.call("paint_fonts", supports="Съешь ещё булок")
    out["paint_fonts_supports_ms"] = ms(time.perf_counter() - start); out["fonts_supporting_cyrillic"] = len(result["fonts"])
    return out


SCENARIOS = {name[len("scenario_"):]: fn for name, fn in globals().items() if name.startswith("scenario_")}


def run_child(name, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    started = time.perf_counter()
    proc = subprocess.run([sys.executable, __file__, "--scenario", name], capture_output=True, text=True, env=env, cwd=ROOT)
    if proc.returncode:
        return {"error": proc.stderr[-2000:]}
    return {"seconds": round(time.perf_counter() - started, 1), "result": json.loads(proc.stdout.strip().splitlines()[-1])}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--only", help="comma-separated scenario names")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.scenario:
        result = SCENARIOS[args.scenario]()
        print(json.dumps(result, ensure_ascii=False))
        return
    names = args.only.split(",") if args.only else list(SCENARIOS)
    report = {}
    for name in names:
        print(f"[{name}] ...", file=sys.stderr, flush=True)
        report[name] = run_child(name)
        if name == "import":
            report["import_openblas_1"] = run_child(name, {"OPENBLAS_NUM_THREADS": "1"})
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out: args.out.write_text(text, "utf-8")
    print(text)


if __name__ == "__main__":
    main()
