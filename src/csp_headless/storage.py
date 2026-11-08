"""Versioned project archives and interoperable exports, written atomically."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import struct
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image

from .model import Document, Layer, BLENDS, dimensions, finite
from .render import composite, rgba

MIME = "application/x-agent-paint"


def png(image):
    buffer = BytesIO(); image.save(buffer,"PNG"); return buffer.getvalue()


def check_path(path):
    """Reject control characters: an unescaped '\\n' or '\\t' inside a Windows path otherwise surfaces as an opaque OS error."""
    text = str(path)
    for char in text:
        if ord(char) < 32:
            raise ValueError(f"Path contains control character U+{ord(char):04X} ({char!r}) in {text!r}. "
                             "A backslash sequence such as \\n or \\t was probably interpreted; use forward slashes or escape backslashes")
    return path


def atomic_write(path, writer, overwrite=True):
    check_path(path)
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".apaint-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as stream:
            writer(stream)
            stream.flush(); os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary,path)
        else:
            # Hard link is atomic and cannot clobber an existing destination.
            os.link(temporary,path)
            os.unlink(temporary)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return str(path)


def save_project(document, path, overwrite=True):
    if Path(path).suffix.lower() != ".apaint":
        raise ValueError("Editable projects use .apaint; use export for PNG/ORA/PSD")
    metadata=document.info()
    metadata={k:metadata[k] for k in ("document_id","name","size","dpi","background","active_layer","revision","regions")}
    # Optional keys (older readers ignore them): the document's current brush and colour.
    if document.brush:
        metadata["brush"]=document.brush; metadata["color"]=document.color
    manifest = {"format":"agent-paint","version":1,"document":metadata,"layers":[]}
    def write(stream):
        with zipfile.ZipFile(stream,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("mimetype",MIME,compress_type=zipfile.ZIP_STORED)
            for layer in document.layers:
                record = layer.info()
                record["image"] = f"layers/{layer.id}.png"
                archive.writestr(record["image"],png(layer.image))
                if layer.mask is not None:
                    record["mask"] = f"masks/{layer.id}.png"
                    archive.writestr(record["mask"],png(layer.mask))
                manifest["layers"].append(record)
            if document.selection is not None:
                manifest["selection"] = "selection.png"
                archive.writestr("selection.png",png(document.selection))
            preview = composite(document); preview.thumbnail((512,512))
            archive.writestr("preview.png",png(preview))
            archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,allow_nan=False))
    result = atomic_write(path,write,overwrite)
    document.path = result; document.saved_revision = document.revision
    return result


def open_project(path):
    check_path(path)
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != ".apaint":
        raise ValueError("Open expects .apaint. Use import_image for raster images or import_ora for OpenRaster")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries)>512 or sum(e.file_size for e in entries)>512*1024*1024:
            raise ValueError("Project archive exceeds resource limits")
        if archive.read("mimetype").decode() != MIME:
            raise ValueError("Not an Agent Paint archive")
        data = json.loads(archive.read("manifest.json"))
        if data.get("format") != "agent-paint" or data.get("version") != 1:
            raise ValueError("Unsupported project format/version")
        meta = data["document"]
        width,height = dimensions(*meta["size"])
        doc = Document(width,height,name=meta["name"],id=meta["document_id"],
                       dpi=finite(meta["dpi"],1,2400),background=meta["background"])
        if doc.background is not None: rgba(doc.background)
        def image(name,mode):
            with Image.open(BytesIO(archive.read(name))) as source:
                if source.size != doc.size: raise ValueError("Layer dimensions differ from document")
                return source.convert(mode)
        doc.layers = []
        if not 1 <= len(data["layers"]) <= 128: raise ValueError("Expected 1..128 layers")
        identifiers = set()
        for record in data["layers"]:
            if record["id"] in identifiers: raise ValueError("Duplicate layer id")
            identifiers.add(record["id"])
            if record["blend"] not in BLENDS or record["kind"] not in {"raster","vector","text"}:
                raise ValueError("Unsupported layer type or blend")
            layer = Layer(record["name"],image(record["image"],"RGBA"),id=record["id"],
                          opacity=finite(record["opacity"],0,1),blend=record["blend"],kind=record["kind"])
            for key in ("visible","locked","alpha_lock","clipping"):
                if type(record[key]) is not bool: raise ValueError("Invalid layer flag")
                setattr(layer,key,record[key])
            layer.objects = record["objects"]
            if not isinstance(layer.objects,list) or len(layer.objects)>100000: raise ValueError("Invalid objects")
            if record.get("mask"): layer.mask = image(record["mask"],"L")
            doc.layers.append(layer)
        doc.active_layer = meta["active_layer"]; doc.layer()
        doc.regions = meta.get("regions",{})
        doc.revision = int(meta["revision"])
        if isinstance(meta.get("brush"),dict):
            from .render import validate_brush
            try:
                doc.brush = validate_brush(meta["brush"]); rgba(meta.get("color","#000000"))
                doc.color = meta.get("color","#000000")
            except ValueError:
                doc.brush = {}  # an unreadable stored brush falls back to the session's brush
        if "selection" in data: doc.selection = image(data["selection"],"L")
        # View is session-specific; reopen at document overview.
        doc.path = str(path); doc.saved_revision = doc.revision
        return doc


ORA_BLENDS = {"normal":"svg:src-over","multiply":"svg:multiply","screen":"svg:screen", "overlay":"svg:overlay",
              "darken":"svg:darken","lighten":"svg:lighten","add":"svg:plus","difference":"svg:difference",
              "soft_light":"svg:soft-light"}


def effective_layers(document):
    """Bake masks/clipping into per-layer alpha for interchange, retain opacity separately."""
    from PIL import ImageChops
    base = Image.new("L",document.size)
    for layer in document.layers:
        image = layer.image.copy()
        alpha = image.getchannel("A")
        if layer.mask is not None: alpha = ImageChops.multiply(alpha,layer.mask)
        if layer.clipping: alpha = ImageChops.multiply(alpha,base)
        else: base = alpha.point(lambda a: round(a*layer.opacity)) if layer.visible else Image.new("L",document.size)
        image.putalpha(alpha)
        yield layer,image


def export_ora(document,stream):
    root = ET.Element("image",w=str(document.width),h=str(document.height),name=document.name,version="0.0.3")
    stack = ET.SubElement(root,"stack")
    with zipfile.ZipFile(stream,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype","image/openraster",compress_type=zipfile.ZIP_STORED)
        for layer,image in reversed(list(effective_layers(document))):
            if layer.blend not in ORA_BLENDS: raise ValueError(f"ORA does not support {layer.blend}; use .apaint")
            src = f"data/{layer.id}.png"
            archive.writestr(src,png(image))
            ET.SubElement(stack,"layer",name=layer.name,src=src,x="0",y="0",opacity=str(layer.opacity),
                visibility="visible" if layer.visible else "hidden", **{"composite-op":ORA_BLENDS[layer.blend]})
        if document.background is not None:
            archive.writestr("data/background.png",png(Image.new("RGBA",document.size,rgba(document.background))))
            ET.SubElement(stack,"layer",name="Paper",src="data/background.png",x="0",y="0",opacity="1.0",visibility="visible",**{"composite-op":"svg:src-over"})
        archive.writestr("stack.xml",ET.tostring(root,encoding="utf-8",xml_declaration=True))
        archive.writestr("mergedimage.png",png(composite(document)))
        thumb = composite(document); thumb.thumbnail((256,256)); archive.writestr("Thumbnails/thumbnail.png",png(thumb))


def import_ora(path):
    check_path(path)
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist())>512 or sum(e.file_size for e in archive.infolist())>512*1024*1024:
            raise ValueError("ORA archive too large")
        raw = archive.read("stack.xml")
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw: raise ValueError("XML entities are not allowed")
        root = ET.fromstring(raw)
        width,height = dimensions(int(root.attrib["w"]),int(root.attrib["h"]))
        doc = Document(width,height,name=root.get("name","Imported ORA"),background=None)
        doc.layers=[]
        stack = root.find("stack")
        if stack is None or any(item.tag != "layer" for item in stack):
            raise ValueError("Nested ORA stacks are not yet supported")
        reverse_blends = {v:k for k,v in ORA_BLENDS.items()}
        for item in reversed(list(stack)):
            mode = reverse_blends.get(item.get("composite-op","svg:src-over"))
            if mode is None: raise ValueError("Unsupported ORA blend")
            with Image.open(BytesIO(archive.read(item.attrib["src"]))) as source:
                dimensions(*source.size)
                image=Image.new("RGBA",doc.size)
                image.alpha_composite(source.convert("RGBA"),(int(item.get("x",0)),int(item.get("y",0))))
            doc.layers.append(Layer(item.get("name","Layer"),image,opacity=finite(float(item.get("opacity",1)),0,1),
                                    blend=mode,visible=item.get("visibility","visible")=="visible"))
        if not 1<=len(doc.layers)<=128: raise ValueError("Expected 1..128 ORA layers")
        doc.active_layer=doc.layers[-1].id
        return doc


def export_psd(document, stream):
    """PSD v1, raw RGBA channels, real raster layers and Unicode layer names."""
    pack = lambda fmt,*v: struct.pack(">"+fmt,*v)
    def block(data): return pack("I",len(data))+data
    modes={"normal":b"norm","multiply":b"mul ","screen":b"scrn","overlay":b"over","darken":b"dark",
           "lighten":b"lite","add":b"lddg","subtract":b"fsub","difference":b"diff","soft_light":b"sLit"}
    layers=list(effective_layers(document))
    if document.background is not None:
        layers.insert(0,(Layer("Paper",Image.new("RGBA",document.size,rgba(document.background))),Image.new("RGBA",document.size,rgba(document.background))))
    records,channels=[],[]
    for layer,image in layers:
        channel_data=[b"\0\0"+image.getchannel(c).tobytes() for c in ("R","G","B","A")]
        rec=pack("iiiiH",0,0,document.height,document.width,4)
        rec+=b"".join(pack("hI",cid,len(data)) for cid,data in zip((0,1,2,-1),channel_data))
        name=layer.name.encode("ascii","replace")[:255]; pascal=bytes([len(name)])+name
        pascal+=b"\0"*((-len(pascal))%4)
        uname=layer.name.encode("utf-16-be"); unicode_name=pack("I",len(uname)//2)+uname
        tagged=b"8BIMluni"+block(unicode_name)+b"\0"*(len(unicode_name)%2)
        extra=pack("II",0,0)+pascal+tagged
        flags=0 if layer.visible else 2
        rec+=b"8BIM"+modes[layer.blend]+pack("BBBB",round(layer.opacity*255),0,flags,0)+block(extra)
        records.append(rec); channels.extend(channel_data)
    layer_info=pack("h",-len(layers))+b"".join(records)+b"".join(channels)
    layer_info+=b"\0"*(len(layer_info)%2)
    stream.write(b"8BPS"+pack("H",1)+b"\0"*6+pack("HIIHH",4,document.height,document.width,8,3))
    stream.write(pack("II",0,0))  # color mode data / image resources
    stream.write(block(block(layer_info)+pack("I",0)))
    stream.write(pack("H",0))
    merged=composite(document)
    # PSD's merged RGB preview is stored over white; transparency is a separate channel.
    white=Image.new("RGBA",document.size,"white"); white.alpha_composite(merged)
    for channel in ("R","G","B"): stream.write(white.getchannel(channel).tobytes())
    stream.write(merged.getchannel("A").tobytes())


def export_image(document,path,overwrite=True,transparent=False,scale=1):
    check_path(path)
    ext=Path(path).suffix.lower()
    formats={".png":"PNG",".jpg":"JPEG",".jpeg":"JPEG",".webp":"WEBP",".tif":"TIFF",".tiff":"TIFF"}
    if ext not in formats and ext not in {".ora",".psd"}: raise ValueError("Export supports PNG, JPEG, WebP, TIFF, ORA, PSD")
    if type(scale) is not int or not 1<=scale<=16: raise ValueError("scale is an integer 1..16")
    if scale!=1 and ext in {".ora",".psd"}: raise ValueError("scale applies to flat PNG/JPEG/WebP/TIFF exports")
    if document.width*document.height*scale*scale>128_000_000: raise ValueError("Scaled export exceeds 128 megapixels")
    def write(stream):
        if ext==".ora": export_ora(document,stream)
        elif ext==".psd": export_psd(document,stream)
        else:
            image=composite(document,transparent)
            if scale!=1:
                # Integer nearest-neighbour: each document pixel becomes a crisp scale x scale block.
                image=image.resize((image.width*scale,image.height*scale),Image.Resampling.NEAREST)
            if ext in {".jpg",".jpeg"}:
                background=Image.new("RGBA",image.size,"white"); background.alpha_composite(image); image=background.convert("RGB")
            dpi=document.dpi*scale
            image.save(stream,formats[ext],**({"lossless":True} if ext==".webp" else {}),dpi=(dpi,dpi))
    return atomic_write(path,write,overwrite)
