# Rendering quality for 117 and 1800

The island editor and the viewer draw 117's and 1800's islands at a quality the user picks, so they run on modest
graphics cards. The older games (2205, 2070, 1404, 1701) draw at their best, because they draw easily.

The module is `render/quality.py` (no Qt, no GPU). The setting is `Settings.quality`: per game, a preset's name or a
Custom record, with Medium as the default. The menu is Rendering > Rendering quality in the island editor. Nothing
here was checked in the game: the values the games' files give are read, the rest are ours.

Note: written on 2026-11-13.

## What the games define

**117** keeps its graphics options in `<Documents>/Anno 117 - Pax Romana/config/engine.ini`, `RenderEngine`:
`QualitySetting` (a preset by name, `UltraHigh` on the author's machine) and `Custom` with a level 0 to 4 per area
(`Water`, `Lighting`, `Shadow`, `Terrain`, `Texture`, `AA`, `Object`, `ViewDistance`, `Raytracing`,
`FeedbackQualityLevel`). Which levels a named preset sets is in the game and is not read **[open]**.

The data side is `data/base/config/engine/enginesettings/default.xml`. Per quality level (`QualityLevel0` .. `4`) it
holds `ViewDistance`, `ObjectViewDistance`, the cluster distances, the cache factors, `MinimumObjectSizeInPixels`,
`FadeDistanceMultiplier`, `LODBias` and `LODDistances`. It also holds the global `LODAreas` and `LODMinDistances`
(camera.md 3). The vanilla values:

| Level | `ViewDistance` | `ObjectViewDistance` | `MinimumObjectSizeInPixels` | `LODBias` |
|---|---|---|---|---|
| 0 | 450 | 375 | 16 | 0.18 |
| 1 | 500 | 425 | 15 | 0.5 |
| 2 | 550 | 450 | 14 | 1.0 |
| 3 | 650 | 525 | 13 | 1.5 |
| 4 | 750 | 600 | 12 | 2.5 |

**1800** has the same file (`data/config/engine/enginesettings/default.xml`, and `extended.xml` for the longer view)
with five levels. It has no `LODBias` (its level of detail goes by `LODDistances`). It adds
`PropFadeDistanceMultiplier`. Its `MinimumObjectSizeInPixels` are 12, 12, 10, 8, 6.

## The presets

A preset takes the game's values of one quality level (**[game]**: `LODBias`, `MinimumObjectSizeInPixels`; read from
the install's file where the code has it, `quality.VANILLA` otherwise). It sets the rest itself (**[ours]**). Our
presets map onto the game's levels by name: Low = level 0, Medium = 1, High = 2, Ultra = 4. The game's "very high",
level 3, has no preset of ours. Custom can set any value.

| Value | Low | Medium | High | Ultra | Mark | Where it applies |
|---|---|---|---|---|---|---|
| level | 0 | 1 | 2 | 4 | [game] | which level's values below |
| `lod_bias`, `min_pixels` | 0.18, 16 | 0.5, 15 | 1.0, 14 | 2.5, 12 | [game] | both island looks and the game's shaders (`cull.SLodRules`) |
| `render_scale` | 0.6 | 0.8 | 1.0 | 1.0 | [ours] | the island view's scene size (`Renderer.render`) |
| `shadow_size` | 2048 | 2048 | 4096 | 4096 | [ours] | the game frame's atlas of four cascades (`CGameFrame.m_shadow_size`), the view's own map at half of it |
| `shadows` | on | on | on | on | [ours] | Custom can switch them off |
| `shadow_cascades` | 2 | 3 | 4 | 4 | [ours] | the sun's cascades the game frame draws casters into and the shading reads (`cUsedShadowPartitions`, `CGameFrame.set_quality`; see the note below) |
| `view_distance` | 900 | 1600 | 3000 | none | [ours] | objects farther from the eye not drawn (`SLodRules.far`); the game's `ObjectViewDistance` is for its own camera, which never zooms out over a whole island |
| `max_texture` | 512 | 1024 | 2048 | as stored | [ours] | the game's shaders upload a map from its first level at most this large (`gamemaps.smaller`); the view's own maps are halved to it (`desktoprender.shrink`) |
| `tessellation` | off | on | on | on | [ours] | the near terrain from the height pages (`desktopgame`) |
| `bake_size` | 128 | 256 | 256 | 256 | [ours] | the game's bake of a node, texels a side (`terrainbake.CBake(size=)`, made with the frame); the view's own fallback bake stays at `terraindraw.SLICE_SIZE` |
| clouds, haze, SSAO, SSR, post, eye adaptation | off | SSR off | on | on | [ours] | switches for phases 6 to 8 and the viewer's 117 frame; the island view draws none of them yet |
| `particles` | 8,000 | 20,000 | 40,000 | 40,000 | [ours] | the particle quads a frame draws, nearest first (`particledraw`) |
| `cloth` | 8 | 24 | 48 | 64 | [ours] | for the viewer's cloth (not read yet) |

`cUsedShadowPartitions` is a constant of the shaders, not a compile switch. The cascades are still fitted four, and
the shadow ends where the last used one ends.

The shaders' `SHADOW_MAP_CASCADE_COUNT` (4, compiled in) stays. The count the quality sets is the run-time
`cUsedShadowPartitions`, which the engine's own quality sets too (shader-compile.md, `cbShadow`). The casters smaller
than a texel of a cascade are left out of it [ours]. The engine's own test (`model_culler.fx`, `isSmall`) holds the
caster's light-space box against `cShadowCullSize` "pixels" (`areaLS * cShadowMapSize.x * cShadowMapSize.y / 4`).
For a round caster that is our test with a cull size of 4 **[E]** (the engine's value is not in a file).

**Changing the quality.** The level of detail, the draw distance, the render scale, the shadow cascades and the
particle budget apply from the next frame. The shadow map, the maps' level, the tessellation and the bake size are
made with the game frame. A change of one of them (`quality.remakes`) makes the look "The game's own shaders" anew.
Its parts compile again from the shader cache and read their maps again. That takes seconds on a small island and a
minute or two on a continental one. A higher `max_texture` in the view's own look takes effect for the maps uploaded
after it (the pixels of those already up were let go).

## What 1800's path needs (the viewer's forward pass)

This is what 1800's path needs in `viewer/forwardpass.py`. The end of this section says how much of it is built. The
viewer's menu is described in `viewer.md` "Rendering quality".

1. `q = quality.resolve("anno1800", settings.quality.get("anno1800"), text)` with `text` the install's
   `data/config/engine/enginesettings/default.xml` (read once in the background); `q.preset` for the menu.
2. The level of detail: 1800 picks by `LODDistances` per quality level (not by screen size). Take
   `quality.game_levels(text)[q.level]["LODDistances"]` (the table is left out of `game_levels`' plain values: read it
   beside) and drop instances past `q.view_distance`. Leave out those smaller than `q.min_pixels` on screen.
3. `q.render_scale` on the forward frame's size, `q.shadow_size` for its shadow map, `q.max_texture` for the maps it
   uploads (`gamemaps.smaller` works on any `DdsFile`), `q.post` / `q.ssao` for its post chain, `q.particles` and
   `q.cloth` as the budgets of its particles and cloth.
4. A View > Rendering quality menu in the viewer that writes `settings.quality[game]` as the island editor's does
   (`workbench.set_quality`).

**Built in the viewer:**

- item 4 (View > Rendering > Rendering quality, `viewer/renderingmenu`);
- the effect switches (`clouds`, `haze`, `post`, `eye_adaptation`, `shadows`) for 117's frame and 1800's path
  (`nativelook.with_quality`);
- for 1800: `render_scale` (`forwardpass.drawn_size`), `shadow_size` and `shadow_cascades` (the cascades' map and
  the count read, `forwardpass.shadow_quality`, `shadowforward.cascades(size=, used=)`);
- for 117's own shaders in the viewer: `render_scale`, `shadow_size` and `shadow_cascades` (`gamepass`:
  `forwardpass.drawn_size`, `CGameFrame.set_quality`);
- `max_texture` for every map the viewer loads, in any look, under its own 1024 (`ModelFiles.texture(most=)`).

**Left:**

- items 1 and 2 (the game's level values and `LODDistances`: a viewer draws one model, so there is nothing to cut);
- the particle and cloth budgets (one model's effects and cloths);
- `ssao` (1800's path draws none).

Note: built on 2026-11-13.

## Measured

See island-117-view.md "Speed", part 3, for the table (Low against Ultra on Egypt's quadrant and
`roman_island_medium_03`, the look "The game's own shaders" on Direct3D 12).

Tests: `tests/render/test_quality.py` (the presets with a file's levels and without, the older games, Custom kept, the draw
distance, what makes a frame anew, a map's coarser level and the view's halved maps; installed: 117's and 1800's
files give five levels each).

Note: measured on 2026-11-13.
