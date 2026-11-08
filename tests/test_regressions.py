"""Cross-operation invariants and format interoperability, not implementation snapshots."""
from io import BytesIO
from pathlib import Path
import threading

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


def test_vector_transform_round_trip_does_not_accumulate_clipping(e):
    e.call("paint_layer",action="create",kind="vector")
    e.call("paint_shape",shape="ellipse",points=[[10,10],[40,40]],fill="#F00")
    original=e.doc.layer().image.tobytes()
    e.call("paint_transform",translate=[200,0])
    assert e.doc.layer().image.getbbox() is None
    e.call("paint_transform",translate=[-200,0])
    assert e.doc.layer().image.tobytes()==original


def test_selection_and_layer_mask_can_be_seen_directly(e):
    e.call("paint_selection",action="rectangle",rectangle=[10,10,40,40])
    selection=e.call("paint_snapshot",source="selection")
    assert selection.image.getpixel((20,20))==(255,255,255,255)
    assert selection.image.getpixel((100,100))==(0,0,0,255)
    e.call("paint_layer",action="mask_from_selection")
    mask=e.call("paint_snapshot",source="mask")
    assert selection.image.tobytes()==mask.image.tobytes()


def test_merge_normal_and_flatten_preserve_visible_composition(e):
    e.call("paint_fill",color="#FF0000",opacity=.7)
    e.call("paint_layer",action="create")
    e.call("paint_strokes",strokes=[{"points":[[15,20],[110,110]],"color":"#0000FF","brush":{"size":30,"opacity":.6}}])
    before=composite(e.doc).tobytes()
    e.call("paint_layer",action="merge_down")
    assert composite(e.doc).tobytes()==before
    e.call("paint_layer",action="create")
    e.call("paint_fill",color="#FFFF00",opacity=.5)
    e.call("paint_layer",action="update",properties={"blend":"multiply"})
    before=composite(e.doc).tobytes()
    e.call("paint_layer",action="flatten")
    assert composite(e.doc).tobytes()==before and len(e.doc.layers)==1


@pytest.mark.parametrize("ext",[".ora",".psd"])
def test_interchange_bakes_masks_and_clipping(e,tmp_path,ext):
    e.call("paint_strokes",strokes=[{"points":[[20,30],[90,90]],"color":"#FF0000","brush":{"size":30}}])
    e.call("paint_layer",action="create")
    e.call("paint_fill",color="#0000FF")
    e.call("paint_layer",action="update",properties={"clipping":True,"opacity":.7})
    e.call("paint_selection",action="rectangle",rectangle=[0,0,60,128])
    e.call("paint_layer",action="mask_from_selection")
    path=tmp_path/("layers"+ext)
    e.call("paint_document",action="export",path=str(path))
    if ext==".ora":
        fresh=Engine(tmp_path/"other"); fresh.call("paint_document",action="import_ora",path=str(path))
        assert composite(fresh.doc).tobytes()==composite(e.doc).tobytes()
    else:
        from psd_tools import PSDImage
        psd=PSDImage.open(path)
        expected=Image.new("RGBA",e.doc.size,"white"); expected.alpha_composite(composite(e.doc))
        preview=Image.new("RGBA",e.doc.size,"white"); preview.alpha_composite(psd.topil().convert("RGBA"))
        assert np.max(np.abs(np.asarray(expected,dtype=int)-np.asarray(preview,dtype=int)))<=1
        # Independently compose exported layer channels, rather than trusting its cached preview.
        canvas=Image.new("RGBA",e.doc.size)
        for layer in psd:
            image=layer.topil().convert("RGBA")
            image.putalpha(image.getchannel("A").point(lambda a:round(a*layer.opacity/255)))
            canvas=Image.alpha_composite(canvas,image)
        actual=Image.new("RGBA",e.doc.size,"white"); actual.alpha_composite(canvas)
        assert np.max(np.abs(np.asarray(expected,dtype=int)-np.asarray(actual,dtype=int)))<=1


def test_abort_rolls_back_even_if_requested_during_single_stroke(e,monkeypatch):
    import csp_headless.engine as module
    started=threading.Event(); resume=threading.Event()
    native=module.paint_stroke
    def paused(*args,**kwargs):
        started.set(); assert resume.wait(5)
        return native(*args,**kwargs)
    monkeypatch.setattr(module,"paint_stroke",paused)
    errors=[]
    def paint():
        try: e.call("paint_strokes",strokes=[{"points":[[20,20],[90,90]]}])
        except InterruptedError as error: errors.append(error)
    thread=threading.Thread(target=paint); thread.start()
    assert started.wait(5)
    assert e.call("paint_abort")["abort_requested"]
    resume.set(); thread.join(5)
    assert not thread.is_alive() and errors and e.doc.layer().image.getbbox() is None


def test_bad_transform_leaves_scene_and_history_unchanged(e):
    e.call("paint_fill",color="#123456")
    before=e.doc.info(); image=e.doc.layer().image.tobytes()
    with pytest.raises(ValueError): e.call("paint_transform",matrix=[1,0,0,0,0,0])
    assert e.doc.info()==before and e.doc.layer().image.tobytes()==image


def test_undo_restores_editable_source_and_masks(e):
    e.call("paint_text",text="ABC",x=10,y=10,style={"font_size":24})
    layer=e.doc.layer(); object_id=layer.objects[0]["id"]
    original=layer.image.tobytes()
    e.call("paint_layer",action="rasterize")
    e.call("paint_history")
    assert e.doc.layer().kind=="text" and e.doc.layer().objects[0]["id"]==object_id
    assert e.doc.layer().image.tobytes()==original


def test_existing_export_is_not_overwritten(e,tmp_path):
    path=tmp_path/"user.png"; path.write_bytes(b"user content")
    with pytest.raises(FileExistsError): e.call("paint_document",action="export",path=str(path))
    assert path.read_bytes()==b"user content"


def test_invalid_text_style_rolls_back_new_layer(e):
    before=e.doc.info()
    with pytest.raises(ValueError): e.call("paint_text",text="Text",x=1,y=1,style={"font":"no-such-font"})
    assert e.doc.info()==before


def test_cancelled_queued_request_does_not_abort_active_painter(e,monkeypatch):
    import csp_headless.engine as module
    entered=threading.Event(); release=threading.Event(); cancel=threading.Event()
    original=module.paint_stroke
    def paused(*args,**kwargs):
        entered.set(); assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,"paint_stroke",paused)
    successful=[]; errors=[]
    def active():
        successful.append(e.call("paint_strokes",strokes=[{"points":[[20,20],[90,90]]}]))
    def queued():
        try: e.call("paint_fill",_cancel_event=cancel,color="#FF0000")
        except InterruptedError as error: errors.append(error)
    first=threading.Thread(target=active); first.start(); assert entered.wait(5)
    second=threading.Thread(target=queued); second.start()
    cancel.set(); release.set(); first.join(5); second.join(5)
    assert successful and errors and len(e.doc.undo_stack)==1
    assert e.doc.layer().image.getpixel((0,0))[3]==0


def test_cancel_active_request_rolls_back(e,monkeypatch):
    import csp_headless.engine as module
    cancel=threading.Event()
    original=module.paint_stroke
    def cancelled(*args,**kwargs):
        result=original(*args,**kwargs)
        cancel.set()
        return result
    monkeypatch.setattr(module,"paint_stroke",cancelled)
    with pytest.raises(InterruptedError):
        e.call("paint_strokes",_cancel_event=cancel,strokes=[{"points":[[20,20],[90,90]]}])
    assert e.doc.layer().image.getbbox() is None and not e.doc.undo_stack


def test_layer_snapshots_cannot_silently_compare_different_active_layers(e):
    before=e.call("paint_snapshot",source="layer")
    e.call("paint_layer",action="create")
    with pytest.raises(ValueError,match="same document, layer"):
        e.call("paint_snapshot",source="layer",compare_to=before.info["snapshot_id"])


def test_subpixel_empty_crop_is_rejected(e):
    with pytest.raises(ValueError,match="smaller than one pixel"):
        e.call("paint_snapshot",rectangle=[0,0,.1,.1])


def test_hidden_rgb_in_erased_pixels_does_not_block_flood(e):
    e.call("paint_strokes",strokes=[{"points":[[64,64]],"color":"#FF0000","brush":{"size":60}}])
    e.call("paint_strokes",strokes=[{"points":[[64,64]],"brush":{"kind":"eraser","size":90}}])
    e.call("paint_fill",kind="flood",point=[1,1],color="#0000FF")
    assert e.doc.layer().image.getpixel((64,64))==(0,0,255,255)


def test_stable_layer_id_wins_over_another_layers_name(e):
    original=e.doc.active_layer
    e.call("paint_layer",action="create",name=original)
    selected=e.call("paint_layer",action="select",layer_id=original)
    assert selected["id"]==original and e.doc.active_layer==original
