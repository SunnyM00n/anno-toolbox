# Granny 2 (`.gr2`): the meshes of Anno 1404 and Anno 1701

This page is hand-written. It was read from the History Editions' own files and checked against them. The reader is
`src/annotoolbox/formats/grannyread.py`, the section decompressor is `grannyoodle.py`, and `grannyglb.py` gives
the meshes as glTF. The island editor draws them through `othergames/grannymodels.py` and `editor/grannyviewer.py`
([`island-editor.md`](../island-editor.md), "Other games"). Nothing here was checked in either game.

These public descriptions were used as references (no code copied):

- **opengr2** (`libopengrn`, MPL-2.0): the header, file info, section table, fixups, member kinds and the magic
  values;
- **xoreos-tools** (`granny-decoder`, Boost Software License 1.0) by way of **nwn2mdk** (`gr2_decompress.cpp`,
  Apache 2.0): the Oodle1 decoder, which `grannyoodle.py` follows algorithm for algorithm in its own code.

Note: written on 2026-10-27 and 2026-11-13.

## What the games ship

| | Anno 1404 + Venice (History Edition) | Anno 1701 (History Edition) |
|---|---|---|
| `.gr2` files | 5,308: 4,233 32-bit, 1,075 64-bit | 3,639, all 64-bit |
| Revision, sections | 7, 8 sections in every file | 7, 8 sections in every file |
| Section compression | Oodle1 (41,304 sections), stored (1,160) | Oodle1 (25,473), stored (3,639) |
| Exported from | 3ds Max 9 (`ArtToolInfo`), 1 unit a metre and a tile, y up, back = -z | 3ds Max, 39.37 units a metre (inches), 32 units a tile, y up, back = -z |
| Named by | the `.cfg` model configs (2070's format, below) | `.ocf` object configs -> `.mcf` mesh configs (below) |

A sample of 150 (1404) and 100 (1701) files was read whole, without a failure. About a quarter hold animations only
(no mesh). Python decodes a file in 0.05 to 1.2 s (the biggest is about 230 KB). The editor caches what it made of
each.

## File layout

Little-endian in Anno's files (the format has big-endian variants; `MAGICS` lists only what Anno ships).

| Offset | Size | Field |
|---|---|---|
| 0 | 16 | magic: `29 DE 6C C0 BA A4 53 2B 25 F5 B7 A5 F6 66 E2 EE` (32-bit pointers, revision 7) or `E5 9B 49 5E 6F 63 1F 14 1E 13 EB A9 90 BE ED C4` (64-bit pointers, revision 7) |
| 16 | 4 | header size (`0x1C8` in every Anno file) |
| 20 | 4 | header format (0) |
| 24 | 8 | reserved |
| 32 | 4 | **file info**: revision (7) |
| 36 | 4 | total file size |
| 40 | 4 | CRC32 of everything from the section table on (offset 104: the table, the stored data, the fixups) |
| 44 | 4 | offset of the section table, from the file info (72) |
| 48 | 4 | section count (8) |
| 52 | 8 | root type: (section, offset) |
| 60 | 8 | root object: (section, offset) |
| 68 | 4 | type tag (`0x8000002E` in 1404's files) |
| 72 | 32 | reserved (revision 7) |

The section table has 44 bytes (eleven u32) per section:

- compression (0 stored, 1 Oodle0, 2 Oodle1; 3 and 4 Bitknit, newer than Anno's files);
- data offset in the file, stored size, size and alignment (4);
- stop 0 and stop 1 (the ends of the 4-byte and 2-byte parts of the data, which Oodle1 codes with separate models
  and a byte swap would handle by width);
- fixup table offset and count;
- marshalling table offset and count (for byte-swapping a foreign file; empty in Anno's).

**Fixups** (pointer relocations) are 12 bytes each, stored uncompressed: source offset in this section, target
section, target offset. A pointer field holds nothing useful in the file; a fixup says where it points. A pointer
without a fixup is null. `GrannyFile.pointer_at((section, offset))` answers from the table. Nothing is patched.

In the files looked at, section 0 holds the root object and the mesh records, and section 6 holds the type tree. The
vertex and index arrays sit in the sections in between.

## Type trees

Every record in the file is described by a type the file carries: an array of member definitions ending with a
member of kind 0. A definition is `u32 kind, pointer name (C string), pointer target type, i32 array width, 3 x
u32 reserved, pointer reserved`: 32 bytes with 32-bit pointers, 44 with 64-bit ones (packed, no padding). Records
are packed the same way: a member takes the size below times its array width (at least 1).

| Kind | Member | Size in a record | Read as |
|---|---|---|---|
| 0 | end of the type | - | - |
| 1 | inline record | the target type's size | `GrannyObject` at the same place |
| 2 | reference | pointer | the target type's record, or None |
| 3 | reference to array | i32 count, pointer | `GrannyArray` when the target type is plain numbers, else a list of records |
| 4 | array of references | i32 count, pointer to pointers | list of records |
| 5 | variant reference | pointer to its type, pointer | the record in the type it names |
| 6 | (removed) | 0 | - |
| 7 | reference to variant array | pointer to its type, i32 count, pointer | as 3, in the type it names (the vertices) |
| 8 | string | pointer | `str` (Latin-1) |
| 9 | transform | 68: flags, position (3 f32), orientation (4 f32), scale and shear (3 x 3 f32) | dict |
| 10 | real32 | 4 | float |
| 11 / 12 | int8 / uint8 | 1 | int |
| 13 / 14 | binormal int8 / normal uint8 | 1 | int (`grannyglb` scales to -1..1 / 0..1) |
| 15 / 16 | int16 / uint16 | 2 | int |
| 17 / 18 | binormal int16 / normal uint16 | 2 | int (scaled as 13 / 14) |
| 19 / 20 | int32 / uint32 | 4 | int |
| 21 | real16 | 2 | float |
| 22 | empty reference | pointer | None |

Root object (Granny's file info): `ArtToolInfo` (the exporter's units and axes), `ExporterInfo`, `FromFileName`
(the `.max` file on the studio's drive), `Textures`, `Materials`, `Skeletons`, `VertexDatas`, `TriTopologies`,
`Meshes`, `Models`, `TrackGroups`, `Animations`, `ExtendedData`.

## Meshes

What `grannyglb.read_meshes` takes from each of `Meshes`:

- `Name`; `PrimaryVertexData.Vertices`, a variant array whose type names the vertex fields: `Position`, `Normal`,
  `Tangent`, `Binormal`, `TextureCoordinates0` (all real32 in the 1404 files looked at), on skinned meshes also
  `BoneWeights` / `BoneIndices`. Rigid meshes store their vertices in model space (the bone's oriented box agrees
  only after the bone's offset is taken away), so they are drawn as stored.
- `PrimaryTopology`: `Groups` (`MaterialIndex`, `TriFirst`, `TriCount` in triangles), `Indices` (int32) or
  `Indices16` (16-bit, declared signed, read unsigned).
- `MaterialBindings[MaterialIndex].Material.Name`: the name the game's config gives the material's textures by.
  The materials' own `Maps` and 3ds Max data (`ExtendedData`: the bitmap paths on the studio's drive) are not used.

Texture coordinates are used as stored: the textures land right on 1404's and 1701's buildings, trees and rocks
(editor pictures).

Note: the editor pictures were made on 2026-10-27.

## Oodle1

Each section is decoded in three runs: bytes `[0, stop0)`, `[stop0, stop1)` and `[stop1, size)`. Each run has fresh
models, but all three share one continuous coded stream. The stream starts with three 12-byte parameter blocks (one
per run). In the first word, bits 0-8 are the literal alphabet size and bits 9-31 the longest match offset. In the
second word, bits 0-8 are the literal model's capacity and bits 19-31 the high offset model's capacity. Then come
four bytes of size model capacities.

- **Coder**: an arithmetic decoder over the bit stream from its second bit on. A value below `top` is
  `numer / (denom / top)`, and the last value of a range takes the remainder.
- **Models** ("weigh windows"): each keeps the values seen so far with weights, and a range table rebuilt as the
  total grows (weights halved, empty slots dropped, the heaviest moved last once the total passes a threshold).
  Slot 0 is the escape: a value not seen yet follows flat below the model's alphabet size, or one added since the
  last rebuild is picked by its index.
- **Tokens**: a size symbol (65 values, modelled by the previous size symbol: 4 x 16 + 1 models) is 0 for a literal
  (one byte, modelled by its position modulo 4), or it is a match: lengths 2 to 61, then 128, 192, 256, 512. A
  match's offset is coded as low 2 bits, high bits (above 10, alphabet limited by what the run has decoded) and
  middle 8 bits (one model per high value). The copy may overlap itself.

The stream is read one byte ahead: the decoder fills it out with four zero bytes. `tests/test_granny.py` holds an
encoder for exactly this scheme, so the decoder is tested on files built in the test.

## Model configs that name the files

**1404** `.cfg` is 2070's model config (`models/cfg2070.py` parses it): `m_Models` (`MODEL`: `m_FileName` a `.gr2`),
`m_InstancingModels` (`m_InstancingModel`, tinted by `m_ColorR0`..: grass and trunks), `m_Clothes`, `m_Files` (nested
configs), `m_InstancingBBClusters` (a tree's leaf cards, numbered in hex), each with `m_Materials` by `m_Name` and an
`m_Transformer` chain. Texture names say `.png`; the game ships `<stem>_0.dds` (2,827 of the 2,971 diffuse names in
the configs; 89 name a real `.png`, 47 a `.dds`, 8 are missing).

**1701** `.ocf` (`<ObjectConfig>`): `GUID` and one `MeshConfig` per variation, an `.mcf`. An `.mcf` is a tree of
`m_MeshConfig` nodes, each with `m_MCChilds` holding `m_MCConfig` entries (a `m_MeshConfig` and its
`m_MeshTransform`s) and the `m_Material`s of that list by `m_Name` (diffuse `m_DiffuseTexture`, normal
`m_BumpTexture`, `.tga` files as named). A few files carry a bare `&` in a texture name.

| `m_MCClassType` | Node | | Transform |
|---|---|---|---|
| 0 | group | 101 | offset (`m_OffsetPosition`, world units) |
| 1 | mesh (`m_MeshFilename`) | 102 | rotation (`m_q.w/x/y/z`) |
| 2 | nested `.mcf` (`m_MeshConfigFilename`) | 103 | link to a dummy |
| 3 | effect (`.efc`) | 104 | sway |
| 4 | billboard (`.blb`) | 105 | level of detail (`m_LODMin`, `m_LODMax`: 61-100 the nearest) |
| 6 | dummy | 107 | scale per axis (`m_ScaleFactor`) |
| 7 | instanced vegetation (`m_GrannyObjectFilename`, `m_TextureFilename`) | 109 | shown on a condition (`IS_DAMAGED`) |
| 8 | walking unit | 110 | colour |
| 9 | cloth (`m_MeshFilename`, `m_DiffuseTexture`) | 111 | circling motion |
| 50, 51 | animation, material | | |

The island editor draws classes 1, 2, 7 and 9 at the nearest level of detail, leaves out what a condition switches
on, and scales the model by 1/32 (one view unit a tile). The viewer (`othergames.grannymodels.read_1701_model`) keeps
the rest too:

- a config's condition triggers (class 109 `m_ConditionTrigger`, all of them needed);
- the `m_Animation`s of a list (`m_Filename`; `m_Speed`, 1000 the clip's own pace; `m_StartOffset`; `m_Loop`),
  which play on the meshes of that list and below;
- cloth (class 9: `m_GravityFactor`, `m_WindFactor`, taken as factors on the simulation's defaults, a guess);
- effects (class 3: `m_EffectFilename`; `m_EffectStartParameter`, where empty plays always and a name such as
  `SHOOT_01` is a state the game starts it in; `m_EffectLoopDuration`, `m_EffectTimeOffset` in ms).

A nested `.mcf` that is missing, does not read or nests deeper than four files is named in the view's notes and
never drawn. `m_StartOffset` is 0 in every one of the 3,905 `m_Animation` entries of the shipped `.mcf` files (six
leave it out), so the view leaves it out. Whether a value delays the clip or starts it further in is not known, and
only a modder's file would show it. 1701's `.efc` is a text format of its own:
[`anno1701-effects.md`](anno1701-effects.md).

Note: the `m_StartOffset` entries were counted on 2026-11-13.

## 1701's info files

These are read by `src/annotoolbox/models/info1701.py`. There is one `.info` beside each `.mcf` (1,381 of each).
It holds tagged elements without a root.

| Element | What |
|---|---|
| `BoundingBox` `Min` / `Max` (`x y z`) | the model's box in world units (32 a tile) |
| `NoBuildings` | the tiles nothing else is built on: the footprint (433 files); its `GridResolution` is ignored (one file writes 32, its tiles match the islands only at 1) |
| `Blocking` | half tiles units do not walk (`GridResolution` 2; 412 files) |
| `Entrances`, `LandPositions`, `WaterPositions` | cells at the same resolution: where units enter, a ship's land and water side |
| `Dummies` | named boxes (`hitbox01`, `fire01`, `airport01`, `saboteur01`, `infoicon` ...): `Center.x/y/z`, `Extents.x/y/z` in 128ths of a world unit (4,096 a tile) |
| `AnimationList` | `Animation` entries: `GUID`, `ID`, `Timelabel`, `Time` |

A cell (`GridPos` `x`, `z`) counts from the grid point at the corner of the tile the object stands in and skips 0:
cell 1 spans the first step past the point, cell -1 the first before it. The object's origin is the `.ocf`'s
`XOffset` / `ZOffset` world units past that point (0 or 16: an odd-sized building stands half a tile in). So the
footprint round the origin is the `NoBuildings` tiles minus the offset. This was checked against the footprints
that the shipped islands' grids hold (`othergames.grid1701`, the first 80 islands): 154 of 163 objects agree
exactly, and the other nine are landscape pieces (stone circles, caves, a bridge). The island editor still reads its
1701 footprints off the islands (`editor/gameviewers.py`). `editor.footprints.read_footprint(info, ocf)` gives them
from the files.

Note: checked on 2026-11-13.

## Skeletons, animations and curves

These are read by `src/annotoolbox/formats/grannyanim.py` (stdlib only). The reader was checked against the
games' own Granny runtime (version 2.12.0.4, shipped beside the executables) by evaluating the same curves through
it. Every format below matches, over every animation of both games, with each curve sampled at 49 times across its
animation's duration:

- 1404: 1,633 files and 140,724 curves, worst difference 1.5e-3 on one ship fragment's position;
- 1701: 1,355 files and 106,959 curves, worst difference 5.4e-4.

The formats:

- **Bones** (`Skeletons[].Bones[]`): `Name`, `ParentIndex` (-1 a root, parents first), `Transform` (rest in the
  parent), `InverseWorldTransform` (16 floats for row vectors: the move in the last row). A local matrix for column
  vectors is move x turn x scale-shear, the stored 3 x 3 transposed. The rest locals composed times the inverse
  world give the identity to 1e-5 (one 1404 trebuchet skeleton has a rest pose that differs from its bind pose).
- **Skins**: a mesh's `BoneBindings[].BoneName` number its vertices' `BoneIndices`. A rigid mesh moves with its
  first binding. `Models[].MeshBindings` name the skeleton (`Models[].Skeleton`) a mesh belongs to.
- **Animations**: `Duration`, `TimeStep` (seconds), `TrackGroups[]` (one per model, named after it) with
  `TransformTracks[]` (`Name` = bone, `OrientationCurve`, `PositionCurve`, `ScaleShearCurve`). Scale-shear is not
  always the identity (6,564 constant non-unit curves in 1404), so a rigid-only rig is exact only while a bone's scale
  stays constant.
- **Curves**: a B-spline with one control per knot. Knot `i` ends the span whose last control is `i`, and knots and
  controls before the first repeat it (de Boor on that index). Formats seen (1404 / 1701): `DaIdentity`,
  `DaConstant32f`, `D3Constant32f`, `D4Constant32f`, `DaK32fC32f` (degree 0-2), `DaK16uC16u`, `DaK8uC8u`,
  `D4nK16uC15u`, `D4nK8uC7u` (packed unit quaternions: three components, the top bits of the second and third word
  name the one left out, the first word's top bit its sign, and each component has its own 4-bit selector into a
  16-entry scale/offset table), `D3K16uC16u`, `D3K8uC8u`, `D3I1K32fC32f`, `D3I1K16uC16u`, `D3I1K8uC8u`,
  `D9I1K8uC8u`, `D9I3K16uC16u`, `D9I3K8uC8u`. Quantized knots are the raw value divided by `OneOverKnotScale` (the
  `...Trunc` fields keep the float's top 16 bits).
- **Past the last knot**, the runtime does not hold the last control: it goes on evaluating the last span (the
  knots beyond the end are taken as the last). An animation's last knot can fall short of its duration (1701's
  `horatio_run_01.gr2`: 1.9167 s of 1.92 s), so the end of a loop goes on to its duration. Held, it differed by 0.04
  there.
- **Format bytes** of the plain headers: `DaK32fC32f` 1, `DaIdentity` 2, `DaConstant32f` 3, `D3Constant32f` 4,
  `D4Constant32f` 5.

**In the view** (`models/grannyrig.py`, `viewer/modelfiles.py`): a mesh's `BoneIndices` count its `BoneBindings`,
whose names are bones of its model's skeleton (1404's skinned meshes carry one `BoneIndices` and no weights, 1701's
four of each, bytes). A rigid mesh (no bone numbers) moves whole with its first binding's bone, its vertices already
in the model. The skeleton becomes RDM joints from the rigid part of each inverse world transform. An animation is
sampled every `TimeStep` (30 a second without one) into RDM keys: each bone's place in the model posed with scale
and shear, its rigid part, and the local of that against its parent's. This is exact while a bone's scale stays as
bound. An animation file without a skeleton of its own plays against the skeleton, of those read so far, that names
the most of its bones.

**Cloth pins**: 1404's and 1701's cloth meshes carry `DiffuseColor0` (four floats, r g b a). A vertex is pinned when
its blue byte has the top bit (1404's flags: blue 128 at the pole; 1701's: white). The view hands them on as an RDM
colour element (bytes B G R A, as 2070 stores them), so `render/clothsim.py` reads the same pin rule. Such a mesh is
not skinned.

**1701's cloth is subdivided** (the scheme is inferred). The evidence is twofold. First, 1701's cloth meshes are too
coarse to wave (the shipped flags: 6 to 14 vertices, 4 to 12 triangles; a flag of two triangles a row cannot bend
between its rows). Second, 17 of the 18 class 9 nodes of the shipped `.mcf` files say `m_LODLevelCount` 3 with an
`m_LodBias` (0.02 to 0.3), fields no other mesh node carries: levels of detail the game makes of a mesh that ships
one.

The view splits every triangle of a 1701 cloth into four, twice (levels 0 to 2: 16 times the triangles;
`grannyrig.subdivided`, `viewer.modelfiles.CLOTH_ROUNDS`). A new vertex sits at each edge's middle, shared by the
edge's two triangles. Its position, normal, UV and colour are the ends' mean. It is pinned only where both ends are,
so a pinned edge (the pole) stays pinned and a free edge stays free. Not known: whether the game splits into four or
along one axis, smooths the new vertices, or picks the level by distance with `m_LodBias`. `m_WeldDistance` (0 in
all 15 files that have it) is not used. 1404's cloth meshes are not split.

Note: the reader was written on 2026-11-12 and 2026-11-13; the curves were checked and the cloth scheme was inferred
on 2026-11-13.

## Writing

`src/annotoolbox/formats/grannywrite.py` writes a record tree with the type tree that describes it: 64-bit
pointers, eight sections of which the first holds everything (stored, not compressed), its fixups after it, and the
header's CRC. The type tag (`0x80000001`) is not the runtime's own, so the runtime converts the file into its own
types by member name (a member left out reads as zero; a transform is written as the identity). The member names and
kinds are the shipped files'. A mesh's vertices carry position, normal, tangent and binormal (from its triangles and
UVs, as 1404's own meshes: 208 of 238 sampled), the first UV set, and a skin's bytes and colours. Curves are written
as `DaK32fC32f` (knots and controls as floats) or `DaIdentity`. The reader and the writer share one table of member
sizes (`grannyread.field_size`). The games' Granny runtime (shipped with both History Editions) read such a file,
gave its mesh's vertex and triangle counts and evaluated its curve as written.

**Only 64-bit files are written.** Both History Editions run a 64-bit runtime, which reads 64-bit files as they are
and converts 32-bit ones ("Converting pointer size from 32 to 64", its log says). That step fails (a write through a
null pointer) on a 32-bit file whose sections are stored rather than Oodle1-compressed, once a record mixes member
sizes (a curve's header of bytes and a 16-bit dimension; a record of an `int32` and a `uint8`). Our 32-bit files
fail this way. So does one of 1404's own 32-bit animation files rewritten with its sections stored, while the same
file compressed converts (the runtime was loaded on its own, no game started). Files whose records are all 4-byte
members (meshes, skeletons, models) convert stored too. A 32-bit file would therefore need Oodle1 compression (the
decoder is `grannyoodle.py`; the test encoder handles small sections only), and it gains nothing: the 64-bit file is
what the runtime reads without converting.

**Placing a written file**: `rdm/grannygltf.config_files` writes the model config and info file that the games' own
files have beside a mesh. For 1701 that is an `.mcf` with the mesh node and one material per material name (shader
`mesh/default.fx`), and an `.info` with the bounding box. For 1404 it is a `.cfg` with the model and its materials
(vertex format `p3n3g3b3t2`), and an `.ifo` with the box in fixed point. Each material's diffuse texture is named
after the mesh and the material. glTF to `.gr2` and back: [`../rdm-gltf.md`](../rdm-gltf.md), "Granny models".

**State.** Done:

- the curve reader, checked over both games;
- skins, skeletons and animations in the view;
- cloth pins, and 1701's cloth subdivided;
- 1404 through 2070's config path;
- 1701's conditions, clips, cloth and effect states, and its `.efc` effects drawn
  ([`anno1701-effects.md`](anno1701-effects.md));
- 1701's `.info` footprints (`models/info1701.py`);
- the object builder opening 1701's `.ocf` / `.mcf` / `.info`;
- the writer (64-bit, tangent frames), glTF both ways, and the config and info file that place a written file;
- tests in `tests/test_granny_rig.py` and `tests/test_efc1701.py` (synthetic, and gated on `ANNO1404_DIR` /
  `ANNO1701_DIR`).

Open:

- drawing 1701's grid cells over the builder's 3D view (`objbuild/shapes.py`);
- the island editor taking its 1701 footprints from the `.info` (`editor/gameviewers.py`);
- the game-shader looks posing Granny skins.

Not done: `m_StartOffset` (0 in every shipped file, above) and 32-bit writing (above). Nothing of this was checked
in either game.

Note: the runtime checks of written files were made on 2026-11-13, and the state is of that date.
