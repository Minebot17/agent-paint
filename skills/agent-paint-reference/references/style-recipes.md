# Style recipes from the 2026-11 studies

Ninety agents painted one original work each, in three waves of thirty: 2026-11-08 on Agent Paint
0.2, then 2026-11-09 (studies 31–60) and 2026-11-14 (84–113) on 0.4; earlier studies (2026-11-06)
add a few more. Numbers in brackets point to the tables in the engine project's
`docs/painting-experience.md`. Each line keeps what produced the style and the weakness the painter
reported. They are starting points from single studies, not validated rules: trial the marks,
inspect, adapt. Where a recipe needed a workaround, the tool that replaces it (0.3 or 0.5) is named.

## Universal lessons

- Plan composition, value masses and palette first; every study that skipped it ended with an empty
  corner, a weak bottom third or a centred mirror-symmetric layout.
- Give the picture a mood, one focal point and one surprising detail that reads at first glance (a
  needle with golden thread leaving the frame, a cow of the wrong colour, a full moon reflected under a
  crescent). These were the most memorable places of the third wave; a detail too small for a 500 px
  view (a pigeon on a signpost, a ghost carp) is lost.
- Model the medium, not only the picture: the strongest studies computed the material in a script —
  a body of revolution (vase, cup, hull), marbling physics, a knit or quilt surface in (u,v), a
  perspective camera with projected shadows, a lit height map, a distance field — and sent it as one
  `actions_file`.
- Graphic, design and craft styles come out stronger than painterly ones. Oil, tempera, wax, rococo
  and cave paintings stayed smooth and "digital": break their edges, vary dabs, let underlayers show.
- Regularity is the most frequent flaw: vary size, spacing, angle and shape on purpose (seeded
  jitter, a few hand-placed exceptions). Identical repeats read as stamps.
- "Vector-clean" edges betray hand styles (charcoal, fresco, manuscript, fauvism, chalk, enamel):
  add broken edges (textured eraser with `texture_space="paper"`), pressure variation and slight
  misalignment. About half of all studies named this as their weakness.
- Figures, faces and animals stayed schematic in most studies: construct the big masses and the
  gesture before features; give them at least one plane change and one overlap.
- Text: choose a font with `paint_fonts(supports=...)`; editable text has no grain, so add texture to
  a stamped copy when the style is printed or chalky.
- Multiply layers per ink/colour give honest transparent overlaps (watercolor, risograph, Bauhaus);
  offsetting them by 2–7 px gives print misregistration (lubok, lithograph).
- Big repetitive geometry (tiles, dots, facets, hatching) belongs in a generated plan file or in
  `paint_pattern`, not in hand-typed JSON. Give generators a size and time limit.

## Painting and drawing media

- **Impasto / impressionism** — tinted underpainting closes gaps; ~15k short dabs (round, hardness
  .8, texture .45, pressure .35→1→.45), direction per surface, size growing toward the viewer;
  complementary shadows. Weak: flat paint, uniform "fish-scale" sky. 0.3: `relief` on a stamp for
  thickness, vary dab size within the sky.
- **Pointillism** — ~50k round dots from a weighted pigment set per zone, dot size by depth,
  complementary dots in shadows, dotted complementary border; ran through `mcp_plan.py` in ~16 s.
  Weak: noisy sky, no focal value contrast. Keep a darker focal zone.
- **Futurism (84)** — ~26k divisionist dabs coloured by position (~20% complementary), directed
  radially from the light and along the motion; ghost silhouettes with falling alpha; force lines as
  `smooth` curves tapered at both ends. Weak: small subject, flat wedges, confetti specks.
- **Fauvism** — big hard round strokes (hardness .82–.95, paper texture .3), flat masses first,
  dark blue/red contours, bare canvas left in gaps. Weak: polyline chevrons, arrow-shaped tapers.
  0.3: `smooth=true` strokes.
- **Tenebrism (31)** — brown imprimatura; modelling on `alpha_lock` with a paper-textured brush; one
  multiply glaze by radial gradient; flame by airbrush on add; impasto lights + `relief` in the
  highlight selection. Weak: smooth digital surface, regular folds; `texture` ≥ .45 thins coverage.
- **Rococo (85)** — foliage from thousands of tapered dabs (pressure .2→1→.6→0) in clumps, each with
  its own lit side; sky of radial gradients; soft focus by stamp + blur; frame of offset contours,
  gilding on `alpha_lock`, pearls `dash=[0,10]`. Weak: broccoli trees, pale figures; it read as a
  misty landscape, not rococo — give the figures and the frivolity the stage.
- **Encaustic (86)** — red underlayer; ~2,500 wall dabs with paper texture and watercolor puddles;
  scratches with the eraser down to the underlayer; stamp + ~4,000 smudges that never cross
  silhouettes; `relief` by luminance. Weak: smooth pears, drips like nails — digital oil, not wax.
- **Sienese tempera (34)** — gold on `alpha_lock` over red bole + stipple; punched halo by
  `dash=[0,gap]` and offset copies; face: green earth → flesh → hatch with a gradient → lead white;
  craquelure of 3,000 lines. Weak: long neck, regular brocade and cracks.
- **Reverse glass painting (87)** — crumpled foil: 22 px grid with jittered nodes, ~4,800 triangles
  toned by a height map of folds, `relief` + stipple; transparent paint on multiply; outline on its
  own layer with sinusoidal pressure. Weak: the foil reads low-poly in places.
- **Watercolor (botanical, wet-on-wet)** — watercolor brush hardness .6–.9, opacity .45–.6,
  `edge_darkening` .7–1, multiply layers: wash → glaze → wet-on-dry details; leave paper for
  highlights. Weak: soft blur instead of blooms, wire-like veins. 0.3: rims show on soft brushes
  too; `mixing` no longer dirties; draw veins with taper and break them.
- **Urban sketch (104)** — fineliner 2.7 px, stabilization 0, ±1 px wobble, overshooting ends, 18%
  gaps; each colour area is one zigzag watercolor stroke offset from the lines. Weak: capsule-shaped
  sky, too few darks.
- **Sumi-e ink wash** — one stroke per leaf (round, pressure .18→1→0), three ink tones, dry brush by
  paper-coloured scratches on an alpha-locked stalk; misty hills = gradient in feathered polygons;
  big empty space; vermilion seal. Weak: straight parallel stalks. 0.3: `smooth`, `hatch` with jitter
  for dry-brush fibres.
- **Gongbi (38)** — silk = gradient + hatch 0/90 at .07; petals by radial fills with glazed tips;
  "iron-wire" outline round 1.2–2.6 px; ~1,000 feather strokes along a direction field. Weak: even
  gradients instead of many glazes.
- **Blue-green landscape (88)** — stepped polygon cliffs with ochre → malachite → azurite
  gradients; texture strokes from interpolated curves; hatch with a density gradient; gold along the
  ridges. Weak: look-alike peaks, crowded bottom.
- **Surrealism (32)** — sky of five gradients in selections; ~4,100 perspective floor tiles from a
  script; hard shadows by projecting the 3D geometry onto an opaque multiply layer. Weak: low-poly
  cliff, tile moiré at the horizon.
- **Naive jungle (33)** — 7 leaf profiles as `smooth` polygons with 5% jitter, a dark half per leaf,
  veins as polygons and strokes, 18 greens, 4 depth planes. Weak: a vector children's book rather
  than paint.
- **Action painting (36)** — random walk with inertia, `smooth` skeins with pressure .28–1, pools at
  pressure peaks, drips, splatter + stipple, `relief` on a stamp. Weak: smooth skeins without the
  torn edge of enamel.
- **Cave painting (42)** — wall by airbrush + stipple + `relief`; pigments on multiply with texture
  .85 paper; hand stencils = wall selection minus the hand + an airbrush ring. Weak: soft and
  digital, stamp-like horses, hands like twigs.
- **Charcoal and chalk on toned paper** — paper-anchored grain on every tool, construction lines at
  20% opacity, masses with airbrush, diagonal hatching clipped by selection, textured eraser for
  lifts, white chalk only on the highest lights. Weak: mechanical hatching, helmet-like cap.
  0.3: `paint_pattern(kind="hatch", jitter, taper)`; smudge now blends cleanly.
- **Coloured pencils (107)** — pencil 3–5 px, texture .55–.7 paper; a berry from scripted hatching
  with Lambert pressure and a new direction per pass; gloss = dense hatch + smudge + white pencil;
  refraction by a mirrored copy ×1.7. Weak: wall hatching like rain.
- **Ballpoint sketch** — thin blue-violet strokes (1.9–2.3 px, stabilization 0), tone only by 3–4
  hatching passes each on a multiply layer, construction lines and ink blobs, notebook rules,
  handwritten notes on the baseline. Weak: procedural hatching. 0.3: hatch with jitter; `anchor="ls"`.
- **Fairy-tale pen illustration (105)** — sepia pen with pressure .06→1→0; bark fibres from a script
  flowing around knots that form a face; crosshatch in shadows; muted watercolor washes; mist on
  screen. Weak: tiny fairy, the vignette turns monochrome.
- **One-line drawing (106)** — one path of 3,072 points (~15.9k px) with wheel spirals and daisy
  loops, drawn as one `smooth` stroke; pressure by segment weight + "breathing" + hand tremor.
  Weak: spokes as chords, a scribbled pedal.
- **Marker sketch (59)** — one profile + a perspective camera for two views; fills by parallel
  `marker` strokes (.85/.5) in a selection, one multiply layer per pass (CG2 → CG4 → CG7); gel-pen
  highlights. Weak: regular marker bands.
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
- **Lubok (40)** — outline with a hard brush and random pressure; 4 multiply colour layers offset
  5–7 px; textured eraser .75; the caption rasterized and chipped. Weak: a marker line, not a cut one.
- **Mezzotint (46)** — solid dark plate, light lifted with cream airbrush layers with paper texture;
  grain by stamp → noise → blur → noise inside the plate; plate mark `relief(alpha)`. Weak: a sepia
  photo without rocker texture.
- **Gyotaku (113)** — fish body from 15–30k small dabs (opacity ~.13, paper texture), denser at the
  edge, with noise and gaps; scales as a grid of erased arcs; fan fins; washi fibres. Weak: beaded
  edges.
- **Risograph** — one multiply layer per ink, misregistration by translating a layer, knock-outs cut
  from both inks, grain by textured eraser per ink. Weak: clean title, flat overprint. 0.3:
  `halftone` with `texture`, stamped text copy.
- **Cabaret lithograph (95)** — silhouettes from a script; the cast shadow is the same geometry
  projected from the footlights; crachis spatter = stipple (`size_jitter` .8) with gradients; colour
  layers offset 2–3 px. Weak: the font is not of the 1890s, doll-like dancer.
- **Polish poster (94)** — gouache round (hardness .92, texture .3): background in 90–150 px dabs,
  face by elliptical tangents on `alpha_lock`; gears from a script; an eye mechanism on a clipping
  layer; hand-lettered title + paper eraser. Weak: egg head, vector gears.
- **Punk zine (96)** — black fill + white halftone with radial gradients from the lamp; letters as
  rotated text objects on torn scraps; stamp → noise → multiply copy; photocopier-lid shadow.
  Weak: clean title, even noise. 0.5: `threshold`/`levels` on a stamp for the photocopy look.
- **Pop-art comic** — Ben-Day dots, tapered heavy outlines, burst and balloon by selection fill +
  shrink. Weak: static pose. 0.3: `paint_pattern(kind="halftone")`; `channel_shift` is RGB only —
  translate the colour layer instead for plate misregistration.
- **Manga (47)** — G-pen: hardness 1, taper .25/.35, pressure .15→1→0; `halftone` with gradients in
  polygons; ~280 speed lines; gutters = the inverse of the union of panels. Weak: a stiff figure.
- **Noir comic (98)** — a perspective 3D model of the stairs → black polygons; blind stripes =
  polygons between planes through the light and each slat; shadows by projected silhouettes; dry
  brush as tufts of strokes with broken `dash`; one colour only (a yellow taxi). Weak: sticker-like
  figure, too many stripes.
- **Ligne claire (48)** — perspective with f=750 from a script; every form a polygon with a 3 px
  outline in one call; flat colours; shadows by projected silhouettes on multiply. Weak: the hero's
  face too small, the accent went to the awning.
- **1930s cartoon (99)** — rubber-hose limbs as 17 px `smooth` strokes without joints; gloves from
  one function; pie-cut pupils; flat greys; film: stamp + blur + noise + scratches, dust, vignette,
  a shifted frame. Weak: lines steadier than the period.
- **Kawaii stickers (100)** — white die-cut = fill + a closed stroke 2r wide; cut line = stroke
  2r+5; volume by airbrush along the edge; a peeled corner = the geometry mirrored across the fold
  line. Weak: clean edges, a straight peel. 0.5: round `grow` also makes the die-cut.
- **Graffiti (97)** — letter polygons on their own layers; extrusion from 14 offset copies; fades by
  airbrush on `alpha_lock`; drips only where a drop does not land in another letter; bricks +
  `relief`, mortar on multiply through the paint. Weak: vector outlines.
- **Art Deco poster** — symmetric geometry from a script, stepped gradients as hard bands, single
  vanishing point, clipping layer for stripes, geometric font. Weak: frontal static subject.
  0.3: `letter_spacing` for spaced capitals.
- **Art Nouveau** — whiplash contours with sinusoidal pressure, pastel flat fields, halo mosaic,
  mirrored ornament. Weak: fan-like regular tail, duplicated eye rings on overlaps. Keep overlapping
  rings on separate layers; break symmetry.
- **Psychedelic poster** — complementary colours edge to edge, ripples `r + A·sin(nθ)`, thick wavy
  outlines, letters as separate rotated/stretched text objects along an arc. Weak: crowded bottom.
- **Op art (35)** — a phase field φ(x,y) monotonic in y; level lines are the borders of stripe
  polygons; minimum period 11 px, stripes thinner than 2.6 px dropped. Weak: the sphere reads as a
  lens.
- **Bauhaus abstraction** — one multiply layer per colour, diagonal balance, staff lines, wobbly hand
  lines (stabilization 0, dense points). Weak: empty corner. Use vector layers for editable geometry.
- **Memphis (49)** — terrazzo of 1,500 small polygons; squiggles with a `smooth` marker; zigzag =
  black 40 + colour 26 px; hard offset shadows; hatch, halftone. Weak: vector-clean.
- **Y2K chrome (112)** — chrome as contour bands of the reflected height Ry = 2y·√(1−x²−y²),
  coloured from an environment map; plastic = offset copies + a translucent gradient; gloss in an
  intersection of selections; floor reflection from a stamp. Weak: a mechanical horizon on the chrome.
- **Glitch (52)** — `stamp_visible` → ~50 slices (selection + nearest translate) → `channel_shift`
  in bands → pixel sort as `antialias=false` shapes → 8/16 px blocks → scanlines. Weak: silhouette
  figures. Shift opaque stamps: transparent holes give rainbow bands.
- **ASCII art (53)** — a 160×60 Consolas grid; glyph and colour per cell from an SDF scene; one
  multiline `paint_text` per ANSI colour; two blurred glows on screen, hatch scanlines. Weak: thin
  smoke, an even carpet of dots.
- **Synthwave** — gradient sky, striped sun via cleared bands, perspective grid, glow on every neon
  layer, chromatic aberration, grain on overlay. Weak: flat car, aberration only per layer.
  0.3: `stamp_visible` then `channel_shift` for the whole image.
- **Tattoo flash** — hard round 8–9 px outlines, fill → shading in shape selection → outline →
  white highlight dashes; whip shading with textured pressure 1→0. Weak: flat panther head, puffy
  sails. 0.3: `stipple` with a gradient for whip shading.

## Maps, diagrams and technical drawing

- **Blueprint** — Prussian ground with mottling, line weights by role, dashed hidden / dash-dot
  centre lines, section hatching in selections, monospace title block. 0.3: `dash` and `hatch`
  replace thousands of scripted segments.
- **Technical cutaway (103)** — hull = a surface of revolution seen through a camera, ~3,000 lit
  facets; cut by a plane; the interior by the painter's algorithm inside the opening's selection;
  section hatch 45°; light beams = gradient with alpha. Weak: box-like bridge, schematic figures.
- **Sea chart (50)** — rhumb lines from a script, erased over land; 4–5 offset waterlines with
  falling opacity; a watercolor band along the coast (`edge_darkening` .9–1); `letter_spacing`;
  folds as a pair of lines. Weak: roses and cartouche too clean, a cartoon sea serpent.
- **Metro map (102)** — octilinear grid of 60 px, arcs R=52; 14 px lines without pressure; each
  label placed from 12 candidates with overlap checks on Pillow font metrics. Weak: one label at
  45°, a token airport.
- **Paint by numbers (101)** — ~340 areas with colour numbers; each number at the deepest point of
  its area (label map + erosion); the painted part with jittered borders and dabs; `relief` by alpha
  and luminance. Weak: overlapping labels, green rims.

## Mosaic, glass, paper, geometry

- **Roman mosaic** — tiles laid along contour rows (andamento), black outer row per figure,
  background halo rows then straight courses, guilloche border, missing tiles; ~26k tile polygons in
  ~14 s via a plan. Weak: thin dolphin snouts, mirrored composition.
- **Stained glass** — each pane = polygon with fill + 6–7 px lead outline in one call, Voronoi
  background, inner glow (blurred copy, screen), grisaille strokes, stone frame. Weak: busy wing
  coverts, dark flat stone.
- **Girih (43)** — decagons and bowties (edge 64 px), Hankin straps at 54°, an over/under walk of the
  straps (292 crossings, no conflicts); `bleed` .6; `relief` on the straps. Weak: vector-clean,
  highlights like snow.
- **Tessellation (60)** — hexagon lattice; one edge profile shared by two tiles, smoothstep
  deformation in x, so no gaps; `bleed` .8; every edge stroked once. Weak: identical mirrored turtles.
- **Layered paper-cut** — 6–8 silhouette layers pale→saturated, each with its own drop shadow
  growing toward the front, paper noise per layer. Weak: identical fish. 0.3: `relief(alpha)`.
- **Quilling (57)** — spirals from a script: Archimedean core → teardrop/marquise; a strip = soft
  shadow + 4.2 px band + 1.3 px light edge; three planes with shadows blurred 5/6/9. Weak: empty
  left side, look-alike flowers.
- **Low-poly** — Delaunay facets from a script, colour from one light direction, sky/water as facet
  gradients, mirrored reflection. 0.3: `bleed=.6` instead of 1 px same-colour outlines.
- **Isometric diorama** — `x = cx + (gx−gy)·w/2`, `y = cy + (gx+gy)·w/4 − z·w/2`, back-to-front
  depth sort, three tones per material, AO blur on multiply, glow on screen. Weak: flat windmill
  blades, awning hiding windows.
- **Pixel art** — art-pixel grid (e.g. 6×6), `antialias=false`, 16-colour palette, dithering.
  0.2 needed a comb fix for polygon edges and hand-built dithers; 0.3: exact `[x0,x1)` polygons,
  `paint_pattern(kind="dither", cell=6)`, `resample="nearest"`, `export(scale=...)`.

## Crafts and materials

- **Ebru marbling (44)** — a numpy simulation on polygon boundaries: Jaffer drop, comb
  z·λ/(d+λ), stylus as Gaussian advection; ~1,360 polygons in one `actions_file`, no strokes.
  Weak: round "plate" halos.
- **Batik (45)** — three multiply dye layers; a motif: selection → cream → `shrink 3` → dyes (the
  wax line stays); craquelure from 2,000 branching walks. Weak: a flat bird, a regular parang.
  0.5: `shrink` is round.
- **Mola (93)** — an exact distance transform in numpy + marching squares; cloth layers bottom-up,
  holes by `clear` in a selection; channels by eraser every 24 px; triangle fillers; stitches by
  `dash`; `relief(alpha)`. Weak: a round patch near the tentacles.
- **Patchwork quilt (109)** — patches in the quilt's (u,v) on a tent-like surface in perspective
  (~12k actions); calico stipple, gingham hatch along local axes, polka-dot halftone; quilting
  `dash`; puff by `relief` of the blurred quilting. Weak: prints do not follow the folds.
- **Fair Isle knit (56)** — at most 2 colours per row; a stitch = two V legs (`smooth` round,
  hardness .62) + highlight + underlay + fuzz; jitter in size, angle and colour; a fold by
  compressing the grid with tanh. Weak: the fold has no ridge.
- **Cloisonné (110)** — compute in the plate's coordinates and project; cells = linear gradient in
  a selection; wire = outline + gradient + `relief(alpha)` + shadow; rim of 1,900 quads; shells by
  stipple. Weak: grid-like scales, a centred plate.
- **Marquetry (55)** — 3D planes in perspective; each piece: selection → wood fill → grain
  converging to the vanishing point → hatch along the grain → scorch at the edge → 1–1.4 px seam.
  Weak: vector-clean, a busy back wall.
- **Pyrography (54)** — eccentric pencil rings + noise; each burn tone on its own multiply layer;
  hatch along the fur + stipple with a gradient; scorch halo = copy + blur 5–7. Weak: a rectangular
  elk, flat water.
- **Bas-relief (58)** — a grey height map; light = a shifted copy of the map on add + the original
  on subtract, merged by `stamp_visible`; modelling on overlay, a cast shadow; limestone, dirt,
  lichen. Weak: plastic-smooth forms. 0.5: `relief(depth=4..20)` lets a gentle height map read.
- **Plasticine (108)** — `smooth` polygons; volume by airbrush along the outline with pressure from
  the light; fingerprints = pairs of dark/light arcs; depth of field by blurring layers 9/4.5/2.2;
  stamp + `relief`. Weak: CGI-smooth surfaces.
- **Latte art (111)** — nested foam polygons with a Gaussian drag along the pour axis; clipped to
  the liquid, blurred; crema = radial gradient + stipple + tiger spots; the cup as a body of
  revolution (~12k polygons). Weak: the pattern reads as an owl.

## Historical, folk and decorative

- **Illuminated manuscript** — vellum gradient + stains, gold = gradient on alpha lock + white
  highlights + dark outline, lapis/vermilion fields, knotwork with correct over/under, blackletter
  justified by scale. Weak: vector-clean, snake-like dragon. 0.3: `align="justify"`, `anchor="ls"`.
- **Egyptian fresco** — registers on ground lines, composite-view figures from a canon grid, flat
  mineral colours with dark-red outline in one call, invented glyphs, cracks and flaked plaster.
  Weak: clean contour, cloned musicians. Vary poses; roughen contours.
- **Black-figure vase (41)** — the vase as a body of revolution: x = cx + r·sinθ,
  y = v + .085·r·cosθ, so bands and figures curve with the surface; incisions through a mask;
  ~2,300 Lambert-shaded quads. Weak: a mirror-symmetric vase, unclear wrestlers.
- **Persian miniature (37)** — flat polygons with outline in one call; spongy jagged rocks from a
  script; tiles = hatch [60,−60] + gold halftone; a cypress breaking the frame. Weak: the night does
  not read, stiff figures.
- **Heraldry (51)** — mantling lobes as Catmull-Rom curves with serrated edges and spirals; shading
  in a selection shrunk by 2 px; gold on `alpha_lock`; the motto letter by letter, rotated along the
  tangent. Weak: mantling like tentacles, schematic otters.
- **Khokhloma** — black lacquer ground, gold by gradient on alpha lock, "travka" commas (pressure
  0→1 drop heads), berries with highlight, 16-fold border repeat, lacquer sheen. Weak: polygonal
  body, sparse ornament. 0.3: `smooth` silhouettes and curls.
- **Madhubani (39)** — double outline: black 6–11 px + a paper-coloured inner stroke; hatch and
  crosshatch in polygons; rims `dash=[0,10]`; ~210 fillers placed by an occupancy mask. Weak: a
  straight pole of a trunk, vector lines.
- **Minhwa (89)** — fills offset ±2 px from the outline; ink outline with a pressure wave and
  breaks; stripes on clipping layers; pine needles in fans; ageing by multiply + `relief`. Weak: the
  tiger became a cartoon cat — study the folk tiger's grin and pattern first.
- **Rimpa screen (90)** — ~290 overlapping gold-leaf squares with tone jitter and dark seams;
  tarashikomi = watercolor (`edge_darkening` 1) blots of 30–170 px; ~500 grass threads; panel folds
  on multiply/screen. Weak: a visible gold grid, naturalistic grass.
- **Truck art (91)** — reflective tape: polygons split into 4 mirror tones at a random angle;
  scallops along normals; a chain of ellipse rings; enamel by stamp + `relief` + noise. Weak: vector
  edges, an overloaded peacock.
- **Tingatinga (92)** — smooth polygons → pattern in a selection of the same shape → black closed
  outline; limbs = black stroke w+12 under the colour stroke w (hardness 1); nests of hatch
  [55,−55]; gloss. Weak: outlines cleaner than hand-painted enamel.
- **Cubism** — facets from polygon selections + gradients, shadow/light wedges on multiply/screen
  cutting through objects, rotated second views, collage text. Weak: vector look, rain-like
  hatching. 0.3: `hatch` with jitter per facet.
