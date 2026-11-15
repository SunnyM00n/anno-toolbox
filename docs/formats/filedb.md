# FileDB, the tree format of the new engine's level files

FileDB is the binary tree format of the island, map and terrain files of Anno 1800 and Anno 117
(`gamedata.data` and `rd3d.data` inside `.a7m` / `.a7t`, `.a7minfo`, `.a7tinfo`, `.tmc`). It holds tags
(nodes with children) and attributes (named byte strings). The meaning of an attribute's bytes is up to
the reader of that file. Three layouts exist, and a reader tells them apart by the last four bytes of
the file. This page was written from the shipped 1800 files and the 117 files the island editor writes.

Note: written on 2026-10-17.

## Common shape

- A node stream, then two name tables (tags, attributes), then a tail.
- Ids: 0 closes the current tag, 1..0x7fff open a tag, and 0x8000 and above are attributes. Ids map to
  names through the tables. Some ids are used without a table entry (tag 1 is the anonymous list
  element, attribute 0x8000 a list's count in a few places).
- A missing field means "keep the default" to the game. The field order does not matter.

## Which files use which layout

The files were counted over the shipped files with `filedb.py`. Every document was read and written back
byte for byte. The layout goes with the archive an island was built for, not with the file type. In the
islands checked, `gamedata.data`, `rd3d.data` and `.a7minfo` of one island share one layout. So a reader
must not assume v1 for 1800.

| Game, archives | Layout | Files |
|---|---|---|
| 1800 islands from `data6` to `data16` (release to 2020: Old and New World, Cape Trelawney, the Arctic, Enbesa) | v1 | 180 islands |
| 1800 islands from `data21`, `data22`, `data24` (2021-22: the dlc10 scenario, end-of-year and some New World pool islands) | v2 | 37 islands |
| 1800 islands from `data24` to `data29` (2022 on: dlc11, dlc12, scenario and later pool islands) | v3 | 84 islands |
| 1800 map templates (`.a7t` + `.a7tinfo`) | v1, v2, v3 | 75, 1, 17 |
| 2205, `.a6m` maps (`gamedata.data`, `rd3d.data`, `mapinfo.info` inside) | v1 | all 24 maps (12 have no `rd3d.data`) |
| 117 | v3 | every level file the editor reads (`filedb.py` is pinned against them) |

The other FileDB files of the installs (every file read and written back byte for byte; the big sets
sampled, 300 files each):

| File | 2205 | 1800 | 117 |
|---|---|---|---|
| `maindata/file.db`, the archive index (`othergames/index2205.py`) | v1 | v3 | `file_h.db`, v3 (another tree: `sources/anno117.read_archive_map`) |
| terrain tiles `.tmc` | - | v1 192, v2 15, v3 93 of 300 | v3 |
| height pages `.ctt` (`_CTT`, the size, then a zlib stream of the document) | - | v1 199, v2 15, v3 86 of 300 | v3 |
| building and prop groups `.bfg`, `.brp` | - | v1, v2, v3 | `.bfg` v3 |
| cameras `.a6cam` | v1 (7) | v1 (1) | - |
| `data/infotips/export.bin` | - | v3 | v3 |
| `.clip` (the benchmark's camera path) | - | - | v3 (2) |

The History Editions of 1404 and 1701 carry the same archive index for their own archive (`ahe/file.db`, v1).

Note: the island and map files were counted on 2026-10-17, the other files on 2026-11-07.

## v1

No magic number.

- Tail: the last `uint32` is the offset of the tag name table.
- Node stream (from offset 0): `uint16 id`; for an attribute (id >= 0x8000) a **varint** size follows,
  then that many bytes, unpadded. id 0 closes a tag.
- Varint: 7 bits per byte, least significant group first, high bit set on every byte but the last.
- Name table: varint count, then per entry the NUL-terminated name followed by its `uint16 id`. The
  attribute table follows the tag table directly.

## v2

- Tail (16 bytes): `uint32 tag table offset, uint32 attribute table offset, uint32 8, int32 -2`.
- Node stream: per node `int32 size, int32 id`, then `size` bytes of content zero-padded to a multiple of
  8 (tags have size 0).
- Name tables: `uint32 count`, `count x uint16 id`, then the NUL-terminated names, padded to 8.

## v3 (Anno 117)

As v2, with `uint32 0, uint32 number of nodes + 1` in front of the tail and the magic -3:
`uint32 tag table offset, uint32 attribute table offset, uint32 8, int32 -3`.

`filedb.py` reads all three (`version` tells them apart) and writes any of them (`write(...,
version=)`). A document rewritten with its own name ids (`name_ids`) and layout comes out byte for
byte as it was. A name table holds only the names the stream uses, in the order of the original (no
fixed order in v1: the game's tables come out of a hash map). An id without a table entry reads as
`#<id>` and is written back with that id and no entry.

A v3 document can also be patched in place. `scan` keeps every node's offsets. `patch` splices new
attribute contents in and moves the two name-table offsets of the tail by the change. Tags carry no
size, so nothing else moves. This is how `savegame117.py` edits a 70 MB save without rewriting it
([`save-format.md`](save-format.md)).

## Infotips (`data/infotips/export.bin`)

117's tooltip definitions are one v3 document: `InfoTipCount`, `TemplateCount`, then per tip an `InfoTipData` tag
(`Guid` u32, `Owner`, `ChildCount`, its `InfoElement` tags, `Variables`). An `InfoElement` holds:

- `ElementType` (u32);
- a `VisibilityElement` tag;
- its parts: `Text`, `SubText` and `Value` tags with `TextId` (int64 loca id) and `Text` (UTF-8, literal text with
  `{script expressions}`); `Icon`, `LeftIcon` and `RightIcon` with `IconText`; `Style`;
- nested `InfoElement`s.

`infotips117.from_filedb` reads the texts and the nesting (973 tips and 39 templates).

A mod changes it like an asset file. The mod loader turns the binary into XML (root `InfoTips`), applies the mod's
`data/infotips/export.bin.xml` ModOps and writes the binary back (read from the executable, not seen in a run yet).
The write-back takes this XML form:

- a value tag's text is parsed by the tag's type: `Text`, `IconText`, `Condition`, `ValueText`, `HeadlineText`,
  `SublineText`, `ColorText`, `Source`, `Name`, `WarningTextSource` UTF-8; `TextId`, `IconGUID` int64;
  `ExpectedValueFloat` float; `IsWarning`, `IsTemplate`, `Indentation`, `ExpectedValueBool` bool (`true` / `1`);
  every other tag (`Guid` ...) int32, decimal;
- an element's type is its `Type` attribute, an enum **name** (a number is not read): `InfoElement` Root,
  Headline, HeadlineWithSubline, HeadlineItem, HeadlineColor, Subheader, Paragraph, ParagraphMain, ParagraphFluff,
  TextValue, TextValueMain, Shortcut, Subline, Image, ProgressBar, SeparatorLine, SeparatorLineCenter,
  SeparatorSpace, List, Grid, GridElement, Section, Frame, Container, Template, Tooltip (value 0 to 25);
  `VisibilityElement` None, Condition, Group; inside it `OperatorType` (And, Or), `ResultType` (Bool, Int, Float,
  Dataset) and `CompareOperator` (Equal, Not, Smaller, SmallerOrEqual, GreaterOrEqual, Greater) as named text;
- `ChildCount`, `InfoTipCount` and `TemplateCount` are left out: the write-back counts;
- a tag with element children becomes a tag, one with text an attribute, an empty one an empty tag.

`infotips117.modops` writes that form (one `add` on `/InfoTips`). `read_modops` reads it back (the test's round
trip).

Note: read on 2026-10-26; the counts are from the game of that date.

## Anno 1800 island files, as found in the data

- `<name>.a7m` is an RDA V2.2 archive holding `gamedata.data` (the game state: terrain heights, grids,
  areas, objects) and `rd3d.data` (the render terrain). `<name>.a7minfo` beside it describes the island
  for the map generator (`MapSize`, `ActiveMapGrid` one bit per 8 x 8 tiles, `ActiveMapRect`,
  `ObjectMetaInfo/SlotObjects` per slot type, `Version`). `_gamedata/<name>/` holds the terrain meshes
  `meshes/<lod>/<x>x<z>.tmc` (a `Meshes` grid of 32 x 32 entries with `Indices`, `Vertices`,
  `UsedMaterialBits`, `GroundObjects`), normal and tint map pages and three preview images.
- `gamedata.data`: `TerrainManager/HeightMap` is a dense `Width x Height` int16 array, two samples per
  tile plus one (769 x 769 for a 384-tile island), 1/128 world unit per step (the same scale as 117).
  `AreaIDs` is a dense uint16 per tile (`x`, `y`, `val`). `WorldManager/Water`, `RiverGrid` and
  `RegrowManager/TreeMap` are bit grids (`x`, `y`, `bits`, rows padded to 32 bits). 117 adds a
  `FordGrid` and a `ForestChunkMap` and stores `AreaIDs` in 16 x 16 blocks.
- `rd3d.data`: the `Terrain` header and levels as in 117, `CoarseHeightMap` at twice the game heights,
  plus `SlushMap`, and outside `Terrain` the `WaterInfos` and `WaterFlow` maps and the island's props
  (`PropGrid` with `FileNames` and `Instances`; the props are `.prp` files).
- Ground materials: `rd3d.data` holds them itself, where 117 draws from `_gamedata/<name>/materialmaps/`.
  `MaterialMapWidth` is 2 texels per tile. `MaterialIDs2` is one `uint32` per `MaterialIDBlockSize` (2) x 2
  texels with six 5-bit material ids. `MaterialAlphas2` is one `uint32` per texel with six 5-bit opacities.
  This is 117's `matid` / `matalpha` packing. (117's own `rd3d.data` keeps smaller copies under the same
  names.) The ids are those of the material set `MaterialSetFileName` (UTF-16). Old World islands name
  none and get the engine's default, `data/config/engine/material_sets/terra_01.xml`. The material set and
  material files have 117's layout.
- Objects: `AreaManagerData`, one nested FileDB per area (in the island's layout), with the groups
  `GameObject`, `EditorObject`, `NaturePreset` and `Prop`. An object has 117's fields (`guid`,
  `Position`, `Mesh/Orientation` quaternion x, y, z, w, `Mesh/Scale`). Its model comes from its asset in
  `data/config/export/main/asset/assets.xml`.
- `.a7t` (map template) is an RDA archive with one `gamedata.data` holding the empty session.
  `.a7tinfo` holds the `MapTemplate`: `Size`, `PlayableArea`, `InitialPlayableArea`, and one
  `TemplateElement` per island slot (`ElementType`, `Element` with `Position`, `Size` 0..2, `Difficulty`,
  `Config/Type`, or a fixed island's `MapFilePath` and `Rotation90`).
