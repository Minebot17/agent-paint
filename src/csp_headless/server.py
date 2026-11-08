"""Thin MCP transport over Engine.call; STDIO by default, optional loopback HTTP."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import threading
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .engine import Engine, Picture
from .storage import png

GUIDE="""Agent Paint 0.3 is an independent desktop-free painting engine. All drawing coordinates are
document pixels (strokes may opt into normalized 0..1). Pressure and opacity are 0..1.
No window, foreground app, pointer, OCR, UI binding, calibration, duration or sleep is involved.

START: paint_document(action='create',width=...,height=...) returns document_id. Several agents may
share one server: pass document_id on EVERY call. Different documents are painted in parallel; calls
on one document run one at a time. Each document owns its brush and colour
(paint_brush with document_id), so nobody else can change your brush; a new document starts from
a copy of the active document's brush.

LAYERS: stable IDs, stack indices bottom-to-top. layer_id also accepts a unique layer NAME, so name
layers ('sky', 'ink') and target them directly. paint_strokes, paint_shape, paint_fill,
paint_pattern, paint_filter, paint_transform, paint_sample and paint_selection(from_layer/flood)
take layer_id and paint there WITHOUT changing the active layer. paint_layer(create) inserts above
the active layer by default; position='top'|'bottom' or index chooses the place; results report
index and layer_count. stamp_visible adds a merged copy of everything visible (for whole-image
grain, chromatic aberration, relief). duplicate goes above its source and becomes active;
merge_down keeps the lower layer's id. Raster layers take pixels, vector layers keep editable
shapes/strokes, text layers keep editable text. Locked layers reject painting. alpha_lock keeps
coverage; erasing or clear on an alpha-locked layer is an ERROR (unlock first).

BRUSH & STROKES: paint_brush(action='configure',settings={...},color='#RRGGBB'). Kinds: round,
pencil, airbrush, watercolor, marker, eraser, smudge (own renderers, not CSP). Settings: size px,
opacity, flow, hardness, spacing, min_size (width floor at zero pressure), taper_start/end,
texture + seed, texture_space stroke|paper (paper = one document-fixed tooth for chalk, pastel,
charcoal), edge_darkening (watercolor rim; works at any hardness), mixing, stabilization (a
moving average over stabilization*50 px of arc; 0 keeps your geometry), blend.
paint_strokes(strokes=[{points:[[x,y,p],...], brush:{...}, color, smooth, dash, dash_offset,
closed}], brush={...}, color=..., smooth=..., layer_id=...): call-level brush/color/smooth are
defaults for all strokes. smooth=true draws a Catmull-Rom curve THROUGH the points (pressure too):
5-10 points make a clean curve, tapers stay pointed, no polyline corners. Without smooth a stroke
is a polyline. dash=[on,off,...] px makes dashed lines; dash=[0,gap] makes round dots of the brush
size every gap px (stitches, dotted guides, bead rows). Press-and-lift leaves: pressure
0.15 -> 1 -> 0 with smooth=true.

SHAPES & SELECTIONS: paint_shape renders only inside its bounds (thousands are cheap). Draw fill
and outline in ONE call to keep lines on edges. smooth=true turns polygon/line vertices into a
smooth curve (organic silhouettes from few points). bleed=0.5..1 hides hairline seams between
adjacent antialiased facets (low-poly, mosaic, stained glass). antialias=false gives exact pixels:
box shapes and polygons both cover [x0,x1) (pixel-centre rule), so adjacent hard shapes tile
without overlap. Points are pairs [[x,y],...], never a flat list. paint_selection: polygon (smooth
too), rectangle, ellipse, flood, from_layer, add/subtract/intersect, feather/grow/shrink; None =
unrestricted, empty = nothing writable; rectangles/ellipses may extend past the canvas.

FILLS, PATTERNS, FILTERS: paint_fill solid/flood/linear/radial gradient; kind='clear' erases.
paint_pattern draws document-anchored patterns inside points (polygon), rectangle or the selection:
hatch (angle or angles=[45,-45] crosshatch, spacing, width, jitter for hand-made breaks and wobble,
taper, dash), stipple (spacing, size, size_jitter, density), halftone (Ben-Day / print dots: angle,
spacing, size), dither (ordered Bayer, cell = art-pixel size, e.g. 6 for pixel art). density 0..1
and gradient={type:linear|radial,start,end,from,to} modulate tone (hatch width, dot size, dot
probability, dither level); texture adds paper grain. paint_filter: blur (premultiplied; glow =
duplicate + blur + screen/add; soft shadows), channel_shift (misregistration), noise (seeded
grain), relief (emboss lighting from luminance = brushwork/impasto, or alpha = cut paper, foil;
angle 135 = light from upper left). paint_transform(resample='nearest') keeps pixel art crisp.

TEXT: paint_text creates editable text; style: font, font_size, color, outline_width/color, align
left|center|right|justify, letter_spacing (px tracking), line_height (absolute baseline distance;
line_spacing is the extra gap), scale_x/y, rotation, anchor l/m/r + t/m/b/s ('mm' centres on x,y,
'ls' puts the first BASELINE on y: ruled paper, manuscripts). Returns bbox; missing_glyphs warns
about tofu; paint_fonts(supports='text') finds covering fonts. Editable text has no grain: add
texture to a rasterized/stamped copy if needed.

BATCHES & BIG PLANS: paint_batch(actions=[{tool,arguments,store?},...]) is atomic: one undo step,
any failure rolls back scene and brush. Inline <=256 actions; actions_file='plan.jsonl' or .json
runs up to 20000 actions from disk without sending coordinates through the chat (generate
coordinates with a script, then let the engine draw them). An action with "store":"ink" makes its
result referable later as {"$ref":"ink.layer_id"}. results='compact' (default) returns ids and
union bboxes; 'none' only a summary; 'full' everything. paint_layer list/state report object
counts; paint_object(action='list') returns objects. Undo history stores only changed layers.

FILES: save an editable .apaint (paint_document save), then export PNG; export never saves the
project: its project_saved=true only when the .apaint on disk is current, else it says what to do.
Opening a project that is already open selects it (force=true reloads from disk); another copy of
the same document opens under a new document_id. paint_document(action='inspect',path=...)
verifies a saved project read-only (layers, objects, revision) without opening it. ORA/PSD
rasterize text/vectors and bake masks/clipping; .apaint keeps everything editable. Existing paths
need overwrite=true; ordinary Save uses the project's own path.

LOOK: paint_snapshot returns actual pixels (regions stay valid across preview zoom; upscale=true
for crisp small crops; compare_to reports pixel change, never beauty). paint_compare shows a
reference beside the painting and never imports it. Successful calls do not prove a good painting:
inspect after each meaningful pass, judge composition, value masses, edges and material, and
change the method when a second pass fails. Watch for mechanical regularity, mirror symmetry and
empty corners; vary spacing, size and angle on purpose.
"""

mcp=FastMCP("Agent Paint",instructions=GUIDE,log_level="WARNING")
mcp._mcp_server.version=__version__
_engine=None


def engine():
    global _engine
    if _engine is None: _engine=Engine()
    return _engine


async def execute(tool,**arguments):
    cancelled=threading.Event()
    try:
        result=await asyncio.to_thread(engine().call,tool,_cancel_event=cancelled,**arguments)
        if isinstance(result,Picture):
            data=result.png if result.png is not None else png(result.image)
            return CallToolResult(content=[TextContent(type="text",text=json.dumps(result.info,ensure_ascii=False)),
                Image(data=data,format="png").to_image_content()],structuredContent=result.info)
        return CallToolResult(content=[TextContent(type="text",text=json.dumps(result,ensure_ascii=False))],structuredContent=result)
    except asyncio.CancelledError:
        cancelled.set()
        raise
    except Exception as error:
        info={"error":type(error).__name__,"message":str(error),"tool":tool,
              "scene_mutations_rolled_back":tool in {"paint_strokes","paint_layer","paint_selection","paint_fill","paint_shape","paint_text","paint_object","paint_transform","paint_batch","paint_region","paint_filter","paint_pattern"}}
        return CallToolResult(isError=True,structuredContent=info,content=[TextContent(type="text",text=json.dumps(info,ensure_ascii=False))])


class StrictModel(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)


class Stroke(StrictModel):
    points: list[list[float]]=Field(min_length=1,max_length=100000,description="Document pixels [[x,y],[x,y,pressure],...]. One point is a dot; pressure 0..1.")
    closed: bool=False
    brush: dict=Field(default_factory=dict,description="Per-stroke brush overrides; see paint_brush")
    color: str | None=None
    smooth: bool | None=Field(default=None,description="Catmull-Rom curve through the points (and pressures) instead of a polyline")
    dash: list[float] | None=Field(default=None,description="[on,off,...] px; [0,gap] = round dots every gap px")
    dash_offset: float | None=None


class BatchAction(StrictModel):
    tool: Literal["paint_layer","paint_brush","paint_strokes","paint_selection","paint_fill","paint_shape","paint_text","paint_object","paint_transform","paint_filter","paint_pattern"]
    arguments: dict=Field(default_factory=dict)
    store: str | None=Field(default=None,description="Keep this action's result; later arguments may use {\"$ref\": \"name.key\"}")


DocumentId=Annotated[str|None,Field(description="Target this document instead of the shared active one (recommended when several agents share the server)")]
LayerId=Annotated[str|None,Field(description="Target layer id or unique layer name; the active layer stays unchanged. Default: active layer")]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def paint_state() -> CallToolResult:
    """Inspect authoritative scene/layer/brush state, document IDs, capabilities and limits. No desktop access."""
    return await execute("paint_state")


@mcp.tool()
async def paint_document(action:Literal["create","info","list","select","close","open","save","export","inspect","import_image","import_ora"]="info",
    document_id:str|None=None,width:int=1024,height:int=1024,name:str="Untitled",background:str|None="#FFFFFF",dpi:float=144,
    path:str|None=None,overwrite:bool=False,transparent:bool=False,force:bool=False,scale:int=1) -> CallToolResult:
    """Create/open/save .apaint; export full-resolution PNG/JPEG/WebP/TIFF or raster-layer ORA/PSD (scale 2..16 = nearest-neighbour enlargement for flat formats). Export never saves the project: project_saved tells whether the .apaint on disk is current. inspect verifies a saved .apaint read-only. Opening an already open project selects it (force=true reloads). document_id selects/closes, or targets info/save/export without changing the active document. Existing paths protected unless overwrite. Close dirty document requires force."""
    return await execute("paint_document",**locals())


@mcp.tool()
async def paint_layer(action:Literal["list","info","create","select","duplicate","update","reorder","delete","clear","rasterize","mask_from_selection","mask_remove","mask_invert","mask_apply","merge_down","flatten","stamp_visible"]="list",
    layer_id:LayerId=None,name:str|None=None,kind:Literal["raster","vector","text"]="raster",properties:dict|None=None,index:int|None=None,
    position:Literal["above_active","top","bottom"]|None=None,include_background:bool=True,
    document_id:DocumentId=None) -> CallToolResult:
    """Manage layers by id or unique name. Indices bottom-to-top. create inserts above the active layer unless position (top/bottom) or index is given; reorder takes index or position top/bottom. stamp_visible adds a merged copy of everything visible (default at the top). properties (on create or update): name, opacity(0..1), blend, visible, locked, alpha_lock, clipping. Results include index and layer_count; list reports object_count (paint_object lists objects)."""
    return await execute("paint_layer",**locals())


@mcp.tool()
async def paint_brush(action:Literal["get","list","configure","save_preset","load_preset","delete_preset","export_presets","import_presets"]="get",
    settings:dict|None=None,color:str|None=None,name:str|None=None,path:str|None=None,overwrite:bool=False,
    document_id:DocumentId=None) -> CallToolResult:
    """Configure the document's own brush (other agents' documents are unaffected) and shared presets. kind=round/pencil/airbrush/watercolor/marker/eraser/smudge; size px; opacity,hardness,flow,spacing,min_size,taper_start,taper_end,texture,stabilization,mixing 0..1 (spacing up to 2); pressure_size/pressure_opacity bool; seed integer; blend; texture_space stroke|paper (paper = grain fixed to the document); edge_darkening 0..1 (pigment pools at the mark edge). stabilization averages over stabilization*50 px of arc (0 keeps geometry). Color #RRGGBB[AA]."""
    return await execute("paint_brush",**locals())


@mcp.tool()
async def paint_strokes(strokes:Annotated[list[Stroke],Field(min_length=1,max_length=1024)],
    coordinate_space:Literal["document","normalized"]="document",expected_revision:int|None=None,
    layer_id:LayerId=None,brush:dict|None=None,color:str|None=None,smooth:bool=False,document_id:DocumentId=None) -> CallToolResult:
    """Render an atomic stroke batch with real pressure, taper and antialiasing into layer_id (default active). brush/color/smooth here are defaults for every stroke; per-stroke values override. smooth=true draws Catmull-Rom curves through the points; dash=[on,off] dashes, [0,gap] dots. Document pixels independent of preview. Failure rolls back the complete call."""
    return await execute("paint_strokes",strokes=[s.model_dump(exclude_none=True) for s in strokes],coordinate_space=coordinate_space,
                         expected_revision=expected_revision,layer_id=layer_id,brush=brush,color=color,smooth=smooth,document_id=document_id)


@mcp.tool()
async def paint_selection(action:Literal["none","all","polygon","rectangle","ellipse","invert","feather","grow","shrink","flood","from_layer"]="none",
    points:list[list[float]]|None=None,rectangle:list[float]|None=None,point:list[float]|None=None,
    operation:Literal["replace","add","subtract","intersect"]="replace",radius:float=0,tolerance:float=0,sample_merged:bool=False,
    antialias:bool=True,smooth:bool=False,layer_id:LayerId=None,document_id:DocumentId=None) -> CallToolResult:
    """Set an exact pixel selection mask immediately. rectangle=[left,top,right,bottom], document pixels; may extend past the canvas. antialias=false gives hard 0/255 edges (polygons use the pixel-centre rule, [x0,x1)). smooth=true makes a polygon a smooth curve through its points. from_layer/flood read layer_id (default active). Feather/grow/shrink radius <=100. Flood tolerance 0..255 (binary). Selection constrains subsequent raster operations."""
    return await execute("paint_selection",**locals())


@mcp.tool()
async def paint_fill(kind:Literal["solid","flood","linear","radial","clear"]="solid",color:str|None=None,color2:str="#FFFFFF",
    start:list[float]|None=None,end:list[float]|None=None,point:list[float]|None=None,tolerance:float=0,sample_merged:bool=False,
    opacity:float=1,blend_mode:str="normal",layer_id:LayerId=None,document_id:DocumentId=None) -> CallToolResult:
    """Fill a raster layer (layer_id or active) within the selection. Gradient start/end or flood point in document pixels. Opacity 0..1. Flood samples the target layer or merged painting. kind=clear erases the selection (or whole layer) to transparency; opacity<1 erases partially; clearing an alpha-locked layer is an error."""
    return await execute("paint_fill",**locals())


@mcp.tool()
async def paint_shape(shape:Literal["rectangle","rounded_rectangle","ellipse","polygon","line","bezier"],points:list[list[float]],
    fill:str|None=None,outline:str|None=None,width:float=1,radius:float=12,editable:bool=False,antialias:bool=True,
    smooth:bool=False,bleed:float=0,layer_id:LayerId=None,document_id:DocumentId=None) -> CallToolResult:
    """Geometry rendered only within its own bounds (fast even on big canvases). Points are pairs [[x,y],...]. Box shapes use two corners; cubic bezier four control points. Fill+outline in one call keeps outlines on edges. smooth=true: polygon/line through its points as a smooth curve. bleed px widens the fill to hide seams between adjacent antialiased facets. antialias=false paints hard pixels (boxes and polygons cover [x0,x1)). Vector layer retains editable objects, raster layer paints pixels. Pixel selection applies only to raster."""
    return await execute("paint_shape",**locals())


@mcp.tool()
async def paint_text(text:str,x:float,y:float,style:dict|None=None,layer_id:str|None=None,object_id:str|None=None,
    document_id:DocumentId=None) -> CallToolResult:
    """Create editable Unicode text (a new text layer above the active layer, or into layer_id), or update object by IDs. Style: font(name/path),font_size,color,outline_width,outline_color,letter_spacing(px tracking, keeps kerning),line_height(absolute px baseline-to-baseline) or line_spacing(extra px gap),align(left/center/right/justify),scale_x,scale_y,rotation(clockwise degrees),anchor(lt default; l/m/r + t/m/b/s; mm centres on x,y; ls puts the first baseline on y). Returns bbox (ink bounds) and missing_glyphs if the font lacks characters."""
    return await execute("paint_text",**locals())


@mcp.tool()
async def paint_object(action:Literal["list","get","update","duplicate","delete"]="list",object_id:str|None=None,
    layer_id:str|None=None,changes:dict|None=None,document_id:DocumentId=None) -> CallToolResult:
    """Edit retained text/vector objects by stable IDs. update changes geometry/style/brush or six-coefficient transform, then rerenders from retained source."""
    return await execute("paint_object",**locals())


@mcp.tool()
async def paint_transform(matrix:list[float]|None=None,translate:list[float]|None=None,scale:list[float]|None=None,rotation:float=0,
    origin:list[float]|None=None,layer_id:LayerId=None,selection_only:bool=False,
    resample:Literal["bicubic","bilinear","nearest"]="bicubic",document_id:DocumentId=None) -> CallToolResult:
    """Affine layer transform (document canvas fixed). matrix=[a,b,c,d,e,f], x'=ax+by+c,y'=dx+ey+f; or translate/scale/clockwise degrees about origin. resample='nearest' keeps pixel art crisp. Text/vector source remains editable. selection_only cuts/moves selected raster pixels."""
    return await execute("paint_transform",**locals())


@mcp.tool()
async def paint_history(action:Literal["undo","redo"]="undo",steps:int=1,document_id:DocumentId=None) -> CallToolResult:
    """Undo/redo complete atomic scene operations. A batch is one step. Bounded in-memory history; .apaint stores current scene, not undo buffers."""
    return await execute("paint_history",**locals())


@mcp.tool()
async def paint_batch(actions:Annotated[list[BatchAction]|None,Field(max_length=256)]=None,expected_revision:int|None=None,
    actions_file:str|None=None,results:Literal["compact","full","none"]="compact",document_id:DocumentId=None) -> CallToolResult:
    """Atomic synchronous sequence, one undo step. Scene and brush changes roll back together on error. No filesystem actions or nested batches. Each action: {tool,arguments[,store]}; {"$ref":"name.key"} reuses a stored result (e.g. a new layer's id). Inline up to 256 actions, or actions_file = path to .json/.jsonl with up to 20000 actions (generate big plans with a script; coordinates never pass through the chat). results: compact (ids + union bbox), full, none."""
    return await execute("paint_batch",actions=[a.model_dump(exclude_none=True) for a in actions] if actions is not None else None,
                         expected_revision=expected_revision,actions_file=actions_file,results=results,document_id=document_id)


@mcp.tool()
async def paint_view(rectangle:list[float]|None=None,zoom:float|None=None,center:list[float]|None=None,rotation:float=0,
    document_id:DocumentId=None) -> CallToolResult:
    """Set preview crop/zoom/rotation. Does not change any drawing coordinates or pixels. Empty arguments restore overview. Snapshot(use_view=true) displays this view."""
    return await execute("paint_view",**locals())


@mcp.tool()
async def paint_region(name:str,action:Literal["define","get","delete"]="define",rectangle:list[float]|None=None,
    document_id:DocumentId=None) -> CallToolResult:
    """Named document region [left,top,right,bottom], stored in project. Use paint_snapshot(region=name) for stable detail comparisons across preview views."""
    return await execute("paint_region",**locals())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def paint_snapshot(label:str="Snapshot",rectangle:list[float]|None=None,region:str|None=None,layer_id:str|None=None,
    max_side:int=1600,path:str|None=None,compare_to:str|None=None,use_view:bool=False,transparent:bool=False,
    source:Literal["composite","layer","selection","mask"]="composite",overwrite:bool=False,upscale:bool=False,
    document_id:DocumentId=None) -> CallToolResult:
    """Return actual rendered canvas/layer/selection/mask pixels and persistent ID/hash. Matching prior crop reports pixel changes, never beauty. Preview max_side 64..8192; upscale=true enlarges small crops by an integer nearest-neighbour factor up to max_side. path saves the preview (overwrite=true replaces). Use document export for full resolution."""
    return await execute("paint_snapshot",**locals())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def paint_compare(reference_path:str,rectangle:list[float]|None=None,reference_rectangle:list[float]|None=None,max_side:int=1600,
    document_id:DocumentId=None) -> CallToolResult:
    """Show reference left/painting right, equal display height. Crops use pixel edges in their respective images. Analysis only; never imports reference pixels."""
    return await execute("paint_compare",**locals())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def paint_sample(points:list[list[float]],sample_merged:bool=True,layer_id:LayerId=None,document_id:DocumentId=None) -> CallToolResult:
    """Read exact RGBA at document pixel points; merged canvas, or one layer (layer_id, or active with sample_merged=false)."""
    return await execute("paint_sample",**locals())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def paint_fonts(query:str="",supports:str|None=None) -> CallToolResult:
    """List installed TrueType/OpenType font names and paths, optionally filtered by filename. supports=<sample text> keeps only fonts with glyphs for all its characters (e.g. Cyrillic)."""
    return await execute("paint_fonts",**locals())


@mcp.tool()
async def paint_filter(kind:Literal["blur","channel_shift","noise","relief"],radius:float=0,offsets:dict[str,list[int]]|None=None,amount:float=.1,
    seed:int=1,monochrome:bool=True,angle:float=135,source:Literal["luminance","alpha"]="luminance",layer_id:LayerId=None,
    document_id:DocumentId=None) -> CallToolResult:
    """Filter a raster layer within the selection, honouring alpha lock. blur: premultiplied Gaussian radius 0..250 (glow: duplicate layer, blur, blend screen/add; soft shadows). channel_shift: offsets {r:[dx,dy],g:[..],b:[..]} whole pixels (chromatic aberration, misregistration). noise: amount 0..1 seeded grain, monochrome or per-channel. relief: emboss lighting, amount 0..1 strength, radius softness, angle = light direction (135 = upper left), source luminance (brushwork, impasto) or alpha (cut paper, foil). For the whole picture use paint_layer(stamp_visible) first."""
    return await execute("paint_filter",**locals())


@mcp.tool()
async def paint_pattern(kind:Literal["hatch","stipple","halftone","dither"],color:str|None=None,opacity:float=1,blend_mode:str="normal",
    angle:float=45,angles:list[float]|None=None,spacing:float=8,width:float=1.5,jitter:float=0,taper:float=0,
    dash:list[float]|None=None,density:float|None=None,size:float|None=None,size_jitter:float=0,gradient:dict|None=None,
    cell:int=1,texture:float=0,points:list[list[float]]|None=None,rectangle:list[float]|None=None,smooth:bool=False,
    seed:int=1,layer_id:LayerId=None,document_id:DocumentId=None) -> CallToolResult:
    """Pattern fill drawn by the engine inside points (polygon; smooth=true for curves), rectangle, or the selection/whole layer. hatch: lines at angle (degrees, clockwise from +x) or angles=[45,-45] crosshatch, spacing/width px, jitter 0..1 (hand-made breaks, wobble, offsets), taper 0..1 dash ends, dash [on,off]. stipple: random dots, spacing = mean distance, size diameter, size_jitter, density = keep probability. halftone: dot grid at angle with pitch spacing, dot diameter size*sqrt(level) (Ben-Day, print screens). dither: ordered Bayer 8x8 with cell px art pixels (pixel-art gradients). density 0..1 (default hatch 1, others 0.5) and gradient {type: linear|radial, start:[x,y], end:[x,y], from, to} set the tone; texture 0..1 adds document-fixed paper grain. Patterns are anchored to the document, so adjacent fills continue seamlessly."""
    return await execute("paint_pattern",**locals())


@mcp.tool()
async def paint_abort() -> CallToolResult:
    """Signal rendering to stop without acquiring the drawing lock; active atomic operation rolls back."""
    return await execute("paint_abort")


@mcp.resource("paint://guide")
def artist_guide() -> str:
    return GUIDE


def main():
    global _engine
    parser=argparse.ArgumentParser(description="Agent Paint: headless painting MCP")
    parser.add_argument("--state-dir",type=Path)
    parser.add_argument("--transport",choices=["stdio","http"],default="stdio")
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--history-mb",type=float,help="undo history of all documents together (default 512, env AGENT_PAINT_HISTORY_MB)")
    parser.add_argument("--park-minutes",type=float,help="compress documents unused this long, 0 disables (default 15, env AGENT_PAINT_PARK_MINUTES)")
    args=parser.parse_args()
    _engine=Engine(args.state_dir,history_mb=args.history_mb,park_minutes=args.park_minutes)
    if args.transport=="http":
        if args.host not in {"127.0.0.1","localhost","::1"}: parser.error("HTTP binds to loopback only; use a secure tunnel for remote access")
        mcp.settings.host=args.host; mcp.settings.port=args.port
        mcp.run(transport="streamable-http")
    else: mcp.run(transport="stdio")


if __name__=="__main__": main()
