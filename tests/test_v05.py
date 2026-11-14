"""Agent Paint 0.5: optional timelapse recording/rendering and fixes from the 2026-11 style waves (31-60, 84-113)."""
import asyncio
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image
import pytest

from csp_headless.engine import Engine
from csp_headless.render import composite
from csp_headless.timelapse import picture_hash, read_recording, render, video_size
from csp_headless.video import find_ffmpeg


@pytest.fixture
def e(tmp_path):
    result=Engine(tmp_path/"state",park_minutes=0)
    result.call("paint_document",action="create",width=160,height=120,background="#FFFFFF")
    return result


def alpha(image): return np.asarray(image.getchannel("A")).astype(int)


def has_ffmpeg():
    try: find_ffmpeg(); return True
    except ValueError: return False


def paint_scene(e,tmp_path):
    """Strokes, a batch with store/$ref, a plan file, text, a duplicate, selection, preset, undo/redo."""
    e.call("paint_brush",action="configure",settings={"size":9,"texture":.3,"seed":4},color="#204080")
    e.call("paint_strokes",strokes=[{"points":[[10,10,.2],[60,40,1],[120,20,.1]],"smooth":True},
                                    {"points":[[20,100],[140,90]],"color":"#AA3311"}])
    e.call("paint_batch",actions=[
        {"tool":"paint_layer","arguments":{"action":"create","name":"glaze","position":"top"},"store":"glaze"},
        {"tool":"paint_shape","arguments":{"shape":"ellipse","points":[[40,30],[110,95]],"fill":"#FFCC0080",
                                           "layer_id":{"$ref":"glaze.layer_id"}}},
        {"tool":"paint_layer","arguments":{"action":"update","layer_id":{"$ref":"glaze.layer_id"},
                                           "properties":{"blend":"multiply"}}}])
    plan=tmp_path/"plan.jsonl"
    plan.write_text("\n".join(json.dumps(a) for a in [
        {"tool":"paint_strokes","arguments":{"strokes":[{"points":[[5,60+i*3],[150,62+i*3]]} for i in range(5)],
                                             "brush":{"size":2},"color":"#00000080"}},
        {"tool":"paint_pattern","arguments":{"kind":"hatch","points":[[100,10],[155,10],[155,60],[100,60]],"spacing":4}}]),"utf-8")
    e.call("paint_batch",actions_file=str(plan))
    plan.unlink()  # the recording keeps its own copy
    e.call("paint_text",text="Hi",x=8,y=8,style={"font_size":18,"color":"#112233"},layer_name="title")
    copy=e.call("paint_layer",action="duplicate",layer_id="glaze")
    e.call("paint_transform",translate=[6,4],layer_id=copy["layer_id"])
    e.call("paint_selection",action="ellipse",rectangle=[30,20,90,80])
    e.call("paint_fill",color="#33AA55",opacity=.5,layer_id="Layer 1")
    e.call("paint_selection",action="none")
    e.call("paint_brush",action="save_preset",name="mine")
    e.call("paint_brush",action="configure",settings={"size":3})
    e.call("paint_brush",action="load_preset",name="mine")
    e.call("paint_strokes",strokes=[{"points":[[150,110],[100,70]]}])
    e.call("paint_filter",kind="blur",radius=1.5,layer_id="Layer 1")
    e.call("paint_history",action="undo")
    e.call("paint_history",action="redo")


# --- Timelapse -----------------------------------------------------------------------------------

def test_recording_is_off_by_default(e):
    e.call("paint_strokes",strokes=[{"points":[[10,10],[50,50]]}])
    assert e.call("paint_timelapse",action="status")["recording"] is False
    assert not (e.state_dir/"timelapses").exists()
    assert e.call("paint_state")["recording_timelapse"]==[]


def test_record_and_replay_ends_on_exactly_the_same_picture(e,tmp_path):
    e.call("paint_strokes",strokes=[{"points":[[5,5],[30,30]]}])  # before recording: lives in start.apaint
    folder=tmp_path/"rec"
    started=e.call("paint_timelapse",action="start",path=str(folder))
    assert started["recording"] and Path(started["path"])==folder.resolve()
    paint_scene(e,tmp_path)
    status=e.call("paint_timelapse",action="status")
    assert status["recording"] and status["commands"]>=15
    stopped=e.call("paint_timelapse",action="stop")
    assert not stopped["recording"] and stopped["units"]>=stopped["commands"]
    _,manifest,commands=read_recording(folder)
    assert manifest["status"]=="stopped" and manifest["final_sha256"]==picture_hash(composite(e.doc))
    assert any(c["tool"]=="paint_batch" and c["arguments"].get("actions_file","").startswith("assets/") for c in commands)
    assert any(c.get("ids") for c in commands)  # generated layer/object ids are kept for replay
    loaded=[c for c in commands if c["tool"]=="paint_brush" and c["arguments"].get("settings",{}).get("size")==9]
    assert loaded and all(c["arguments"]["action"]=="configure" for c in loaded)  # load_preset recorded as its brush
    frames=tmp_path/"frames"
    result=render(folder,None,fps=10,seconds=3,hold=.5,intro=.2,max_side=320,frames_dir=frames)
    assert result["verified"] is True and not result.get("replay_failures")
    assert result["frames"]==2+30+5 and len(list(frames.glob("frame_*.png")))==result["frames"]
    first,last=(Image.open(frames/f"frame_{i:06d}.png") for i in (1,result["frames"]))
    assert first.size==last.size==(320,240)  # 160x120 art doubled with crisp pixels
    assert np.abs(np.asarray(first,dtype=int)-np.asarray(last,dtype=int)).mean()>3  # the picture was built up


def test_frames_follow_the_work_inside_a_big_batch(e,tmp_path):
    e.call("paint_timelapse",action="start",path=str(tmp_path/"rec"))
    actions=[{"tool":"paint_strokes","arguments":{"strokes":[{"points":[[5+i*7,10],[5+i*7,110]]}]}} for i in range(20)]
    e.call("paint_batch",actions=actions)
    e.call("paint_timelapse",action="stop")
    frames=tmp_path/"frames"
    result=render(tmp_path/"rec",None,fps=10,seconds=2,hold=0,intro=0,frames_dir=frames,max_side=160)
    assert result["units"]==20 and result["frames"]==20 and result["verified"]
    counts=[int((np.asarray(Image.open(frames/f"frame_{i:06d}.png").convert("L"))<128).sum()) for i in (1,10,20)]
    assert counts[0]<counts[1]<counts[2]  # one batch, yet the strokes appear one after another


def test_closing_the_document_stops_the_recording(e,tmp_path):
    e.call("paint_timelapse",action="start",path=str(tmp_path/"rec"))
    e.call("paint_strokes",strokes=[{"points":[[10,10],[100,100]]}])
    closed=e.call("paint_document",action="close",force=True)
    assert closed["timelapse"]["reason"]=="document closed"
    assert read_recording(tmp_path/"rec")[1]["status"]=="stopped"


def test_recording_folder_rules(e,tmp_path):
    folder=tmp_path/"rec"; folder.mkdir(); (folder/"notes.txt").write_text("mine")
    with pytest.raises(ValueError,match="not empty"): e.call("paint_timelapse",action="start",path=str(folder))
    with pytest.raises(ValueError,match="other files"): e.call("paint_timelapse",action="start",path=str(folder),overwrite=True)
    e.call("paint_timelapse",action="start",path=str(tmp_path/"ok"))
    with pytest.raises(ValueError,match="already recorded"): e.call("paint_timelapse",action="start",path=str(tmp_path/"x"))
    e.call("paint_timelapse",action="stop")
    with pytest.raises(ValueError,match="not being recorded"): e.call("paint_timelapse",action="stop")
    e.call("paint_timelapse",action="start",path=str(tmp_path/"ok"),overwrite=True)  # a previous recording may be replaced


def test_failed_calls_are_not_recorded(e,tmp_path):
    e.call("paint_timelapse",action="start",path=str(tmp_path/"rec"))
    with pytest.raises(ValueError): e.call("paint_fill",color="not-a-colour")
    with pytest.raises(ValueError): e.call("paint_batch",actions=[{"tool":"paint_fill","arguments":{"color":"#FF0000"}},
                                                                  {"tool":"paint_fill","arguments":{"color":"nope"}}])
    e.call("paint_fill",color="#FF0000")
    e.call("paint_timelapse",action="stop")
    assert [c["tool"] for c in read_recording(tmp_path/"rec")[2]]==["paint_fill"]


@pytest.mark.skipif(not has_ffmpeg(),reason="ffmpeg is not installed")
def test_render_mp4(e,tmp_path):
    e.call("paint_timelapse",action="start",path=str(tmp_path/"rec"))
    for i in range(12):
        e.call("paint_strokes",strokes=[{"points":[[10+i*12,10],[10+i*12,110,.3]],"color":"#AA2200"}])
    e.call("paint_timelapse",action="stop")
    result=e.call("paint_timelapse",action="render",path=str(tmp_path/"rec"),fps=12,seconds=2,hold=.5)
    output=Path(result["output"])
    assert output==(tmp_path/"rec"/"timelapse.mp4").resolve() and output.stat().st_size>1000
    assert result["verified"] and result["encoder"] and result["size"]==[1280,960]
    with pytest.raises(ValueError,match="exists"): e.call("paint_timelapse",action="render",path=str(tmp_path/"rec"))
    again=e.call("paint_timelapse",action="render",path=str(tmp_path/"rec"),output=str(tmp_path/"short.mp4"),
                 seconds=1,hold=0,max_side=200,overwrite=True)
    assert again["size"]==[160,120] and Path(again["output"]).is_file()


def test_video_size_keeps_whole_pixels_and_even_sides():
    assert video_size(160,120,1280)==((1280,960),True)
    assert video_size(35,21,120)==((70,42),True)  # factor 3 would give odd sides; 2 keeps every pixel square
    assert video_size(36,20,120)==((108,60),True)
    assert video_size(1601,1201,1280)==((1280,960),False)


# --- Selections ----------------------------------------------------------------------------------

def radii(mask,center):
    """Selected extent from the centre along the x axis and along the diagonal."""
    m=np.asarray(mask)>=128; cx,cy=center
    axis=next(r for r in range(200) if not m[cy,cx+r])
    diagonal=next(r for r in range(200) if not m[cy+r,cx+r])*math.sqrt(2)
    return axis,diagonal


def test_shrink_and_grow_keep_circles_round(tmp_path):
    e=Engine(tmp_path/"s",park_minutes=0); e.call("paint_document",action="create",width=300,height=300)
    e.call("paint_selection",action="ellipse",rectangle=[50,50,250,250])
    e.call("paint_selection",action="shrink",radius=40)
    axis,diagonal=radii(e.doc.selection,(150,150))
    assert abs(axis-60)<=2 and abs(diagonal-60)<=3  # a square kernel gave ~60 vs ~43 on the diagonal
    e.call("paint_selection",action="grow",radius=30)
    axis,diagonal=radii(e.doc.selection,(150,150))
    assert abs(axis-90)<=2 and abs(diagonal-90)<=3
    edge=np.asarray(e.doc.selection)[150,238:244]
    assert 0<edge.min()<255 or set(edge)<= {0,255}  # antialiased or exact


def test_shrink_ignores_the_canvas_edge_and_zero_radius_is_identity(e):
    e.call("paint_selection",action="all")
    e.call("paint_selection",action="shrink",radius=10)
    assert np.asarray(e.doc.selection).min()==255
    e.call("paint_selection",action="ellipse",rectangle=[10,10,90,70])
    before=np.asarray(e.doc.selection).copy()
    e.call("paint_selection",action="grow",radius=0)
    assert (np.asarray(e.doc.selection)==before).all()


def test_selection_rectangle_corners_any_order_and_zero_size(e):
    e.call("paint_selection",action="ellipse",rectangle=[90,70,10,10])
    assert e.doc.selection.getbbox()==(10,10,90,70)
    result=e.call("paint_selection",action="rectangle",rectangle=[40,40,40,90])
    assert result["bbox"] is None and "nothing" in result["note"]
    e.call("paint_fill",color="#FF0000")
    assert alpha(e.doc.layer().image).sum()==0  # an empty selection protects everything


def test_from_layer_threshold_gives_a_hard_selection(e):
    e.call("paint_layer",action="create",name="glaze")
    e.call("paint_fill",color="#0000FF",opacity=.3,layer_id="glaze")
    soft=e.call("paint_selection",action="from_layer",layer_id="glaze")
    assert set(np.unique(np.asarray(e.doc.selection)))=={round(.3*255)}
    e.call("paint_selection",action="from_layer",layer_id="glaze",threshold=40)
    assert set(np.unique(np.asarray(e.doc.selection)))=={255}
    e.call("paint_selection",action="from_layer",layer_id="glaze",threshold=200)
    assert set(np.unique(np.asarray(e.doc.selection)))=={0}


# --- Strokes, shapes, batches --------------------------------------------------------------------

def test_call_level_dash_applies_to_every_stroke(e):
    e.call("paint_layer",action="create",name="a"); e.call("paint_layer",action="create",name="b")
    line=[[10,20],[150,20]]
    e.call("paint_strokes",strokes=[{"points":line},{"points":[[10,60],[150,60]]}],dash=[0,12],brush={"size":4},layer_id="a")
    e.call("paint_strokes",strokes=[{"points":line,"dash":[0,12]},{"points":[[10,60],[150,60]],"dash":[0,12]}],
           brush={"size":4},layer_id="b")
    assert (alpha(e.doc.layer("a").image)==alpha(e.doc.layer("b").image)).all()
    row=alpha(e.doc.layer("a").image)[20]
    assert (row>0).sum()<len(row)//2  # dots, not a solid line


def test_empty_stroke_list_and_degenerate_shapes_are_skipped(e):
    assert e.call("paint_strokes",strokes=[])["painted"]==0
    summary=e.call("paint_batch",actions=[
        {"tool":"paint_shape","arguments":{"shape":"polygon","points":[[10,10],[20,20]],"fill":"#FF0000"}},
        {"tool":"paint_shape","arguments":{"shape":"ellipse","points":[[30,30],[30,60]],"fill":"#FF0000"}},
        {"tool":"paint_shape","arguments":{"shape":"rectangle","points":[[90,80],[40,20]],"fill":"#00FF00"}}])
    assert summary["skipped_degenerate"]==2
    e.call("paint_layer",action="create",name="ordered")
    e.call("paint_shape",shape="rectangle",points=[[40,20],[90,80]],fill="#00FF00",layer_id="ordered")
    assert (alpha(e.doc.layer("Layer 1").image)==alpha(e.doc.layer("ordered").image)).all()  # reversed corners normalized


def test_rounding_overshoot_is_clamped_but_real_errors_stay(e):
    e.call("paint_strokes",strokes=[{"points":[[10,10,1.007],[50,50,-.004]]}],brush={"opacity":1.002})
    e.call("paint_fill",color="#FF0000",opacity=1.004,layer_id="Layer 1")
    with pytest.raises(ValueError,match="pressure"): e.call("paint_strokes",strokes=[{"points":[[10,10,1.2]]}])
    with pytest.raises(ValueError,match="opacity"): e.call("paint_fill",color="#FF0000",opacity=1.5)


def test_store_inside_arguments_is_accepted(e):
    summary=e.call("paint_batch",actions=[
        {"tool":"paint_layer","arguments":{"action":"create","name":"ink","store":"ink"}},
        {"tool":"paint_fill","arguments":{"color":"#000000","layer_id":{"$ref":"ink.layer_id"}}}])
    assert summary["stored"]["ink"]["name"]=="ink"
    with pytest.raises(ValueError,match="twice"):
        e.call("paint_batch",actions=[{"tool":"paint_layer","arguments":{"action":"create","store":"a"},"store":"b"}])


def test_big_compact_batches_summarize_and_keep_warnings(e):
    actions=[{"tool":"paint_strokes","arguments":{"strokes":[{"points":[[i,5],[i,15]]}],"layer_id":"Layer 1"}} for i in range(70)]
    actions.append({"tool":"paint_text","arguments":{"text":"\U0001F600x","x":5,"y":40,"layer_name":"emoji",
                                                   "style":{"font":"arial"}}})
    summary=e.call("paint_batch",actions=actions)
    assert "results" not in summary and summary["results_omitted"]==71
    assert any("missing" in w.get("warning","") or "glyph" in w.get("warning","") for w in summary["warnings"])
    small=e.call("paint_batch",actions=actions[:3])
    assert len(small["results"])==3


# --- Layers and text -----------------------------------------------------------------------------

def test_layer_names_stay_unique(e):
    e.call("paint_layer",action="create",name="ink")
    with pytest.raises(ValueError,match="already exists"): e.call("paint_layer",action="create",name="ink")
    first=e.call("paint_layer",action="duplicate",layer_id="ink")
    second=e.call("paint_layer",action="duplicate",layer_id="ink")
    assert (first["name"],second["name"])==("ink copy","ink copy 2")
    named=e.call("paint_layer",action="duplicate",layer_id="ink",name="ink shadow")
    assert named["name"]=="ink shadow"
    with pytest.raises(ValueError,match="already exists"):
        e.call("paint_layer",action="update",layer_id="ink shadow",properties={"name":"ink"})
    e.call("paint_layer",action="update",layer_id="ink",properties={"name":"ink"})  # renaming to itself is fine
    stamps=[e.call("paint_layer",action="stamp_visible")["name"] for _ in range(2)]
    assert stamps==["Visible stamp","Visible stamp 2"]


def test_text_layers_get_names_and_raster_targets_explain_the_fix(e):
    made=e.call("paint_text",text="Title",x=5,y=5,layer_name="caption")
    assert e.doc.layer(made["layer_id"]).name=="caption"
    again=e.call("paint_text",text="Title",x=5,y=40)
    assert e.doc.layer(again["layer_id"]).name=="Title"
    third=e.call("paint_text",text="Title",x=5,y=70)
    assert e.doc.layer(third["layer_id"]).name=="Title 2"
    with pytest.raises(ValueError,match="kind='text'"): e.call("paint_text",text="x",x=1,y=1,layer_id="Layer 1")


def test_merge_down_accepts_blend_and_clipping(e):
    e.call("paint_fill",color="#808080",layer_id="Layer 1")
    e.call("paint_layer",action="create",name="shade",properties={"blend":"multiply","clipping":True})
    e.call("paint_shape",shape="rectangle",points=[[0,0],[80,120]],fill="#FF8000",layer_id="shade")
    expected=np.asarray(composite(e.doc)).astype(int)
    result=e.call("paint_layer",action="merge_down",layer_id="shade")
    assert "lower layer only" in result["note"] and len(e.doc.layers)==1
    assert np.abs(np.asarray(composite(e.doc)).astype(int)-expected).max()<=1


# --- Fills, filters, snapshots -------------------------------------------------------------------

def test_fill_gradient_inside_a_polygon(e):
    e.call("paint_layer",action="create",name="g")
    result=e.call("paint_fill",kind="linear",color="#FF0000",color2="#0000FF",start=[20,0],end=[140,0],
                  points=[[20,20],[140,20],[80,110]],layer_id="g")
    assert result["area"]=="polygon"
    image=e.doc.layer("g").image
    assert image.getpixel((80,40))[3]==255 and image.getpixel((10,10))[3]==0 and image.getpixel((150,100))[3]==0
    assert image.getpixel((30,22))[0]>image.getpixel((130,22))[0]


def test_new_colour_filters(e):
    e.call("paint_fill",kind="linear",color="#000000",color2="#FFFFFF",start=[0,0],end=[159,0],layer_id="Layer 1")
    def row(): return np.asarray(e.doc.layer().image)[60,:,0].astype(int)
    e.call("paint_filter",kind="levels",black=64,white=192,layer_id="Layer 1")
    stretched=row(); assert stretched[:39].max()==0 and stretched[-35:].min()==255 and 100<stretched[80]<160
    e.call("paint_filter",kind="invert",layer_id="Layer 1")
    assert row()[0]==255 and row()[-1]==0
    e.call("paint_filter",kind="threshold",level=128,layer_id="Layer 1")
    assert set(np.unique(row()))=={0,255}
    e.call("paint_fill",color="#FF0000",layer_id="Layer 1")
    e.call("paint_filter",kind="hue_saturation",hue=120,layer_id="Layer 1")
    r,g,b,a=e.doc.layer().image.getpixel((5,5)); assert g>200 and r<40 and a==255
    e.call("paint_filter",kind="hue_saturation",saturation=0,lightness=.5,layer_id="Layer 1")
    r,g,b,_=e.doc.layer().image.getpixel((5,5)); assert abs(r-g)<=2 and abs(g-b)<=2 and r>150


def test_relief_depth_strengthens_gentle_height_maps(e):
    e.call("paint_fill",kind="radial",color="#C0C0C0",color2="#404040",start=[80,60],end=[80,0],layer_id="Layer 1")
    original=np.asarray(e.doc.layer("Layer 1").image)[..., 0].astype(int)
    e.call("paint_layer",action="duplicate",layer_id="Layer 1",name="deep")
    e.call("paint_filter",kind="relief",amount=1,radius=1,layer_id="Layer 1")
    e.call("paint_filter",kind="relief",amount=1,radius=1,depth=10,layer_id="deep")
    def change(name): return np.abs(np.asarray(e.doc.layer(name).image)[..., 0].astype(int)-original).mean()
    assert change("Layer 1")<4 and change("deep")>5*change("Layer 1")


def test_snapshot_explains_when_upscale_cannot_enlarge(e):
    info=e.call("paint_snapshot",max_side=200,upscale=True).info
    assert info["upscale_factor"]==1 and "max_side" in info["note"]


def test_mcp_exposes_0_5(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def scenario():
        params=StdioServerParameters(command=sys.executable,args=["-m","csp_headless.server","--state-dir",str(tmp_path/"st")])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                tools={t.name:t for t in (await session.list_tools()).tools}
                assert "paint_timelapse" in tools
                assert "dash" in tools["paint_strokes"].inputSchema["properties"]
                assert "threshold" in tools["paint_selection"].inputSchema["properties"]
                assert "points" in tools["paint_fill"].inputSchema["properties"]
                created=await session.call_tool("paint_document",{"action":"create","width":64,"height":48})
                doc=created.structuredContent["document_id"]
                start=await session.call_tool("paint_timelapse",{"action":"start","path":str(tmp_path/"rec"),"document_id":doc})
                assert not start.isError and start.structuredContent["recording"]
                drawn=await session.call_tool("paint_strokes",{"strokes":[],"dash":[0,6],"document_id":doc})
                assert not drawn.isError
                await session.call_tool("paint_strokes",{"strokes":[{"points":[[5,5],[60,40]]}],"document_id":doc})
                stop=await session.call_tool("paint_timelapse",{"action":"stop","document_id":doc})
                assert not stop.isError and stop.structuredContent["commands"]==2
                if has_ffmpeg():
                    made=await session.call_tool("paint_timelapse",{"action":"render","path":str(tmp_path/"rec"),
                                                                    "seconds":1,"hold":0,"max_side":256})
                    assert not made.isError and made.structuredContent["verified"]
    asyncio.run(scenario())
