"""Sparse tiled RGBA pixels for layers: empty areas cost no memory, and copies share unchanged tiles."""
from __future__ import annotations

import numpy as np
from PIL import Image

TILE = 256


def _zero(tile):
    """True when every byte of a tile is zero: dropping it then changes no stored value."""
    return tile.getbbox(alpha_only=False) is None


class TiledImage:
    """RGBA pixels stored as TILE x TILE tiles (smaller along the right/bottom edges); a missing
    tile is all (0,0,0,0).

    copy() shares every tile copy-on-write: the first write to a shared tile replaces it with a
    private copy, so history snapshots and undo cost only the tiles an operation touched. Stored
    values are exactly those of a full image, including the colour kept in erased pixels; only tiles
    whose every byte is zero are dropped.

    The read API used by the engine and its callers (size, crop, getpixel, getbbox, getchannel,
    tobytes, numpy conversion) works directly on tiles; anything else takes to_image() explicitly."""

    mode = "RGBA"

    def __init__(self, size, tiles=None):
        self.size = (int(size[0]), int(size[1]))
        self.tiles = {} if tiles is None else tiles  # (tx, ty) -> Image
        self._private = set()  # keys whose tile object no other image or history entry references
        self._touched = set()  # keys written since the last trim()

    def __repr__(self):
        return f"<TiledImage {self.size[0]}x{self.size[1]} {len(self.tiles)} tiles>"

    @property
    def width(self): return self.size[0]

    @property
    def height(self): return self.size[1]

    @property
    def nbytes(self):
        """Bytes of pixel memory actually held."""
        return sum(t.width*t.height*4 for t in self.tiles.values())

    # --- geometry -----------------------------------------------------------------------------
    def tile_box(self, key):
        left, top = key[0]*TILE, key[1]*TILE
        return left, top, min(self.size[0], left+TILE), min(self.size[1], top+TILE)

    def keys_in(self, box):
        """Keys of the tiles intersecting box (clipped to the image)."""
        left, top = max(0, int(box[0])), max(0, int(box[1]))
        right, bottom = min(self.size[0], int(box[2])), min(self.size[1], int(box[3]))
        if left >= right or top >= bottom:
            return []
        return [(tx, ty) for ty in range(top//TILE, (bottom-1)//TILE+1) for tx in range(left//TILE, (right-1)//TILE+1)]

    def tile(self, key):
        """The stored tile (read-only: it may be shared) or None when it is empty."""
        return self.tiles.get(key)

    def is_empty(self, box=None):
        if box is None:
            return not self.tiles
        return not any(key in self.tiles for key in self.keys_in(box))

    # --- conversion ---------------------------------------------------------------------------
    @classmethod
    def from_image(cls, image):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        out = cls(image.size)
        for key in out.keys_in((0, 0, *image.size)):
            tile = image.crop(out.tile_box(key))
            if not _zero(tile):
                out.tiles[key] = tile
                out._private.add(key)
        return out

    def to_image(self):
        image = Image.new("RGBA", self.size)
        for key, tile in self.tiles.items():
            image.paste(tile, self.tile_box(key)[:2])
        return image

    def copy(self):
        """Another image sharing every tile; whichever is written first copies the tile it changes."""
        self._private.clear()
        return TiledImage(self.size, dict(self.tiles))

    # --- reading ------------------------------------------------------------------------------
    def crop(self, box):
        """New image of box (which may extend past the image; outside is transparent)."""
        left, top, right, bottom = (int(v) for v in box)
        out = Image.new("RGBA", (right-left, bottom-top))
        for key in self.keys_in((left, top, right, bottom)):
            tile = self.tiles.get(key)
            if tile is None:
                continue
            tl, tt, tr, tb = self.tile_box(key)
            il, it, ir, ib = max(left, tl), max(top, tt), min(right, tr), min(bottom, tb)
            part = tile if (il, it, ir, ib) == (tl, tt, tr, tb) else tile.crop((il-tl, it-tt, ir-tl, ib-tt))
            out.paste(part, (il-left, it-top))
        return out

    def getpixel(self, xy):
        x, y = int(xy[0]), int(xy[1])
        if not (0 <= x < self.size[0] and 0 <= y < self.size[1]):
            raise IndexError("image index out of range")
        tile = self.tiles.get((x//TILE, y//TILE))
        return (0, 0, 0, 0) if tile is None else tile.getpixel((x % TILE, y % TILE))

    def getbbox(self, *, alpha_only=True):
        boxes = []
        for key, tile in self.tiles.items():
            box = tile.getbbox(alpha_only=alpha_only)
            if box is not None:
                left, top = self.tile_box(key)[:2]
                boxes.append((left+box[0], top+box[1], left+box[2], top+box[3]))
        if not boxes:
            return None
        return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)

    def getchannel(self, channel):
        index = "RGBA".index(channel) if isinstance(channel, str) else channel
        out = Image.new("L", self.size)
        for key, tile in self.tiles.items():
            out.paste(tile.getchannel(index), self.tile_box(key)[:2])
        return out

    def tobytes(self, *args):
        return self.to_image().tobytes(*args)

    def __array__(self, dtype=None, copy=None):
        array = np.asarray(self.to_image())
        return array if dtype is None else array.astype(dtype)

    # --- writing (in place; only touched tiles change) ----------------------------------------
    def _writable(self, key):
        tile = self.tiles.get(key)
        if tile is not None and key in self._private:
            return tile
        if tile is not None:
            return tile.copy()
        left, top, right, bottom = self.tile_box(key)
        return Image.new("RGBA", (right-left, bottom-top))

    def _store(self, key, tile):
        self.tiles[key] = tile
        self._private.add(key)
        self._touched.add(key)

    def paste(self, image, xy):
        """Write an RGBA image with its top-left corner at xy, like Image.paste without a mask."""
        x, y = int(xy[0]), int(xy[1])
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        width, height = image.size
        for key in self.keys_in((x, y, x+width, y+height)):
            tl, tt, tr, tb = self.tile_box(key)
            il, it, ir, ib = max(x, tl), max(y, tt), min(x+width, tr), min(y+height, tb)
            part = image.crop((il-x, it-y, ir-x, ib-y))
            if (il, it, ir, ib) == (tl, tt, tr, tb):
                self._store(key, part)  # the whole tile is replaced: the fresh crop becomes the tile
            else:
                tile = self._writable(key)
                tile.paste(part, (il-tl, it-tt))
                self._store(key, tile)

    def alpha_composite(self, image, dest=(0, 0), source=(0, 0)):
        """In-place Image.alpha_composite: image (cropped to source) over this one at dest."""
        if len(source) == 2:
            source = (*source, image.width, image.height)
        overlay = image if tuple(source) == (0, 0, image.width, image.height) else image.crop(source)
        x, y = int(dest[0]), int(dest[1])
        for key in self.keys_in((x, y, x+overlay.width, y+overlay.height)):
            tl, tt, tr, tb = self.tile_box(key)
            il, it, ir, ib = max(x, tl), max(y, tt), min(x+overlay.width, tr), min(y+overlay.height, tb)
            tile = self._writable(key)
            tile.alpha_composite(overlay.crop((il-x, it-y, ir-x, ib-y)), (il-tl, it-tt))
            self._store(key, tile)

    def trim(self):
        """Drop tiles written since the last trim that ended up all zero."""
        for key in self._touched:
            tile = self.tiles.get(key)
            if tile is not None and _zero(tile):
                del self.tiles[key]
                self._private.discard(key)
        self._touched.clear()

    # --- history ------------------------------------------------------------------------------
    def changed_tiles(self, other):
        """{key: this image's tile (None if empty)} for every tile that differs from `other` by
        identity. Copy-on-write makes unchanged tiles the very same objects."""
        keys = set(self.tiles) | set(other.tiles)
        return {key: self.tiles.get(key) for key in keys if self.tiles.get(key) is not other.tiles.get(key)}

    def set_tiles(self, tiles):
        """Put back tiles taken from a history record (they stay shared with it)."""
        for key, tile in tiles.items():
            if tile is None:
                self.tiles.pop(key, None)
            else:
                self.tiles[key] = tile
            self._private.discard(key)
