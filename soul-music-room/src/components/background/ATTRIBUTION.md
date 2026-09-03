# Mineradio particle background attribution

`MineradioParticleBackground.tsx`, `useMineradioParticleEngine.ts`, and
`mineradioWallpaperPulseShaders.ts` contain an adaptation of the Three.js
`WALLPAPER PULSE` particle implementation from:

- Project: [XxHuberrr/Mineradio](https://github.com/XxHuberrr/Mineradio)
- Original author / repository owner: XxHuberrr
- Source file: `public/index.html` (preset 5)
- Source commit: `6b130103f759e5dcd1e133700071c8216b8fa5a6`
- Original project license: GNU General Public License v3.0 only

The adapted source includes the 183 x 183 particle grid, point-sprite texture,
simplex-noise field, aurora-ribbon and depth-spark formulas, cover-color sampling,
depth-based point sizing, camera, and DPR limits. Electron APIs, the player UI,
lyrics, shelves, gestures, and desktop-window integrations were not copied.

React-specific changes are limited to deterministic one-time particle seeding,
component lifecycle cleanup, paused rendering while the document is hidden, smooth
play-state and cover transitions, and optional low-amplitude pointer parallax.

No Mineradio name, logo, or brand artwork is used by the rendered component.
This adaptation is distributed under the repository's `GPL-3.0-only` license; see
[`../../../LICENSE`](../../../LICENSE).
