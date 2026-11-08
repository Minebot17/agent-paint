"""Regression tests for Agent Paint 0.3: gaps reported by 30 style-study agents (docs/painting-experience.md, 2026-11-08)."""
import asyncio
import json
import math
from pathlib import Path
import sys

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


def alpha(image): return np.asarray(image.getchannel("A")).astype(int)


def layer_alpha(e,name=None): return alpha(e.doc.layer(name).image)


# --- Per-document brush: agents sharing a server cannot change each other's tool state ----------

def test_brush_belongs_to_each_document(e):
    first=e.doc.id
    second=e.call("paint_document",action="create",width=64,height=64,background=None)["document_id"]
    e.call("paint_brush",action="configure",settings={"size":40,"texture":.8},color="#FF0000",document_id=first)
    assert e.call("paint_brush",action="get",document_id=second)["settings"]["size"]==12
    assert e.call("paint_brush",action="get",document_id=first)["settings"]["size"]==40
    # A stroke without explicit brush uses its own document's brush, not the last configured one.
    e.call("paint_strokes",strokes=[{"points":[[32,32]]}],document_id=second)
    drawn=e.documents[second].layer().image
    assert drawn.getpixel((32,32))[:3]==(0,0,0) and alpha(drawn).astype(bool).sum()<200


def test_new_document_starts_from_active_brush_and_default_without_documents(tmp_path):
    e=Engine(tmp_path/"state")
    e.call("paint_brush",action="configure",settings={"size":21},color="#123456")  # no document yet: default
    e.call("paint_document",action="create",width=32,height=32)
    assert e.brush["size"]==21 and e.color=="#123456"
    e.call("paint_brush",action="configure",settings={"size":33})
    e.call("paint_document",action="create",width=32,height=32)
    assert e.brush["size"]==33


def test_undo_does_not_revert_the_current_brush(e):
    e.call("paint_strokes",strokes=[{"points":[[20,20],[80,80]]}])
    e.call("paint_brush",action="configure",settings={"size":44},color="#00FF00")
    e.call("paint_history",action="undo")
    assert e.brush["size"]==44 and e.color=="#00FF00"
    e.call("paint_history",action="redo")
    assert e.brush["size"]==44


def test_brush_is_saved_in_project(e,tmp_path):
    e.call("paint_brush",action="configure",settings={"size":17,"kind":"pencil"},color="#ABCDEF")
    path=tmp_path/"b.apaint"
    e.call("paint_document",action="save",path=str(path))
    other=Engine(tmp_path/"other")
    other.call("paint_document",action="open",path=str(path))
    assert other.brush["size"]==17 and other.brush["kind"]=="pencil" and other.color=="#ABCDEF"


# --- Layer targeting, placement and compact listings --------------------------------------------

def test_layer_id_and_names_target_without_changing_active(e):
    base=e.doc.active_layer
    e.call("paint_layer",action="create",name="ink")
    e.call("paint_layer",action="select",layer_id=base)
    e.call("paint_strokes",strokes=[{"points":[[10,10],[60,60]]}],layer_id="ink")
    e.call("paint_shape",shape="rectangle",points=[[70,70],[90,90]],fill="#FF0000",layer_id="ink")
    e.call("paint_fill",color="#00FF00",layer_id="ink",kind="flood",point=[120,5])
    e.call("paint_pattern",kind="hatch",rectangle=[0,100,40,128],layer_id="ink")
    assert e.doc.active_layer==base and e.doc.layer(base).image.getbbox() is None
    assert e.doc.layer("ink").image.getpixel((80,80))==(255,0,0,255)
    e.call("paint_selection",action="from_layer",layer_id="ink")
    assert e.doc.selection.getpixel((80,80))==255
    assert e.call("paint_sample",points=[[80,80]],layer_id="ink")["samples"][0]["rgba"]==(255,0,0,255)


def test_layer_position_and_index_reporting(e):
    bottom=e.doc.active_layer
    a=e.call("paint_layer",action="create",name="a")
    assert a["index"]==1 and a["layer_id"]==a["id"] and a["layer_count"]==2
    e.call("paint_layer",action="select",layer_id=bottom)
    b=e.call("paint_layer",action="create",name="b")
    assert b["index"]==1  # default: above the active layer
    top=e.call("paint_layer",action="create",name="top",position="top")
    assert top["index"]==3 and [l.name for l in e.doc.layers][-1]=="top"
    low=e.call("paint_layer",action="create",name="low",position="bottom")
    assert low["index"]==0
    moved=e.call("paint_layer",action="reorder",layer_id="low",position="top")
    assert moved["index"]==len(e.doc.layers)-1
    with pytest.raises(ValueError,match="bottom-to-top"): e.call("paint_layer",action="reorder",layer_id="a",index=99)
    with pytest.raises(ValueError,match="index or position"): e.call("paint_layer",action="create",index=0,position="top")


def test_layer_list_reports_object_counts_not_objects(e):
    e.call("paint_layer",action="create",kind="vector",name="shapes")
    for i in range(5): e.call("paint_shape",shape="ellipse",points=[[i*10,10],[i*10+8,18]],fill="#000000")
    listed=e.call("paint_layer",action="list")["layers"]
    entry=[l for l in listed if l["name"]=="shapes"][0]
    assert entry["object_count"]==5 and "objects" not in entry and entry["index"]==1
    assert "objects" not in e.call("paint_state")["document"]["layers"][1]
    assert len(e.call("paint_object",action="list",layer_id="shapes")["objects"])==5


def test_stamp_visible_copies_the_composition(e):
    e.call("paint_fill",color="#336699",opacity=.5)
    e.call("paint_layer",action="create")
    e.call("paint_strokes",strokes=[{"points":[[10,64],[118,64]],"color":"#FF8800","brush":{"size":20}}])
    e.call("paint_layer",action="update",properties={"blend":"multiply"})
    expected=composite(e.doc,transparent=True).tobytes()
    stamp=e.call("paint_layer",action="stamp_visible",include_background=False)
    assert stamp["index"]==len(e.doc.layers)-1 and e.doc.layer().image.tobytes()==expected


# --- Batches: files, references, compact results ------------------------------------------------

def test_batch_from_jsonl_with_refs_and_more_than_256_actions(e,tmp_path):
    plan=tmp_path/"plan.jsonl"
    lines=[{"tool":"paint_layer","arguments":{"action":"create","name":"dots"},"store":"dots"}]
    lines+=[{"tool":"paint_strokes","arguments":{"strokes":[{"points":[[2+(i%60)*2,2+(i//60)*20]]}],"layer_id":{"$ref":"dots.layer_id"},
             "brush":{"size":1.5,"pressure_size":False}}} for i in range(300)]
    plan.write_text("\n".join(json.dumps(l) for l in lines),"utf-8")
    result=e.call("paint_batch",actions_file=str(plan),results="none")
    assert result["completed"]==301 and "results" not in result and result["tools"]["paint_strokes"]==300
    assert result["stored"]["dots"]["layer_id"]==e.doc.layer("dots").id and result["bbox"] is not None
    assert len(e.doc.undo_stack)==1
    with pytest.raises(ValueError,match="1..256"):
        e.call("paint_batch",actions=lines[1:])


def test_batch_file_json_rollback_and_unknown_ref(e,tmp_path):
    plan=tmp_path/"plan.json"
    plan.write_text(json.dumps({"actions":[{"tool":"paint_fill","arguments":{"color":"#FF0000"}},
                                           {"tool":"paint_fill","arguments":{"color":{"$ref":"missing.color"}}}]}),"utf-8")
    with pytest.raises(ValueError,match="Unknown \\$ref"): e.call("paint_batch",actions_file=str(plan))
    assert e.doc.layer().image.getbbox() is None
    with pytest.raises(ValueError,match="either"): e.call("paint_batch")


def test_batch_compact_results_are_small(e):
    e.call("paint_layer",action="create",kind="vector",name="v")
    for i in range(30): e.call("paint_shape",shape="rectangle",points=[[i,i],[i+5,i+5]],fill="#000000")
    actions=[{"tool":"paint_layer","arguments":{"action":"info","layer_id":"v"}},
             {"tool":"paint_strokes","arguments":{"strokes":[{"points":[[5,90+i]]} for i in range(100)],"layer_id":e.doc.layers[0].id}}]
    compact=e.call("paint_batch",actions=actions)
    full=e.call("paint_batch",actions=actions,results="full")
    assert compact["results"][0]["object_count"]==30 and "objects" not in compact["results"][0]
    assert "bounds" not in compact["results"][1] and compact["results"][1]["bbox"]
    assert len(json.dumps(compact))<len(json.dumps(full))/3


# --- Stroke geometry: smoothing, dashes, stabilizer ----------------------------------------------

def test_smooth_stroke_follows_a_curve_through_its_points(e):
    square=[[64,24],[104,64],[64,104],[24,64]]  # four points on a circle of radius 40
    e.call("paint_strokes",strokes=[{"points":square,"closed":True,"smooth":True,"brush":{"size":3,"pressure_size":False}}])
    a=layer_alpha(e)
    # The spline midpoint between (104,64) and (64,104) lies at about (89,89): outside the chord.
    assert a[87:91,87:91].max()>0
    assert a[84,84]==0  # a polyline would pass through the chord midpoint
    for x,y in square: assert a[y,x]>0  # Catmull-Rom passes through every authored point


def test_polyline_without_smooth_is_unchanged(e):
    e.call("paint_strokes",strokes=[{"points":[[64,24],[104,64],[64,104]],"brush":{"size":3,"pressure_size":False}}])
    assert layer_alpha(e)[44,84]>0  # chord midpoint between the first two points


def test_dashes_and_dots(e):
    e.call("paint_strokes",strokes=[{"points":[[4,20],[124,20]],"dash":[10,10],"brush":{"size":4,"pressure_size":False}}])
    row=layer_alpha(e)[20,4:124]>0
    runs=np.diff(np.r_[0,row.astype(int),0])
    assert (runs==1).sum()==6  # 120 px with a 20 px period
    e.call("paint_layer",action="clear")
    e.call("paint_strokes",strokes=[{"points":[[4,60],[124,60]],"dash":[0,20],"brush":{"size":6,"pressure_size":False}}])
    row=layer_alpha(e)[60]>0
    assert (np.diff(np.r_[0,row.astype(int),0])==1).sum()==7  # dots at 4, 24, ... 124
    with pytest.raises(ValueError,match="gap"):
        e.call("paint_strokes",strokes=[{"points":[[0,0],[9,9]],"dash":[5,0]}])


def test_stabilizer_keeps_a_sparse_zigzag(e):
    zigzag=[[10,100],[30,30],[50,100],[70,30],[90,100],[110,30]]
    e.call("paint_strokes",strokes=[{"points":zigzag,"brush":{"size":3,"pressure_size":False,"stabilization":.2}}])
    a=layer_alpha(e)
    assert a[30:36,68:73].max()>0 and a[94:101,48:53].max()>0  # peaks survive (used to flatten)


# --- Wet media fixes ------------------------------------------------------------------------------

def test_smudge_on_transparent_layer_has_no_dark_streaks(e):
    e.call("paint_strokes",strokes=[{"points":[[40,64]],"color":"#FF2020","brush":{"size":40,"pressure_size":False}}])
    e.call("paint_strokes",strokes=[{"points":[[30,64],[100,64]],"brush":{"kind":"smudge","size":30,"mixing":.9,"pressure_size":False}}])
    pixels=np.asarray(e.doc.layer().image).astype(int)
    visible=pixels[...,3]>24
    assert visible[64,75] and pixels[visible][:,0].min()>=200 and pixels[visible][:,1].max()<=60


def test_watercolor_mixing_does_not_pick_up_transparent_black(e):
    e.call("paint_strokes",strokes=[{"points":[[20,64],[108,64]],"color":"#3366CC",
        "brush":{"kind":"watercolor","size":30,"mixing":1,"texture":0,"pressure_size":False}}])
    pixels=np.asarray(e.doc.layer().image).astype(int)
    visible=pixels[...,3]>8
    assert np.abs(pixels[visible][:,:3]-[0x33,0x66,0xCC]).max()<=2


def test_edge_darkening_shows_on_soft_brush(e):
    e.call("paint_strokes",strokes=[{"points":[[64,64]],"color":"#000000",
        "brush":{"size":90,"hardness":.3,"edge_darkening":1,"pressure_size":False,"texture":0}}])
    row=layer_alpha(e)[64]
    assert row[20:45].max()>row[64]+15


# --- Shapes and selections ------------------------------------------------------------------------

def test_hard_polygon_uses_half_open_pixel_rule_and_tiles(e):
    e.call("paint_shape",shape="polygon",points=[[10,10],[20,10],[20,20],[10,20]],fill="#000000",antialias=False)
    assert layer_alpha(e).sum()==100*255
    e.call("paint_layer",action="clear")
    e.call("paint_shape",shape="polygon",points=[[30,30],[60,30],[30,60]],fill="#FF0000",antialias=False)
    e.call("paint_layer",action="create")
    e.call("paint_shape",shape="polygon",points=[[60,30],[60,60],[30,60]],fill="#0000FF",antialias=False)
    first,second=(alpha(l.image)>0 for l in e.doc.layers)
    assert not (first&second).any() and (first|second).sum()==900
    e.call("paint_selection",action="polygon",points=[[10,10],[20,10],[20,20],[10,20]],antialias=False)
    assert np.asarray(e.doc.selection).sum()==100*255
    e.call("paint_layer",action="clear")
    e.call("paint_selection",action="none")
    e.call("paint_shape",shape="polygon",points=[[5,5],[5,5.2],[90,5.1]],fill="#000000",antialias=False)
    assert e.doc.layer().image.getbbox() is None  # zero-area sliver paints nothing (was a 1 px line)


def test_bleed_hides_seams_between_antialiased_facets(e):
    for bleed,expected in ((0,False),(.75,True)):
        e.call("paint_layer",action="clear")
        e.call("paint_shape",shape="polygon",points=[[20,20],[100,20],[20,100]],fill="#000000",bleed=bleed)
        e.call("paint_shape",shape="polygon",points=[[100,20],[100,100],[20,100]],fill="#000000",bleed=bleed)
        interior=layer_alpha(e)[24:96,24:96]
        assert (interior.min()==255)==expected


def test_smooth_polygon_shape_and_selection(e):
    corners=[[24,24],[104,24],[104,104],[24,104]]
    e.call("paint_shape",shape="polygon",points=corners,fill="#000000",smooth=True)
    a=layer_alpha(e)
    # The curve passes through every vertex and bulges between them (a straight edge would stop at y=24).
    assert a[64,64]==255 and a[25,25]>0 and a[19,64]==255
    e.call("paint_layer",action="clear")
    e.call("paint_shape",shape="polygon",points=corners,fill="#000000")
    assert layer_alpha(e)[19,64]==0
    e.call("paint_selection",action="polygon",points=corners,smooth=True)
    assert e.doc.selection.getpixel((64,19))==255 and e.doc.selection.getpixel((64,64))==255


# --- Patterns ---------------------------------------------------------------------------------------

def test_hatch_spacing_area_and_document_anchoring(e):
    e.call("paint_pattern",kind="hatch",angle=0,spacing=8,width=2,rectangle=[0,0,64,128],color="#000000")
    column=layer_alpha(e)[:,32]>128
    assert 14<=(np.diff(np.r_[0,column.astype(int),0])==1).sum()<=17 and layer_alpha(e)[:,70:].max()==0
    # Two neighbouring fills continue one pattern: same pixels as a single fill.
    e.call("paint_pattern",kind="hatch",angle=30,spacing=6,rectangle=[64,0,128,128],layer_id=e.doc.active_layer)
    split=layer_alpha(e)[:,64:]
    e.call("paint_layer",action="clear")
    e.call("paint_pattern",kind="hatch",angle=30,spacing=6,rectangle=[0,0,128,128])
    assert np.array_equal(split,layer_alpha(e)[:,64:])


def test_crosshatch_jitter_is_seeded(e):
    args=dict(kind="hatch",angles=[45,-45],spacing=7,width=1.2,jitter=.6,taper=.5,points=[[10,10],[118,20],[64,118]],seed=4)
    first=e.call("paint_pattern",**args)
    pixels=e.doc.layer().image.tobytes()
    assert first["marks"]>20 and first["area"]=="polygon"
    e.call("paint_history",action="undo")
    e.call("paint_pattern",**args)
    assert e.doc.layer().image.tobytes()==pixels


def test_halftone_gradient_and_stipple_density(e):
    e.call("paint_pattern",kind="halftone",spacing=8,angle=15,gradient={"type":"linear","start":[0,0],"end":[128,0],"from":.05,"to":.95})
    a=layer_alpha(e)
    assert a[:,:32].mean()<a[:,96:].mean()/3
    e.call("paint_layer",action="clear")
    e.call("paint_pattern",kind="stipple",spacing=5,size=2,density=.2,rectangle=[0,0,64,128],seed=2)
    e.call("paint_pattern",kind="stipple",spacing=5,size=2,density=.9,rectangle=[64,0,128,128],seed=2)
    a=layer_alpha(e)
    assert a[:,:64].mean()*2.5<a[:,64:].mean()


def test_dither_is_hard_and_follows_level(e):
    result=e.call("paint_pattern",kind="dither",cell=4,density=.25,color="#224466")
    a=layer_alpha(e)
    assert set(np.unique(a))<={0,255} and abs((a>0).mean()-.25)<.02 and result["marks"]>0
    blocks=a.reshape(32,4,32,4).transpose(0,2,1,3).reshape(32,32,16)
    assert ((blocks==0).all(axis=2)|(blocks==255).all(axis=2)).all()  # whole 4x4 art pixels


def test_pattern_validation_and_empty_area(e):
    with pytest.raises(ValueError): e.call("paint_pattern",kind="plaid")
    e.call("paint_selection",action="rectangle",rectangle=[200,200,300,300])
    assert e.call("paint_pattern",kind="stipple")["marks"]==0


# --- Filters, transforms ------------------------------------------------------------------------------

def test_relief_lights_one_side_and_keeps_alpha(e):
    e.call("paint_fill",color="#808080")
    e.call("paint_shape",shape="rectangle",points=[[40,40],[88,88]],fill="#FFFFFF")
    before=layer_alpha(e).copy()
    e.call("paint_filter",kind="relief",amount=1,radius=1.5,angle=135)
    image=e.doc.layer().image
    assert np.array_equal(layer_alpha(e),before)
    assert image.getpixel((40,64))[0]>image.getpixel((87,64))[0]  # upper-left edges lit, lower-right shaded


def test_nearest_transform_keeps_pixel_art_crisp(e):
    e.call("paint_shape",shape="rectangle",points=[[10,10],[14,14]],fill="#FF0000",antialias=False)
    e.call("paint_transform",scale=[3,3],origin=[0,0],resample="nearest")
    a=layer_alpha(e)
    assert set(np.unique(a))=={0,255} and a.sum()==144*255
    e.call("paint_layer",action="clear")
    e.call("paint_shape",shape="rectangle",points=[[10,10],[14,14]],fill="#FF0000",antialias=False)
    e.call("paint_transform",rotation=10)
    pixels=np.asarray(e.doc.layer().image).astype(int)
    edge=(pixels[...,3]>0)&(pixels[...,3]<255)
    assert edge.any() and pixels[edge][:,0].min()>=250  # premultiplied: no dark fringe on moved edges


# --- Text --------------------------------------------------------------------------------------------

def test_letter_spacing_line_height_and_baseline_anchor(e):
    plain=e.call("paint_text",text="AVA",x=10,y=10,style={"font_size":20})["bbox"]
    wide=e.call("paint_text",text="AVA",x=10,y=40,style={"font_size":20,"letter_spacing":8})["bbox"]
    assert (wide[2]-wide[0])-(plain[2]-plain[0])>=14
    two=e.call("paint_text",text="x\nx",x=60,y=10,style={"font_size":20,"line_height":40})
    obj=e.doc.layer().objects[-1]
    assert obj["style"]["line_height"]==40 and two["bbox"][3]-two["bbox"][1]>=40
    base=e.call("paint_text",text="xx",x=20,y=100,style={"font_size":30,"anchor":"ls"})["bbox"]
    assert abs(base[3]-100)<=2  # 'x' sits on the baseline
    descender=e.call("paint_text",text="gg",x=80,y=100,style={"font_size":30,"anchor":"ls"})["bbox"]
    assert descender[3]>104 and abs(descender[1]-base[1])<=3
    e.call("paint_text",text="justified words\nlast line",x=5,y=110,style={"font_size":8,"align":"justify"})
    with pytest.raises(ValueError): e.call("paint_text",text="x",x=1,y=1,style={"anchor":"lx"})


# --- Files -------------------------------------------------------------------------------------------

def test_export_reports_whether_project_is_current(e,tmp_path):
    e.call("paint_fill",color="#FF0000")
    first=e.call("paint_document",action="export",path=str(tmp_path/"a.png"))
    assert first["project_saved"] is False and "save" in first["note"]
    e.call("paint_document",action="save",path=str(tmp_path/"a.apaint"))
    second=e.call("paint_document",action="export",path=str(tmp_path/"b.png"))
    assert second["project_saved"] is True and "note" not in second and second["project_path"].endswith("a.apaint")


def test_reopen_inspect_and_copies(e,tmp_path):
    e.call("paint_fill",color="#FF0000")
    path=tmp_path/"p.apaint"
    e.call("paint_document",action="save",path=str(path))
    again=e.call("paint_document",action="open",path=str(path))
    assert again["already_open"] and len(e.documents)==1
    e.call("paint_fill",color="#00FF00")
    with pytest.raises(ValueError,match="unsaved"): e.call("paint_document",action="open",path=str(path))
    reloaded=e.call("paint_document",action="open",path=str(path),force=True)
    assert reloaded["reloaded_from_disk"] and e.doc.layer().image.getpixel((1,1))==(255,0,0,255)
    report=e.call("paint_document",action="inspect",path=str(path))
    assert report["layer_count"]==1 and report["open_in_session"] and report["matches_open_document"]
    copy_path=tmp_path/"copy.apaint"
    e.call("paint_document",action="save",path=str(copy_path))
    e.call("paint_document",action="open",path=str(path))  # the first file of the same document
    assert len(e.documents)==2


# --- History ----------------------------------------------------------------------------------------

def test_history_stores_only_changed_layers(tmp_path):
    e=Engine(tmp_path/"state")
    e.call("paint_document",action="create",width=1600,height=1600,background=None)
    for i in range(6):  # 6 full layers x ~10 MB: a full snapshot per step would keep only ~2 steps
        e.call("paint_layer",action="create",name=f"l{i}")
        e.call("paint_fill",color=f"#{i*40:02X}2040")
    steps_before=len(e.doc.undo_stack)
    for i in range(20):
        e.call("paint_strokes",strokes=[{"points":[[50+i*60,50],[50+i*60,400]],"brush":{"size":10}}],layer_id="l3")
    assert len(e.doc.undo_stack)>=20
    marked=composite(e.doc).tobytes()
    e.call("paint_history",action="undo",steps=20)
    assert e.doc.layer("l3").image.getpixel((50,200))==(120,32,64,255)
    e.call("paint_history",action="redo",steps=20)
    assert composite(e.doc).tobytes()==marked and steps_before>=6


def test_undo_redo_exact_across_layer_create_delete(e):
    e.call("paint_fill",color="#102030")
    states=[composite(e.doc).tobytes()]
    e.call("paint_layer",action="create",name="x"); e.call("paint_fill",color="#FF0000",opacity=.5); states.append(composite(e.doc).tobytes())
    e.call("paint_layer",action="delete",layer_id=e.doc.layers[0].id); states.append(composite(e.doc).tobytes())
    e.call("paint_strokes",strokes=[{"points":[[5,5],[120,120]],"brush":{"size":9}}]); states.append(composite(e.doc).tobytes())
    for expected in reversed(states[:-1]):
        e.call("paint_history",action="undo")
        if composite(e.doc).tobytes()!=expected:
            e.call("paint_history",action="undo")  # layer create + fill are two steps
        assert composite(e.doc).tobytes()==expected
    while e.doc.redo_stack: e.call("paint_history",action="redo")
    assert composite(e.doc).tobytes()==states[-1]


# --- Errors that explain themselves ------------------------------------------------------------------

def test_flat_point_list_and_brush_key_errors_are_explicit(e):
    with pytest.raises(ValueError,match="pairs"): e.call("paint_shape",shape="polygon",points=[10,10,20,20,30,10],fill="#000")
    with pytest.raises(ValueError,match="brush hardness"):
        e.call("paint_strokes",strokes=[{"points":[[1,1]],"brush":{"hardness":3}}])
    with pytest.raises(ValueError,match="valid keys"):
        e.call("paint_brush",action="configure",settings={"sise":3})


# --- Real MCP ---------------------------------------------------------------------------------------

def test_mcp_exposes_0_3_features(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    plan=tmp_path/"plan.jsonl"
    plan.write_text("\n".join(json.dumps(line) for line in [
        {"tool":"paint_layer","arguments":{"action":"create","name":"ink","position":"top"},"store":"ink"},
        {"tool":"paint_strokes","arguments":{"layer_id":{"$ref":"ink.layer_id"},"smooth":True,
            "strokes":[{"points":[[5,40,.2],[30,10,1],[60,40,.1]]}],"brush":{"size":6}}},
        {"tool":"paint_pattern","arguments":{"kind":"halftone","spacing":6,"rectangle":[0,44,64,64],"layer_id":"ink"}}]),"utf-8")

    async def scenario():
        params=StdioServerParameters(command=sys.executable,args=["-m","csp_headless.server","--state-dir",str(tmp_path/"mcp")])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                names={t.name for t in (await session.list_tools()).tools}
                assert "paint_pattern" in names
                doc=(await session.call_tool("paint_document",{"action":"create","width":64,"height":64})).structuredContent["document_id"]
                batch=await session.call_tool("paint_batch",{"actions_file":str(plan),"document_id":doc})
                assert not batch.isError, batch.content
                assert batch.structuredContent["completed"]==3 and batch.structuredContent["stored"]["ink"]["index"]==1
                stroke=await session.call_tool("paint_strokes",{"strokes":[{"points":[[5,5],[60,5]],"dash":[4,4]}],
                                                                "layer_id":"ink","brush":{"size":2},"document_id":doc})
                assert not stroke.isError, stroke.content
                exported=await session.call_tool("paint_document",{"action":"export","path":str(tmp_path/"o.png"),"document_id":doc})
                assert exported.structuredContent["project_saved"] is False and "note" in exported.structuredContent
                guide=await session.read_resource("paint://guide")
                assert "actions_file" in guide.contents[0].text
    asyncio.run(scenario())


def test_copy_on_write_history_survives_mixed_operations(e):
    """Random mix of in-place and replacing operations: every undo/redo state is exact."""
    import random
    rng=random.Random(5)
    e.call("paint_layer",action="create",name="a"); e.call("paint_layer",action="create",name="b")
    states=[composite(e.doc).tobytes()]
    operations=[
        lambda: e.call("paint_strokes",strokes=[{"points":[[rng.randint(0,127),rng.randint(0,127)],[rng.randint(0,127),rng.randint(0,127)]],
                                                 "color":"#%06X"%rng.randint(0,0xFFFFFF)}],brush={"size":rng.randint(3,20)},layer_id=rng.choice(["a","b"])),
        lambda: e.call("paint_fill",color="#%06X"%rng.randint(0,0xFFFFFF),opacity=.3,layer_id=rng.choice(["a","b"])),
        lambda: e.call("paint_strokes",strokes=[{"points":[[10,64],[118,64]]}],brush={"kind":"smudge","size":20},layer_id="a"),
        lambda: e.call("paint_strokes",strokes=[{"points":[[64,10],[64,118]]}],brush={"kind":"eraser","size":9},layer_id="b"),
        lambda: e.call("paint_transform",translate=[3,-2],layer_id="a"),
        lambda: e.call("paint_filter",kind="blur",radius=1.5,layer_id="b"),
        lambda: e.call("paint_pattern",kind="stipple",spacing=6,layer_id="a",seed=rng.randint(0,99)),
        lambda: e.call("paint_layer",action="update",layer_id="b",properties={"opacity":rng.random()}),
    ]
    for _ in range(24):
        rng.choice(operations)()
        states.append(composite(e.doc).tobytes())
        if rng.random()<.2:  # a failing batch must roll back without disturbing history
            with pytest.raises(ValueError):
                e.call("paint_batch",actions=[{"tool":"paint_fill","arguments":{"color":"#FFFFFF","layer_id":"a"}},
                                              {"tool":"paint_fill","arguments":{"color":"bad"}}])
            assert composite(e.doc).tobytes()==states[-1]
    for expected in reversed(states[:-1]):
        e.call("paint_history",action="undo")
        assert composite(e.doc).tobytes()==expected
    for expected in states[1:]:
        e.call("paint_history",action="redo")
        assert composite(e.doc).tobytes()==expected
