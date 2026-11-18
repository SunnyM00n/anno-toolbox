# Island editor: the 3D view and the minimap

This page says how the editor draws an island: the 3D view, its particle effects, the GPU load, the game's rendering
rules it follows, and the minimap.

Part of [Island editor](island-editor.md).

## The 3D view

`desktopview` (the Qt widget, the input) around `desktoprender` (moderngl; GLSL in `render/shaders`, the models in
`render/modellibrary`, the camera in `render/camera`, the terrain, ribbons and pins in `desktopgeometry`, picking in
`desktoppick`: each instance drawn as its number into the pixel under the cursor). What the view
draws comes from `islandeditor.IslandEditor` (the meshes and textures as files come from its mixin `islandassets.IslandAssets`; a converted or vanilla 117 island; Anno 2070 editing and the other games
are subclasses of the same shape): the island as JSON (heights at 2 samples a tile, 1 over 512 tiles; splines, the
water network, objects, props, forest), each model's parts from its config (`modelparts.model_parts`: mesh, textures, colour,
shader, transforms; only an `ORIENTATION_TRANSFORM` places a nested part - an animation transformer's `Rotation` is
a sway in degrees, and read as a quaternion it twisted the sardine boats), each mesh as glTF (`rdmglb`), each texture
as PNG (texconv; `.tga` resolves to `_0.dds` like `.psd`), all cached in the workspace.

- **Terrain** from the editor's heights (the render heightmap, `CoarseHeightMap`), the brushes' changes uploaded
  row by row.
- **Ground**: the baked image (`islandview.albedo`) until the materials are in, then every material's own diffuse
  and normal map at full size, tiled at the material's repetition and blended by the editor's weight maps (up to 4
  texels per tile; one shader over texture arrays), which the paint brush changes. Ground textures lose their alpha
  (a blend weight) and normal maps come as plain RGB normals (`full_normal`: Anno's keep the gloss in alpha).
- **Objects**: every placed object as its model config draws it, instanced once per config; a slot or tree asset
  without a config of its own takes the first model of its asset in the mod's `assets.xml`. Normal maps without
  tangents; banner leaves (ShaderID 28, `shader117`) turned to the camera and to the sun in the shadow pass (the
  cards read from the RDM, `rdmbanner.read_banner`: a glTF carries unit normals, which lose the card size); parts
  on 117's water shader (ShaderID 7: waterfalls, river turbulence) see-through, their flipbooks (8 x 8 frames on
  the waterfalls) played frame by frame, any other water texture scrolling at its `cFlowSpeed`.
- **Forest**: every forest tile planted with one of the region tree's models (a seeded pick per tile, as the
  island build does, `tile_pick`), each tree an entry of its own (kind `tree`, `tile:x,z`): listed per model, picked
  with a click, Del clears its tile, the library's "forest of this island" group plants one. A forest tree stays on
  its tile (117's forest is a tile map): no gizmo.
- **Props**: the island's prop grid. Grass and bushes (a model placed more than 200 times) are the grass layer,
  without shadows and left out beyond 220 units; every prop - grass, bushes, 2070's waterfalls, their splashes, the
  rainbow, fires and lights - is listed, picked, moved and deleted like an object.
- **Markers**: 117's editor-only objects (herds, audio spots, markers, blockers) as coloured pins per kind.
- **Pins for objects without a model**: an object whose asset names no model, only a placeholder, or whose config
  draws nothing (decals, effects only) is a white pin. An object whose asset or config the editor cannot read is the
  same pin, and the Checks tab says why. Buildings, slots, ruins and units of an island are drawn with the model their
  asset names, by the object's variation, through the same model library as props
  ([Placed objects](island-editor.md#placed-objects)).
- **Sea, rivers and lakes**: plain lit surfaces (the sea at height 0, rivers as ribbons, lakes at their spline
  heights). Each pixel of a river or lake is drawn once (a depth pass, then colour where the depth is equal), because
  two see-through surfaces would double into a light band. Sun shadows follow the camera (2048 map). The sky is a
  gradient with fog. The frame is rendered at most at 1.5 pixels per point into a half-float target and finished with
  an ACES curve.
- **Hiding**: a right click on a model in the view, or on a model or category in the object list, hides that
  object, all of that model or the whole category, and shows what is hidden again; hidden entries are dimmed. It
  changes the view only, not the edits. "Hide foliage" takes out every plant at once: tree and vegetation assets,
  models from tree, bush, grass and plant folders, and the forest and grass layers.
- **Layers** (the view tab): objects, markers, effects (props), grass, forest, water, each on or off; the particle
  effects of a model follow its layer.
- **Theme**: the editor look's own colours follow the window's Light / Dark theme live (`viewcolors`, `render/light`):
  the sky gradient and the fog it fades to (a pale sky in Light, a slate one in Dark), the empty view before an
  island is open, and the overlays - the brush ring, the selection box, the gizmo's active handle and a picked river
  point (the accent: bright yellow in Dark, a deeper orange that reads on the pale sky in Light). The game-look
  modes (the game's light, 2070's look) keep the game's colours; so do the colours that name a thing - the gizmo's
  axes, the markers' kinds, the water tool's lines. The road piece editor's canvas (`suite/theme` `CANVAS`) switches
  with it, an open road window included.

What a reader had to leave out (no editor-only assets, a damaged `.water.json`, a ground texture drawn in its mean
colour, an 1800 install without its `assets.xml`) goes into the Log view tab and the status bar, not silently.

### Particle effects

The Rendering menu's **Particles** draws the effects that models place (steam over a home, smoke, a waterfall's
spray, a hologram); it is off by default, because the effects cost frame time and the view then keeps drawing at
15 frames a second. **Freeze particles** holds them as they are. `scripts/editor_shots.py --particles` draws the same
in a picture, at one fixed moment (`--fx-time`) so that two runs agree. The effects are a sketch of where they sit and
how big and bright they are, not the game's own picture.

- **What is drawn.** 117's `PARTICLE` blocks (baked `.rdp` files, `formats/rdp117`) and 2070's `EFFECT` entries (`.efc`
  files) of a model and of the models it nests, for every instance within 260 units of the camera (400 a model, 40,000
  quads a frame, the nearest first). Both are one record in the view (`render/particlefx`): a strip of ticks that
  repeats, each particle alive for a range of them with its position, size, turn and colour per tick. 117's file is the
  strip as stored; 2070's emitters are run through the simulation that bakes them into that strip (`models/particlebake`:
  born every PEP milliseconds, moving by the velocity curves, colour and alpha over the age), once per file in the
  background (about a tenth of a second each). The view plays the strip from its clock and blends between ticks; a
  config's time scale slows it, its colour and alpha tint it.
- **How a quad is drawn** (`render/particledraw`): a camera-facing, an upright (turning about the vertical)
  or a fixed card, one tile of the effect's texture atlas, additive or alpha-blended as the file says, lifted by the
  material's colour boost (`1 + (boost * 0.07)^2.2`, 1.8 for the glow materials). Unlit, depth tested and not written;
  blended cards are sorted far to near within a texture, the additive ones come last.
- **Left out**: effects hung on a bone (the view has no poses);
  effects a model shows only in a state (damage, a visibility mask: the burning flames of a nested config);
  2070's mesh, line, arrow and wave-map emitters (not sprites; listed in the Log as `effect left out`); a 2070 emitter that plays once only
  when a sequence names it (it then repeats); wind, soft edges, shadows and fog on particles; sorting between textures.
  The brightness (`GAIN`) is a guess against the editor's light.

### Decals on the terrain

The objects' DECALs are drawn (`editor/desktop/desktopdecals`, `render/decalmesh`). Each placed object's decals (its
config's and its nested configs', `ModelLibrary.decals`) are laid on the drawn ground as a grid over the decal's
rectangle. One cell is about the terrain's sample step (at most 64 a side). Every vertex is at the height of the
terrain the view shows (the game's heights with the edits when the game terrain is on, else the session's) plus 4 mm,
with u along the decal's x and v along its z. The game's high-poly decal is a mesh over the terrain in the same way;
how the engine cuts it was not read **[I]**. The meshes are made again when an object, a batch or the ground changes,
one per pass and material. They are drawn blended, without writing depth, in the pass their data picks (117's rule,
`rendering/material-passes.md` "Decals"): `OnStreets` 0 before the street models
(`ShaderID` 19), `OnStreets` 1 after them, both before the other models; `Type Vehicle` after the models. Drawn with
the editor's model shader (no normal map; the colour map and the decal's opacity). Seen offscreen
(`out/agent-render6/5-decals/`): three 1800 buildings placed on a third-party island, their ground decals following
the slope. Still left out: a decal that is shown only in a state.

Note: the decals were added on 2026-11-12.

**1800's decal masks** (read from 1800's `decal_pbr.fx`, `decal_terrain.fx` and `model_common.fxh`, and 117's
`decal_terrain.fx`). A decal has no mask map of its own. What covers the ground is the colour map's alpha times the
material's opacity: `cOpacity` for the model decals (`ShaderID` 1, 3,506 of 1800's decals) and `cDiffuseOpacity` for
the terrain decals (`ShaderID` 2, 67; 117's terrain decals the same). The configs set the latter to 0 or 0.7 on cracks
and ice cracks, to lay only their normal map on the ground. The view reads both (their product:
`configtree.material_fields`), so a terrain decal that is meant to leave the ground's colour does not paint its own
(white on the ice cracks). The white seen on the airship hangar's ground is its texture's snow (an arctic hangar).
The decals take the sun's shadow (the model shader's decal kind, 3), so a building's ground does not stand bright in
its own shadow. The terrain decals clamp their texture coordinates at the edge (the game's `ClampTexCoord`). The view
repeats them, which only shows in a texture whose edge is not transparent.

Note: the shader files were read on 2026-11-12.

### GPU load

The view draws on demand: every frame while it is being used (input, the camera, a gizmo), when something new has
loaded, and 15 frames a second only while flowing water or a flipbook is on screen. Measured on n_l03 at rest: about
14 frames a second instead of the display's 60 or more. Big islands are read coarser: the continental
island (768 tiles, 3,504 objects, 44,878 props, 26,957 forest tiles) at the full 2 samples per tile made a terrain
mesh of 4.7 million triangles and a ground image of 3072 pixels, 21 s to draw; over 512 tiles it is read at one
height sample per tile, the ground image is at most 2048 pixels, and the island data and ground image are cached on
disk per island file (2.5 s and 18 s the first time, milliseconds after). The terrain and the instances are
view-frustum culled (`render/cull`). The terrain's index buffer is ordered in at most 16 x 16 chunks, each with a box
from its heights, and a run of chunks in view is one draw. A batch (one model) keeps its instance matrices sorted into
32-unit ground cells. Only the cells whose box (the instances' origins widened by the model's reach) touches the
camera's six planes are gathered into the batch's draw buffer. The main pass therefore draws what is in view, while
picking and the sun-shadow pass (which needs what lies outside the view too) keep the whole buffer. Measured offscreen
on `roman_dlc01_island_continental_01` (1600 x 1000, the editor look, shadows on, 20 frames each, a real GPU through
`scripts/editor_shots.py --bench 20`): whole island 290 ms both ways (everything is in view); close up (40 units above
the land) 426 ms and 3,697 draw calls, 48,036 instances, 1,179,648 terrain triangles without culling, 95 ms,
345 draw calls, 8,212 instances and 78,336 triangles with it. Eight random cameras on the same island give the same
picture to the last pixel with culling on and off (`tests/test_desktopcull.py` checks the same on a synthetic island).
What is left of the close-up frame is the shadow pass and the per-draw state each part sets from Python.

The view also picks each cell's **level of detail by 117's rule** (screen size, `render/cull.CellTable` over all
batches at once). It draws the models' `_lodN` meshes at the coarser levels and leaves out what is too small on screen
(not markers). The sun-shadow pass draws only the casters in the sun's box at their level, so picking alone keeps the
whole buffer (`desktopbatches`: an instance buffer per pass and level). The light is set once per program and frame,
and a model's pixels are let go once the GPU has them. With this, Egypt's continental quadrant (134,666 entries) went
from 128 to about 30 ms a frame for the whole island (rendering/island-117-view.md "Speed").

Note: the rest frame rate was measured on 2026-10-15, the culling was added on 2026-10-27 and the level of detail on
2026-11-13.

### Drawn by 117's rules

The editor is meant to draw an island in the same way the game does, by the rules read from the game's shaders
(`docs/rendering/`). The Rendering menu's switches do this, and each one is listed below with what it still leaves
out. 117's rules are for 117 and built islands, and the 2070 look is for islands edited for 2070. The game's look
and the 2070 look exclude one another.

- **Game terrain**: the ground as the game draws it near the camera, rebuilt from the island's height pages exactly
  as the engine rebuilds them (Catmull-Rom upsampling plus the deltas, the coarse heightmap plus the page clamped to
  one unit; `gameterrain`), on the editor's half-tile grid. Seams between pages show here as in game. It shows the
  saved island; the brushes keep editing the editor's own heights. Rebuilt once per island (about 10 s for n_l03),
  then cached. Open: the engine's patch grid for near nodes (how many height samples a near node really takes) lives
  in protected code; until it is known the editor samples every half tile.
- **Game light** (it has no switch; the game's look draws in it): the game's light for a Roman day
  (`gamelook`): the sun at the 58-degree cap with the colour of the game's atmosphere, the sky light as its nine
  spherical-harmonic coefficients at the game's 0.7, an HDR sky from zenith to horizon, and the frame finished like
  the game's: the colour cube of the ambient's grade (read from the install), exposure 0.55, the shadow lift and the
  game's ACES curve (until the game's post chain, island-117-view.md phase 8). Inferred values (rendering/lighting.md 6).
- **2070 look** (islands edited for 2070), by 2070's own rules (`rendering/rendering-2070.md`): each 16-tile chunk
  paints its layers in its own order (the order per chunk and the 17x17 alpha maps, `ground2070`), the bottom one
  opaque and each further one at `saturate(5*(a-1) + 6*coverage)` with `a` its texture's alpha, in raw gamma values;
  the North preset's sun and ambient (2070's "ambient where unlit, sun where lit" in linear light is an ambient light
  plus a sun light of the difference), its sky colours, and a final pass with the scene clipped at 2, the Uncharted 2
  curve white at 2 and the mood colour; the sea on Direct3D by 2070's own shaders (below). Not yet: bloom, 2070's
  rivers and lakes, its gamma-space fog formula, clouds, the Blinn highlight.
- **Underwater look** (islands edited for 2070; Rendering > Underwater look, `--looks underwater` in
  `scripts/editor_shots.py`): the 2070 look as the game shows the sea floor from below, for the underwater islands
  (`light.UNDERWATER`, the shader's `underwater`). Colours are worked in gamma space. Light is absorbed per
  channel by `(1 - depth)^(1.97, 1.09, 1.09)` with `depth = max(-y / 36, distance / 72)` (so the red is gone first),
  and the haze takes over with the weight `(distance / 57.2)^2.52`; its colour is the light from above times
  `saturate((1 - (v.y * 0.5 + 0.5))^(1.97, 1.09, 1.09) + eye.y / 72) * 0.6`, with `v` from the pixel to the eye, so it
  is dark looking down and white looking up. An empty pixel (the sky) takes that colour too. An eye above the sea
  counts as at the surface, the sea's surface and the dark bed are not drawn, and the frame is finished as in the 2070
  look. The light colour is one neutral constant (3.5 in gamma), where the game reads it from the island's preset.
  **Caustics** add two layers of light patterns drifting against each other, stronger with depth, before the
  absorption; the patterns are procedural, the game samples a texture, so only their kind and strength follow it
  (frozen with Freeze particles' clock in `scripts/editor_shots.py`, `--looks caustics`). Not yet: caustics scaled by
  the surface's slope and shadow, the sun rays, plankton and bubbles, the light of point lights through water. Seen
  on `u_m14_rift` (render only, not compared with the game's own picture).
- **The game's own shaders** (Rendering > The game's own shaders, `--looks shaders` in `scripts/editor_shots.py`
  with `--renderer d3d12`; phase 1 of `rendering/island-117-view.md`):
  on Direct3D 12, a 117 island's terrain (height pages, the game's bake, its shadow) and objects are drawn by the
  game's own shaders into the engine's G-buffer and lit by its deferred shading in game light (`desktopgame`,
  `desktopgameobjects`, `render/terraindraw`; phases 1-5). The sea, rivers and lakes and the config decals are drawn
  that way too (`desktopgamedecals`). Particles are still the view's, drawn over it.

  This is a 117 island's only full look, and it is on by default where the view draws on Direct3D 12 (the app's
  default device there). The view's own look draws only while the game's shaders compile on a first start and where
  they fail (per model, else the whole view). Its meshes and maps are not loaded while the game's look draws; picking
  then clicks the objects' boxes (island-117-view.md "Speed", part 6).

  The game's shaders also draw the visible sky, haze and clouds, and its grade, tone map and eye adaptation. The
  water parts read the scene behind them (phases 6 to 8, island-117-view.md). The sea, coast waves, rivers and lakes
  and the session fog are drawn by the game's shaders too (phase 6, steps 2 to 4; phase 7). Where those do not draw,
  the view's own water is plain see-through surfaces.

  **The terrain by the game's shipped shaders** (2026-11-17): where the Toolbox's Direct3D 12 module is installed, the
  terrain's nodes and their shadow are drawn by the game's own compiled terrain shader as it ships, not by a copy the
  Toolbox rewrites and compiles; the rest of this look is drawn as before. It looks the same, with sharper rock faces
  at a grazing angle (the game's anisotropic filtering). Not checked against the game's own picture
  (`rendering/native-backend.md` section 9).

  **The start-up stage** (2026-11-17): the first start after a game update prepares this look's shaders. While the
  start-up screen shows it compiles what an island needs first (the shaders the game does not ship, the look's fixed
  passes); once the window is up, every model shader the game ships compiles in the background, with "Preparing game
  shaders: n of m" in the status bar. An island opened meanwhile goes first. A later start skips both. When an update
  draws a part of the look with the game's own shaders, or after a game update, a pop-up offers to remove the compiled
  shaders no longer needed (Help > Remove unused game shaders at any time). Not seen in the installed program yet
  (`tool-suite.md` section 21, "The start-up stage").
- **Go to** (the Home tab): `x z [distance] [turn]` points the camera at a place in tiles, from a turn in degrees.
  This shows the same spot in a 2070 and a 117 island one after the other (how the falls were compared).

**Rendering quality** (Rendering > Rendering quality): for an island of 117 or 1800 the view draws at the preset that
the settings keep for that game: Low, Medium (the default), High or Ultra (Custom: set in the settings file). The
older games' islands are drawn at their best, and the menu is greyed. A preset sets the level of detail's bias and the
smallest object drawn from the game's own quality level. It also sets our render scale, shadow map size, draw
distance, largest map side, tessellation and particle budget. It applies in both looks and the game's shaders. A
change of the shadow map, the maps' size or the tessellation makes the game's shaders' frame anew (Medium to High on
`roman_island_medium_03`: 11 s, 24 s before 2026-11-17; rendering/island-117-view.md "Speed", part 14). The values, sources and measurements are in
[`rendering/quality.md`](rendering/quality.md).

Note: the game's own shaders were added from 2026-11-12 to 2026-11-14, and the rendering quality on 2026-11-13.

### Looks

- **117's islands** (and 1800's and 2205's once their game look exists): the game's own shaders are the look. The
  editor's own look is the plain fallback. It is drawn only while those shaders compile on a first start and where they
  fail (per model, else the whole view), and its full scene is not loaded while the game's look draws
  (`rendering/island-117-view.md` "Speed", part 6: built).
- **There is no OpenGL imitation of 117's look**, that is, no look that draws a 117 island on OpenGL with the game's
  light, ground blend and colour grade. The following do not exist for that reason:
  - the Rendering menu's Game ground and Game light switches
  - the game-ground blend of the editor's ground (its coverage maps, `IslandEditor.ground_coverage`)
  - `editor_shots`' `game` look
  - the read-back of the sky passes' prefiltered cube (`gamesky.SkyPasses.reflection_levels`: our look's reflection)
  - Game water, the OpenGL model of the game's water (`WATER_GAME_FS`, `gamelook.water`). The game's own sea, rivers
    and lakes replace it, and the fallback's water is the plain surfaces
  - the terrain shader's ground mode 2 with its `coverage` sampler

  These stay, because other parts read them:
  - `render/gamelook` (117's sky light, sun and grade tables: the light and grade of the game's shaders look until its
    post chain, the viewer, 2070's grade in `world/grade2070`)
  - `render/gameground` (the viewer's ground)
  - the game light itself (`ViewOptions.game_light`, set by the game's look)
  - Game terrain (the heights that the view's picking and its fallback ground follow)

  The editor look's ground for 117's, 1800's and 2205's islands is the simplest fallback: each material's flat colour
  (the mean of its diffuse map) by its weight, with no detail maps (`desktopground.PLAIN_GROUND`). 2070's, 1404's and
  1701's islands keep the editor's look. It is their only look, with their seas drawn by their own shaders (see
  below). The numbers before and after are in `rendering/island-117-view.md` "Speed",
  part 8.

Note: these looks were decided on 2026-11-13; the OpenGL imitation was removed on 2026-11-14 and 2026-11-15.

### 2070's sea by the game's shaders

On Direct3D (12, the default, or 11) a 2070 island's sea is drawn by 2070's own water shaders over the view's own
terrain and models, in whichever look the island has (`desktopsea2070`, `render/ocean2070`). This applies to an island
of Anno 2070 and to one edited for 2070. The shaders draw:

- the wave maps and the sky that the water reflects
- the ocean's chunks, refracting what lies below with the light absorbed by the depth
- the caustics on the sea floor
- the island's water info map, built from its heights as the game builds it (waves calmer at the coast) and made again
  when the heights are edited
- the coast waves by `waterwaves.fx` (`render/coast2070`): a height field of 128 units round what the camera sees,
  stepped 30 times a second, with surf and foam along every coast. A picture starts them 20 s early, so they show
  settled
- the planar reflection: the view's own terrain and models mirrored in the water and clipped there
  (`camera.oblique_near`)
- the island's own ambient set (its area by the island's kind, the set the world's areas name)

The sun's shadow does not fall on the sea: the game's shader compares a distance of 0 there, so its sea is lit
everywhere, and the view's is too.

The view's own sea stays the fallback: while the shaders compile, where they fail (said once on the message line),
with the water layer off, under water and on OpenGL. Rivers and lakes stay the view's. While the game's sea draws, the
frame is drawn without multisampling (its depth is read).
`rendering/shader-compile.md` section 10, "2070's sea in the island view", says what is
the game's and what stands in. It also says why 1404's and 1701's seas need their own passes.

Note: added on 2026-11-14.

The seam in the view (for the island view's other looks): `desktoprender.Renderer._game_sea` asks each of the old
games' seas whether it draws (`wanted`: the island's game, its install, Direct3D, the water layer), `_targets(...,
single=True)` makes the frame single-sampled for it, and its `draw` runs after the opaque parts and the dots, before
`_draw_sea`, setting `game_sea` (so the view's own sea and its sea bed are left out). It reads the frame's colour and
depth targets (`t["color"]`, `t["depth"]`) and restores the depth test after its composites. What the seas share is
`desktopsea.IslandSea` (its shaders and maps compiled and read once in the background, `_compiled`; the layer over
the view's depth, cleared with the mirrored scene drawn at a frame's start, `_begin`, and composited back with the
depth test on again, `_end`; the copies, the camera and the sun in the game's axes and units, the failure said once,
a map built from the island's heights in the background, `_heights_map`; the island's set, profile or area by its
folder, `by_folder`; the compiler build of the game's record, `Files.compiler`); a sea says its games, makes itself
ready and records its own passes between `_begin` and `_end`.

### 1701's sea by the game's shaders

On Direct3D (12 or 11) an Anno 1701 island's sea is drawn by 1701's own water shaders over the view's own terrain and
models (`desktopsea1701`, `render/ocean1701`). It is the game's projected grid over the part of the sea the camera
sees, with its waves from the game's wave texture. It refracts the view's frame, and reflects the view's scene mirrored
in the water and the game's sky cube. It is coloured by the island's ambient profile (`North`, or `South` for an island
of the southern folder), and the waves are calmer where the water is shallow.

The coast waves are drawn too: every coast line is drawn each frame as the game's strip into the terrain's wave
texture by `coastline.fx` (`render/coast1701`, from the lines with their edits, flat and steep coasts by their own
technique), which lifts the sea along the shore. The sun's shadow falls on the sea (the view's own shadow map read as
the game reads its own, see below). The game draws no sea from a camera lower than half a tile above the water; the
view draws its own there.

`rendering/shader-compile.md` section 13, "The sea", says what is the game's and what
stands in (the water depth from the view's depth, the sea floor round the island at its border's height, the view's
casters for the game's, no ships' wakes). Rivers and lakes stay the view's.

Note: added on 2026-11-14.

### 1404's sea by the game's shaders

On Direct3D (12 or 11) an Anno 1404 island's sea is drawn by 1404's own water shaders over the view's own terrain and
models (`desktopsea1404`, `render/ocean1404`). It is the game's projected grid (1701's design with 1404's numbers)
over the part of the sea the camera sees. Its great and medium waves are calmed near the coast by the island's water
depth map, which is built from the island's heights as the game builds it (and made again in the background when the
heights are edited). The shallow water is clear over the sand. The sea refracts the view's frame with the caustics,
and reflects the view's scene mirrored in the water and the sky cube that 1404's sky shader renders. It has foam on
the choppy crests and uses the island's ambient set (`North`, or `South` for an island of the southern folder). The
game draws no sea from a camera lower than 0.1 above the water.

The sun's shadow falls on the sea too: the view's own sun shadow map (its models, not the terrain) is read by the
game's `StandardShadow` technique in the game's axes and units (`desktopsea.IslandSea._view_shadow`; the same for
1701's sea). `rendering/shader-compile.md` section 12, "The sea", says what is the
game's and what stands in (the depth map's scale from the set, the view's casters for the game's, no rain and no
ships' waves). Rivers and lakes stay the view's.

Note: added on 2026-11-14.

Known gaps of the editor's own look: the ground's blend is linear, where 117 sharpens material edges by height
(`AlphaSharpness`); the particle effects are a sketch ([Particle effects](#particle-effects)).

## Minimap

The Minimap dock (`editor/minimap`, `desktop/minimappanel`) paints the island in the same way Anno 117 paints its
session minimap, from the editor's current heights. It is repainted 150 ms after the heights stop changing. The rules:

- A pixel is two samples of a tile.
- **Land** is a height above sea level (compared as the game stores it, 128 to a unit).
- The **coast** is the outline colour, fading in as a 5 x 5 box over the land mask passes 0.25 to 0.5, then outline to
  normal up to 0.75.
- Inside, the **normal** colour turns to the **mountain** colour with height, up to 40.
- Sea is clear and shows the dock's sea colour.
- **Underwater plateaus** are teal (49, 110, 133) with a (158, 224, 255) border. They are clear pixels above -28 and
  at most at sea level, as patches with open sea all round (a patch that touches painted land is a shelf or a lake and
  stays sea). The plateau is shaded a little lighter with height. A 2070 underwater island, say `u_m14_rift`, shows as
  one teal shape.

The three colours are neutral stand-ins (`minimap.LAND_COLORS`). The game's colours are in its minimap scene asset,
which the editor does not read yet (`paint(..., colors=...)` takes them).

A paint that fails is said in the status line; the dock keeps its last picture.

Limits: the picture is drawn from the editor's own formula and was not compared with a game screenshot. x runs right
and z down, as the heights are stored. Fog, owners and the camera frame of the game's minimap are not drawn.

## Archived tuning

These values were switched back to the plain mapping. The table gives each old value and its reason:

| Name | Old value | Why it existed |
|---|---|---|
| `modellibrary.BANNER_ALBEDO` | 0.55 | Darkened the pale 2070 leaf sheets as the game's shader did at 0.4. The plain value is 1.0, as the shader's own plain value. |
| `desktopground.GROUND_TEXTURE_MAX` | 1024 | The editor drew 2070's ground at 117's material size. The plain value is 512, 2070's own. |

Note: switched back on 2026-11-06.
