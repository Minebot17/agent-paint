"""Regression tests for gaps reported by painting agents (see docs/painting-experience.md, 2026-11-06)."""
import asyncio
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image
import pytest

from csp_headless.engine import Engine
from csp_headless.render import composite


@pytest.fixture
def e(tmp_path):
    result=Engine(tmp_path/"state")
    result.call("paint_document",action="create",width=128,height=128,background=None)
    return result


def alpha(e): return np.asarray(e.doc.layer().image.getchannel("A"))


# --- Shapes: local rendering, hard pixels -------------------------------------------------------

def test_shape_cost_does_not_scale_with_canvas(tmp_path):
    e=Engine(tmp_path/"state")
    e.call("paint_document",action="create",width=2400,height=1800,background=None)
    started=time.perf_counter()
    for i in range(40):
        e.call("paint_shape",shape="polygon",points=[[10+i,10],[60+i,12],[30+i,50]],fill="#336699")
    per_shape=(time.perf_counter()-started)/40
    # Full-canvas supersampling took ~0.3 s per shape at this size; a local patch is milliseconds.
    assert per_shape<.05, per_shape


def test_shape_patch_matches_bounds_and_reports_them(e):
    result=e.call("paint_shape",shape="ellipse",points=[[40,40],[60,70]],fill="#FF0000")
    l,t,r,b=result["bounds"]
    box=e.doc.layer().image.getbbox()
    assert l<=box[0] and t<=box[1] and box[2]<=r and box[3]<=b
    a=alpha(e)
    assert a[55,50]==255 and ((a>0)&(a<255)).sum()>20  # solid interior, antialiased rim


def test_hard_rectangle_covers_half_open_pixel_range(e):
    e.call("paint_shape",shape="rectangle",points=[[10,10],[20,20]],fill="#00FF00",antialias=False)
    a=alpha(e)
    assert set(np.unique(a))=={0,255}
    assert a[10:20,10:20].min()==255 and a.sum()==100*255


def test_hard_polygon_has_no_partial_pixels(e):
    e.call("paint_shape",shape="polygon",points=[[5,5],[90,17],[33,80]],fill="#0000FF",antialias=False)
    assert set(np.unique(alpha(e)))=={0,255}


def test_antialiased_integer_rectangle_has_no_ringing(e):
    e.call("paint_shape",shape="rectangle",points=[[10,10],[30,30]],fill="#000000")
    a=alpha(e)
    assert a[9,15]==0 and a[15,8]==0  # no halo outside (Lanczos used to ring here)


def test_offcanvas_shape_is_noop(e):
    result=e.call("paint_shape",shape="rectangle",points=[[500,500],[600,600]],fill="#000000")
    assert result["bounds"] is None and e.doc.layer().image.getbbox() is None


def test_vector_hard_shape_stays_hard_after_rerender(e):
    e.call("paint_layer",action="create",kind="vector")
    obj=e.call("paint_shape",shape="ellipse",points=[[10,10],[50,40]],fill="#FF0000",antialias=False)["object_id"]
    e.call("paint_object",action="update",object_id=obj,changes={"fill":"#00FF00"})
    assert set(np.unique(alpha(e)))=={0,255}


# --- Selections ----------------------------------------------------------------------------------

def test_integer_rectangle_selection_is_exact(e):
    e.call("paint_selection",action="rectangle",rectangle=[10,10,11,11])
    mask=np.asarray(e.doc.selection)
    assert mask[10,10]==255 and mask.sum()==255  # was ~195 inside with ~18 bleeding onto neighbours


@pytest.mark.parametrize("action,arguments",[("polygon",{"points":[[3,3],[90,20],[40,100]]}),
                                             ("ellipse",{"rectangle":[7,9,83,61]})])
def test_hard_selection_is_binary(e,action,arguments):
    e.call("paint_selection",action=action,antialias=False,**arguments)
    assert set(np.unique(np.asarray(e.doc.selection)))=={0,255}


def test_antialiased_ellipse_selection_keeps_soft_edge(e):
    e.call("paint_selection",action="ellipse",rectangle=[7,9,83,61])
    values=set(np.unique(np.asarray(e.doc.selection)))
    assert len(values)>2


def test_selection_may_extend_past_canvas(e):
    e.call("paint_selection",action="ellipse",rectangle=[-50,-50,60,60])
    assert e.doc.selection.getbbox()[:2]==(0,0)
    e.call("paint_selection",action="rectangle",rectangle=[200,200,300,300])
    assert e.doc.selection.getbbox() is None  # entirely outside: empty, nothing writable


# --- Fill clear ---------------------------------------------------------------------------------

def test_fill_clear_erases_selection_only(e):
    e.call("paint_fill",color="#FF0000")
    e.call("paint_selection",action="rectangle",rectangle=[0,0,64,128])
    e.call("paint_fill",kind="clear")
    a=alpha(e)
    assert a[:,:64].max()==0 and a[:,64:].min()==255


def test_fill_clear_partial_and_alpha_lock(e):
    e.call("paint_fill",color="#FF0000")
    e.call("paint_fill",kind="clear",opacity=.5)
    assert abs(int(alpha(e)[5,5])-128)<=1
    e.call("paint_layer",action="update",properties={"alpha_lock":True})
    before=e.doc.layer().image.tobytes()
    # 0.3: clearing an alpha-locked layer fails loudly instead of silently doing nothing.
    with pytest.raises(ValueError,match="alpha_lock"): e.call("paint_fill",kind="clear")
    assert e.doc.layer().image.tobytes()==before


# --- Layers -------------------------------------------------------------------------------------

def test_layer_create_honours_properties(e):
    layer=e.call("paint_layer",action="create",name="glow",properties={"opacity":.4,"blend":"screen","clipping":True})
    assert layer["opacity"]==.4 and layer["blend"]=="screen" and layer["clipping"] is True


def test_layer_create_rejects_bad_properties_without_side_effects(e):
    count=len(e.doc.layers)
    with pytest.raises(ValueError): e.call("paint_layer",action="create",properties={"opacity":2})
    with pytest.raises(ValueError): e.call("paint_layer",action="create",properties={"glow":True})
    assert len(e.doc.layers)==count


# --- Text ---------------------------------------------------------------------------------------

def test_text_anchor_centres_and_reports_bbox(e):
    result=e.call("paint_text",text="Hi",x=64,y=64,style={"font_size":30,"anchor":"mm"})
    l,t,r,b=result["bbox"]
    assert abs((l+r)/2-64)<=3 and abs((t+b)/2-64)<=4
    assert list(e.doc.layer().image.getbbox())==result["bbox"]


def test_text_partly_left_of_canvas_does_not_crash(e):
    result=e.call("paint_text",text="Wide title",x=0,y=20,style={"font_size":30,"anchor":"mt"})
    assert result["bbox"][0]<0 and e.doc.layer().image.getbbox() is not None


def test_text_rejects_unknown_anchor(e):
    with pytest.raises(ValueError): e.call("paint_text",text="x",x=1,y=1,style={"anchor":"center"})


def test_missing_glyphs_are_reported(e):
    result=e.call("paint_text",text="Привет 世界",x=2,y=2,style={"font_size":20})  # bundled DejaVu: Cyrillic yes, CJK no
    assert result["missing_glyphs"]==sorted("世界") and "warning" in result
    clean=e.call("paint_text",text="Привет",x=2,y=40,style={"font_size":20})
    assert "missing_glyphs" not in clean


def test_fonts_can_be_filtered_by_coverage(e):
    bundled=[f for f in e.call("paint_fonts",query="DejaVuSans")["fonts"]]
    assert bundled
    assert e.call("paint_fonts",query="DejaVuSans",supports="Привет")["fonts"]
    assert not e.call("paint_fonts",query="DejaVuSans",supports="世界")["fonts"]


# --- Snapshots and export -----------------------------------------------------------------------

def test_snapshot_upscale_is_crisp_nearest_neighbour(e):
    e.call("paint_shape",shape="rectangle",points=[[4,4],[8,8]],fill="#FF0000",antialias=False)
    shot=e.call("paint_snapshot",rectangle=[0,0,16,16],max_side=128,upscale=True)
    assert shot.info["upscale_factor"]==8 and shot.image.size==(128,128)
    pixels=np.asarray(shot.image)
    assert set(np.unique(pixels[...,3]))=={0,255}
    assert shot.info["document_pixels_per_image_pixel"]==[1/8,1/8]


def test_snapshot_path_overwrite(e,tmp_path):
    target=tmp_path/"preview.png"
    e.call("paint_snapshot",path=str(target))
    with pytest.raises(FileExistsError): e.call("paint_snapshot",path=str(target))
    e.call("paint_snapshot",path=str(target),overwrite=True)


def test_export_integer_scale_nearest(e,tmp_path):
    e.call("paint_shape",shape="rectangle",points=[[0,0],[1,1]],fill="#FF0000",antialias=False)
    out=tmp_path/"x6.png"
    result=e.call("paint_document",action="export",path=str(out),scale=6,transparent=True)
    assert result["size"]==[768,768]
    with Image.open(out) as image:
        block=np.asarray(image.convert("RGBA"))[:6,:6]
        assert (block==[255,0,0,255]).all() and np.asarray(image.convert("RGBA"))[6,6,3]==0
    with pytest.raises(ValueError): e.call("paint_document",action="export",path=str(tmp_path/"x.ora"),scale=2)


def test_control_character_in_path_is_explained(e,tmp_path):
    with pytest.raises(ValueError,match="control character U\\+000A"):
        e.call("paint_document",action="export",path=str(tmp_path)+"\new.png")


# --- Filters ------------------------------------------------------------------------------------

def test_blur_is_premultiplied(e):
    e.call("paint_shape",shape="rectangle",points=[[50,50],[70,70]],fill="#FF0000",antialias=False)
    e.call("paint_filter",kind="blur",radius=6)
    pixel=e.doc.layer().image.getpixel((46,60))
    assert 0<pixel[3]<255 and pixel[:3]==(255,0,0)  # soft edge keeps its colour, no dark fringe


def test_channel_shift_moves_channels(e):
    e.call("paint_shape",shape="rectangle",points=[[40,40],[60,60]],fill="#FFFFFF",antialias=False)
    e.call("paint_filter",kind="channel_shift",offsets={"r":[5,0],"b":[-5,0]})
    image=e.doc.layer().image
    assert image.getpixel((62,50))[:4]==(255,0,0,255)   # only red extends right
    assert image.getpixel((37,50))[:4]==(0,0,255,255)   # only blue extends left
    assert image.getpixel((50,50))==(255,255,255,255)
    with pytest.raises(ValueError): e.call("paint_filter",kind="channel_shift",offsets={"r":[1.5,0]})


def test_noise_is_seeded_and_keeps_coverage(e):
    e.call("paint_fill",color="#808080")
    e.call("paint_filter",kind="noise",amount=.3,seed=7)
    first=e.doc.layer().image.tobytes()
    assert set(np.unique(alpha(e)))=={255}
    e.call("paint_history",action="undo")
    e.call("paint_filter",kind="noise",amount=.3,seed=7)
    assert e.doc.layer().image.tobytes()==first


def test_filter_respects_selection_lock_and_kind(e):
    e.call("paint_fill",color="#000000")
    e.call("paint_selection",action="rectangle",rectangle=[0,0,64,128])
    e.call("paint_filter",kind="noise",amount=1,seed=3,monochrome=False)
    rgb=np.asarray(e.doc.layer().image)[...,:3]
    assert rgb[:,64:].max()==0 and rgb[:,:64].max()>0
    e.call("paint_layer",action="update",properties={"locked":True})
    with pytest.raises(ValueError): e.call("paint_filter",kind="blur",radius=2)
    e.call("paint_layer",action="create",kind="vector")
    with pytest.raises(ValueError): e.call("paint_filter",kind="blur",radius=2)


def test_filter_failure_inside_batch_rolls_back(e):
    before=composite(e.doc).tobytes()
    with pytest.raises(ValueError):
        e.call("paint_batch",actions=[{"tool":"paint_fill","arguments":{"color":"#00FF00"}},
                                      {"tool":"paint_filter","arguments":{"kind":"blur","radius":-1}}])
    assert composite(e.doc).tobytes()==before


# --- Brush grain and pigment pooling ------------------------------------------------------------

def _overlap_pixel(e,space):
    values=[]
    for start in (10,40):
        e.call("paint_layer",action="create")
        e.call("paint_strokes",strokes=[{"points":[[start,64],[100,64]],"color":"#000000",
            "brush":{"size":30,"texture":1,"texture_space":space,"pressure_size":False,"seed":5}}])
        values.append(np.asarray(e.doc.layer().image.getchannel("A"))[60:68,70:90].copy())
    return values


def test_paper_texture_is_anchored_to_document(e):
    paper=_overlap_pixel(e,"paper")
    assert np.array_equal(paper[0],paper[1])  # two different strokes share one paper tooth
    stroke=_overlap_pixel(e,"stroke")
    assert not np.array_equal(stroke[0],stroke[1])


def test_edge_darkening_pools_pigment_at_rim(e):
    e.call("paint_strokes",strokes=[{"points":[[64,64]],"color":"#000000",
        "brush":{"size":80,"hardness":.9,"edge_darkening":1,"pressure_size":False}}])
    row=alpha(e)[64]
    assert row[30:40].max()>row[64]+20


def test_new_brush_settings_are_validated(e):
    with pytest.raises(ValueError): e.call("paint_brush",action="configure",settings={"texture_space":"canvas"})
    with pytest.raises(ValueError): e.call("paint_brush",action="configure",settings={"edge_darkening":2})
    assert "dry_media" in e.call("paint_brush",action="list")["presets"]


# --- Several agents on one server ---------------------------------------------------------------

def test_document_id_targets_without_switching_active(e):
    first=e.doc.id
    e.call("paint_document",action="create",width=32,height=32,background=None)
    second=e.doc.id
    e.call("paint_fill",color="#FF0000",document_id=first)
    assert e.active_document==second
    assert e.documents[second].layer().image.getbbox() is None
    assert e.documents[first].layer().image.getpixel((5,5))==(255,0,0,255)
    e.call("paint_history",action="undo",document_id=first)
    assert e.documents[first].layer().image.getbbox() is None and e.active_document==second
    info=e.call("paint_document",action="info",document_id=first)
    assert info["document_id"]==first and e.active_document==second


def test_document_id_errors(e):
    with pytest.raises(ValueError,match="Unknown document_id"): e.call("paint_fill",color="#000",document_id="nope")
    other=e.doc.id
    e.call("paint_document",action="create",width=32,height=32)
    with pytest.raises(ValueError):
        e.call("paint_batch",actions=[{"tool":"paint_fill","arguments":{"color":"#000","document_id":other}}])


def test_mcp_exposes_new_parameters(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def scenario():
        params=StdioServerParameters(command=sys.executable,args=["-m","csp_headless.server","--state-dir",str(tmp_path/"mcp")])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                first=(await session.call_tool("paint_document",{"action":"create","width":48,"height":48})).structuredContent["document_id"]
                await session.call_tool("paint_document",{"action":"create","width":16,"height":16})
                hard=await session.call_tool("paint_selection",{"action":"ellipse","rectangle":[-10,-10,30,30],"antialias":False,"document_id":first})
                assert not hard.isError, hard.content
                filled=await session.call_tool("paint_fill",{"color":"#FF0000","document_id":first})
                assert not filled.isError
                blurred=await session.call_tool("paint_filter",{"kind":"blur","radius":2,"document_id":first})
                assert not blurred.isError, blurred.content
                text=await session.call_tool("paint_text",{"text":"A","x":24,"y":24,"style":{"anchor":"mm"},"document_id":first})
                assert text.structuredContent["bbox"]
                shot=await session.call_tool("paint_snapshot",{"document_id":first,"rectangle":[0,0,16,16],"max_side":64,"upscale":True})
                assert shot.structuredContent["upscale_factor"]==4
    asyncio.run(scenario())
