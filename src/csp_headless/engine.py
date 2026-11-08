"""Synchronous application API. All state is owned here, never in an OS desktop."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import __version__
from .model import Document, Layer, BLENDS, dimensions, finite, uid
from .render import (BRUSH_DEFAULTS, BRUSH_KINDS, FILTER_KINDS, PATTERN_DEFAULT_DENSITY, PATTERN_KINDS, RESAMPLING,
                     TEXT_ALIGNS, TEXT_ANCHORS, TEXT_STYLE_KEYS, TEXTURE_SPACES,
                     add_noise, apply_paint, blend, channel_shift, composite, font_for, fonts, gaussian_blur,
                     hard_polygon_mask, missing_glyphs, paint_stroke, paper_noise, pattern_coverage, relief,
                     render_objects, rgba, shape_patch, smooth_outline, text_bounds, text_image, transformed,
                     validate_brush, validate_dash, validate_gradient, validate_points)
from .storage import atomic_write, check_path, export_image, import_ora, open_project, png, save_project

# Tools that act on one document. They accept document_id to target it explicitly, so several
# agents sharing one server never draw into each other's active document.
DOCUMENT_SCOPED = {"paint_layer","paint_brush","paint_strokes","paint_selection","paint_fill","paint_shape","paint_text",
                   "paint_object","paint_transform","paint_history","paint_batch","paint_view","paint_region",
                   "paint_snapshot","paint_compare","paint_sample","paint_filter","paint_pattern"}
SCENE_TOOLS = {"paint_strokes","paint_selection","paint_fill","paint_shape","paint_text","paint_transform","paint_batch",
               "paint_filter","paint_pattern"}
LAYER_PROPERTIES = {"name","opacity","blend","visible","locked","alpha_lock","clipping"}
BATCH_TOOLS = {"paint_layer","paint_brush","paint_strokes","paint_selection","paint_fill","paint_shape","paint_text",
               "paint_object","paint_transform","paint_filter","paint_pattern"}
INLINE_BATCH_LIMIT = 256
FILE_BATCH_LIMIT = 20000
STROKE_FIELDS = {"points","closed","brush","color","smooth","dash","dash_offset"}


def resolve_refs(value, stored):
    """Replace {"$ref": "name.key.0"} with a value stored by an earlier batch action ("store": "name")."""
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            path = str(value["$ref"]).split(".")
            if path[0] not in stored:
                raise ValueError(f"Unknown $ref {value['$ref']!r}: no earlier action stored {path[0]!r}")
            result = stored[path[0]]
            for key in path[1:]:
                try: result = result[int(key)] if isinstance(result, list) else result[key]
                except (KeyError, IndexError, ValueError, TypeError) as error:
                    raise ValueError(f"$ref {value['$ref']!r} does not resolve at {key!r}") from error
            return copy.deepcopy(result)
        return {k: resolve_refs(v, stored) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_refs(v, stored) for v in value]
    return value


def compact_result(result):
    """Small per-action batch result: ids and union bounds, without per-stroke boxes or object lists."""
    if not isinstance(result, dict):
        return result
    out = {}
    for key, value in result.items():
        if key == "bounds" and isinstance(value, list) and value and isinstance(value[0], (list, type(None))):
            boxes = [b for b in value if b]
            if boxes:
                out["bbox"] = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
        elif key == "bounds":
            out["bbox"] = value
        elif key == "objects" and isinstance(value, list):
            out["object_count"] = len(value)
        elif key == "object" and isinstance(value, dict):
            out["object_id"] = value.get("id")
        elif key == "layers" and isinstance(value, list):
            out["layer_count"] = len(value)
        else:
            out[key] = value
    return out


def load_batch_file(path):
    """Read batch actions from .json ([...] or {"actions":[...]}) or .jsonl (one action per line)."""
    check_path(path)
    file = Path(path).expanduser()
    if not file.is_file():
        raise ValueError(f"actions_file not found: {path}")
    if file.stat().st_size > 64*1024*1024:
        raise ValueError("actions_file exceeds 64 MiB; split the plan into several batches")
    text = file.read_text("utf-8-sig")
    if file.suffix.lower() == ".jsonl":
        actions = []
        for number, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try: actions.append(json.loads(line))
                except json.JSONDecodeError as error: raise ValueError(f"actions_file line {number}: {error}") from error
    else:
        try: data = json.loads(text)
        except json.JSONDecodeError as error: raise ValueError(f"actions_file is not valid JSON: {error}") from error
        actions = data.get("actions") if isinstance(data, dict) else data
    if not isinstance(actions, list):
        raise ValueError("actions_file must hold a JSON list of {tool, arguments} or {\"actions\": [...]}")
    return actions


@dataclass
class Picture:
    info: dict
    image: Image.Image


class StopSignal:
    def __init__(self,abort,cancel=None): self.abort,self.cancel=abort,cancel
    def is_set(self): return self.abort.is_set() or self.cancel is not None and self.cancel.is_set()


class Engine:
    def __init__(self,state_dir=None):
        self.state_dir=Path(state_dir or os.environ.get("AGENT_PAINT_STATE_DIR",Path.cwd()/".state")).resolve()
        self.state_dir.mkdir(parents=True,exist_ok=True)
        self.documents={}
        self.active_document=None
        self.lock=threading.RLock()
        self.stop=threading.Event()
        self._signal=StopSignal(self.stop)
        # Brush/colour used before any document exists; every document then owns its own copy.
        self._default_brush=validate_brush({})
        self._default_color="#000000"
        self._audit_buffer=None
        self.presets={
            "ink":validate_brush({"size":8,"taper_end":.15}),
            "pencil":validate_brush({"kind":"pencil","size":5,"opacity":.8}),
            "soft_airbrush":validate_brush({"kind":"airbrush","size":90,"hardness":0,"opacity":.25}),
            "watercolor":validate_brush({"kind":"watercolor","size":40,"hardness":.7,"opacity":.45,
                                         "edge_darkening":.4,"texture_space":"paper"}),
            "dry_media":validate_brush({"kind":"pencil","size":10,"opacity":.9,"texture":.6,"texture_space":"paper"}),
            "marker":validate_brush({"kind":"marker","size":24,"opacity":.4,"pressure_size":False}),
            "eraser":validate_brush({"kind":"eraser","size":20}),
            "smudge":validate_brush({"kind":"smudge","size":30,"hardness":.5})}
        self.snapshots={}
        self._batch_depth=0
        self._event=0

    @property
    def doc(self):
        if self.active_document not in self.documents:
            raise ValueError("No active document. Create or open one with paint_document")
        return self.documents[self.active_document]

    # The current brush and colour belong to the targeted document, so agents sharing a server
    # cannot change each other's tool state. Without any document they are engine defaults.
    @property
    def brush(self):
        doc=self.documents.get(self.active_document)
        return doc.brush if doc is not None else self._default_brush

    @brush.setter
    def brush(self,value):
        doc=self.documents.get(self.active_document)
        if doc is not None: doc.brush=value
        else: self._default_brush=value

    @property
    def color(self):
        doc=self.documents.get(self.active_document)
        return doc.color if doc is not None else self._default_color

    @color.setter
    def color(self,value):
        doc=self.documents.get(self.active_document)
        if doc is not None: doc.color=value
        else: self._default_color=value

    def _adopt(self,doc,brush=None,color=None):
        """Give a newly created/opened document a brush: its stored one, else the current one."""
        if not doc.brush:
            doc.brush=copy.deepcopy(brush if brush is not None else self.brush)
            doc.color=color if color is not None else self.color
        self.documents[doc.id]=doc; self.active_document=doc.id

    def call(self,tool,_cancel_event=None,**arguments):
        if tool not in OPERATIONS:
            raise ValueError(f"Unknown operation {tool}")
        # Abort never waits for the drawing lock.
        if tool=="paint_abort":
            self.stop.set(); return {"abort_requested":True}
        with self.lock:
            previous=self._signal
            if _cancel_event is not None:
                if _cancel_event.is_set(): raise InterruptedError("Request cancelled before execution")
                self._signal=StopSignal(self.stop,_cancel_event)
            try:
                return self._call_targeted(tool,arguments)
            finally:
                self._signal=previous

    def _call_targeted(self,tool,arguments):
        target=arguments.pop("document_id",None) if tool in DOCUMENT_SCOPED else None
        if target is None: return self._call_locked(tool,arguments)
        if target not in self.documents: raise ValueError(f"Unknown document_id {target!r}; see paint_document(action='list')")
        if self._batch_depth and target!=self.active_document:
            raise ValueError("Actions inside a batch use the batch's document; pass document_id to paint_batch")
        # Calls serialize on the engine lock, so switching the active document for one call is race-free.
        previous=self.active_document; self.active_document=target
        try: return self._call_locked(tool,arguments)
        finally:
            if previous in self.documents: self.active_document=previous

    def _call_locked(self,tool,arguments):
            started=time.monotonic()
            self._event+=1
            event=self._event
            before=None
            context=None
            scene_tool=tool in SCENE_TOOLS
            scene_tool |= tool=="paint_layer" and arguments.get("action","list") not in {"list","info","select"}
            scene_tool |= tool=="paint_object" and arguments.get("action","list") not in {"list","get"}
            scene_tool |= tool=="paint_region" and arguments.get("action","define")!="get"
            if scene_tool and not self._batch_depth:
                before=self.doc.snapshot()
                context=(copy.deepcopy(self.brush),self.color,copy.deepcopy(self.presets))
                self.stop.clear()
            try:
                result=getattr(self,tool)(**arguments)
                if before is not None:
                    if self._signal.is_set(): raise InterruptedError("Operation aborted; transaction rolled back")
                    self.doc.revision+=1
                    self.doc.push(before)
                    target=result.info if isinstance(result,Picture) else result
                    target["revision"]=self.doc.revision
                self._audit(event,tool,arguments,started,"ok")
                return result
            except BaseException as error:
                if before is not None:
                    self.doc.restore(before)
                    self.brush,self.color,self.presets=context
                self._audit(event,tool,arguments,started,"error",str(error))
                raise

    def _audit(self,event,tool,arguments,started,status,error=None):
        nested=self._batch_depth>0
        encoded=json.dumps(arguments,ensure_ascii=False,default=str)
        # Actions inside a batch are logged compactly and written once with the batch.
        limit=2000 if nested else 16000
        record={"event":event,"tool":tool,"status":status,"elapsed_ms":round((time.monotonic()-started)*1000,3),
                "arguments":arguments if len(encoded)<limit else {"sha256":hashlib.sha256(encoded.encode()).hexdigest(),"characters":len(encoded)},
                "document_id":self.active_document,"error":error}
        if nested: record["in_batch"]=True
        line=json.dumps(record,ensure_ascii=False,default=str)+"\n"
        if nested and self._audit_buffer is not None:
            self._audit_buffer.append(line); return
        with (self.state_dir/"actions.jsonl").open("a",encoding="utf-8") as stream:
            stream.write(line)

    def _flush_audit(self):
        if self._audit_buffer:
            with (self.state_dir/"actions.jsonl").open("a",encoding="utf-8") as stream:
                stream.writelines(self._audit_buffer)
        self._audit_buffer=None

    def paint_state(self):
        return {"engine":"Agent Paint","version":__version__,"headless":True,"uses_desktop":False,
                "coordinates":"document pixels; pressure 0..1; opacity 0..1",
                "documents":[{"id":d.id,"name":d.name,"size":list(d.size),"dirty":d.revision!=d.saved_revision} for d in self.documents.values()],
                "active_document":self.active_document,"document":self.doc.info() if self.active_document else None,
                "brush":copy.deepcopy(self.brush),"color":self.color,"brush_scope":"per document (pass document_id)",
                "presets":list(self.presets),
                "operations":list(OPERATIONS),"project_format":".apaint v1",
                "limits":{"max_pixels":32_000_000,"max_layers":128,"undo_memory_mb":128,
                          "batch_inline_actions":INLINE_BATCH_LIMIT,"batch_file_actions":FILE_BATCH_LIMIT,
                          "strokes_per_call":1024},
                "capabilities":{"pressure":True,"layer_masks":True,"clipping":True,"editable_text":True,
                                "vector_objects":True,"blend_modes":sorted(BLENDS),"brush_kinds":sorted(BRUSH_KINDS),
                                "texture_spaces":sorted(TEXTURE_SPACES),"filters":sorted(FILTER_KINDS),
                                "patterns":sorted(PATTERN_KINDS),"text_anchors":sorted(TEXT_ANCHORS),
                                "text_aligns":sorted(TEXT_ALIGNS),"transform_resampling":sorted(RESAMPLING),
                                "hard_edge_selections_and_shapes":True,"stroke_smoothing":True,"stroke_dash":True,
                                "layer_id_targeting":True,"layer_names_as_ids":True,"batch_files":True,
                                "batch_refs":True,"stamp_visible":True,"inspect_project":True,
                                "document_id_targeting":sorted(DOCUMENT_SCOPED)},
                "limitations":["Independent brush renderer; not CSP pixel parity or .sut/.clip compatibility",
                               "No CSP materials, animation, 3D, comics or plugin runtime",
                               "PSD/ORA export rasterizes text/vectors and bakes masks/clipping; .apaint keeps them editable"]}

    def paint_document(self,action="info",document_id=None,width=1024,height=1024,name="Untitled",
                       background="#FFFFFF",dpi=144,path=None,overwrite=False,transparent=False,force=False,scale=1):
        if document_id is not None and action in {"info","save","export"}:
            if document_id not in self.documents: raise ValueError("Unknown document_id")
            previous=self.active_document; self.active_document=document_id
            try: return self.paint_document(action,None,width,height,name,background,dpi,path,overwrite,transparent,force,scale)
            finally: self.active_document=previous
        if action=="create":
            width,height=dimensions(width,height)
            if background is not None: rgba(background)
            doc=Document(width,height,name=name,background=background,dpi=finite(dpi,1,2400,"dpi"))
            self._adopt(doc)
        elif action=="inspect":
            # Read-only verification of a saved project: nothing is registered or changed.
            if not path: raise ValueError("path is required")
            check_path(path)
            saved=open_project(path)
            open_copy=self.documents.get(saved.id)
            return {"path":str(Path(path).expanduser().resolve()),"bytes":Path(path).expanduser().stat().st_size,
                    "document_id":saved.id,"name":saved.name,"size":list(saved.size),"dpi":saved.dpi,
                    "background":saved.background,"revision":saved.revision,"layers":saved.layers_info(),
                    "layer_count":len(saved.layers),"object_count":sum(len(l.objects) for l in saved.layers),
                    "regions":saved.regions,"has_stored_brush":bool(saved.brush),
                    "open_in_session":open_copy is not None,
                    "matches_open_document":open_copy is not None and open_copy.revision==saved.revision and
                                            open_copy.path is not None and Path(open_copy.path).resolve()==Path(path).expanduser().resolve()}
        elif action in {"open","import_image","import_ora"}:
            if not path: raise ValueError("path is required")
            check_path(path)
            if action=="open": doc=open_project(path)
            elif action=="import_ora": doc=import_ora(path)
            else:
                with Image.open(path) as source:
                    dimensions(*source.size)
                    doc=Document(*source.size,name=name if name!="Untitled" else Path(path).stem,background=None)
                    doc.layer().image=source.convert("RGBA")
            existing=self.documents.get(doc.id)
            if existing is not None:
                same=existing.path is not None and Path(existing.path).resolve()==Path(path).expanduser().resolve()
                if same and existing.revision!=existing.saved_revision and not force:
                    raise ValueError(f"This project is already open (document_id {existing.id}) with unsaved changes. "
                                     "Save it, select it, or pass force=true to discard them and reload from disk")
                if same and not force:
                    # Re-opening the file that is already open simply selects it.
                    self.active_document=existing.id
                    return {**existing.info(),"already_open":True}
                if same:
                    self._adopt(doc,existing.brush,existing.color)
                    return {**doc.info(),"reloaded_from_disk":True}
                # Another file of the same document (e.g. a saved variant): open it as a separate document.
                original=doc.id; doc.id=uid()
                self._adopt(doc)
                return {**doc.info(),"document_id_changed_from":original,
                        "note":"Another saved copy of this document is already open, so this one received a new document_id"}
            self._adopt(doc)
        elif action=="select":
            if document_id not in self.documents: raise ValueError("Unknown document_id")
            self.active_document=document_id
        elif action=="close":
            doc=self.documents.get(document_id or self.active_document)
            if doc is None: raise ValueError("Unknown document_id")
            if doc.revision!=doc.saved_revision and not force: raise ValueError("Document has unsaved changes; save or use force=true")
            del self.documents[doc.id]
            if self.active_document==doc.id: self.active_document=next(iter(self.documents),None)
            return {"closed":doc.id,"active_document":self.active_document}
        elif action=="save":
            destination=path or self.doc.path
            if not destination: raise ValueError("path is required for first save")
            # Saving again to the document's own path is ordinary Save, not Save As.
            same=self.doc.path and Path(destination).resolve()==Path(self.doc.path).resolve()
            save_project(self.doc,destination,overwrite=overwrite or bool(same))
        elif action=="export":
            if not path: raise ValueError("path is required")
            doc=self.doc
            output=export_image(doc,path,overwrite,transparent,scale)
            current=doc.path is not None and doc.revision==doc.saved_revision
            result={"path":output,"size":[doc.width*scale,doc.height*scale],"scale":scale,"exported":True,
                    # True when the editable .apaint on disk holds exactly what was exported.
                    "project_saved":current,"project_path":doc.path,
                    "rasterized_objects":Path(path).suffix.lower() in {".ora",".psd"}}
            if not current:
                result["note"]=("Export wrote only the image; the editable project has unsaved changes. "
                                "Save it with paint_document(action='save', path='....apaint')." if doc.path else
                                "Export wrote only the image; save the editable project with paint_document(action='save', path='....apaint').")
            return result
        elif action=="list": return {"documents":[d.info() for d in self.documents.values()]}
        elif action!="info": raise ValueError("Unknown document action")
        return self.doc.info()

    @staticmethod
    def _stack_position(doc,index,position,default,extra=0):
        """Insertion index (bottom-to-top). position: above_active | top | bottom; or an explicit index."""
        if index is not None and position is not None: raise ValueError("Use index or position, not both")
        if index is not None:
            high=len(doc.layers)-1+extra
            if type(index) is not int and not (isinstance(index,float) and index.is_integer()):
                raise ValueError(f"Layer index must be an integer 0..{high} (0 = bottom)")
            if not 0<=int(index)<=high:
                raise ValueError(f"Layer index {index} is outside 0..{high} (bottom-to-top; the document has {len(doc.layers)} layers). "
                                 "Use position='top' to put a layer above all others")
            return int(index)
        position=position or default
        if position=="top": return len(doc.layers)-1+extra
        if position=="bottom": return 0
        if position=="above_active": return doc.layers.index(doc.layer())+extra
        raise ValueError("position is above_active, top or bottom")

    def _layer_result(self,doc,layer,objects=True):
        return {**layer.info(objects),"index":doc.layers.index(layer),"layer_count":len(doc.layers)}

    def paint_layer(self,action="list",layer_id=None,name=None,kind="raster",properties=None,index=None,position=None,
                    include_background=True):
        doc=self.doc
        if action=="list": return {"layers":doc.layers_info(),"active_layer":doc.active_layer,"order":"bottom-to-top"}
        if action in {"create","stamp_visible"}:
            if len(doc.layers)>=128: raise ValueError("Maximum 128 layers")
            if action=="create":
                if kind not in {"raster","vector","text"}: raise ValueError("Layer kind must be raster, vector or text")
                layer=Layer(name or f"Layer {len(doc.layers)+1}",Image.new("RGBA",doc.size),kind=kind)
            else:
                # Merged copy of everything visible (the composition stays editable underneath).
                if type(include_background) is not bool: raise ValueError("include_background requires boolean")
                layer=Layer(name or "Visible stamp",composite(doc,not include_background))
            # Properties are honoured on create (previously they were silently ignored).
            if properties: self._layer_properties(layer,properties)
            where=self._stack_position(doc,index,position,"above_active" if action=="create" else "top",extra=1)
            doc.layers.insert(where,layer); doc.active_layer=layer.id
            return self._layer_result(doc,layer)
        layer=doc.layer(layer_id)
        if action=="info": return self._layer_result(doc,layer)
        if action=="select": doc.active_layer=layer.id
        elif action=="duplicate":
            if len(doc.layers)>=128: raise ValueError("Maximum 128 layers")
            layer=copy.deepcopy(layer); layer.id=uid(); layer.name=name or layer.name+" copy"
            doc.layers.insert(doc.layers.index(doc.layer(layer_id))+1,layer); doc.active_layer=layer.id
        elif action=="update": self._layer_properties(layer,properties or {})
        elif action=="reorder":
            if index is None and position is None: raise ValueError("reorder needs index (0 = bottom) or position top/bottom")
            if position=="above_active": raise ValueError("reorder position is top or bottom (or give an index)")
            where=self._stack_position(doc,index,position,"top")
            doc.layers.remove(layer); doc.layers.insert(where,layer)
        elif action=="delete":
            if len(doc.layers)==1: raise ValueError("Cannot delete the last layer; clear it instead")
            pos=doc.layers.index(layer); doc.layers.remove(layer)
            if doc.active_layer==layer.id: doc.active_layer=doc.layers[max(0,pos-1)].id
            return {"deleted":layer.id,"active_layer":doc.active_layer}
        elif action=="clear":
            if layer.locked: raise ValueError("Layer is locked")
            layer.image=Image.new("RGBA",doc.size); layer.objects=[]
        elif action=="rasterize":
            if layer.locked: raise ValueError("Layer is locked")
            layer.kind="raster"; layer.objects=[]
        elif action=="mask_from_selection":
            if layer.locked: raise ValueError("Layer is locked")
            layer.mask=doc.selection.copy() if doc.selection is not None else Image.new("L",doc.size,255)
        elif action=="mask_remove": layer.mask=None
        elif action=="mask_invert":
            if layer.mask is None: raise ValueError("Layer has no mask")
            layer.mask=ImageChops.invert(layer.mask)
        elif action=="mask_apply":
            if layer.locked: raise ValueError("Layer is locked")
            if layer.mask is not None:
                image=layer.image.copy(); image.putalpha(ImageChops.multiply(image.getchannel("A"),layer.mask))
                layer.image=image; layer.mask=None
                layer.kind="raster"; layer.objects=[]
        elif action=="merge_down":
            pos=doc.layers.index(layer)
            if not pos: raise ValueError("No layer below")
            below=doc.layers[pos-1]
            if layer.locked or below.locked: raise ValueError("Layer is locked")
            # A blend pair dependent on lower backdrop cannot be represented by an ordinary merged layer.
            if below.blend!="normal" or layer.blend!="normal" or below.clipping or layer.clipping or not below.visible or not layer.visible:
                raise ValueError("Merge requires visible, unclipped layers with normal blend; flatten for an exact composition")
            lower=blend(Image.new("RGBA",doc.size),below.image,"normal",below.opacity,below.mask)
            below.image=blend(lower,layer.image,layer.blend,layer.opacity,layer.mask)
            below.opacity=1; below.mask=None; below.kind="raster"; below.objects=[]
            doc.layers.remove(layer); doc.active_layer=below.id; layer=below
        elif action=="flatten":
            if any(l.locked for l in doc.layers): raise ValueError("Unlock layers before flattening")
            layer=Layer(name or "Flattened",composite(doc)); doc.layers=[layer]; doc.active_layer=layer.id; doc.background=None
        else: raise ValueError("Unknown layer action")
        return self._layer_result(doc,layer,objects=False)

    @staticmethod
    def _layer_properties(layer,props):
        if set(props)-LAYER_PROPERTIES: raise ValueError(f"Unknown layer properties: {sorted(set(props)-LAYER_PROPERTIES)}")
        for key,value in props.items():
            if key=="opacity": finite(value,0,1)
            elif key=="blend" and value not in BLENDS: raise ValueError("Unsupported blend mode")
            elif key in {"visible","locked","alpha_lock","clipping"} and type(value) is not bool: raise ValueError(f"{key} requires boolean")
            elif key=="name" and not isinstance(value,str): raise ValueError("name requires string")
        for key,value in props.items(): setattr(layer,key,value)

    def paint_brush(self,action="get",settings=None,color=None,name=None,path=None,overwrite=False):
        scope=self.active_document if self.active_document in self.documents else "default"
        if action=="get": return {"settings":copy.deepcopy(self.brush),"color":self.color,"brush_of":scope}
        if action=="list": return {"presets":copy.deepcopy(self.presets),"kinds":sorted(BRUSH_KINDS)}
        if action=="configure":
            brush=validate_brush({**self.brush,**(settings or {})})
            if color is not None: rgba(color)
            self.brush=brush
            if color is not None: self.color=color
        elif action=="save_preset":
            if not name: raise ValueError("name is required")
            self.presets[name]=copy.deepcopy(self.brush)
        elif action=="load_preset":
            if name not in self.presets: raise ValueError("Unknown preset")
            self.brush=copy.deepcopy(self.presets[name])
        elif action=="delete_preset":
            if name not in self.presets: raise ValueError("Unknown preset")
            del self.presets[name]
        elif action=="export_presets":
            if not path: raise ValueError("path required")
            data=json.dumps({"format":"agent-paint-brushes","version":1,"presets":self.presets},indent=2).encode()
            return {"path":atomic_write(path,lambda s:s.write(data),overwrite)}
        elif action=="import_presets":
            if not path: raise ValueError("path required")
            data=json.loads(Path(path).read_text("utf-8"))
            if data.get("format")!="agent-paint-brushes" or data.get("version")!=1: raise ValueError("Not a brush library")
            presets={k:validate_brush(v) for k,v in data["presets"].items()}
            self.presets.update(presets)
        else: raise ValueError("Unknown brush action")
        return {"settings":copy.deepcopy(self.brush),"color":self.color,"presets":list(self.presets),"brush_of":scope}

    def _points(self,points,coordinate_space):
        validated=validate_points(points)
        if coordinate_space=="document": return validated
        if coordinate_space!="normalized": raise ValueError("coordinate_space must be document or normalized")
        if any(not 0<=x<=1 or not 0<=y<=1 for x,y,p in validated): raise ValueError("Normalized coordinates must be in 0..1")
        return [(x*(self.doc.width-1),y*(self.doc.height-1),p) for x,y,p in validated]

    def paint_strokes(self,strokes,coordinate_space="document",expected_revision=None,layer_id=None,brush=None,color=None,
                      smooth=False):
        """Strokes into layer_id (default: active layer, which stays active). Call-level brush/color/smooth are
        defaults for every stroke; per-stroke values override them."""
        doc=self.doc; layer=doc.layer(layer_id)
        if expected_revision is not None and expected_revision!=doc.revision: raise ValueError("Document revision conflict")
        if layer.locked or layer.kind=="text":
            raise ValueError(f"Layer {layer.name!r} is {'locked' if layer.locked else 'a text layer'}; target an unlocked raster or vector layer")
        if not isinstance(strokes,list) or not 1<=len(strokes)<=1024: raise ValueError("Expected 1..1024 strokes; use paint_batch for more")
        if type(smooth) is not bool: raise ValueError("smooth requires boolean")
        base=validate_brush({**self.brush,**(brush or {})})
        default_color=color if color is not None else self.color; rgba(default_color)
        plans=[]
        for number,stroke in enumerate(strokes):
            if not isinstance(stroke,dict) or "points" not in stroke: raise ValueError(f"Stroke {number} needs points")
            if set(stroke)-STROKE_FIELDS: raise ValueError(f"Unknown stroke field(s) {sorted(set(stroke)-STROKE_FIELDS)}; valid: {sorted(STROKE_FIELDS)}")
            settings=validate_brush({**base,**stroke.get("brush",{})})
            colour=stroke.get("color",default_color); rgba(colour)
            pts=self._points(stroke["points"],coordinate_space)
            curve=stroke.get("smooth",smooth)
            if type(curve) is not bool: raise ValueError("smooth requires boolean")
            dash=validate_dash(stroke.get("dash"),stroke.get("dash_offset",0))
            if layer.kind=="vector" and (settings["kind"] in {"eraser","smudge","watercolor"} or doc.selection is not None):
                raise ValueError("Vector strokes require an ordinary brush and no pixel selection; rasterize for wet/eraser operations")
            if settings["kind"]=="eraser" and layer.alpha_lock:
                raise ValueError(f"Layer {layer.name!r} has alpha_lock=true, so the eraser cannot change it. "
                                 "Unlock alpha with paint_layer(action='update', properties={'alpha_lock': false}) first")
            plans.append((pts,settings,colour,bool(stroke.get("closed",False)),curve,dash,stroke.get("dash_offset",0)))
        boxes=[]
        for pts,settings,colour,closed,curve,dash,offset in plans:
            if self._signal.is_set(): raise InterruptedError("Painting aborted; transaction rolled back")
            boxes.append(paint_stroke(layer,doc.selection,pts,settings,colour,closed,self._signal,curve,dash,offset))
            if layer.kind=="vector":
                obj={"id":uid(),"type":"stroke","points":pts,"brush":settings,"color":colour,"closed":closed}
                if curve: obj["smooth"]=True
                if dash: obj["dash"]=dash; obj["dash_offset"]=offset
                layer.objects.append(obj)
        result={"painted":len(plans),"layer_id":layer.id,"pressure_applied":True,"synchronous":True}
        drawn=[b for b in boxes if b]
        result["bbox"]=[min(b[0] for b in drawn),min(b[1] for b in drawn),max(b[2] for b in drawn),max(b[3] for b in drawn)] if drawn else None
        if len(boxes)<=64: result["bounds"]=boxes
        return result

    def _rectangle(self,rectangle):
        if rectangle is None or len(rectangle)!=4: raise ValueError("rectangle is [left,top,right,bottom]")
        l,t,r,b=[finite(v) for v in rectangle]
        if not 0<=l<r<=self.doc.width or not 0<=t<b<=self.doc.height: raise ValueError("Rectangle must lie inside document")
        box=tuple(map(round,(l,t,r,b)))
        if box[0]>=box[2] or box[1]>=box[3]: raise ValueError("Rectangle is smaller than one pixel")
        return box

    def _flood(self,point,tolerance,sample_merged=False,layer_id=None):
        from PIL import ImageDraw
        if point is None or len(point)!=2: raise ValueError("point=[x,y] is required")
        x,y=map(round,point); finite(tolerance,0,255,"tolerance")
        if not 0<=x<self.doc.width or not 0<=y<self.doc.height: raise ValueError("Flood point outside document")
        image=composite(self.doc) if sample_merged else self.doc.layer(layer_id).image
        # Floodfill uses a sentinel in a separate RGB image to preserve original pixels and alpha.
        arr=np.asarray(image).astype(np.int16)
        arr[arr[...,3]==0,:3]=0  # Invisible stored RGB must not become a flood barrier.
        target=arr[y,x]
        distance=np.max(np.abs(arr-target),axis=2)
        barrier=Image.fromarray(np.uint8(distance>tolerance)*255).convert("RGB")
        ImageDraw.floodfill(barrier,(x,y),(127,0,0),thresh=0)
        return Image.fromarray(np.uint8(np.asarray(barrier)[...,0]==127)*255)

    @staticmethod
    def _selection_box(rectangle):
        """Integer [l,t,r,b] that may extend past the canvas; drawing clips it."""
        if rectangle is None or len(rectangle)!=4: raise ValueError("rectangle is [left,top,right,bottom]")
        l,t,r,b=[round(finite(v,-100000,100000)) for v in rectangle]
        if l>=r or t>=b: raise ValueError("Rectangle needs left<right and top<bottom (at least one pixel after rounding)")
        return l,t,r,b

    def paint_selection(self,action="none",points=None,rectangle=None,point=None,operation="replace",radius=0,
                        tolerance=0,sample_merged=False,antialias=True,smooth=False,layer_id=None):
        doc=self.doc
        if operation not in {"replace","add","subtract","intersect"}: raise ValueError("Unknown selection operation")
        if type(antialias) is not bool: raise ValueError("antialias requires boolean")
        if type(smooth) is not bool: raise ValueError("smooth requires boolean")
        if action=="none": doc.selection=None; return {"selection":None}
        if action=="all": mask=Image.new("L",doc.size,255)
        elif action in {"invert","feather","grow","shrink"}:
            if doc.selection is None: raise ValueError("No selection")
            finite(radius,0,100,"radius")
            mask=doc.selection.copy()
            if action=="invert": mask=ImageChops.invert(mask)
            elif action=="feather": mask=mask.filter(ImageFilter.GaussianBlur(radius))
            else:
                kernel=round(radius)*2+1
                mask=mask.filter(ImageFilter.MaxFilter(kernel) if action=="grow" else ImageFilter.MinFilter(kernel))
        elif action=="flood": mask=self._flood(point,tolerance,sample_merged,layer_id)
        elif action=="polygon" and not antialias:
            # Same pixel-centre rule as hard polygon shapes: [x0,x1) like hard rectangles.
            pts=validate_points(points or [],3)
            outline=smooth_outline(pts,True) if smooth else pts
            mask=hard_polygon_mask(doc.width,doc.height,outline)
        elif action in {"polygon","rectangle","ellipse"}:
            aa=3 if antialias and doc.width*doc.height<=4_000_000 else 1
            mask=Image.new("L",(doc.width*aa,doc.height*aa)); draw=ImageDraw.Draw(mask)
            if action=="polygon":
                pts=validate_points(points or [],3)
                outline=smooth_outline(pts,True) if smooth else [(x,y) for x,y,p in pts]
                draw.polygon([(x*aa,y*aa) for x,y in outline],fill=255)
            else:
                box=self._selection_box(rectangle)
                # Rectangle edges use the same half-open bounds as snapshot crops.
                box=(box[0]*aa,box[1]*aa,box[2]*aa-1,box[3]*aa-1)
                (draw.rectangle if action=="rectangle" else draw.ellipse)(box,fill=255)
            # Box (area) downsampling gives exact coverage: integer rectangles stay perfectly hard,
            # fractional edges get fractional coverage, and nothing rings outside the shape.
            if aa!=1: mask=mask.resize(doc.size,Image.Resampling.BOX)
        elif action=="from_layer": mask=doc.layer(layer_id).image.getchannel("A")
        else: raise ValueError("Unknown selection action")
        if doc.selection is not None and action not in {"invert","feather","grow","shrink"}:
            if operation=="add": mask=ImageChops.lighter(doc.selection,mask)
            elif operation=="subtract": mask=ImageChops.subtract(doc.selection,mask)
            elif operation=="intersect": mask=ImageChops.multiply(doc.selection,mask)
        elif operation=="intersect" and doc.selection is None:
            pass  # None is unrestricted, so intersection is the new mask.
        elif operation=="subtract" and doc.selection is None:
            mask=ImageChops.invert(mask)
        doc.selection=mask
        return {"has_selection":True,"bbox":mask.getbbox(),"operation":operation}

    def paint_fill(self,kind="solid",color=None,color2="#FFFFFF",start=None,end=None,point=None,tolerance=0,
                   sample_merged=False,opacity=1,blend_mode="normal",layer_id=None):
        doc=self.doc; layer=doc.writable(layer_id)
        finite(opacity,0,1,"opacity")
        if blend_mode not in BLENDS: raise ValueError(f"Unsupported blend mode; use one of {sorted(BLENDS)}")
        if kind=="clear":
            if layer.alpha_lock:
                # Clearing changes only alpha, which alpha lock forbids: fail loudly instead of a silent no-op.
                raise ValueError(f"Layer {layer.name!r} has alpha_lock=true, so clearing cannot change its transparency. "
                                 "Unlock alpha with paint_layer(action='update', properties={'alpha_lock': false}) first")
            # Erase to transparency inside the selection (whole layer without one); opacity<1 erases partially.
            image=Image.new("RGBA",doc.size,(0,0,0,round(255*opacity)))
            apply_paint(layer,image,(0,0,*doc.size),doc.selection,erase=True)
            return {"filled":"clear","layer_id":layer.id,"selection_applied":doc.selection is not None}
        c1=rgba(color or self.color); c2=rgba(color2)
        mask=doc.selection
        if kind=="flood":
            flood=self._flood(point,tolerance,sample_merged,layer.id)
            mask=ImageChops.multiply(mask,flood) if mask is not None else flood
        if kind in {"solid","flood"}: image=Image.new("RGBA",doc.size,c1)
        elif kind in {"linear","radial"}:
            if start is None or end is None or len(start)!=2 or len(end)!=2: raise ValueError("Gradient requires start/end points")
            for v in start+end: finite(v)
            dx,dy=end[0]-start[0],end[1]-start[1]; length2=dx*dx+dy*dy
            if length2<1e-8: raise ValueError("Gradient endpoints must differ")
            # Render in rows to bound peak float allocations on large documents.
            image=Image.new("RGBA",doc.size)
            x=np.arange(doc.width,dtype=np.float32)[None,:]
            ca,cb=np.array(c1,dtype=np.float32),np.array(c2,dtype=np.float32)
            for top in range(0,doc.height,128):
                y=np.arange(top,min(top+128,doc.height),dtype=np.float32)[:,None]
                value=((x-start[0])*dx+(y-start[1])*dy)/length2 if kind=="linear" else np.sqrt((x-start[0])**2+(y-start[1])**2)/math.sqrt(length2)
                values=np.clip(value,0,1)[...,None]
                chunk=Image.fromarray(np.uint8(ca*(1-values)+cb*values+.5))
                image.paste(chunk,(0,top))
        else: raise ValueError("Fill kind must be solid, flood, linear, radial or clear")
        if opacity!=1: image.putalpha(image.getchannel("A").point(lambda a:round(a*opacity)))
        apply_paint(layer,image,(0,0,*doc.size),mask,False,blend_mode)
        return {"filled":kind,"layer_id":layer.id,"selection_applied":mask is not None}

    def paint_shape(self,shape,points,fill=None,outline=None,width=1,radius=12,editable=False,antialias=True,
                    smooth=False,bleed=0,layer_id=None):
        doc=self.doc; layer=doc.layer(layer_id)
        if layer.locked or layer.kind=="text":
            raise ValueError(f"Layer {layer.name!r} is {'locked' if layer.locked else 'a text layer'}; target an unlocked raster/vector layer")
        if shape not in {"rectangle","rounded_rectangle","ellipse","polygon","line","bezier"}:
            raise ValueError("shape is rectangle, rounded_rectangle, ellipse, polygon, line or bezier")
        pts=validate_points(points,2 if shape!="polygon" else 3)
        finite(width,.1,4096,"width"); finite(radius,0,4096,"radius"); finite(bleed,0,64,"bleed")
        if type(antialias) is not bool: raise ValueError("antialias requires boolean")
        if type(smooth) is not bool: raise ValueError("smooth requires boolean")
        if shape in {"rectangle","ellipse","rounded_rectangle"}:
            if len(pts)!=2 or pts[0][0]>=pts[1][0] or pts[0][1]>=pts[1][1]: raise ValueError("Shape needs top-left/bottom-right corners [[left,top],[right,bottom]]")
        if fill is not None: rgba(fill)
        if outline is not None: rgba(outline)
        if fill is None and outline is None: raise ValueError("Provide fill and/or outline color")
        obj={"id":uid(),"type":"shape","shape":shape,"points":pts,"fill":fill,"outline":outline,"width":width,"radius":radius}
        if not antialias: obj["antialias"]=False
        if smooth: obj["smooth"]=True
        if bleed: obj["bleed"]=bleed
        # Rendered only inside the shape's own bounds: cost no longer scales with the canvas.
        box,patch=shape_patch(doc.size,obj)
        if editable or layer.kind=="vector":
            if doc.selection is not None: raise ValueError("Editable shapes cannot use a pixel selection; rasterize first")
            if layer.kind!="vector": raise ValueError("Create/select a vector layer for editable shapes")
            layer.objects.append(obj)
        if box is not None: apply_paint(layer,patch,box,doc.selection)
        return {"object_id":obj["id"] if layer.kind=="vector" else None,"layer_id":layer.id,"editable":layer.kind=="vector",
                "bounds":list(box) if box else None}

    def paint_text(self,text,x,y,style=None,layer_id=None,object_id=None):
        doc=self.doc
        if not isinstance(text,str) or len(text)>16000: raise ValueError("Text must contain at most 16000 characters")
        finite(x,-100000,100000); finite(y,-100000,100000)
        style=style or {}
        if set(style)-TEXT_STYLE_KEYS: raise ValueError(f"Unknown text style: {sorted(set(style)-TEXT_STYLE_KEYS)}")
        if "font_size" in style: finite(style["font_size"],1,4096,"font_size")
        if style.get("align","left") not in TEXT_ALIGNS: raise ValueError(f"Invalid text alignment; use one of {sorted(TEXT_ALIGNS)}")
        if style.get("anchor","lt") not in TEXT_ANCHORS:
            raise ValueError("anchor is horizontal l/m/r + vertical t/m/b/s (s = first baseline), e.g. 'mm' centres the text on x,y "
                             "and 'ls' puts its baseline on y")
        for key in ("color","outline_color"):
            if key in style: rgba(style[key])
        if layer_id: layer=doc.layer(layer_id)
        elif object_id: layer=doc.layer()
        else:
            if len(doc.layers)>=128: raise ValueError("Maximum 128 layers")
            layer=Layer(text[:40] or "Text",Image.new("RGBA",doc.size),kind="text")
            doc.layers.insert(doc.layers.index(doc.layer())+1,layer)
        if layer.locked or layer.kind!="text": raise ValueError("Text needs an unlocked text layer")
        if object_id:
            obj=next((o for o in layer.objects if o["id"]==object_id),None)
            if obj is None: raise ValueError("Unknown text object")
            obj.update(text=text,x=x,y=y,style={**obj["style"],**style})
        else:
            obj={"id":uid(),"type":"text","text":text,"x":x,"y":y,"style":{"font_size":32,"color":self.color,**style}}
            layer.objects.append(obj)
        render_objects(layer); doc.active_layer=layer.id
        result={"layer_id":layer.id,"object":copy.deepcopy(obj),"editable":True,"bbox":self._object_bounds(obj)}
        font=font_for(obj["style"].get("font"),obj["style"].get("font_size",32))
        missing=missing_glyphs(font,obj["text"])
        if missing:
            result["missing_glyphs"]=missing
            result["warning"]=(f"Font {Path(font.path).stem!r} has no glyphs for {''.join(missing)!r}; they render as boxes. "
                               "Pick a font with paint_fonts(supports=<your text>).")
        return result

    def _object_bounds(self,obj):
        """Ink bounds of a text object in document pixels, including any retained transform."""
        if "transform" not in obj: return text_bounds(obj)
        box=transformed(text_image(self.doc.size,obj),obj["transform"]).getchannel("A").getbbox()
        return list(box) if box else None

    def paint_object(self,action="list",object_id=None,layer_id=None,changes=None):
        layer=self.doc.layer(layer_id)
        if action=="list": return {"layer_id":layer.id,"objects":copy.deepcopy(layer.objects)}
        obj=next((o for o in layer.objects if o["id"]==object_id),None)
        if obj is None: raise ValueError("Unknown object id")
        if action=="get": return copy.deepcopy(obj)
        if layer.locked: raise ValueError("Layer is locked")
        if action=="delete": layer.objects.remove(obj)
        elif action=="duplicate":
            obj=copy.deepcopy(obj); obj["id"]=uid(); layer.objects.append(obj)
        elif action=="update":
            changes=changes or {}
            allowed={"shape":{"points","fill","outline","width","radius","antialias","smooth","bleed"},
                     "stroke":{"points","brush","color","closed","smooth","dash","dash_offset"},
                     "text":{"text","x","y","style"}}[obj["type"]]|{"transform"}
            if set(changes)-allowed: raise ValueError(f"Unknown or immutable object field; {obj['type']} objects accept {sorted(allowed)}")
            for key,value in copy.deepcopy(changes).items():
                if key in {"style","brush"}: obj[key].update(value)
                else: obj[key]=value
            if obj["type"]=="stroke":
                obj["points"]=validate_points(obj["points"]); obj["brush"]=validate_brush(obj["brush"]); rgba(obj["color"])
                if obj.get("dash") is not None: obj["dash"]=validate_dash(obj["dash"],obj.get("dash_offset",0))
        else: raise ValueError("Unknown object action")
        render_objects(layer)
        return {"layer_id":layer.id,"object_id":obj["id"],"action":action}

    def paint_transform(self,matrix=None,translate=None,scale=None,rotation=0,origin=None,layer_id=None,selection_only=False,
                        resample="bicubic"):
        doc=self.doc; layer=doc.layer(layer_id)
        if layer.locked: raise ValueError("Layer is locked")
        if resample not in RESAMPLING: raise ValueError("resample is nearest (pixel art), bilinear or bicubic")
        if matrix is not None and (translate is not None or scale is not None or rotation!=0 or origin is not None):
            raise ValueError("Use matrix or decomposed transform, not both")
        if matrix is None:
            tx,ty=translate or [0,0]; sx,sy=scale or [1,1]; ox,oy=origin or [doc.width/2,doc.height/2]
            for v in (tx,ty,sx,sy,ox,oy,rotation): finite(v,-1e7,1e7)
            angle=math.radians(rotation); a,b,d,e=math.cos(angle)*sx,-math.sin(angle)*sy,math.sin(angle)*sx,math.cos(angle)*sy
            matrix=[a,b,ox+tx-a*ox-b*oy,d,e,oy+ty-d*ox-e*oy]
        if selection_only:
            if doc.selection is None or layer.kind!="raster": raise ValueError("selection_only requires a selected raster layer")
            cut=layer.image.copy(); cut.putalpha(ImageChops.multiply(cut.getchannel("A"),doc.selection))
            remainder=layer.image.copy(); remainder.putalpha(ImageChops.multiply(remainder.getchannel("A"),ImageChops.invert(doc.selection)))
            layer.image=Image.alpha_composite(remainder,transformed(cut,matrix,resample=resample))
            doc.selection=transformed(doc.selection,matrix,resample=resample)
        else:
            layer.image=transformed(layer.image,matrix,resample=resample)
            if layer.mask is not None: layer.mask=transformed(layer.mask,matrix,resample=resample)
            if layer.kind!="raster":
                def affine(m): return np.array([[m[0],m[1],m[2]],[m[3],m[4],m[5]],[0,0,1]],dtype=float)
                for obj in layer.objects:
                    m=affine(matrix)@affine(obj.get("transform",[1,0,0,0,1,0]))
                    obj["transform"]=list(m[:2].reshape(-1))
                render_objects(layer)
        return {"layer_id":layer.id,"matrix":matrix,"selection_only":selection_only,"resample":resample}

    def paint_history(self,action="undo",steps=1):
        doc=self.doc
        if action not in {"undo","redo"}: raise ValueError("action must be undo or redo")
        if type(steps) is not int or not 1<=steps<=100: raise ValueError("steps must be 1..100")
        source=doc.undo_stack if action=="undo" else doc.redo_stack
        target=doc.redo_stack if action=="undo" else doc.undo_stack
        if len(source)<steps: raise ValueError(f"Not enough history ({len(source)} {action} step(s) available); no changes applied")
        for _ in range(steps):
            # Undo/redo restore the scene; the document's path, save state and current brush stay.
            path,saved_revision,revision,brush,color=doc.path,doc.saved_revision,doc.revision,doc.brush,doc.color
            entry=source.pop()
            current=doc.snapshot()
            doc.restore(entry)
            target.append(doc.compact(current))
            doc.path,doc.saved_revision,doc.revision,doc.brush,doc.color=path,saved_revision,revision+1,brush,color
        return doc.info()

    def paint_batch(self,actions=None,expected_revision=None,actions_file=None,results="compact"):
        """Atomic sequence (one undo step). actions inline (<=256) or actions_file (.json/.jsonl, <=20000).
        An action may "store" its result under a name; later arguments use {"$ref": "name.key"}.
        results: compact (ids and union bounds), full, or none (summary only)."""
        if expected_revision is not None and expected_revision!=self.doc.revision: raise ValueError("Document revision conflict")
        if results not in {"compact","full","none"}: raise ValueError("results is compact, full or none")
        if (actions is None)==(actions_file is None): raise ValueError("Provide either actions (inline) or actions_file (path to .json/.jsonl)")
        if actions_file is not None:
            actions=load_batch_file(actions_file)
            limit=FILE_BATCH_LIMIT
        else: limit=INLINE_BATCH_LIMIT
        if not isinstance(actions,list) or not 1<=len(actions)<=limit:
            raise ValueError(f"Expected 1..{limit} actions" + ("; put larger plans in actions_file" if actions_file is None else "; split the plan"))
        for index,item in enumerate(actions):
            if not isinstance(item,dict) or not {"tool"}<=set(item)<={"tool","arguments","store"} or item["tool"] not in BATCH_TOOLS:
                raise ValueError(f"Batch action {index}: expected {{tool, arguments[, store]}} with tool in {sorted(BATCH_TOOLS)} "
                                 "(no filesystem actions or nested batches)")
            arguments=item.get("arguments",{})
            if not isinstance(arguments,dict): raise ValueError(f"Batch action {index}: arguments must be an object")
            if arguments.get("document_id") not in {None,self.active_document}:
                raise ValueError("A batch edits one document; pass document_id to paint_batch, not to its actions")
            if item["tool"]=="paint_brush" and arguments.get("action") in {"import_presets","export_presets"}:
                raise ValueError("Filesystem actions are not permitted inside a batch")
            if "store" in item and (not isinstance(item["store"],str) or not item["store"] or "." in item["store"]):
                raise ValueError(f"Batch action {index}: store is a name without dots")
        self._batch_depth+=1
        outer=self._audit_buffer is None
        if outer: self._audit_buffer=[]
        collected=[]; stored={}; counts={}; bbox=None
        try:
            for index,item in enumerate(actions):
                if self._signal.is_set(): raise InterruptedError("Batch aborted")
                try:
                    arguments=resolve_refs(item.get("arguments",{}),stored)
                    result=self.call(item["tool"],**arguments)
                except Exception as error:
                    raise ValueError(f"Batch action {index} ({item['tool']}) failed; entire batch rolled back: {error}") from error
                counts[item["tool"]]=counts.get(item["tool"],0)+1
                small=compact_result(result)
                if isinstance(small,dict) and isinstance(small.get("bbox"),list) and len(small["bbox"])==4:
                    b=small["bbox"]; bbox=b if bbox is None else [min(bbox[0],b[0]),min(bbox[1],b[1]),max(bbox[2],b[2]),max(bbox[3],b[3])]
                if "store" in item: stored[item["store"]]=result
                if results=="full": collected.append(result)
                elif results=="compact": collected.append(small)
        finally:
            self._batch_depth-=1
            if outer: self._flush_audit()
        summary={"completed":len(actions),"atomic":True,"undo_steps":1,"tools":counts,"bbox":bbox,
                 "stored":{name:compact_result(value) for name,value in stored.items()}}
        if results!="none": summary["results"]=collected
        if actions_file is not None: summary["actions_file"]=str(Path(actions_file).expanduser().resolve())
        return summary

    def paint_view(self,rectangle=None,zoom=None,center=None,rotation=0):
        doc=self.doc
        if rectangle is not None:
            rect=list(self._rectangle(rectangle))
        elif zoom is not None:
            finite(zoom,.01,128)
            cx,cy=center or [doc.width/2,doc.height/2]
            for v in (cx,cy): finite(v)
            w,h=doc.width/zoom,doc.height/zoom
            rect=[cx-w/2,cy-h/2,cx+w/2,cy+h/2]
        else: rect=[0,0,*doc.size]
        finite(rotation,-36000,36000)
        doc.view={"rectangle":rect,"rotation":rotation}
        return {"view":copy.deepcopy(doc.view),"drawing_coordinates":"unchanged document pixels"}

    def paint_region(self,name,action="define",rectangle=None):
        if not name or len(name)>128: raise ValueError("Region needs a name of at most 128 characters")
        if action=="define": self.doc.regions[name]=list(self._rectangle(rectangle))
        elif action=="delete":
            if name not in self.doc.regions: raise ValueError("Unknown region")
            del self.doc.regions[name]
        elif action=="get":
            if name not in self.doc.regions: raise ValueError("Unknown region")
        else: raise ValueError("Region action must be define/get/delete")
        return {"name":name,"rectangle":self.doc.regions.get(name),"coordinate_space":"document"}

    def _snapshot_record(self,identifier):
        if identifier in self.snapshots: return self.snapshots[identifier]
        path=self.state_dir/"snapshots"/(identifier+".json")
        if len(identifier)>80 or not all(c.isalnum() or c in "-_" for c in identifier) or not path.is_file(): raise ValueError("Unknown snapshot id")
        return json.loads(path.read_text("utf-8"))

    def paint_snapshot(self,label="Snapshot",rectangle=None,region=None,layer_id=None,max_side=1600,path=None,
                       compare_to=None,use_view=False,transparent=False,source="composite",overwrite=False,upscale=False):
        doc=self.doc
        if type(max_side) is not int or not 64<=max_side<=8192: raise ValueError("max_side must be integer 64..8192")
        if type(upscale) is not bool or type(overwrite) is not bool: raise ValueError("upscale and overwrite require booleans")
        if region:
            if rectangle is not None: raise ValueError("Use region or rectangle")
            if region not in doc.regions: raise ValueError("Unknown region")
            rectangle=doc.regions[region]
        if rectangle is not None: box=self._rectangle(rectangle)
        elif use_view: box=tuple(doc.view.get("rectangle",[0,0,*doc.size]))
        else: box=(0,0,*doc.size)
        if source=="selection":
            if doc.selection is None: raise ValueError("No selection")
            rendered=doc.selection.convert("RGBA")
        elif source=="mask":
            mask=doc.layer(layer_id).mask
            if mask is None: raise ValueError("Layer has no mask")
            rendered=mask.convert("RGBA")
        elif source=="layer" or source=="composite" and layer_id:
            rendered=doc.layer(layer_id).image.copy()
        elif source=="composite": rendered=composite(doc,transparent)
        else: raise ValueError("source must be composite, layer, selection or mask")
        image=rendered.crop(tuple(map(round,box)))
        if use_view and doc.view.get("rotation"): image=image.rotate(-doc.view["rotation"],Image.Resampling.BICUBIC,expand=True)
        image.thumbnail((max_side,max_side),Image.Resampling.LANCZOS)
        factor=1
        if upscale:
            # Integer nearest-neighbour enlargement keeps every document pixel a crisp square (pixel art, fine detail).
            factor=min(64,max_side//max(image.size))
            if factor>1: image=image.resize((image.width*factor,image.height*factor),Image.Resampling.NEAREST)
            else: factor=1
        identifier=uid(); directory=self.state_dir/"snapshots"; directory.mkdir(exist_ok=True)
        image_path=directory/(identifier+".png"); image.save(image_path)
        resolved_layer=doc.layer(layer_id).id if layer_id or source in {"layer","mask"} else None
        descriptor={"document_id":doc.id,"rectangle":list(box),"layer_id":resolved_layer,
                    "rotation":doc.view.get("rotation",0) if use_view else 0,"transparent":transparent,"source":source}
        info={"snapshot_id":identifier,"label":label,"revision":doc.revision,"descriptor":descriptor,
              "image_path":str(image_path),"size":list(image.size),"sha256":hashlib.sha256(image.tobytes()).hexdigest(),
              "document_pixels_per_image_pixel":[(box[2]-box[0])/image.width,(box[3]-box[1])/image.height],
              "upscale_factor":factor}
        if compare_to:
            prior=self._snapshot_record(compare_to)
            if prior["descriptor"]!=descriptor: raise ValueError("Snapshots must cover the same document, layer and rectangle")
            with Image.open(prior["image_path"]) as earlier:
                before=earlier.convert("RGBA")
                if before.size!=image.size: before=before.resize(image.size,Image.Resampling.LANCZOS)
                delta=np.abs(np.asarray(before,dtype=np.int16)-np.asarray(image,dtype=np.int16))
                info["comparison"]={"compare_to":compare_to,"changed_pixel_fraction":float(np.any(delta,axis=2).mean()),
                                    "mean_absolute_channel_difference":float(delta.mean()),"semantic_score":None}
        (directory/(identifier+".json")).write_text(json.dumps(info,ensure_ascii=False,indent=2),"utf-8")
        self.snapshots[identifier]=info
        if path:
            info["saved_path"]=atomic_write(path,lambda stream:stream.write(png(image)),overwrite=overwrite)
        return Picture(info,image)

    def paint_compare(self,reference_path,rectangle=None,reference_rectangle=None,max_side=1600):
        check_path(reference_path)
        source=composite(self.doc)
        if rectangle is not None: source=source.crop(self._rectangle(rectangle))
        with Image.open(reference_path) as reference:
            dimensions(*reference.size)
            reference=reference.convert("RGBA")
        if reference_rectangle:
            if len(reference_rectangle)!=4: raise ValueError("Reference crop uses pixel edges")
            l,t,r,b=reference_rectangle
            if not 0<=l<r<=reference.width or not 0<=t<b<=reference.height: raise ValueError("Reference crop outside image")
            reference=reference.crop(tuple(map(round,reference_rectangle)))
        finite(max_side,64,8192)
        ratios=[im.width/im.height for im in (reference,source)]
        height=max(1,math.floor(min(max_side,(max_side-16)/sum(ratios))))
        widths=[max(1,round(r*height)) for r in ratios]
        image=Image.new("RGBA",(sum(widths)+16,height),"#303030")
        image.alpha_composite(reference.resize((widths[0],height),Image.Resampling.LANCZOS),(0,0))
        image.alpha_composite(source.resize((widths[1],height),Image.Resampling.LANCZOS),(widths[0]+16,0))
        return Picture({"reference":"left","painting":"right","analysis_only":True,"reference_imported":False},image)

    def paint_sample(self,points,sample_merged=True,layer_id=None):
        source=composite(self.doc) if sample_merged and layer_id is None else self.doc.layer(layer_id).image
        result=[]
        for point in points:
            if len(point)!=2: raise ValueError("Sample points use [x,y]")
            x,y=map(round,point)
            if not 0<=x<self.doc.width or not 0<=y<self.doc.height: raise ValueError("Sample outside document")
            value=source.getpixel((x,y)); result.append({"point":[x,y],"rgba":value,"hex":"#"+"".join(f"{v:02X}" for v in value)})
        return {"samples":result}

    def paint_fonts(self,query="",supports=None):
        """List fonts; supports=<text> keeps only fonts that have a glyph for every visible character."""
        from PIL import ImageFont
        if supports is not None and (not isinstance(supports,str) or len(supports)>2000):
            raise ValueError("supports is the sample text (at most 2000 characters)")
        result=[]
        for p in fonts():
            if query.casefold() not in Path(p).stem.casefold(): continue
            if supports:
                try:
                    if missing_glyphs(ImageFont.truetype(p,24),supports): continue
                except OSError: continue
            result.append({"name":Path(p).stem,"path":p})
        return {"fonts":result,"supports":supports}

    def paint_filter(self,kind,radius=0,offsets=None,amount=.1,seed=1,monochrome=True,layer_id=None,angle=135,
                     source="luminance"):
        """Pixel filters on a raster layer, limited to the selection and honouring alpha lock."""
        doc=self.doc; layer=doc.layer(layer_id)
        if layer.locked: raise ValueError("Layer is locked")
        if layer.kind!="raster": raise ValueError("Filters need a raster layer; rasterize text/vector first (or stamp_visible)")
        if kind not in FILTER_KINDS: raise ValueError(f"Filter kind must be one of {sorted(FILTER_KINDS)}")
        original=layer.image
        if kind=="blur":
            finite(radius,0,250,"radius")
            filtered=gaussian_blur(original,radius)
        elif kind=="relief":
            finite(radius,0,50,"radius"); finite(amount,0,1,"amount"); finite(angle,-36000,36000,"angle")
            if source not in {"luminance","alpha"}: raise ValueError("relief source is luminance (brushwork) or alpha (cut shapes)")
            filtered=relief(original,angle,amount,radius,source)
        elif kind=="channel_shift":
            offsets=offsets or {}
            if not isinstance(offsets,dict) or not offsets or set(offsets)-{"r","g","b"}:
                raise ValueError("offsets maps r/g/b to integer [dx,dy] pixel shifts, e.g. {'r':[3,0],'b':[-3,0]}")
            parsed={}
            for key,value in offsets.items():
                if not isinstance(value,(list,tuple)) or len(value)!=2: raise ValueError("Each offset is [dx,dy]")
                if any(int(finite(v,-4096,4096))!=v for v in value): raise ValueError("Channel offsets are whole pixels")
                parsed[key]=(int(value[0]),int(value[1]))
            filtered=channel_shift(original,parsed)
        else:
            finite(amount,0,1)
            if type(seed) is not int or seed<0: raise ValueError("seed must be a nonnegative integer")
            if type(monochrome) is not bool: raise ValueError("monochrome requires boolean")
            filtered=add_noise(original,amount,seed,monochrome)
        if doc.selection is not None: filtered=Image.composite(filtered,original,doc.selection)
        if layer.alpha_lock: filtered.putalpha(original.getchannel("A"))
        layer.image=filtered
        return {"filtered":kind,"layer_id":layer.id,"selection_applied":doc.selection is not None}

    def paint_pattern(self,kind,color=None,opacity=1,blend_mode="normal",angle=45,angles=None,spacing=8,width=1.5,
                      jitter=0,taper=0,dash=None,density=None,size=None,size_jitter=0,gradient=None,cell=1,texture=0,
                      points=None,rectangle=None,smooth=False,seed=1,layer_id=None):
        """Pattern fills drawn by the engine inside an area: hatch/crosshatch lines, stipple dots, halftone
        grids and ordered dither. The area is points (polygon) or rectangle, intersected with the selection;
        without either the selection (or the whole layer) is filled. gradient modulates density."""
        doc=self.doc; layer=doc.writable(layer_id)
        if kind not in PATTERN_KINDS: raise ValueError(f"Pattern kind must be one of {sorted(PATTERN_KINDS)}")
        colour=rgba(color or self.color)
        finite(opacity,0,1,"opacity")
        if blend_mode not in BLENDS: raise ValueError(f"Unsupported blend mode; use one of {sorted(BLENDS)}")
        if angles is None: angles=[angle]
        if not isinstance(angles,list) or not 1<=len(angles)<=8: raise ValueError("angles is a list of 1..8 directions in degrees")
        for a in angles: finite(a,-36000,36000,"angle")
        finite(spacing,1 if kind!="hatch" else .75,4096,"spacing"); finite(width,.1,512,"width")
        finite(jitter,0,1,"jitter"); finite(taper,0,1,"taper"); finite(size_jitter,0,1,"size_jitter"); finite(texture,0,1,"texture")
        if density is None: density=PATTERN_DEFAULT_DENSITY[kind]
        finite(density,0,1,"density")
        if size is None: size=spacing*.5 if kind=="stipple" else spacing*1.15
        finite(size,.1,4096,"size")
        if type(cell) is not int or not 1<=cell<=256: raise ValueError("cell is an integer 1..256 (dither pixel size)")
        if type(seed) is not int or seed<0: raise ValueError("seed must be a nonnegative integer")
        if type(smooth) is not bool: raise ValueError("smooth requires boolean")
        params={"angles":angles,"spacing":spacing,"width":width,"jitter":jitter,"taper":taper,
                "dash":validate_dash(dash) if dash is not None else None,"density":density,"size":size,
                "size_jitter":size_jitter,"gradient":validate_gradient(gradient),"cell":cell,"seed":seed}
        # Area: optional polygon/rectangle intersected with the selection.
        area=doc.selection
        if points is not None or rectangle is not None:
            if points is not None and rectangle is not None: raise ValueError("Use points (polygon) or rectangle, not both")
            if points is not None:
                pts=validate_points(points,3)
                outline=smooth_outline(pts,True) if smooth else [(x,y) for x,y,p in pts]
                aa=3 if doc.width*doc.height<=4_000_000 else 1
                region=Image.new("L",(doc.width*aa,doc.height*aa))
                ImageDraw.Draw(region).polygon([(x*aa,y*aa) for x,y in outline],fill=255)
                if aa!=1: region=region.resize(doc.size,Image.Resampling.BOX)
            else:
                l,t,r,b=self._selection_box(rectangle)
                region=Image.new("L",doc.size); ImageDraw.Draw(region).rectangle((l,t,r-1,b-1),fill=255)
            area=ImageChops.multiply(area,region) if area is not None else region
        box=area.getbbox() if area is not None else (0,0,*doc.size)
        if box is None: return {"pattern":kind,"layer_id":layer.id,"marks":0,"bbox":None,"note":"Empty area: nothing painted"}
        coverage,marks=pattern_coverage(kind,box,params)
        alpha=np.asarray(coverage,dtype=np.float32)
        if texture:
            alpha*=1-texture+texture*paper_noise(seed,box)
        alpha*=opacity*colour[3]/255
        patch=Image.new("RGBA",coverage.size,colour[:3]+(255,))
        patch.putalpha(Image.fromarray(np.uint8(np.clip(alpha+.5,0,255))))
        apply_paint(layer,patch,box,area,False,blend_mode)
        return {"pattern":kind,"layer_id":layer.id,"marks":marks,"bbox":list(box),
                "area":"polygon" if points is not None else "rectangle" if rectangle is not None else
                       "selection" if doc.selection is not None else "layer"}


OPERATIONS=("paint_state","paint_document","paint_layer","paint_brush","paint_strokes","paint_selection","paint_fill",
            "paint_shape","paint_text","paint_object","paint_transform","paint_history","paint_batch","paint_view",
            "paint_region","paint_snapshot","paint_compare","paint_sample","paint_fonts","paint_filter","paint_pattern",
            "paint_abort")
