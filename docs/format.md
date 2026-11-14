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

# Timelapse recording, version 1 (engine 0.5)

A folder written by `paint_timelapse(action="start")`. Recording is optional and off by default.

| Entry | Meaning |
| --- | --- |
| `manifest.json` | `format: "agent-paint-timelapse"`, `version: 1`, engine version, `status` (`recording` or `stopped`), document id/name/size/background, `started_at`, `start_revision`, the server's brush presets at start; on stop also `stopped_at`, `reason`, `commands`, `units`, `seconds`, `final_revision`, `final_sha256` |
| `start.apaint` | The document when recording started, in the `.apaint` format above (including the selection and the brush) |
| `commands.jsonl` | One successful state-changing call per line, in call order |
| `assets/` | `plan-<seq>.jsonl` copies of batch `actions_file` plans; `keyframe-<seq>.apaint` when the document was reloaded from disk while recorded |
| `preview.png` | The finished picture, at most 512×512, written on stop |

A command line is `{"seq", "t", "tool", "arguments", "units", "ids"?}`:
- `seq` counts from 1, and `t` is seconds since the start.
- `arguments` are the call's arguments without `document_id` and `expected_revision`. A batch
  `actions_file` is rewritten to its `assets/` copy, relative to the folder.
- `units` is the call's work: strokes count one by one, a batch counts its actions and their
  strokes, and anything else counts 1.
- `ids` are the layer/object ids the call generated, in generation order. A replay hands them back
  so that later commands naming them still apply.
- `tool` is an MCP tool name, or `__keyframe__` with `arguments.path` (replace the document with
  that `.apaint`).
- `paint_brush` `load_preset` is stored as `configure` with the resulting settings, because presets
  are shared by everybody on a server.

`final_sha256` is SHA-256 of the full-resolution composite. It covers the UTF-8 text of the size
tuple, for example `(1600, 1200)`, followed by the RGBA bytes. A renderer reports `verified=true` when
its replay produces the same hash. A recording cut short by a crash stays readable up to its last
complete line.
