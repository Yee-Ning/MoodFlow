# Liquid Glass Attribution

The SVG refraction pipeline and displacement-map geometry in this directory are
adapted from the GPL-3.0 project [XxHuberrr/Mineradio](https://github.com/XxHuberrr/Mineradio),
copyright (C) 2026 XxHuberrr.

Source checkpoint: commit `6b130103f759e5dcd1e133700071c8216b8fa5a6`.

Extracted from `public/index.html`:

- `generateControlGlassDisplacementMap(width, height, radius)` and its 7% edge geometry.
- The red/green/blue `feDisplacementMap` passes, negative chromatic offsets,
  screen blending, and final `0.5` Gaussian blur.
- The saved panel/button background, filter, radius, and shadow baselines.
- Resize-driven displacement-map updates and runtime SVG backdrop-filter detection.

MoodFlow modifications:

- Split the implementation into React provider, surface, filter, button, and hook modules.
- Added unique filter IDs, per-instance map sizing, RAF-throttled `ResizeObserver`
  updates, Object URL cleanup, reduced-motion/transparency handling, and an explicit fallback.
- Limited the full Mineradio-derived path to the bottom player and its nested controls.
- Excluded Mineradio branding, logos, Electron code, music services, and business state.

This adapted implementation is distributed under GPL-3.0-only. See the project
root `LICENSE`. Keep this notice and the license with redistributed source or builds.

