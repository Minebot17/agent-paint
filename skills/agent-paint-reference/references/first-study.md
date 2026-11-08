# First full Agent Paint study, E — 2026-10-18

The original cover was repainted at 1110×1620 through actual Agent Paint MCP operations. Whole-image
and matching detail comparisons use the same source and the previous CSP export D. The user had
preferred D's face to C and subsequently explicitly preferred D over E overall. D remains the
preferred artistic baseline. The user did not give reasons for this preference; the observations
below are the agent's visual assessment. This is one study with engine, skill and execution
conditions changed together. It does not isolate a causal effect of the skill/renderer.

Open [examples/face-d-e.png](examples/face-d-e.png) for face work and
[examples/materials-d-e.png](examples/materials-d-e.png) for hair/cloth work. Columns are source, D, E.

Observed results:

- E has cleaner lid/mouth edges and detailed warm irises, but larger circular irises and a more
  overtly graphic face. This does not establish greater attractiveness or a more faithful expression.
- Smooth plane boundaries and pressure alone did not reproduce the source's broken printed edges.
  Hair still reads in broad ribbons/helmet-like groups. More fine strands did not resolve the mass.
- A smooth sleeve variant became a curved band. Reconstructing the shoulder connection and using
  angular planes changed it visibly, but folds remain stylized and their material is not solved.
- A hand variant with a simplified outer contour lost the palm and became hook-like. It was rejected.
  Restoring individual fingers and the palm mass reduced that topology error. Fingers still require
  stronger joint/overlap construction; clean outlines did not establish anatomy.
- The background is denser than D, but repeated architectural cells remain regular. Density is not
  equivalent to the source's spatial structure or etched rendering.
- The accepted face expression layer was retained while modifying its underlying cheek plane.
  Earlier face, hand and sleeve variants remain in the project, with failed variants hidden.

Cleaner edges and greater background density did not make E the user's preferred result. Preserve
the character of D when testing the new engine; do not treat mechanical precision as artistic progress.

Next painting decisions should follow these visible limits. In particular, use a complete material
fragment to test connected planes and locally broken edges before repeating smooth strips. Use
palm mass and bounded gaps to validate a hand before detailing its contour. Keep a successful face
variant instead of repeatedly changing it to force a general improvement claim.

Full evidence and authored MCP plans: engine repository `docs/experiments/2026-10-18-reference-e/`.
Current experience journal: `docs/painting-experience.md` in that repository.
