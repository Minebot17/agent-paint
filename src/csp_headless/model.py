"""Owned, editable scene state. No screenshot inference or native window handles."""
from __future__ import annotations

from dataclasses import dataclass, field
import copy
import math
import secrets
from PIL import Image, ImageChops

MAX_PIXELS = 32_000_000
BLENDS = {"normal", "multiply", "screen", "overlay", "darken", "lighten", "add", "subtract", "difference", "soft_light"}


def uid():
    return secrets.token_hex(8)


def finite(value, low=None, high=None, name=None):
    label = f"{name}: " if name else ""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}Expected a finite number, got {value!r}")
    if low is not None and value < low or high is not None and value > high:
        raise ValueError(f"{label}Number outside {low}..{high} (got {value})")
    return value


def dimensions(width, height):
    finite(width, 1, 16384); finite(height, 1, 16384)
    if int(width) != width or int(height) != height or width * height > MAX_PIXELS:
        raise ValueError(f"Integer dimensions required; maximum {MAX_PIXELS:,} pixels")
    return int(width), int(height)


@dataclass
class Layer:
    name: str
    image: Image.Image
    id: str = field(default_factory=uid)
    opacity: float = 1.0
    blend: str = "normal"
    visible: bool = True
    locked: bool = False
    alpha_lock: bool = False
    clipping: bool = False
    mask: Image.Image | None = None
    kind: str = "raster"
    objects: list[dict] = field(default_factory=list)

    def clone(self):
        """Independent layer record sharing the (copy-on-write) pixels."""
        other = copy.copy(self)
        other.objects = copy.deepcopy(self.objects)
        other.__dict__.pop("_owned", None)
        other.__dict__.pop("_history", None)
        if "_history" in self.__dict__:
            other._history = dict(self._history)
        return other

    def info(self, objects=True):
        """Layer description. objects=False replaces the (possibly huge) object list by a count."""
        data = {"id": self.id, "layer_id": self.id, "name": self.name, "kind": self.kind, "opacity": self.opacity,
                "blend": self.blend, "visible": self.visible, "locked": self.locked,
                "alpha_lock": self.alpha_lock, "clipping": self.clipping,
                "has_mask": self.mask is not None}
        if objects:
            data["objects"] = copy.deepcopy(self.objects)
        else:
            data["object_count"] = len(self.objects)
        return data


def own_pixels(layer):
    """Copy-on-write guard: call before changing layer.image IN PLACE.

    Snapshots share layer pixels instead of copying them; the first in-place write after a
    snapshot works on a private copy, so the snapshot (rollback, undo) keeps the old pixels.
    Code that assigns a new image to layer.image needs no guard."""
    if not layer.__dict__.get("_owned"):
        layer.image = layer.image.copy()
        layer._owned = True


def _changed_box(a, b):
    """None if equal, the changed bounding box if comparable, False if not comparable."""
    if a is None or b is None:
        return None if a is b else False
    if a.size != b.size or a.mode != b.mode:
        return False
    return ImageChops.difference(a, b).getbbox(alpha_only=False)


@dataclass
class Document:
    width: int
    height: int
    name: str = "Untitled"
    id: str = field(default_factory=uid)
    dpi: float = 144.0
    background: str | None = "#FFFFFF"
    layers: list[Layer] = field(default_factory=list)  # bottom to top
    active_layer: str = ""
    selection: Image.Image | None = None  # None means unrestricted
    regions: dict = field(default_factory=dict)
    view: dict = field(default_factory=dict)
    revision: int = 0
    path: str | None = None
    saved_revision: int = -1
    # Each document owns its brush and colour, so agents sharing one server cannot overwrite
    # each other's tool state. The engine fills these on create/open.
    brush: dict = field(default_factory=dict)
    color: str = "#000000"
    undo_stack: list = field(default_factory=list, repr=False)
    redo_stack: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        dimensions(self.width, self.height)
        if not self.layers:
            self.layers = [Layer("Layer 1", Image.new("RGBA", self.size))]
            self.active_layer = self.layers[0].id
        if not self.view:
            self.view = {"rectangle": [0, 0, self.width, self.height], "rotation": 0}

    @property
    def size(self):
        return self.width, self.height

    def layer(self, identifier=None):
        identifier = identifier or self.active_layer
        exact = [l for l in self.layers if l.id == identifier]
        if exact:
            return exact[0]
        matches = [l for l in self.layers if l.name == identifier]
        if len(matches) != 1:
            raise ValueError(f"Layer missing or ambiguous: {identifier!r}; use its id")
        return matches[0]

    def writable(self, identifier=None):
        layer = self.layer(identifier)
        if layer.locked:
            raise ValueError(f"Layer {layer.name!r} is locked")
        if layer.kind != "raster":
            raise ValueError(f"Layer {layer.name!r} is a {layer.kind} layer; rasterize it or target a raster layer for pixel painting")
        return layer

    @staticmethod
    def _copy_state(state):
        """Copy a state dict: layer records are cloned, pixels and the selection mask are shared
        (they are never changed in place without own_pixels / reassignment)."""
        result = {}
        for key, value in state.items():
            if key == "layers": result[key] = [layer.clone() for layer in value]
            elif key == "selection": result[key] = value
            else: result[key] = copy.deepcopy(value)
        return result

    def snapshot(self):
        for layer in self.layers:
            layer.__dict__.pop("_owned", None)  # pixels are now shared with the snapshot
        return self._copy_state({k: v for k, v in self.__dict__.items() if k not in {"undo_stack", "redo_stack"}})

    def restore(self, snapshot):
        """Restore a snapshot. Compacted layers rebuild their pixels from the current state:
        'shared' reuses them, a patch pastes the stored changed rectangle onto a copy."""
        current = {l.id: l for l in self.layers}
        for key, value in self._copy_state(snapshot).items():
            setattr(self, key, value)
        for layer in self.layers:
            history = layer.__dict__.pop("_history", None)
            if not history:
                continue
            source = current.get(layer.id)
            if source is None:
                raise RuntimeError("Corrupt history entry: compacted layer has no current pixels")
            for field_name in ("image", "mask"):
                record = history.get(field_name)
                if record == "shared":
                    setattr(layer, field_name, getattr(source, field_name))
                elif record is not None:
                    box, patch = record
                    restored = getattr(source, field_name).copy()
                    restored.paste(patch, box[:2])
                    setattr(layer, field_name, restored)

    def compact(self, snapshot):
        """Keep only what an operation changed. For each layer equal to the current state the pixels
        are dropped; for a partly changed layer only the changed rectangle is kept. Large documents
        therefore keep many undo steps. Undo is stack-ordered, so the current state at restore time
        always equals the state this entry was compacted against."""
        current = {l.id: l for l in self.layers}
        for layer in snapshot["layers"]:
            now = current.get(layer.id)
            if now is None or "_history" in layer.__dict__:
                continue
            history = {}
            for field_name in ("image", "mask"):
                before, after = getattr(layer, field_name), getattr(now, field_name)
                if before is None:
                    continue
                # Copy-on-write makes untouched pixels the very same object: no comparison needed.
                box = None if before is after else _changed_box(before, after)
                if box is None:
                    history[field_name] = "shared"
                elif box is not False and (box[2]-box[0])*(box[3]-box[1]) < .6*before.width*before.height:
                    history[field_name] = (box, before.crop(box))
                else:
                    continue
                setattr(layer, field_name, None)
            if history:
                layer._history = history
        return snapshot

    @staticmethod
    def _history_cost(snapshot):
        total = 1024
        for l in snapshot["layers"]:
            if l.image is not None: total += l.image.width*l.image.height*4
            if l.mask is not None: total += l.mask.width*l.mask.height
            for record in l.__dict__.get("_history", {}).values():
                if record != "shared":
                    total += record[1].width*record[1].height*len(record[1].getbands())
        if snapshot.get("selection") is not None:
            total += snapshot["selection"].width*snapshot["selection"].height
        return total

    def push(self, snapshot, memory_mb=128):
        self.undo_stack.append(self.compact(snapshot))
        # Bound by approximate uncompressed image bytes, rather than arbitrary action count.
        budget = memory_mb*1024*1024
        total = sum(self._history_cost(s) for s in self.undo_stack)
        while len(self.undo_stack) > 1 and total > budget:
            total -= self._history_cost(self.undo_stack.pop(0))
        self.redo_stack.clear()

    def layers_info(self):
        return [{**l.info(objects=False), "index": i} for i, l in enumerate(self.layers)]

    def info(self):
        return {"document_id": self.id, "name": self.name, "size": list(self.size), "dpi": self.dpi,
                "background": self.background, "active_layer": self.active_layer,
                "layers": self.layers_info(), "layer_order": "bottom-to-top", "revision": self.revision,
                "dirty": self.saved_revision != self.revision, "path": self.path,
                "selection_bbox": self.selection.getbbox() if self.selection is not None else None,
                "has_selection": self.selection is not None, "regions": copy.deepcopy(self.regions),
                "view": copy.deepcopy(self.view), "undo": len(self.undo_stack), "redo": len(self.redo_stack)}
