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


# --- 4. Blend modes in chunks within the covered rectangle -------------------------------------------

def reference_blend(bottom, top, mode, opacity=1.0, mask=None):
    """The previous whole-image implementation."""
    from PIL import ImageChops
    from csp_headless import render
    if mask is not None or opacity != 1:
        top = top.copy(); alpha = top.getchannel("A")
        if mask is not None: alpha = ImageChops.multiply(alpha, mask)
        if opacity != 1: alpha = alpha.point(lambda a: round(a * opacity))
        top.putalpha(alpha)
    if mode == "normal": return Image.alpha_composite(bottom, top)
    return render._blend_pixels(bottom, top, mode)


@pytest.mark.parametrize("mode", sorted(model.BLENDS))
def test_chunked_blend_is_bit_identical(mode, monkeypatch):
    from csp_headless import render
    monkeypatch.setattr(render, "BLEND_CHUNK_PIXELS", 700)  # many small chunks
    rng = np.random.default_rng(11)
    bottom = rng.integers(0, 256, (90, 120, 4), dtype=np.uint8)
    bottom[::7, ::5, 3] = 0  # transparent pixels that still store RGB
    top = np.zeros((90, 120, 4), np.uint8); top[20:70, 30:100] = rng.integers(0, 256, (50, 70, 4), dtype=np.uint8)
    bottom, top = Image.fromarray(bottom), Image.fromarray(top)
    mask = Image.fromarray(rng.integers(0, 256, (90, 120), dtype=np.uint8))
    for opacity, m in ((1, None), (.55, None), (1, mask), (.3, mask)):
        assert render.blend(bottom, top, mode, opacity, m).tobytes() == reference_blend(bottom, top, mode, opacity, m).tobytes()
    empty = Image.new("RGBA", (120, 90))
    assert render.blend(bottom, empty, mode).tobytes() == reference_blend(bottom, empty, mode).tobytes()


# --- 5. Retained objects rendered as patches ---------------------------------------------------------

def reference_render_objects(layer):
    """The previous implementation: a canvas-sized image (and transform) per object."""
    from csp_headless import render
    from csp_headless.model import Layer
    size = layer.image.size
    image = Image.new("RGBA", size)
    for obj in layer.objects:
        if obj["type"] == "text":
            im = render.text_image(size, obj)
        elif obj["type"] == "shape":
            if "transform" not in obj:
                box, patch = render.shape_patch(size, obj)
                if box is not None: image.alpha_composite(patch, box[:2])
                continue
            im = render.shape_image(size, obj)
        else:
            temp = Layer("t", Image.new("RGBA", size))
            render.paint_stroke(temp, None, obj["points"], obj["brush"], obj["color"], obj.get("closed", False),
                                smooth=obj.get("smooth", False), dash=obj.get("dash"), dash_offset=obj.get("dash_offset", 0))
            im = temp.image
        if "transform" in obj: im = render.transformed(im, obj["transform"])
        image = Image.alpha_composite(image, im)
    return image


def test_object_patches_match_canvas_sized_rendering(e):
    import math
    from csp_headless import render
    vec = e.call("paint_layer", action="create", name="vec", kind="vector")["layer_id"]
    e.call("paint_shape", shape="polygon", points=[[10, 10], [90, 20], [40, 80]], fill="#AA3366", outline="#112233", width=3, layer_id=vec)
    e.call("paint_shape", shape="ellipse", points=[[250, 150], [330, 260]], fill="#33AA66", layer_id=vec)  # partly off-canvas
    e.call("paint_shape", shape="bezier", points=[[5, 200], [100, 120], [200, 260], [300, 190]], outline="#000000", width=4, layer_id=vec)
    e.call("paint_shape", shape="rectangle", points=[[100, 100], [140, 130]], fill="#2244FF", antialias=False, layer_id=vec)
    e.call("paint_strokes", strokes=[{"points": [[20, 220, .2], [160, 160, 1], [300, 230, .1]], "smooth": True},
                                     {"points": [[30, 30], [290, 40]], "dash": [10, 6]}], brush={"size": 9, "taper_end": .3}, layer_id=vec)
    e.call("paint_strokes", strokes=[{"points": [[50, 120], [270, 120]]}], color="#CC880080",
           brush={"size": 14, "blend": "multiply", "texture": .4, "seed": 3}, layer_id=vec)
    e.call("paint_strokes", strokes=[{"points": [[60, 60], [200, 200]]}, {"points": [[60, 200], [200, 60]]}], brush={"size": 11}, layer_id=vec)
    strokes = [o for o in e.doc.layer(vec).objects if o["type"] == "stroke"]
    # Retained strokes may be switched to brushes that would need existing pixels.
    e.call("paint_object", action="update", layer_id=vec, object_id=strokes[-2]["id"], changes={"brush": {"kind": "watercolor"}})
    e.call("paint_object", action="update", layer_id=vec, object_id=strokes[-1]["id"], changes={"brush": {"kind": "eraser"}})
    text = e.call("paint_text", text="Привет, мир\nline two", x=-20, y=170,
                  style={"font_size": 28, "scale_x": 1.3, "rotation": 12, "outline_width": 2})["layer_id"]
    e.call("paint_text", text="Angled", x=310, y=10, style={"font_size": 30, "anchor": "rt", "letter_spacing": 3}, layer_id=text)
    matrices = [None, [math.cos(.3) * 1.4, -math.sin(.3), 60, math.sin(.3), math.cos(.3) * .8, -30],
                [1, 0, -300, 0, 1, 0], [.25, .1, 200, -.05, .3, 120], [1, 0, 0, 0, 1, 0], [2.5, 0, -150, 0, 2.5, -200]]
    white = Image.new("RGBA", e.doc.size, "white")
    for matrix in matrices:
        # Untransformed objects and whole-pixel moves are bit-identical. Fractional matrices offset the
        # patch inside the resampling matrix, so a sampling coordinate may round differently: rare
        # pixels differ by one premultiplied unit, invisible once composited.
        exact = matrix is None or (matrix[0], matrix[1], matrix[3], matrix[4]) == (1, 0, 0, 1)
        for lid in (vec, text):
            layer = e.doc.layer(lid)
            for obj in layer.objects:
                obj.pop("transform", None)
                if matrix: obj["transform"] = list(matrix)
            expected = reference_render_objects(layer)
            render.render_objects(layer)
            if exact:
                assert layer.image.tobytes() == expected.tobytes(), (lid, matrix)
            else:
                a = np.asarray(Image.alpha_composite(white, expected), dtype=int)
                b = np.asarray(Image.alpha_composite(white, layer.image), dtype=int)
                differing = np.abs(a - b).max(axis=2)
                assert differing.max() <= 2 and (differing > 0).mean() < .005, (lid, matrix)
        obj = e.doc.layer(text).objects[0]
        if matrix:
            old = render.transformed(render.text_image(e.doc.size, obj), obj["transform"]).getchannel("A").getbbox()
            new = e._object_bounds(obj)
            assert (old is None) == (new is None)
            if old: assert max(abs(p - q) for p, q in zip(old, new)) <= (0 if exact else 1)


def test_vector_transform_validates_matrix_without_objects(e):
    e.call("paint_layer", action="create", name="empty", kind="vector")
    with pytest.raises(ValueError, match="Singular"):
        e.call("paint_transform", matrix=[0, 0, 0, 0, 0, 0], layer_id="empty")


# --- 6. Vectorised flood fill ------------------------------------------------------------------------

def reference_flood(image, x, y, tolerance):
    """The previous implementation: Pillow's pure-Python ImageDraw.floodfill on a barrier image."""
    from PIL import ImageDraw
    arr = np.asarray(image).astype(np.int16)
    arr[arr[..., 3] == 0, :3] = 0
    distance = np.max(np.abs(arr - arr[y, x]), axis=2)
    barrier = Image.fromarray(np.uint8(distance > tolerance) * 255).convert("RGB")
    ImageDraw.floodfill(barrier, (x, y), (127, 0, 0), thresh=0)
    return Image.fromarray(np.uint8(np.asarray(barrier)[..., 0] == 127) * 255)


def flood_cases():
    from PIL import ImageDraw
    rng = np.random.default_rng(21)
    noise = rng.integers(0, 256, (70, 90, 4), dtype=np.uint8)
    noise[rng.random((70, 90)) < .2, 3] = 0  # transparent pixels that keep a hidden RGB
    yield Image.fromarray(noise), [(0, 0), (40, 30), (89, 69)], [0, 30, 64.5, 140, 255]
    walls = np.full((80, 100, 4), 255, np.uint8)
    walls[::9, :, :3] = 0; walls[:, ::13, :3] = 0; walls[40:44, 30:70] = 0
    yield Image.fromarray(walls), [(1, 1), (50, 41), (99, 79)], [0, 10]
    maze = Image.new("RGBA", (120, 100), (255, 255, 255, 255)); draw = ImageDraw.Draw(maze)
    for k, inset in enumerate(range(2, 46, 4)):  # nested frames with alternating gaps: a winding corridor
        draw.rectangle([inset, inset, 119 - inset, 99 - inset], outline=(0, 0, 0, 255))
        gap_y = inset if k % 2 == 0 else 99 - inset
        draw.line([40 + 40 * (k % 2), gap_y, 42 + 40 * (k % 2), gap_y], fill=(255, 255, 255, 255))
    yield maze, [(0, 0), (60, 50), (5, 50)], [0, 254.5]
    yield Image.new("RGBA", (40, 30)), [(0, 0), (39, 29)], [0]


def test_flood_region_matches_previous_algorithm():
    from csp_headless.render import flood_region
    for image, seeds, tolerances in flood_cases():
        for x, y in seeds:
            for tolerance in tolerances:
                expected = reference_flood(image, x, y, tolerance)
                assert flood_region(image, x, y, tolerance).tobytes() == expected.tobytes(), (image.size, x, y, tolerance)


# --- 7. Snapshots and saving -------------------------------------------------------------------------

def test_snapshot_encodes_png_once_for_file_path_and_transport(e, tmp_path):
    import io
    from pathlib import Path
    e.call("paint_strokes", strokes=[{"points": [[10, 10], [300, 200]]}], brush={"size": 20}, layer_id="a")
    picture = e.call("paint_snapshot", path=str(tmp_path / "copy.png"))
    assert picture.png == Path(picture.info["image_path"]).read_bytes() == (tmp_path / "copy.png").read_bytes()
    with Image.open(io.BytesIO(picture.png)) as decoded:
        assert decoded.convert("RGBA").tobytes() == picture.image.tobytes()


def test_project_entries_and_small_previews_are_unchanged(e, tmp_path):
    import io, zipfile
    from csp_headless.storage import encode_pngs, png
    layered_document(e)
    path = tmp_path / "p.apaint"
    e.call("paint_document", action="save", path=str(path))
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        assert entries[0].filename == "mimetype" and entries[-1].filename == "manifest.json"
        assert [i.filename for i in entries[1:4]] == [f"layers/{l.id}.png" for l in e.doc.layers[:3]]
        expected = composite(e.doc); expected.thumbnail((512, 512))
        with Image.open(io.BytesIO(archive.read("preview.png"))) as preview:
            assert preview.convert("RGBA").tobytes() == expected.tobytes()
    images = [layer.image for layer in e.doc.layers]
    assert encode_pngs(images) == [png(image) for image in images]


def test_large_document_preview_is_close_to_the_composite_thumbnail(tmp_path):
    from csp_headless.storage import preview_image
    big = Engine(tmp_path / "big")
    big.call("paint_document", action="create", width=1800, height=1300, background="#EEDDCC")
    rng = np.random.default_rng(4)
    for i, mode in enumerate(["normal", "multiply", "screen", "normal"]):
        lid = big.call("paint_layer", action="create", name=f"l{i}")["layer_id"]
        big.call("paint_strokes", strokes=[{"points": [[float(rng.uniform(0, 1800)), float(rng.uniform(0, 1300))] for _ in range(5)],
                                            "color": "#%06X" % int(rng.integers(1 << 24))} for _ in range(6)],
                 brush={"size": 90}, layer_id=lid)
        big.call("paint_layer", action="update", layer_id=lid, properties={"blend": mode})
    expected = composite(big.doc); expected.thumbnail((512, 512))
    preview = preview_image(big.doc)
    assert preview.size == expected.size
    assert np.abs(np.asarray(preview, dtype=int) - np.asarray(expected, dtype=int)).mean() < 2
    for layer in big.doc.layers: layer.blend = "normal"  # normal layers composite cheaply: exact preview
    expected = composite(big.doc); expected.thumbnail((512, 512))
    assert preview_image(big.doc).tobytes() == expected.tobytes()
