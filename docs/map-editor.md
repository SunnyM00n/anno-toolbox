# The map editor: a user's guide and the files behind it

The Toolbox's **Maps** app edits a game's sessions as maps: which islands lie where, the random slots that the map
generator fills, the start positions, the map's size and its playable area. It can also grow the whole map on any of
its four sides, derive three difficulties from one map, make mirrored copies, set mine slots and fertilities, and move
the island complexes of the Aegyptus map as one. It writes what it changes as a mod that the game reads (Anno 117 and
Anno 1800), or as the game's own world file or random-map template (Anno 2070, 1404 and 1701). The code is
`src/annotoolbox/mapedit/` (its `__init__` lists the modules). **Nothing it writes has been started in a game
yet.** The list of what to try is at the end.

Contents: [what modders expect](#what-modders-expect) · [the files per game](#the-files-per-game) ·
[how a map is loaded and modded](#how-a-map-is-loaded-and-modded) · [the window](#the-window) ·
[what the data settles](#what-the-vanilla-data-settles) · [the checks](#the-checks) · [writing it](#writing-it) ·
[growing a map](#growing-a-map-on-any-side) ·
[where absolute positions are kept](#where-117-and-1800-keep-absolute-session-positions) ·
[three difficulties](#three-difficulties-from-one-map) · [mirrored copies](#mirrored-copies) ·
[mine slots and fertilities](#mine-slots-and-fertilities) · [island complexes](#island-complexes) ·
[what is built](#what-is-built) · [to try in the game](#to-try-in-the-game)

## What modders expect

Two community tools edit map templates. Their behaviour was read from their READMEs (2026-11-13). Nothing of their
code or pictures is used here.

| | TAMPER (Taludas Anno 117 Map Template Editor) | Community Map Editor (anno-mods `AnnoMapEditor`) |
|---|---|---|
| Game | Anno 117 (Latium and Albion; the DLC's enlarged Latium; the Aegyptus map) | Anno 1800 (117 announced as under way) |
| Licence | MIT | MIT (Jakob Harder, 2022) |
| Opens | templates extracted from the game's archives (RdaConsole), `.a7tinfo` through FileDBReader, its own XML | templates from the game's archives (found automatically), `.a7tinfo`, FileDBReader's XML |
| Edits | new maps (region, difficulty, size in 64-tile steps up to 8192, border distance, offset), the playable area (and the enlarged map's initial one), random slots by size and type from a palette, fixed islands picked from `assets.xml` with `MapFilePath`, `IslandLabel`, `Rotation90`, type and `FertilityGuids`, ship spawns; drag, arrow keys, multi-select, group rotation, mirror copies, undo | move, add, remove islands; change a pool island's type; mine slots and fertilities; undo (not for size changes) |
| Size | grows existing maps only, on the north and east edges | resize on the roadmap |
| Checks | overlapping boxes, an XL slot needs 64 more tiles from the playable area's border, islands outside the playable area or half in the border, no spawn (blocks the export), fewer than 4 starters (blocks), more slots of a kind than the pool has islands | too many small, medium or large pool slots (the game uses each pool island once, extras are dropped), more than one continental island (drawing glitches), third-party and pirate slots may become small islands |
| Writes | the `.a7tinfo`, its XML, a PNG of the map, a mod: three difficulties per region (easy, medium, hard derived by shrinking islands), the `.a7t` and `.a7te` as shipped, `assets.xml` with the `MapTemplate` assets and horizon islands, `modinfo.json`, texts | `.a7tinfo`, XML, a ready-to-play mod for the Old World's sandbox |

What a modder expects from such a tool, taken from both: open the game's own templates without extracting by hand;
see the islands as they look and the slots by kind; place, move, rotate and remove; a playable area and a border that
are visible; the game's own size classes and pool limits checked before the export, with the rule named; a mod that
loads without hand work; undo everywhere. What neither does: grow a map on the south or west side (it moves every
position), or open the older games' worlds.

## The files per game

### Anno 117 and Anno 1800: map templates

A session's map is a **map template**, three files side by side (read from the 51 templates of 117 and the 93 of 1800,
2026-11-13; FileDB is [`formats/filedb.md`](formats/filedb.md)):

| File | What | Layout |
|---|---|---|
| `<name>.a7tinfo` | the template: size, playable areas, the elements | FileDB (117: v3; 1800: v1 75, v2 1, v3 17) |
| `<name>.a7t` | the empty session the template starts from: terrain, grids, camera, objects | an RDA V2.2 archive with one `gamedata.data` (FileDB); 1.5 MB for an empty 2048-tile frame, 70 to 134 MB for 117's templates (two height maps of 4097 x 4097) |
| `<name>.a7te` | the editor's level header | XML text: `Dimensions` (X, Z), `ChunkSize` (64), one `Chunk` per 64-tile chunk; 1800 ships one with every template, 117 with 50 of 51 |

The `.a7tinfo`'s root `MapTemplate`:

| Field | What |
|---|---|
| `Size` | two int32, the map's edge in tiles (117: 2048, 2688 the DLC's enlarged maps, 2560 Aegyptus; 1800: 1408 to 2560) |
| `PlayableArea` | four int32: x0, z0, x1, z1 in tiles; outside it the camera and ships stop (the margin: 117 20/28 tiles on the pool maps, 10 on the campaign's; 1800 100 to 434) |
| `InitialPlayableArea` | the same, the area before an enlargement (117's enlarged maps, 1800's 13 enlarged maps); zero when unused |
| `EnlargementOffset`, `IslandShift`, `IsEnlargedTemplate` | 117: where the base map lies inside the enlarged one (always 0, 0 in vanilla), a shift (always 0), the enlarged map's mark |
| `RandomlyPlacedThirdParties` | a list (empty in every vanilla template but one of 1800's) |
| `ElementCount` | the number of `TemplateElement`s |
| `TemplateElement` | one per element: `ElementType` (absent 0, 1, 2) and `Element` |

The three kinds of element:

| `ElementType` | Kind | `Element` holds | Vanilla 117 / 1800 |
|---|---|---|---|
| absent (0) | a **fixed island**: one island file at one place | `Position` (two int32, the grid's corner, on 8 tiles), `MapFilePath` (UTF-16), `Rotation90` (0..3), `IslandLabel`, `FertilityGuids`, `RandomizeFertilities`, `MineSlotActivation` (117) / `MineSlotMapping` (1800), `RandomIslandConfig/value/Type/id` and `Difficulty/id`; 117 also `FertilitiesPerAreaIndex`, `FertilitySetGUIDs`, `IslandSize` | 120 / 176 |
| 1 | a **random slot**: the map generator puts a pool island there | `Position`, `Size` (absent Small, 1 Medium, 2 Large, 3 XL), `Difficulty`, `Config/Type/id` (absent Normal), 117 `Locked` | 1,024 / 1,498 |
| 2 | a **start position**: where a player's ships start | `Position` only (117 `Locked`) | 87 / 234 |

Island types (`IslandType`, the same ids in both games, [`formats/enums-117.md`](formats/enums-117.md)): 0 Normal,
1 Starter, 2 Decoration, 3 Third party, 4 Pirate, 5 Cliff island, 6 Reserved for a player, 7 Volcanic (117). The
size classes and their largest island come from the game's `MapGenerator` asset (2002538): 117 Small 256, Medium
320, Large and XL 432, Continental 2048; 1800 Small 192, Medium 272, Large 384. The pool is the `RandomIsland` assets
(117: 74, 1800: 187): `FilePath`, `IslandRegion`, `IslandDifficulty`, `IslandType`, 117's `IslandPlaceholderSize`
(an untagged island counts as Small). A placed island's own size is its `.a7minfo`'s `MapSize`; its pictures are the
`_gamedata/<name>/` PNGs beside the `.a7m` (`mapimage.png`, `gamemapimage.png`, `activemapimage.png`, 117's
`provincemapimage.png`).

Third parties and pirates are islands of their type (a fixed island with its file, or a slot of type 3 or 4); the
characters who own them are the `MapTemplate` asset's `FixedPreplacedParticipants` (117) or the session's setup
(1800). The `MapTemplate` asset ties it together: `TemplateFilename` (the `.a7t`; the other two are found beside
it), `EnlargedTemplateFilename` with `EnlargeDLC`, `IsUsedByMapGenerator`, `MapTemplateType` (117) or
`TemplateMapType` (1800), `TemplateRegion`, `TemplateSize`, `IslandSize`, and in 117 the `HorizonIslands` (models on
the horizon, each at a world `Position` X, Y, Z: outside the map).

### Anno 2205: sector maps

A 2205 session is one baked map (`<name>.a6m`, [`formats/anno2205-map-format.md`](formats/anno2205-map-format.md)):
the islands are part of its terrain, not placed by a template, and the session asset lists its areas by name. There
is nothing to place, so the Maps app shows 2205 sessions read only (the session's picture, its areas) and the
Islands app edits them.

### Anno 2070, 1404 and 1701: world files

A map is a world file (`.www`, the tagged text of [`formats/anno1404-world-format.md`](formats/anno1404-world-format.md)
and [`formats/anno1701-world-format.md`](formats/anno1701-world-format.md), read and written byte for byte by
`isd2070`). Root `WorldConfig`:

| Game | Size | Islands | Start places |
|---|---|---|---|
| 2070 | `Width`, `Height` | `Islands`: `v` / `IslandConfig` with `Name`, `Filename`, `PositionX`, `PositionZ` (the top-left corner), `Width`, `Height`, `Direction` (0..3), `Type` (0 normal, 1 underwater build, 2 starter, 3 third party, 5 decoration) | `ArkSlots` (`x`, `y`) |
| 1404 | `Width`, `Height` | the same `IslandConfig` (types 0 regular, 1 main, 2 start harbour, 3 corsair, 4 decoration, 5 Venetian, 6 volcano) | none in the world; the setup places the players |
| 1701 | `m_Width`, `m_Height` | `IslandConfig` with `m_Name`, `m_GOPFilename`, `m_XPosition`, `m_ZPosition`, `m_Width`, `m_Height`, `m_IslandDirection` | `PlayerPositions` (`x`, `y`) |

The playable area is the whole world (no margin field); the island's picture is `<island>.png` or
`<island>_minimap.png` beside its `.isd`. A world entry's `Width` and `Height` are its island's (every vanilla entry
is square); an island added or swapped takes the file's own `Width`.

### Anno 2070, 1404 and 1701: random-map templates

The random maps of these games are `.tww` files (the same tagged text, read and written byte for byte: 54 of 2070, 72
of 1404, 170 of 1701, read 2026-11-13). Root `RandomMapTemplate`: `Name`, `Width`, `Height`, `IslandTemplates`
(`k` / `v` / `Island`), `ResourceProfiles`, `PlayerPositions` and `PlayerPositionsEasymode` (`i` with `x`, `y`; empty
in 2070 and 1404, four places in 108 of 1701's), 2070's and 1404's `WorldLightProfiles`, 1701's `Islands` (empty) and
`HIDObjectConfigs`. An `Island` is a placeholder the game fills with an island of its size and climate:

| Field | What | Values |
|---|---|---|
| `Rect` | `left`, `top`, `right`, `bottom` in tiles; `top` is the smaller z | always square; edges 2070: 96, 128, 144, 160, 208, 256; 1404: 112, 160, 208; 1701: 64, 96, 128, 160 |
| `Type` | 2070 and 1404 | 2070: 0 to 4, 1404: 0, 1, 2, 3, 5 (the world entries' types: 2 a start island) |
| `Climes` | the climates the island may come from, bits | 1, 2 (1701 also 3; eight 1701 islands write `Clime`) |
| `Cultures`, `Fertilities`, `Resources` | 1701 only, bits | edited as numbers; a new placeholder takes the ones of the entry it copies |

The Maps app shows each placeholder as a random slot whose size is one of the game's edges; the panel edits its edge,
type and climate bits, and for 1701 its culture, fertility and resource bits (what each bit stands for was not read). Start places are 1701's `PlayerPositions` (the easy mode's list is kept as read).

## How a map is loaded and modded

- **117 and 1800.** A new game takes a session's `MapTemplate`: a random one of those with `IsUsedByMapGenerator` of
  the session's region, size and type, or the one a session names. The generator puts the fixed islands where the
  template says, fills each random slot with a pool island of its size and type that is not used yet, and starts the
  ships at the start positions. A mod can change a map in two ways, both read by the mod loader
  (`engine-modloader.md`). **A file at the vanilla path** replaces the template for every new
  game (TAMPER's and the Community Map Editor's way). **A new path plus a `MapTemplate` asset** added by ModOps
  adds a map beside the vanilla ones. Savegames keep their islands; only a new game reads the template.
- **2070, 1404 and 1701.** The world file is read when the scenario or map starts. There is no mod loader. A changed
  world goes into an archive that wins over the game's own (the suite's project build does it: [`suite.md`](suite.md),
  "Build and install").

## The window

Start it with `python -m annotoolbox --app maps`, or press the **Maps** button of the suite.

- **File > Open game** lists the games of the settings. The left list holds the game's maps (117 and 1800 templates,
  the older games' worlds and random-map templates, 2205's sessions), with a filter. A map opens off the window's
  thread, and its islands' grids and pictures are read there too. An island that is added or swapped is read there
  before it is shown, and growing, keeping and writing run off the window's thread as well. A 2205 session is handed
  to the Islands app when it runs beside this one.
- **The view, from above**, shows:
  - the sea and the map's edge
  - the margin outside the playable area, shaded, and the initial playable area, dashed
  - each fixed island as its map picture, turned by its `Rotation90`, inside its grid's outline
  - each random slot as a square of its size class, coloured by its type, with its size letter in it
  - start positions as rings

  The wheel zooms, the right button pans, and Home fits the map. North is up: x grows to the right (east) and z
  upwards (north), in the same way TAMPER shows the 117 maps (the origin is in the south-west corner). A quarter turn
  (`Rotation90` 1) turns an island counter-clockwise seen from above, as the game does
  ([below](#what-the-vanilla-data-settles)).
- **Select** with a click (Shift adds; a click on an island of a [complex](#island-complexes) selects the complex, Alt
  only the island). **Move** by dragging (snapped to 8 tiles). **Rotate** with R (one fixed island in place, a
  selection of several as a group about its centre). **Remove** with Delete, **duplicate** with Ctrl+D, and nudge with
  the arrow keys (8 tiles). Ctrl+Z / Ctrl+Y undo and redo every edit, the growth included. **Map > Open the island in
  the Islands app** opens the selected fixed island there.
- **Add**: a random slot (size and type), a start position, a fixed island, or a complex of another map of the game. A
  fixed island comes from the game's pool (its `RandomIsland` assets), from any island file of the game, or from a
  file on disk (an island the Islands app built into the project).
- **The panel** on the right edits the selection and the map. For the selection: position, size, type, `Rotation90`,
  `MapFilePath`, `IslandLabel`, `FertilityGuids` as GUIDs and `RandomizeFertilities`; for a random-map template's
  placeholder: its edge, type and climate bits. For the map: size, playable area and initial playable area. A larger
  size grows the map on the east and north, and a map never shrinks. **Map > Grow the map...** grows it on any side.
  **Map > Mirrored copies...**, **Edit > Mine slots and fertilities...** and **File > Write easy, medium and
  hard...** (the three difficulties) are described below as well.
- **Checks** lists the findings of the rules below, each with its element. A double click selects it.

## What the vanilla data settles

The points below were checked against the games' own files (the probes read the installed games; nothing of them is
kept).

- **The sense of a quarter turn.** 117's campaign templates and many of 1800's sessions carry their fixed islands'
  terrain baked into the session's height map. Each fixed island's own height map, turned every way, was compared with
  the session's at its `Position` (correlation over the grid): unturned islands match unturned (13 in 117, 22 in 1800),
  and `Rotation90` r always matches r turns of (u, v) -> (E - v, u) on the island's tiles (E the grid's edge): 117 one
  island at 3, one at 2; 1800 eight at 1, six at 2, eight at 3, none against it. Seen from above with x east and z
  north, that is **counter-clockwise**. The picture and the active area for the overlap check are both turned that
  way. An island's map picture (`mapimage.png`) holds its north row first (57 of 60 of 117's islands; the other three
  are near-symmetric), so it is drawn unflipped.
- **Camera flights, channels 0 and 2.** Of the seven channels of a `Cam` or `Asset` track, 0 and 2 span the map, 1 is a
  small height (3 to 55), 3 lies near 0.8 to 1.3, 4 and 5 are angles (300 to 360 and 0 to 360 degrees), 6 is empty. On
  the Aegyptus map the object tracks' first keyframes sit on the session's objects read as (channel 0, channel 2) =
  (x, z) to within 0.1 to 2 tiles, against more than 200 tiles read the other way round. So 0 is x and 2 is z, and only
  those two move when a map grows; the angles need nothing.
- **Grids are rows of z.** Read as rows of z, the session height map of 117's Celtic campaign matches each unturned
  island's own heights at its `Position` with a correlation of 0.59 to 0.86 (by magnitude: the two count heights the
  opposite way); read as rows of x below 0.55, but for one near-symmetric island. The bit grids (`Water`, rivers,
  fords) are empty in every 117 template and set in two of 1800's only; there more of the set bits fall inside the
  fixed islands' grids read as rows of z than as rows of x (0.8 % against 1.6 % outside), which agrees but does not
  settle it alone. They share the height map's layout in the code.
- **2070's `Direction` turns counter-clockwise too** ([below](#2070s-direction)): a world entry's `Direction` is
  `Rotation90` unchanged, and `world/sectors2070.to_world` turns the island that way.

Note: the first three points were checked on 2026-11-13, and 2070's `Direction` was settled on 2026-11-14. Until then
`to_world` took a quarter turn as clockwise.

### 2070's Direction

A 2070 world (`.www`) has a picture beside it (`.png`): the map turned 45 degrees about its centre, scaled so that a
tile is a pixel along each diagonal (pixel x = x + z - W/2, pixel y = x - z + W/2, W the map's size). The frame was
found on the first map by trying the turns, flips and scales against its unturned islands (only this one fits; the
unturned islands of all 54 world files agree with it at a mean correlation of 0.77 over 353 islands). Each island's own
heights, carried to the map by its `Direction` in each sense and compared with the picture's land: islands at 2 (the
same in both senses) agree 0.82, islands at 1 and 3 agree 0.78 and 0.81 turned counter-clockwise, 0.47 and 0.52 turned
clockwise; of 290 islands at 1 or 3, 287 agree better counter-clockwise (135 of 137 at 1, 152 of 153 at 3). So
`Direction` d is d turns of (u, v) -> (H - v, u) on the island's tiles, as 117's `Rotation90` is. The installed-game
test is `tests/test_maps2070.py` (`ANNO2070_DIR`).

**What the counter-clockwise sense changes, per map** (for the in-game list; read from the installed 2070 on
2026-11-14, every fixed map of `maps2070.MAPS` with a world file). An island at `Direction` 1 or 3 stands differently
than it did with the clockwise reading. The two senses differ by a half turn of the island in its own box, so its
corner and box stay and its land turns over. Islands at 0 and 2 stand as before. "Tiles that change" counts the
island's tiles whose land and sea swap under that half turn (its land against its land turned 180 degrees; an
underwater island's land is its plateau). The table names the largest on each map, which is the island to look at
first. Surface and underwater islands are counted alike. 573 of the 1,488 placements on the 53 maps turned.

| Map | Islands | Placed differently | Most visible (tiles that change) |
| --- | --- | --- | --- |
| `singleplayer_01` | 29 | 11 | `n_l07` (Direction 1, 10,434 tiles) |
| `singleplayer_02` | 27 | 9 | `n_l17` (Direction 3, 9,742 tiles) |
| `singleplayer_03` | 56 | 28 | `n_m04` (Direction 1, 11,474 tiles) |
| `singleplayer_04` | 21 | 9 | `n_s05` (Direction 1, 4,906 tiles) |
| `singleplayer_06` | 38 | 23 | `n_l06` (Direction 3, 13,752 tiles) |
| `singleplayer_07` | 14 | 7 | `n_m02` (Direction 3, 8,608 tiles) |
| `singleplayer_08` | 22 | 8 | `n_m05` (Direction 3, 9,376 tiles) |
| `multiplayer_01` | 29 | 11 | `n_l07` (Direction 1, 10,434 tiles) |
| `multiplayer_02` | 38 | 9 | `n_m05` (Direction 1, 9,376 tiles) |
| `multiplayer_03` | 16 | 4 | `n_l31` (Direction 1, 8,086 tiles) |
| `multiplayer_05` | 35 | 11 | `n_l25` (Direction 1, 8,420 tiles) |
| `dlc01_scenario01` | 28 | 16 | `n_m05n` (Direction 3, 9,376 tiles) |
| `dlc01_scenario02` | 29 | 16 | `n_m04` (Direction 3, 11,474 tiles) |
| `dlc02_scenario01` | 40 | 15 | `n_m04` (Direction 1, 11,474 tiles) |
| `dlc02_scenario02` | 34 | 13 | `u_d07` (Direction 1, 8,392 tiles) |
| `addon_scenario01` | 42 | 27 | `n_l01` (Direction 3, 10,242 tiles) |
| `addon_scenario02` | 42 | 20 | `n_l09` (Direction 1, 9,318 tiles) |
| `addon_scenario03` | 47 | 18 | `n_l10` (Direction 1, 23,854 tiles) |
| `campaign_chapter01` | 21 | 11 | `sinking_island_m01` (Direction 1, 14,930 tiles) |
| `campaign_chapter02` | 32 | 10 | `n_m06` (Direction 3, 7,026 tiles) |
| `campaign_chapter03` | 45 | 26 | `chap3_n_l09` (Direction 1, 9,228 tiles) |
| `deep_ocean_mission01` | 33 | 10 | `n_s05` (Direction 3, 4,906 tiles) |
| `deep_ocean_mission02` | 28 | 16 | `n_l04` (Direction 3, 9,430 tiles) |
| `deep_ocean_mission03` | 36 | 24 | `n_m13` (Direction 3, 5,942 tiles) |
| `deep_ocean_mission04` | 28 | 15 | `n_m05` (Direction 3, 9,376 tiles) |
| `deep_ocean_mission05` | 43 | 22 | `u_d07` (Direction 1, 8,392 tiles) |
| `world_event01_world01` | 14 | 6 | `u_l06` (Direction 3, 3,662 tiles) |
| `world_event01_world02` | 26 | 8 | `u_m02` (Direction 1, 1,838 tiles) |
| `world_event01_world03` | 35 | 5 | `n_d05` (Direction 1, 2,284 tiles) |
| `world_event02_world01` | 10 | 5 | `we2_n_s01_positive` (Direction 1, 2,820 tiles) |
| `world_event02_world02` | 21 | 6 | `we2_n_l34_positive` (Direction 3, 6,494 tiles) |
| `world_event02_world03` | 25 | 12 | `we2_n_l10_positive` (Direction 1, 23,854 tiles) |
| `world_event03_world01` | 19 | 5 | `n_s03` (Direction 1, 3,862 tiles) |
| `world_event03_world02` | 30 | 8 | `n_m07` (Direction 3, 8,682 tiles) |
| `world_event03_world03` | 28 | 8 | `n_l21` (Direction 3, 11,310 tiles) |
| `world_event04_world01` | 20 | 11 | `n_l20_we4_tycoon` (Direction 3, 15,208 tiles) |
| `world_event04_world02` | 26 | 11 | `n_l12_we4m2player` (Direction 3, 18,734 tiles) |
| `world_event04_world03` | 29 | 10 | `n_l20_we4_tycoon` (Direction 3, 15,208 tiles) |
| `world_event05_world01` | 19 | 4 | `n_s01` (Direction 3, 2,810 tiles) |
| `world_event05_world02` | 17 | 4 | `u_d14` (Direction 3, 2,298 tiles) |
| `world_event05_world03` | 16 | 3 | `n_m04` (Direction 3, 11,474 tiles) |
| `singleplayer_sudoku` | 1 | 0 | - |
| `testlevel_assetoverviewms07` | 33 | 6 | `n_l26` (Direction 3, 18,792 tiles) |
| `testlevel_assetoverviewms08` | 34 | 7 | `n_l03` (Direction 3, 16,576 tiles) |
| `testlevel_assetoverviewms09` | 34 | 2 | `n_s09` (Direction 3, 1,460 tiles) |
| `testlevel_assetoverviewms10` | 34 | 9 | `n_l10` (Direction 1, 23,854 tiles) |
| `testlevel_assetoverviewms11` | 40 | 10 | `n_l10` (Direction 3, 23,854 tiles) |
| `testlevel_assetoverviewms12` | 40 | 4 | `n_m04` (Direction 3, 11,474 tiles) |
| `testlevel_chapter01` | 16 | 8 | `sinking_island_m01` (Direction 1, 14,930 tiles) |
| `testlevel_mergedpress` | 31 | 17 | `pressversion_n_l20_harbor` (Direction 3, 15,462 tiles) |
| `testlevel_pressversion_march2011` | 16 | 7 | `pressversion_n_l20_harbor` (Direction 3, 15,462 tiles) |
| `testlevel_underwater_world` | 11 | 5 | `underwaterworld_u_x03` (Direction 3, 2,608 tiles) |
| `testlevel_benchmark_1` | 10 | 3 | `n_d05` (Direction 1, 2,284 tiles) |

## The checks

| Rule | Severity | Games |
|---|---|---|
| two islands' land overlaps (a fixed island: the `ActiveMapRect` of its `.a7minfo`, turned with it; a slot: the square of its size class's largest island); a continental island's box may hold others, as the DLC's maps do, and the islands of one [complex](#island-complexes) meet by design | warning | all |
| an island or start position whose centre lies outside the playable area (half in the border, the community's line) | warning | all |
| a start position inside a random slot's square (a fixed island's bay may hold one: vanilla starts lie in their island's active area) | warning | all |
| more than one continental island (a fixed island whose file the game names continental: 117's DLC land mass, 1800's New World continent; a Continental slot) | warning | 117, 1800 |
| more Normal or Volcanic random slots of a size than the region's pool holds islands of that size (starters, third parties and pirates are filled otherwise) | warning | 117, 1800 |
| no start position | error | 117, 1800 |
| a fixed island's file not in the game or the project | error | all |
| the playable area outside the map | error | 117, 1800 |
| the map not square | warning | 117, 1800 |

The game itself checks none of this before it loads a map. An error means that the map will not work as it reads, and
a warning means that the generator or the drawing may not do what the map shows. Run over the vanilla maps
(2026-11-13), the checks name a few dense pool templates (8 to 10 Normal slots of a size against 7 pool islands) and
the Gamescom template's press island (a file the game no longer ships), and nothing else. Fixed islands that overlap as a map ships
them (the Aegyptus map's: an island owns the cells it marks active, `island-maps.md` "How Anno 117 handles the same
needs"; 1800's story and battle-site islands) are complexes with designed seams, named only once an edit moves one
against another. The 117 playable areas are not all on 4 tiles (1800's 203 / 195 margins), so that is no rule.

## Writing it

**Save to the project** (Ctrl+S) keeps the changed files in the open project, under `map-edits/<game>/<game path>`.
The project's **Build mod** puts them into the one mod (117, 1800) or the archives (the older games), together with
every other app's work. **File > Write as a mod folder...** writes a stand-alone mod.

- **Replace the vanilla map** (the default): the edited `.a7tinfo` at the template's own path, the `.a7t` and `.a7te`
  only when the size changed (they are the game's otherwise).
- **As a new map**: the three files under `data/mods/<mod id>/maps/<name>/`, and a ModOps file that adds a
  `MapTemplate` asset after the vanilla asset (a copy of the vanilla one with the new `TemplateFilename` and a GUID
  from the project's range). The GUID offered is the highest free one of the range (the islands' numbering and the
  asset editor's new assets take the lowest). A GUID outside the range, or one that another new map or new asset of
  the project holds, is refused. A new map made from an enlarged template takes the enlarged map's horizon islands as
  its own.
- **The older games**: the world file or random-map template at its own path.

An unchanged template writes back byte for byte (the installed-game test checks every template of 117 and 1800).

## Growing a map on any side

**Map > Grow the map...** takes four numbers of tiles (west, south, east and north), each a multiple of 64 (the
`.a7te`'s chunk). Growing west or south moves every absolute position by that amount. The reason for this feature:
community maps cut the margins to fit more islands, because growing south or west breaks quests, the origin being the
south-west corner. The Maps app moves every reference it knows consistently, and lists each one it moved and each one
it cannot move, so nothing breaks silently. This is for new games only: a savegame keeps its own frame.

What it changes, by file:

- **`.a7tinfo`**: `Size`; `PlayableArea` and `InitialPlayableArea` moved and, with "grow the playable area too" (the
  default), widened by the amounts; every element's `Position`.
- **`.a7t`**: the session's size, every grid moved and padded (below), every object, camera and camera sequence moved.
- **`.a7te`**: the new dimensions and chunk list.
- **`MapTemplate` asset** (117): each horizon island's `Position` X and Z moved (a ModOps file); 1800 has none.
- **Not changed, listed**: the island files (their coordinates are their own), Lua files that hold numbers in the
  map's range near the template's name (literal positions a script may use), values only the program holds (the
  camera's start, the generator's own spacing), the enlarged map's `EnlargementOffset` and `IslandShift` (kept; their
  meaning past 0 is not known), and any part of the session file the scan does not know.

The report (shown in the window; kept in the project as `map-edits/reports/<name>.grow.json` and written beside a
mod folder as `<mod id>.grow.json`, never inside the mod) lists every reference: the file, the place, `moved` with the
old and new value or `kept` with the reason.

## Where 117 and 1800 keep absolute session positions

The inventory the growth works from: every manager of every vanilla template's session file (117: 51, 1800: 93),
read 2026-11-13, with what it holds and what the growth does. Units are tiles; the height maps have two samples per
tile.

| Where | What | 117 | 1800 | Growth |
|---|---|---|---|---|
| `.a7tinfo` `Size`, `PlayableArea`, `InitialPlayableArea`, element `Position` | the template | all | all | moved |
| `SessionSettings/PlayableArea` | the session's own copy of the playable area | all | all | moved |
| `TerrainManager` `WorldSize`, `HeightMap`, `DisplacementHeightMap` (117) | the size; heights, 2 x size + 1 samples of uint16 | all | all | moved, the new strip copies the edge |
| `WorldManager` `Water`, `RiverGrid`, `FordGrid` | bit grids, one bit per tile, rows of 32-bit words | all | all | moved; water copies the edge, rivers and fords empty |
| `WorldManager/EnvironmentGrid` | one byte per 4 x 4 tiles | all | all | moved, edge copied |
| `WorldManager/StreetMap` (`StreetID` dense, `VarMapData` sparse) | streets | - | all | moved, empty |
| `AreaIDs` | the area of each tile: 117 sparse 16-tile blocks, 1800 dense uint16 or sparse | all | all | moved, the new strip the sea's area |
| `IrrigationManager/m_StaticTileGrid` | sparse tile grid | all | 20 | moved (blocks shifted) |
| `SpawnAreaPoints` | one grid per spawn kind, `m_XSize` / `m_YSize` the map | - | all | the size grown; a non-empty point grid is listed, not moved |
| `ObjectSpawnManager/StaticSpawnAreaGrids` | spawn areas | all, empty | - | listed when not empty |
| `AreaManagerData` | the objects of the session (a nested FileDB): `Position` x, y, z of each object, quest objects among them | all (the campaign's 2) | all (none) | every `Position` moved; other position-like fields listed |
| `CameraManager` (117) / `SessionCameraManager` (1800) `EditorSavedCameraStates/View/From` | the editor's ten saved cameras | all | all | moved |
| `CameraSequenceManager` `Cam` and `Asset` `AllKeyframes` | camera flights: seven channels of keyframes; channels 0 and 2 hold x and z ([settled](#what-the-vanilla-data-settles)), the others a height, a lens value and angles | 7 | 10 | channels 0 and 2 moved |
| `GameObjectManager/GameObjectLabelMap`, `SelectionManager` | labels and ids of objects | 1 | 9 | nothing to move |
| `WeatherManager`, `SeasonManager`, `VolcanoEruptionManager`, `SessionUnitManager`, `ItemSessionManager` | times, wind, states | yes | yes | nothing to move |
| `MinimapManager`, `IslandManager`, `SessionParticipantManager`, `DiscoveryManager`, `PathManager` and the rest | empty in every template | | | listed when not empty |
| `MapTemplate` asset `HorizonIslands/Item/Position`, `EnlargedHorizonIslands/...` | horizon models, world X, Y, Z; a value of 0 may be left out (one vanilla `Position` holds only `Z`) | 32 assets, 448 positions; the enlarged maps' list 16 assets, 272 | none | the list of the grown template moved (ModOps): the enlarged map's for an enlarged template, the base list kept and listed then (the asset serves both) |
| other assets | the asset lists hold no other X/Z position block (scanned for `<X>`..`<Z>` blocks and position-named values) | none | none | - |
| Lua scripts | 117: 2,530 files, 1800: 31; a number in a script is not known to be a position | | | listed: numbers in the map's range in scripts that name the template or session |

## Three difficulties from one map

A 117 layout ships as three templates, `<layout>_easy`, `_medium` and `_hard`, each with its own `MapTemplate` asset
whose `IslandSize` (Large, Medium, Small) is what the game setup's island-size choice picks. Compared over the ten
layouts of 117 (2026-11-13): the three keep the same number of elements, the same start positions and the same third
parties, pirates and starters; what changes is the size of the Normal slots the pool fills. From medium to easy two or
so Small slots become Medium and two Medium become Large (chain: Small, Medium, Large 6, 4, 2 -> 4, 4, 4); from medium
to hard the Large ones become Medium and two Medium become Small (6, 4, 2 -> 8, 4, 0); the Roman hard maps also make
the XL starter slots Large. Vanilla re-lays the slots for each level; the derived maps keep every slot where it is.

**File > Write easy, medium and hard...** takes the open map as the medium one and derives the other two by that rule
(`variants.py`): `STEPS` (2) Normal slots one size up for easy, the ones with the most free sea around them first; for
hard the Large ones one size down, then `STEPS` Medium ones, the most crowded first, and the XL starters Large. The
Volcanic slots of the DLC's layouts keep their size (they do in all three vanilla levels). A resized slot keeps its centre. The three are written as three new maps (`<name>_easy`,
`_medium`, `_hard`, three GUIDs, the assets' `IslandSize` Large, Medium, Small, used by the generator), or, when the
open map is a vanilla `_medium` one and was not grown, over its two vanilla siblings. The checks run on each; what
they find is listed before anything is written.

## Mirrored copies

**Map > Mirrored copies...** copies the selection (or turns it in place) across the playable area's centre: across
the north-south line (x mirrored), across the east-west line (z mirrored), both (four copies: the quarters of a
four-player map), or through the centre (a half turn). A slot or a start position mirrors exactly; a fixed island
cannot be mirrored by the game (it only turns its grid), so its copy is turned two quarter turns, which keeps the
side that faced the line facing it. Positions snap to 8 tiles; an odd margin (20 against 28) shifts a copy by up to
4 tiles. The copies are selected after, so a check names any that overlap.

## Mine slots and fertilities

A fixed island's element holds, besides its fertilities (`FertilityGuids`, with `RandomizeFertilities`), three maps,
each a list of `#32768` attributes alternating key and value:

| Field | Game | Key | Value | Vanilla templates |
|---|---|---|---|---|
| `MineSlotMapping` | 1800 | an object `ID` of the island (8 bytes): its mine slot | the `Slot` asset the slot becomes (4 bytes; 0 random) | 31 of 176 islands; the keys are the island's objects (42 of 42 on `moderate_c_01`), the slots `random slot mining`, `clay`, `oil pump`, the values `Coal Mine Slot`, `Clay Pit Slot` and the like |
| `MineSlotActivation` | 117 | the same object `ID` | 1 active, 0 not (1 byte) | empty in all 120 |
| `FertilitySetGUIDs` | 117 | an area index (2 bytes) | a `FertilitySet` GUID (4 bytes) | empty in all |
| `FertilitiesPerAreaIndex` | 117 | an area index (2 bytes) | the fertility GUIDs of that area (4 bytes each) | empty in all |

117's layouts are read from its savegames, which keep the generated template (`GameSessionManager/MapTemplate`) with
these maps filled: area 1 (also 4) with one set and six or seven fertilities, a mine slot per `Slot` object of the
island (`Slot ... Random`) with 0 or 1. **Edit > Mine slots and fertilities...** (a selected fixed island) lists the
island's `Slot` objects (read from its `.a7m` off the window's thread) and the slots the element names, each with its
value (117's `MineSlotActivation`: not listed, active, inactive; 1800's `MineSlotMapping`, shown in the same column as 117's
form of the list: not listed, random, or a `Slot` asset of the game), and for 117 one row
per area (index, set GUID, fertility GUIDs); **The area's set...** and **Add a fertility...** pick one of the game's
`FertilitySet` or `Fertility` assets by name for the selected area, and the panel's `FertilityGuids` field has the same
picker (`...`). The element's maps are rewritten from the dialog; an element the dialog
did not touch keeps its bytes. Whether 117's generator keeps values a template sets, instead of dealing its own, is to
try in the game.

## Island complexes

The Aegyptus map (`egypt_province_default_01`) is not made of slots: 39 fixed islands tile the Nile valley, their
grids overlapping into four complexes of 10, 10, 10 and 9 islands (one per quarter; their active areas meet, so the
land runs through). A **complex** is a set of fixed islands whose grids overlap, joined transitively (`placing.complexes`).
A click on one island selects its complex; moving, turning (as a group about the complex's centre), duplicating and
removing then act on all of it, so its seams stay where they are. **Add > A complex of another map...** reads another
map of the game, lists its complexes and places the chosen one at the view's centre, its islands where they lie to
each other. The checks do not flag islands of one complex overlapping each other.

## What is built

Names: a fixed island's fields in the code and the panel are 117's tags: `Rotation90` (`rotation90`),
`MapFilePath` (`map_file_path`), `IslandLabel` (`island_label`), `FertilityGuids` (`fertility_guids`),
`RandomizeFertilities` (`randomize_fertilities`) and `MineSlotActivation` (`mine_slot_activation`, which holds 1800's
`MineSlotMapping` values for an 1800 map). The panel shows the tag as the label and says what it means in a tooltip. The
older games' world files map their rotation, file and name onto the same fields.

The data side: `mapedit/template.py` (the `.a7tinfo` model, byte for byte, with the mine slot and area maps),
`mapedit/levelfile.py` (the `.a7te`), `mapedit/sessionfile.py` (the `.a7t` and its growth), `mapedit/grow.py` (the
growth), `mapedit/pool.py` (the pool, size classes, pictures, `Slot` assets and an island's slot objects),
`mapedit/rules.py` (the checks), `mapedit/worlds.py` (the older games' world files and random-map templates),
`mapedit/placing.py` (turns, complexes, mirrored copies, group turns), `mapedit/variants.py` (the three
difficulties), `mapedit/mapdoc.py` (the open map and its undo stack), `mapedit/references.py` (the report),
`mapedit/mapmod.py` (the mod's files), `mapedit/projectpart.py`; the window: `mapedit/mapview.py`,
`mapedit/mappanel.py`, `mapedit/mapdialogs.py`, `mapedit/mapwindow.py`. The fixed 2070 maps and the random sector
write their `.a7tinfo` through `template` and their `.a7te` with `levelfile` (one writer each). Tests:
`tests/test_mapedit.py`, `tests/test_mapedit_extras.py` (synthetic), `tests/test_mapedit_installed.py` (the installed
games, with `ANNO117_DIR`, `ANNO1800_DIR`, `ANNO2070_DIR`, `ANNO1404_DIR`, `ANNO1701_DIR`).

Three things are left open:

- 2205 placements (none exist)
- 2070's `Direction` in the game. It is settled from the data ([above](#2070s-direction)); the maps' islands at 1 and
  3 in the table there are the ones to look at
- the meaning of each of the 1701 `.tww` islands' `Cultures`, `Fertilities` and `Resources` bits (they are edited as
  numbers)

## To try in the game

- An edited 117 template (moved and added slots, a new start position) written as a replacement: a new game on it
  shows the islands where the view put them.
- The same as a new map (`MapTemplate` asset): the setup offers it, a new game loads.
- A 117 and an 1800 map grown on the west and south by 256 tiles: the game loads it, the islands, the start
  positions and the playable area sit where the view shows, the terrain's edge has no seam, the horizon islands
  (117) stand where they stood relative to the map.
- A map with fixed positions and a quest played through after growing (117's campaign template, which carries quest
  objects in its session file): the quest's objects and camera flights are where they were relative to the islands.
- 2070 and 1404 worlds with a moved island, through the project's archive build.
- A 2070, 1404 and 1701 random-map template with a moved and a resized placeholder: a random map from it puts an
  island of that size there.
- The three difficulties of an edited 117 layout as new maps: the setup's island size picks easy, medium, hard.
- A four-player map made with mirrored copies: each player's quarter looks the same, the turned islands included.
- An 1800 island with a mine slot set to a Gold Ore Mine; a 117 island with one slot inactive and its area's
  fertility set chosen: the game keeps them instead of dealing its own.
- The Aegyptus map with one complex moved: its islands' land still runs through, no seam.
- 2070's fixed maps: an island at `Direction` 1 stands as in 2070 (counter-clockwise, as above; the grids move with `to_world`).
