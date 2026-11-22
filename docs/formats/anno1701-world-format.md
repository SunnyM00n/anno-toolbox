# Anno 1701 world and island files

This page describes how Anno 1701 (History Edition, data of the 2007 add-on) stores its worlds,
random-map templates and islands. It was read from the files in the install's archives (`data0.rda`
and the language archives, "Crypted Resource File V1.1", read by `rda.CRdaArchive`). It is hand-written
from the files themselves. The counts are over every such file in the archives. Nothing here was
written back and loaded by the game.

Note: written on 2026-10-17.

## The tag tree

Worlds (`.www`), world rules (`.wcf`), random-map templates (`.tww`), island headers (`.isc`) and
island data (`.isd`) share one container, the same one Anno 2070 uses (`isd2070.parse` reads it):

- `<Name>` opens a node; `</>` closes the innermost one (`</Name>` does too). Names may contain
  spaces and dots: `Config 0000`, `Player 0000`, `m_DynGridPosition.x`.
- Text between tags is the node's value: integers, floats with six decimals, names, paths.
- `CDATA[` + a little-endian uint32 length + that many bytes + `]` is a binary value.
- There is no header. The tools wrote a line break after some closes and none after others (in a
  `.isc`, `</position>` has one and the `</>` before it has none). Some hand-made files are indented,
  and old developer worlds never close their root. `isd2070.parse` keeps the white space after each
  open and close tag, and an unclosed root. So `isd2070.serialize` writes every one of the 3,046
  tag-tree files in the archives (`.isd`, `.isc`, `.www`, `.wcf`, `.tww`) back byte for byte. The same
  change round-trips 618 of 1404's 619 files and keeps 2070's 609 of 609.

Anno 1404's world files (`anno1404-world-format.md`) are the next step of the same layout. Its
"older files" are 1701's.

Note: the byte-for-byte round trip was checked on 2026-10-17.

Lists come in two generations:

| Generation | List | Map |
|---|---|---|
| older (2006 levels) | `arraysize` + `i0000`, `i0001`, ... | `mapsize` + `k0000`/`v0000`, ... |
| newer (shipped islands, the world editor) | repeated `i` | repeated `k`, `v` pairs; each `v` starts with `hasValue` 1 |

A small vector is either `x`/`y`/`z` children or a `data` child holding a binary value: twelve bytes
are three floats (render positions) or three int32 (logic positions).

### Units

A tile is **32 world units**. Render positions and heights are floats in world units. Logic
positions are int32 fixed point with **4096 per tile** (128 per world unit). Object turns are
radians x 4096 (`m_Direction`) and 64ths of a full turn (`m_GRIDDirection`). World and island
rectangles are in tiles, `left`/`top`/`right`/`bottom`.

## World: `.www`

Root `WorldConfig`:

| Node | Value |
|---|---|
| `m_Width`, `m_Height` | world size in tiles (192 to 2048) |
| `m_BaseTexture` | a ground texture id (0, 42 or 82 in the shipped worlds) |
| `MiniMapTexture` | the world's preview picture (`<world>.jpg`) |
| `m_Name`, `m_Description` | free text |
| `m_NameTextID`, `m_DescriptionTextID`, `m_PreviewImage` | localised name and description, picture (scenarios) |
| `m_Difficulty` | 0 easy, 1 normal, 2 hard |
| `m_Areas` | list of `m_Name` + `m_Rect`: named rectangles that scripts refer to |
| `PlayerPositions`, `PlayerPositionsEasymode` | lists of `x`, `y` (tiles): start points, and the "start on an island" ones |
| `WorldLightProfiles` | `North`, `Center`, `South`: light profile names of `data/config/game/ambientmap.xml` |
| `Islands` | map from island number to `IslandConfig` (below) |
| `HIDObjectConfigs` | map from object handle to `HID`, `Name`, `ConfigList`: per-object property overrides |

`IslandConfig`:

| Node | Value |
|---|---|
| `m_Name` | the island's name in this world |
| `m_GOPFilename`, `m_TerrainFilename` | the island's object file and terrain file; both are the same `.isd` in every shipped world |
| `m_XPosition`, `m_ZPosition` | top-left corner in the world, tiles |
| `m_Width`, `m_Height` | island size, tiles |
| `m_IslandDirection` | quarter turns, 0 to 3 |
| `m_HasVolcano`, `m_DiscoverOnLoad` | flags |
| `m_Fertility` | `data`: 19 x uint32, 0 or 1 per fertility or resource kind |
| `ExplorationStatus`, `LightProfile`, `LakeColor`, `SeaColor` | |

## World rules: `.wcf`

The file sits beside the `.www` and has the same name. Its root is `CGameData`:

- `PlayerInfo` (four players: `m_PlayerId`, `m_Type`, `m_Profile`: an AI profile name);
- `EnableUndiscovered` and `EasyMode`;
- `EnabledCultures` (bit per native culture, 127 = all seven);
- `EnabledDisasters` (bit mask of the disasters) and `EnabledMenaces`;
- `StartCapital`, `EnableTraderQuests`, `GuestsOfHonorSetting` and `PirateStrength`;
- `DemolitionReturn` (how much of the cost comes back on demolition);
- the victory conditions `VictoryConditionsAND`, `VictoryCapitalInt`, `VictoryCitizensInt`,
  `VictoryQuestsInt`, `VictoryCastleInt`, `VictoryColonialGoodsInt`, `VictorySettleAreaInt`,
  `VictoryIndependenceBool`, `VictoryKillOpponentsBool` and `VictoryPirateAllianceBool`.

Shipped multiplayer worlds add `RandomMap`, `KidMode`, `IsScenario`, `IsTutorial`, `Difficulty`,
`Resources`, `MapLayout` and `MapSize`.

## Random-map template: `.tww`

The root is `RandomMapTemplate` (108 shipped, in `data/levels/randommap/` and `randommultimap/`):

- `Name`, `Width` and `Height`;
- `IslandTemplates` (map from number to `Island`: `Rect`, `Climes` bit mask of north and south,
  `Cultures` bit mask, `Fertilities` bit mask, `Resources` bit mask);
- `Islands` (fixed islands, as in a world);
- `ResourceProfiles`, `PlayerPositions`, `PlayerPositionsEasymode`, `WorldLightProfiles` and
  `HIDObjectConfigs`.

The random-map generator fills each placeholder with a pool island of matching size and climate. Its
settings are in `data/config/game/randommapgenerator.xml`: the island list, the templates per layout
(many small, some medium, few large islands), distribution weights, deposit ranges and island
difficulty shares. It hands out fertilities and deposits from a profile of
`randommapresourceprofiles.xml`. A profile says how many islands get each crop, how many limited and
endless deposits of clay, marble, iron, gold and gems there are, and how many whale grounds.

## Island header: `.isc`

The header sits beside each `.isd` and has the same name, with a preview `<island>.jpg`. The archives
hold 1,291 of them.

| Node | Value |
|---|---|
| `Width`, `Height`, `IslandSize` | tiles |
| `Clime` | 0 north, 1 south |
| `Difficulty` | 0 to 2 |
| `AIPoints` | list of `m_AIPointID` (0 to 6) + `m_RasterPosition` `x`, `y` (tiles): hints for the computer players |
| `Coastlines` | list of lines: `m_ID`, `m_GUID` 0, `m_Variation`, `m_Polygon` (points) |
| `Rivers` | list of lines: `m_ID`, `m_GUID` (the river object, 235 or 236), `m_Variation`, `m_Polygon` |
| `RiverBlockings` | list of `x`, `y`: tiles a river runs over |
| `RiverPathBlockings` | list of positions (fixed point, two int32) a river blocks for walking |
| `FogAreas` | list of rectangles |
| `SendExplorationMessage`, `Volcano` | flags |
| `ProjectFile` | the palette the island was built with (`data\project\anno3.prj`) |
| `AreaList`, `CameraList` | named areas and cameras (empty on shipped islands) |
| `Players` | `count` + `Player NNNN`: `Name`, `Player` (culture), `Color`, `Type`, `ObjectConfigList` |
| `DefaultResourceConfigList`, `NeutralObjectConfigList`, `HIDObjectConfigs` | property overrides: `count` + `Config NNNN` of `Label`, `Enable`, `GUID` (property id), `Value` |
| `CrRecord` | build lists for computer players: `m_Titel`, `m_BuildList` of `m_Action`, `m_GUID`, `m_loc`, `m_Locs`, `m_gridDirection`, `m_variation`, `m_Name` |

A coast or river **point** (`m_Polygon/i`) has three parts:

- `position`: three floats in world units, as a `data` value or as `x`, `y`, `z` children (y = 0 on
  a coast, the water height on a river);
- `width`: world units (96, three tiles, is the common coast width);
- `distance`: the length along the line up to this point (0 throughout in the older files).

The newer generation holds 16,679 coast lines in 339 files and 387 rivers in 101 files. The older one
(`arraysize` + `i0000`..., in the points too) holds the coasts of 91 files and the rivers of 118.

### Rivers' blocking lists

`RiverBlockings` holds tiles (`x`, `y`: the tile's column and row). `RiverPathBlockings` holds half-tile cells (two
int32 in 1/4096 tile at the cell's centre, so `.25` or `.75` of a tile; a cell can be listed twice). Both lie along
the rivers' ribbons (the points and widths of `Rivers`), with some slack.

The rule was matched on four islands (`n_g01`, `mis12_n_spieler`, `mis21_asian_m01`, `mis01_s_k07`). The measure is
the gap between a cell's nearest sample point (a 5 x 5 or 7 x 7 grid in the cell) and the ribbon's edge:

- A tile is listed when the gap is at most 0 (precision 0.88 to 0.98, recall 0.93 to 1.0 against the file).
- A path cell is listed when the gap is at most 4 world units (0.82 to 0.98 and 0.96 to 0.99).
- Every listed tile lies within 4 units of the edge and every path cell within 14 (one outlier of 19).

The game's own rule (a river's mesh, probably) is not read. The editor therefore redraws the lists by what a move
changes (`editor/formats/isc/blockings.py`). Entries near the old ribbon that the new one does not reach are dropped,
cells the new ribbon covers are added, and the rest stays.

## Island data: `.isd`

Root `Terrain` (the render terrain) and `m_GOPManager` (logic and objects).

`Terrain/Settings`: `Dim_X`, `Dim_Z` (tiles, a power of two from 16 to 256), `UsedSizeX`,
`UsedSizeZ`, `TileSize_X`, `TileSize_Z` (32), `ChunkSize` (16), `TextureBaseSize_X/Y` (8),
`TextureAlphaSize_X/Y` (32), `TextureTiling` (4 or 8).

`Terrain/HeightMap`: (UsedSize + 1)^2 vertices, row by row (rows are z), **32 bytes** each. The
grid covers `UsedSizeX/Z`, not `Dim`. In 125 of the 407 shipped islands, `Dim` is the next power of two
above the used size, and the grid still has (UsedSize + 1)^2 vertices. The counts below are over the
407 islands in `data/levels/islands/`.

| Offset | Type | Value |
|---|---|---|
| 0 | float | 0, or the vertex's x in world units (94 islands) |
| 4 | float | height in world units (the sea floor is -100; a shipped plateau is at 20) |
| 8 | float | 0, or the vertex's z in world units (the same 94) |
| 12 | float x 3 | normal; unit length in only 40 islands |
| 24 | uint16 | 0xffff on every vertex |
| 26 | uint16 | not decoded: 1 on every vertex in 293 islands, small varying values in the rest |
| 28 | uint32 | colour 0x00RRGGBB (white except where the ground is shaded) |

`Terrain/TileMap`: UsedSize^2 tiles, **36 bytes** each:

| Offset | Type | Value |
|---|---|---|
| 0 | float | not decoded: 20.0 in 293 islands, other bytes in the older ones |
| 4 | uint32 | not decoded: 0 in 345 islands |
| 8 | uint16 | **ground texture id**: a `TEX_GUID` of the palette (below); 93 ids in use, three of them (28, 65, 74) not in `anno3.prj` |
| 10 | uint16 | not decoded: 0, other bytes in 34 older islands |
| 12 | float x 3 | normal of the tile's first triangle |
| 24 | float x 3 | normal of the second |

The undecoded words look like uninitialised memory in the older files (the scenario cliff islands):
read as one uint32, the texture id there comes out above 65,535. There is one texture per tile: 1701
has no blend weights. `Terrain/m_LakeRects` and `m_CoastRects` (with `m_IslandID`, `m_Type`) are
rectangles in tiles.

`m_GOPManager/m_GRIDManager` holds:

- `m_WaterLevel`;
- `m_GRID` (uint32 per tile: flags in the top two bits, the low bits not decoded);
- `m_HeightMap` (int16 per tile, the rounded height);
- `m_FarmInfo` (uint16 per tile, 128 where nothing grows);
- `m_PathBlockerLayer` (two by two cells per tile), `m_BuildBlockerLayer` and `m_CoastBlockerLayer`,
  each a `BitGrid` of `m_XSize`, `m_YSize`, `m_ZSize`, `m_IntXSize` (uint32 words per row) and
  `m_BitGrid` (the bits).

`m_GOPManager/m_HIDManager`: `m_LastHID`, `m_DummyOccupationMap`, then one node per game object
named by its handle in eight hex digits: `m_GUID`, `m_Variation`, `m_Type`, `m_Position` (fixed
point), `m_Direction`, `m_GRIDDirection`, `m_GRIDOffsetX/Z`, `m_DynGridSize`,
`m_DynGridPosition.x/.y`, `m_TerrainState`, `m_IgnoreWater`, `m_BlockDemolition`, `m_OccupiedHID` and
`m_SetToTerrain`. `m_Position` is x, y (height), z, in either `data` form or `m_x`/`m_y`/`m_z`
children (a fifth of the objects). `m_Direction` / 4096 and `m_GRIDDirection` x 2 pi / 64 agree to
within a grid step on all but a handful. The stored height lies on the terrain for objects of
`m_TerrainState` 0 (87 % within one world unit of the interpolated terrain). For state 1 and 3 it does
not (median 21 and 8 units off), presumably because the game sets them onto the terrain (not checked).

`m_GOPManager/m_PIDManager`: `m_LastPID`, then a second object list by handle:
`m_GUID`, `m_Variation`, `m_Type`, `m_Position.x/.y/.z` and `m_Size.x/.y/.z` (floats, world units),
`m_Direction` (float radians), `m_Delay`, `m_DynGridSize`, `m_DynGridPosition.x/.y`.

## The island palette: `.prj`

`data/project/anno3.prj` (and seven personal variants beside it) is text. Its first line is
`PROJECTFILE V1.7`, and `;` starts a comment. It is made of sections, each followed by a `{ ... }`
block of `KEY value` lines. Folders nest and end with `[CLOSE]`:

- `[OBJECTFOLDER]`: `OBJ_FOLDER_NAME`, `OBJ_FOLDER_NEUTRAL`, `OBJ_FOLDER_FOR_ALL`,
  `OBJ_FOLDER_USECFGFILE` (TRUE / FALSE), `OBJ_FOLDER_FILENAME` (the folder's property sheet,
  `data\config\worldbuilder\<x>.cfg`).
- `[OBJECT]`: `OBJ_NAME`, `OBJ_GUID`, `OBJ_RANDOM_VAR` (pick a random variation when placed),
  `OBJ_RANDOM_ROT` (random turn), `OBJ_USE_CONFIG`, `OBJ_CONFIG_FILENAME`, `OBJ_UPGRADES { }`.
- `[UPGRADEFOLDER]` (`UPG_FOLDER_NAME`, `UPG_FOLDER_FILENAME`, `UPG_FOLDER_FOR_ALL_PLAYERS`) and
  `[UPGRADE]` (`UPG_NAME`, `UPG_GUID`).
- `[TEXTURE]`: `TEX_NAME`, `TEX_GUID` (the id in `TileMap`), `TEX_FILENAME`
  (`data\graphics\textures\...\ground\*.tga`), `TEX_GROUP_ID`, `TEX_MAP_TO_GUID` (1 = a variant of
  another texture), `TEX_MAPPING_GUID` (that texture, 65535 for none), `TEX_MAPPING_PROFILE`,
  `TEX_BITS` (below), `TEX_MAP_COLOR` (minimap colour, 0x00BBGGRR).

`anno3.prj`: 83 folders, 818 objects, 90 ground textures, 74 upgrades.

Three files sit beside it:

- `default.dat` (`PROJECT CONFIGFILE V1.7`, `[CONFIGDATA]` with each object's default properties:
  `OBJECT_GUID`, then `CONFIG_GUID` blocks of `CONFIG_LABEL`, `CONFIG_ENABLE`, `CONFIG_VALUE`);
- `dropdown.ini` (`VALUEGUID id label`, then `DROPDOWN value label` choices);
- `lightdata.ini` (`[LIGHT]` presets: `LIGHT_NAME`, then `[TERRAIN_LIGHT]` and `[OBJECT_LIGHT]` with
  `LIGHT_POSITION x y z`, `LIGHT_COLOR r g b`, `LIGHT_INTENSITY`).

`data/config/worldbuilder/` holds:

- the property sheets, `PROPERTY { GUID id LABEL "text" [CHECKBOX] SLIDER { MIN a MAX b } }` or
  `... DROPDOWN { "choice" ... }`, one file per object folder (buildings, units, ships, military,
  mines, resources, level resources, streets, trees, animals, dummies, upgrades, special actions);
- `texturebits.bit` (`BIT n "name"`: 0 unbuildable, 1 unwalkable, 2 to 30 procedural grass kinds 10
  to 38, 31 infertile);
- `camera.xml` (the builder's free camera: clip planes, fog, field of view 0.75, distance 0 to 20000,
  LOD distances).

## Archives

The game mounts `data<n>.rda` for n = 0, 1, 2, ... and, where `data<n>.rda` is missing,
`data<n>_<language>.rda` (`data2_eng`, `data3_eng`), up to the first number with neither. In the
History Edition these are `data0`, `data1`, `data2_<lang>`, `data3_<lang>`, `data4` and `data5`. When
two archives hold the same path, **the entry with the newer timestamp wins**, whatever the order (a tie
keeps the earlier archive). A world packed for play (below) adds its archive under the same rule. No
path is in two of the shipped archives, so the rule matters only for added archives.

`ahe/a3he0.rda` (V2.2) is the History Edition's own interface (2,043 files: its menus,
`data/texts.xml`, the same set as 1404's `ahe/a4he0.rda`). It holds no island data and no path of the
other archives, and its entries are stamped 0. `isd1701.source_tree` reproduces this and reads `a3he0`
last, as 1404's tree reads `a4he0`. Every shipped island is in `data0.rda`. The History Edition's
`ahe/file.db` indexes that archive in the new engine's FileDB tree (`othergames/index2205.CArchiveIndex`
reads it, `filedb.md`).

Note: `isd1701.source_tree` reads `a3he0` last since 2026-11-07.

## Savegames (`.sww`)

Two developer saves ship in `data/levels/testlevel/`. One is the plain tag tree: the world `WorldConfig`
with `m_IsSaveGame` 1. `isd2070.parse` reads it and writes it back byte for byte, now that a value's
trailing blank is kept. The other is packed: `ZZZZ`, a `uint32` size (the unpacked length), then a zlib
stream (2070's saves pack their parts the same way). The Saves app's reader (`saveedit/container.py`)
unpacks the second kind and shows either as a tree. Nothing interprets them
([`../save-editor.md`](../save-editor.md)).

Note: state of 2026-11-13.

## A world packed for play

The world editor saves a playable world as an archive "Resource File V1.1" (or the crypted variant)
named after the world, in a `UserContent` folder. The archive holds `usercontent/<name>.www`, `.wcf`
and `.jpg`, plus `rda_console.ini`. That file holds one console line, `Load("usercontent/<name>.www")`,
the console command that starts the world.

## The occupation grid

`m_GRIDManager/m_GRID` holds one uint32 per tile (rows are z). A tile that a game object holds has bit 30 set and the
object's handle (the number of its `m_HIDManager` node name) in the low 28 bits. 10,412 of the tiles sampled in every
twelfth island are of this kind (the top nibble is 4, or 12 with bit 31). The other words (top nibble 1, 5, 7, 9, 13)
are other data and are not decoded.

`m_DynGridSize` is no tile count (size 2 is 4 x 4 for one asset and 7 x 4 for another): a footprint belongs to the
asset. The footprints were read off the 407 shipped islands (257 assets). An asset's footprint is `w` x `h` tiles. Its
cells are `[-w // 2, w - w // 2)` round the tile the object's origin stands in (`m_GRIDOffsetX/Z`, in world units,
shifts the origin into the tile: 16 centres it). The footprint is turned by quarter turns (`m_GRIDDirection` / 16; a
turn takes (dx, dz) to (-dz, dx)). The rule holds exactly for 20,468 of 21,459 occupied footprints, and for 606 more
where neighbours overwrote some tiles. `othergames/grid1701` reads the shapes and writes the grid after an edit. What
the game does with the word on loading is not known.

Note: the footprints were read on 2026-11-09.
