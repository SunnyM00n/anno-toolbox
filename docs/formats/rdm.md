# RDM: the mesh and animation container of Anno 2070 and Anno 117

This page is hand-written. It was read from 2070's own reader and writer and checked against every `.rdm` the
2070 Complete Edition ships (3,836 files) and against Anno 117's own files. The reader is `rdmread.py` and the
writer is `rdmwrite.py`. Nothing here was seen in a game unless it says so.

Note: written on 2026-10-25.

## Container

A file is a header followed by **blocks**. A block is `u32 count, u32 item size` followed by `count x item size`
bytes. Every reference in the file is the **absolute file offset of a block's data** (the byte after its two words).
The count and item size are always read from the 8 bytes in front of the offset. There is no table of contents: the
blocks form a tree that starts at header1.

Reading rules (the game's, 2070 and 117 alike):

- A reference `<= 8`, or at or past the end of the file, means "absent" (count 0). It is never an error.
- Each item is copied into a zeroed struct of the engine's own size: `min(item size, struct size)` bytes. A shorter
  item reads as zeros at the end, and a longer one is cut. The reader then steps by the file's item size. So records
  may grow at the end without breaking old readers, and the early 68-byte mesh record reads with a zero box.
- A string is a block of item size 1, `count` bytes, no terminator (shipped files sometimes include a NUL in the count).
- Byte order: byte 3 of the file is 1 for little-endian. Anything else is big-endian, and every record is then
  byte-swapped field by field by a layout description of that record (types below). 97 of 2070's files are
  big-endian (most stones, the mountain boulders, the pearl reef and the kids' animations). Both games read them, and
  so does `rdmread`.
- The writer appends blocks depth-first in this order: header1, export info and its two strings, the mesh records and
  then each mesh's own blocks (name, vertex description and elements, index format, submeshes, vertices, indices),
  the material references and each material with its strings, the skeletons with their joints and joint names, the
  animations with their tracks, keys and names, and the extra streams. Shipped files follow this order, so every
  reference points forward, but no reader depends on it.

### Field types

The layouts and the vertex elements share one type code:

| Code | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Field | u8 | s8 | u16 | s16 | u32 | s32 | half | float | 64-bit | file offset | nested record |
| Bytes | 1 | 1 | 2 | 2 | 4 | 4 | 2 | 4 | 8 | 4 | (its record) |

A big-endian file swaps 2-, 4- and 8-byte fields; bytes and strings stay as they are.

## Header (20 bytes)

| Offset | Value |
|---|---|
| 0 | `R` `D` `M` (checked) |
| 3 | byte order: 1 little-endian, 0 big-endian |
| 4 | u32 0x14 (checked) |
| 8 | u32 0 (checked) |
| 0xc | u32 4 (checked) |
| 0x10 | offset of header1 (0x1c in every shipped file) |

## Records

All offsets inside a record are relative to the item; "-> X" is a file offset of a block of X.

| Record | Size | Fields |
|---|---|---|
| header1 | 0x30 | +0 -> export info; +4 -> meshes; +8 -> material refs; +0xc -> skeletons; +0x10 -> animations; +0x14 -> extra streams; 6 x u32 0 |
| export info | 0x48 | +0 -> string (2070: the source `.max` path); +4 -> string (the export preset, `Tangent.rmp`, `PNT.rmp` ...); zeros |
| mesh | 0x5c | +0 -> string ref (name); +4 -> vertex description; +8 -> index format; +0xc -> vertices; +0x10 -> indices; +0x14 -> submeshes; +0x18 u32 skeleton index (0xffffffff static); +0x1c float[3] box min; +0x28 float[3] box max; 10 x u32 0 |
| early mesh | 0x44 | the same up to +0x18, then 40 zero bytes: no box (148 meshes in 2070; they read with a zero box) |
| string ref | 0x1c | +0 -> string; zeros |
| vertex description | 0x18 | +0 -> vertex elements; +4 u32 bones per vertex (2070: 0 static, 1 with `I`, 2/3/4 with `I`+`W`; 117 vanilla 0/1/3); zeros |
| vertex element | 0x10 | u32 usage, u32 type, u32 subtype, u32 count (below) |
| index format | 0x14 | +0 u32 index type: 3 (16-bit) in every 2070 file; 117 also uses 5 (32-bit); the index block's item size is that type's size; zeros |
| submesh | 0x1c | u32 first index, u32 index count, u32 material (into the file's material list, and in 117 into the model's material list); 16 bytes zero |
| material ref | 0x1c | +0 -> material; zeros |
| material | 0x30 | +0 -> string (2070: the 3ds Max material name, often empty); +4 -> string (its texture path); zeros |
| skeleton | 0x20 | +0 -> joints; +4 u32 (2070: 0, -1 or a small number, the skeleton's place in multi-skeleton files; 117 reads a block offset of per-joint name hashes here, absent when <= 8); +8 -> optional byte block (empty in every 2070 file); zeros |
| joint | 0x54 | +0 -> string (name); +4 float[3] position; +0x10 float[4] rotation (quaternion x, y, z, w); +0x20 s32 parent (-1 root); +0x24 float[3] box min; +0x30 float[3] box max (the joint's extent); 24 bytes zero |
| animation | 0x30 (0x2c swapped) | +0 -> string (the model it moves: 2070 writes the mesh name, `..._lod0`); +4 -> tracks; +8 u32 duration in ms; +0xc u32 frames per second (2070: 15, 25, 30, 50, 60); zeros |
| track | 0x18 | +0 -> string (joint name); +4 -> keys; zeros |
| key | 0x20 | float[4] rotation (x, y, z, w); float[3] position; float time in seconds (the last key's time is the duration) |
| extra stream | 0x40 | +0 -> vertex description; +4 u32; +8 -> data (count = the +4 word x the +0x10 word, item = the description's stride); +0xc, +0x10, +0x14 u32; zeros. A second, per-frame vertex stream; no shipped 2070 file has one |

A mesh file has meshes and materials (and skeletons when skinned). An animation file has an export info and the
animations only. A 2070 animation file may hold several animations, one per model it moves (the saw mill's work
animations move the mill, the log and the plank).

## Vertices

The vertex description lists the elements in the order they lie in a vertex. The stride is the sum of
`type size x count`, and the vertex block's item size equals it in every shipped file (the engine copies
`count x item size` and draws with the stride, so a mismatch would shift every vertex). The vertex count is the
vertex block's count, and the index count is the index block's.

| Usage | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| Letter | P position | N normal | G tangent | B bitangent | T texcoord | C colour | W weights | I bone indices |

The format name the configs use is letter, count and kind per element, joined with `_`. Type 6 is `h` (half), 7 is
`f` (float) and 2 is `s` (16-bit integer). Type 5 is a **packed four-byte vector** written with count 1 and always
named `4b`, or `4c` when the subtype is 3 to 5. The subtype tells the packed kinds apart:

- 6: a signed direction stored as unsigned bytes (`x * 2 - 1`; normals, tangents, bitangents);
- 4: a colour;
- 0: bone indices (`index * 255` read back: up to 256 joints);
- 2: weights (divided by their sum).

A big-endian file stores a packed vector as one reversed 32-bit word. 2070 and 117 store the tangent frame as
T = -dP/du, B = -dP/dv.

2070's mesh layouts (meshes over all shipped files):

| Layout | Meshes | Bones per vertex |
|---|---|---|
| `P4h_N4b_G4b_B4b_T2h` | 1,988 | 0 |
| `P4h_N4b_G4b_B4b_T2h_I4b` | 398 | 1 |
| `P4h_N4b_T2h_I4b` | 354 | 1 |
| `P4h_N4b_T2h` | 120 | 0 |
| `P4h_N4b_G4b_B4b_T2h_C4c` | 69 | 0 |
| `P4h` (shadow and cut-out helpers) | 51 | 0 |
| `P4h_N4b_T2h_I4b_W4b` | 46 | 3 or 4 |
| `P4h_N4b_T2h_C4c` | 20 | 0 |
| `P3f_T2f_C4c` (cloth) | 18 | 0 |
| `P4h_N4b_G4b_B4b_T2h_I4b_W4b` | 10 | 2 |
| `P4h_N4b_T2h_C4c_C4c` (waterfall) | 5 | 0 |
| `P3f_T2f_T2f_C4c` (a flag) | 1 | 0 |

117's vanilla layouts: `P4h_N4b_G4b_B4b_T2h` (24 bytes), `+I4b` (28), `+I4b_W4b` (32),
`P3f_N4b_G4b_B4b_T2f_T2f_C4b` (44), cloth `P3f_N3f_G3f_B3f_T2f_C4c` (60). A 117 material declares the layout it
needs; a mesh that lacks a declared element draws nothing.

Of the 2,780 mesh files, 55 hold more than one mesh (up to 40: the cogeneration plant, the airport's parts), 974
meshes are skinned, and no mesh has more than 65,535 vertices.

## Skeletons and animations

- A joint stores the **inverse bind pose**; an animation key is the joint's **local transform**. A root joint at
  `(0.0042, 0, 0.0128)` with identity rotation has keys at `(-0.0042, 0, -0.0128)`.
- Tracks find their joint **by name**, not by index.
- Every shipped 2070 skeleton is **parent-first** (no joint's parent comes after it). 117 requires this: it silently
  drops a skeleton with one joint out of that order.
- A mesh names its skeleton by index. 2070 files hold up to 32 skeletons (one per rigid part). 117 registers the
  skeleton of the file's first skin only.

## 2070 and 117

The two games read the same container with the same records, field types and element vocabulary. 117 reads 2070's
files as they are (both byte orders, the short mesh record with a zero box). What differs is what the 117 materials
and skeleton code ask of the content:

- the layouts above;
- three weights summing to 255 with `W4b`;
- the bones-per-vertex word;
- skin 0 only;
- a real bounding box: 117 recomputes only an all-zero box;
- the skeleton record's +4 word, which 117 reads as an offset.

## 117 conventions

This section says what 117's own files do, measured over the 16,441 vanilla `.rdm` files
against the 2070 files, and what `relayout` does. Layouts and headers were measured on all files. Geometry,
skeletons and animations were measured on every 6th file (4,555 files). One shipped file is truncated and
unreadable. "Same" means both corpora measured alike, so the copy needs no transformation.

| Convention | 117 vanilla | 2070 | relayout | Checked on |
|---|---|---|---|---|
| Header | `RDM\x01`, `0x14, 0, 4`, header1 at 0x1c, little-endian in all 16,441 | same, 97 files big-endian | little-endian, same words; header1 is 0x30 bytes (117's mesh files have 0x34, a zero word more: the reader zero-fills) | `celtic/buildings/institution/institution_01/rdm/institution_01_main_lod0.rdm` |
| Axes | y up, ground at y 0: median lowest y of building meshes -0.03, 20 % of normals face +y, 34 % x, 32 % z | -0.02, 23 %, 36 %, 36 % | none | `/buildings/*_lod0.rdm`, 181 and 207 meshes |
| Winding | counter-clockwise against the vertex normal: 2,991 of 3,252 meshes above 80 %, 20 below 20 %, rest mixed (double-sided art) | 1,542 of 1,552 | none | all meshes with normals |
| Tangent frame | T = -dP/du in 3,096 of 3,240, B = -dP/dv in 3,098, none opposite | 1,216 / 1,218 of 1,230 | copied; the check fails a file whose agreement moved by 5 % from its source | same |
| UV | v not flipped (the tangent test ties the sign to v); v outside [0,1] in 546 of 3,255 (tiling) | 70 of 1,552 | none | same |
| Normals | packed bytes, x * 2 - 1, unit length in 3,248 of 3,270 meshes | 1,552 of 1,591 | copied exactly | same |
| Element types | `P4h` half4, `N4b`/`G4b`/`B4b` subtype 6, `T2h`, `C4b` subtype 2 (second colour 6), `C4c` subtype 4, `I4b` subtype 0, `W4b` subtype 2 | same vocabulary | the layouts it writes are all vanilla's, element for element | `validate --layouts-from` |
| Vertex colour | C4b subtype 2 on 600 meshes, two on 290 (reed, lavender), cloth C4c (simulation weights) | `C4c` subtype 4 on 94 meshes | bytes copied into `C4b`; whether 2070's bytes mean what 117's `C4b` means (order, gamma) is not known | open, game |
| Index format | type 3 below 65,536 vertices, type 5 in 52 meshes | type 3 | type 3, type 5 beyond 65,535 vertices | all |
| Submeshes | contiguous from 0 over all indices in every mesh; materials in range; not sorted by material in 304 | all sorted | unchanged | all |
| Box | stored box = the vertices' in 3,071 of 3,270 meshes (the rest within 0.035), zero in 1 | zero in 155 (early record) | computed from the half-float positions, per mesh | all |
| LOD names | `<name>_lod0` to `_lod4`; the distances are in the cfg, not the file | same | each LOD is a file of its own | names |
| Skeleton | parent-first in 260 of 260; 1 to 189 joints; several roots in 24; +4 word 0 in 258 (offset in 2) | parent-first in 985 of 985; up to 48 joints a file; several roots in 119 | merged, word 0 | skeleton survey |
| Joint boxes | filled in 260 of 260 | filled in 567 of 575 | copied; the eight all-zero skeletons stay zero (a rigid part: unknown whether 117 minds) | open, game |
| Bind pose and keys | local key = parent's stored joint x inverse of the joint's, in both | same | unchanged | a fit of the stored joints against the first keys, 45,353 and 15,105 joints |
| Bones per vertex | 1 (`I4b`) or 3 (`I4b_W4b`); weights sum to 255 in 164 of 562 skinned meshes only (the shader divides by the sum) | 1 to 4 | 3, or 4 where needed (`NUM_BONES_PER_VERTEX` is a loop over up to 16 weights in `constants_skinning.fxh`/`functions_skinning.fxh`) | `shaders.rda` |
| Animations | records 48 / 24 / 32 bytes; rates 15 (2,158 of 2,183), 24, 10, 5, 6, 25, 45, 100; first key at time 0 and last key = duration in all 2,183; unit rotations; name field empty or a short name | rates 15, 25, 30, 50, 60; same records, same time rules in 1,351 of 1,351 | rate and duration kept; name field kept (`..._lod0`) | `/anim*/` |
| Units | metres; building footprints on the 1 m grid | same | none | in game |

Open, only the game answers: whether 117 plays 30, 50 and 60 fps records (vanilla ships none), whether it
reads four weights, and what a `C4b` colour means for the plant shader.
