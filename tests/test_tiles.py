"""TiledImage must store exactly what a full image stores, while sharing and skipping empty tiles."""
import numpy as np
from PIL import Image
import pytest

from csp_headless.engine import Engine
from csp_headless.model import Layer
from csp_headless.tiles import TILE, TiledImage

SIZE = (TILE * 2 + 37, TILE + 19)  # edge tiles narrower than TILE on both axes


def random_image(seed, size=SIZE, holes=True):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, (size[1], size[0], 4), dtype=np.uint8)
    if holes:
        pixels[:, :TILE] = 0                        # whole empty tiles
        pixels[rng.random(size[::-1]) < .1, 3] = 0  # transparent pixels that keep their RGB
    return Image.fromarray(pixels)


def test_round_trip_and_reading_api_match_a_full_image():
    image = random_image(1)
    tiled = TiledImage.from_image(image)
    assert tiled.to_image().tobytes() == image.tobytes() == tiled.tobytes()
    assert (0, 0) not in tiled.tiles and tiled.nbytes < SIZE[0] * SIZE[1] * 4
    assert np.array_equal(np.asarray(tiled), np.asarray(image))
    assert tiled.getchannel("A").tobytes() == image.getchannel("A").tobytes()
    assert tiled.getbbox() == image.getbbox() and tiled.getbbox(alpha_only=False) == image.getbbox(alpha_only=False)
    for xy in [(0, 0), (TILE, 5), (SIZE[0] - 1, SIZE[1] - 1), (TILE * 2 + 3, TILE + 2)]:
        assert tiled.getpixel(xy) == image.getpixel(xy)
    for box in [(0, 0, *SIZE), (10, 20, 300, 200), (TILE - 3, TILE - 3, TILE + 3, TILE + 3), (-20, -10, 40, 30),
                (SIZE[0] - 5, SIZE[1] - 5, SIZE[0] + 30, SIZE[1] + 30)]:
        assert tiled.crop(box).tobytes() == image.crop(box).tobytes(), box
    assert TiledImage((10, 10)).getbbox() is None and TiledImage((10, 10)).nbytes == 0


@pytest.mark.parametrize("xy,size", [((5, 7), (40, 30)), ((TILE - 10, TILE - 5), (60, 40)), ((-15, -8), (TILE + 40, 50)),
                                     ((0, 0), SIZE), ((TILE, 0), (TILE, TILE)), ((SIZE[0] - 10, SIZE[1] - 10), (50, 50))])
def test_writes_match_pillow_and_leave_copies_untouched(xy, size):
    base = random_image(2)
    patch = random_image(3, size, holes=False)
    tiled = TiledImage.from_image(base)
    kept = tiled.copy(); before = kept.tobytes()
    expected = base.copy(); expected.paste(patch, xy)
    tiled.paste(patch, xy)
    assert tiled.tobytes() == expected.tobytes() and kept.tobytes() == before
    if xy[0] >= 0 and xy[1] >= 0:  # alpha_composite, like Pillow, takes non-negative destinations
        tiled = TiledImage.from_image(base); kept = tiled.copy()
        expected = base.copy(); expected.alpha_composite(patch, xy, (3, 2))
        tiled.alpha_composite(patch, xy, (3, 2))
        assert tiled.tobytes() == expected.tobytes() and kept.tobytes() == before


def test_unchanged_tiles_are_shared_and_history_keeps_only_changed_ones():
    tiled = TiledImage.from_image(random_image(4, holes=False))
    copy = tiled.copy()
    copy.paste(Image.new("RGBA", (10, 10), (1, 2, 3, 4)), (TILE + 5, 5))
    changed = tiled.changed_tiles(copy)
    assert list(changed) == [(1, 0)] and changed[(1, 0)] is tiled.tiles[(1, 0)]
    assert all(copy.tiles[k] is tiled.tiles[k] for k in tiled.tiles if k != (1, 0))
    restored = copy.copy(); restored.set_tiles(changed)
    assert restored.tobytes() == tiled.tobytes()


def test_trim_drops_only_all_zero_tiles():
    tiled = TiledImage((SIZE[0], SIZE[1]))
    tiled.paste(Image.new("RGBA", (20, 20), (9, 9, 9, 255)), (3, 3))
    tiled.paste(Image.new("RGBA", (20, 20), (7, 0, 0, 0)), (TILE + 3, 3))  # invisible but non-zero RGB
    tiled.paste(Image.new("RGBA", (20, 20)), (3, 3))  # erased back to all zero
    tiled.trim()
    assert set(tiled.tiles) == {(1, 0)} and tiled.getpixel((TILE + 4, 4)) == (7, 0, 0, 0)


def test_layers_store_tiles_and_empty_layers_cost_nothing(tmp_path):
    assert isinstance(Layer("x", Image.new("RGBA", (64, 64))).image, TiledImage)
    e = Engine(tmp_path / "state", park_minutes=0)
    e.call("paint_document", action="create", width=3000, height=2000)
    for i in range(10): e.call("paint_layer", action="create", name=f"l{i}")
    assert sum(layer.image.nbytes for layer in e.doc.layers) == 0
    e.call("paint_strokes", strokes=[{"points": [[100, 100], [140, 120]]}], brush={"size": 9}, layer_id="l3")
    assert 0 < e.doc.layer("l3").image.nbytes <= 4 * TILE * TILE * 4
