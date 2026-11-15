# Anno 1404 world and random-map template files

This page describes how Anno 1404 (History Edition) stores a world: which islands lie where on the sea,
how they are turned, and which fertilities, deposits and natives they have, plus the script areas and the
players. It also covers the random-map templates that the game fills with islands. The format was read
from the shipped files (103 worlds and templates of the current writer, 35 older test worlds) and from
what the History Edition's world editor reads and writes. It is not yet checked by loading a written file
in the game. Anno 2070's worlds use the same layout with additions (last section).

Note: read on 2026-10-17.

## Container

The files are the tagged text that islands (`.isd`) use. `isd2070.parse` / `serialize` read and write
them, byte for byte for the current writer's files.

- `<Tag>` opens a node and `</Tag>` closes it. The text between them is the value. Lines end in CRLF.
- A binary value is `CDATA[`, a little-endian uint32 length, that many bytes, `]`.
- Older files (the 2008 test worlds) wrap a binary value as `<Tag><data>CDATA[...]</></Tag>` (or
  `<data>empty</>`), close with the generic `</>`, and name island fields after their members
  (`m_Name`, `m_GOPFilename`, `m_IslandDirection`, `m_XPosition`, `m_ZPosition`, `m_Fertility`). Readers
  accept both.
- Reading: whitespace (space, tab, CR, LF) before a token is skipped, and text runs to the next `<`.
  `<Tag/>` is an empty node and `<!--...-->` is a comment. Attributes are not parsed (all text up to `>`
  is the tag). There are no entities, except that some string values store `<` and `>` as `&lt;` and
  `&gt;` (`&` stays as is). A tag is looked up as the first child of that exact name.
- Some readers also take a value's members as children: a 2D point as `<m_x>`, `<m_z>` (the int64 in
  decimal), and a list of integers as `<arraysize>` plus one `<i>` per element.
- The same tree has a binary form. Anno 2070 writes its save games in it; these files never use it. It
  starts with `BIN `. Each node follows as an int32 tag number (from 1) and an int32 length (-1: a
  NUL-terminated string follows, above 0: that many bytes, 0: no value), then its child nodes. Every node,
  a leaf too, is closed by an int32 -1. Then come `MAP `, the tag count, per tag a uint8 name length
  (with the NUL), the name and its int32 number, and last `END ` and the offset of `MAP `.
  `formats/bintree.py` reads and writes it byte for byte ([`../save-editor.md`](../save-editor.md)).

Note: the binary form was read from a 2070 save on 2026-11-13.

Value encodings:

| Kind | Encoding |
|---|---|
| integer, boolean | decimal text (booleans 0 / 1) |
| string | text |
| wide string (`MapInfo.xml` names and descriptions) | binary: the UTF-16LE code units, two bytes per character, no byte-order mark and no terminator; an empty string leaves the tag empty |
| list of integers | one binary block of int32 |
| list of integer pairs | one binary block of int32 pairs |
| list of records | one `<i>` per element |
| map | `<k>key</k><v>value</v>` pairs |
| optional record (in a list or map) | the value starts with `<hasValue>1</hasValue>` |
| 2D point | binary, 16 bytes: x and z as int64 fixed point with 12 fraction bits (divide by 4096) |
| 3D point | binary, 24 bytes: three such int64 in the order x, z, height |
| colour | binary, 16 bytes: float32 r, g, b, a in 0..1 |
| rectangle | `<left>`, `<top>`, `<right>`, `<bottom>` (integers, tiles) |
| point | `<x>`, `<y>` (integers) |
| bit grid | `m_XSize`, `m_YSize`, `m_IntXSize` (uint32 words per row), `m_BitGrid` (binary rows of uint32; bit x of row y) |
| shape | `<Polygon>` with one `<i>` 2D point per corner, or `<Circle>` with `Center` and `Radius` |

## World (`.www`)

Root `WorldConfig`, in this order:

| Tag | Content |
|---|---|
| `Width`, `Height` | world size in tiles; square (256 to 1536 in the shipped worlds) |
| `Areas` | list of optional records: `m_Name` and `m_Shape` (a shape in world tiles); named areas for scripts and quests |
| `WorldLightProfiles` | `North`, `South`: light profile names (keys of `data/config/engine/AmbientMap.xml`), empty for the default |
| `Islands` | map from island id (1, 2, ...; a new island takes the lowest free id) to an optional `IslandConfig` |
| `GameData` | `PlayerInfos`: map from player 0..7 to `m_Type` (1 human, 2 computer), `m_Profile` (AI profile GUID) and `m_Color` (colour) |

`IslandConfig`:

| Tag | Content |
|---|---|
| `Name` | display name |
| `Filename` | the island file, relative to the data root (`\` or `/`), e.g. `data\Levels\Islands\North\N_M02.isd` |
| `Direction` | 0..3, quarter turns about the island's centre (below) |
| `PositionX`, `PositionZ` | world tile of the island's top-left corner; multiples of 16 in every shipped world |
| `Width`, `Height` | the island's size; written for reference, a reader takes the size from the island file |
| `Type` | 0 regular, 1 main, 2 start harbour, 3 corsair, 4 decoration, 5 Venetian, 6 volcano; missing means 0 |
| `Fertility` | list of fertility ids (the game's `Fertility` data set: 3 Dates, 4 Spices, 5 Cider, 6 Hemp, 7 Wheat, 8 Herbs, 9 Grape, 10 Bees, 11 Clay, 12 Rose, 13 Coffee, 14 Indigo, 15 Silk, 16 Almond, 17 Sugarcane, 18 wildcard, one entry per wildcard); 0 and ids above 18 are ignored |
| `LightProfile` | the island's own light profile, or empty |
| `ObjectConfigs` | map from an object id on the island to `<IntValues>`, a map of integers: 0 resource type (0 Stone, 1 Quartz, 2 Brine, 3 Coal, 4 Copper, 5 Iron, 6 Gold, 7 Pearls, 8 Fur animals; Venice 9 Custom), 1 amount in kg, 2 refill maximum in kg; campaign worlds also carry 3 and 4 (meaning unknown) |
| `NativeSlotConfigs` | map from the index of a native slot of the island to the GUID of the native settlement placed there |
| `AutoBuildConfig` | `PlayerID`, `IslandType`, `BuildingList` (pairs of building GUID and count): a computer player's starting town; present only when set |

An object config only applies when the object exists on the island and its kind fits. The kind follows
from the object's GUID: 600 is a mine (brine, coal, copper, iron, gold), 601 a reef (pearls), 602 a cave
(stone, quartz, fur animals), 603 a river, 604 a quartz deposit and 605 a stone deposit. A native slot
index beyond the island's slots is ignored.

The game's own worlds carry further tags that the world editor neither reads nor writes
(`MiniMapTexture`, `m_Name`, `m_Description`, `m_NameTextID`, `m_DescriptionTextID`, `m_PreviewImage`,
`m_Difficulty`, `PlayerPositions`, `PlayerPositionsEasymode`, `WorldLightProfiles/Center`, an island's
`ExplorationStatus`).

### Placement

Take an island of size w x h (even, and square for every 1404 island: 32 to 208 tiles in steps of 16) at
corner (left, top) with direction d. With dx = x - w/2 and dz = z - h/2, its own point (x, z) maps to the
world point

| d | world x | world z |
|---|---|---|
| 0 | left + x | top + z |
| 1 | left + w/2 - dz | top + h/2 + dx |
| 2 | left + w/2 - dx | top + h/2 - dz |
| 3 | left + w/2 + dz | top + h/2 - dx |

Width and height are not swapped by a turn. A world point belongs to the island when, mapped back into
the island, its 16-tile chunk is set in the island's `UsedChunks`. The world editor keeps islands on the
16-tile grid and inside the world, and never lets two islands claim the same chunk. Their rectangles may
overlap where the chunks are unused.

### What a world needs from an island file

- `Width`, `Height`, `Clime` (0 north, 1 south) and `Difficulty`;
- `UsedChunks` (bit grid of 16-tile chunks);
- `SendExplorationMessage` (set on playable islands);
- the resource objects (object groups under `m_GOPManager/Objects`: `m_ID`, `m_GUID`, `m_Position`);
- the `NativeSlots` (each with `BuildingSet`, `BuildingPosition`, `BuildingDirection`, `GateSet`,
  `GatePosition`, `GateDirection`; older files `ThirdPartySlots`);
- `<island>.png` beside the file, the top view a world editor draws.

The tag tree of 1404's hand-made benchmark world (`data/levels/benchmark/basic/benchmark.www`) writes
empty elements XML's way, `<Areas/>`. `isd2070.parse` keeps them (`Node.empty`), so all 66 worlds, 72
templates and 481 islands read and write back byte for byte.

Note: `isd2070.parse` keeps the empty elements since 2026-11-07.

## User-content map (`.rdu`)

A user's map is an RDA archive in the V2.0 format of the game's own archives (`rda.write_v20`). It is
started from `data/config/game/template.rdu` (Venice: `addondata/...`). The template holds `icons.xml`,
`mapinfo.xml`, `assets.xml`, `quests.xml`, `aiprofiles.xml`, `features.xml`, `texts.xml` (texts in GUIDs
100000000 to 199999999) and `data/loca/ger/txt/usercontent.txt` (UTF-16). Venice adds `version.xml`.
Saving adds:

| File | Content |
|---|---|
| `Map.www` | the world |
| `MapInfo.xml` | `WorldInfo`: `Name` (up to 128 characters), `Description`, `UserImage` (a file in the archive), `Difficulty` 0..2 |
| `Map.jpg` | the map's preview, 256 pixels |
| `UserImage.jpg` | the author's picture, longest side 256 pixels |
| `Version.xml` | `<Version>1</Version>` for the base game, 2 for Venice |

Inside the game's file system a file in an archive is addressed as `archive|file`.

## Random-map template (`.tww`)

Root `RandomMapTemplate`:

| Tag | Content |
|---|---|
| `Name` | empty in the shipped templates |
| `Width`, `Height` | world size; a template serves the world size whose width it has |
| `IslandTemplates` | map from slot id (from 1) to an optional `<Island>`: `Type` (0 regular, 1 main, 2 start harbour, 3 corsair, 5 Venetian), `Rect` (rectangle in world tiles, on the 16-tile grid; 112, 160 or 208 square for small, medium, large), `Climes` (1 north, 2 south, 3 either) |
| `ResourceProfiles` | three strings, empty |
| `PlayerPositions`, `PlayerPositionsEasymode` | lists of points, empty |
| `WorldLightProfiles` | `North`, `South`, empty |

Slots never overlap. Every shipped template has a north and a south start slot of 112 and a south corsair
slot of 112 (Venice adds a Venetian one), with 13 to 42 slots in all. The generator's settings are in
`data/config/game/RandomMapGenerator.xml` (same container):

- world widths per size (896, 1152, 1408, 1664);
- island lists per slot type and difficulty;
- template lists per island size;
- per amount, world size and clime: the rates, limits, attractions and forced entries for fertilities,
  deposits, natives and rivers.

## Anno 2070

2070's worlds (`data/levels/.../*.www`) have the same root and island records. The shipped scenarios show
these additions: `ArkSlots` (points), the `UnderwaterNorth` and `UnderwaterSouth` light profiles, and per
island `MissionID`, `m_Resources` and `m_Fertility`. Their meaning in 2070 is not worked out yet.
