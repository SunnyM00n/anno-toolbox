# The asset viewer: a user's guide

The suite's **Viewer** shows the files of any locally installed Anno game (Anno 117, 1800, 2205, 2070, 1404 and 1701)
in the same manner that the game reads them. It reads the game's archives in the game's own order, with a mod folder
over them, and shows each file in the view that its kind needs. A model opens in 3D with the parts of its config
beside it.

The layout follows the studio's own object builder. The parts are on the left, the model in the middle and the fields
on the right. The sequences are on a timeline below.

It only reads your own installs, and modifies neither a game nor a mod. The index of a game's archives and the loaded
sound clips are kept in the workspace's `viewer-cache` folder.

## Start it

```bash
pip install -e ".[gui]"                        # the windows (the light install has the commands only)
python -m annotoolbox --app viewer        # the suite's window, on the Viewer (Ctrl+4)
```

The game folders are the suite's. They are set once in the island editor, under File > Settings.

- **File > Open game** picks a game. The first opening of Anno 117 indexes its archives (a few seconds). The others
  open at once.
- The viewer remembers the game and the file that was last open.
- **File > Lay a mod folder over the game** reads a mod's files first, as the mod loader does for a file the mod
  replaces.
- **Open a folder of loose files** browses an unpacked tree.

## The window

| Part | What it does |
|---|---|
| **Files** (left) | the game's folders, filled when opened; a search over every path (all words, any order, as you type) and a filter by kind (models, textures, effects, sounds, data; for Anno 117 also **Deleted by the game**: the paths the game's archive map takes away, with the archives that still carry them). Under the list: the file's path, the archive it is read from and the ones it overrides. Double-click a file to open it. |
| **Parts** (left) | the opened config's parts as the object builder lists them: nested files, models with their materials and animations, cloths, particles, decals, lights, collisions, sequences. A tick shows or hides a part (a nested file's whole model with it). Selecting a part frames it with a box in the view and shows its fields. |
| **Properties** (right) | the file's facts (size, the archive it is read from and those it overrides, format, a texture's levels and faces, a sound's format and length; a newer game's config names the baked copy the game reads instead), or the selected part's fields in file order. A blue value names a file: **double-click** opens it - a material's texture in the texture view, a nested config, a mesh, an effect. **File > Back** (Alt+Left) returns. |
| **Used by** (right) | the files and the assets that name the open file, and the field an asset names it in. **File > Who uses the file** (Ctrl+U) builds the reference graph. It is the same read as **Find the unreferenced files**: every file that can name another, once, kept beside the game. From then on the list follows each file you open. Double-click a config to open it, an asset to show it in the asset editor (in the suite's window). Notes on how a name is resolved are below the table. |
| **References** (right, beside Used by) | what names the open file and what it names, for every kind of file (configs, asset files, scripts, effects, sound banks by media id). Notes on how the graph is built and used are below the table. |
| **Checks** (right, beside Used by) | every file the open model names that the view cannot draw: under **Not in the game's files** the name, why the game has no file for it (below, "How files are found") and the config that names it; under **Do not load** a file that is there but does not read or decode, with the reader's message. Double-click a row to open the config that names it. A missing colour map is drawn as a magenta and black checker, never as plain white. |
| **Sequences** (bottom) | the model's sequences (the packed id spelled out: action, info, variant) and each model's animations; **Play**, the speed, and a timeline of the sequence's tracks (blue: an animation, orange: an effect, grey: a state); a click on the timeline jumps there. An effect the sequence starts shows only inside its orange windows, played from their start. |

**Used by.** Each name is turned into the file its game loads. The name as written is tried first, then the game's
texture rules, then the mesh rules. 1701's `.mcf` configs are read too.

**References.** **File > Find the unreferenced files** reads every file that can name another once. It runs in the
background, and the graph is kept beside the game. A game opened later takes the kept graph at once.

- The panel then follows each file you open.
- The file browser gets a **Not referenced** box. It lists the files that no file in use names, which includes the
  files only named by unused files. It combines with the kind and the search.
- **File > Export the unreferenced files (CSV)** writes the list with each file's class.
- "Not referenced" means no reference found in the data, not that the game never loads it.
  [`references.md`](references.md) says what counts as an entry point and what the program loads by a name it builds.

## Models

A model config (`.cfg`; 1701's `.mcf`) or a mesh alone (`.rdm`, `.gr2`) opens in 3D.

- Left drag turns the model.
- Right or middle drag moves it.
- The wheel zooms.
- **F** frames the model.
- A **click** on the model selects the part under the mouse (its row in Parts, its fields on the right).

What the viewer shows, by game and by topic:

- **Anno 117, 1800 and 2205.** About half of 117's vanilla configs have a baked copy under `shared/`, which the game
  reads in place of the `data/` one (with five levels of detail made by the studio's tools).
  - **View > Read the baked shared/ copy** is on by default, so the view shows what the game draws. Switch it off to
    see the `data/` config.
  - **View > Level of detail** picks the level (each level has its own list of materials).
  - Every material of a mesh is drawn with its own textures.
- **Anno 2070**: the config's models, nested configs, tree leaves and effects, and its cloth too.
- **Anno 1404** reads by the same path, because its configs are 2070's format over Granny meshes. It shows models,
  nested configs, effects, cloth, decals, leaves, sequences and animations. A Granny group takes its material by name.
- In both 2070 and 1404, a part, effect or nested config that a `VISIBILITY_TRANSFORM` shows only in a state is drawn
  while one of its condition bits is held (View > Condition bits).
- **1701**: the `.mcf` tree with its animations, cloth (class 9) and effects (class 3).
  - `m_Animation`: the clips of a list play on its meshes, and `m_Speed` 1000 is the clip's own pace.
  - A condition trigger (`IS_DAMAGED`) and a named effect start parameter (`SHOOT_01`) each become a condition bit, in
    the order met, and the notes name them.
  - A nested `.mcf` that is missing, broken or nested too deep is named in the notes too.
  - 1701's `.efc` files (a text format of their own, [`formats/anno1701-effects.md`](formats/anno1701-effects.md))
    play their particle, card and smoke emitters by rules inferred from the files. Its model, firework, ring and chain
    emitters are not drawn.
  - Granny skins, skeletons and curves: [`formats/granny.md`](formats/granny.md), "Skeletons, animations and curves".
- **Animations**: a skinned mesh is posed by the animation its sequence plays (or the one picked in the list). A model
  without an animation shows its bind pose.
  - An animation's `Scale` slows it, and its `LoopCount` loops it that often, after which it holds its end.
  - A sequence's `Speed` runs its clock.
  - A joint the animation does not name keeps its bind pose, but a root one stands at the origin. Anno 2070's children
    play shared animations without a root track.
  - A Granny mesh (1404, 1701) is skinned by its bone bindings (a rigid one moves whole with its bone).
  - A Granny animation file is sampled every time step against the skeleton of a mesh read before that, which names
    most of its bones (`models/grannyrig.py`).
  - A mesh whose vertex colours pin it (blue's top bit) is cloth and stays rigid (1701's coarse cloth split into four
    triangles per triangle, twice: [`formats/granny.md`](formats/granny.md), "Cloth").
  - The look by 1404's and 1701's own shaders draws Granny skins at rest so far.
- **Parts that move**: a part hung on a joint (a tool in a worker's hand, a shield on an arm) follows the joint as the
  animation poses it. A part with a spin (a mill wheel, a crane, a beacon) turns as the game turns it.
- **Effects**: the particle effects a config places play with the model (117's `.rdp`, 2070's `.efc` as their sprite
  emitters). An effect file opened on its own plays at the origin. **View > Effects** turns them off.
- **Looks** (View > Rendering): **the game's own shaders** (the default; below, "The renderer" and "Looks") and **our
  plain look**, the fallback (below, "Looks"). Shadows, the ground plane and wireframe are switches.
  - **Night** dims the sun and the sky in the game's look so the config's lights show. It also switches what the
    config shows by the time of day (a torch lit at night, a priest with his torch;
    `engine-animation.md` "Time of day and the states of effects, lights and decals"). Its
    lights and effects fade over the config's `FadeTime`.
  - A part with a clock alignment shows the computer's time (engine-animation.md "Spinning parts").
  - **File > Save the picture** writes the view as PNG.
- **States**: parts, nested files, effects, lights and decals shown only in a state of the object show while the
  sequence's condition tracks set one of their bits. **View > Condition bits** holds bits by hand, as the game holds
  Selected or a state no sequence sets. It lists the bits the model reads and the ones the engine sets itself (117's
  names; the other games' by number). **Release all** lets them go.
- **Place on the open island** (File): in the suite's window, hands the open model config to the island editor's Place
  tool (an island of the same game must be open there). A model an asset of the island's game names is placed as that
  asset, on its build grid (island-editor-tools "Placing buildings").
- **Edit in the object builder** (File): in the suite's window, opens the open model config (with its info and
  feedback files) in the Objects app. Alone, the entry is hidden.

What the view does not draw the way the game does is listed in `tool-suite.md` section 19, "Drawn as
the game draws", and in section 21 for the games' own shaders. In short:

- Decals that our look lays lie on a flat ground.
- A height map's parallax and vertex colours are not drawn.
- A state the engine sets itself (damaged, under construction) shows only when its bit is held by hand.
- A tree's or prop's distance fading is not reproduced.
- The game's own levels-of-detail switch by screen size is not reproduced. The level is the one you pick.

## How files are found

A config names its files the way the studio's tools wrote them: source names (`.psd`, `.fbx`), backslashes, mixed case,
and now and then a path on the studio's own drive. The viewer, the island editor and the files check below turn a name
into the file the game loads by one set of rules (`models/fileresolve.py`), tried in this order:

| What | Rules |
|---|---|
| Any name | lower case, forward slashes, no leading or doubled slash (1800 writes `maps//x.psd`); a path on the studio's drive (117 writes `d:/projects/anno8/game/data/...` in thousands of nested configs, most of them props the game does not ship at all) is read from its `data/` or `shared/` folder on |
| A texture (117, 1800, 2205) | a source name (`.psd`, `.png`, `.tga`) as its `<stem>_0.dds`, then the name as written (a `.dds` is loaded as it is named, first), then `<stem>.dds`, `<stem>.tga`, then a lower quality level `<stem>_1.dds` to `_4.dds` (the same texture at half, quarter ... size, shipped beside `_0`) |
| A texture (2070, 1404, 1701) | the name as written first (1701 ships its `.tga`), then the same list |
| A texture slot | every slot a material writes (diffuse, normal, metal, dye, height, alpha, night glow, wind ripples, second set, splat, flow, vertex animation ...); a slot its switch turns off (`NORMAL_ENABLED` 0, ...; rendering/material-fields.md) loads nothing and is not a miss; the studio's placeholders (`default_*`, `failure_texture`, 1701's `leer`) draw without a map when the game has no file of that name, as the game draws its default |
| A mesh or animation | `.fbx` as its `.rdm`; a plain `x.rdm` the game ships only as `x_lod0.rdm`; a `data/` mesh 117 ships only as its baked `shared/` copy (what a `data/` config read without its copy names) |
| A nested config | its baked `shared/` copy first (engine-modloader.md, "the `shared/` copies"), then the `data/` file; with View > Read the baked shared/ copy off, the `data/` file first and the copy when there is none |
| An effect's texture | as a material's (a `.rdp`'s header and materials, a `.efc`'s emitters) |

**Why a name finds no file** (the Checks panel, the files check), told by the game's own archive index:

- *deleted by the game*: 117's archive map takes the file away.
- *folder not shipped*: no file at all lies in that folder (the studio's `test_data/`, `testdata/`, `tools/` folders,
  `_old` and deprecated ones).
- *absent here, the same name ships elsewhere*: the file moved, the config did not. The panel names where it lies.
- *absent*.

These are files the game itself does not have. It draws those parts without them.

**Decoding**: the viewer decodes these formats in process (`ddsdecode`, `bc7decode`, `bc6hdecode`):

- every DDS layout the six games ship (DXT1 to DXT5, BC4, BC5, BC6H, BC7, the uncompressed and float layouts, cubes,
  arrays and volumes; the legacy and the DX10 headers),
- PNG,
- TGA, and
- JPEG.

A file that does not decode is listed under **Do not load** and drawn as the checker.

**The files check** checks all of it against an installed game:

- every model config it ships, as the viewer reads it,
- every texture found, decoded, and
- with `--islands`, a sample of its islands opened in the island editor and drawn its way.

```bash
anno-toolbox viewer-files anno117 [--sample N] [--islands N] [--no-decode]   # anno1800, anno2205, anno2070, anno1404, anno1701
```

It prints the configs checked, the references by kind, what finds no file by kind and reason (with examples and where a
moved file lies), and the textures that do not decode by reason. What it found is below.

### What the files check found

The table covers every model config of the six installs. It gives the numbers before and after the rules above were
introduced (before: the rules the viewer and the island editor had until then). "Unresolved" counts the references
that find no file (placeholders and switched-off slots aside):

| Game | Configs | References | Unresolved, viewer | Unresolved, island editor | Textures decoded (failures) | All `.dds` of the game: refused |
|---|---|---|---|---|---|---|
| 117 | 19,439 (6,651 read through their `shared/` copy) | 844,057 | 6,748 and 4 configs unreadable -> 6,662, 0 | 6,801 -> 6,662 | 14,780 (0) | 1,165 -> 0 |
| 1800 | 7,928 | 192,151 | 253 -> 195 | 258 -> 195 | 8,980 (0) | 4 -> 0 |
| 2205 | 2,327 | 24,227 | 235 -> 229 | 2,186 -> 229 | 3,348 (0) | 2 -> 1 |
| 2070 | 1,438 | 17,315 | 23 -> 25 | 23 -> 25 | 1,456 (1) | 25 -> 22 |
| 1404 | 1,427 | 6,691 | 20 -> 20 | 20 -> 20 | 1,286 (0) | 3 -> 1 |
| 1701 (`.mcf`) | 1,381 | 7,462 | 17 -> 17 | 17 -> 17 | 1,276 (0) | 0 |

- **What changed the numbers**:
  - 117's three configs whose name fields hold a stray `&` are read (683 references more).
  - 117's studio paths, and its meshes that are only baked into `shared/`, are found.
  - 1800's doubled slashes and its plain `.rdm` names that are shipped only as `_lod0` (32 meshes) are found too.
  - Before, the island editor looked a `.dds` name up as `<name>_0.dds` (2205: 1,957 references missed).
  - Before, BC7 was decoded only through texconv (117: 12,558 of its 14,780 maps).
  - 2070's count rose because every slot is looked up (4,678 -> 8,907 references).
  - The decoders read 117's `R16G16_UINT` and `R8G8_SNORM` maps, the volume textures of all the newer games and 2070's
    and 1404's legacy `R32F` header.
- **The remainder is what the games lack**, by their own archive index.
  - Per reason in 117: a nested config in a folder the game does not ship 5,115 (the studio's `d:/projects/...` props
    and `_old`, quest and test folders), a name that ships under another folder (textures 346, configs 186, meshes 128,
    animations 113), plainly absent (textures 227, animations 141, configs 135, meshes 37), and a texture or mesh
    folder not shipped (143, 89).
  - The other games' lists are of the same kinds (`test_data/`, `testdata/`, a network path of the studio in 2205,
    stale paths).
  - The one texture that does not decode (2070's `effects/default/default_norm_0.dds`) is a 145-byte header that the
    game ships without its texels.
  - The refused files are the games' empty files (2070: 20, 2205: 1, 1404: 1) and 2070's two such stubs.
- **Three islands of each game** were opened in the island editor and drawn its way (117: 585 configs, 824 maps; 1800:
  294, 262; 2205: 356, 585; 2070: 96, 114; 1404: 124, 201; 1701: 81, 175).
  - 117, 2205 and 1701 each name 1 to 3 files the game lacks (a quest prop's mesh, an axe's height map; test-data and
    network paths; an Aztec house's map). The others name none.
  - 1800's baked `.brp` props are read as the `.prp` beside them.
- **No white squares**: a colour map that does not load is drawn as the checker and listed. It is never drawn with only
  the material's tint (white in the game's look and on the leaf and banner cards, grey in the editor's look).

Note: the files check was run on 2026-11-09.

## The renderer

**View > Rendering** (`viewer/renderingmenu`, its logic `render/nativelook`, tested in `tests/render/test_nativelook.py`)
shows, for the game that is open:

| Entry | What it does |
|---|---|
| the game's own shaders (`Anno 117's own shaders (DXC, Direct3D 12)`, ...) | the default where that game's compiler, the backend it needs and our reading of its frame are there; greyed out with the reason otherwise (no compiler found, the backend missing, or "not yet" for a game whose path is not built) |
| Our plain look | our own shaders, on every game and device: the fallback of every game (below, "Looks") |
| Backend | "Automatic" picks what the look needs (Direct3D 12 for DXC's DXIL: 117; Direct3D 11 for FXC's DXBC: 2070, 2205, 1800, 1404, 1701; our looks: the `renderer` setting); an override is kept per game, but the game's own shaders always run on their own API |
| Sun shadows, The sky's light, The visible sky and haze, Clouds, Local lights, Transparent pass, Water, Ocean waves, Post chain, Eye adaptation, Ambient occlusion, Screen-space reflections | the frame's parts of the game's own shaders as switches (117 has all: `rendering/frame-117-viewer.md`); greyed out with "not yet" where the path does not draw them. What each switch turns off, per game, is listed below the table |
| Compiler, Game build, Adapter | which compiler is in use and where it came from (the game's own, Windows', `tools/`), the game's executable version, the adapter (name, hardware or software, API) with its Direct3D 12 feature level and ray-tracing tier |
| Hardware acceleration | on by default; off asks Qt for a software adapter (WARP on Direct3D 12 and 11) from the next start, for machines without a usable GPU or for comparing; the moderngl device keeps the driver's OpenGL |

What each frame switch turns off, per game (a test reads the passes for it):

- **2205**: shadows, sky cubes, the visible sky and haze, clouds (the sky box and the cloud shadows), lights, blended
  pass, model water and post.
  - Ambient occlusion reads "the game draws none".
  - Screen-space reflections read "the game traces them on rivers and lakes only".
- **1800**: shadow cascades, sky cubes, the visible sky and haze, clouds (its cloud box), lights, blended pass, model
  water, post and its eye adaptation (switched off until turned on, as the game leaves it), its ambient occlusion, and
  its model water's small ocean waves with its sea (Ocean waves; off until turned on).
  - Screen-space reflections are off on the Low and Medium quality, as the game's levels 0 and 1.
- **2070** adds clouds.
- **1404** adds its clouds and model water.
- **1701** adds its depth fog, sky, clouds and water sheets (`rendering/shader-compile.md` section 11, "View >
  Rendering per game").
- **Ocean waves**: 2205, 1800, 2070, 1701 and 1404 offer them as their sea in place of the view's ground. They are
  switched off until turned on, as a model viewer shows a ground. When on, they show a sea of at least 128 units each
  way round the model at the water level 0, and only with View > Ground on.

The switches are checkable entries that report their new state. The choice is kept per game in the suite's settings
(`rendering`, `hardware`).

A window keeps the graphics API it opened with. A game whose own shaders need the other Direct3D draws them from the
next start of the viewer, and the view says so. `ANNO_RENDERER` (`gl`, `d3d12`, `d3d11`, `vulkan`, `opengl`) overrides
the device of our looks.

**In the suite's window** the island editor's 3D view decides the API. Direct3D and Vulkan become QRhi's OpenGL
backend there unless the island view draws on Direct3D too (`island-editor.md`, "The renderer"). The first start on a
QRhi backend bakes each of our shaders once (a few seconds), then reads it from the build cache.

Note: the switches were once built with a bare menu callback, which did not report their new state, so they could only
be turned off. They are checkable entries now.

**Anno 117's own shaders** (Direct3D 12) draw as follows:

- Every material is drawn by its own effect file's G-buffer pass (or, without one, its transparent technique over the
  lit colour).
- It casts the sun's shadow by its effect's shadow technique into four cascades. The game's own analysis fits them to
  what is on screen.
- It is lit by the game's deferred shading with that shadow, the config's point, spot and area lights and the sky light
  the game's sky passes compute.
- The game's colour grade, tone map and eye adaptation finish the picture.
- Water draws by the game's water shader with the scene's colour and depth copies.
- The ground plane is in the game's G-buffer.
- The visible sky and haze above the horizon are the game's environment passes.

All of it is compiled from the install's sources by the install's compiler (`Bin/Win64`) or Microsoft's open-source
release of the same version (`tools/dxc/` or `ANNO_DXC_DIR`). The shipped graphics set has no ray tracing (QRhi has no
ray-tracing API). The first time a material is shown, its shaders compile (a residence: about half a minute). They
then come from the build cache. The water's small waves come from the game's ocean simulation. Not drawn yet: clouds,
fog, screen-space reflections, cut-outs by their own pass and the decals (those keep our look). What each part feeds
and what stands in: `rendering/frame-117-viewer.md`.

**Anno 2070's own shaders** (Direct3D 11, FXC) draw its G-buffer pass, its sun shadow, its own deferred shading, its
sky, the config's point and spot lights, its model water and cloth and its default post sequence (bloom, filmic curve,
mood colour). Each is a switch of View > Rendering (`rendering/shader-compile.md` section 10).

Both passes, and 2205's, are one base (`viewer/ownpass.py`, `COwnPass`). The base holds these parts:

- the reasons a pass cannot draw,
- the install's source, read once,
- the background compile and its error,
- a frame's groups, sorted,
- the frame parts, once they compiled,
- View > Rendering's switches (`_on`),
- the FXC stand-ins, and
- the release.

1701's pass is 1404's with its own source, frame, sky and sea (`CFxcPass1404`: one guard for the sea under the model,
one for the sky's compile). The old engines' passes share the caster that may fail (`fxcpass.optional_pass`) and the
compiler build of the game's record (`fxcpass.old_compiler`).

2070's deferred shading that does not compile is said once on the message line and hands every group to our look
(`CFxcPass.m_shading_broken`; `COwnPass.m_broken` is a failed frame's reason). Each game's title, backend and compiler come
from `render/nativelook.GAMES` (whose `pass_class` names the pass `gamepass.make_pass` builds).

**Anno 2205's own shaders** (Direct3D 11, FXC) draw these:

- `model_pbr`, glass, decal and frame-cycle materials, forward-lit by the game's passes and blend states.
- The light of its temperate ambient setting, by the engine's formulas, the sun's shadow map, the game's cube maps,
  haze, cloud shadows and the config's lights.
- Its particle effects by `particle.fx`, and its DECALs by `decal.fx` / `decal_pbr.fx`.
- Its post chain (bloom, filmic tone map, image adjustments; View > Rendering > Post chain) by
  `posteffect_aa_bloom.fx`. Under it, the view's ground and sky take the setting's light.
- Cloth, phong_ex, water, icon, interior-mapping (with its geometry shader), arctic, hair and planet materials
  (section 11 there).

Particles and decals the game's shaders draw are left out of our drawer and our decal pass. Our drawer keeps lines,
movies and the rest.

**Anno 1800's own shaders** draw the same way: its `ModelDefault` pass, the Old World's light, its own prefiltered sky
cubes, the config's lights and its shadow cascades. 1800 stores a quarter of its light for its post chain. With Post
chain off, its models and its sky are therefore multiplied back by four before the view's finish.

**Anno 1404's own shaders** (Direct3D 11, FXC, shader model 4) draw these (`viewer/fxcpass1404.py`, section 12):

- `model.fx`, forward lit on the Granny meshes,
- normal maps with tangent frames made from the uvs,
- its sun shadow, haze, scattered sky and post sequence,
- a dye material's player colour, and
- its sea under the model (Ocean waves).

**Anno 1701's own shaders** (Direct3D 11, FXC, its Direct3D 9 effects compiled as shader model 4) draw each material's
`mesh/` effect lit and fogged, its sun shadow and the dye effects' player colour. Where a pass has no pixel shader, an
entry of ours stands in for Direct3D 9's fixed-function stage by the pass's state block (`viewer/fxcpass1701.py`,
section 13).

Status and estimates: `tool-suite.md` section 21, "The migration, third run".

### Looks

Every game draws its models by its own shaders, so our own look is a **plain fallback**, not an imitation of a game.
The table says what the game's look covers, per game. The rest of the frame is the view's own in every game: the
ground plane where the game does not draw it, the background behind the sky, the selection box and the wireframe.

| Game | Drawn by the game's own shaders | Left to our look |
|---|---|---|
| 117 | every material's G-buffer or transparent pass, its shadows, deferred shading, lights, sky light, visible sky, haze, clouds, water with its ocean waves, the ground plane, colour grade, tone map, eye adaptation | the config's decals, cut-outs by their own pass, our particle drawer's effects |
| 1800 | `model_pbr`'s materials, its shadow cascades, sky cubes, visible sky with its cloud box, haze, lights, blended pass, post chain, eye adaptation, model water, its ocean with the small and large waves (Ocean waves, an open sea under the model) | its effects as our drawer plays them |
| 2205 | `model_pbr`, glass, decal, cloth, phong, water (refracting the models behind it), icon, interior and the other materials, its shadow, sky cubes (in the game's axes), visible sky, haze, cloud shadows, lights, particles, DECALs, post chain, its sea with the ordinary waves (Ocean waves, an open sea under the model) | SSAO (the game draws none), SSR (rivers and lakes only) |
| 2070 | its G-buffer, shadow, deferred shading, sky, clouds, lights, blended pass, model water, cloth, post sequence, its sea with the ocean's waves (Ocean waves, a deep sea under the model) reflecting the sky cube its sky shader renders | its effects as our particle drawer plays them |
| 1404 | `model.fx` and `model_dye.fx`, its shadow, haze, sky and clouds, model water with its caustics, post sequence, its sea's projected grid (Ocean waves, an open sea under the model) with the sun's shadow on it | its effects as our drawer plays them |
| 1701 | each material's `mesh/` effect, fog, shadow, sky dome, clouds, water sheets, the fixed-function stage stood in for, its sea's projected grid (Ocean waves, a sea under the model) with the sun's shadow on it | its effects as our drawer plays them |

**When the fallback shows**:

- a group whose shaders still compile (the first time a material is shown; later they come from the build cache),
- a group that cannot be made ready (from the next frame, "When a pass fails"),
- the whole scene, when a pass fails as a whole or the game's shaders cannot run on this machine (no compiler, no
  Direct3D), and
- when **Our plain look** is picked in View > Rendering.

Inside the game's look, a group that our look draws stands in the frame's own light (the light the game's pass hands
the view's ground and sky). Picked as a look, the plain look has the editor's light and finish.

**What the plain look draws** (`viewer/plainshading.py`, the island editor's `render/shaders.py`): the colour map, the
normal map, the sun with its shadow, the sky's ambient and its plain reflection, the config's point lights by a plain
falloff, and the haze. Textures, condition bits, time slices, animations, effects, picking and the editor's overlays
work as before.

**Not part of our look.** These only made our look imitate a game, and every game draws by its own shaders:

- the "Our 117 game look" and "Our Anno 2070 look" entries, and each game's own fallback look,
- the prefiltered sky reflection (`render/envmap.py`: 1800's sky cube and its BRDF table, 117's sky cube read back from
  the game's sky passes or prefiltered from our own sky),
- the dye mix (the newer games' "albedo times colour" and 2070's "colour over brightness"),
- the metal map,
- the gloss and glow channels of 117's normal maps, and
- 117's light falloff in our shading.

Numbers: `tool-suite.md` section 21, "The seventeenth run".

### When a pass fails

The games' own passes are recorded during a frame and run at its end, inside Qt's call that draws the view, where an
exception has no caller to reach. An exception that is raised there while a pass is made ready must not end a pass
that has not begun. Qt does not check this, and the whole process would go down. A failure is therefore handled as
follows (`render/nativepass`, `render/rhipass`, `viewer/ownpass`):

- **A model that cannot be made ready** (its constants, its resources, its pipeline) is left out of that pass
  (`nativepass.prepared`, every pass of models: the G-buffer, the forward and transparent passes, both shadow maps) and
  named in the device's list.
  - From the next frame, its group is drawn by our look.
  - The message line says once which part failed and why (`COwnPass._take_failures`).
  - A 117 group's shadow caster goes with it, and the ground plane falls back to the view's own ground.
- **A pass that fails as a whole** (the deferred shading, a compute stage, the post chain) is ended by the kind of pass
  it began, or not ended when it never began.
  - The frame goes on with the next pass.
  - The error is kept with the frame's errors.
  - The game's own shaders are switched off for the scene. Our look draws it, with the reason in the menu's place and
    on the message line.
  - The next scene tries them again.

What a game's pass draws in the view's place (its ground plane or sea, 2205's effects and decals) counts only for a
frame the pass drew. While it compiles, after it failed, or when a frame holds nothing for it, the view draws its own
ground, effects and decals (`CModelRenderer._by_game`).

Note: the failure was seen on 2026-11-12 with 1800's shadow caster.

### Rendering quality

**View > Rendering > Rendering quality** (117 and 1800 only; `viewer/renderingmenu`) offers the presets of
`render/quality.py` ([`rendering/quality.md`](rendering/quality.md)). They are kept per game in `Settings.quality` as
the island editor keeps them (default Medium; Custom only as the settings file sets it).

The preset goes into the view's state (`SViewState.quality`) and from there to the game's pass.

- Its effect switches (shadows, clouds, haze, post chain, eye adaptation) take those parts out as View > Rendering's
  switches do (`nativelook.with_quality`: a part draws only when both leave it on). This holds for 117's frame and
  1800's forward path alike.
- 1800's forward path draws its models at the preset's render scale (the composite stretches them over the view). It
  draws its shadow cascades in a map of the preset's size, reading the preset's number of cascades
  (`forwardpass.drawn_size`, `shadow_quality`).
- 117's frame takes its render scale, shadow map and cascades the same way.
- Every 2D map the view loads is cut to the preset's largest side (`max_texture`: Low 512) under the viewer's own 1024
  (`ModelFiles.texture`). The cubes and 1800's grade table are read as stored.

Not applied, with the reason:

- The level of detail and the draw distance: a viewer shows one model at its finest level, which is the reason to open
  it.
- The particle and cloth budgets: they share an island's many effects and cloths out, so one model's are not cut. 1800
  draws no particles by its own shader.

## Textures

A texture is a DDS (BC1 to BC5, BC7, BC6H, the uncompressed and float layouts, cubes and arrays), PNG, TGA or JPEG.

- **Channel** shows the colour over a checkerboard by its alpha, the colour alone, or one channel as grey (a normal
  map's alpha is its gloss, a mask's channels are separate maps).
- **Level** and **face** pick the mip level and the cube face.
- **Cross** shows a cube's six faces at once.
- An HDR texture (float layouts, BC6H) shows at an **exposure** in stops.
- **Zoom** fits the picture or scales it.

## Sounds

- **A Wwise bank** (`.bnk`, Anno 117, 1800, 2205) shows its version, its objects counted by kind, and these tables:
  - **Sounds**: the sound's name from the bank's info file beside it, the codec, whether its media is in the bank or
    streamed, and the media file.
  - **Events**: each event with its name. Playing one walks its actions and containers to its sounds and plays one of
    them, as a random container would. The count and whether the walk was read exactly appear when it plays. The walk
    is exact for the containers of all three games' banks (see `sound/bank-objects.md`).
  - **Music**: the music tracks with their segment, length and media. For 1800 and 2205 too, their music objects are
    walked exactly. Only the music switch's decision tree is not read, so a switch plays every branch.

  Selecting an event shows a **drop-down for each switch or state group** its containers follow (the group and value
  names from the init bank's info file where the game ships them; 2205's show as numbers). "(every value)" plays any
  branch as before, and a value plays only its branch. The switches and states the event sets itself are listed.

  Double-click a row (or select it and press **Play**) to hear it. A streamed sound plays its whole file, not only the
  start the bank keeps.
- **The older games' sound database** (`sounddatabase.xml` of 2070, 1404 and 1701), **a model's `.snd`** and **a spoken
  line's `.sp`** (`sounds.md` "The sound database of the old games"):
  - The database shows as tables of **Sounds** (id, file, kind, volume, how many at once, the zoom range, group),
    **Lists** (every list with its sounds, weights and files) and **Ambients** (channels with their pauses and sounds).
  - A `.snd` shows as a **Timeline**: per animation sequence its length and the sounds on it, with their files. The
    ids are read in the game's database, off the window's thread, and the rows follow when it is in.
  - A `.sp` shows as bars of loudness, 20 a second.
  - Double-click a row (or Play) plays its file. For a `.sp` it plays the MP3 beside it.
  - The Sound app edits the database and replaces clips.
- **A clip** (`.wem`, `.wav`, `.mp3`, `.ogg`) shows its format, channels, rate and length, and Play.
- Wwise Vorbis clips need the codebook file of the ww2ogg tool (`packed_codebooks_aoTuV_603.bin`). The installed program ships it in its tools folder; a checkout puts it in `tools/` (`tools/README.md`).
  PCM and ADPCM clips and the older games' audio files play without it.

## Data files

Text files (XML, Lua, JSON, configs) show as text. FileDB documents (`.a7m`, `.ifo`, `.fc` and the other level files
of the newer games) show as a tree of tags and attributes (an attribute's bytes, or its text when it is text).
Anything else shows as its first bytes.

## Export, import and inspect

**File > Export...** (Ctrl+E) exports the open file. **Export the selection or folder...** exports the file or the
folder selected in the file list (or the selected rows of a search). **Export the files the filter shows...** exports
every file the search text and the kind filter match (it asks first above 2,000 files).

The dialog offers, per kind of file present, **as stored** (always) and the converted forms:

- textures as PNG (8 or 16 bit), OpenEXR or Radiance HDR, a level, face or slice each or the largest only,
- pictures as PNG,
- movies as MP4 or WebM (needs ffmpeg, optional),
- sounds as WAV or OGG, a sound bank per sound and per event,
- models as glTF (`.glb` or `.gltf`, with the axes, strict, LODs and textures options),
- FileDB, XML, language texts, UI files and effects as JSON or CSV, and
- islands as height map and object list.

The export writes `index.json` and `index.csv` into the folder (source path, the archive it came from, kind, size,
files, status, notes). Every form and its limits: [`exporting.md`](exporting.md). A form that needs a tool that is not
installed is marked in the dialog, and the file is exported as stored.

**File > Import a model (glTF to .rdm)...** picks a `.glb` or `.gltf`, with the vertex layout, a file to follow, the
axes, the material order and the frame rate. It writes the `.rdm` files and shows the report: what was written, and
what the import dropped, rebuilt or refused (a refused model says why and writes nothing;
[`rdm-gltf.md`](rdm-gltf.md)). With a project open, **also add the model to the project's mod** keeps it under
`model-edits/`, and the build puts it into the mod at the game folder given.

The **Inspect** panel (beside Used by) lists every element of the open `.rdm` (meshes, submeshes, materials, skeleton,
animations). **Values** adds a checksum per vertex element, index list and track. **Compare with...** diffs it against
another `.rdm` or a glTF (as the `.rdm` its import would write).

## Between the apps (the suite's window)

- **Assets**: a field that names a file (a model, an icon) has **View** beside its "..." button. The file opens here.
- **Islands**: the selected object's **Show the asset** opens its asset in the asset editor, and **View the model**
  opens its config here.
- **Islands, Assets**: the selected object's **Export the model** and an asset field's **Export** open the export
  dialog on that file.
- **Viewer**: an asset in **Used by** opens in the asset editor. **File > Place on the open island** picks the open
  config for the island editor's Place tool.

## What a person should try

1. Open Anno 117, search `residence tier01 estate06 cfg`, open the config: the house with its trees, the cloth line and
   the smoke. Untick Particles, pick LOD 3 and switch off the shared/ copy.
2. Open a unit (search `priest lean cfg`, or `celtic guards cfg` for shields in hand). Press Play on its sequence, pick
   other animations in the list, and click a part of it.
3. Double-click a material's `cModelDiffTex`: the texture opens. Look at its alpha, step through its levels, and go
   Back.
4. Open a bank under `data/sound/generatedsoundbanks/windows/` and play a few sounds, events and music tracks. On the
   HDR or cube texture, try the exposure and the cross. Ctrl+U on a texture lists who uses it.
5. Do the same in Anno 2070 (a building under `data/graphics/buildings/`, its 2070 look), 1800, 2205, 1404 and 1701.

## Language

The viewer's windows are in English or German. Choose the language in the settings (General page, "Language").
It applies the next time the app starts. Game data (asset and file names, the games' own texts) is shown as the game
has it. See [the suite's guide](suite.md#language).
