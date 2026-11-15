# The particle editor (the suite's Particles app)

The Particles app opens a particle effect of any Anno game. It shows the effect's emitters as a tree, with their
fields as forms, curves as graphs and colours as pickers, and plays it live in the viewer's 3D view. It saves the
edited file into the open project, from where the project's build writes it into the mod. The design is in
`tool-suite.md` section 31, and the code is `src/annotoolbox/particleedit/`.

The app is based on the six installs on the author's machine and the readers this repository already has. **Nothing
here was tried in a game.** The list at the end says what the game must confirm.

## The files, per game

The counts are over every installed file on 2026-11-14 (the base games with every add-on and DLC the installs hold).

| Game | Effect file | Files | Emitters | Syntax | How the game plays it |
|---|---|---|---|---|---|
| 117 | `.rdp` | 417 | (baked) | tagged text with binary blobs | replays the stored ticks |
| 1800 | `.rdp` | 307 | (baked) | the same | the same |
| 2205 | `.rdp` | 414 (one of them empty) | (baked) | the same | the same, drawn by its `particle.fx` |
| 2070 | `.efc` | 304 | 711 | tags without a root (`<Effect0>` ...) | simulates the emitters |
| 1404 | `.efc` | 176 | 424 | the same tags as 2070 | simulates the emitters |
| 1701 | `.efc` | 208 | 630 | named blocks in braces | simulates the emitters |

No install holds an `.rpfx` file: the newer engines' particle file is the `.rdp`. The newer games also ship
`gpu_particles.fx` (117) and sprite sheets as Bink movies for the interface (`fx_*.bk2`). Neither is an effect file
that a model places, so the app does not open them.

Every one of these files reads into the app's document and writes back **byte for byte** when nothing changed
(the installed test, 2026-11-14: all 1,826 files of the six games).

### 117, 1800 and 2205: the baked effect (`.rdp`)

The format is described in `engine-particles.md` "Particles": `<Tag>value</>` lines, nested
blocks closed by `</>`, and `CDATA[<u32 length><bytes>]` blobs. The engine never simulates. It replays, per particle,
a first and last frame and a material, and per tick a position, rotation, scale and colour. The three games write the
same tags. 2205 leaves out `DoSort` and `Invincible` in some files, and 117 adds the most. The 2205 file
`data/graphics/particles/electricity/electricity_01.rdp` is empty (0 bytes); the app opens it as empty and read only.

**The header** (every file but the empty one):

| Field | What it does | Values found |
|---|---|---|
| `FrameCount` | frames of the strip, at most 256 | 1 to 256 |
| `FrameTime` | milliseconds a frame lasts: the pace of the whole effect | 33.333 in most, longer for slow effects (903 in 1800's letters) |
| `Looped` | 1: the strip repeats; 0: it plays once when started | both |
| `ParticleCount`, `TotalTickCount` | how many particles and ticks the blobs hold | written by the bake; **read only** |
| `SelfOcclusion`, `Lighting`, `DoSort`, `Invincible` | shaded from the effect's centre; lit by the sun; particles sorted far to near; kept when the camera is far (the names; their effect is the game's) | 0 / 1 |
| `MaxScale` | the size a scale byte of 255 means, in metres: **scales every particle** | 0.01 to a few metres |
| `Center`, `Extents` | the box the positions are fractions of: moving the centre moves every particle, the extents stretch the cloud | metres |
| `TexturePath` | the texture all materials show (a `.psd` name is loaded as `<stem>_0.dds`) | 128 of 117's files, 188 of 1800's, 331 of 2205's; the others name it per material |
| `NormalMapPath`, `MaxSH`, `DepthScale`, `ParticleCenterOffset`, `ComplexLighting`, `TerrainAdapt`, `DisableViewAngleFade`, `AlbedoRampPath`, `EmissiveRampPath`, `LightmapRTBkPath`, `LightmapLBtFPath`, `ChurnParticle`, `DistanceFade` | the newer files' extra tags, shown as they are (no reader of this repository interprets them) | 117 has all of them, 1800 three |

**A material** (`Materials`, 1 to 8 per file; 16 in 2205's shader limits):

| Field | What it does |
|---|---|
| `ViewAligned` | 0 the quad turns with the emitter, 1 it faces the camera, 2 it stands upright |
| `Order` | the draw order of the materials' batches (a byte) |
| `ColorBoost` | the colour is lifted by `1 + (boost x 0.07) ^ 2.2` (a byte; 117 also writes `0.000000`) |
| `Additive` | 1 adds the colour; 0 blends by alpha |
| `Refractive`, `WaveParticle`, `OpaqueWithDepth` | the refraction list; raises the ocean's waves instead of drawing; cut at alpha 0.1 and depth-written (engine-particles "Particle lists") |
| `LineType`, `LineTiling`, `LineLength` | 0 a quad, 1 and 2 a ribbon; the ribbon's texture repeat and length |
| `cropUVWH` | the part of the texture shown: `x`, `y` a corner, `z` width, `w` height |
| `MovieTexPath`, `MovieFPS`, `MovieNormPath`, `MovieMotionPath`, `MovieMotionMultiplier`, `BlendTexIntensity` | a flipbook sheet the material shows (its own texture), its pace, its normal and motion sheets |

**The blobs** `P_Starts`, `P_Ends`, `P_Materials`, `T_Positions`, `T_Rotations`, `T_Scales`, `T_Colors` (and
`T_Occlusion` in 36 of 117's files) are what the bake wrote. The app shows their sizes and changes them only as a
whole. **Tint** multiplies every tick's colour and alpha by a picked colour (`T_Colors` is stored as blue, green, red,
alpha). **Fade** sets the alpha of the first and last tick of each particle to 0, as vanilla files do. Curves over a
particle's life do not exist in this format: what a 2070 or 1701 emitter has as a curve is baked into the ticks here.

### The cfg particle parts (117, 1800, 2205)

A model config places an effect with a `PARTICLE` block in its `Particles` list. The fields below were read from the
fireplace of 117 (`data/base/graphics/celtic/buildings/ornaments/hall_of_fame/fireplaces/fireplace_01.cfg`) and from
engine-particles "The cfg side":

| Field | What it does |
|---|---|
| `Transformer` | where the effect stands in the model (an `ORIENTATION_TRANSFORM`, or a `BONELINK_TRANSFORM` on a joint) |
| `Name`, `FileName` | the effect's name in the config; the `.rdp` it plays |
| `TimeScale` | how much slower the strip plays (2: half the pace) |
| `WindImpact` | how much the wind pushes the particles |
| `IsEmitterBound`, `AdaptTerrainHeight` | particles follow the model as it moves; the effect sits on the ground's height |
| `VisibleFlags` | the condition bits the effect waits for |
| `DelayFadeOut` | when the effect is switched off, the living particles finish |
| `Color`, `Alpha`, `EmissiveIntensity` | a tint, an opacity and a glow over the whole effect |
| `ReceiveShadows`, `SoftParticlesEnabled`, `UseDepthBias`, `EarlyPass`, `VolumeBlended`, `AlwaysVisible`, `DarkenAtNight`, `BypassExposure` | the newer switches of the draw (the viewer's game-shader look reads the first three, shader-compile.md section 11) |
| `SubImageSize`, the four distance tags with a literal semicolon (`<DistanceScaleStartDistance;>`) | a flipbook's tile count; fading by distance |

A sequence starts a particle by its index in the list (`Type` 1, `StartTime`, `EndTime`, `ParticleID`). The object
builder edits all of this as config fields. The Particles app shows the block of the effect it opened from a model, and
saves it with the config.

The older games place an effect in the config too: 2070 and 1404 with an `EFFECT` in `Effects` (`FileName`,
`Transformer`, a visibility mask, a colour), 1701 with a `.mcf` part of class 3 (`m_EffectFilename`,
`m_EffectStartParameter`, `m_EffectTimeOffset`, `m_EffectLoopDuration`, `m_EffectSize`, `m_EffectWindForce`,
`m_EffectRotateFree`).

### 2070 and 1404: the emitter list (`.efc`)

The file holds tags without a root element: `<Effect0>`, `<Effect1>` and so on. Each one is an emitter, with its
values as `<Name>value</Name>` lines (the reader is `models.particles2070`; the format is in engine-particles
"Particles"). The two games write the same tags, with two exceptions. 1404 has `Blending` (0 in 236 emitters, 1 in
173, 2 in 11, 3 in 4) where 2070 has `Technique`, and it has `LEmitter`, `Reflect`, `Refract` and `Shadow` where 2070
has `Reflection`.

**The header:**

| Field | What it does |
|---|---|
| `Name` | the emitter's name in the studio's tool (`NoobEffect0`, `RenameMe0`) |
| `EffectType` | 1 particles (2070: 472 emitters, 1404: 296), 2 a mesh (114, 28), 3 a billboard (83, 90), 4 a line (15, 5), 5 mass (0, 1), 6 an arrow (1, 4), 7 a flame (26, 0) |
| `Texture0` (`Texture1`, `Texture2` always empty) | the texture; an atlas whose tile `AtlasOffsetU/V` to `AtlasTilingU/V` (the opposite corner) is shown |
| `Shader` | the effect shader (`effect_particle.fx`, `effect_model.fx`) |
| `Technique` (2070) / `Blending` (1404) | 2070: 0 alpha blend, 1 additive, 2 refract, 3 z-write, 4 no depth test, 5 the water's wave map; 1404's numbers are taken to mean the same for 0 and 1 (not checked): the viewer's effect reader (`models.particles2070.parse_efc`) reads `Blending` where an emitter has no `Technique`, so 1404's additive emitters are drawn additive; its 2 and 3 (15 emitters) are not known and are drawn as alpha blend |
| `Delay`, `LifeTime`, `Duration` | ms: the start after the effect, a particle's life, how long the emitter emits |
| `Loop`, `Bound` | 1 repeats; 1 the particles follow the emitter |
| `OffsetX/Y/Z` | the emitter's place in the effect |
| `Alignment` | 0 faces the camera, 1 upright, 2 fixed |
| `WindDependency` | 0 to 1: how much the wind moves the particles |
| `Model`, `TexOffset` | a mesh emitter's `.rdm`; its texture's random offset |
| `RenderTarget`, `LReceiver`, `LEmitter`, `Reflection` / `Reflect`, `Refract`, `Shadow`, `Heat`, `Glow` | the draw's targets and switches (the names; no reader here interprets them) |

**The curves.** A curve `X` is five tags and its knots:

- `XValueMin` and `XValueMax`: the range
- `XValueDef`: the value without knots
- `XVarianz`: a per-particle factor in `[1 - 2 var, 1]`
- `XSize`: the knot count
- per knot `i` (in hex): `XKnotPos_i` (0 to 1 along the particle's life, or along the emitter's duration for the
  emitter's curves) and `XKnotX_i` to `XKnotW_i` (fractions of the range)

Values are straight between knots and held outside them. The files hold these curves:

| Curve | What it is | 2070 / 1404 emitters |
|---|---|---|
| `PEP` | milliseconds **between** two particles | 711 / 424 |
| `EmitArea` | the radius of the ring particles are born on | 586 / 325 |
| `EmitterRotX/Y/Z` | the emitter's turn over its duration | 711 / 385 |
| `VelocityX/Y/Z` | the displacement from the spawn point over the life (not a speed) | 671 / 414 |
| `GravityX/Y/Z` | written, never read by the simulation the viewer runs | 586 / 324 |
| `Scale` (`ScaleY`, `ScaleZ` for meshes) | half the particle's size | 711 / 423 |
| `Rotation`, `RotationX/Y/Z` | the particle's angle over its life | |
| `Color` | the colour: knots X, Y, Z are red, green, blue | 711 / 424 |
| `Alpha` | the opacity | 711 / 424 |
| `TexOffsetTU/TV`, `AnimationSpeed`, `NoiseFrequency` | a mesh's texture scroll; a flame's flicker | 2070 only |

### 1701: the brace `.efc`

The syntax and the kinds are described in [`formats/anno1701-effects.md`](formats/anno1701-effects.md). A file is one
`Effect` block with an emitter block per kind (`ParticleEffect` 305, `BillboardEffect` 150, `InstancingEffect` 53,
`SprayEffect` 43, `InstancingSlowEffect` 38, `GrannyEffect` 20, `FireWorxEffect` 9, `SuperNovaEffect` 8,
`VolumeChainsEffect` 4). Each emitter has its header, plain numbers, **graphs** (`Minimum`, `Maximum`, `Single`,
`Value`, `Linear` and `Point` blocks of `p` x y, tangents `i` and `o`, `udt`) and **colour lists** (`Color` blocks of
`RGBA` and `Time`). The instancing kinds write `Loop` twice. Both lines are kept and shown, and the second is the one
the viewer plays.

### What the preview draws of the 1701 kinds

The viewer's reader draws `ParticleEffect`, `BillboardEffect`, `InstancingEffect`, `InstancingSlowEffect` and
`SprayEffect` ([`formats/anno1701-effects.md`](formats/anno1701-effects.md)). The kinds in the table below are drawn
from the fields the files hold. Every rule is inferred from the field names and values, and nothing was seen in the
game:

| Kind | Emitters | The preview |
|---|---|---|
| `FireWorxEffect` | 9 | a ball of sparks at `DelayTime`: `CountX2` rings of `SegCount` sparks, flying out to `Size` across and `Height` up over `Move` ms (fast first), living `LifeTime`, fading over the last `FadeTime`; `RandomColorFlag`: one colour of the `Color` list for the whole burst, else the list runs over a spark's life. A spark is `StartWidth` x 12 units wide (the file gives no size). `Gravity` (0.0051: its unit is not known) and `Stretch` are not played |
| `SuperNovaEffect` | 8 | a ring of `SegCount` cards that widens to `Size` over `Move` ms, a band `Spread` deep when full, coloured by the list, fading over the last `FadeTime`; `Flat` 1 lays the cards on the ground along the ring, else they face the camera. `TexRepeat`, `TexSlide`, `StartWidth` are not played |
| `GrannyEffect` | 20 | not drawn: it shows a `.gr2` model by graphs, and the preview plays sprites; a model is the viewer's mesh view's |
| `VolumeChainsEffect` | 4 | not drawn: `Stretch`, `Spread` and `Move` say how long the chain is, not how its links move |

A kind that is not drawn is named in the viewer's notes with that reason. The tree and the forms still edit it.

Note: the table's kinds were added on 2026-11-14.

## How mods replace or add effects

- **117 and 1800**: a mod's file at a game path wins over the game's archives, so an edited `.rdp` at its own path
  replaces the effect wherever it is placed. 117 reads a vanilla `data/` model config's baked `shared/` copy in its
  place (`engine-modloader.md`, "the `shared/` copies"). The config of an effect placed in such
  a model is that copy, and a patch of it is a `shared/...cfg.xml`. The effect files themselves have no `shared/`
  copies (none of the 417). To give one model an effect of its own, the mod adds the `.rdp` at a new path and changes
  that model's config, either with a ModOps patch (`.cfg.xml`) that sets its `FileName` or by replacing the whole
  config.
- **2205, 2070, 1404, 1701**: there is no mod loader. A mod is files in an archive that the game reads after its own
  (the project's delivery, `tool-suite.md` section 18). The same two ways apply with whole files: the
  effect at its own path, or a new effect file and the config that names it, replaced whole.
- **A new effect** is a file at a path of the mod's own. It plays once a config names it (the object builder adds a
  `PARTICLE` or `EFFECT` part for it).

## The app

- **Open.** File > Open game, then a double-click on an effect file in the game's files (the viewer's file browser),
  or File > Open from a model: a model config of the open game, then one of the effects it places. Another app opens
  an effect through the link `edit_effect` (game, path, model config). Reading the file, building the model's scene
  and the preview's bake run off the window's thread.
- **The tree.** It shows the file, then one row per emitter (2070, 1404, 1701), or the header, each material and the
  tick data (`.rdp`). When the effect was opened from a model, it also shows the config's block that places the
  effect. Each row's fields are a form on the right: numbers, switches as ticks, the known choices as a list
  (`EffectType`, `Technique`, `Alignment`, `ViewAligned`), and texture and file names as text. A field's tool tip says
  what it does (see the tables above). A typed value is **refused** with its reason when it:
  - is no finite number, or no whole number where one is needed
  - is not 0 or 1 for a switch
  - is outside a field's range (`Order` and `ColorBoost` 0 to 255, `FrameTime` 1 to a million, `WindDependency` 0 to
    1, times and lengths from 0, `TimeScale` above 0)
  - is not one of a list's values
  - holds a control character, `<`, `>`, a brace, a `"` in a name or `CDATA[` (it would break the file)

  An edit that changes no byte (a click on a graph's point) is no edit: it makes no undo step and does not mark the
  file.
- **New emitters** (2070, 1404, 1701): see "New emitters" below.
- **Curves** (2070, 1404: every curve; 1701: every graph) are drawn as editable graphs below the form. Drag a point,
  double-click to add one, and right-click to remove it. The range, the default and the variance are fields beside the
  graph. Colour curves and colour lists are a band of colour stops; a double-click on a stop opens the colour picker.
- **The preview.** The viewer's 3D view draws the effect alone, or in its model when it was opened from one. It draws
  with the viewer's particle drawer, or with the game's own particle shader where the viewer has it wired (2205's
  `particle.fx`; View > Rendering, the viewer's menu). The controls are play, pause, a slider to scrub, loop on or
  off, and the pace. An edit redraws the preview a moment later.
- **Undo** and redo of every edit, one step per edit. The title marks unsaved edits.
- **Save** into the open project (`particle-edits/<game>/<game path>`), from where the project's build writes it into
  the mod. There are three ways to save:
  - the effect at its own path (it replaces the game's everywhere)
  - a copy named for the model's config (`<config folder>/<config name>_<effect name>.<ext>`), with the config pointed
    at it: a ModOps patch in 117 and 1800, the whole config in the other games
  - a new file at a path of your own

  A file that does not read back byte for byte, and the empty 2205 file, are shown and not saved. The effect at its
  own path is not saved while it is the game's own file (a copy that changes nothing would only hide what other mods
  change in it). A saved effect opens again with its edits, and the preview shows them (not the game's file the edits
  started from).
- **Edition.** The app is part of the public Toolbox.

**From the viewer**: File > Edit the effect in the particle editor opens an effect through `edit_effect`, with the
model config that places it (`viewer.effectlink`). It opens the effect of the part selected in the Parts panel (a
`PARTICLE` or `EFFECT` part), or else the only effect of the open model, or the effect file itself. The entry is shown
when the suite provides the Particles app. File > Open from a model does the same inside the app.

### New emitters

There are three ways to add an emitter, each one undo step:

- Edit > Add an emitter like the selected one appends a copy of the selected emitter at the end of the file and
  selects it.
- Edit > Add an emitter of a kind the file has does the same from the first emitter of a kind the file holds.
- Edit > Add an emitter of a kind the file lacks copies the first emitter of that kind from another effect file of the
  game. The files do not say what a blank emitter holds, so a game's own emitter is the template. The game's `.efc`
  files are read once in the background (about a second), and only kinds of the file's own syntax are offered.

The copy is made to start with the effect, at its origin: `Delay` and `OffsetX/Y/Z` are 0 (2070, 1404), `StartDelay`
is 0 and `Position` is 0 0 0 (1701). Its name is `Copy of <name>` (the name is the studio tool's own, 1701's files
hold them; a second copy of the same name gets ` 2`), and 2070 and 1404 number it `Effect<n+1>`. Everything else
(texture, curves, colours, the other fields) is the template's, so the new emitter is of that kind and plays at once.
An `.rdp` has no emitters (the effect is baked), so the entries are off for it.

Edit > Remove the selected emitter takes it out (one undo step; an effect keeps at least one emitter). In 2070's and
1404's files the emitters after it take the tag of the one before, so `Effect0`, `Effect1` ... stay without a gap, as
in every file of the games. In 1701 the emitter's lines are removed.

## What to check in the game

None of this was tried in a game (as of 2026-11-14).

- [ ] 117: an `.rdp` edited and saved at its own path (a tint, `MaxScale`, `FrameTime`) shows the change on every model
  that places it.
- [ ] 117: a copy for one model: the `shared/...cfg.xml` patch points the model at the copy, the other models keep
  the vanilla effect.
- [ ] 1800: the same two, and whether its loader patches configs as 117's does.
- [ ] 2205: an edited `.rdp` through the project's archive.
- [ ] 2070 and 1404: an edited curve (`Scale`, `Color`, `PEP`) plays as the preview shows; a copy with its config
  replaced.
- [ ] 1701: an edited graph and colour list play as the preview shows.
- [ ] 1404: whether `Blending` 1 is additive like 2070's `Technique` 1 (the viewer draws it additive, and 2 and 3 as
  alpha blend: what do they do?).
- [ ] 2070, 1404, 1701: a new emitter (Edit > Add an emitter ...) saved and played: the copy plays beside the template.
- [ ] 1701: a firework (`fireworks_01.efc`) and a ring (`addon01/meteor.efc`) against the preview: the burst's size and
  pace, whether the ring lies flat on the ground and how deep its band is, what `Gravity` and `Stretch` do.
- [ ] 1701: `GrannyEffect` and `VolumeChainsEffect` as the game draws them, for the day the preview does.
- [ ] 117: the viewer's File > Edit the effect in the particle editor on a building with an effect opens that effect.
