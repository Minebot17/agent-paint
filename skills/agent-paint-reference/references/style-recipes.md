# Style recipes from the 2026-11 studies

Thirty agents painted one original work each (2026-11-08) on Agent Paint 0.2; earlier studies
(2026-11-06) add a few more. Each line keeps what produced the style and the weakness the painter
reported. They are starting points from single studies, not validated rules: trial the marks,
inspect, adapt. Where a recipe needed a workaround, the 0.3 tool that replaces it is named.

## Universal lessons

- Plan composition, value masses and palette first; every study that skipped it ended with an empty
  corner, a weak bottom third or a centred mirror-symmetric layout.
- Regularity is the most frequent flaw: vary size, spacing, angle and shape on purpose (seeded
  jitter, a few hand-placed exceptions). Identical repeats read as stamps.
- "Vector-clean" edges betray hand styles (charcoal, fresco, manuscript, fauvism, chalk): add broken
  edges (textured eraser with `texture_space="paper"`), pressure variation and slight misalignment.
- Figures, faces and animals stayed schematic in most studies: construct the big masses and the
  gesture before features; give them at least one plane change and one overlap.
- Text: choose a font with `paint_fonts(supports=...)`; editable text has no grain, so add texture to
  a stamped copy when the style is printed or chalky.
- Multiply layers per ink/colour give honest transparent overlaps (watercolor, risograph, Bauhaus).
- Big repetitive geometry (tiles, dots, facets, hatching) belongs in a generated plan file or in
  `paint_pattern`, not in hand-typed JSON.

## Painting and drawing media

- **Impasto / impressionism** — tinted underpainting closes gaps; ~15k short dabs (round, hardness
  .8, texture .45, pressure .35→1→.45), direction per surface, size growing toward the viewer;
  complementary shadows. Weak: flat paint, uniform "fish-scale" sky. 0.3: `relief` on a stamp for
  thickness, vary dab size within the sky.
- **Pointillism** — ~50k round dots from a weighted pigment set per zone, dot size by depth,
  complementary dots in shadows, dotted complementary border; ran through `mcp_plan.py` in ~16 s.
  Weak: noisy sky, no focal value contrast. Keep a darker focal zone.
- **Fauvism** — big hard round strokes (hardness .82–.95, paper texture .3), flat masses first,
  dark blue/red contours, bare canvas left in gaps. Weak: polyline chevrons, arrow-shaped tapers.
  0.3: `smooth=true` strokes.
- **Watercolor (botanical, wet-on-wet)** — watercolor brush hardness .6–.9, opacity .45–.6,
  `edge_darkening` .7–1, multiply layers: wash → glaze → wet-on-dry details; leave paper for
  highlights. Weak: soft blur instead of blooms, wire-like veins. 0.3: rims show on soft brushes
  too; `mixing` no longer dirties; draw veins with taper and break them.
- **Sumi-e ink wash** — one stroke per leaf (round, pressure .18→1→0), three ink tones, dry brush by
  paper-coloured scratches on an alpha-locked stalk; misty hills = gradient in feathered polygons;
  big empty space; vermilion seal. Weak: straight parallel stalks. 0.3: `smooth`, `hatch` with jitter
  for dry-brush fibres.
- **Charcoal and chalk on toned paper** — paper-anchored grain on every tool, construction lines at
  20% opacity, masses with airbrush, diagonal hatching clipped by selection, textured eraser for
  lifts, white chalk only on the highest lights. Weak: mechanical hatching, helmet-like cap.
  0.3: `paint_pattern(kind="hatch", jitter, taper)`; smudge now blends cleanly.
- **Ballpoint sketch** — thin blue-violet strokes (1.9–2.3 px, stabilization 0), tone only by 3–4
  hatching passes each on a multiply layer, construction lines and ink blobs, notebook rules,
  handwritten notes on the baseline. Weak: procedural hatching. 0.3: hatch with jitter; `anchor="ls"`.
- **Chalkboard** — chalk = textured eraser passes over each chalk layer (shared paper tooth),
  flat-chalk diagonal stripes in fills, ghosts of old writing at low opacity, wooden frame.
  Weak: obviously typeset letters. Stamp text and break it with the textured eraser.
- **Gouache, mid-century** — flat polygons in a retro palette, black line layer offset (−7,−5) for
  misregistration, dry chalky edges by textured eraser, spatter, atomic-age motifs. Weak: offsets
  that look like errors; template cat head. Offset only some lines; vary motif sizes.

## Print and graphic

- **Ukiyo-e woodblock** — three-stop bokashi gradients (a two-stop mixes to grey), flat polygons with
  dark key-block outline in the same call, mountains without outline for depth, vertical cartouche.
  Weak: templated pine needles, mechanical ripples, typeset cartouche.
- **Linocut** — black block, carving with paper-coloured tapered strokes (.12→1→0) following form,
  reversals black/white, ink wear by textured eraser, red second block offset 2 px. Weak: symmetric
  subject, dull lower third. 0.3: `relief(source="alpha")` for a slight emboss.
- **Risograph** — one multiply layer per ink, misregistration by translating a layer, knock-outs cut
  from both inks, grain by textured eraser per ink. Weak: clean title, flat overprint. 0.3:
  `halftone` with `texture`, stamped text copy.
- **Pop-art comic** — Ben-Day dots, tapered heavy outlines, burst and balloon by selection fill +
  shrink. Weak: static pose. 0.3: `paint_pattern(kind="halftone")`; `channel_shift` is RGB only —
  translate the colour layer instead for plate misregistration.
- **Art Deco poster** — symmetric geometry from a script, stepped gradients as hard bands, single
  vanishing point, clipping layer for stripes, geometric font. Weak: frontal static subject.
  0.3: `letter_spacing` for spaced capitals.
- **Art Nouveau** — whiplash contours with sinusoidal pressure, pastel flat fields, halo mosaic,
  mirrored ornament. Weak: fan-like regular tail, duplicated eye rings on overlaps. Keep overlapping
  rings on separate layers; break symmetry.
- **Psychedelic poster** — complementary colours edge to edge, ripples `r + A·sin(nθ)`, thick wavy
  outlines, letters as separate rotated/stretched text objects along an arc. Weak: crowded bottom.
- **Tattoo flash** — hard round 8–9 px outlines, fill → shading in shape selection → outline →
  white highlight dashes; whip shading with textured pressure 1→0. Weak: flat panther head, puffy
  sails. 0.3: `stipple` with a gradient for whip shading.
- **Blueprint** — Prussian ground with mottling, line weights by role, dashed hidden / dash-dot
  centre lines, section hatching in selections, monospace title block. 0.3: `dash` and `hatch`
  replace thousands of scripted segments.
- **Bauhaus abstraction** — one multiply layer per colour, diagonal balance, staff lines, wobbly hand
  lines (stabilization 0, dense points). Weak: empty corner. Use vector layers for editable geometry.

## Mosaic, glass, paper, geometry

- **Roman mosaic** — tiles laid along contour rows (andamento), black outer row per figure,
  background halo rows then straight courses, guilloche border, missing tiles; ~26k tile polygons in
  ~14 s via a plan. Weak: thin dolphin snouts, mirrored composition.
- **Stained glass** — each pane = polygon with fill + 6–7 px lead outline in one call, Voronoi
  background, inner glow (blurred copy, screen), grisaille strokes, stone frame. Weak: busy wing
  coverts, dark flat stone.
- **Layered paper-cut** — 6–8 silhouette layers pale→saturated, each with its own drop shadow
  growing toward the front, paper noise per layer. Weak: identical fish. 0.3: `relief(alpha)`.
- **Low-poly** — Delaunay facets from a script, colour from one light direction, sky/water as facet
  gradients, mirrored reflection. 0.3: `bleed=.6` instead of 1 px same-colour outlines.
- **Isometric diorama** — `x = cx + (gx−gy)·w/2`, `y = cy + (gx+gy)·w/4 − z·w/2`, back-to-front
  depth sort, three tones per material, AO blur on multiply, glow on screen. Weak: flat windmill
  blades, awning hiding windows.
- **Pixel art** — art-pixel grid (e.g. 6×6), `antialias=false`, 16-colour palette, dithering.
  0.2 needed a comb fix for polygon edges and hand-built dithers; 0.3: exact `[x0,x1)` polygons,
  `paint_pattern(kind="dither", cell=6)`, `resample="nearest"`, `export(scale=...)`.

## Historical and decorative

- **Illuminated manuscript** — vellum gradient + stains, gold = gradient on alpha lock + white
  highlights + dark outline, lapis/vermilion fields, knotwork with correct over/under, blackletter
  justified by scale. Weak: vector-clean, snake-like dragon. 0.3: `align="justify"`, `anchor="ls"`.
- **Egyptian fresco** — registers on ground lines, composite-view figures from a canon grid, flat
  mineral colours with dark-red outline in one call, invented glyphs, cracks and flaked plaster.
  Weak: clean contour, cloned musicians. Vary poses; roughen contours.
- **Khokhloma** — black lacquer ground, gold by gradient on alpha lock, "travka" commas (pressure
  0→1 drop heads), berries with highlight, 16-fold border repeat, lacquer sheen. Weak: polygonal
  body, sparse ornament. 0.3: `smooth` silhouettes and curls.
- **Cubism** — facets from polygon selections + gradients, shadow/light wedges on multiply/screen
  cutting through objects, rotated second views, collage text. Weak: vector look, rain-like
  hatching. 0.3: `hatch` with jitter per facet.
- **Synthwave** — gradient sky, striped sun via cleared bands, perspective grid, glow on every neon
  layer, chromatic aberration, grain on overlay. Weak: flat car, aberration only per layer.
  0.3: `stamp_visible` then `channel_shift` for the whole image.
