# `.apaint`, version 1

ZIP UTF-8 archive. `mimetype` is the first entry, uncompressed, with content
`application/x-agent-paint`. No executable code, pickle, external resource fetching or archive
extraction is used when loading.

| Entry | Meaning |
| --- | --- |
| `manifest.json` | `format: "agent-paint"`, `version: 1`, document metadata and layer records |
| `layers/<layer-id>.png` | Full document-size RGBA pixels, straight alpha, cached for all layer kinds |
| `masks/<layer-id>.png` | Optional document-size 8-bit grayscale mask |
| `selection.png` | Optional document-size grayscale selection; absence means unrestricted |
| `preview.png` | Composite thumbnail, at most 512×512. Since engine 0.3.1 a document larger than about 1536 px with visible non-normal blend layers is composited from layers reduced to about 1024 px, so its preview is approximate; never use it as image data |

`document`: stable document ID, name, `[width,height]`, DPI, optional paper/background CSS color,
active layer ID, revision, named document regions; since engine 0.3 optionally the document's
current `brush` (full settings) and `color`, which older readers ignore. Save paths are not serialized;
opening always uses the actual opened path. Runtime history and view are not restored from files.
`saved_revision`/dirty status are recomputed at open.

`layers` array is **bottom to top**. Each record contains stable ID, name, image entry, optional mask
entry, opacity `0..1`, blend, visible/locked/alpha_lock/clipping booleans, kind `raster|vector|text`,
and an `objects` array. Raster pixels are authoritative for raster layers. Cached pixels permit
exact reopening of editable layers without font dependencies; objects are authoritative when editing.

Objects:

- `stroke`: ID, points `[x,y,pressure]`, full brush settings, CSS color, closed flag; 0.3 adds
  optional `smooth: true` (Catmull-Rom curve through the points) and `dash` `[on,off,...]` with
  `dash_offset`. Older engines ignore them and render a solid polyline.
- `shape`: ID, shape type, points, optional fill/outline colors, stroke width, corner radius,
  optional `antialias: false` (hard pixels; absent means antialiased). Engine 0.1 ignores the key and antialiases.
  0.3 adds optional `smooth: true` (polygon/line through its points as a curve) and `bleed` (px of
  fill widening that hides seams). Since 0.3 hard polygons use the pixel-centre rule `[x0,x1)`.
- `text`: ID, literal Unicode text, x/y, style dictionary (see MCP paint_text; 0.2 adds optional
  `anchor`, default `lt`; 0.3 adds `letter_spacing`, `line_height`, `align: justify` and the
  baseline anchors `ls`/`ms`/`rs`).

Each object may have `transform: [a,b,c,d,e,f]`, mapping `x'=a*x+b*y+c`, `y'=d*x+e*y+f`.
All geometry uses document pixels. Shape strokes have antialiased edges. Rotations and nonuniform
scale are representable without changing the source coordinates. Font files are not embedded;
the bundled default is portable, external fonts require installation for future editing.

Loaders reject unknown format versions rather than guessing. Current resource limits: 32 million
document pixels, each side <=16384, 1..128 layers, <=512 ZIP entries, <=512 MiB uncompressed archive.
Layer/mask/selection dimensions must match the document. ZIP paths are read by name, never extracted.

Future format changes must introduce an explicit version/migration. Do not silently repurpose fields
to reproduce old GUI state; GUI state is outside the document model.
