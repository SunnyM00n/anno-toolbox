# Anno 2205 session maps (`.a6m`)

This page describes what an Anno 2205 session map holds. It was read from the shipped maps
(`session_arctic_01`, `session_earth_02`, `session_moon_01`; the object lists and material maps of all 24) and
from the session and object assets. The tree files inside use FileDB layout v1 ([`filedb.md`](filedb.md)), and
`filedb.py` reads them. Every map's two tree files come back byte for byte (all 24). Nothing here was tried in a
game.

Note: read on 2026-10-17. The byte-for-byte round trip was checked on 2026-10-27.

## Files

- `data/<...>/sessions/<name>/<name>.a6m`: an RDA V2.2 archive with `mapinfo.info`, `gamedata.data` and,
  in the original maps, `rd3d.data`.
- `_a6mdata/<name>/` beside it: `mapimage.png` / `mapimage_<n>.dds` (the minimap), and the terrain
  normal-map pages `normalmaps/base.dds` and `normalmaps/<level>/<x>x<y>.dds` (some `<x>_<y>.dds`). In the
  last patch it also holds a loose `rd3d.data`, which the game prefers to the one inside the `.a6m` (the
  patched `.a6m` files carry none). The loose file is an RDA V2.2 archive whose one member `rd3d.data` is
  the tree file. Inside an `.a6m`, the member is the tree file itself.
- A session's Python script `<name>.py` lies beside the map.
- The Session asset (`data/config/game/asset/sessions.xml`) names the map (`MapFile`) and lists the
  session's areas (`Islands`), its start area (`StartIsland`), its fertilities (`Fertilities`), region
  and story.

Units: map tiles. Object positions are 64-bit fixed point with 4096 per tile (the height `y` is in the
terrain's world units, which match `HeightMap` / 128). `Direction` is 4096 per degree (90 degrees =
368640). Bezier paths and props use floats in tiles.

## `mapinfo.info`

| Field | Type | Notes |
|---|---|---|
| `MapSize` | int32 x 2 | 1024 x 1024 in every shipped session but the orbit (`session_orbit_01`, 512) |
| `DefaultSessionGUID` | int32 | 5100028 ("Session Editor") in the files |

## `gamedata.data`

`FileVersion` 2, then `GameSessionManager` with one tag per manager. What a map holds:

| Path | Content |
|---|---|
| `SessionSettings` | `HasTerrain`, `HasWater` (default 1), `HasAirGrid`, `MirrorTerrain` (0), `CullUnderwaterTerrain` (1), `GlobalAmbientName` (UTF-16), `HiResNormalMapFileName`, `TimeOfDay` (float, 0.5), `PlayableArea` (int32 x0, z0, x1, z1 in tiles), `EditorSessionGUID` |
| `TerrainManager/WorldSize` | int32 x 2 |
| `TerrainManager/HeightMap` | `Width`, `Height` (2049 = 2 per tile + 1), `HeightMap` = int16 per sample, row-major with z as the row, 128 per unit (land at about 4.75, sea floor about -8) |
| `AreaManager/ConstructionMap/AreaInfo` | one empty item per entry of the Session asset's `Islands` list |
| `AreaManager/ConstructionMap/AreaLinks` | pairs of area indices (none in the maps checked) |
| `AreaManager/ConstructionMap/AreaDistribution` | `x`, `y` (1024), `val` = int8 per tile: index into the session's `Islands` list, -1 = no area |
| `WorldManager/StreetMap/StreetID` | `x`, `y`, `val` = one street id byte per tile (0 in a fresh map) |
| `WorldManager/Water` | `x`, `y`, `bits`: one bit per tile, rows padded to 32 bits, least significant bit first; **set = land** |
| `WorldManager/EnvironmentGrid/EnvironmentGRid` | `x`, `y` (128), `val` = one byte per 8 x 8 tiles |
| `WorldManager/AirBlockerGrid` | optional bit grid; absent in the shipped maps |
| `AnimalManager/HerdGrid` | bit grid per tile: where herds may walk |
| `CSlotManager/SnapPartner` | pairs of object IDs (int64): objects snapped to each other |
| `GameObjectManager` | the objects, their groups and the editor's folders (below) |
| `SessionCameraManager`, `CameraSequenceManager`, ... | cameras and sequences |

The `Islands` list of a Session asset is ordered: construction areas first (SpawnArea assets without a
`SpawnType`), then `ImpassableArea`, then `PassableArea` (sea lanes). Construction and impassable
areas lie on land (`Water` set), passable areas on sea.

## Objects (`GameObjectManager`)

| Tag | Content |
|---|---|
| `GameObjectIDCounter`, `NonGameObjectIDCounter` | u64, the next free object IDs (absent in fresh maps) |
| `GameObjectLabelMap` | pairs of unnamed attributes: a label (ASCII, e.g. `spaceport`) and the object ID (u64) it names |
| `ObjectGroupCollection/ObjectGroups` | per group a name (unnamed attribute) and an item with `GameObjects`, the object IDs (u64) in it |
| `ObjectGroupFilterCollection/Root/Filter` | the editor's folder tree: items with `FolderID` (u32) and a nested `Filter` list; no names are stored |
| `GameObject/objects`, `NaturePreset/objects`, `EditorObject/objects` | the objects, one unnamed item each; `NaturePreset` is empty in every shipped map |

An object item:

| Field | Type | Notes |
|---|---|---|
| `guid` | u32 | the asset; everything not listed here comes from it |
| `ID` | u64 | the object ID (high bits vary; groups and labels refer to it) |
| `Variation` | u32, default 0 | index into the asset's `Object/Variations` list of model files (from 0) |
| `ObjectFolderID` | u32, default 1 | the folder in `ObjectGroupFilterCollection` |
| `Position` | `x`, `y`, `z`: int64 / 4096 | `x`, `z` in tiles, `y` the height; an absent axis is 0 |
| `Direction` | int64 / 4096 degrees | the turn about the vertical axis |
| `PlayerID` | int16, default 0 | the owner |
| `FreeOrientation` | `Orientation` 4 x float (quaternion x, y, z, w), `Scale` float (default 1) | when present the whole rotation; its turn about the vertical axis is minus `Direction` |
| `DynamicVariation/ConfigFilename` | UTF-16 | a model file of the object's own; wins over the variation |
| components | tags named after the asset's components (`Mesh`, `Walking`, `Blocking`, `Infolayer`, ...) | their saved state |

The model an object draws is its `DynamicVariation` file if it has one, else entry `Variation` of the
asset's `Object/Variations` list. When the variation is past the list (`session_moon_03` keeps 30 city
screens at variations 30-33 of a 23-entry list) or the file does not load, the game draws its placeholder
asset (1000101, or 1000014 for props). In the shipped sessions, every object's asset names a model. The
only ones that do not resolve are developer placeholders (`test_data/graphics/placeholder/`) that do not
ship.

The asset files are `data/config/game/asset/assets.xml` and the files its `<Group><FileName>` entries
include, the DLC files (`data/dlc01/config/game/asset/asset.xml`, ...) among them. The game reads
`assets.xml` first and then its include queue from the back. A GUID defined twice keeps the definition
read last. An asset's `Object/Variations` comes from its own `<Values>`, else from its `BaseAssetGUID`
asset, else from its `<Template>` in `templates.xml` (`StarterObject Dock Point` and others carry the
list there).

Objects of interest for the world: lakes and rivers are EditorObjects with a `BezierPath` (`Path`:
`Minimum`, `Maximum`, `Closed`, `BezierCurve` items with float `p`, `i`, `o` points) and a `Lake`
(`LakeType`, `WaterColor`) or `River` component. Dock points are objects with `Occupyable` and
`Blocking`. The maps hold no slot or deposit objects: fertilities belong to the session.

## `rd3d.data`

A FileDB v1 tree (the loose copy wrapped in an RDA archive, see Files):

| Tag | Content |
|---|---|
| `Terrain` | `GridSize` 32768 (tiles x 32), `NodeSize` 128, `UnitScale` 1/32, `MaterialMapWidth/Height` 2048, `TintMapWidth/Height` 4096, `GritMapWidth/Height` 2048, `PageSize` 512, `PageBaseResolution` 64, `NodeSizeWS` 4, `MaterialSetFileName` (UTF-16, e.g. `data/config/engine/material_sets/polar_01.xml`), `MaterialIDs2` (512 x 512 uint32), `MaterialAlphas2` (2048 x 2048 uint32), `Tints` (the tint map: `TintMapWidth` x `TintMapHeight` texels as raw BC1 blocks row by row, rows = z, no header; the luminance in green, the two chroma channels squared about the middle, as the bake's `TINT_YCC_COMPRESSED` / `TINT_POW2` read it; read on `session_earth_02`), `SlushMap`, `Levels` (nine mesh levels of nodes: `LevelSize`, `NodeSizeWS`, per node `BoxMin`, `BoxMax`, `IndexCount`, `VertexCount`, `Indices`, `Vertices`, `UsedMaterialBits`, `AtlasSizeFactor`), `GritMap2` (512 x 512), `AllNormalMapLevels` (page flags per level), `MaterialInfos` (`BaseColorLinear` per material id, tags named by the id), `CoarseHeightMap` (2049 x 2049 int16, the game heights x 2) |
| `WaterInfos` | `width`, `height` (512), `map` = 4 bytes per 2 x 2 tiles |
| `WaterFlow` | the same size; (127, 127, 255, 0) = still water |
| `PropGrid/Props` | per prop `FileName` (UTF-16), `Position`, `Rotation` (quaternion), `Scale`, `Color` |
| `TerrainHeightMap` | 1024 x 1024 float32, the height at each tile centre |

Note: the `Tints` map was read on 2026-11-14.

### The ground's material maps

- `MaterialAlphas2`: `MaterialMapWidth` x `MaterialMapHeight` u32 (2048 x 2048, 2 texels per tile), rows
  = z; six 5-bit opacities per texel (bits 0-29), slot 0 in the low bits.
- `MaterialIDs2`: one u32 per **4 x 4** alpha texels (512 x 512, so 2 x 2 tiles), six 5-bit material
  ids for the six slots of the alpha texels under it. There is no `MaterialIDBlockSize`: the block of 4
  is fixed in the game's bake shader. The packing is 117's and 1800's; only the block differs (2 there).
- The ids are the `ID`s of the material set (`MaterialSetFileName`, files in
  `data/config/engine/material_sets/`, the same layout as 117's). A material with `Type` 1 is a ground
  object layer (`ObjectFileName`, a `.prp`: grass, gravel); it is painted into the maps but not drawn
  into the ground.
- How the game lays the ground: one pass per `Type` 0 material, **in the order the material set file
  lists them**, skipping a material that a terrain node's `UsedMaterialBits` lacks. A pass's coverage at
  a texel is the sum of that material's alphas over the six slots (bilinear over 2 x 2 texels). Passes
  under 0.02 are dropped. The pass is blended over the earlier ones with the diffuse alpha and the
  material's `AlphaSharpness` (117's `BlendSplat`). What no pass covers stays the bake's grey
  (0.7, 0.7, 0.7). The slot order is not the drawing order.

## Rivers, lakes, props, folders and the portrait scene

These were read from all 24 maps. The editor draws them (`island-editor.md`, "Other games").

- **Rivers and lakes** are `EditorObject`s of the `River` and `Lake` assets with a `BezierPath` (`Path/BezierCurve`
  items `p`, `i`, `o` = point and tangents in tiles, `w` = a river point's width), exactly 1800's layout. A lake's
  points all lie at its level (`p` y). `session_earth_02` has one river and two lakes, the other maps have 0 to 23
  lakes (Tundra 23), and the Moon maps have none. Objects of the plain `BezierPath` asset (33 on `session_earth_02`)
  are paths without water. The sea is the whole map under height 0. `SessionSettings/HasWater` 0 (the Moon maps,
  the portrait scene) means there is none.
- **Props** (`rd3d.data` `PropGrid/Props`) are one item each: `FileName` (a `.prp`, the same file format as 1800's),
  `Position` (3 floats, tiles), `Rotation` (quaternion), `Scale` (3 floats) and `Color` (4 floats). 1800 stores a
  list of file names and an index per prop instead. A map holds from none (the orbit, strategic view and portrait
  maps) to 124,347 props (`session_earth_06`).
- **Object IDs** are `<u32 serial><u32 kind>`. The high word is the asset's kind (an object of another asset has
  another high word). The low word counts the objects of that kind from 1. The counters `GameObjectIDCounter` and
  `NonGameObjectIDCounter` are in none of the 24 maps, so a copy takes the highest serial of its kind plus one.
- **Folders**: `ObjectGroupFilterCollection/Root/Filter` is a tree of `FolderID`s (1,815 folders on
  `session_earth_02`, folder 1 the default one at the top). No name is stored. `ObjectGroupCollection` is something
  else: named groups (`Coast01`, `spaceport`...) listing object IDs, most of them empty.
- **`portrait_bot`** (`data/sessions/meta/`) is a 64 x 64 tile scene. Its `rd3d.data` has no `CoarseHeightMap` (the
  game's own `TerrainManager/HeightMap`, flat, is the only height). It has no water and one object (a marker). Its
  `MaterialSetFileName` names `fpp_moon01.xml`, which no archive holds (the game ships `moon_01.xml`, which has the
  material id 1 the map paints with).
- Every map has its `rd3d.data` either inside the `.a6m` or loose (`_a6mdata/<name>/rd3d.data`, an RDA archive around
  the tree file), never both: 12 of the 24 have it loose.

Note: read on 2026-10-27.

## How the game finds a map (read from the program, not run)

The game reads the archive index `maindata/file.db` (a FileDB v1 tree of folders and files) once at start and finds
every file through it. An entry says which archive of the index's list holds the bytes, where they are, how long
they are, and whether they are compressed. **A member of a map has an entry of its own**, named
`<map>.a6m|gamedata.data` (also `|mapinfo.info`, `|rd3d.data`). It carries the member's absolute position in the
archive that holds the map (the map's own header and the member's offset inside it, added). A map's three members,
the loose `rd3d.data` and its member `...rd3d.data|rd3d.data` are all entries. The index lists 59 archives
(`data0..18`, four languages of ten).

- A **loose file** is looked for only when the index has no entry, or before the archive when `engine.ini` sets
  `FileSystem.PreferLocalFiles` (loose files win then). A path with a `|` is **never** read from a loose file. So a
  new `x.a6m` laid into `data/sessions/...` is not read for a map the index knows, with or without that setting. This
  is why 2205 has no mod folder to put a changed map into.
- An entry the index lacks makes the game split the name at `|`, open the part before it as an archive and look
  inside. So a map the index does not know loads from a loose `.a6m`, but this is no way to change a shipped map.
- **What does work, by the format**: an archive of our own appended to the index's list, holding the changed members
  (zlib streams of the `78 01` kind, as in the maps), with the entries of those members pointing into it. The game
  opens the archives of the list when it reads the index, so no code changes. The rest of the index stays as it is
  (`othergames.index2205`: the real `file.db` comes back byte for byte). The map's own `.a6m` entry stays. The
  patched maps of the last game patch are laid out the same way: their `.a6m|rd3d.data` entry still points at
  the old copy in `data3.rda`, and their `rd3d.data` is the loose one.
- This is not tried in the game. It replaces `maindata/file.db` (the editor keeps the original and restores it),
  which the launcher's file check would repair.

Note: read on 2026-10-27.

## Writing a map: what an edit touches (read from `session_earth_02` and written back, not run in the game)

The island editor edits a session as a whole map (1024 tiles, a fixed set of areas, no pool of islands to
pick from). Heights, ground paint, rivers and lakes are written by `editor/formats/a7m/sessionground.py`. The keys
of the edit file are 1800's (`heights`, `paint`, `rivers`, `lakes`). Only what an edit reaches is rewritten: an
unedited map comes back byte for byte, and an edit leaves every other attribute of both tree files as it was (tests:
`tests/editor/formats/a7m/test_session2205ground.py`, on the installed `strategic_view_01` and on `session_earth_02`).

What a **height** edit (corner deltas in world units, one corner a tile) changes:

| Where | What |
|---|---|
| `rd3d.data` `Terrain/CoarseHeightMap/map` | int16, 256 codes a unit: the bilinear delta at each half-tile sample |
| `gamedata.data` `TerrainManager/HeightMap/HeightMap` | the same samples, 128 codes a unit. The flat deep sea (-8 in the coarse map) is **0** here; a sample on it stays 0 until an edit lifts it clear of the floor, then it takes the new height |
| `rd3d.data` `TerrainHeightMap/map` | float32, the coarse sample at each tile's centre (`[2z+1, 2x+1]`, checked on the whole map: largest difference one code of the game's 128) |
| `gamedata.data` `WorldManager/Water` | the land bits of the tiles the edit lifts out of the sea or sinks into it (a tile is land above 0.0625 mean corner height) |
| `rd3d.data` `WaterInfos/map` | the first byte of each 2 x 2 tile cell the edit moved: `clip(round(63.75 x depth), 23, 255)` (depth = minus the height at the cell centre; 23 on land and in the shallows; 94 % of a real map's cells lie within 2 of it). The other three bytes (shore foam and the like) are kept: their rule is not known |
| `rd3d.data` `Terrain/Levels` | the nine mesh levels (`Nodes`, row-major in `LevelSize` x `LevelSize`; a node is `4 << level` tiles wide). The vertices are 117's `.tmc` vertex (x, z in 1/32 tile, height + 32768 in 1/256 unit, normal bytes z, y, x first): each vertex under the edit is raised and its normal follows the new slope; the node's `BoxMin`/`BoxMax` heights are widened |

The **normal-map pages** are `_a6mdata/<name>/normalmaps/<level>/<x>_<z>.dds` at level 0 and `<x>x<z>` at levels 1
and 2. `session_earth_02` has 1,201 of them: 812, 289 and 99. They are BC1 without mips, 512 x 512 on the pages
checked, with `16 << level` tiles a page. A page holds a tangent normal in RGB with green up. Fitted to the heights
of the pages checked, the slope per tile is `(-(2R/255-1) / (2G/255-1), -(2B/255-1) / (2G/255-1))` for x and z
(correlation 0.92 on level 0, 0.77 to 0.85 on level 1, regression slope 1.0 to 1.1).

A height edit adds the slope of its delta (the bilinear delta blurred over a tile, then differentiated) to the slope
a page stores and renormalises. It encodes again only the 4 x 4 blocks the edit changes (`models/bc1encode`:
principal-axis range fit, 42.6 dB on a real page). The rest of a page keeps its bytes. The build puts the rewritten
pages into our archive and points the index at them (the index has an entry for every page). The tint pages and the
grit map are kept. Only the shading is rewritten: a page keeps the detail it had. How the game's shader uses the
pages' channels was not read; the fit is from the data (`editor/formats/a7m/sessionpages.py`,
`tests/editor/formats/a7m/test_session2205pages.py`).

**Paint** (`MaterialIDs2` per 4 x 4 texels, `MaterialAlphas2`): the strokes of the map's own material set are laid by
`groundedit.paint_map` (the same encoder as 117's and 1800's, block 4). The painted materials' bits are set in
`UsedMaterialBits` of every stored node under the stroke, at every level.

**Rivers and lakes**: the editor objects' Bezier paths are written as 1800's (`groundedit.apply_water`). An object's
position is fixed point here, so a lake's level moves its `Position.y` and its path bounds.

**A new map is not a new island.** A session is the whole of one map: its terrain, its areas (`AreaDistribution`), the
Session asset that names it and the other sessions' references to it. "Blank island creation" therefore does not
exist for 2205; the editor edits the map. A *blank map* would be a donor map with its objects, props and ground
cleared and its heights flattened (all of it the same functions), written under a new name. It would also need:

- a Session asset in `sessions.xml` pointing at the new `MapFile` (the game has no mod folder: an entry in our
  archive and in the index, as for the edited maps);
- the 1,200 normal pages and the minimap images of the donor (or none: a map the index does not know loads from a
  loose `.a6m`);
- a game that accepts a session it has no story for.

This is not built, and only the game can say whether such a session starts.

Note: read and written back on 2026-11-12.

## Compared with Anno 117

- The same container idea (`.a7m` = RDA with `gamedata.data` and `rd3d.data`), FileDB v3 instead of v1.
- Heights: the same numbers (int16, 128 per unit, 2 samples per tile + 1), stored dense here and in
  sparse 16 x 16 blocks in 117.
- Ground: the same packing and the same material-set order; one id per 4 x 4 alpha texels here, per
  2 x 2 in 117 (`MaterialIDBlockSize`); 117 keeps the maps in `materialmaps/`, 2205 in `rd3d.data`.
- Objects: 117's fields under other names. Positions are in fixed point instead of floats, the rotation
  is in `Direction` / `FreeOrientation` instead of `Mesh/Orientation`, and the model is a variation index
  into the asset instead of a file per object. The lists are flat per manager instead of per-area
  documents.
- `Water` means land in both; `EnvironmentGRid` is per 8 x 8 tiles here and per 4 x 4 in 117.
- 117 islands are separate files merged into a session; a 2205 session is one map whose land is
  divided into areas by `AreaDistribution`. 117's `AreaIDs`, `RiverGrid`, `FordGrid`, mine slots and
  fertilities per island have no 2205 counterpart.
