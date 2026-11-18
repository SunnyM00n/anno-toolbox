# RDM and glTF: the mapping, the rule, the choice of format

This page describes how the RDM toolchain moves an `.rdm` into glTF 2.0 and back (`rdmgltf.py`, `gltfrdm.py` with
`gltfmesh.py` and `gltfskin.py`), the rule it keeps, how that is tested, and why glTF was chosen. The format itself
is described in [`formats/rdm.md`](formats/rdm.md).

## Install and run

The toolchain is part of the Toolbox. The installed program ([`installer.md`](installer.md)) has it as
`anno-toolbox.exe rdm ...` and `anno-toolbox.exe export`. A developer's checkout has it too, light (no window
library) or full (with the windows):

```bash
pip install -e .                  # light: the standard library only; `anno-toolbox rdm ...` and `export`
pip install -e ".[textures]"      # numpy: a PNG copy of a DDS texture in an export (BC1 to BC7, BC6H)
pip install -e ".[gui]"           # the full Toolbox: the windows (the viewer exports and imports models)
```

In the viewer:

- **File > Export...** writes a model as glTF (`.glb`).
- **File > Import a model (glTF to .rdm)...** shows the import's report: what was dropped, rebuilt or refused.
- The **Inspect** panel shows every element of the open `.rdm`, and **Compare with...** compares it with another
  file.

The island editor's and the asset editor's **Export** buttons open the same dialog for a selected object's model or
a model field. An imported model can be kept in the project. It then goes into the mod at the game path it replaces
(`model-edits/` in the project folder).

## Use

```bash
anno-toolbox rdm export model_lod0.rdm                          # model_lod0.glb beside it
anno-toolbox rdm export model_lod0.rdm --anim anim/work.rdm --anim anim/idle.rdm   # with its animations
anno-toolbox rdm export model_lod0.rdm --lods --texture model_diff.dds            # the LOD chain in one file, a texture
anno-toolbox rdm export models/ --anims anims/ -o glb/          # a whole folder, each mesh with the animations that move it
anno-toolbox rdm import model_lod0.glb -o out/model_lod0.rdm    # the mesh file and, beside it, its animation files
anno-toolbox rdm import new_model.glb --like model_lod0.rdm     # a new model in the original's layouts and material order
anno-toolbox rdm inspect model_lod0.rdm --values                # every element, and checksums of the values
anno-toolbox rdm diff model_lod0.rdm model_lod0.glb --values    # what an import of the glTF would change (nothing)
anno-toolbox rdm validate models/                               # held against the conventions of Anno 117's files
anno-toolbox rdm relayout old_model.rdm -o for117/              # rewritten into the layouts Anno 117 uses
anno-toolbox rdm layouts                                        # the vertex layouts the games ship
```

Every command takes files and folders (searched recursively) and never overwrites a file without `--force`. Notes
say what a command dropped, computed or could not keep. `anno-toolbox rdm <command> --help` lists the options.

**Blender:** use File > Import > glTF 2.0 and File > Export > glTF 2.0. Export with *Include > Custom Properties*
on. The extras this tool wrote then come back where Blender keeps them, and the import restores those stored values
exactly. Without them, the import still writes every field the game reads from the geometry, skins and keys, and
says what it rebuilt or left at zero (`--like original.rdm` gives back the joint boxes, layouts and material order).
Tangents need not be exported: a missing tangent frame is computed. Animations need no *Always Sample*. This is not
yet tried with Blender itself: the tests stand in for it by stripping the extras and adding what Blender adds.

## The rule: exactly what the source holds

Nothing added, nothing lost, in both directions.

- **Export** writes the source's meshes, submeshes, materials, joints and animations and nothing else. glTF needs
  two things RDM does not have: the **scene** (glTF shows only nodes in a scene) and, when several files go into one
  document (a LOD chain), one **file node** per file. Both are marked as the export's own (`extras.rdm.helper`) and
  removed again on import. There is no armature, root or dummy node, no extra joint, no placeholder material and no
  default value the source does not hold. Where glTF wants data the source does not have, the export writes the
  least glTF accepts and marks it (`extras.rdm.implied`): the weights of a mesh with one bone per vertex
  (255, 0, 0, 0) and the bone indices of a rigid part of a skeleton (all on its first joint). The import drops them
  again.
- **Import** takes what the RDM file can hold. It drops, with a note, what a modelling tool added on its own:
  cameras, lights, empties, the armature object, extra UV maps and colour layers the layout has no place for, and a
  bone the tool added that no vertex weighs. It **refuses**, with a message, what it cannot drop without changing
  the model: a primitive without a material (a submesh names one), a joint that scales, morph targets, a bone that
  carries weights but is not the source's, and repeated joint names. It never writes a placeholder: an empty name
  stays empty, and an unknown joint box stays zero and is reported.
- **Order, names and counts** are kept: mesh order, submesh order and material indices, material order, joint order
  and indices (a vertex's bone indices are remapped only when a modelling tool reordered the skin), track order,
  vertex count and order where the document still has them.

How it is proven, on synthetic files (`tests/rdmsamples.py`):

- `rdm -> glTF -> rdm` is **byte-identical** for static, skinned and animation files in all four forms and both axes
  conventions, for a LOD chain and for a big-endian source (`test_rdmgltf.py`).
- The export's node, material, skin and primitive counts equal the source's, and `inspect` finds no item that is not
  the source's or a marked helper.
- With the extras stripped (what most tools do), every field the game reads comes back except the stored box, the
  joint boxes and the 4th components, and those are reported
  (`test_without_the_extras_every_field_the_game_reads_comes_back`).
- Every addition of a modelling tool listed above is dropped or refused (`test_gltfrdm.py`).

On the games' own files (every `.rdm` of the 2070 Complete Edition, 3,836, and every 10th of Anno 117's, 1,945),
every file came back identical in every field through `export` and `import`, and through the writer alone (byte
order and the size of 2070's early mesh record aside, which are not content). The run found two things the synthetic
files had not: a `-0.0` coordinate lost its sign in the un-mirroring, and 117 ships animations with two tracks on
one joint. Both are fixed and in the tests.

`rdm diff a.rdm b.glb --values` shows the same for any file: it compares the source with what the import of the
glTF would write, element by element and value by value.

Note: the run over the games' own files was made on 2026-11-07.

## Mapping

| RDM | glTF | Exact? |
|---|---|---|
| mesh | node + mesh, named as the RDM mesh; the mesh's layout, bones-per-vertex word, index type, skeleton index and stored box in `extras.rdm` | yes |
| submesh | primitive (all primitives of a mesh share its vertex accessors), its material | yes |
| material (name, texture path) | material (name; the texture path in `extras.rdm.texture`) | yes |
| `P4h` / `P3f` position | `POSITION` (float); the 4th value in the extras or `_RDM_<n>` | yes |
| `N4b` / `N3f` normal | `NORMAL`: the decoded vector `b / 127.5 - 1`, which packs back to the same byte | yes (with `--strict`: unit length, then within one step) |
| `G4b` tangent, `B4b` bitangent | `TANGENT` (xyz = dP/du, w the handedness) and `_BITANGENT` (dP/dv); RDM stores both negated | yes; without `_BITANGENT` the bitangent is cross(N, T) * w |
| `T2h` / `T2f` UV sets | `TEXCOORD_0`, `TEXCOORD_1` | yes (half floats are exact in float) |
| `C4b` / `C4c` colours | `COLOR_0`, `COLOR_1`: normalized unsigned bytes | yes, byte for byte |
| `I4b` bone indices, `W4b` weights | `JOINTS_0` (unsigned bytes), `WEIGHTS_0` (normalized unsigned bytes) | yes (`--strict`: weights scaled to sum to 255, which the game's division makes the same) |
| any other element | `_RDM_<n>` with the element's own values | yes |
| skeleton | skin: joint nodes in joint order (their rest pose from the inverse bind poses), `inverseBindMatrices`; the skeleton word and optional data in `extras.rdm` | yes; the stored position and rotation of each joint ride in its extras and win where they still give the bind matrix |
| joint box | `extras.rdm.box_min` / `box_max` of the joint node | yes; without the extras: zero, and said |
| animation entry | animation: per track one rotation and one translation sampler (LINEAR) on the joint node; name, frame rate, duration, the track order and the tracks without keys in `extras.rdm` | yes |
| a track on a joint the mesh lacks | a channel without a target node, its joint named in the channel's extras | yes |
| LOD files | one document, one marked file node per file (`--lods`) | yes |
| per-frame vertex stream (extra stream) | refused: glTF has no place for it (no shipped file has one) | - |

**Skeletons.** A joint stores its inverse bind pose, and a key stores the joint's local transform. RDM's quaternions
are the conjugates of glTF's (`rigmath.py`).

**Axes.** Anno's model space is left-handed (y up), and glTF's is right-handed. By default the export mirrors on x
(positions, directions, joints and keys, the winding turned), which a sign change undoes exactly. `--axes anno`
keeps the stored values (what earlier tools wrote).

**Import of a model made elsewhere:**

- The layout comes from `--layout`, else the `--like` file, else the attributes (`P4h_N4b_G4b_B4b_T2h` with `C4b`
  per colour layer, `_I4b_W4b` when skinned).
- A missing normal or tangent frame is computed from the triangles and UVs (`tangents.py`, angle-weighted, and
  reported).
- Missing UVs, colours or bones that the layout needs are refused.
- STEP keys are held, and CUBICSPLINE keys are sampled at the frame rate.
- The frame rate is the animation's own, `--fps`, or the first usual rate the key times fall on.

## Games

| Game | Read | Written and loaded in the game |
|---|---|---|
| Anno 2070 | every `.rdm` the Complete Edition ships (3,836: meshes, skinned meshes, cloth, animations, both byte orders) | - |
| Anno 117: Pax Romana | 39,479 of its 39,480 mesh files (the other one is cut short) | the writer's static models seen in game; skinned models and animations follow its conventions, not all confirmed; nothing imported from glTF has been placed in a game yet |
| Anno 1800 | 21,516 of 21,516 mesh files | not tried |
| Anno 2205 | 4,507 of 4,507 mesh files | not tried |

Through `export` and `import` and back, every 2070 file and every 10th Anno 117 file came back identical in every
field. A test that passes proves the file format, not that a game draws the file. Only the game can confirm that.

Note: checked on 2026-11-07.

### Granny models (Anno 1404, 1701)

A Granny `.gr2` goes to glTF through the same export once it is RDM records (`rdm/grannygltf.py`):

- its meshes become float vertices with their material names;
- a skin's bones go through the mesh's bindings (a rigid mesh: one bone);
- the skeletons become joints (the rigid part of each inverse bind);
- every animation is sampled at its time step into keys.

The export (the suite's, or the viewer's dialog with **Animations from the folder**) adds the animations of the
folder's `.gr2` files whose tracks name only joints the mesh has. 1701's lengths are divided by 32 (one unit a tile,
as 1404's). Skin weights become bytes summing to 255, as for any import. The way back is a command:

```
anno-toolbox rdm granny model.glb [-o model.gr2] [--game anno1404|anno1701] [--axes gltf|anno]
                                     [--place GAME_FOLDER] [--force]
```

It imports the glTF as for an `.rdm` and writes one `.gr2` (`formats/grannywrite.py`, 64-bit, one stored section):
a model per skeleton (a rigid mesh gets a bone of its own), the tangent frame from the triangles and UVs, linear
curves for the keys, and the usual quaternion (RDM keeps its conjugate). `--game anno1701` multiplies the lengths by
32 and writes 39.37 units a metre.

`--place data/graphics/objects/custom` also writes the model config and info file that name the `.gr2` as lying in
that folder of the game's files (1701: `.mcf` and `.info`; 1404: `.cfg` and `.ifo`). They carry one material per
material name, whose diffuse texture is `<mesh>_<material>_diff.tga` (1701) or `.png` (1404, which loads its
`_0.dds`). Nothing is written when a file exists, unless `--force` is given.

The Granny runtime that both History Editions ship reads such a `.gr2`, converts it and evaluates its curves as
written. No written file has been placed in either game yet ([`formats/granny.md`](formats/granny.md), "Writing").

Note: the runtime check was made on 2026-11-13.

### Granny to RDM (Anno 1404, 1701 into 2070, 2205, 1800, 117)

```
anno-toolbox rdm from-granny <file.gr2 | folder> -o out/ --to anno2070|anno2205|anno1800|anno117
                                [--anims <folder>] [--axes same|turn] [--source anno1404|anno1701] [--scale F] [--force]
```

The `.gr2` becomes the RDM records the glTF export already makes (`rdm/grannygltf.py`) and goes straight to the RDM
writer in the target game's layouts (`rdm/grannyrdm.py`), with no glTF between, so nothing is quantised twice. The
Toolbox's Export (and the viewer's File > Export) has it as **.gr2 to .rdm** with a game choice. The container is the
same in all four games; what differs is the layouts each game ships (`rdm layouts`; read off the installs, 2026-11-17):

| Game | Static | Vertex colours | Cloth | Skin |
|---|---|---|---|---|
| 2070 | `P4h_N4b_G4b_B4b_T2h` | `+ C4c` | `P3f_T2f_C4c` | `+ I4b_W4b`, word 3 (4 where a vertex weighs four) |
| 117 | the same | `+ C4b` | `P3f_N3f_G3f_B3f_T2f_C4c` | as 2070 |
| 1800 | the same | none shipped: left out, said | as 117 | `+ I4b` (word 1), or with two bones a vertex the 16-bone layout `+ I4b x4 + W4b x4` (word 16) |
| 2205 | the same | none shipped | none shipped: written as a plain mesh, said | as 1800 |

What is carried: meshes with their material groups (the file's material names, in the order of first use), the
normals, the first UV set, the tangent frame (Granny's own is not read: it is made from the triangles and UVs around
Granny's normals, stored negated as the games store it), vertex colours (bytes B, G, R, A as 2070 stores them; not
carried when every vertex is white), skin weights and bone numbers through the mesh's bindings (a rigid mesh on one
bone), the skeletons as joints, and every animation as an animation file of its own, sampled at its time step: the
file's own as `<name>_anim.rdm`, and each `.gr2` of `--anims` whose tracks mostly (75 %) name the model's bones as
`<its name>.rdm`. A clip is made for the model's biggest skeleton and the levels of detail lack some bones; the missing
tracks are left out. A mesh whose colours pin some vertices and not all is a cloth ([`formats/granny.md`](formats/granny.md), "Cloth pins") and is
written as `<name>_cloth.rdm`. A file with animations, or with bone numbers on its vertices, is written as a skin;
otherwise as a static mesh (a rigid mesh's bone is then not needed: its vertices are in the model already).

The rules applied:

- **Units.** 1701 counts 32 units a tile and 1404 one, both RDM games count one a tile (2070 and 117: engine-placement;
  1800 and 2205 taken as the same, **[I]** not checked). Every length (vertices, joints' moves, keys' moves) is divided
  by the source's tile; the source is read off the file's art tool info (39.37 units a metre: 1701). `--scale`
  overrides the factor.
- **Axes.** All of them are y up and back = -z, and a Granny mesh keeps its winding: over 148 converted 1404 and 139
  1701 meshes the median share of triangles that run counter-clockwise against their normals is 1.00, as in 117's
  own files. `--axes turn` turns the model half a turn about y (a rotation, so skins and keys stay exact: a vertex in
  bone space is where it was).
- **Boxes and names.** The stored box is the box of the half-float positions as written, so it holds every vertex
  drawn. Mesh, joint and material names are the source's.

Checked, 2026-11-17: on the synthetic writer (`tests/test_granny_rdm.py`: a mesh, a skin with weights and an
animation, the units, the half turn, the 16-bone layout, colours and cloth, the command and the export, read back with
our reader: vertices, weights and joints equal within the half-float and byte quantisation), and on the installs: 213
files of 1404 and 203 of 1701 (every 25th and 18th) converted for all four games and read back. For 117, every output
held to 117's conventions (`rdm validate`, the layouts read off the install): no deviation but two source files whose
meshes share a name, and a median tangent frame agreement of 1.00. Not converted (error with a reason, 2 of 203 in
1701): a file whose skeletons share a joint name (a rider and its horse), which 117 would merge into one skin that
names its joints one way. No converted file was placed in a game. Not done: the model config that names the `.rdm`
(a 117 config needs its material blocks and the textures converted; the `.rdm` holds names only), and cloth's
simulation settings.

## Compared with rdm4

[rdm4](https://github.com/lukts30/rdm4) is the community's earlier tool (Rust, MIT). The table lists every feature
its README and issue tracker describe, and where the toolchain stands:

| rdm4 feature | Here | How it is tested |
|---|---|---|
| `.rdm` to glTF: `glb`, `gltf`, `gltfmin` (`-e`) | `export --form glb\|gltf\|gltfmin\|embedded` | byte-identical round trip in all four forms |
| glTF to `.rdm` with a vertex format (`-g`, six formats) | `import --layout` with any layout name (`layouts` lists 17 the games ship), or the one the file or `--like` names, or the one its attributes ask for | round trips per layout, `--layout` test |
| glTF mesh to convert (`--gltf-mesh-index`, default 0) | `--mesh INDEX\|NAME`; by default **every** mesh (through rdm4, 49 of 2070's multi-mesh files came back with their first mesh only) | multi-mesh files round trip; `--mesh` test |
| `--no_transform` | `--no-transform`; by default a static mesh's node transforms are baked in, a skinned mesh's ignored as glTF says | a moved node from elsewhere |
| `--negative-x-and-v0v2v1` (mirror on x) | `--axes gltf` (the default, both ways, exactly reversible) or `--axes anno` | mirror test: positions, winding, tangent handedness |
| `--overide-mesh-idx` (material order of a cfg) | `--material-order a,b,...`, `--like original.rdm`; an export keeps the order itself | `--like` test |
| `-u` joint names `UnstableIndex` / `UniqueName` | `--joint-names name\|index`; repeated names are refused with a message (rdm4 issue 50) | test |
| skin and skeleton (`-s`) | always; **every** skeleton of a file as its own skin (rdm4 reads one: issue 190) | skinned round trip with two skeletons and a rigid part |
| animation, `-m` external animation file (`-a`) | `--anim FILE` (repeatable), `--anims DIR` picks the files that move the mesh; files of several entries (rdm4 fails: issue 169) | animation round trip with two entries, an empty and an unbound track |
| glTF animation to `.rdm` | `import` writes each animation file back, named and grouped as before | byte-identical |
| diffuse texture (`-t`) | `--texture [MATERIAL=]FILE`: PNG, JPEG or DDS (with a PNG copy viewers can show), per material | - |
| `-o`, `-n`, `--force` | `-o` file or folder, names from the input, `--force`; nothing is overwritten without it (rdm4 issue 53) | test |
| `-v` verbosity | every note is printed: what was dropped, computed or could not be kept | - |
| games 2070, 2205, 1800 | the same and Anno 117; both byte orders (rdm4 issue 144) | read over every shipped file (above) |
| Explorer context menu (`.reg`) | **not provided**: a Python command; a context-menu entry is a later convenience | - |
| a prebuilt `.exe` | the Toolbox's installer ([`installer.md`](installer.md)) carries the commands as `anno-toolbox.exe rdm ...` | - |

The next table lists what rdm4's files were measured to carry that the source did not (tainting), and what the
toolchain does instead:

| rdm4 | Here |
|---|---|
| adds an `armature` joint, written after its children, and a track for it (Anno 117 then drops the skeleton and the mesh draws nothing) | no joint, node or track the source lacks; Blender's armature object is dropped on import |
| writes the same box (about 2 units each way) into every mesh, bush or cliff, and zero joint boxes | the stored box and joint boxes as they are; a new mesh gets its own box, a joint without one is zero and said |
| re-quantises normals (up to 3 steps of 255) | normals packed back to the byte they came from |
| drops the fourth bone weight | four weights kept |
| writes 15 frames per second into every animation | the animation's own rate |
| writes one bone per vertex into meshes with weights (Anno 117's are three), and three into a static part that had none | the source's word; a static part stays static |
| needs normals and tangents in the glTF (issue 30), sampled animations, no scale | computes a missing tangent frame (said); resamples STEP and CUBICSPLINE keys; a scale of one is ignored, any other is refused |
| reads no `C4b` colours (issue 97) | every element the format has, colours byte for byte |
| no batch mode (issue 100) | every command over folders |

Added here: `inspect` and `diff` (every element and value of a file, so a modder sees that nothing was added),
`validate` (the conventions of Anno 117's own files), `relayout` (a model rewritten into Anno 117's layouts, every
filled value listed), LOD chains in one document, big-endian files and a library API. Not matched: a per-frame
vertex stream (none ships; glTF has no place for it, so it is refused, not guessed), and morph targets and scaling
keys, which no RDM key can hold.

## The choice of format

For each kind of data the toolchain moves, the exchange formats that modellers use were held against four criteria:
a lossless round trip of what the game uses, skinning and animation fidelity, an open specification with a writer
and reader that need no proprietary SDK, and how well Blender (then 3ds Max, Maya) imports and exports it.

| Data | Choice | Why | Not chosen |
|---|---|---|---|
| static meshes, submeshes | glTF 2.0 (`.glb` default; `.gltf`) | open Khronos spec, a stdlib reader and writer; Blender's own importer and exporter; primitives are submeshes with materials, any number of UV sets and colour layers, named custom attributes for the rest; buffers hold the exact float and byte values | FBX: proprietary, the SDK's licence keeps it out of open tools and the binary form is undocumented; Collada: dropped from current Blender; OBJ: one UV set, no colours, tangents, skins or animations; USD: open and good, but reading what Blender writes (`.usdc`) needs the USD library |
| skinned meshes, skeletons | glTF 2.0 | a skin's inverse bind matrices are RDM's inverse bind poses; four joints and weights a set as RDM's `I4b`/`W4b`, in bytes | FBX: Blender's exporter adds leaf bones and turns bone axes by default, a model that comes back is not the one that went |
| animations | glTF 2.0 | a key is a time, a rotation and a translation, as RDM's; LINEAR samplers keep them as they are | FBX resamples curves; USD would do, at the cost above |
| materials and texture maps | glTF materials; textures PNG beside the DDS | an `.rdm` holds a material's name and a path only; DDS (BCn) is the game's form, PNG the lossless 8-bit form every tool and glTF read; TGA adds nothing, EXR only matters for float data, which Anno's textures are not | - |
| LODs | one glTF with a node per file | each LOD is a file of its own in the game; `MSFT_lod` is not read by Blender | - |
| attributes glTF has no slot for | glTF custom attributes (`_BITANGENT`, `_RDM_<n>`) and extras | the bitangent, a position's 4th value and a packed direction's 4th byte survive; a tool that drops them loses only those, and the import says what it rebuilt | - |

glTF is the best fit for every kind. USD would be the second choice for an export-only path later.
