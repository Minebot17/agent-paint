# Agent Paint 0.5 workflow

Use `agent_paint` MCP tools when exposed. This installation's engine source is
`C:/repos/clip-stuido-headless`, Windows Python is `.venv/Scripts/python.exe` there. The same public
operations run on Linux/WSL. No desktop, focus or mouse access is needed. Check
`paint_state.version` / `capabilities` when unsure which features the running server has (a server
started before an engine update keeps the old code until it restarts; 0.5 lists `timelapse`).

## Transport

If the conversation does not expose the MCP, use a real MCP client over STDIO rather than pretending
the tools were called: `scripts/mcp_plan.py plan.jsonl --state-dir <own dir> --evidence <own dir>`
runs one `{tool, arguments, store?, name?}` per line, resolves `{"$ref":"name.key"}` from stored
results, stops at the first `isError` and saves responses plus snapshot images (`name.png`). One plan
is one server session: save the `.apaint` at the end; a later pass opens it first. Use Windows paths
(`C:/...`) for it under Git Bash (`$PWD` may resolve to `/tmp`). It must never fall back to another
renderer. Running a plan in its own process also isolates you from other agents on the shared server.

When the agent's own session breaks (an API network error, a restart), the MCP server and its open
documents live on: on resume call `paint_state`, find your `document_id`, take a snapshot and
continue. Save the `.apaint` after each pass so that even a server restart costs nothing.

## Typical pass

1. `paint_document(create)` → keep `document_id`; pass it on every call. If the user asked for a
   timelapse, start it now (see below).
2. `paint_layer(create, name=..., position="top"|"bottom", properties={...})`; results report `index`
   and `layer_count`. Raster for paint/selection, vector for editable strokes/shapes, text for type.
3. `paint_brush(configure, settings, color, document_id)` once per brush change, or per-call
   `brush`/`color` defaults on `paint_strokes`, or per-stroke overrides. `stabilization=0` keeps
   geometry; `min_size` is the width floor at zero pressure.
4. Render with `layer_id` on `paint_strokes`, `paint_shape`, `paint_fill`, `paint_pattern`,
   `paint_filter`, `paint_transform` (the active layer stays unchanged; names work as ids).
5. `paint_region(define)` + `paint_snapshot(region=...)` for matching detail evidence; inspect images.
6. Save `.apaint` (after every pass), export PNG, inspect; `paint_document(inspect)` verifies the project.

## Calls that save effort

- Curves: stroke `smooth=true` = Catmull-Rom through the points, pressure interpolated. Leaves and
  commas: `[[x0,y0,.15],[x1,y1,1],[x2,y2,.6],[x3,y3,0]]`. Polygons/lines/selections take `smooth` too.
- Dashes: stroke `dash=[18,6,3,6]` (centre line), `[6,4]` (hidden line), `[0,12]` (dots every 12 px);
  `paint_strokes(dash=..., dash_offset=...)` applies to every stroke that has no `dash` of its own.
- Patterns inside `points`/`rectangle`/selection: `paint_pattern(kind="hatch", angles=[45,-45],
  spacing=6, width=1.5, jitter=.5, taper=.6, gradient={type:"radial",start,end,from,to})`;
  `stipple` (spacing, size, density); `halftone` (spacing, angle, size); `dither` (cell = art pixel).
- Shapes: fill and outline in one call; `bleed=.5..1` hides seams between antialiased facets;
  `antialias=false` covers `[x0,x1)` for boxes and polygons (pixel art tiles exactly).
- Selections: `from_layer`/`flood` accept `layer_id`; feather/grow/shrink; add/subtract/intersect.
  Grow and shrink measure true distance, so circles stay round and a rounded offset outline is one
  call. `from_layer(threshold=128)` gives a hard mask from a soft or glazed layer (without it, partial
  alpha makes a partial mask and a fill through it barely shows). Subtract removes the new mask from
  the current selection, so `from_layer(A)` → `shrink` → `from_layer(A, operation="subtract")` is
  empty by design. Ring (inline, outline): `from_layer(A)` → `shrink(r)` → `paint_fill` a temporary
  layer → `from_layer(A)` → `from_layer(temp, operation="subtract")`.
- Fill inside a contour without a selection call: `paint_fill(kind=..., points=[...], smooth=true)`
  for solid, gradients and clear; it is intersected with an active selection.
- Glow / soft shadow: `paint_layer(duplicate)` → recolour with `alpha_lock` + `paint_fill` → `paint_filter(blur)`
  → `paint_transform(translate)` → blend `screen`/`add` (glow) or `multiply` (shadow). With
  `alpha_lock` a blur stays inside the shape; without it, blur spreads past a feathered selection.
- Whole picture effects: `paint_layer(stamp_visible)` then `channel_shift`, `noise` or
  `relief` (angle 135 = light from upper left; `source="luminance"` brushwork, `"alpha"` cut shapes;
  `depth=4..20` makes gentle painted height maps read). Shift a stamped opaque copy: `channel_shift`
  over transparent holes gives rainbow bands.
- Value and colour: `paint_filter(kind="levels", black=, white=, gamma=)` (gamma > 1 lightens
  midtones), `threshold` (`level`; photocopy, stencil), `invert`, `hue_saturation` (`hue` degrees,
  `saturation` factor 0..4, `lightness` -1..1). They keep alpha and honour the selection.
- Pixel art: `antialias=false`, `paint_pattern(kind="dither", cell=6)`, `paint_transform(resample="nearest")`,
  `paint_document(export, scale=6)`.
- Text: `letter_spacing`, `line_height`, `align="justify"`, `anchor="ls"` (first baseline on y),
  `rotation`, `scale_x/y`; `paint_fonts(supports="…")` for glyph coverage. Without `layer_id` each
  call makes a new text layer (`layer_name` names it); a `layer_id` must name a text layer, never a
  raster one. Read `missing_glyphs`. Editable text has no grain; texture a stamped/rasterized copy if
  the style needs it.

## Batches and plans

`paint_batch` is atomic and one undo step; any failure rolls back scene and brush. Inline ≤256
actions; `actions_file="plan.jsonl"` (or `.json`) ≤20000 actions without passing coordinates through
the chat. `"store":"name"` on an action (or inside its `arguments`) and `{"$ref":"name.layer_id"}`
later. `results="compact"` (default) lists ids and bboxes per action only up to 64 actions, then a
summary with `results_omitted`; use `"none"` for big plans. Warnings of every action (missing glyphs,
merge notes) are collected in `warnings`. Filesystem actions and nested batches are excluded.
`expected_revision` guards against drawing over an uninspected revision. Undo history keeps only
changed rectangles (copy-on-write) and is not saved in the project.

Small slips no longer roll a batch back: an empty `strokes` list does nothing; zero-size boxes and
polygons with fewer than 3 distinct points are skipped and counted in `skipped_degenerate`; values
up to 0.01 outside 0..1 (pressure 1.004) are clamped. Still fatal: unknown arguments, a missing or
ambiguous layer, a smoothed stroke far outside the canvas, real out-of-range values.

## Timelapse (only when the user asks)

Recording is off by default and costs nothing then. Start it only on the user's request.

1. Right after `paint_document(create)` (or `open`): `paint_timelapse(action="start",
   path="<your folder>/timelapse", document_id=...)`. Without `path` it goes under the server's state
   directory; the result names the folder. The folder must be new or empty (`overwrite=true`
   replaces an old recording).
2. Paint as usual. Every successful call that changes the document is recorded, with copies of plan
   files; failed calls and rolled-back batches are not, nor are snapshots, samples, saves and exports.
3. `paint_timelapse(action="stop")` when the picture is done (closing the document also stops it).
4. `paint_timelapse(action="render", path=<folder>)` replays the recording in a private engine and
   writes `<folder>/timelapse.mp4`. `seconds` is the build-up (default 5–20 s by the amount of work),
   `hold` the final picture (2 s), also `fps` (30), `max_side` (1280; small art and pixel art are
   enlarged by a whole factor), `output`, `overwrite`. A 1600×1200 painting takes about 40 s.
5. Report `output`, `seconds` and `verified` (true: the replay ended on exactly the recorded picture).
   Render needs ffmpeg (`AGENT_PAINT_FFMPEG`, `PATH` or `pip install -e ".[video]"`). Without it the
   recording is still complete: report the folder and `python -m csp_headless.timelapse render <folder>`.

## Behaviour to know

- Each document owns its brush and colour (saved in `.apaint`); undo does not revert the brush.
- New layers go above the active layer by default; `duplicate` goes above its source and becomes
  active. Layer names are unique: create or rename to a taken name is an error; automatic names
  (`… copy`, `Visible stamp`, text) get ` 2`, ` 3`.
- `merge_down` keeps the lower layer's id. A clipped or blended upper layer merges with its blend
  applied (the result notes it); the lower layer must be normal, unclipped and visible.
- `alpha_lock` keeps coverage; eraser or `paint_fill(kind="clear")` on it is an error.
- Points are pairs `[[x,y],...]` (or triples with pressure); flat lists are rejected.
- `paint_layer(list)` and `paint_state` report `object_count`; `paint_object(list)` returns objects.
- Export never saves the project; `project_saved` says whether the `.apaint` on disk is current.
  Opening an already open project selects it (`force=true` reloads from disk); a different saved
  copy of the same document opens under a new `document_id`.
- `paint_snapshot(path=..., overwrite=true)` reuses a file name; `upscale` says when `max_side` is
  too small to enlarge.
- Watercolor `edge_darkening` shows at any hardness; smudge mixes premultiplied (no dark streaks) but
  pulls in transparency at the canvas edge. `texture` ≥ .45 visibly thins opaque strokes and airbrush.
- Own brush renderers, not CSP parity. Colours mix in 8-bit sRGB. ORA/PSD rasterize text/vectors
  and bake masks/clipping; `.apaint` keeps everything editable. External fonts are not embedded.

The renderer accepts geometry plans, not only manual gestures; inspect their perspective, scale and
irregularity. Mark count or rendering speed never substitutes for construction and visual review.
