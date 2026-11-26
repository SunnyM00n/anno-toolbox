# Rendering quality for 117 and 1800

The island editor and the viewer draw 117's and 1800's islands at a quality the user picks, so they run on modest
graphics cards. The older games (2205, 2070, 1404, 1701) draw at their best, because they draw easily.

The views set their quality as 117's own graphics options do: the game's quality presets, and Custom with a level per
area of the game. Without a choice of the user's they follow the player's own Anno 117 settings. The module is
`render/quality.py` (no Qt, no GPU). The menu entries and the Custom window are `suite/qualitydialog.py`. The setting
is `Settings.quality`: per game, nothing (the player's settings), a preset's name, or a Custom record. The menu is
Rendering > Rendering quality in the island editor and View > Rendering > Rendering quality in the viewer. Nothing
here was checked in the game: the values the games' files give are read, the rest are ours.

Marks: **[game]** is read from the game's files, texts or log; **[inf]** is inferred from them; **[ours]** is a value
of ours where the game's is not known or does not fit the view; **[I]** is a rule of ours; **[open]** is not read.

Note: written on 2026-11-13; the game's graphics settings were added on 2026-11-26.

## The game's graphics settings (117)

### Where they are kept

The player's settings are `<Documents>/Anno 117 - Pax Romana/config/engine.ini`, a JSON file, block `RenderEngine`
**[game]**:

- `QualitySetting`: the preset, one of `Low`, `Medium`, `High`, `VeryHigh`, `UltraHigh` and `Custom` (the enum's
  names in the game's order).
- `Custom`: a level per area, `Water`, `Lighting`, `Shadow`, `Terrain`, `Texture`, `AA`, `Object`, `ViewDistance`,
  `Raytracing` and `FeedbackQualityLevel`.
- `Upscaling` (the upscaler and the mode of each: `Upscaling`, `DLSS`, `XeSS`, `FSR`, `DLSSModel`,
  `DLSSRayReconstruction`) and `FrameGen` (`Reflex`, `XeLL`, `AntiLag`, `FrameGeneration` and a mode per vendor).
- `ExtendedCameraDistance`: the option "Increased camera distance". With it the engine reads
  `enginesettings/extended.xml` instead of `default.xml` (camera.md).
- `EnableHDR`, `HDRMaxLuminance` and `HDRExposure`; `Window.VSync`, `Window.MaxFPS` and the window's size.

The game ships its own start file, `data/base/config/game/engine.ini`. It sets `QualitySetting` `Custom` with Water 2,
Lighting 3, Shadow 4, Terrain 2, Texture 2, AA 0, Object 4, ViewDistance 3, Raytracing 0 and FeedbackQualityLevel 2
**[game]**.

The renderer applies 23 settings in this order: `Water`, `Lighting`, `Shadow`, `Terrain`, `Texture`, `AA`, `VSYNC`,
`Object`, `ViewDistance`, `Raytracing`, then the upscaling and frame generation keys. The game's log writes each change
as `UpdateEngineSettings:   Shadow: 3 -> 4` **[game]**. `FeedbackQualityLevel` is not among them: the game's own
side reads it (below).

### The options menu

The Graphics page of the options lists these entries **[game]** (texts from `texts_english.xml` and `texts_german.xml`
by their names):

| Area (`engine.ini`) | Entry (en / de) | The game's tip | Levels |
|---|---|---|---|
| (`QualitySetting`) | Graphics Quality / Grafikqualität | "Choose a predefined graphics setup." | Low, Medium, High, Very High, Ultra High, Custom |
| `Water` | Water Quality / Wasserqualität | the level of detail of water and reflections | 0 to 3 |
| `Lighting` | Lighting Quality / Beleuchtungsqualität | the lighting quality | 0 to 3 |
| `Shadow` | Shadow Quality / Schattenqualität | the shadows' quality | 0 to 4 |
| `Terrain` | Terrain Quality / Terrainqualität | the terrain's complexity | 0 to 2 |
| `Texture` | Texture Quality / Texturqualität | more detailed textures | 0 to 2 |
| `AA` | Anti-Aliasing | how polygon edges are smoothed | 0 to 3 |
| `Object` | Object Quality / Objektqualität | the complexity of buildings and vegetation | 0 to 4 |
| `ViewDistance` | View Distance / Sichtweite | the distance at which buildings, units and vegetation appear | 0 to 4 |
| `Raytracing` | Raytracing | the ray-traced quality, with a card that has hardware ray tracing | 0 to 3 |
| `FeedbackQualityLevel` | Crowds / Menschenmengen | how many people the streets show | 0 to 4 |

The page also has the upscaler (Off, DLSS Super Resolution, FSR Upscaling, Xe Super Sampling, with their modes from
Ultra Performance to Native AA and DLAA), frame generation, the low-latency modes, "Disable Rain and Fog" (weather
effects), the increased camera distance, the window mode, the resolution and the adapter. The texts also hold 2x, 4x
and 8x MSAA and TAA for anti-aliasing, and an older "Shader Quality" entry that no setting names any more.

The top level of each area is the level the player's file and the game's log show at Ultra High (3, 3, 4, 2, 2, 3, 4,
4, 3, 4 in the order of the table). That it is the top is **[inf]**. The game accepts 0 to 2 for `Texture` and says
so when a file holds another value **[game]**. The names of the single levels in each list are **[open]**.

### The presets

**Ultra High** sets every area to its top **[game]**: the player's `engine.ini` and the game's log at Ultra High hold
exactly these levels. The `Custom` block of the file holds the levels the game last applied, whatever the preset
**[inf]**: it changed with them in the log.

**Low to Very High** set their levels in the program, not in a file. They were not read **[open]**.

Note: the author's log showed Water 3, Lighting 2, Shadow 3, Terrain 2, Texture 2, Object 3, ViewDistance 3 and
Raytracing 2 at Ultra High up to the first start of update 2.1 (2026-09-22), and the tops above from the next start
on. Read on 2026-11-26 from 326 logs.

### What each area changes

| Area | What changes | Mark |
|---|---|---|
| `Lighting` | which shipped graphics set the shaders are compiled with. The 20 sets are 10 pairs (deferred, and forward with multisampling): none with SSAO; SSAO with 2 samples; SSAO, local light shadows and SSR with 4 samples; and these with the local ambient and specular occlusion besides. Each but the last also comes with ray-traced light, and some with ray-traced reflections | the sets [game], their order as four levels [inf] |
| `Shadow` | the sun's cascades the shading reads (`cUsedShadowPartitions`, 1 to 4) and the shadow map | the constant [game]; the counts and sizes per level [open] |
| `Terrain` | the terrain shaders' `TESSELLATION_QUALITY` 0 to 2 (three lists shipped; a fixed factor of 2 at 0, 3 at 1 and 2) and the size of the terrain's baked ground cache | the defines [game]; that the level picks them [inf]; the cache's sizes [open] |
| `Texture` | the texture variant a map is loaded as (`<name>_<level>.dds`, the highest first) and the video memory the texture streaming may use | [inf]; nearly every map ships one variant (`_0`, 38,958 of them), so the level works by the streaming |
| `Object` and `ViewDistance` | one `QualityLevel` record of `enginesettings/default.xml` (0 to 4): `ViewDistance` (the far plane), `ObjectViewDistance`, the cluster distances, the cache factors, `MinimumObjectSizeInPixels` and `LODBias` | the record's values [game]; which area picks the record [open] |
| `Water` | the water's detail and its reflections (the game's tip) | [open] |
| `AA` | the edge smoothing: TAA or multisampling (the shipped sets have 1, 2 and 4 samples) | which level is which [open] |
| `Raytracing` | the ray-traced light (`RTGI_QUALITY` 1 to 3, six lists shipped) and ray-traced reflections | the defines [game]; the mapping [inf] |
| `FeedbackQualityLevel` | the units the game spawns: `UnitCountFactorByQualitySetting` {0.3, 0.6, 1, 1.2, 1.5} and the clip distances of `ClippingInfos` per level (`QualityLevelClipping`: Low .. UltraHigh), asset 2002022 `FeedbackParametersGlobal` in `assets.xml` | [game] |

The upscalers draw the scene at a share of the screen and scale it up: this is the only way the game lowers its
resolution. It has no render scale of its own **[game]**: no such key or text exists.

### What the views cannot reproduce

- **Water**: the view draws the game's sea and inland water at one detail, and the level's values are not known.
- **Anti-aliasing**: the view keeps its own edge smoothing, and the game's levels are not known.
- **Raytracing**: the view has no ray tracing (the shaders' graphics set without it, `shadercache.view_graphics_set`).
- **Crowds**: the view draws no units.
- **Upscaling and frame generation**: the vendors' libraries are the game's, and the view does not load them.

The four areas are kept with their levels. A Custom window shows them with a tip that says the view does not draw them.

## The plan, as built

Our settings are the game's own: the same presets with the same names, the same areas with the same levels, and the
game's values where the view has the feature.

- **The default** is the player's own settings when 117 is installed: `QualitySetting` and the `Custom` block of
  `engine.ini`, read only (`quality.player_choice`). The file is never written. Without it the views draw at Medium.
- **A preset** sets the area levels (`quality.preset_levels`). Ultra High is the game's. A lower preset takes its step
  in the list (Low 0, Medium 1, High 2, Very High 3) up to the area's top **[I]**, until the game's are read.
- **Custom** keeps a level per area and our render scale (`CQualityDialog`). A preset chosen there sets every area, and
  an area set by hand makes the quality Custom, as in the game.
- **The levels become the values the renderers read** (`quality.derive`; the `SQuality` fields keep their names):

| Area | Levels | What the views do | Mark |
|---|---|---|---|
| `Lighting` | 0 to 3 | SSAO, clouds, haze and eye adaptation from 1; SSR from 2 | SSAO and SSR as the graphics sets [inf]; the rest [ours] |
| `Shadow` | 0 to 4 | cascades 2, 3, 4, 4, 4; the atlas of four cascades 2048, 2048, 2048, 4096, 4096 | [ours] |
| `Terrain` | 0 to 2 | tessellated from 1; the bake 128, 256, 256 texels a node | [ours]: the game tessellates at every level |
| `Texture` | 0 to 2 | the largest map side 512, 1024, as stored | [ours] |
| `Object` | 0 to 4 | `LODBias` and `MinimumObjectSizeInPixels` of that `QualityLevel` record; particles 8,000 to 40,000; cloth 8 to 64 | the record's values [game]; the record by `Object` [I]; the budgets [ours] |
| `ViewDistance` | 0 to 4 | `level`: that record's distances (the clusters' start, 1800's `LODDistances`); the objects' draw distance 900, 1600, 3000, 6000 m and none | the distances [game]; the record by `ViewDistance` [I]; the draw distance [ours]: the game's `ObjectViewDistance` is for its own camera, which never zooms out over a whole island |
| the render scale | 100 to 50 % | of the view's pixels the scene is drawn at | [ours]: 100 % in every preset, as the game keeps its resolution |

**Settings.** `Settings.quality[game]` is missing (the player's settings), a preset's name in the game's spelling, or
`{"preset": "Custom", "levels": {area: level}, "render_scale": ..., "shadows": ...}`. A level out of range or not a
number takes Medium's. A settings file of a released Toolbox (0.3) kept `low`, `medium`, `high`, `ultra` and a Custom
record with its `base`: they read as Low, Medium, High and Ultra High, and the record as its base's levels with its
render scale and shadow switch (`quality.map_released`, one table).

What is left: the levels of Low to Very High, the shadow and texture values per level, which area picks the
`QualityLevel` record, and the water, anti-aliasing and ray tracing in the view.

Note: built on 2026-11-26; not checked against the game's look.

## What the games define

## The engine settings' quality levels

**117** keeps its graphics options in `engine.ini` (above). The data side is `data/base/config/engine/enginesettings/default.xml`. Per quality level (`QualityLevel0` .. `4`) it
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

## Where each value applies

`quality.derive` turns the area levels into these values (the plan above says which level gives which). A value of the
game's quality level is read from the install's file where the code has it, `quality.VANILLA` otherwise.

| Value | From | Where it applies |
|---|---|---|
| `level` | `ViewDistance` | which `QualityLevel` record's distances apply (the clusters' start, 1800's `LODDistances`) |
| `lod_bias`, `min_pixels` | `Object`'s record | both island looks and the game's shaders (`cull.SLodRules`) |
| `render_scale` | Custom | the island view's scene size (`Renderer.render`) |
| `shadow_size` | `Shadow` | the game frame's atlas of four cascades (`CGameFrame.m_shadow_size`), the view's own map at half of it |
| `shadows` | Custom (a settings record) | the shadows switched off |
| `shadow_cascades` | `Shadow` | the sun's cascades the game frame draws casters into and the shading reads (`cUsedShadowPartitions`, `CGameFrame.set_quality`; see the note below) |
| `view_distance` | `ViewDistance` | objects farther from the eye not drawn (`SLodRules.far`) |
| `max_texture` | `Texture` | the game's shaders upload a map from its first level at most this large (`gamemaps.smaller`); the view's own maps are halved to it (`desktoprender.shrink`) |
| `tessellation`, `bake_size` | `Terrain` | the near terrain from the height pages (`desktopgame`); the game's bake of a node, texels a side (`terrainbake.CBake(size=)`, made with the frame); the view's own fallback bake stays at `terraindraw.SLICE_SIZE` |
| clouds, haze, SSAO, SSR, eye adaptation | `Lighting` | switches for phases 6 to 8 and the viewer's 117 frame; the island view draws none of them yet |
| post | always on | the post chain (View > Rendering can switch it off) |
| `particles`, `cloth` | `Object` | the particle quads a frame draws, nearest first (`particledraw`); the viewer's cloth (not read yet) |

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
`roman_island_medium_03`, the look "The game's own shaders" on Direct3D 12). The presets were ours then, with a render
scale of 0.6 at Low and 0.8 at Medium. The game's presets keep the full resolution, so that table is not measured with
them yet.

Tests: `tests/render/test_quality.py` (the presets' area levels with a file's levels and without, the older games,
Custom kept, a hand-edited record, a released settings file's names, the player's engine.ini followed and a file that
holds no setting, the draw distance, what makes a frame anew, a map's coarser level and the view's halved maps;
installed: 117's and 1800's files give five levels each) and `tests/suite/test_qualitydialog.py` (the menu's entries,
a preset setting every area, an area by hand making it Custom, the render scale, the game's entry).

Note: measured on 2026-11-13.
