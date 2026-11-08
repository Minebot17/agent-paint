from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import threading
import zipfile

import numpy as np
from PIL import Image, ImageChops
import pytest

from csp_headless.engine import Engine, Picture, OPERATIONS
from csp_headless.model import BLENDS
from csp_headless.render import blend, composite, fonts
from csp_headless.storage import open_project, save_project


@pytest.fixture
def e(tmp_path):
    engine=Engine(tmp_path/"state")
    engine.call("paint_document",action="create",width=96,height=96,background=None)
    return engine


def pixels(e): return composite(e.doc).tobytes()


def dot(e,x=40,y=40,size=20,color="#FF0000",**brush):
    return e.call("paint_strokes",strokes=[{"points":[[x,y]],"color":color,"brush":{"size":size,"pressure_size":False,**brush}}])


def test_empty_canvas_is_transparent_and_no_desktop(e):
    state=e.call("paint_state")
    assert state["headless"] and state["uses_desktop"] is False
    assert e.doc.layer().image.getbbox() is None


@pytest.mark.parametrize("size",[(0,1),(1,0),(1.1,20),(100000,20),(10000,10000)])
def test_document_resource_limits(e,size):
    count=len(e.documents)
    with pytest.raises(ValueError): e.call("paint_document",action="create",width=size[0],height=size[1])
    assert len(e.documents)==count


def test_multidocument_independence(e):
    first=e.doc.id; dot(e)
    first_pixels=pixels(e)
    e.call("paint_document",action="create",width=16,height=16)
    assert len(e.documents)==2 and e.doc.size==(16,16)
    e.call("paint_document",action="select",document_id=first)
    assert pixels(e)==first_pixels
    with pytest.raises(ValueError): e.call("paint_document",action="close")


def test_pressure_width_and_taper(e):
    e.call("paint_strokes",strokes=[{"points":[[10,48,.1],[80,48,1]],"brush":{"size":30}}])
    alpha=np.array(e.doc.layer().image.getchannel("A"))
    assert (alpha[:,75]>100).sum()>(alpha[:,20]>100).sum()*2
    e.call("paint_layer",action="clear")
    e.call("paint_strokes",strokes=[{"points":[[10,48],[85,48]],"brush":{"size":30,"taper_end":.5}}])
    alpha=np.array(e.doc.layer().image.getchannel("A"))
    assert (alpha[:,40]>100).sum()>(alpha[:,80]>100).sum()*2


def test_normalized_coordinates_and_view_do_not_move_drawing(e):
    e.call("paint_view",zoom=4,center=[40,40],rotation=25)
    e.call("paint_strokes",strokes=[{"points":[[.5,.5]],"brush":{"size":5}}],coordinate_space="normalized")
    assert e.doc.layer().image.getpixel((48,48))[3]>0
    assert e.doc.layer().image.getpixel((10,10))[3]==0


def test_eraser_and_alpha_lock(e):
    dot(e,size=40)
    e.call("paint_layer",action="update",properties={"alpha_lock":True})
    old_alpha=e.doc.layer().image.getchannel("A").tobytes()
    dot(e,size=90,color="#00FF00")
    assert e.doc.layer().image.getchannel("A").tobytes()==old_alpha
    # 0.3: the eraser on an alpha-locked layer is an explicit error, not a silent no-op.
    with pytest.raises(ValueError,match="alpha_lock"): dot(e,size=20,kind="eraser")
    assert e.doc.layer().image.getchannel("A").tobytes()==old_alpha
    e.call("paint_layer",action="update",properties={"alpha_lock":False})
    dot(e,size=20,kind="eraser")
    assert e.doc.layer().image.getpixel((40,40))[3]==0


def test_selection_and_empty_selection_are_distinct(e):
    e.call("paint_selection",action="rectangle",rectangle=[10,10,30,30])
    e.call("paint_fill",color="#FF0000")
    assert e.doc.layer().image.getpixel((20,20))==(255,0,0,255)
    assert e.doc.layer().image.getpixel((60,60))[3]==0
    e.call("paint_selection",action="all")
    e.call("paint_selection",action="invert")
    before=pixels(e); e.call("paint_fill",color="#00FF00")
    assert pixels(e)==before
    e.call("paint_selection",action="none")
    e.call("paint_fill",color="#00FF00")
    assert e.doc.layer().image.getpixel((60,60))==(0,255,0,255)


@pytest.mark.parametrize("action",["polygon","ellipse","rectangle","flood","from_layer"])
def test_selection_sources(e,action):
    dot(e,size=60)
    arguments={"action":action}
    if action=="polygon": arguments["points"]=[[10,10],[70,10],[40,70]]
    if action in {"ellipse","rectangle"}: arguments["rectangle"]=[10,10,70,70]
    if action=="flood": arguments["point"]=[40,40]
    e.call("paint_selection",**arguments)
    assert e.doc.selection.getpixel((40,40))>200
    assert e.doc.selection.getpixel((95,95))==0


def test_selection_boolean_and_feather(e):
    e.call("paint_selection",action="rectangle",rectangle=[10,10,40,40])
    e.call("paint_selection",action="rectangle",rectangle=[30,30,60,60],operation="add")
    assert e.doc.selection.getpixel((50,50))==255
    e.call("paint_selection",action="rectangle",rectangle=[30,30,60,60],operation="subtract")
    assert e.doc.selection.getpixel((35,35))==0
    e.call("paint_selection",action="feather",radius=3)
    assert 0<e.doc.selection.getpixel((10,20))<255
    area=np.asarray(e.doc.selection).sum()
    e.call("paint_selection",action="grow",radius=2)
    assert np.asarray(e.doc.selection).sum()>area
    e.call("paint_selection",action="shrink",radius=3)
    assert np.asarray(e.doc.selection).sum()<area


def test_flood_respects_barriers_and_selection(e):
    e.call("paint_shape",shape="line",points=[[48,0],[48,95]],outline="#FFFFFF",width=4)
    e.call("paint_fill",kind="flood",point=[10,10],color="#FF0000")
    assert e.doc.layer().image.getpixel((10,10))==(255,0,0,255)
    assert e.doc.layer().image.getpixel((80,10))[3]==0


@pytest.mark.parametrize("kind",["linear","radial"])
def test_gradients(e,kind):
    e.call("paint_fill",kind=kind,color="#FF0000",color2="#0000FF",start=[0,0],end=[95,0])
    im=e.doc.layer().image
    assert im.getpixel((0,0))==(255,0,0,255)
    assert im.getpixel((95,0))==(0,0,255,255)
    middle=im.getpixel((48,0))
    assert 120<middle[0]<135 and 120<middle[2]<135


@pytest.mark.parametrize("mode",sorted(BLENDS))
def test_blend_transparent_backdrop_retains_color(mode):
    a=Image.new("RGBA",(1,1))
    b=Image.new("RGBA",(1,1),(100,160,210,128))
    assert blend(a,b,mode).getpixel((0,0))==(100,160,210,128)


def test_multiply_screen_known_pixels():
    a=Image.new("RGBA",(1,1),(128,128,128,255)); b=Image.new("RGBA",(1,1),(128,128,128,255))
    assert blend(a,b,"multiply").getpixel((0,0))==(64,64,64,255)
    assert blend(a,b,"screen").getpixel((0,0))==(192,192,192,255)


def test_layer_opacity_visibility_mask_and_clipping(e):
    dot(e,size=40)
    base=e.doc.layer().id
    top=e.call("paint_layer",action="create")["id"]
    e.call("paint_layer",action="update",properties={"clipping":True,"opacity":.5})
    e.call("paint_fill",color="#0000FF")
    im=composite(e.doc)
    assert im.getpixel((0,0))[3]==0
    r,g,b,a=im.getpixel((40,40)); assert 120<r<135 and 120<b<135 and a==255
    e.call("paint_selection",action="rectangle",rectangle=[0,0,40,96])
    e.call("paint_layer",action="mask_from_selection")
    assert composite(e.doc).getpixel((50,40))==(255,0,0,255)
    e.call("paint_layer",action="update",properties={"visible":False})
    assert composite(e.doc).getpixel((30,40))==(255,0,0,255)


def test_duplicate_reorder_and_locked_layer(e):
    dot(e)
    original=e.doc.layer().id
    copied=e.call("paint_layer",action="duplicate")["id"]
    assert copied!=original and e.doc.layer(copied).image.tobytes()==e.doc.layer(original).image.tobytes()
    assert e.doc.layer(copied).image is not e.doc.layer(original).image
    e.call("paint_layer",action="reorder",index=0)
    assert e.doc.layers[0].id==copied
    e.call("paint_layer",action="update",properties={"locked":True})
    before=pixels(e)
    with pytest.raises(ValueError): dot(e)
    assert pixels(e)==before


@pytest.mark.parametrize("kind",["round","pencil","airbrush","watercolor","marker","eraser","smudge"])
def test_brush_kinds_render_deterministically(e,kind):
    dot(e,color="#FFEE99",size=60)
    first=e.doc.layer().image.copy()
    stroke={"points":[[20,35,.3],[50,60,1],[80,30,.5]],"color":"#152477","brush":{"kind":kind,"size":18,"seed":42}}
    e.call("paint_strokes",strokes=[stroke]); result=e.doc.layer().image.tobytes()
    assert result!=first.tobytes()
    e.call("paint_history",action="undo"); e.call("paint_strokes",strokes=[stroke])
    assert e.doc.layer().image.tobytes()==result


def test_brush_profiles_are_portable(e,tmp_path):
    e.call("paint_brush",action="configure",settings={"size":31,"taper_start":.2,"pressure_opacity":True})
    e.call("paint_brush",action="save_preset",name="my-ink")
    path=str(tmp_path/"brushes.json")
    e.call("paint_brush",action="export_presets",path=path)
    e.call("paint_brush",action="configure",settings={"size":5})
    e.call("paint_brush",action="delete_preset",name="my-ink")
    e.call("paint_brush",action="import_presets",path=path)
    e.call("paint_brush",action="load_preset",name="my-ink")
    assert e.brush["size"]==31 and e.brush["pressure_opacity"] is True


def test_batch_rolls_back_scene_and_brush_and_layers(e):
    before=pixels(e); scene=e.doc.info(); brush=copy.deepcopy(e.brush)
    actions=[{"tool":"paint_layer","arguments":{"action":"create","name":"must vanish"}},
             {"tool":"paint_brush","arguments":{"action":"configure","settings":{"size":50}}},
             {"tool":"paint_fill","arguments":{"color":"#FF0000"}},
             {"tool":"paint_brush","arguments":{"action":"save_preset","name":"must vanish"}},
             {"tool":"paint_fill","arguments":{"color":"not a color"}}]
    with pytest.raises(ValueError,match="action 4"): e.call("paint_batch",actions=actions)
    assert pixels(e)==before and e.doc.info()==scene and e.brush==brush and "must vanish" not in e.presets


def test_batch_single_undo_and_redo(e):
    before=pixels(e); rev=e.doc.revision
    actions=[{"tool":"paint_fill","arguments":{"color":"#FFFF00"}},
             {"tool":"paint_strokes","arguments":{"strokes":[{"points":[[20,20],[70,70]]}]}}]
    e.call("paint_batch",actions=actions,expected_revision=rev)
    after=pixels(e); assert before!=after and len(e.doc.undo_stack)==1
    e.call("paint_history"); assert pixels(e)==before
    e.call("paint_history",action="redo"); assert pixels(e)==after
    with pytest.raises(ValueError,match="revision conflict"): e.call("paint_strokes",strokes=[{"points":[[10,10]]}],expected_revision=rev)


def test_batch_rejects_filesystem_before_side_effects(e,tmp_path):
    path=tmp_path/"must-not-exist.json"
    with pytest.raises(ValueError): e.call("paint_batch",actions=[{"tool":"paint_brush","arguments":{"action":"export_presets","path":str(path)}}])
    assert not path.exists()


@pytest.mark.parametrize("bad",[{"points":[[0,0,float("nan")]]},{"points":[[20,20,1.2]]},
                                {"points":[[20,20]],"brush":{"size":0}},{"points":[[20,20]],"brush":{"fake":3}}])
def test_stroke_batch_preflight_and_rollback(e,bad):
    before=pixels(e)
    with pytest.raises(ValueError): e.call("paint_strokes",strokes=[{"points":[[20,20]]},bad])
    assert pixels(e)==before and not e.doc.undo_stack


def test_vectors_are_editable_after_transform(e):
    e.call("paint_layer",action="create",kind="vector")
    obj=e.call("paint_shape",shape="ellipse",points=[[10,10],[30,30]],fill="#FF0000",editable=True)["object_id"]
    e.call("paint_transform",translate=[30,0])
    assert e.doc.layer().image.getpixel((50,20))[3]>200
    e.call("paint_object",action="update",object_id=obj,changes={"fill":"#0000FF"})
    assert e.doc.layer().image.getpixel((50,20))==(0,0,255,255)
    assert e.doc.layer().image.getpixel((20,20))[3]==0
    e.call("paint_strokes",strokes=[{"points":[[5,80],[70,80]],"brush":{"size":5}}])
    assert len(e.doc.layer().objects)==2


@pytest.mark.parametrize("shape,points",[("rectangle",[[5,5],[80,80]]),("rounded_rectangle",[[5,5],[80,80]]),
    ("ellipse",[[5,5],[80,80]]),("polygon",[[5,5],[80,10],[30,80]]),("line",[[5,5],[80,80]]),
    ("bezier",[[5,5],[80,5],[5,80],[80,80]])])
def test_shapes_have_pixels(e,shape,points):
    e.call("paint_shape",shape=shape,points=points,fill="#55FF77",outline="#FF0000",width=3)
    assert e.doc.layer().image.getbbox() is not None


def test_unicode_text_edit_and_protection_of_other_layers(e):
    dot(e); original=e.doc.layers[0].image.tobytes()
    r=e.call("paint_text",text="Привет\n世界",x=3,y=3,style={"font_size":18,"color":"#224488","scale_x":.8})
    assert e.doc.layer().kind=="text" and e.doc.layer().image.getbbox()
    e.call("paint_text",text="Новый",x=10,y=30,layer_id=r["layer_id"],object_id=r["object"]["id"],style={"font_size":22})
    assert e.doc.layers[0].image.tobytes()==original
    assert len(e.doc.layer().objects)==1 and e.doc.layer().objects[0]["text"]=="Новый"


def test_selected_raster_transform(e):
    dot(e,x=20,y=20,size=12)
    dot(e,x=70,y=70,size=12,color="#0000FF")
    e.call("paint_selection",action="rectangle",rectangle=[5,5,35,35])
    e.call("paint_transform",translate=[30,0],selection_only=True)
    assert e.doc.layer().image.getpixel((20,20))[3]==0
    assert e.doc.layer().image.getpixel((50,20))[0]==255
    assert e.doc.layer().image.getpixel((70,70))[2]==255


def test_save_reopen_exact_pixels_and_editable_objects(e,tmp_path):
    dot(e)
    e.call("paint_selection",action="ellipse",rectangle=[10,10,70,70])
    e.call("paint_layer",action="mask_from_selection")
    e.call("paint_selection",action="none")
    e.call("paint_layer",action="create",kind="vector",name="Фигуры")
    e.call("paint_shape",shape="polygon",points=[[20,20],[50,20],[30,50]],fill="#33FF88")
    text=e.call("paint_text",text="Текст",x=2,y=70,style={"font_size":14})
    e.call("paint_region",name="face",rectangle=[10,10,70,70])
    before=pixels(e); path=tmp_path/"roundtrip.apaint"
    e.call("paint_document",action="save",path=str(path))
    restored=open_project(path)
    assert composite(restored).tobytes()==before and restored.layers[0].mask is not None
    assert restored.layers[1].kind=="vector" and restored.layers[1].objects
    assert restored.regions["face"]==[10,10,70,70] and not restored.info()["dirty"]
    assert restored.layers[-1].objects[0]["id"]==text["object"]["id"]


def test_overwrite_protection_and_save_path_survives_undo(e,tmp_path):
    dot(e); path=tmp_path/"saved.apaint"
    e.call("paint_document",action="save",path=str(path))
    original=path.read_bytes()
    with pytest.raises(FileExistsError): save_project(e.doc,path,False)
    assert path.read_bytes()==original
    e.call("paint_history")
    assert e.doc.path==str(path.resolve()) and e.doc.info()["dirty"]
    e.call("paint_document",action="save")
    assert e.doc.info()["dirty"] is False


@pytest.mark.parametrize("ext",[".png",".jpg",".webp",".tiff",".ora",".psd"])
def test_full_resolution_exports(e,tmp_path,ext):
    dot(e)
    e.call("paint_layer",action="create",name="Синий слой")
    dot(e,x=70,y=70,color="#0000FF")
    path=tmp_path/("out"+ext)
    e.call("paint_document",action="export",path=str(path))
    assert path.stat().st_size>100
    if ext==".ora":
        fresh=Engine(tmp_path/"ora-state"); fresh.call("paint_document",action="import_ora",path=str(path))
        assert pixels(fresh)==pixels(e)
    elif ext==".psd":
        from psd_tools import PSDImage
        psd=PSDImage.open(path)
        assert psd.size==e.doc.size and len(psd)==2 and psd[0].name=="Layer 1" and psd[1].name=="Синий слой"
        assert psd[0].topil().convert("RGBA").tobytes()==e.doc.layers[0].image.tobytes()
        expected=Image.new("RGBA",e.doc.size,"white"); expected.alpha_composite(composite(e.doc))
        actual=Image.new("RGBA",e.doc.size,"white"); actual.alpha_composite(psd.topil().convert("RGBA"))
        assert np.max(np.abs(np.asarray(expected,dtype=int)-np.asarray(actual,dtype=int)))<=1
    else:
        with Image.open(path) as image: assert image.size==e.doc.size


def test_snapshots_stay_in_document_space_and_compare(e):
    e.call("paint_region",name="detail",rectangle=[10,10,70,70])
    first=e.call("paint_snapshot",region="detail")
    e.call("paint_view",zoom=3)
    same=e.call("paint_snapshot",region="detail",compare_to=first.info["snapshot_id"])
    assert same.info["comparison"]["changed_pixel_fraction"]==0
    dot(e)
    changed=e.call("paint_snapshot",region="detail",compare_to=first.info["snapshot_id"])
    assert changed.info["comparison"]["changed_pixel_fraction"]>0
    with pytest.raises(ValueError): e.call("paint_snapshot",compare_to=first.info["snapshot_id"])


def test_compare_reference_never_changes_canvas(e,tmp_path):
    path=tmp_path/"reference.png"; Image.new("RGB",(96,96),"blue").save(path)
    before=pixels(e); result=e.call("paint_compare",reference_path=str(path),max_side=400)
    assert isinstance(result,Picture) and result.info["analysis_only"]
    assert pixels(e)==before and not e.doc.undo_stack


def test_archive_version_rejected(e,tmp_path):
    path=tmp_path/"bad.apaint"
    with zipfile.ZipFile(path,"w") as z:
        z.writestr("mimetype","application/x-agent-paint"); z.writestr("manifest.json",json.dumps({"format":"agent-paint","version":999}))
    with pytest.raises(ValueError,match="version"): e.call("paint_document",action="open",path=str(path))


def test_concurrent_calls_are_serialized(e):
    errors=[]
    def work(index):
        try: dot(e,x=10+index*10,y=40,size=5)
        except Exception as error: errors.append(error)
    threads=[threading.Thread(target=work,args=(i,)) for i in range(6)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors and len(e.doc.undo_stack)==6
    for i in range(6): assert e.doc.layer().image.getpixel((10+i*10,40))[3]>0


def test_core_has_no_desktop_imports():
    import csp_headless
    forbidden={"ctypes","win32api","win32gui","win32con","pyautogui","pynput","subprocess","mss","tkinter","PyQt5","PySide6"}
    for path in Path(csp_headless.__file__).parent.glob("*.py"):
        if path.name=="server.py": continue
        tree=ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node,ast.Import): assert not any(a.name.split(".")[0] in forbidden for a in node.names)
            if isinstance(node,ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden
                assert not (node.module=="PIL" and any(a.name=="ImageGrab" for a in node.names))
