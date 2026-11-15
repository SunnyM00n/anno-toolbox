# Anno 1701's effect files (`.efc`)

This page is hand-written. It was read from the History Edition's 208 `.efc` files (`data/graphics/effects/efc/`).
The reader is `src/annotoolbox/formats/efc1701.py`. The viewer makes its particles from it in
`src/annotoolbox/models/particles1701.py`. The file format below is what the files hold. **How the game plays the
values is inferred** from their names, their ranges and what the effects are for (smoke, fire, fountains). Nothing
here was checked in the game. 2070's `.efc` is XML and another format
(`../engine-particles.md`).

Note: written on 2026-11-13.

## Syntax

Plain text (Latin-1, CRLF), indented with tabs. A file is one `Effect` block. A block is a name and its body in
braces on the lines below. Inside a body, each line is a field or a nested block. A field is a name and its values:
numbers, or one string in double quotes (`""` for none). Paths in strings use backslashes
(`data\graphics\textures\...`).

```
Effect
{
	ParticleEffect
	{
		Name "ParticleEffect0000"
		Duration 1000
		Position 0.000000 2.572962 0.000000
		Emit
		{
			Minimum 0.000000
			Maximum 100.000000
			Single 1
			Value 41.099998
			...
```

`Effect` holds one block per emitter, each named by its kind. The table lists the kinds and how many emitters of
each kind the files hold:

| Kind | Emitters | The viewer |
|---|---|---|
| `ParticleEffect` | 305 | drawn: particles by its graphs |
| `BillboardEffect` | 150 | drawn: one card by its graphs |
| `InstancingEffect` | 53 | drawn: puffs (smoke, fire) by its plain numbers |
| `SprayEffect` | 43 | drawn: puffs thrown out and pulled down |
| `InstancingSlowEffect` | 38 | drawn as `InstancingEffect` |
| `GrannyEffect` | 20 | not drawn: a `.gr2` (`FileName`) shown by `Size`, `Height`, `Alpha`, `Rotation`, `Color` graphs (a model, not a sprite) |
| `FireWorxEffect` | 9 | drawn (inferred): a burst of sparks (`CountX2`, `SegCount`, `Size`, `Height`, `Move`, `FadeTime`; `Gravity` not played) |
| `SuperNovaEffect` | 8 | drawn (inferred): a widening ring of segments (`SegCount`, `Size`, `Spread`, `Move`, `Flat`) |
| `VolumeChainsEffect` | 4 | not drawn: chains of two textures (`Texture0`, `Texture1`, `Stretch`, `Glow`); how the links move is not in the file |

Note: the drawing of `FireWorxEffect` and `SuperNovaEffect` was inferred on 2026-11-14.

## Every emitter's header

| Field | Values |
|---|---|
| `Name` | the studio tool's name (`"ParticleEffect0000"`, `"Copy of ..."`) |
| `StartDelay`, `Duration`, `Pause` | ms: when the emitter starts after the effect, how long one pass lasts, the pause (always 0) |
| `Loop` | 1 repeats the pass; the instancing kinds write a second `Loop` further down, the one the viewer takes |
| `StateBlock` | the render state from the engine's state-block file (`ParticleEffect`, `FX_SMOKE`, `FX_FIRE`, `BillboardEffect`, `FireWorks`, ...) |
| `SrcBlend`, `DestBlend` | Direct3D blend names without `D3DBLEND_` (`""` the state block's own; `ONE` adds) |
| `Position`, `Rotation`, `ScaleAxis`, `ScaleFactors` | the emitter in the effect: a move (world units, 32 a tile), a quaternion x y z w, an unused axis, a scale per axis |
| `SoundID` | a sound started with the emitter (0 none) |
| `PositionSpline` | `Linear` and `Point` blocks (`t`, `p` x y z, in- and out-tangents `i`, `o`): a path the emitter moves on; only the start point in every file looked at |
| `Texture` (or `Textures` with `Texture000`) and `Rect` | the texture file (`.tga` as named) and the part of it shown: `u0 v1 u1 v0` (left, bottom, right, top) |

## Graphs

Most of a `ParticleEffect`'s and a `BillboardEffect`'s numbers are graph blocks:

| Field | Meaning |
|---|---|
| `Minimum`, `Maximum` | the graph's range |
| `Single`, `Value` | `Single 1`: the graph is `Value` throughout |
| `Linear` | 1 straight lines between the points; 0 smooth curves through them by their tangents |
| `Point` | `p` x y (x 0..1 along the graph, y 0..1 between `Minimum` and `Maximum`), tangents `i`, `o` (offsets from `p`), `udt` (0 or 1; meaning not known) |

A colour graph (`Color`) is a list of `Color` blocks instead, each `RGBA` (0..1) and `Time` (0..1).

**Inferred:** the emitter's numbers (`Emit`, `LifeTime`, `Spread`, `EmitterRadius`, `Pitch`, `Yaw`, `PitchVar`,
`YawVar`, `Speed`) run along the emitter's `Duration`. The particle's numbers (`Color`, `Alpha`, `SizeX`, `SizeY`,
`Spin`, `Acceleration`, `AxisRotation`, `GravityX/Y/Z`) run along its life. The units:

- `Emit` is particles a second and `LifeTime` is ms.
- `Speed` is world units a second along the direction given by `Pitch` and `Yaw`, each varied by its `...Var`.
- `Pitch` is the elevation above the horizontal. pi/2, straight up, is the value most emitters hold. The fountains'
  2.5 throws up and out against their negative `GravityY`.
- `Yaw` turns about the up axis. y is up (steam rises by a positive `GravityY`).
- `EmitterRadius` is the sphere a particle starts in.
- `Gravity` is world units a second squared.
- `SizeX` / `SizeY` are the card's width and height, and `Spin` is radians a second.

Other fields: `Lighting`, `LockParticlePos` (particles move with the emitter), `AlphaBlending`, `Alignment` (0 faces
the camera; 1 and 2 not known: drawn facing it), `TimeScale` (the pace: 1 its own) and `RandomSpin`. A
`BillboardEffect` also has `Pivot` and `XMove`, `YMove` (graphs; a texture scroll, not drawn).

## The instancing kinds

`InstancingEffect`, `InstancingSlowEffect` and `SprayEffect` take plain numbers:

- `LifeTime` and `DelayTime` (ms);
- `MinSize` and `MaxSize` (a puff grows from one to the other over its life; world units);
- `EmitRadius` (the circle the puffs start in);
- `Velocity` (world units a second; upward, or outward for a spray);
- `ColorRed/Green/Blue` to `TargetRed/Green/Blue` (0..255, start to end of life);
- `Varianz` (a random spread of the start);
- `Light`, `PPS` (puffs a second), `RPS` (turns a second, `InstancingSlowEffect`) and `EmitterBound`.

`SprayEffect` throws `Particles` puffs from each of `Emitters` points, between `MinDistance` and `MaxDistance` out,
pulled down by `Gravitation`. **Inferred** as above.

## In the viewer

`efc1701.read_efc(text)` gives the emitters with their header, plain fields, graphs and colour lists.
`particles1701.bake(emitter, seed)` runs one of the drawn kinds into the sprite tracks that every effect of the
viewer plays from (30 ticks a second, a loop of whole passes so it repeats without a seam). It uses a fixed random
seed per emitter, so a view looks the same each time. Where a model's `.mcf` places an effect (class 3), the effect
plays at that place, scaled with the model (one view unit a tile).
