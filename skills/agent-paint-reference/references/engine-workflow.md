# Agent Paint 0.3 workflow

Use `agent_paint` MCP tools when exposed. This installation's engine source is
`C:/repos/clip-stuido-headless`, Windows Python is `.venv/Scripts/python.exe` there. The same public
operations run on Linux/WSL. No desktop, focus or mouse access is needed. Check
`paint_state.version` / `capabilities` when unsure which features the running server has (a server
started before an engine update keeps the old code until it restarts).

## Transport

If the conversation does not expose the MCP, use a real MCP client over STDIO rather than pretending
the tools were called: `scripts/mcp_plan.py plan.jsonl --state-dir <own dir> --evidence <own dir>`
runs one `{tool, arguments, store?, name?}` per line, resolves `{"$ref":"name.key"}` from stored
results, stops at the first `isError` and saves responses plus snapshot images (`name.png`). One plan
is one server session: save the `.apaint` at the end; a later pass opens it first. Use Windows paths
(`C:/...`) for it under Git Bash (`$PWD` may resolve to `/tmp`). It must never fall back to another
renderer. Running a plan in its own process also isolates you from other agents on the shared server.

## Typical pass

1. `paint_document(create)` → keep `document_id`; pass it on every call.
2. `paint_layer(create, name=..., position="top"|"bottom", properties={...})`; results report `index`
   and `layer_count`. Raster for paint/selection, vector for editable strokes/shapes, text for type.
3. `paint_brush(configure, settings, color, document_id)` once per brush change, or per-call
   `brush`/`color` defaults on `paint_strokes`, or per-stroke overrides. `stabilization=0` keeps
   geometry; `min_size` is the width floor at zero pressure.
4. Render with `layer_id` on `paint_strokes`, `paint_shape`, `paint_fill`, `paint_pattern`,
   `paint_filter`, `paint_transform` (the active layer stays unchanged; names work as ids).
5. `paint_region(define)` + `paint_snapshot(region=...)` for matching detail evidence; inspect images.
6. Save an unused `.apaint` path, export PNG, inspect; `paint_document(inspect)` verifies the project.

## Calls that save effort

- Curves: stroke `smooth=true` = Catmull-Rom through the points, pressure interpolated. Leaves and
  commas: `[[x0,y0,.15],[x1,y1,1],[x2,y2,.6],[x3,y3,0]]`. Polygons/lines/selections take `smooth` too.
- Dashes: stroke `dash=[18,6,3,6]` (centre line), `[6,4]` (hidden line), `[0,12]` (dots every 12 px).
- Patterns inside `points`/`rectangle`/selection: `paint_pattern(kind="hatch", angles=[45,-45],
  spacing=6, width=1.5, jitter=.5, taper=.6, gradient={type:"radial",start,end,from,to})`;
  `stipple` (spacing, size, density); `halftone` (spacing, angle, size); `dither` (cell = art pixel).
- Shapes: fill and outline in one call; `bleed=.5..1` hides seams between antialiased facets;
  `antialias=false` covers `[x0,x1)` for boxes and polygons (pixel art tiles exactly).
- Selections: `from_layer`/`flood` accept `layer_id`; feather/grow/shrink; add/subtract/intersect.
  Subtract removes the new mask from the current selection, so `from_layer(A)` → `shrink` →
  `from_layer(A, operation="subtract")` is empty by design. Ring (inline, outline): `from_layer(A)` →
  `shrink(r)` → `paint_fill` a temporary layer → `from_layer(A)` → `from_layer(temp, operation="subtract")`.
- Glow / soft shadow: `paint_layer(duplicate)` → recolour with `alpha_lock` + `paint_fill` → `paint_filter(blur)`
  → `paint_transform(translate)` → blend `screen`/`add` (glow) or `multiply` (shadow).
- Whole picture effects: `paint_layer(stamp_visible)` then `channel_shift`, `noise` or
  `relief` (angle 135 = light from upper left; `source="luminance"` brushwork, `"alpha"` cut shapes).
- Pixel art: `antialias=false`, `paint_pattern(kind="dither", cell=6)`, `paint_transform(resample="nearest")`,
  `paint_document(export, scale=6)`.
- Text: `letter_spacing`, `line_height`, `align="justify"`, `anchor="ls"` (first baseline on y),
  `rotation`, `scale_x/y`; `paint_fonts(supports="…")` for glyph coverage. Editable text has no
  grain; texture a stamped/rasterized copy if the style needs it.

## Batches and plans

`paint_batch` is atomic and one undo step; any failure rolls back scene and brush. Inline ≤256
actions; `actions_file="plan.jsonl"` (or `.json`) ≤20000 actions without passing coordinates through
the chat. `"store":"name"` on an action and `{"$ref":"name.layer_id"}` later. `results="compact"`
(default) returns ids and union bboxes; `"none"` only a summary. Filesystem actions and nested
batches are excluded. `expected_revision` guards against drawing over an uninspected revision.
Undo history keeps only changed rectangles (copy-on-write) and is not saved in the project.

## Behaviour to know

- Each document owns its brush and colour (saved in `.apaint`); undo does not revert the brush.
- New layers go above the active layer by default; `duplicate` goes above its source and becomes
  active; `merge_down` keeps the lower layer's id.
- `alpha_lock` keeps coverage; eraser or `paint_fill(kind="clear")` on it is an error.
- Points are pairs `[[x,y],...]` (or triples with pressure); flat lists are rejected.
- `paint_layer(list)` and `paint_state` report `object_count`; `paint_object(list)` returns objects.
- Export never saves the project; `project_saved` says whether the `.apaint` on disk is current.
  Opening an already open project selects it (`force=true` reloads from disk); a different saved
  copy of the same document opens under a new `document_id`.
- Watercolor `edge_darkening` shows at any hardness; smudge mixes premultiplied (no dark streaks).
- Own brush renderers, not CSP parity. Colours mix in 8-bit sRGB. ORA/PSD rasterize text/vectors
  and bake masks/clipping; `.apaint` keeps everything editable. External fonts are not embedded.

The renderer accepts geometry plans, not only manual gestures; inspect their perspective, scale and
irregularity. Mark count or rendering speed never substitutes for construction and visual review.
