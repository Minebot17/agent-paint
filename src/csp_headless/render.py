"""CPU painting primitives; coordinates always refer to document pixels."""
from __future__ import annotations

from functools import lru_cache
import math
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageFont

from .model import BLENDS, finite, own_pixels


def rgba(color):
    if not isinstance(color, str):
        raise ValueError("Color must be a CSS color string (#RRGGBB or #RRGGBBAA)")
    try:
        return ImageColor.getcolor(color, "RGBA")
    except (ValueError, TypeError) as error:
        raise ValueError(f"Invalid color: {color!r}") from error


def blend(bottom, top, mode="normal", opacity=1.0, mask=None):
    if mode not in BLENDS:
        raise ValueError(f"Unsupported blend mode {mode}")
    finite(opacity, 0, 1)
    if mask is not None or opacity != 1:
        top = top.copy()
        alpha = top.getchannel("A")
        if mask is not None:
            alpha = ImageChops.multiply(alpha, mask)
        if opacity != 1:
            alpha = alpha.point(lambda a: round(a*opacity))
        top.putalpha(alpha)
    if mode == "normal":
        return Image.alpha_composite(bottom, top)
    b = np.asarray(bottom, dtype=np.float32)/255
    s = np.asarray(top, dtype=np.float32)/255
    cb, cs, ba, sa = b[..., :3], s[..., :3], b[..., 3:], s[..., 3:]
    if mode == "multiply": mixed = cb*cs
    elif mode == "screen": mixed = 1-(1-cb)*(1-cs)
    elif mode == "overlay": mixed = np.where(cb <= .5, 2*cb*cs, 1-2*(1-cb)*(1-cs))
    elif mode == "darken": mixed = np.minimum(cb, cs)
    elif mode == "lighten": mixed = np.maximum(cb, cs)
    elif mode == "add": mixed = np.minimum(1, cb+cs)
    elif mode == "subtract": mixed = np.maximum(0, cb-cs)
    elif mode == "difference": mixed = np.abs(cb-cs)
    else:
        curve = np.where(cb <= .25, ((16*cb-12)*cb+4)*cb, np.sqrt(cb))
        mixed = np.where(cs <= .5, cb-(1-2*cs)*cb*(1-cb), cb+(2*cs-1)*(curve-cb))
    alpha = sa + ba*(1-sa)
    rgb = ((1-sa)*ba*cb + (1-ba)*sa*cs + ba*sa*mixed)/np.maximum(alpha, 1e-8)
    return Image.fromarray(np.uint8(np.clip(np.concatenate([rgb, alpha], axis=2)*255+.5, 0, 255)))


def composite(document, transparent=False, through=None):
    color = (0, 0, 0, 0) if transparent or document.background is None else rgba(document.background)
    out = Image.new("RGBA", document.size, color)
    base_alpha = Image.new("L", document.size)
    for layer in document.layers:
        if not layer.visible:
            if not layer.clipping:
                base_alpha = Image.new("L", document.size)
            continue
        mask = layer.mask
        effective = layer.image.getchannel("A")
        if mask is not None:
            effective = ImageChops.multiply(effective, mask)
        if layer.clipping:
            mask = ImageChops.multiply(mask, base_alpha) if mask is not None else base_alpha
        else:
            base_alpha = effective.point(lambda a: round(a*layer.opacity))
        out = blend(out, layer.image, layer.blend, layer.opacity, mask)
        if through and layer.id == through:
            break
    return out


def apply_paint(layer, source, box, selection=None, erase=False, mode="normal"):
    """Composite only the touched rectangle; layer masks affect display, not stored pixels."""
    dest = layer.image.crop(box)
    source = source.copy()
    alpha = source.getchannel("A")
    if selection is not None:
        alpha = ImageChops.multiply(alpha, selection.crop(box))
    source.putalpha(alpha)
    if erase:
        if layer.alpha_lock:
            return
        dest.putalpha(ImageChops.multiply(dest.getchannel("A"), ImageChops.invert(alpha)))
        result = dest
    elif layer.alpha_lock:
        result = blend(dest, source, mode)
        result.putalpha(dest.getchannel("A"))
    else:
        result = blend(dest, source, mode)
    own_pixels(layer)
    layer.image.paste(result, box[:2])


BRUSH_DEFAULTS = {"kind": "round", "size": 12.0, "opacity": 1.0, "hardness": 1.0,
                  "flow": 1.0, "spacing": .12, "pressure_size": True, "pressure_opacity": False,
                  "min_size": .05, "taper_start": 0.0, "taper_end": 0.0,
                  "texture": 0.0, "seed": 1, "blend": "normal", "stabilization": 0.0,
                  "mixing": .5, "texture_space": "stroke", "edge_darkening": 0.0}
BRUSH_KINDS = {"round", "pencil", "airbrush", "watercolor", "marker", "eraser", "smudge"}
TEXTURE_SPACES = {"stroke", "paper"}


def validate_brush(settings):
    if not isinstance(settings, dict):
        raise ValueError("Brush settings must be an object")
    unknown = set(settings)-set(BRUSH_DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown brush settings: {sorted(unknown)}; valid keys: {sorted(BRUSH_DEFAULTS)}")
    brush = {**BRUSH_DEFAULTS, **settings}
    if brush["kind"] not in BRUSH_KINDS:
        raise ValueError(f"Unknown brush kind {brush['kind']!r}; use one of {sorted(BRUSH_KINDS)}")
    if brush["blend"] not in BLENDS:
        raise ValueError(f"Unknown brush blend {brush['blend']!r}; use one of {sorted(BLENDS)}")
    if brush["texture_space"] not in TEXTURE_SPACES:
        raise ValueError("texture_space must be 'stroke' (grain follows each stroke) or 'paper' (grain fixed to the document)")
    finite(brush["size"], .1, 4096, "brush size")
    finite(brush["spacing"], .01, 2, "brush spacing")
    for key in ("opacity", "hardness", "flow", "min_size", "taper_start", "taper_end", "texture", "stabilization", "mixing",
                "edge_darkening"):
        finite(brush[key], 0, 1, f"brush {key}")
    for key in ("pressure_size", "pressure_opacity"):
        if type(brush[key]) is not bool:
            raise ValueError(f"{key} requires boolean")
    if type(brush["seed"]) is not int or brush["seed"] < 0:
        raise ValueError("seed must be a nonnegative integer")
    return brush


def validate_points(points, minimum=1):
    if not isinstance(points, (list, tuple)) or not minimum <= len(points) <= 100000:
        raise ValueError(f"Expected {minimum}..100000 points as [[x,y],...] or [[x,y,pressure],...]")
    result = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) not in (2, 3):
            raise ValueError(f"Points are pairs [x,y] or triples [x,y,pressure], got {point!r}; "
                             "a flat list [x0,y0,x1,y1,...] is not accepted")
        x, y = finite(point[0], -1000000, 1000000, "point x"), finite(point[1], -1000000, 1000000, "point y")
        p = finite(point[2], 0, 1, "point pressure") if len(point) == 3 else 1.0
        result.append((x, y, p))
    return result


def validate_dash(dash, offset=0):
    """Dash pattern [on, off, ...] in document pixels; on=0 draws round dots of the brush size."""
    if dash is None:
        return None
    if not isinstance(dash, (list, tuple)) or not 1 <= len(dash) <= 16:
        raise ValueError("dash is a list of 1..16 lengths in pixels, e.g. [12,6] (dash, gap) or [0,10] (dots every 10 px)")
    values = [float(finite(v, 0, 100000, "dash length")) for v in dash]
    if len(values) % 2:
        values = values*2
    if sum(values[1::2]) <= 0 or sum(values) < .5:
        raise ValueError("dash needs a positive gap, e.g. [12,6]; a pattern without gaps is a solid stroke")
    finite(offset, -1000000, 1000000, "dash_offset")
    return values


def _dedupe(points):
    out = [points[0]]
    for p in points[1:]:
        if math.hypot(p[0]-out[-1][0], p[1]-out[-1][1]) > 1e-6:
            out.append(p)
    return out


def catmull_rom(points, closed=False, step=2.0, limit=150000):
    """Centripetal Catmull-Rom spline through the given (x,y,pressure) points.

    The curve passes through every authored point, never overshoots into loops/cusps, and also
    interpolates pressure. A handful of control points therefore draws a smooth curve, instead of
    a polyline with visible corners."""
    pts = _dedupe([tuple(map(float, p)) for p in points])
    if closed and len(pts) > 2 and math.hypot(pts[0][0]-pts[-1][0], pts[0][1]-pts[-1][1]) < 1e-6:
        pts = pts[:-1]
    n = len(pts)
    if n < 3:
        return pts+[pts[0]] if closed and n > 1 else pts
    arr = np.array(pts, dtype=np.float64)
    out = []
    segments = n if closed else n-1
    for i in range(segments):
        p1, p2 = arr[i], arr[(i+1) % n]
        p0 = arr[i-1] if closed or i > 0 else 2*p1-p2
        p3 = arr[(i+2) % n] if closed or i+2 < n else 2*p2-p1
        def knot(t, a, b): return t+max(1e-4, math.hypot(b[0]-a[0], b[1]-a[1]))**.5
        t0 = 0.0; t1 = knot(t0, p0, p1); t2 = knot(t1, p1, p2); t3 = knot(t2, p2, p3)
        count = max(1, math.ceil(math.hypot(p2[0]-p1[0], p2[1]-p1[1])/step))
        t = (t1+(t2-t1)*np.arange(count)/count)[:, None]
        a1 = (t1-t)/(t1-t0)*p0+(t-t0)/(t1-t0)*p1
        a2 = (t2-t)/(t2-t1)*p1+(t-t1)/(t2-t1)*p2
        a3 = (t3-t)/(t3-t2)*p2+(t-t2)/(t3-t2)*p3
        b1 = (t2-t)/(t2-t0)*a1+(t-t0)/(t2-t0)*a2
        b2 = (t3-t)/(t3-t1)*a2+(t-t1)/(t3-t1)*a3
        out.append((t2-t)/(t2-t1)*b1+(t-t1)/(t2-t1)*b2)
        if sum(len(o) for o in out) > limit:
            raise ValueError("Smoothed stroke is too long: split it into several strokes")
    out.append((arr[0] if closed else arr[-1])[None, :])
    curve = np.concatenate(out)
    curve[:, 2] = np.clip(curve[:, 2], 0, 1)
    return [tuple(p) for p in curve.tolist()]


def resample(points, step=1.0, limit=150000):
    """Polyline resampled at a uniform arc-length step (end point kept)."""
    pts = _dedupe(points)
    if len(pts) < 2:
        return pts
    arr = np.array(pts, dtype=np.float64)
    seg = np.hypot(*np.diff(arr[:, :2], axis=0).T)
    lengths = np.r_[0, np.cumsum(seg)]
    total = lengths[-1]
    count = max(2, math.ceil(total/step)+1)
    if count > limit:
        count = limit
    s = np.linspace(0, total, count)
    return np.stack([np.interp(s, lengths, arr[:, k]) for k in range(3)], axis=1).tolist()


def stabilize(points, amount):
    """Distance-based stabilizer: a moving average over amount*50 px of arc length.

    Endpoints stay fixed and the window is measured in pixels, so sparse authored points (zigzags,
    angular hatching) keep their shape; only jitter shorter than the window is smoothed."""
    if amount <= 0 or len(points) < 3:
        return points
    arr = np.array(resample(points, 1.0), dtype=np.float64)
    n = len(arr)
    k = max(1, round(amount*25))
    sums = np.vstack([np.zeros((1, 3)), np.cumsum(arr, axis=0)])
    index = np.arange(n)
    half = np.minimum(k, np.minimum(index, n-1-index))
    low, high = index-half, index+half+1
    return ((sums[high]-sums[low])/(high-low)[:, None]).tolist()


def dash_intervals(total, dash, offset=0.0, limit=50000):
    """Arc-length intervals [a,b] that are 'on' for a dash pattern along a stroke of given length."""
    period = sum(dash)
    position = -(offset % period)
    intervals, index = [], 0
    while position <= total:
        on, off = dash[index], dash[index+1]
        a, b = position, position+on
        # Zero-length dashes are dots; a dash merely clipped to nothing at an end is dropped.
        if b >= 0 and (on == 0 or min(b, total) > max(a, 0.0)):
            intervals.append((max(a, 0.0), min(b, total)))
        position += on+off
        index = (index+2) % len(dash)
        if len(intervals) > limit:
            raise ValueError("Dash pattern produces too many dashes for this stroke; use longer dashes/gaps")
    return intervals


PAPER_TILE = 512


@lru_cache(maxsize=16)
def _paper_tile(seed, soft):
    """Seamless noise tile; with texture_space='paper' every stroke samples the same document-fixed grain."""
    rng = np.random.default_rng(seed)
    tile = rng.random((PAPER_TILE, PAPER_TILE), dtype=np.float32)
    if soft:
        # Blur a 3x3 repeat and keep the centre so the tile remains seamless.
        big = Image.fromarray(np.uint8(np.tile(tile, (3, 3))*255)).filter(ImageFilter.GaussianBlur(2))
        tile = np.asarray(big, dtype=np.float32)[PAPER_TILE:2*PAPER_TILE, PAPER_TILE:2*PAPER_TILE]/255
    tile.setflags(write=False)
    return tile


def paper_noise(seed, box, soft=False):
    l, t, r, b = box
    tile = _paper_tile(seed, soft)
    return tile[np.ix_(np.arange(t, b) % PAPER_TILE, np.arange(l, r) % PAPER_TILE)]


def darken_edges(mask, amount, size, softness=0.0):
    """Pigment pooling: thin the interior of a mark and concentrate density along its inside edge.

    mask is the hard mark. softness (the brush's blur radius) softens the body fully but the rim
    only a little, like a wash whose drying edge stays more defined than its bleed."""
    m = np.asarray(mask, dtype=np.float32)/255
    radius = max(1.0, size*.08)
    blurred = np.asarray(mask.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)/255
    rim = np.clip((m-blurred)*2.5, 0, 1)*m
    if softness:
        def soften(values, r):
            return np.asarray(Image.fromarray(np.uint8(values*255+.5)).filter(ImageFilter.GaussianBlur(r)), dtype=np.float32)/255
        m = soften(m, softness)
        rim = soften(rim, softness*.35)
    result = np.clip(m*(1-.45*amount)+rim*amount, 0, 1)
    return Image.fromarray(np.uint8(result*255+.5))


def stroke_mask(size, points, brush, closed=False, smooth=False, dash=None, dash_offset=0.0):
    points = validate_points(points)
    if smooth and len(points) > 2:
        # Catmull-Rom closes the curve itself; sample finer than the brush so wide strokes stay round.
        points = catmull_rom(points, closed, step=max(.5, min(2.0, brush["size"]*.25)))
    elif closed and len(points) > 1:
        points.append(points[0])
    if brush["stabilization"] and len(points) > 2:
        points = stabilize(points, brush["stabilization"])
    dash = validate_dash(dash, dash_offset)
    # Continuous coverage avoids dark knots at overlapping stamp locations.
    samples = [points[0]]
    step = max(.5, min(8, brush["size"]*brush["spacing"]))
    for a, b in zip(points, points[1:]):
        count = max(1, math.ceil(math.hypot(b[0]-a[0], b[1]-a[1])/step))
        if len(samples)+count > 150000:
            raise ValueError("Stroke too long: split or increase spacing")
        samples.extend(tuple(a[j]+(b[j]-a[j])*i/count for j in range(3)) for i in range(1, count+1))
    lengths = np.r_[0, np.cumsum([math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(samples, samples[1:])])]
    total = max(1e-8, lengths[-1])
    radii = []
    for (_, _, p), length in zip(samples, lengths):
        response = max(brush["min_size"], p) if brush["pressure_size"] else 1
        if len(samples) > 1:
            if brush["taper_start"]:
                response *= max(.01, min(1, length/(total*brush["taper_start"])))
            if brush["taper_end"]:
                response *= max(.01, min(1, (total-length)/(total*brush["taper_end"])))
        radii.append(max(.05, brush["size"]*.5*response))
    hardness = min(brush["hardness"], .05) if brush["kind"] == "airbrush" else brush["hardness"]
    blur_radius = brush["size"]*(1-hardness)*.22
    pad = brush["size"]*.5+3*blur_radius+3
    l = max(0, math.floor(min(p[0] for p in samples)-pad))
    t = max(0, math.floor(min(p[1] for p in samples)-pad))
    r = min(size[0], math.ceil(max(p[0] for p in samples)+pad))
    b = min(size[1], math.ceil(max(p[1] for p in samples)+pad))
    if l >= r or t >= b:
        return None, None, samples
    box = (l,t,r,b)
    w, h = r-l, b-t
    # Bound temporary supersampling memory for broad strokes on large canvases.
    aa = 3 if w*h < 1_000_000 else 2 if w*h < 6_000_000 else 1
    mask = Image.new("L", (w*aa,h*aa))
    draw = ImageDraw.Draw(mask)
    local = [((p[0]-l)*aa, (p[1]-t)*aa, radius*aa, p[2]) for p,radius in zip(samples,radii)]
    runs = [local]
    if dash and len(local) > 1:
        def at(s):
            j = min(max(int(np.searchsorted(lengths, s, side="right"))-1, 0), len(local)-2)
            span = lengths[j+1]-lengths[j]
            u = 0.0 if span <= 0 else (s-lengths[j])/span
            a, z = local[j], local[j+1]
            return tuple(a[k]+(z[k]-a[k])*u for k in range(4))
        runs = []
        for a, b in dash_intervals(float(lengths[-1]), dash, dash_offset):
            if b <= a:
                runs.append([at(a)])  # zero-length dash: a round dot of the brush width
                continue
            inner = local[int(np.searchsorted(lengths, a, side="right")):int(np.searchsorted(lengths, b, side="left"))]
            runs.append([at(a), *inner, at(b)])
    for run in runs:
        for a, z in zip(run, run[1:]):
            dx,dy = z[0]-a[0],z[1]-a[1]
            length = math.hypot(dx,dy)
            if length:
                nx,ny = -dy/length, dx/length
                color = round(255*((a[3]+z[3])*.5 if brush["pressure_opacity"] else 1))
                draw.polygon([(a[0]+nx*a[2],a[1]+ny*a[2]),(z[0]+nx*z[2],z[1]+ny*z[2]),
                              (z[0]-nx*z[2],z[1]-ny*z[2]),(a[0]-nx*a[2],a[1]-ny*a[2])], fill=color)
        for x,y,radius,p in run:
            draw.ellipse((x-radius,y-radius,x+radius,y+radius), fill=round(255*(p if brush["pressure_opacity"] else 1)))
    if aa != 1:
        # Area (box) downsampling is exact coverage: Lanczos left a ~6% halo beside every line and
        # kept the core of thin lines below full opacity.
        mask = mask.resize((w,h), Image.Resampling.BOX)
    if brush["edge_darkening"]:
        # Pool pigment on the hard mark; the rim is softened less than the body, so the darker
        # edge also survives on soft brushes.
        mask = darken_edges(mask, brush["edge_darkening"], brush["size"], blur_radius if hardness < 1 else 0)
    elif hardness < 1:
        mask = mask.filter(ImageFilter.GaussianBlur(blur_radius))
    texture = max(brush["texture"], .55 if brush["kind"] == "pencil" else .18 if brush["kind"] == "watercolor" else 0)
    if texture:
        soft = brush["kind"] == "watercolor"
        if brush["texture_space"] == "paper":
            noise = paper_noise(brush["seed"], box, soft)
        else:
            rng = np.random.default_rng(brush["seed"])
            noise = rng.random((h,w), dtype=np.float32)
            if soft:
                noise_image = Image.fromarray(np.uint8(noise*255)).filter(ImageFilter.GaussianBlur(2))
                noise = np.asarray(noise_image,dtype=np.float32)/255
        mask = Image.fromarray(np.uint8(np.asarray(mask)*(1-texture+texture*noise)))
    opacity = brush["opacity"]*brush["flow"]
    if opacity != 1:
        mask = mask.point(lambda a: round(a*opacity))
    return box, mask, samples


def paint_stroke(layer, selection, points, brush, color, closed=False, stop=None, smooth=False, dash=None, dash_offset=0.0):
    box, mask, samples = stroke_mask(layer.image.size, points, brush, closed, smooth, dash, dash_offset)
    if box is None:
        return None
    if stop is not None and stop.is_set():
        raise InterruptedError("Painting aborted; operation rolled back")
    if brush["kind"] == "smudge":
        smudge_stroke(layer,selection,samples,brush,stop)
        return list(box)
    colour = rgba(color)
    mask = ImageChops.multiply(mask, Image.new("L",mask.size,colour[3]))
    if brush["kind"] == "watercolor" and brush["mixing"]:
        # Pick up underlying pigment in proportion to its coverage: transparent pixels (stored as
        # black) must not dirty the wash, which a straight RGB blend used to do.
        old = np.asarray(layer.image.crop(box), dtype=np.float32)
        weight = brush["mixing"]*.25*old[..., 3:]/255
        rgb = np.array(colour[:3], dtype=np.float32)*(1-weight)+old[..., :3]*weight
        source = Image.fromarray(np.uint8(np.clip(rgb+.5, 0, 255)), "RGB").convert("RGBA")
    else:
        source = Image.new("RGBA", mask.size, colour)
    source.putalpha(mask)
    apply_paint(layer,source,box,selection,brush["kind"] == "eraser",brush["blend"])
    return list(box)


def _premultiplied_crop(image, box):
    """Float RGBA crop with premultiplied colour; area outside the canvas is transparent."""
    arr = np.asarray(image.crop(box), dtype=np.float32)/255
    arr[..., :3] *= arr[..., 3:]
    return arr


def smudge_stroke(layer,selection,samples,brush,stop=None):
    """Drag pigment along the path: carried paint and the surface mix in premultiplied space.

    Premultiplied mixing smears colour and coverage together. Mixing straight RGBA used to pull the
    black stored in transparent pixels into the carried colour and left dark streaks."""
    carry=None
    width,height=layer.image.size
    diameter=max(2,math.ceil(brush["size"]))
    softness=max(.05,1-brush["hardness"])
    strength=brush["mixing"]*brush["opacity"]*brush["flow"]
    tips={}
    def tip(size):
        if size not in tips:
            yy,xx=np.mgrid[:size,:size]
            distance=np.sqrt((xx-(size-1)/2)**2+(yy-(size-1)/2)**2)/(size/2)
            tips[size]=np.clip((1-distance)/softness,0,1).astype(np.float32)
        return tips[size]
    for x,y,pressure in samples:
        if stop is not None and stop.is_set(): raise InterruptedError("Smudge aborted")
        response=max(brush["min_size"],pressure) if brush["pressure_size"] else 1
        size=max(2,round(diameter*response))
        l,t=round(x-size/2),round(y-size/2)
        box=(l,t,l+size,t+size)
        current=_premultiplied_crop(layer.image,box)
        if carry is None:
            carry=current
            continue
        if carry.shape[:2]!=current.shape[:2]:
            # Nearest resampling of premultiplied paint when pressure changes the tip size.
            rows=np.minimum((np.arange(size)*carry.shape[0]/size).astype(int),carry.shape[0]-1)
            cols=np.minimum((np.arange(size)*carry.shape[1]/size).astype(int),carry.shape[1]-1)
            carry=carry[rows][:,cols]
        weight=strength*(pressure if brush["pressure_opacity"] else 1)
        m=tip(size)*weight
        if selection is not None:
            m=m*np.asarray(selection.crop(box),dtype=np.float32)/255
        mixed=current*(1-m[...,None])+carry*m[...,None]
        # Paint drags fully into empty areas, but dragging emptiness into paint only thins it a
        # little (a finger lifts some pigment; it does not erase).
        coverage=current[...,3:]; smeared=mixed[...,3:]
        thinned=np.where(smeared<coverage,coverage-(coverage-smeared)*.35,smeared)
        mixed=np.concatenate([mixed[...,:3]/np.maximum(smeared,1e-6)*thinned,thinned],axis=2)
        if layer.alpha_lock:
            # Keep coverage: rescale the mixed colour to the original alpha.
            alpha=current[...,3:]
            colour=mixed[...,:3]/np.maximum(mixed[...,3:],1e-6)
            mixed=np.concatenate([colour*alpha,alpha],axis=2)
        clip=(max(0,l),max(0,t),min(width,l+size),min(height,t+size))
        if clip[0]<clip[2] and clip[1]<clip[3]:
            region=mixed[clip[1]-t:clip[3]-t,clip[0]-l:clip[2]-l]
            alpha=region[...,3:]
            straight=np.where(alpha>1e-6,region[...,:3]/np.maximum(alpha,1e-6),0)
            out=np.concatenate([straight,alpha],axis=2)
            own_pixels(layer)
            layer.image.paste(Image.fromarray(np.uint8(np.clip(out*255+.5,0,255)),"RGBA"),clip[:2])
        carry=carry*.75+current*.25


@lru_cache(maxsize=1)
def fonts():
    paths = [Path(__file__).parent/"assets",Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts", Path("/usr/share/fonts"),
             Path("/usr/local/share/fonts"), Path.home()/".local/share/fonts",Path("/System/Library/Fonts")]
    result = []
    for root in paths:
        if root.is_dir():
            result.extend(str(p) for p in root.rglob("*") if p.suffix.lower() in {".ttf", ".otf", ".ttc"})
    return sorted(set(result))


def font_for(name, size):
    if not name:
        bundled=Path(__file__).parent/"assets"/"DejaVuSans.ttf"
        name=str(bundled) if bundled.is_file() else "DejaVuSans.ttf"
    elif not Path(name).is_file():
        matches = [p for p in fonts() if Path(p).stem.casefold() == name.casefold()]
        if not matches:
            raise ValueError(f"Font not found: {name}; use paint_fonts")
        name = matches[0]
    return ImageFont.truetype(name, max(1, round(size)))


RESAMPLING = {"nearest": Image.Resampling.NEAREST, "bilinear": Image.Resampling.BILINEAR,
              "bicubic": Image.Resampling.BICUBIC}


def transformed(image, matrix, size=None, resample="bicubic"):
    if len(matrix) != 6:
        raise ValueError("Affine matrix is [a,b,c,d,e,f]: x'=ax+by+c, y'=dx+ey+f")
    for v in matrix: finite(v, -1e7, 1e7)
    if resample not in RESAMPLING:
        raise ValueError("resample is nearest (pixel art, hard edges), bilinear or bicubic")
    a,b,c,d,e,f = matrix
    det = a*e-b*d
    if abs(det) < 1e-8:
        raise ValueError("Singular transform")
    inverse = (e/det,-b/det,(b*f-e*c)/det,-d/det,a/det,(d*c-a*f)/det)
    if resample == "nearest":
        return image.transform(size or image.size, Image.Transform.AFFINE, inverse, Image.Resampling.NEAREST)
    # Premultiplied resampling: transparent (black) pixels must not darken the moved edge.
    mode = "RGBa" if image.mode == "RGBA" else image.mode
    moved = image.convert(mode).transform(size or image.size, Image.Transform.AFFINE, inverse, RESAMPLING[resample])
    return moved.convert(image.mode)


TEXT_STYLE_KEYS = {"font","font_size","color","outline_width","outline_color","line_spacing","line_height","letter_spacing",
                   "align","scale_x","scale_y","rotation","anchor"}
# Horizontal l/m/r + vertical t (top) / m (middle) / b (bottom of ink) / s (baseline of the first line).
TEXT_ANCHORS = {h+v for h in "lmr" for v in "tmbs"}
TEXT_ALIGNS = {"left", "center", "right", "justify"}


def _glyph_signature(font, ch):
    l, t, r, b = font.getbbox(ch)
    image = Image.new("L", (max(1, r-l), max(1, b-t)))
    ImageDraw.Draw(image).text((-l, -t), ch, font=font, fill=255)
    return r-l, b-t, image.tobytes()


def missing_glyphs(font, text, limit=512):
    """Characters the font renders as its .notdef box (tofu). Pillow has no font fallback."""
    import unicodedata
    chars = sorted({c for c in text if not c.isspace() and unicodedata.category(c)[0] not in "CZ"})[:limit]
    if not chars:
        return []
    notdef = {_glyph_signature(font, c) for c in ("￿", "\U0010fffd")}
    return [c for c in chars if _glyph_signature(font, c) in notdef]


def _tracked_text(text, font, fill, spacing, align, tracking, stroke_width, stroke_fill):
    """Letter-spaced text: glyph positions keep the font's kerning (prefix advances) plus tracking.

    Returns (image, first-baseline y) with 2 px padding around the ink, like the untracked path."""
    lines = text.split("\n") if text else [" "]
    ascent, descent = font.getmetrics()
    step = font.getbbox("A", stroke_width=stroke_width)[3]+stroke_width+spacing
    layouts = []
    for line in lines:
        if len(line) > 2000:  # long lines: per-glyph advances (no kerning) keep this linear
            xs, x = [], 0.0
            for ch in line:
                xs.append(x); x += font.getlength(ch)+tracking
            layouts.append((xs, max(0.0, x-tracking)))
        else:
            xs = [font.getlength(line[:i])+i*tracking for i in range(len(line))]
            layouts.append((xs, font.getlength(line)+tracking*(len(line)-1) if line else 0.0))
    widest = max(width for _, width in layouts)
    margin = int(font.size+2*stroke_width+abs(tracking))+4
    width = int(math.ceil(widest))+2*margin
    height = int(math.ceil(step*(len(lines)-1)+ascent+descent))+2*margin+2*stroke_width
    if width*height > 64_000_000:
        raise ValueError("Text raster is too large")
    canvas = Image.new("RGBA", (max(1, width), max(1, height)))
    draw = ImageDraw.Draw(canvas)
    for row, (line, (xs, line_width)) in enumerate(zip(lines, layouts)):
        offset = {"center": (widest-line_width)/2, "right": widest-line_width}.get(align, 0)
        top = margin+row*step
        for ch, x in zip(line, xs):
            if not ch.isspace():
                draw.text((margin+offset+x, top), ch, font=font, fill=fill, anchor="la",
                          stroke_width=stroke_width, stroke_fill=stroke_fill)
    box = canvas.getchannel("A").getbbox() or (margin, margin, margin+1, margin+1)
    left, top = max(0, box[0]-2), max(0, box[1]-2)
    local = canvas.crop((left, top, min(canvas.width, box[2]+2), min(canvas.height, box[3]+2)))
    return local, margin+ascent-top


def text_patch(obj):
    """Render a text object locally; returns (left, top, image) in document pixels."""
    text = obj["text"]
    style = obj["style"]
    if not isinstance(text,str) or len(text)>16000: raise ValueError("Text must be a string of at most 16000 characters")
    if set(style)-TEXT_STYLE_KEYS: raise ValueError(f"Unknown text style property; valid: {sorted(TEXT_STYLE_KEYS)}")
    finite(obj["x"],-100000,100000,"text x"); finite(obj["y"],-100000,100000,"text y")
    finite(style.get("font_size",32),1,4096,"font_size")
    finite(style.get("line_spacing",4),0,4096,"line_spacing")
    align = style.get("align","left")
    if align not in TEXT_ALIGNS: raise ValueError(f"Invalid text alignment; use one of {sorted(TEXT_ALIGNS)}")
    anchor = style.get("anchor","lt")
    if anchor not in TEXT_ANCHORS:
        raise ValueError("Text anchor is two letters: l/m/r (horizontal) then t/m/b/s (vertical; s = first baseline), "
                         "e.g. 'mm' centres on x,y and 'ls' puts the baseline on y")
    font = font_for(style.get("font"),style.get("font_size",32))
    color = rgba(style.get("color","#000000"))
    stroke_width = round(finite(style.get("outline_width",0),0,100,"outline_width"))
    stroke_fill = style.get("outline_color","#FFFFFF")
    if "line_height" in style:
        # Absolute baseline-to-baseline distance; line_spacing is the extra gap Pillow adds.
        line_height = finite(style["line_height"],1,8192,"line_height")
        spacing = line_height-(font.getbbox("A",stroke_width=stroke_width)[3]+stroke_width)
    else:
        spacing = round(style.get("line_spacing",4))
    tracking = finite(style.get("letter_spacing",0),-1000,1000,"letter_spacing")
    if tracking:
        local, baseline = _tracked_text(text, font, color, spacing, align, tracking, stroke_width, stroke_fill)
    else:
        # Draw from top-left, accounting for glyph bearings and ascenders.
        probe = ImageDraw.Draw(Image.new("L",(1,1)))
        bbox = probe.multiline_textbbox((0,0),text or " ",font=font,spacing=spacing,stroke_width=stroke_width,align=align)
        w,h = max(1,math.ceil(bbox[2]-bbox[0])+4),max(1,math.ceil(bbox[3]-bbox[1])+4)
        if w*h > 32_000_000:
            raise ValueError("Text raster is too large")
        local = Image.new("RGBA",(w,h))
        ImageDraw.Draw(local).multiline_text((2-bbox[0],2-bbox[1]),text,font=font,fill=color,spacing=spacing,
            align=align,stroke_width=stroke_width,stroke_fill=stroke_fill)
        baseline = 2-bbox[1]+font.getmetrics()[0]
    sx,sy = style.get("scale_x",1),style.get("scale_y",1)
    finite(sx,.01,100,"scale_x"); finite(sy,.01,100,"scale_y")
    dims = (max(1,round(local.width*sx)),max(1,round(local.height*sy)))
    if dims[0]*dims[1] > 32_000_000: raise ValueError("Scaled text is too large")
    baseline *= dims[1]/local.height
    local = local.resize(dims,Image.Resampling.LANCZOS)
    angle = finite(style.get("rotation",0),-36000,36000,"rotation")
    fx = "lmr".index(anchor[0])*.5
    if anchor[1] == "s":
        # Baseline anchor: map the unrotated anchor point through the rotation.
        ax, ay = local.width*fx, baseline
        if angle:
            w, h = local.size
            local = local.rotate(-angle,Image.Resampling.BICUBIC,expand=True)
            rad = math.radians(angle); dx, dy = ax-w/2, ay-h/2
            ax = math.cos(rad)*dx-math.sin(rad)*dy+local.width/2
            ay = math.sin(rad)*dx+math.cos(rad)*dy+local.height/2
        return round(obj["x"]-ax), round(obj["y"]-ay), local
    if angle:
        local = local.rotate(-angle,Image.Resampling.BICUBIC,expand=True)
    fy = "tmb".index(anchor[1])*.5
    return round(obj["x"]-local.width*fx), round(obj["y"]-local.height*fy), local


def text_image(size, obj):
    left, top, local = text_patch(obj)
    out = Image.new("RGBA",size)
    # alpha_composite rejects negative destinations, so crop text that starts left of/above the canvas.
    sx, sy = max(0,-left), max(0,-top)
    if sx < local.width and sy < local.height:
        out.alpha_composite(local,(left+sx,top+sy),(sx,sy))
    return out


def text_bounds(obj):
    """Tight ink bounds [left,top,right,bottom] in document pixels (may extend past the canvas)."""
    left, top, local = text_patch(obj)
    box = local.getchannel("A").getbbox()
    return None if box is None else [left+box[0], top+box[1], left+box[2], top+box[3]]


def hard_polygon_mask(width, height, points):
    """Exact 0/255 polygon coverage with the pixel-centre (top-left) rule, even-odd filling.

    A pixel belongs to the polygon when its centre (x+0.5, y+0.5) is inside. A polygon spanning
    x0..x1 therefore covers pixels x0..x1-1 exactly like hard rectangles [x0,x1): adjacent hard
    polygons sharing an edge neither overlap nor leave gaps, and a zero-area polygon paints nothing."""
    mask = np.zeros((height, width), dtype=np.uint8)
    pts = np.asarray([(p[0], p[1]) for p in points], dtype=np.float64)
    x0, y0 = pts[:, 0], pts[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    keep = y0 != y1
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    if not len(x0):
        return Image.fromarray(mask)
    low, high = np.minimum(y0, y1), np.maximum(y0, y1)
    slope = (x1-x0)/(y1-y0)
    for row in range(max(0, math.floor(low.min())), min(height, math.ceil(high.max()))):
        centre = row+.5
        active = (low <= centre) & (centre < high)
        if not active.any():
            continue
        xs = np.sort(x0[active]+(centre-y0[active])*slope[active])
        for a, b in zip(xs[0::2], xs[1::2]):
            start, end = max(0, math.ceil(a-.5)), min(width, math.ceil(b-.5))
            if start < end:
                mask[row, start:end] = 255
    return Image.fromarray(mask)


def smooth_outline(points, closed, step=1.5):
    """Catmull-Rom outline through authored vertices, as (x,y) pairs."""
    return [(x, y) for x, y, _ in catmull_rom([(p[0], p[1], 1.0) for p in points], closed, step)]


def shape_patch(size, obj):
    """Render a shape only inside its padded bounding box.

    Returns (box, image) with box=(left,top,right,bottom) clipped to the canvas, or (None, None)
    when the shape lies entirely outside. Rendering cost scales with the shape, not the canvas.
    antialias=False draws hard pixels (box shapes then cover the half-open pixel range [x0,x1)).
    smooth=True turns polygon/line vertices into a Catmull-Rom curve through them; bleed widens the
    fill by that many pixels (hides hairline seams between adjacent antialiased facets).
    """
    validate_points(obj["points"],3 if obj["shape"]=="polygon" else 2)
    finite(obj.get("width",1),.1,4096,"width"); finite(obj.get("radius",12),0,4096,"radius")
    bleed = finite(obj.get("bleed",0),0,64,"bleed")
    kind = obj["shape"]
    if kind not in {"rectangle","rounded_rectangle","ellipse","polygon","line","bezier"}: raise ValueError("Unknown shape")
    if kind in {"rectangle","rounded_rectangle","ellipse"}:
        pts=obj["points"]
        if len(pts)!=2 or pts[0][0]>=pts[1][0] or pts[0][1]>=pts[1][1]: raise ValueError("Invalid bounding rectangle: points are [[left,top],[right,bottom]]")
    if kind == "bezier" and len(obj["points"]) != 4: raise ValueError("Cubic bezier requires four control points")
    antialias = obj.get("antialias", True)
    if type(antialias) is not bool: raise ValueError("antialias requires boolean")
    if type(obj.get("smooth", False)) is not bool: raise ValueError("smooth requires boolean")
    fill = rgba(obj["fill"]) if obj.get("fill") else None
    outline = rgba(obj["outline"]) if obj.get("outline") else None
    source = obj["points"]
    if obj.get("smooth") and kind in {"polygon","line"} and len(source) > 2:
        source = smooth_outline(source, kind == "polygon")
    # Bezier control points bound the curve, so one padded box covers every shape kind.
    pad = obj.get("width",1)+bleed+4
    xs = [p[0] for p in source]; ys = [p[1] for p in source]
    l = max(0, math.floor(min(xs)-pad)); t = max(0, math.floor(min(ys)-pad))
    r = min(size[0], math.ceil(max(xs)+pad)); b = min(size[1], math.ceil(max(ys)+pad))
    if l >= r or t >= b:
        return None, None
    w, h = r-l, b-t
    aa = 1 if not antialias else 3 if w*h <= 4_000_000 else 1
    im = Image.new("RGBA",(w*aa,h*aa))
    draw = ImageDraw.Draw(im)
    width = max(1,round(obj.get("width",1)*aa))
    seam = max(1,round(2*bleed*aa)) if bleed and fill else 0
    exact = [((p[0]-l)*aa,(p[1]-t)*aa) for p in source]
    pts = exact if antialias else [(round(x),round(y)) for x,y in exact]
    if kind in {"rectangle","ellipse","rounded_rectangle"}:
        box = [*pts[0],*pts[1]]
        if not antialias: box = [box[0],box[1],box[2]-1,box[3]-1]
        if seam:
            grown = [box[0]-seam/2,box[1]-seam/2,box[2]+seam/2,box[3]+seam/2]
            if kind == "rectangle": draw.rectangle(grown,fill)
            elif kind == "ellipse": draw.ellipse(grown,fill)
            else: draw.rounded_rectangle(grown,radius=obj.get("radius",12)*aa+seam/2,fill=fill)
        if kind == "rectangle": draw.rectangle(box,fill,outline,width)
        elif kind == "ellipse": draw.ellipse(box,fill,outline,width)
        else: draw.rounded_rectangle(box,radius=obj.get("radius",12)*aa,fill=fill,outline=outline,width=width)
    elif kind == "polygon":
        if fill is not None:
            if antialias:
                draw.polygon(pts,fill=fill)
            else:
                coverage = hard_polygon_mask(w, h, exact)
                im.paste(Image.new("RGBA",(w,h),fill),(0,0),coverage)
            if seam: draw.line(pts+[pts[0]],fill=fill,width=seam,joint="curve")
        if outline: draw.line(pts+[pts[0]],fill=outline,width=width,joint="curve")
    elif kind == "bezier":
        curve = []
        for u in np.linspace(0,1,256):
            curve.append(tuple((1-u)**3*pts[0][i]+3*(1-u)**2*u*pts[1][i]+3*(1-u)*u*u*pts[2][i]+u**3*pts[3][i] for i in range(2)))
        draw.line(curve,fill=outline or fill,width=width,joint="curve")
    else: draw.line(pts,fill=outline or fill,width=width,joint="curve")
    # Box (area) downsampling is exact coverage antialiasing; Lanczos rings around hard edges.
    return (l,t,r,b), (im.resize((w,h),Image.Resampling.BOX) if aa != 1 else im)


def shape_image(size, obj):
    box, patch = shape_patch(size, obj)
    out = Image.new("RGBA",size)
    if box is not None:
        out.paste(patch, box[:2])
    return out


FILTER_KINDS = {"blur", "channel_shift", "noise", "relief"}


def gaussian_blur(image, radius):
    """Premultiplied blur: transparent pixels never darken or tint the soft edge."""
    if not radius:
        return image.copy()
    return image.convert("RGBa").filter(ImageFilter.GaussianBlur(radius)).convert("RGBA")


def _shifted(channel, dx, dy):
    out = np.zeros_like(channel)
    h, w = channel.shape
    sx0, sx1 = max(0,-dx), min(w,w-dx); sy0, sy1 = max(0,-dy), min(h,h-dy)
    if sx0 < sx1 and sy0 < sy1:
        out[sy0+dy:sy1+dy, sx0+dx:sx1+dx] = channel[sy0:sy1, sx0:sx1]
    return out


def channel_shift(image, offsets):
    """Move R, G and B (with their coverage) independently: chromatic aberration / misregistration."""
    arr = np.asarray(image, dtype=np.float32)/255
    alpha = arr[..., 3]
    planes, coverage = [], []
    for index, key in enumerate("rgb"):
        dx, dy = offsets.get(key, (0, 0))
        planes.append(_shifted(arr[..., index]*alpha, dx, dy))
        coverage.append(_shifted(alpha, dx, dy))
    new_alpha = np.maximum.reduce(coverage)
    rgb = np.stack(planes, axis=2)/np.maximum(new_alpha, 1e-8)[..., None]
    out = np.concatenate([np.clip(rgb, 0, 1), new_alpha[..., None]], axis=2)
    return Image.fromarray(np.uint8(out*255+.5))


def add_noise(image, amount, seed, monochrome=True):
    """Document-anchored grain on colour channels; alpha (coverage) is unchanged."""
    arr = np.asarray(image, dtype=np.int16).copy()
    rng = np.random.default_rng(seed)
    shape = arr.shape[:2] if monochrome else arr.shape[:2]+(3,)
    noise = rng.normal(0, 1, shape).astype(np.float32)*amount*64
    if monochrome:
        noise = noise[..., None]
    arr[..., :3] = np.clip(arr[..., :3]+noise, 0, 255).astype(np.int16)
    return Image.fromarray(np.uint8(arr))


def relief(image, angle=135.0, amount=.5, radius=1.5, source="luminance"):
    """Emboss lighting from a height map (luminance or coverage); alpha is unchanged.

    angle is the light direction in degrees counter-clockwise from +x (135 = from the upper left,
    as in common editors). Height from luminance lifts brushwork (impasto, carved block, embossed
    paper); from alpha it raises whole cut shapes (paper layers, stamped foil)."""
    arr = np.asarray(image, dtype=np.float32)/255
    alpha = arr[..., 3]
    if source == "alpha":
        height = alpha
    elif source == "luminance":
        height = (arr[..., 0]*.299+arr[..., 1]*.587+arr[..., 2]*.114)*alpha
    else:
        raise ValueError("relief source is luminance or alpha")
    if radius:
        height = np.asarray(Image.fromarray(np.uint8(np.clip(height*255+.5, 0, 255))).filter(
            ImageFilter.GaussianBlur(radius)), dtype=np.float32)/255
    gy, gx = np.gradient(height)
    theta = math.radians(angle)
    lx, ly = math.cos(theta), -math.sin(theta)
    # Slopes rising away from the light face it; scale so a softened unit edge reaches about ±1.
    shade = np.clip(-(gx*lx+gy*ly)*2.5*max(radius, .5), -1, 1)*amount
    rgb = arr[..., :3]
    lit = np.where(shade[..., None] > 0, rgb+(1-rgb)*shade[..., None], rgb*(1+shade[..., None]))
    out = np.concatenate([np.clip(lit, 0, 1), alpha[..., None]], axis=2)
    return Image.fromarray(np.uint8(out*255+.5))


# --- Pattern fills: hatching, stippling, halftone and ordered dither ---------------------------

PATTERN_KINDS = {"hatch", "stipple", "halftone", "dither"}
PATTERN_DEFAULT_DENSITY = {"hatch": 1.0, "stipple": .5, "halftone": .5, "dither": .5}
_BAYER8 = None


def _bayer8():
    global _BAYER8
    if _BAYER8 is None:
        m = np.array([[0]])
        for _ in range(3):
            m = np.block([[4*m, 4*m+2], [4*m+3, 4*m+1]])
        _BAYER8 = (m+.5)/64
    return _BAYER8


def validate_gradient(gradient):
    """{type: linear|radial, start:[x,y], end:[x,y], from:0..1, to:0..1} modulating pattern density."""
    if gradient is None:
        return None
    if not isinstance(gradient, dict) or set(gradient)-{"type", "start", "end", "from", "to"}:
        raise ValueError("gradient is {type:'linear'|'radial', start:[x,y], end:[x,y], from:0..1, to:0..1}")
    kind = gradient.get("type", "linear")
    if kind not in {"linear", "radial"}:
        raise ValueError("gradient type is linear or radial")
    start, end = gradient.get("start"), gradient.get("end")
    if not (isinstance(start, (list, tuple)) and isinstance(end, (list, tuple)) and len(start) == 2 and len(end) == 2):
        raise ValueError("gradient needs start=[x,y] and end=[x,y] in document pixels")
    for v in (*start, *end):
        finite(v, -1000000, 1000000, "gradient point")
    if math.hypot(end[0]-start[0], end[1]-start[1]) < 1e-6:
        raise ValueError("gradient start and end must differ")
    return {"type": kind, "start": list(start), "end": list(end),
            "from": finite(gradient.get("from", 0), 0, 1, "gradient from"), "to": finite(gradient.get("to", 1), 0, 1, "gradient to")}


def pattern_level(gradient, density, x, y):
    """Density at document points; a gradient maps 0..1 along it from 'from' to 'to'."""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    if gradient is None:
        return np.full(np.broadcast(x, y).shape, float(density))
    (sx, sy), (ex, ey) = gradient["start"], gradient["end"]
    dx, dy = ex-sx, ey-sy
    if gradient["type"] == "linear":
        t = ((x-sx)*dx+(y-sy)*dy)/(dx*dx+dy*dy)
    else:
        t = np.hypot(x-sx, y-sy)/math.hypot(dx, dy)
    t = np.clip(t, 0, 1)
    return gradient["from"]+(gradient["to"]-gradient["from"])*t


_GOLDEN = np.uint64(0x9E3779B97F4A7C15)


def _mix64(x):
    x = (x ^ (x >> np.uint64(30)))*np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27)))*np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def hash01(seed, *keys):
    """Deterministic uniform [0,1) per integer key tuple (vectorised).

    Pattern randomness is a function of document-anchored indices (line, cell, piece), so the same
    pattern continues across separate fills and does not depend on the filled area."""
    with np.errstate(over="ignore"):
        shape = np.broadcast(*keys).shape if keys else ()
        h = np.full(shape, np.uint64(seed & 0xFFFFFFFFFFFFFFFF), dtype=np.uint64)
        for key in keys:
            h = _mix64(h ^ (np.asarray(key).astype(np.int64).astype(np.uint64)+_GOLDEN))
        return (h >> np.uint64(11)).astype(np.float64)/float(1 << 53)


def _hatch_rows(xs, ys, params, family, angle):
    """Coverage 0..1 of one hatch direction at pixel centres, from an exact distance field."""
    spacing, jitter, seed = params["spacing"], params["jitter"], params["seed"]
    theta = math.radians(angle)
    ux, uy = math.cos(theta), math.sin(theta)
    o = xs*-uy+ys*ux  # coordinate across the lines
    s = xs*ux+ys*uy   # coordinate along the lines
    base = np.floor(o/spacing).astype(np.int64)
    coverage = np.zeros(xs.shape, dtype=np.float32)
    level = pattern_level(params["gradient"], params["density"], xs, ys)
    for step in ((-1, 0, 1, 2) if jitter else (0, 1)):
        k = base+step
        offset = (hash01(seed, family, k, 1)-.5)*jitter*spacing if jitter else 0.0
        phase = hash01(seed, family, k, 2) if jitter else np.zeros(xs.shape)
        on = np.ones(xs.shape, dtype=bool)
        to_end, piece = None, None
        tilt, centre = 0.0, 0.0
        if params["dash"]:
            pattern = params["dash"]; period = sum(pattern)
            pos = np.mod(s+phase*period, period)
            on[:] = False; to_end = np.zeros(xs.shape); piece = np.ones(xs.shape)
            start = 0.0
            for i in range(0, len(pattern), 2):
                length = pattern[i]
                inside = (pos >= start) & (pos < start+length)
                on |= inside
                to_end = np.where(inside, np.minimum(pos-start, start+length-pos), to_end)
                piece = np.where(inside, max(length, 1e-6), piece)
                start += pattern[i]+pattern[i+1]
        elif jitter:
            # Hand-made breaks: pieces of varying length with gaps, each slightly tilted.
            mean = max(spacing*10, 48.0)*(1.5-jitter)
            u = (s/mean)+phase
            cell = np.floor(u).astype(np.int64)
            fraction = u-cell
            gap = jitter*(.08+.35*hash01(seed, family, k, cell, 3))
            on = fraction < 1-gap
            piece = (1-gap)*mean
            to_end = np.minimum(fraction, 1-gap-fraction)*mean
            tilt = (hash01(seed, family, k, cell, 4)*2-1)*math.radians(4)*jitter
            centre = (cell-phase+(1-gap)/2)*mean
        distance = np.abs(o-(k*spacing+offset+tilt*(s-centre)))
        half = params["width"]*level/2
        if params["taper"] and to_end is not None:
            half = half*np.clip(to_end/np.maximum(params["taper"]*piece/2, 1e-6), 0, 1)
        mark = np.clip(half-distance+.5, 0, 1)*on
        mark = np.where(half*2 < .15, 0, mark)
        coverage = np.maximum(coverage, mark.astype(np.float32))
    return coverage


def pattern_coverage(kind, box, params):
    """Coverage (L image of the box) for a document-anchored pattern; returns (image, mark count).

    Lines and grids are anchored to the document origin, so neighbouring fills continue the same
    pattern. Everything is drawn by the engine; params come from paint_pattern."""
    l, t, r, b = box
    w, h = r-l, b-t
    seed = params["seed"]
    gradient, density = params["gradient"], params["density"]
    if kind == "hatch":
        out = np.zeros((h, w), dtype=np.float32)
        xs_row = np.arange(l, r, dtype=np.float64)+.5
        for top in range(t, b, 256):  # row chunks bound the float temporaries
            bottom = min(b, top+256)
            xs, ys = np.meshgrid(xs_row, np.arange(top, bottom, dtype=np.float64)+.5)
            chunk = np.zeros(xs.shape, dtype=np.float32)
            for family, angle in enumerate(params["angles"]):
                chunk = np.maximum(chunk, _hatch_rows(xs, ys, params, family, angle))
            out[top-t:bottom-t] = chunk
        diagonal = math.hypot(w, h)
        lines = len(params["angles"])*max(1, round(diagonal/params["spacing"]))
        if params["jitter"] and not params["dash"]:
            lines *= max(1, round(diagonal/(max(params["spacing"]*10, 48)*(1.5-params["jitter"]))))
        return Image.fromarray(np.uint8(np.clip(out*255+.5, 0, 255))), int(lines)
    if kind == "dither":
        cell = params["cell"]
        gx0, gy0 = l//cell, t//cell
        gx1, gy1 = -(-r//cell), -(-b//cell)
        cx = (np.arange(gx0, gx1)+.5)*cell
        cy = (np.arange(gy0, gy1)+.5)*cell
        level = pattern_level(gradient, density, cx[None, :], cy[:, None])
        bayer = _bayer8()
        threshold = bayer[np.arange(gy0, gy1)[:, None] % 8, np.arange(gx0, gx1)[None, :] % 8]
        on = np.uint8(level > threshold)*255
        big = np.kron(on, np.ones((cell, cell), dtype=np.uint8))
        ox, oy = l-gx0*cell, t-gy0*cell
        return Image.fromarray(np.ascontiguousarray(big[oy:oy+h, ox:ox+w])), int(on.astype(bool).sum())
    aa = 3 if w*h <= 2_500_000 else 2 if w*h <= 8_000_000 else 1
    image = Image.new("L", (w*aa, h*aa))
    draw = ImageDraw.Draw(image)
    count = 0
    def local(x, y): return ((x-l)*aa, (y-t)*aa)
    spacing, jitter = params["spacing"], params["jitter"]
    if kind == "stipple":
        gx0, gy0 = math.floor(l/spacing)-1, math.floor(t/spacing)-1
        gx1, gy1 = math.ceil(r/spacing)+1, math.ceil(b/spacing)+1
        gx, gy = np.meshgrid(np.arange(gx0, gx1), np.arange(gy0, gy1))
        x = (gx+hash01(seed, gx, gy, 1))*spacing
        y = (gy+hash01(seed, gx, gy, 2))*spacing
        keep = hash01(seed, gx, gy, 3) < pattern_level(gradient, density, x, y)
        sizes = params["size"]*(1+params["size_jitter"]*(hash01(seed, gx, gy, 4)*2-1))
        for px, py, size in zip(x[keep], y[keep], sizes[keep]):
            radius = max(.15, size/2)*aa
            cx, cy = local(px, py)
            draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=255)
            count += 1
    elif kind == "halftone":
        theta = math.radians(params["angles"][0])
        ux, uy = math.cos(theta), math.sin(theta)
        corners = [(l, t), (r, t), (l, b), (r, b)]
        us = [(x*ux+y*uy)/spacing for x, y in corners]
        vs = [(-x*uy+y*ux)/spacing for x, y in corners]
        iu, iv = np.meshgrid(np.arange(math.floor(min(us))-1, math.ceil(max(us))+2),
                             np.arange(math.floor(min(vs))-1, math.ceil(max(vs))+2))
        x = (iu*ux-iv*uy)*spacing
        y = (iu*uy+iv*ux)*spacing
        inside = (x > l-spacing) & (x < r+spacing) & (y > t-spacing) & (y < b+spacing)
        level = pattern_level(gradient, density, x, y)
        diameters = params["size"]*np.sqrt(np.clip(level, 0, 1))
        if jitter:
            diameters = diameters*(1+jitter*.3*(hash01(seed, iu, iv, 5)*2-1))
        for px, py, size in zip(x[inside], y[inside], diameters[inside]):
            if size < .2:
                continue
            radius = size/2*aa
            cx, cy = local(px, py)
            draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=255)
            count += 1
    if aa != 1:
        image = image.resize((w, h), Image.Resampling.BOX)
    return image, count


def render_objects(layer):
    size = layer.image.size
    layer.image = Image.new("RGBA",size)
    for obj in layer.objects:
        if obj["type"] == "text": im = text_image(size,obj)
        elif obj["type"] == "shape":
            if "transform" not in obj:
                box, patch = shape_patch(size,obj)
                if box is not None:
                    layer.image.alpha_composite(patch,box[:2])
                continue
            im = shape_image(size,obj)
        elif obj["type"] == "stroke":
            from .model import Layer
            temp = Layer("vector stroke",Image.new("RGBA",size))
            paint_stroke(temp,None,obj["points"],obj["brush"],obj["color"],obj.get("closed",False),
                         smooth=obj.get("smooth",False),dash=obj.get("dash"),dash_offset=obj.get("dash_offset",0))
            im = temp.image
        else: raise ValueError("Unknown editable object type")
        if "transform" in obj:
            im = transformed(im,obj["transform"])
        layer.image = Image.alpha_composite(layer.image,im)
