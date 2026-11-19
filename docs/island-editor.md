# Island editor

A scripted island build cannot get every island right: a waterfall can float, or a cliff can stand in a river.
The editor lets you see an island in the same way the game draws it, and fix it by hand. It also lets people build
their own islands from scratch. It is a PC application (PySide6 for the window, moderngl for the 3D view), and its
layout follows Anno 1800's internal editor. It opens the islands of every locally installed Anno: converted 2070
islands and 117's own for Anno 117, 2070's islands for Anno 2070 itself, 1800's for Anno 1800 itself, and those of
1404, 1701 and 2205 to look at and annotate.

The hand edits are a file of their own per island. The island build applies them on top of every build, so a better
island build and a hand fix never overwrite each other.

The code is in `src/annotoolbox/editor/` (the island, the edit document, the modes), `editor/desktop/` (the
window) and `othergames/` (the other games' installs and islands). The module map is the layout table of
[`README.md`](../README.md).

## The parts

This file keeps the status, how to run the editor, the layout, the islands and modes, and the history. The rest is in
the parts:

| Document | What |
|---|---|
| [`island-editor-view.md`](island-editor-view.md) | The 3D view, particle effects, GPU load, the rendering rules followed, the minimap |
| [`island-editor-tools.md`](island-editor-tools.md) | The tools, the checks, the edit file, the road pieces |

## Full spec

"A full island editor" means here: every piece of island data that the supported games carry and read, every
operation a mapper needs on it, and the file operations from a new island to a mod that the game loads. Each row says
**exists** (and where), **built** (added since the first count), **partial** (what is missing) or **missing** (with an
estimate of the work in hours where it was not built). The island build for another game is an optional part
([Islands and modes](#islands-and-modes)); nothing below depends on it unless the row says so.

The table has 68 rows. For the placed objects, the remaining open work needs the game (the rules in game). A prop's
tint in the view and the preview in 1800's look wait on the renderer; every other open row is data or window work.

Note: the table was first counted on 2026-11-07 (64 rows: 27 exist, 10 built, 10 partial, 17 missing) and last
recounted on 2026-11-09 (27 exist, 21 built, 9 partial, 10 missing). The work area path was built on 2026-11-10.

### Island data: Anno 117, 1800 and 2205 islands

| Data the game reads | Status | Where / what is open |
|---|---|---|
| Terrain heights (`TerrainManager/HeightMap`, rd3d `CoarseHeightMap`) | exists (117, 1800); built (2205) | `groundedit`; 2205: `editor/formats/a7m/sessionground` writes the coarse map, the game's own heights, the tile heights, the land bits, the sea depth byte and the stored mesh levels (formats/anno2205-map-format "Writing a map"); its normal-map pages are rewritten by the slope of the edit (`editor/formats/a7m/sessionpages`, 2026-11-13) |
| Height pages (`normalmaps/<level>/*.ctt`) | exists | re-encoded from the edited field (`terrainbuild.encode_page`) |
| Stored meshes of the coarse levels, node bounds (`meshes/<level>/*.tmc`, `MinY` / `MaxY`) | exists | raised in place; a hill between the vertices of a coarse level is flattened there |
| Coarse displacement, tint pages, grit map | missing | kept as the island has them; the tint brush is designed, not built (est. 4 h, "Slots, herd areas, map pictures and session settings": Designed, not built) |
| Ground paint: material IDs and alphas (117's four levels, rd3d `MaterialIDs2` / `MaterialAlphas2`) | exists | `groundedit`, `islandground.paint_strokes`; used-material masks set |
| The material set and its draw order | partial | a vanilla island paints only its own set (est. 4 h: an own set as built islands get; designed in "Designed, not built") |
| Land and sea bits (`WorldManager/Water`) | exists | follow a height edit |
| Water maps (`WaterInfos` depth byte; the coast wave byte, i.e. the surf; `WaterFlow`) | partial | the depth byte follows an edit; the coast waves are built 2026-11-08 (`cellbrush`, `islands/surfmap`: Terrain > Surf, Waves and Calm, key U); the flow map is kept (est. 3 h: a flow brush, the vector scale is not settled) |
| Rivers (the `River` editor objects: points, widths, handles) | exists | `groundedit.water_network`, `islandwater.fill_spline` |
| The gameplay river grid (`RiverGrid`), the ground under a moved river | missing; the rule is not found | kept; shape the bed with the height brush. Tried 2026-11-13 against the grid of `roman_island_medium_03` (126 tiles, one river of 28 points, mean width 4.1): by the distance of a tile to the river's line the best fit has precision 0.28 (any radius or share of the width), so the grid does not follow the ribbon; it needs a reading of how the game fills it |
| Lakes (level) | exists; outline built (data) | `edits["lake_outlines"]` (`"k"` -> `points` x, z; straight sides, the level of `lakes` or the lake's own) written by `groundedit.fill_lake` on 117, 1800 and 2205; the ground under it is not carved; the water tool shows each outline point of a new-engine island's lake as a handle at the lake's level (drag it over the ground: `EditSession.move_lake_outline_point`, one undo step, 2026-11-13) and the level at the lake's centre; the lake's drawn surface follows after a build, not while dragging |
| Coast lines (`CoastLine` editor objects) | built (117, 1800, 2205) | `water_network` gives them as `coasts` (x, z), the water tool's gizmo moves their points as on 2070's islands, `groundedit.apply_water` writes `edits["coast"]` (`"k"` -> `points`) through `fill_spline`: each point keeps the height of the old point nearest to it, the normal `d` turns with the line and keeps its sense; a vanilla island's coast lines are short helper curves (2 of 5 and 6 points on roman_island_medium_03), not its shore |
| Ford, air blocker and environment grids | ford built (data), the others missing | `edits["ford"]` (`"x,z"` -> 1 or 0) into `WorldManager/FordGrid` by `islandgrids.apply_ford`; a ford lies on a `RiverGrid` tile in every island checked (117: 130 of 1,552 river tiles on `roman_island_extralarge_04`, never off the river), so a cell off the river is listed as unmatched; the Grid tab's Ford group lays and erases fords with the active map's cell brush and shows them on the ground (2026-11-13, `cellbrush`, `desktopgrids`, [the tools](island-editor-tools.md)). The air blocker grid is absent from the shipped islands checked. The environment grid is a byte per 4 x 4 tiles whose values are the game's `EnvironmentType` (the audio ambience zone: `docs/formats/enums-117.md`, the *Lua* column, not the data id): read over the first 60 vanilla islands, 20 (Sea) lies on cells with 0 % land, 21 (SeaClose) on 4 %, 29 (RiverCrossing) on cells with 95 % river tiles, 1 (CoastRock) and 2 (CoastSand) on cells with 70 and 57 % land, 4 (Forest), 7 (Marsh), 8 (Meadow), 9 (Mountain) and 10 (MountainTop) on all-land cells, and the Egyptian valley's 31 and 32 are Desert and SeaCalm; a trader island also carries 11, 13 and 24 (residence and harbour zones) on a few cells. Built 2026-11-13: the Grid tab's Environment group paints a zone with the cell brush (`cellbrush.ValueEdits`, `edits["environment"]`: `"x,z"` -> value, written into `WorldManager/EnvironmentGrid` by `islandgrids.apply_environment`; the island build's own writer fits the zone to the water and the height: coast 1, river crossing 29, sea 20 and 21, on land Meadow 8 below 10 world units, Mountain 9 above, Mountain top 10 above 30; since 2026-11-13 it writes those numbers under those names, before it wrote Forest 4 for every low land cell - see island-terrain "Environment zones") |
| Active map (rd3d `ActiveMap`, `.a7minfo` `ActiveMapGrid` / `ActiveMapRect`) | built | `cellbrush`, `islands/islandinfo`: Grid > Active map, Draw and Erase cells (key G), written into both grids and the rectangle cut round them; the rules readout counts the cells that hold land; a cell the island's stored geometry does not reach is not drawn by anything (not seen in game) |
| Area IDs and the construction area (`AreaIDs`, area kinds) | the construction area built; the data side of other area kinds built (a brush missing) | the Grid tab's Build area group adds and removes tiles of the construction area (`cellbrush.AreaEdits`, `edits["area"]`, "Build area" below); a tile of any other area id is never touched; the land tiles are still counted for the size class from the file; an edit may also name another area's id the island has (`islandedits.apply_area`, "Area kinds") |
| Blockers (build blockers, the map-exit blocker) | shapes built (2070 and the built island; 1404's files not read); the objects partial | the objects are placed and moved as editor-only objects; the shapes (`BuildBlockerShapes`, `CamBlockerShapes`) are polygons whose points the water tool moves (`edits["blocker_shapes"]`, "Blocker shapes" below) |
| Objects with every transform (`Position`, `Orientation`, `Direction`, scale, a `Prop` block's scale per axis) | exists | `editsession`, `islandedits.set_object` |
| Placed buildings, slots, ruins, units and other objects held by asset GUID: drawn with their model | built (117, 1800: from the game's assets); exists (2070, 1404, 1701, 2205) | `assetmodels` (GUID -> asset in `assets.xml` -> its `Object/Variations` or its base asset's or its template's default -> the island's `Variation`), `islandeditor.objects`; config read from the baked `shared/` copy first (`islandeditor.model`); a pin only where nothing is to be drawn, with its reason in the Checks tab ([Placed objects](#placed-objects)) |
| The same: picked, moved, turned, tilted, scaled, copied, pasted, deleted, undone, written back in the island's own list | built (117, 1800; copies in 2070, 1404, 1701 and 2205) | the object list picks one object (`desktopeditor.ObjectsPanel`); `objectclones` writes a copy of an object of the island with its GUID, variation and blocks (117 and 1800 builds), `Island2070.add_object` 2070's and 1404's (a building into the `Handle` group with the next free `m_ID`), `editor/formats/isc/edit.apply_added` 1701's (a handle past `m_LastHID`), `editor/formats/a7m/edit2205` 2205's (`<serial><kind>`) |
| Buildings and other objects placed from the game's asset list | built (all six games) | `placeables` (the list), `desktop/desktopplace` (the Buildings dock, the Place tool's building mode), `placedrules.check_place` (the live rules); [Placing from the list](#placing-from-the-list) |
| Props (`PropGrid`: position, rotation, scale, colour) | built | the tint is read, set in Properties (a colour button), copied and pasted, kept in the edit file (`color`) and written into the `PropGrid`; the view does not tint a prop yet (waits on the renderer migration, est. 2 h after it) |
| Decorations (decoration objects, natural presets) | exists | 1800's natural presets untried on game data |
| Forest (`TreeMap`, `ForestChunkMap`) | exists (117) | 1800's trees are props |
| Slots (`.a7minfo` `RandomSlotObjects`, slot objects) and their directions | partial (built: the slot objects of an island draw and move like buildings) | kept; turned slots snap to eighths (`snapping.eighth`); the `.a7minfo` slot list follows the objects after every build (built 2026-11-13: `slotlist`, "The slot list"); a panel of it is left |
| Deposits and resource sets, fertilities | nothing on the island to edit | the island's stocks are drawn by the map generator from the slot list and settings ("The slot list"); the editor shows the slots |
| Fog (rd3d `Fog`) | missing | est. 3 h (a brush on the fog map; its scales are not settled) |
| Lights and the session's ambient (`SessionSettings`) | settings built (data and session; no form yet) | light objects place from the library as models; `edits["session"]` writes the ambient, the vegetation set and the playable area (`sessionsettings`) |
| Herds (herd editor objects, `AnimalManager` herd areas) | built | the objects place and move; the Grid tab's Herd area group paints the grid and the build draws the areas again (`herdareas`, "Herd areas") |
| Editor markers (position markers, audio spots, flocks, sequence dummies) | exists | the editor-only library (`editorassets`) |
| Map pictures (`mapimage`, `gamemapimage`, `provincemapimage`, `activemapimage`) | partial | the Minimap dock draws the island; the pictures are redrawn after a height edit of a vanilla island (built 2026-11-13: `mappictures`, "Map pictures"); not for paint, and a built island draws them from its final heights anyway |
| Island metadata (`.a7minfo` `MapSize`, `StartCoastDirection`; `.a7me` dimensions) | built | `islandprops`, Edit > Island properties: the start coast direction and the active rectangle, written beside the island (`.a7minfo`); the grid is shown, not changed (the island's files fix it); the `.a7me` dimensions are kept |
| Pool registration (`RandomIsland`: `IslandRegion`, `IslandDifficulty`, `IslandType`, `IslandPlaceholderSize`) | built | `islandmeta`: read from the source's asset, the edit file's `registration` written into it for an island saved as a new one (`editor/formats/a7m/edit117.build`), checked against the values the game's assets use; the form built 2026-11-08 (Edit > Island properties, the Pool registration tab: the known values as choices, the problems named as you type) |
| Several levels of detail | exists | pages and stored meshes of every level rewritten |

### Island data: Anno 2070, 1404 and 1701 islands

| Data | Status | Where / what is open |
|---|---|---|
| Size, clime, difficulty, sea level | built (2070, 1701) | `editor/formats/isd/props`, Edit > Island properties: clime, difficulty and sea level of a 2070 island, the size shown; 1404's files carry none of the three; 1701 keeps clime (north, south) and difficulty (0 to 2) in the `.isc` header, which the build writes beside the `.isd` |
| Chunk heights and the logic height map | exists (2070, 1404, 1701) | `editor/formats/isd/edit`, `oldenginebuild` |
| Ground texture layers | exists (2070, 1404, 1701) | |
| Objects (nature, simple, handle, feedback, grass groups) | exists (2070); partial (1404: moves only for old turns); built (1701: new objects as copies, footprints and the occupation grid) | the game's use of the grid word is not seen |
| Trees | exists (2070) | |
| Rivers, lakes, coast building lines | exists (2070, 1404); built (1701: rivers and coast lines of the `.isc`, no lakes) | a lake's dam height kept; 1701: `editor/formats/isc/water`, the points and widths and the length along the line; a river's build and path blocking lists are redrawn by what the move changes (2026-11-13, `editor/formats/isc/blockings`; the game's own rule is not known, the fit is in formats/anno1701-world-format "Rivers' blocking lists") |
| Path blocker layers | exists (2070's height path layer, 1404's path layer) | 2070's general layer and the submarine blocker grid kept (est. 2 h) |
| Street grid, object groups, surf lines, sandbanks, fog banks, build and camera blocker shapes, terrain normal splines, animal layer, AI points | missing | kept byte for byte (est. 2-4 h each) |
| The far-view picture (`<island>_2070_0.dds`) | partial | land and coast channels redrawn after a height edit |

### Editing operations

| Operation | Status | Where / what is open |
|---|---|---|
| Select by click; focus; go to a place | exists | `desktopview` |
| Multi-select (add, toggle, a box, every entry of a model) | built | `selection.Selection`; in the window Shift+click and Edit > Select the same model (`desktop/desktopgroup`); a box drawn in the view est. 1 h |
| Move, turn, tilt, scale by gizmo or typed | exists | `desktopview`, Properties |
| A model of several materials drawn with each material's maps | built | `modelparts.model_parts` on `models/configtree`; the mesh cut per material (`mesh_glb(rdm, material)`) |
| The same on several objects at once, as one change | built | `selection.Selection.move` / `turn` / `scale` / `delete` / `duplicate`; in the window the gizmo's move carries the others, Del and Ctrl+D act on all; a group turn and scale on the gizmo est. 2 h |
| Copy and paste, also into another island | built | `selection.copy` / `paste` (a clipboard of plain data); Ctrl+C, Ctrl+V at the cursor's spot |
| Height brushes (raise, lower, flatten, smooth), paint, forest, path blockers | exists | `editsession`, `sessionpath` |
| Brush falloff (smooth, linear, sharp, constant, sphere) | built (height brush) | `brushes.falloff`, Edit > Brush falloff; paint keeps the smooth curve, as the build lays strokes with it (est. 1 h to carry a stroke's curve into the build) |
| Further terrain tools (noise, terrace, ramp, spike removal) | built | `terraintools`: Terrain > Relief; each stroke is one height edit in the edit file, with the brush falloff |
| An object brush for any model (spray with spacing and random turn) | built | `objectbrush`: Object > Spray model (key O) with the library's pick, a spacing, a random turn and size; a stroke is one undo step of added objects |
| Edit layers (named groups of objects, hidden or locked) | built | `editlayers`; kept in the edit file's `layers`, which the build ignores; Edit > Selection to a layer, Layers |
| Snapping to the game's grids (tile, half and quarter tile, the diagonal lattice, the 8-tile active cells, a building's own grid by its footprint (built 2026-11-09), eighth and 15-degree turns, onto the ground) | built | `snapping`, `footprints`; Edit > Snap places / Snap turns / Snap onto the ground, used by Place, the gizmo's move and Paste |
| Undo and redo (200 steps, the History tab) | exists | `suite/undo` (`UndoStack`, shared with the asset editor); a group operation is one step (`selection.one_change`) |
| Validation against the game's rules with clear messages | built | `islandrules.check`, Grid > Check rules on every island (grid, size class, placed objects off the grid, floating or buried, stacked copies, an edited building off its build grid, off the eighths or overlapping another's footprint, the objects drawn as pins and why (built 2026-11-09), rivers, lakes, material IDs, the height range; `check_map` for the 127 islands of a map); the optional checks of slots and falls as before |
| Preview in the game's look | exists (117 rules, 2070 look); partial (1800, 2205: the editor look) | est. 8 h for 1800's rules |
| A free-fly camera, the game's camera steps | partial | the free-fly camera is built (`desktop/flycamera`, the status bar's Camera box); the game's camera steps (read from the installed camera configuration) est. 2 h |

### Placed objects

Buildings, slots, ruins, units and other objects an island file holds by asset GUID (`GameObject` and `EditorObject`
lists of the areas: GUID, ID, position, `Direction`, `Mesh/Orientation`, `Variation`, and the blocks the asset's template
adds). The pictures and the counts are in the Status table.

- **What is drawn** (`assetmodels.AssetModels`, `islandeditor.objects`): the object's own model config
  (`DynamicVariation/ConfigFilename`, decoration), else its asset's: the asset found by GUID in the game's `assets.xml`
  (read without parsing the 46 MB file), its `Object/Variations` item number `Variation` (the first when the island names
  one past the list), the base asset's list when the asset has none (`BaseAssetGUID`), the template's default in
  `templates.xml` when there is neither. The config comes from the baked `shared/` copy the game reads when there is one
  (`islandeditor.model`; two unit configs that failed to parse in `data/` read there or, with a control character in a
  name field, after `configtree.parse` drops it). Props use the same reader.
- **What stays a pin**: an editor-only object (position markers, audio spots: their kind's colour), an object whose asset
  names no model, only an editor placeholder, or whose config draws nothing (decals, an effect only): a white pin
  (`marker` `NoModel`); one whose asset the game does not have or whose config cannot be read: a pin of the same shape
  (`marker` `Unresolved`). `unresolved` names the reason; the Checks tab (Check rules) lists one row per kind of pin with
  its count and the reason, a defect (no asset, unreadable config) as a warning and a missing model as information.
- **Editing**: the object list lists each object of a model placed up to 60 times under it by place; a click selects and
  flies to it. Move, turn, tilt and scale are the gizmo's, Properties' and the multi-select's; every change is a key in
  the edit file's `objects` (`guid:<asset>` and the place name the object), written by `islandedits.set_object` into
  `Position`, `Mesh/Orientation`, `Direction` and `Mesh/Scale` and nothing else, so the rest of the object's data stays
  as it was. A copy (Ctrl+D, Ctrl+C / V) is an `added_objects` entry with `guid`, `clone`, `variation` and the model it
  draws; the build writes it as a copy of an object of the same asset on the island, else of the same template
  (`objectclones`, 1800's too), with an ID of its own, the island's folder and quest links dropped and the `Variation`
  set. An entry that finds nothing to copy (a building pasted onto an island with none of its asset or template) is
  reported as unmatched, not dropped.
- **Grids and rules** (`footprints`, `placedrules`): a building's footprint is the `BuildBlocker` polygons of the info
  file beside its model config (`<model>.ifo`, in tiles round its origin). The building grid snap (Edit > Snap places >
  Building grid) puts the origin where the turned footprint's corners stand on whole tile corners (on an odd eighth: the
  diagonal lattice), and a turn by the gizmo takes the turn setting (Snap turns) and then re-snaps the building. Check
  rules warns for an edited building turned off the eighths, off its build grid or overlapping another footprint
  (separating-axis test on the polygons' hulls; edge to edge is not an overlap); objects the edits did not touch are not
  held to the grid.
- **The old engine** (2070, 1404; built 2026-11-09): the footprints are the same `BuildBlocker` polygons in fixed point
  (4,096 to a tile, `models/infofile`, through `footprints.read_footprint`), read from the game's files; the turn rule is quarters (`placeables.TURNS`:
  117 alone builds in eighths). 1701's footprints are the `NoBuildings` cells of the model's `.info` file (`models/info1701`, 154 of 163 objects equal the islands' grids);
  a landscape piece has none there, so its footprint is read off the shipped islands' occupation grid (`grid1701`), and an asset
  neither holds places freely.
- **Template blocks**: a building written as a plain object takes the blocks of its template from the
  game's other islands (117 and 1800: `templatedonors`, an object of the same asset first, else of the same template,
  read island by island, at most 24, the first found; the island's own object, as before, comes before them): on the
  installed 117 a Harbor-warehouse-like asset the campaign island lacks was written with its `Building`, `Maintenance`,
  `Warehouse` and `Health` blocks (1.2 s; the quest link dropped). 1701: a footprint per model from its `.info` file, else read off the shipped
  islands' occupation grid (`othergames/grid1701`, [format](formats/anno1701-world-format.md#the-occupation-grid)), used by
  the Place tool, the snap and the live check, and the grid follows a moved, new or deleted object. The live check
  (`buildground`) warns for ground that slopes more than `FLAT_STEP` (0.5 world units between the corners under the
  footprint: the editor's own limit, the games publish none), for blocked path cells under it (2070 and 1404: the height
  layer, where the mountains are) and for a rock or cliff model (by its file name) whose origin is in it. A selected
  building has its footprint drawn on the ground (the selection's colour, red where the live check says no).
- **A work area slot's outline** (`islands/objectpath`): the `WorkAreaSlot` objects of 117 (the mountain,
  river and Egyptian slots: 669 in the 145 vanilla islands, one or two points each) keep an outline in world
  coordinates beside their place, a `BezierPath` whose points, tangents (`i`, `o`) and bounds (`Minimum`, `Maximum`) the
  edit used to leave where the slot had been. `islandedits.set_object` now moves every point by the slot's move and
  turns it about the slot's place by the change of its `Direction`, tangents with it; bounds that were the points' own
  stay so, bounds with slack keep their slack (the box that held the old one, moved and turned; 161 of the 669 shipped
  ones have some). A copy takes the outline along, as it is written through the same call. The turn sense is read from
  the shipped slots: for every kind, the angle of the outline from +z towards +x plus the slot's `Direction` is one number
  (coherence 0.8 to 0.96 over 669 outlines against 0.07 to 0.14 for the other sense), and the installed test repeats
  it after a move and a turn (`tests/test_islandedits.py`). Not seen in the game: a slot edited this way.
- **Open**: 2070, 1404, 1701 and 2205 write a plain object without template blocks (their objects carry none); the slope
  limit and the rock rule against the games (est. 1 h in game: a building placed on 0.5 of rise and next to a named rock); 1701's grid word keeps
  its other bits as they were and the game's own rewriting of the grid is not known.

### How the models find their files

Every game's models find their meshes, textures and nested configs by the viewer's rules (`models/fileresolve.py`;
viewer.md "How files are found" has them in full): a source name as the `_0.dds` the game ships (a `.dds` as it is
named, a lower quality level `_1` to `_4` when that is all there is; 2070, 1404 and 1701 try the name as written
first), `.fbx` as `.rdm`, a plain `.rdm` as its `_lod0`, the baked `shared/` copy of a config first, the studio's
`d:/.../data/` paths and doubled slashes as game paths. The 117 island reads them through the mod folder first, then
the game; a texture decodes in process (every DDS layout the games ship), texconv only for a file the decoders do not
read.

- **What does not draw is listed**: Check rules adds a warning per file a drawn model names that does not draw - not
  in the game's files, with the reason by the game's own index (deleted by the game, a folder it does not ship, a file
  that ships under another folder, absent), or not decodable - and the config that names it (`islandrules.check_files`
  over `IslandEditor.file_problems`). A missing colour map draws as a magenta and black checker, a missing normal map
  as none; a placeholder the studio wrote (`default_*`) draws without a map, as the game draws its default.
- **Checked against the installs**: `anno-toolbox viewer-files <game> --islands N` opens N islands of the game the
  way the editor opens them and draws everything they place its way (`editor/islandfiles.py`: each config through
  `model`, each texture through `texture_png`), beside the viewer's files check of every config the game ships; the results
  are in viewer.md, "What the files check found".

### Placing from the list

The **Buildings** dock (tabbed with Object Groups) lists what the open island's game can place, and the
Place tool puts a building on its grid with the rules checked as the cursor moves. The user's side is
[Placing buildings](island-editor-tools.md#placing-buildings).

- **The list** (`placeables.scan`): every asset of the game's asset files (the files the asset editor reads,
  `gamefiles.LAYOUTS`; 2205's assets and the files they include; 1701's `.ocf` object configs, `from_object_configs`)
  that draws a model - its own `Object/Variations`, its base asset's or its template's - read by the asset index's scan
  (`assetindex.scan_file`), not parsed whole, and cached in the editor cache (117: 8 s the first time). The developers'
  stand-ins (`data/dev/`, placeholders) are left out. A **building** is an asset or template with a `Building` block or a
  template named so. Groups: the build menu where the game has one (117's and 1800's `ConstructionCategory` assets: a
  category lists buildings and categories, so a building's group is the path of categories down to it), else the asset
  files' own groups (2070, 1404, 2205), else the template. The search takes a name, a GUID or a template; Buildings only
  is on at first. A row shows its icon (`IconFilename`, read as the asset editor reads it) where the game names one, and in
  the games that keep icons in `icons.xml` (2070, 1404, 1701) the tile of the game's icon atlas; a row the game has no
  icon for says so in its tooltip; the model itself is the preview under the cursor.
- **The ground** an asset may stand on, from its values or its template's defaults: 117 `Building/TerrainType2`
  (`NormalLand`, `Coast`, `Water`, `Marsh`), 1800 `Building/TerrainType` (`Terrain`, `Coast`, `Beach`,
  `Water_Including_Coast`, ...), 2070 and 1404 `Blocking/BlockingRules` (`CoastBuilding`) and a harbour type
  (`HarborType`); a building that names none stands on land, anything else anywhere (`placeables.TERRAIN_WORDS`).
- **Placing** (`desktop/desktopplace.PlaceTool`): a click on a row (or **Place this on the open island** in the asset
  editor, File > Place on the open island in the viewer for a model one asset names; `suite/links` `place_asset`) picks
  it; its model is made drawable (2070's, 1404's and 1701's configs through the island's model readers) and its footprint
  read. Under the cursor the model stands snapped by its footprint to the building grid (Shift: off the grid, by the snap
  setting), turned by `[` `]` in the game's steps, with its footprint drawn on the ground in green, or red with the
  reasons in the status bar (`placedrules.check_place`): off the island, on ground the asset does not allow (land: every
  tile under it above the sea; the coast: some land with water under or beside it; water: no land), over another
  building's footprint. A click places it, the warning stays in the status bar and Check rules lists it (an edited
  building on the wrong ground or turned off its game's steps); a row dragged onto the view places it where it is
  dropped. The placed object is an `added_objects` entry with its `guid`, `variation`, `clone` and `plain`; it moves,
  turns, copies, deletes and undoes as every other.
- **Writing** it, per game: a copy of an object of the same asset on the island, else of its template, else (`plain`) a
  plain object of the island with the asset's GUID - 117 and 1800 a plain placed object (`objectclones`, the next serial
  of the list), 2205 a game object (`editor/formats/a7m/edit2205`, `<serial><kind>`), 2070 and 1404 a handle object of its own with the
  next free `m_ID` and its render parameters (`Island2070.add_object`, the `Handle` group made where the island has none;
  the 1404 build reads 1404's assets for it), 1701 a copy of a game object under the next handle (`editor/formats/isc/edit`).
  The Log lists an entry that found nothing to copy.

### File operations

| Operation | Status | Where / what is open |
|---|---|---|
| Open 117, 1800 (`.a7m`), 2205 (`.a6m`), 2070, 1404 (`.isd`) and 1701 islands; an island file of a mod folder | exists | the Islands panel, `desktop ISLAND --mod` |
| Open an island of an imported mod | built | File > Open island of the imported mod…: the project's `island-edits/anno117/<game path>` file is the base (`App.project_islands`, `open_project_island`), the edit file holds the change; docs/mod-import.md section 13 |
| Open an island packed in a mod's archive | built | File > Open island from a mod…: `Files(archives=True)` indexes a mod's `.rda` files, `App.mod_islands` lists the islands, loose and packed |
| Save the edits (the edit file) | exists | `editsession.save` |
| Write the island in its own game's format (117 and 1800 as a mod, 2205 as an archive and index, 2070 into its patch archive, 1404 and 1701 into an archive) | exists | `editor/formats/a7m/edit117`, `editor/formats/a7m/edit1800`, `editor/formats/a7m/edit2205`, `editor/formats/isd/build`, `oldenginebuild` |
| A new 117 island from scratch at every grid size (128 to 768) | built | `blankisland`, 117 tab > Native > New island…: the vanilla island of that grid cleared (`editor/formats/a7m/edit117.clear`), its forest cleared, its ground shaped by a height edit into a plateau at its own land level with a beach and a shelf, saved as the new island `new<grid>`; the vanilla ground paint, rivers, lakes and coast lines stay (repaint and move them; est. 3 h to clear them too) |
| A new 1800 island | built | a vanilla island cleared (`editor/formats/a7m/edit117.clear`) and, on the Native list's New island (shaped), reshaped into a plateau island by `blankisland.blank_edits` (on the installed game moderate_3rdparty06_01 built in memory: 1,198 objects and 1,848 props cleared, 16,096 corners reshaped, none unmatched); it replaces the vanilla island in place |
| A new 2070, 1404 or 1701 island | built (2070, 1404 and 1701) | 2070: `editor/formats/isd/blank` (the Native list's New island…, written into the patch archive as `normal/new<size>.isd`; no far-view picture, whether 2070 offers it is not known); 1404 and 1701: one of the game's islands cleared in place (`App.open_blank`: New blank island, New island (shaped), as 1800's): its objects, props and rivers are taken off at the build, the ground reshaped on the first open by `blankisland.blank_edits(source=)`; the donor's ground textures stay (repaint), whether the game loads it is not known |
| A new 2205 island | n.a. | 2205 sessions are whole baked maps (sectors), not a pool of islands; the editor edits the map. A blank sector would be a donor map cleared and flattened under a new name plus a Session asset and index entries: about 20 to 30 h, not built (formats/anno2205-map-format "Writing a map") |
| Export as a mod the game loads (the editor's own writer: `modinfo.json`, the island files, the `RandomIsland` asset) | exists (117, 1800) | `editor/formats/a7m/edit117.build`, `editor/formats/a7m/edit1800.build` |
| A check of the written mod for what the mod loader drops without a word | built | `modcheck.check` (the log after every build) |
| Map pictures of a new or edited island | missing | est. 6 h (above) |

## Running it

```bash
pip install -e ".[gui]"              # PySide6, moderngl, numpy, scipy, Pillow
anno-toolbox              # the window; File > Games and folders the first time
anno-toolbox data/anno2070/islands/pool/a2070_n_l03/a2070_n_l03.a7m --mod out/anno2070-islandtest
anno-toolbox --games anno1800     # the editor for 1800 only
```

The island editor is also the first app of the tool suite's window (`tool-suite.md`; the `studio`
command, or `--app islands` on the program's own command line). The window shows the editor beside the asset editor
behind an app switcher, on the same settings. The editor's window, its docks and its edits are the same as when it
runs alone.

**The settings** are the Anno 117 and 2070 installs, the other games' folders, 117's mods folder, 1800's mods folder,
the 2205 folder to patch, the workspace and the theme. The games are automatically searched for in the Ubisoft
launcher's and Steam's registry entries and in the usual folders. Anno 2205 is told from 2070 by `Anno5.exe`. 1800's
mods folder is not guessed: "Build & install" for 1800 copies only where the setting says. The settings are kept in
`%LOCALAPPDATA%/anno-toolbox/editor-app.json`, which also holds the window's last size, place and dock layout
(`layout`, written when the window closes; Layout > Reset layout forgets it).

**The theme** is System (the default: light or dark as Windows says, switching live when Windows does), Light or Dark.
Both palettes keep 1800's tool look with the orange accent (`suite/theme`). The 3D view's own colours follow the theme
live (see below).

**The workspace** (default `%LOCALAPPDATA%/anno-toolbox/workspace`) is where everything the editor writes goes:

- `anno2070-islands/`: the mod of the built islands
- `anno117-islands/`: Anno 117 editing
- `anno1800-islands/`: Anno 1800 editing
- `anno2070-patch/`: Anno 2070 editing
- `island-edits/`: the edit files
- `editor-cache/`: meshes and textures made drawable, named by what they are drawn from
- `road-sheets/`

The games' own files are never changed, with one exception: "Build & install into 2070" replaces 2070's
`patch9.rda`. The original is kept in the workspace, and "Restore the game's own" puts it back.

In the suite's window a **project** can be open (Project, under the app switcher). The edit files are then that
folder's `island-edits/`, and the project's one build takes the islands of its game (117 or 1800) together with the
other apps' work into one mod. With no project open, the workspace is the project.

With an island on the command line (a game path, in `--mod` or the game) the editor opens that one at once.
`python scripts/build_app.py` packages the application with PyInstaller (folder mode, texconv beside it) for a
machine without Python.

**The editor for 1800 only** is the same application restricted to Anno 1800 (`App(games=("anno1800",))`,
`--games anno1800`). Its settings ask for the 1800 install, 1800's mods folder and the workspace only, and the lists
are 1800's. It has no tab for building across games, no 117 / 2070 / 1404 / 1701 / 2205, no Rendering looks (117's
and 2070's rules), and no road piece editor, slot check or fall check (the Tools menu holds only Check rules). The
ribbon keeps the tools that the 1800 islands offer:

- Home: undo, save, build, select, focus, go to
- Object: place, spray, transform, delete, duplicate, hide foliage
- Terrain: the height brushes and Relief, paint, surf, the Material Gallery
- Grid: the active map, Check rules
- Path: rivers and lakes, their points

It shares the settings file and the workspace with the full editor, so the 1800 edit files are the same.

The tests (`tests/test_desktop.py`, `test_editsession.py`, `test_editorapp.py`) run offscreen on a synthetic island;
the renderer's test skips without an OpenGL 3.3 driver (`tests/test_rhi.py` draws the island view on Direct3D 12
and skips without it). Only the game can confirm an edit.

### The renderer

The 3D view draws with one of two devices, with the same pictures on both (`tool-suite.md` section 21, "M6"). The
first is Qt's QRhi on Direct3D 12 (the default where the machine has it: 117's islands open in the game's look
there), Direct3D 11, Vulkan or QRhi's own OpenGL. The second is OpenGL through moderngl (the default elsewhere). The
choice is the suite's `renderer` setting (the viewer's View > Renderer sets it) or the environment variable
`ANNO_RENDERER` (`gl`, `d3d12`, `d3d11`, `vulkan`, `opengl`), read when the window opens. A window composes with one
graphics API, so in the suite's window the viewer follows the island view's API.

A click picks from the id picture on both devices. On QRhi the id picture is drawn in a frame of its own, and its
pixel is read back when that frame is done. Everything else (looks, the game's terrain, ground and water, models,
effects, overlays, the tools) is the same renderer. The setting **Hardware acceleration** (the viewer's
View > Rendering; on by default) is read when the window makes its device. When it is off, QRhi draws on a software
adapter (WARP).

On Direct3D an island of 117, 1800 or 2205 is drawn by its game's own shaders (Rendering > The game's own shaders, on
by default there: `rendering/island-117-view.md`,
`rendering/island-1800-2205-view.md`). The view's own look is the fallback. It is drawn while those shaders compile on
a first start and where they fail, and it is the only look on OpenGL ([Looks](island-editor-view.md#looks)).

## Layout

One workbench window (`workbench.MainWindow`), a dark or light theme with an orange accent (`suite/theme`; the Fusion style,
which obeys the palette). From top to bottom, then the docks:

- **Menu bar**: File (Save, Build & install, Open island from a mod, Games and folders - with the theme -, Reload islands, Close island, Quit), Edit (Undo,
  Redo, Delete, Duplicate, De-Select, Copy, Paste, Select the same model, Snap places, Snap turns, Snap onto the ground,
  Brush falloff, Selection to a layer, Layers: [Several objects, snapping,
  layers](island-editor-tools.md#several-objects-snapping-layers); Island properties:
  [Island settings](island-editor-tools.md#island-settings)), Rendering (Shadows, Particles, Freeze particles, Game terrain, The game's own shaders, 2070 look, Underwater look, Caustics, Rendering quality),
  Tools (Road pieces, Check rules, Check slots, Check falls), Layout (every dock on or off, Reset layout: every dock back where it started, the remembered layout forgotten), Help (Controls).
- **Ribbon** (`desktopribbon`; large icon buttons in labelled groups, the icons drawn as vectors in
  `suite/icons`; a button is greyed when its island, mode or selection does not allow it):

  | Tab | Groups: buttons |
  |---|---|
  | Home | Edit: Undo, Redo · File: Save, Build & install, "Saved" (117 island: replaces the original / as a new island) · Selection: Select, De-Select, Focus · Camera: go to `x z [distance] [turn]` · Layers: Objects, Markers, Effects, Grass, Forest, Water (what the view draws, each on or off; follows the view's state) |
  | Object | Place: Place, Object Groups (raises the library) · Transform: Move, Turn, Tilt, Scale · Edit: Delete, Duplicate · Object Spray: Plant forest, Clear forest, Spray model (O), Radius, Spacing · Visibility: Hide foliage, Show hidden |
  | Terrain | Height: Raise, Lower, Flatten, Smooth · Relief: Noise, Terrace, Ramp, Spike removal · Ground: Paint, Material Gallery (raises it) · Surf: Waves, Calm (U) · Brush: Radius, Strength |
  | Grid | Active map: Draw cells, Erase cells (G), Radius · Checks: Check rules (every island: `islandrules`), Check slots, Check falls |
  | Path | Rivers: Rivers & lakes (and the coast lines of a 2070-format island), Delete point, Insert point · Path blockers: Block, Free, Radius (2070, 1404) · Roads: Road pieces |

  The tool buttons are one exclusive set and follow the view's tool (also when the keys S H P L T A B change it).
  Radius and Strength are the one brush of the height, paint and forest tools, on whichever tab shows them. The
  ribbon is plain data (`desktopribbon.TABS`): a new tool is one item there and one handler in `workbench`.
- **View tabs** under the ribbon: Viewport (the 3D view), Checks (the slot and
  fall results; a click flies there), History (every change; a click goes back or forward to it), Log (the
  build and build jobs, and what an island's reader had to leave out; a job switches to it).
- **Left dock Islands** (`desktopislands`): one tab per game, newest first - 117, 1800, 2205, 2070, 1404, 1701 ([Islands and modes](#islands-and-modes)).
- **The 3D view** in the middle with the frame rate top left (frames per second while drawing; the view draws
  on demand) and the cursor's tile and the ground height bottom left.
- **Right docks**: Material Gallery (the ground materials grouped by the game they come from, each group in its
  draw order) tabbed with Object Groups (the model library: 2070, 117 and 117 editor-only, searched); Properties
  (the selection's place, turn, tilts and scale, a river point's width, a lake's level, typed in; in the suite's
  window **Show the asset** and **View the model** open the selected object in the asset editor and the viewer) above On the
  island (the objects by category and model, with counts; a click flies there, a right click hides) tabbed with the
  Minimap ([Minimap](island-editor-view.md#minimap)).
- **Status bar**: the island and its look, Camera (Orbit or Free fly), FOV (20-90 degrees) and
  Speed (a multiplier of panning and zooming), and the mode on the right with the bar's colour as 1800 does:
  purple in Object Mode (select, place, forest, spray), red-orange in Terrain Mode (height, paint, surf), blue in Path
  Mode (rivers, lakes, coast lines, path blockers, the active map), grey in View only and with no island.

An island loads into this one window, one at a time. Loading another asks to save unsaved edits first, and "Edit" on
the island that is already open only shows it. The view is created with the window and kept (`IslandView3D.load` /
`unload`), because an OpenGL widget that is added to an already shown window makes Qt recreate the window, which looks
like the application starting again. Undo and Redo go up to 200 steps (Ctrl+Z, Ctrl+Y or Ctrl+Shift+Z). Save (Ctrl+S)
writes the edit file (`editsession`, the `islandedits` format below). Build & install builds the open island from its
saved edits.

**Opening an island.** The window's thread only builds panels and draws; everything that waits stays off it. The
island's files and its edit session are read in a worker (`MainWindow._load`, `desktopeditor.open_session`: also the
view's modules and the Slots list's asset files, which the window's thread would otherwise read when the panels are
made), the ground materials and the model list in a second, the models and textures by the renderer's own threads
(two for the models: they decode in Python, and more of them only take the window's thread's turns at the interpreter
lock). The status bar says what happens (`desktopprogress`): reading the terrain and the objects, making the panels,
then what still loads (the materials and model list, the number of models and textures left) with a busy bar beside
it, until nothing does. A frame of the 3D view spends at most 30 ms on uploads of the scene (`FRAME_BUDGET`: the
instances and meshes of the models) and at most 25 ms on parts new on the device (`nativepass.MAKE_BUDGET`: a part's
resources, a decal's, the sea's; `desktopgameobjects.MAKE_BUDGET`: the game's part objects), a model appearing a frame
later, the decals are laid in 25 ms slices (`DecalLayer.update`) and the sea's coast
distance is made in a worker (`gamesea.coast_for`); what is left over follows in the next frames while the view keeps
ticking. A stall report for this (the window's thread's stack whenever it stood still over 200 ms) is how it was
found: known-issues "Island editor held the window while an island opened".

**Closing the window.** A window closed while a build, an install, a port or a restore runs, or while an island is
read, waits for that work first: the status line says what it waits for, the window takes no input meanwhile, and the
island that was being read is not opened. Nothing is cut off mid-write (`desktopjobs.finish_work`); the island's own background reads (materials, model list, minimap) are given half a second, then left to end with the program, so a window closed while a large island still loads closes at once. When a job runs,
the window asks first: **Stop the build**, **Wait** (the default, Enter) or **Cancel** (Esc, the window stays). Stop
ends the job at its next log line (`editorapp.Job.stop`, state "stopped"), as a failure of that step would: what the
step was writing is swapped in whole or not at all. The steps that must run whole report through `Job.note` and are
never cut short: a mod copied into the mods folder with its announcement to the game, an archive install into a game,
a restore. An island being read is waited for without a question.

**Reload islands** (File) reads every game's lists off the window's thread; the panel keeps its lists until the new
ones are read, and the status line says that they are being read.

**Input.** Keys act while the view has the keyboard. The status bar's Camera box switches between Orbit and Free fly.
In Free fly the left button looks round the camera's own eye, the wheel flies, the arrow keys slide and Page Up and
Down lift.

- The left button orbits in every tool. A click selects, places or picks a river point, and Shift+click adds to the
  selection or takes out of it.
- The middle button pans (the right one too in Select). The brushes paint with the right button.
- The wheel zooms towards the cursor, and the arrow keys pan.
- S H P L T A B O G U pick the tools. W E R pick the gizmo (E again: turn or tilt about the model's own axes). Del,
  Ctrl+D, F and Esc work as usual, and `[` `]` turn the model to place.

The gizmo moves along an axis or over the ground, turns about the vertical or tilts, and scales (a prop per axis). A
drag is one change. A tree is picked through a sphere round its crown, because banner leaves are points that the
shader spreads, which a ray passes through. The pivot keeps 4 m from the camera. Zooming on at that distance carries
it forward, and closer than 30 m panning speeds up with the closeness.

**Not there, compared with 1800's editor** (there is no function behind them, so there is no button; each would need
new work):

- Chroma Wheel (no ground colour painting; a prop's tint is set in Properties)
- Regions with Stamps (no regions, fertility areas or stamps in the edit file), and the Region ribbon tab
- Terrain's Chunk Tessellation
- Grid's build grid, build blockers and slot placing as tools (we only check slots; blockers are placed as editor-only
  objects from Object Groups)
- the Mudbox tab
- Camera Sequence, Masks, Object Grid, Ambient and Material Library as view tabs, and the Pen menu

Spike Removal (Terrain > Relief), the Object Brush (Object > Spray model) and a camera mode box (Orbit, Free fly) are
there.

## Islands and modes

In the code, each game's own part sits in `editor/formats/`, with one package per island format family: `a7m/` for
the new engine's FileDB islands and maps (117, 1800: `.a7m`; 2205: `.a6m`), `isd/` for the `.isd` of 2070 and 1404,
and `isc/` for 1701 (its `.isd` and the `.isc` header beside it). Each package's `__init__.py` names its modules and
where to start. What every game shares (the edit document, the brushes, the lists, the build jobs) stays in
`editor/`.

Each game's tab has three lists:

- **Native**: the game's own islands. "Edit" opens one, and saving starts an edit of it: an edit file in the
  workspace's `island-edits/` (`117_<island>.json`, `2070_<island>.json`, `1404_`, `1701_`, `1800_`, `2205_`). The
  game's own file is never changed. The edits are applied when the island is built.
- **Edited**: native islands with an edit file: edit again, build, build and install. 117 islands build into the
  mod `anno117-islands`, 2070 islands into 2070's `patch9.rda`, 1800 islands into the mod `anno1800-islands`
  (Anno 1800 editing below), 2205 sessions into an archive and the game's file index (Anno 2205 editing below), 1404 and 1701
  islands into an archive of the game ("Other games" below).

**Groups**: the three lists show their islands in collapsible groups. A click on a header folds it, and the search
looks through every group (and unfolds the ones it finds something in). An island with no known group is under
"Other", and a list whose islands all fall under "Other" stays flat. Grouping is a step of its own over whatever rows
a list holds (`editlists.group_rows`, `desktopislands.IslandList(group_of=...)`). Where each island belongs is decided
by `islandgroups`, which reads it from the game's own data and not from a list of names:

| Game | Group | Read from |
|---|---|---|
| 117 | Latium, Albion | the province folder in the island's path (`provinces/roman`, `provinces/celtic`) |
| 1800 | Old World, New World, Arctic, Enbesa | the `IslandRegion` of the island's `RandomIsland` asset in `assets.xml` (Moderate, Colony01, Arctic, Africa); an island with no such asset (rivers, campaign, scenarios) takes the region its file name or folders say (`colony02_`, `campaign/moderate`) as those assets pair them; else `Scenario NN` from the path |
| 2205 | Earth, Arctic, Moon, Tundra, Orbit | the session's name (`[event_]session_<region>_NN`); the meta maps are Other |
| 1404 | Occident, Orient, Campaign, Scenarios, Test Islands | the folders under `islands/` (`north` is the Occident, `south` the Orient) |
| 1701 | North, South, Campaign, Scenarios, Natives, ... | the same folders, an add-on's `addon01` level folder skipped |
| 2070 | Normal, Underwater, Campaign, Scenarios, World events, Deep Ocean, ... | every `.isd` of the archives (below): the folders, and for a scenario island the scenario's name from the game's own data |

Only the display names of a game's own keys are a table (`islandgroups.LABELS`; the groups of 117, 1800 and 1404
come in the game's order, the rest alphabetically). A built island is grouped by the group of its 2070 source.
Anno 1800's Cape Trelawney has no island region in the game's data (it is a session of the Sunken Treasures DLC), so
it is not a group. What the data cannot say is not guessed.

**2070's list** holds every island the archives have (base game, patches and the Deep Ocean add-on, `addondata/`),
found through `overlay.SourceTree` (`editor/formats/isd/islandlist`): 384 in all. The pool's islands
(`islands/normal` and its `ecomod` variants) are listed by their stem, and every other island by its path, because
stems repeat (a campaign's `n_s01` is not the pool's). The groups come from the folders and the game's data. The
`ScenarioMission` assets give each scenario its name and its `.www` world, whose `IslandConfig` entries name the
islands it places. An island that no world names takes its folder's scenario:

| Group | Islands |
|---|---|
| Normal / Normal · Ecomod | 79 / 54 (the pool the random maps and the continuous game draw on) |
| Underwater / Third Parties | 37 / 6 |
| Campaign · Chapter 01, 02, 03 | 16 / 12 / 21 |
| Scenarios · Scenario07, Scenario08, Sudoku, Scenario05 / Scenario03 (multiplayer), DLC 01 Scenario01, DLC 01 Scenario02 | 1, 1, 1, 1, 1, 2 |
| World events (and World Event 01, 02, 04, 05) | 1 (23, 23, 5, 4) |
| Deep Ocean · Underwater, Third Parties, Scenarios · Mission 01 - Tsunami Disaster, Mission 03 - Energy Transmission, Mission 04 - `Hostile Takeover`, Mission 05 - Story Conclusion | 46, 3, 3, 3, 1, 2 |
| Test worlds · four folders, Benchmark | 28, 10 |

The data has no North / South split for 2070 (those are 1404's folders), and no archive but the add-on's brings
islands of its own. An island opens in Anno 2070 editing through the reader that the pool's islands use
(`editor/formats/isd/edit`). An edit of a non-pool island is kept in an edit file named by its folders
(`2070_data-islands-campaign-chapter-01-n_s01.json`), and the pool's keep their stem names. 

Which tools an island offers follows its kind (`desktopeditor.modes`):

- A built island or an island file: every tool but the path blocker brush (only 2070-format islands have path
  blockers) and the two sea and grid brushes of a vanilla island (the active map and the surf).
- A vanilla 117 island: every tool but the path blockers (its ground edits are written with its render data,
  [Ground edits of vanilla islands](#ground-edits-of-vanilla-islands)).
- An 1800 island: every tool but the forest and the path blockers.
- A 2070 island: every tool but the active map and the surf.
- A 1404 island: all but the forest, the active map and the surf (objects and Place, the spray, heights, paint,
  rivers, lakes, coast lines, path blockers).
- A 1701 island: everything but forest, blockers and the active and surf maps.
- A 2205 session: objects, heights, paint, water and the spray.
- A view-only island: only Select.

The label in the status bar says which game the island is for and whose rules draw it.

**Anno 117 editing**: "Edit" on a 117 island opens the vanilla island itself; the edit file records it as `"source"` and how
it is saved, `"target"`: `replace` (the mod carries the island at its own path, so every game that rolls it gets the
edited one) or `new` (a copy as `data/anno2070/islands117/<island>_edit/`, with the original's `_gamedata` and a
`RandomIsland` asset copied from the original's, so it joins the pool next to it). `editor/formats/a7m/edit117.IslandFile` opens
the `.a7m` and offers what `islandedits` works on, so objects, effects, editor-only objects and forest edit as on a
built island; heights, paint, rivers and lakes are written into the island and its render data (below); the
editor opens it as `editor/formats/a7m/edit117.VanillaEditor`, whose water tool edits the island's own rivers and lakes and whose
palette is the island's own material set. "Build & install" writes every 117 island with an edit file into
`anno117-islands`, with the `_gamedata` files a ground edit changed beside it.

**Anno 2070 editing** (islands for Anno 2070 itself): "Edit" on a 2070 island opens its `.isd` directly
(`editor/formats/isd/edit`): the terrain from the chunks' heightmaps, the ground from their texture layers, every object of
the island (trees included, 6,858 on n_l03) with the model its asset names, read from the game's own files
(`models/modelread`, drawn by `editor/modelviewer`: each `.rdm` one part per material, the terrain cut-outs left out,
water on the water shader, the leaves as banners as the game draws them; a model that does not read is a marker,
listed in the Log). A library model is placed as it is. An object is named by its `m_ID` (`"2070:<id>"`), which the `.isd` keeps. Objects move, turn,
scale, go and come from the library (trees, rocks, vegetation and props: the `Nature` and `Simple` groups;
resources, mines and herds only move); the Height brush changes the chunks' heightmaps and the logic grid
`m_HeightMap_v2` (1404: `m_HeightMap`; a tile's mean corner height x 1024; a chunk out at sea has no heights and is switched on when an
edit reaches it, in `UsedChunks` too); the Paint brush paints 2070's ground textures into the chunks' layers (the
painted layer gains the stroke's cover, the layers above it lose it; a chunk without the layer gets it on top). The
Forest brush plants tiles that the build turns into tree objects, one per tile, of the island's six commonest
trees, picked and turned per tile as the view drew them (`forest_kinds`, `tile_pick`); clearing deletes the tree
objects under it. A prop carries its transform twice, in its object and in `PropertyMesh/m_RenderParameters`
(header, position, orientation, scale, tint): both are written. "Build & install into 2070" writes every edited
island into a `patch9.rda` with the game's own files of that archive (24 credits texts) and puts it in place of the
game's; why patch9: engine-notes "Anno 2070 archives". After height edits the island's far-view texture
(`<island>_2070_0.dds`: A8R8G8B8 with mips, the island stretched over a power-of-two square, rows from the far
edge) is redrawn: A, the land, and G, a band along the coast, from the edited heights by tables read off the vanilla
islands (A matches at r 0.99, G at 0.78-0.90); R and B, a grey picture of the ground with shading of its own (a mix of
the ground textures explains only R² 0.6 of it), stay the vanilla island's.

The water tool (`editor/formats/isd/water`, `sessionpath`) edits what the `.isd` keeps of the water. **Rivers**: the points
move and their widths change (2070 names a point `p` / `w`, 1404 `position` / `width`; the edit file's `rivers`
index counts the rivers that have two points or more); a river the edits do not touch keeps every byte, an edited
one gets its build blocking outline (one bank forward, the other back, half a width either side of the points:
vanilla's within 0.02 tiles on average) and its path blocking cells redrawn. The vanilla cells follow no outline
rule found (2070: 27 % of the cells differ either way from any half-width tried; 1404's lie in the outline plus
0.25 tiles to within 3 %), so the redrawn cells are every half-tile cell within 0.25 tiles of the outline: never
fewer than vanilla's, a quarter more in 2070 and 3 % in 1404. **Lakes**: the level (`m_WaterHeight`), not the outline. **Coast
building lines**: a point moves over the ground (`coast`, x and z); the directions per segment stay, and with a
hand-added point each segment takes the nearest old one's. On a built island the coast lines are the island's own, numbered building lines first, then the shore lines (`<island>.water.json` `coasts`; until 2026-11-13 area lines came between them, so an older edit file's shore line numbers point one block too far); checked 2026-11-13 by moving a building, an area and a shore line of `n_l03` through the session and building with the saved file: those three lines moved and no other changed. **Path blockers**: the Block / Free brush (B, Path tab)
paints cells of half a tile into `path_blockers` (`"cx,cz"` -> 1 or 0, only what differs from the island): 2070's
`m_HeightPathBlockerLayer` (2 cells per tile; ground above about 7 to 10.5 units, F1 0.86-0.91 for a height
threshold, n_s02 matches nothing, so part is hand-painted) is the layer shown and written (1404 has only
`m_PathBlockerLayer`, which it edits); 2070's general `m_PathBlockerLayer` is left alone, as 44 of the 285 islands
have height-layer cells outside it and the game combines the two. Without a stroke the vanilla layers are written back as they were; the height
brush does not recompute them.

**Construction area.** `AreaIDs` (`GameSessionManager`) is a sparse grid of 16-bit area ids per tile, one value
per tile: 0x2001 is the construction area (the island flag 0x2000 with area 1) and 1 the tiles outside every area; a
continental donor holds more ids (0x4001 and others, `island-build.md` "The island's areas"). The edit file keeps
`area` = `{"x,z": 1 or 0}` (1 the construction area, 0 none), only what differs from the island. The Grid tab's Construction area
group (Add area, Remove area, on the cell tool's brush) draws it as an outline of the area; a tile of another area id
is not offered to the brush (`allowed` of the island JSON's `area_grid` holds the tiles with 0x2001 or 1) and a hand-written
edit for one, for a tile off the island or for an island with no grid is listed as unmatched and left out. The 117 editing
path (`groundedit`) and the island build for a 2070 island (`islandedits.apply`, after the island's own area is written) both set the
tiles through `islandedits.apply_area`. What the construction area is for: slots, buildings and roads can be placed on it
only (a free slot's menu opens for the player's construction area), so painting it makes a tile buildable or not; the
game's size class counts the land tiles, not these.

**Blocker shapes.** 2070 keeps two lists of polygons in the island (1404's islands were not read for them): `BuildBlockerShapes` (where
nothing may be built; 285 islands of 2070: 1,258 shapes of 3 to 234 points) and `CamBlockerShapes` (the camera's limits
round the island; 222 shapes). Each item holds one `Polygon` of fixed-point `x, z` pairs in tiles, nothing else (checked
over all 285 islands). The edit file keeps `blocker_shapes` = `{"build": {"k": {"points": [[x, z], ...]}}, "camera": {...}}`:
`k` counts the shapes with three points or more in file order, a shape with fewer is left alone, and an entry whose
`k` matches no shape or holds fewer than three points is reported as unmatched. The water tool moves a point over the
ground (red polygons for build shapes, blue for camera shapes); the points are written back as the polygon's pairs.

The built island holds no shape: the build keeps 2070's build shapes as the holes of the construction area it writes
(`buildable_mask`: tiles inside a shape are not buildable, the river banks excepted). The edit file of a converted
island (`a2070_<island>.json`) therefore takes the same `blocker_shapes["build"]` and the build uses the moved polygon
for that mask; its `<island>.water.json` lists the shapes as `blockers` (`{"points": [[x, z], ...]}` per shape, in the
island's tiles and numbered as 2070's, before any edit), so the editor shows them on the built island. Camera shapes
have no counterpart in 117 (they are read only for the underwater mode) and are not part of the built island's edits.

### Slots, herd areas, map pictures and session settings

Each part below is described with its code, its tests and what no game has seen yet.

**The slot list** (`islands/slotlist`). `ObjectMetaInfo/RandomSlotObjects` of the `.a7minfo` lists, per slot asset, the
slot objects of the island: an attribute `#32768` holding the asset's GUID, then a node `#1` whose children `#1` each hold
`ObjectId` (the object's `ID`, eight bytes) and `Position` (three floats). The map generator reads it to decide which slots
an island offers (island-slots "117's slots"); vanilla lists every slot object of the island, in asset order (checked on
`roman_island_medium_03`: three slot assets with 3, 2 and 3 entries, each position the object's own). The island build wrote
the list once, when it placed the slots, so a slot moved, deleted or added by hand left the
list as it was. The build brings it in line after the edits (`islandedits.refresh_slot_list`, `slotlist.refresh`): the
assets the list names keep their order and their slots theirs, a slot whose object is gone is dropped, one that moved takes
its object's place, an object of a listed asset that is not listed yet is added at the end; the report says how many
(`slot list`), and a vanilla island's `.a7minfo` is written when it changed. The Checks tab reads the standing slots against
the listed ones per asset (`EditSession.slot_rows`; island JSON `slot_list`): a difference is a readout, since the build
lists what stands. **Deposits** are not part of an island's files: the island's stock of a resource is drawn by the map
generator from the slot list and the settings (island-slots "2070's rules", "117's slots"), so there is nothing on the
island to edit but the slots themselves; the editor shows what stands. Edit > Slot list (`desktopslots`, WP2f) lists the slots
by asset: what the file lists, what stands after the edits, what the build will list and what the map generator reads of
the asset (`MapGeneratorSlotCount` of them, or all with `ActivateAll`; the island JSON's `slot_generator`, read from the
mod's asset first, then the game's, base assets followed: `AssetModels.value_of`).

**The Slots tab** (`desktopslotstab`, `slotkinds`; built 2026-11-18, not opened in the game's window yet; tests on a synthetic
asset list and a fake window). The objects list has two tabs, objects and slots. Slots lists the island's standing slot
objects (not deleted ones) with kind (the asset's `SlotType`, else its name), name, GUID and position; the search takes any
part of kind, name, GUID or template, the kind list narrows to one kind, and a click selects the slot and flies there as a
click in the objects list does. A slot is an object whose asset has the template `Slot`, `Slot_Marsh`, `WorkAreaSlot` or
`WorkAreaRiverSlot` (`slotkinds.read_assets`, the mod's assets over the game's; `IslandEditor.slot_assets`, once per
island). Games: Anno 117 and Anno 1800 islands have it. 2070, 1404 and 1701 islands (an `.isd` / `.isc` file) keep their
slots as objects of resource templates the editor's baseline does not read, and a 2205 session map's slots
(`CSlotManager`) are not read yet, so their tab says the editor lists no slots for that game. Beside the list sits the
button Slot links, the slot linker of the port between 117 and 1800 (island-regions "Slots between the games"): a dialog
with a row per slot asset of the open island, the target game's asset to choose (the rules' suggestion preselected, "left
out" among them), and the choices kept with the project.

**Herd areas** (`islands/herdareas`). `AnimalManager/HerdGrid` is a bit per tile where herds may walk, `HerdAreas` one entry
per connected region of it (four-neighbour) carrying the region's tile count (`CellsCount`; the rest of an entry, `BBox` and
`RandomPoint`, is run-time state and zero in the shipped islands). The edit file keeps `herd` (`"x,z"` -> 1 or 0, only what
differs from the island); the build sets the tiles on land that is no river (the island build's own rule, `WorldManager/Water`
and `RiverGrid`; others are `unmatched`), draws the entries again from the grid (`herdareas.entries`, which the island build
shares, so a built island's areas and an edited one's follow one rule) and drops a patch under 40 tiles, saying how
many tiles it dropped. An island with no entry to copy takes the island build's `herd_template`, else the edit is unmatched. The
Grid tab's Herd area group (`cellbrush.HerdEdits`, the active map's cell brush) paints the grid. Which animal walks where is
not stored with the grid (island-build "Objects placed and left out"): the areas are the island's one roaming ground.

**Map pictures** (`islands/mappictures`). The four pictures of an island (`mapimage`, `gamemapimage`, `provincemapimage`,
`activemapimage`, each PNG and a BC7 `_0.dds` beside it; island-build "Map pictures") are redrawn after a height or a
paint edit of a vanilla 117 or 1800 island (`groundedit.apply`, the `pictures` of its report; a built island draws them
from its final heights and strokes in any case). Each is read at its own size and laid over its
tiles (`mapimage` the grid, the others the active rectangle; the margin pixels of vanilla's, one or three, are not
modelled: the picture is stretched over the rectangle, an offset of under a tile). The province picture is data (slope,
height and coast distance of the heights) and is made again by the formula the island build uses. The colour pictures keep
their colours: the hill shade of the old heights is divided out, the strokes are laid over what is left, the new hill shade
is multiplied in, the alpha and the sea follow the new land, and land that grew out of the sea takes the colour of the
nearest land one pixel inside the old coast. A stroke is laid as the island build lays it (`islandground.stroke_cover`:
the stroke's round soft brush times its strength, the colour blended by that cover) in the mean colour of the material's
diffuse map (`terrain2070.material_colour`, decoded in process); the colour of a material comes from the island's
material set (`materialorder.set_files`: ID to the material file) or, for a 117 material the palette added, from the edit
file's `materials`. A stroke whose material has no colour (a set entry with no diffuse map) is left out of the pictures
and only the ground shows it. An underwater island (its highest ground under the sea) is redrawn in 2070's underwater look, its land counted from below its plateau (`mappictures.underwater_floor`). Not redrawn: the pictures of an island with none. The `_0.dds`
needs the texture island build; without it the PNG is written and the stale `_0.dds` stays, with a warning.

**Session settings** (`islands/sessionsettings`). `GameSessionManager/SessionSettings` holds `GlobalAmbientName` (UTF-16, the
ambient light preset: `amb_roman_01`, `amb_celtic_01`; rendering/lighting 6), `VegetationPropSetName` (plain text: the set the
vegetation props are drawn from) and `PlayableArea` (four int32 tiles x0, z0, x1, z1). The edit file keeps `session`
(`ambient`, `vegetation`, `playable_area`); the build writes the values in the attribute's own form, and a value the game
would not take (an empty name, an area outside the grid or with its corners out of order) or a setting the island has no
attribute for is `unmatched`. `EditSession.session_value` and `set_session` (one undo step; a value equal to the island's own
takes the edit away) are what a form calls; the Checks tab names a bad value. Light objects themselves place from the
library as models. Edit > Island properties has a Session tab (`islandprops._session_section`, the same
`Section` / `Field` form as the island's other settings, written by `islandprops.set_values` into `edits["session"]`):
the ambient light is one of the game's ambient settings (the stems of `config/engine/ambientsettings/*.xml`, the mod's own
included: `sessionsettings.ambient_names`; 101 files in the game, the 46 `amb_` ones first, 6 of them used by the sampled pool islands), the vegetation set one of
the `PropSet` names of `prop_sets.xml` (`vegetation_names`), the playable area four whole tiles. A name the lists do not
hold is kept as typed (a mod's set the editor cannot see); none is checked to load. In all 80 pool islands sampled the area
is the whole grid, so the form starts from the island's own numbers.

**Area kinds** (`islandedits.apply_area`). The `area` edit takes, besides 1 (the construction area) and 0 (none), the id of
another area the island has (a continental island's 0x4001 and the like, island-build "The island's areas"): a tile of
the construction area or of no area, or of another of the island's areas, takes it; an id the island does not have is never
made up and a tile of another area is still left alone by 0 and 1. The brush paints 0 and 1; the session reads an edit that
names another id as no construction area (`AreaEdits._cast`). 145 shipped islands carry, besides 0 (outside), 1 (none) and
0x2001 (construction), the ids 0x4001, 0x6001 ... 0xE001 (the DLC's continental island and the Egyptian quadrants and valley
layers hold one to six of them, from a few to 118,000 tiles). The Grid tab's Construction area group paints them (WP2f): the
island JSON's `area_grid` lists the kinds the island has and their cells (`kinds`: `ids`, and `map` the zlib-packed index
grid), the Area id box names the id the brush paints, `AreaEdits.begin` takes a number above 1 as that id. A tile
takes a kind when it is none, construction or another kind; a tile that is a kind in the island cannot become none or
construction (the build leaves it alone, `apply_area`), and one the edits painted gets its own state back by painting that
state again; the view outlines each kind in a colour of its own.

The 117 build applies the `area` edits once, in the objects step; the ground step does not take them, so what matches
nothing is listed once.

**Tint pages** (`islands/tintpages`, WP2f; documented here from a vanilla island's pages before it was built). A page is
`_gamedata/<island>/tintmaps/<level>/<x>x<z>.dds`, BC1, 512 x 512, one mip; the node of level L covers `16 << L` tiles
(`roman_island_medium_03`, 320 tiles: levels 0 to 5 with 20, 10, 5, 3, 2 and 1 nodes a side, 233 + 66 + 20 + 7 + 3 + 1
pages, the `Flags` of `TintMapLevels` naming the nodes that have one). Measured on that island (`out/wp2f` probes, not kept):
the pages tile the island without overlap (the step across a page border, 0.88 levels, is that between neighbouring texels
inside a page, 0.2 to 0.4, and no border texels repeat in the neighbour); row r of a page is z = z0 + (r + 0.5) / 512 x span
(the page's green channel correlates 0.26 with the coarse land mask this way round against 0.18 to 0.23 any other way
round: a fit, not the shader's word); a coarser level is not the downsampled finer one (mean difference 3 to 5 levels on
pages whose texels vary by 3 to 9), so the pages of every level are independent images and a stroke is laid into every
level it reaches. A texel is stored as YCC (`r` Cb, `g` Y, `b` Cr; rendering/terrain-materials 4) and the shader
multiplies the albedo by `(2 x rgb)^2.2` of the decoded colour: a stroke names the factor it multiplies the albedo with
(1 leaves it, below 1 darkens), the page's texel takes `rgb x factor^(1/2.2)` (the factor blended in by the stroke's cover,
`islandground.stroke_cover`: the round soft brush times its strength), and the colour goes back to YCC (the inverse of the
signed square is `u = sign(v) sqrt|v|`). Only the 4 x 4 blocks a stroke changes are encoded again (`models/bc1encode`), the
rest of a page keeps its bytes. The edit file keeps `tint`: `[{"color": [fr, fg, fb], "radius", "strength", "points":
[[x, z], ...]}]`; the vanilla island build (`groundedit.apply`) and the built island's build lay them on the pages
that exist (a stroke that reaches none is `unmatched`: a node without a page has no tint at all). The Terrain tab's Tint
group holds the brush (`tintbrush.TintEdits`, the cell tool's grid `tint`, one undo step a stroke; the Tint box offers six
factors: darker, lighter, warmer, cooler, greener, drier; the Brush group's radius and strength); the 3D view
shows the dabs as points in the stroke's colour, not the tinted ground (the look of the game's own shaders reads the pages
the build writes, so the tint shows after a build). Left: a live preview in that look.
**An own material set** (WP2f). A vanilla island names a material set shared with many islands
(`Terrain/MaterialSetFileName`), so a material the palette added (`edits["materials"]`: ID -> `file`, `set`) cannot be
written into that set: the set is shared, and the game would not draw an ID the island's set lacks. The vanilla build
(`editor/formats/a7m/edit117.ground`, also the 1800 build) writes a set of the island's own beside the island, `<name>_materialset.cfg`
(`own_set_path`), made from the island's set text with the added materials' entries put in at their IDs, each in its
family's place of the draw order (`terrain2070.compose_set`, the one writer the built island's set
`island_material_set` shares), and names it in the island's `MaterialSetFileName` (the slashes as the file had them). The
paint step then orders its layers by that set and the map pictures take the added materials' colours from it. The island's
other files and every other island keep the shared set. The report says `material set`. **The other game's
materials**: a vanilla 117 island's palette lists 1800's ground materials and an 1800 island's 117's
when the other install is set; the build brings a picked one in as files of the island's own game below
`data/imported/` (`othermaterials`, island-regions "The other game's materials in a palette"). Their swatches and
their look in the editor's view: the entries name their textures at those paths and the two editors read
them through the other game's files (`othermaterials.OtherMaterials`); the gallery groups them under their game.
Left: their look in the game-shader view (island-regions lists what it needs).

**The donor ground of a blank 1404 or 1701 island.** A new island of these games is one of the game's own, cleared:
without a further step it would keep the donor island's painting. `App.open_blank` lays one material over it, the donor's most common
(`blankisland.dominant_material` from the ground's weight layers, checked on `n_l01` of 1404: texture 2, and `n_g01` of
1701: texture 82), by an ordinary paint stroke in the edit file (`blankisland.plain_ground`: a serpentine line of dabs of
32 tiles' radius a quarter radius apart, which covers every tile at least nine tenths with the soft brush): 1404 blends that
texture over every chunk layer (`Island2070.apply_paint`), 1701 gives each tile the texture where the stroke covers at
least `PAINT_COVER`. A first open only: an edit file that has paint keeps it, and 1800's blank islands keep their ground (their
vanilla ground is the game's own look). The mapper changes the plain ground with the paint tool from there. The result of
the paint is the same as one by hand; it has not been looked at in the game.

### Ground edits of vanilla islands

**The rule: one height field, pages generated last.** Every writer of an island's terrain - the
island build of a built island (hand edits included, a new blank island too) and these ground edits - shapes
only the heights and their protected areas; the height pages are then encoded once from that field through one
encoder (`islands/terrainbuild.py`: `build_terrain` for a whole island, `encode_page` for a page). A brush never
edits a page: it changes the coarse map, and each page the edit reaches is re-encoded from the page's own surface
(the coarse map with the island's detail, the residual) plus the edit (`terrainbuild.page_field`, each level
answered with the page's own rebuilt level there). Ground the edit leaves alone keeps its codes; edited ground lands
within one height code of the old surface plus the edit. For a built island the same heights give the same
page bytes either way (`tests/test_terrainbuild.py`). Still incremental: the stored LOD meshes and the node
bounds of a vanilla island, which are raised in place (below).

A vanilla island (117, 1800) carries render data the island build writes for its own islands; `groundedit` puts an
edit into it with the island build's writers and rewrites only what the edit reaches, so an island without a ground
edit keeps every byte and the game reads its own files for everything else.

- **Heights** (the corner deltas, world units): added to rd3d's `CoarseHeightMap` (1/256 unit) and the game's
  `TerrainManager/HeightMap` (1/128 unit), bilinear between the corners. Every height page (`normalmaps/<level>/
  <x>x<z>.ctt`, 16 x 2^level tiles, border included) under the edit is re-encoded through the island build's
  encoder (`terrainbuild.encode_page`) so that the game's rebuild (rendering/terrain-geometry 1.4) lands on the
  page's old surface plus the edit - vanilla's detail stays (the rule above). The stored meshes (`meshes/<level>/`,
  LOD 2 and up; 32 x 32 nodes a file) keep their vertices: each is raised by the edit at its place and lit by the new
  slope, so neighbouring nodes still meet; a new hill between the vertices of a coarse level is flattened there.
  `MinYArray` / `MaxYArray` of the nodes under it only widen; a tile the edit lifts out of the sea or sinks into it
  changes its land bit (`WorldManager/Water`, the island build's rule: the mean corner over 0.0625); the sea's depth
  byte (`WaterInfos`) of the cells under it is written as the island build writes it. Not changed: the island area
  (`AreaIDs`), the coarse displacement, tint pages and the far map pictures; objects keep their own heights.
- **Paint** (strokes of the island's own materials; a stroke of another material is listed as unmatched): every
  material map the island has - 117's four levels in `_gamedata/<name>/materialmaps/` and rd3d's copy, 1800's in
  rd3d - is read as the game lays it (a material's alphas summed over the slots, the materials in the set's list
  order, rendering/terrain-materials 1.4: `groundedit.layered_weights`, over `islandground.slot_weights`, which is
  also how the editor's own ground look reads the maps, `IslandView.weights`; it read them in slot order until
  2026-10-27 and showed painted ground wrongly: the dominant material differed on 31 % of the texels of
  roman_island_medium_03 and 39 % of moderate_3rdparty06_01), the strokes are painted over it
  (`islandground.paint_strokes`), and the island build's encoder (`GroundMixin.encode_materials`) writes the ID blocks
  the strokes touch; the rest keep their bits. The painted materials' bits are set in the used-material masks (rd3d
  and the mesh nodes under the strokes).
- **Rivers and lakes**: the island's own River and Lake editor objects, numbered in the island's order
  (`groundedit.water_network`), are what the water tool edits; an edited river's curve is written through the
  island build's `islandwater.fill_spline` (its points, widths, the handles between them, each point flowing as the old
  point nearest to it); a lake's level moves every point of its outline. The gameplay `RiverGrid` stays the island's
  (on roman_island_medium_03 it holds 126 tiles where the ribbons cover 373; on moderate_3rdparty06_01 none), and the
  ground under a moved river is not carved: shape it with the height brush.

### Other games

The settings also hold Anno 1404, 1701, 1800 and 2205 (each optional; `othergames.installs.GAMES` tells an install
by its executable). Each game carries its engine generation, the studio's own numbering (1701 `anno3`, 1404 `anno4`,
2070 `Anno5`, 2205 `Anno6`, 1800 `Anno7`, 117 8), and with it its family: the old engine (3-5: RDA up to V2.0, islands
as tagged text) or the new one (6-8: RDA V2.2, level data in FileDB, `docs/formats/filedb.md`); the files say which
they are too (the RDA magic, the FileDB tail). The readers are split along that line; a game whose islands no editor
reads yet is still listed, so the start page can say so.
Archives are read in each game's own order (`SourceTree.from_archives`): 1404 its base game, then the add-on, then
the History Edition's archive; 1800 and 2205 `data0..N` in number order; 1701 by its own rule (the newer copy of a
file wins). Their islands are edited into edit files of their own and built back (below).

**1404 and 1701 builds** (`oldenginebuild`; engine-notes "Anno 1404 and 1701 archives"): 1404 has no patch
archive, so "Build & install" rewrites the last `data` archive of the folder the island comes from
(`maindata/data5.rda` or `addon/data5.rda`) with its own files and the islands, after copying the game's archive
into the workspace's `old-engine-originals/` (again whenever the game's file is not ours: an update replaced it).
1701 gets a new `data<n>.rda` (the next number the game would mount) whose files are stamped past the originals'.
The editor always reads the game's own files (our archive is left out of its tree), so a second build never
applies the heights twice; "Restore the game's own archives" puts 1404's back and removes 1701's. "Build" alone
writes into the workspace's `anno1404-archives/` and `anno1701-archives/`.

- **1404** (`gameviewers.IsdViewer`): 2070's `.isd` reader reads its islands as they are, so the view has the
  terrain, the ground in `SplatID` order, rivers and lakes, and every object with the model its asset's variation
  names: a `.cfg` in 2070's format whose meshes are Granny `.gr2` files (`formats/grannyread`,
  [`formats/granny.md`](formats/granny.md); `editor.grannyviewer` over `models.modelread`, which reads 2070's and 1404's configs alike). A model's
  parts are its models, instancing models (tinted: grass, trunks) and cloths, one part per material group of the
  `.gr2` with the textures the config gives that material (`<stem>_0.dds` for the `.png` it names), its nested
  configs, and its trees' leaf cards drawn as 2070's banner leaves; what a VISIBILITY_TRANSFORM shows only in a
  state is left out. Each mesh is decoded once into the workspace cache (0.05 to 1.2 s a file in Python). Checked
  2026-10-27 (`scripts/editor_shots.py`, offscreen): `n_l01` (20,026 objects, 58 models, all drawn: firs,
  leaf trees, bushes, grass, rocks, cliffs), `bm_occident_large01` (a whole city: 8,862 entries, 180 models), and
  every model of the 436 islands that open (1,180,633 of 1,186,531 objects with a model; 694 models, 1,319 parts,
  none failed); markers stay for 5 configs that name no mesh (effects). A config whose `.gr2` cannot be read (missing,
  an unknown magic or compression) is a marker, a mesh that cannot be read is left out of a model that draws, and
  both are listed in the Log. 35 islands (campaign, test and audio islands) stop on objects without a position
  in Anno 2070 editing's object list.
- **1800** (`a7mviewer.A7mViewer` over `editor.formats.a7m.reader`; looked at 2026-10-27 on `moderate_3rdparty06_01`):
  heights, the ground from the island's material maps, the sea, the island's rivers and lakes (the Bezier splines
  of its editor objects, as ribbons; they are also what the water tool edits, `groundedit.water_network`),
  its props (`PropGrid`: 1,848 on that island - trees, bushes, underwater plants, lamps, crates; each a `.prp` file
  naming a mesh and its maps, read as a model config by `editor/formats/a7m/reader.prop_parts`, and those placed more than 200
  times are the grass layer) and its objects with the model their asset names (1,208 objects, 3,056 entries with the
  props, 185 models in the view). An object is listed by its asset's `Name` (`assetmodels.AssetModels` with `ASSETS_1800`); a Prop
  asset placed as an object (lamps, furniture: 90 there) draws its `.prp`; the rest without a model (audio spots,
  position markers, rivers, the coast: 18 there) are markers. An 1800 island's own
  trees are props (its `TreeMap` of 293 tiles is regrowth, not drawn), so there is no forest tool. 117's rules for
  the game's look (`game_*` in the Rendering menu) are not 1800's and stay off: the editor look draws it.
  1800's islands are 117's island format one engine generation earlier - the same managers, object schema, bit
  grids and material packing, in any of the three FileDB layouts - with three differences: the ground material
  maps sit in `rd3d.data` (`MaterialIDs2`, `MaterialAlphas2`) instead of `_gamedata/<name>/materialmaps/`, an Old
  World island names no material set (the engine's default `terra_01.xml` applies), and the material map has 2
  texels per tile. `Island1800` is an `islandview.IslandView` with those handled, so the view's material, weight and
  render methods work unchanged and it opens a 117 island the same way.
  **Editing and the build** (`editor/formats/a7m/edit1800`): objects move, turn, scale, are deleted and copied (Ctrl+D, or Place
  from the library "Anno 1800": the models the island carries and the game's own decoration,
  the asset list's rows of the templates in `ASSETS_1800.library` (`placeables`) - its 540 `VisualObject` and 220 `Prop` assets, each with its first model; a placed
  one is an `added_objects` entry with its `guid`, `cfg` and `template`, written as a clone of an object of the same
  asset, else of the same template, else, for that decoration, of a plain placed object of the island, with an ID
  of its own); props move, turn, scale and are deleted or copied. Objects of the `NaturePreset` and `Prop` groups
  edit as the others (`object_groups`); a `Prop`-group object (54 of the 301 islands carry them: fences, lamps)
  keeps its place, turn and a scale per axis in its `Prop` block, which the editor reads and the build writes
  (`islandedits.set_object`). Heights, paint, rivers and lakes are written as on a 117 island ([Ground edits of
  vanilla islands](#ground-edits-of-vanilla-islands)), into rd3d's material maps; the palette is the island's own
  set (an Old World island's `terra_01.xml`, IDs over 31 left out: the maps store 5 bits). **A new blank island**
  ("New blank island" on a Native island) is the vanilla island cleared, not a new shape: its edit file says
  `"clear": true`, the build takes every object of the `GameObject`, `NaturePreset` and `Prop` groups and every
  prop off (`editor/formats/a7m/edit117.clear`; the coast lines, rivers and markers stay) before the edits, and the editor shows
  it so; its terrain, ground and water are the vanilla island's, for the ground tools to reshape. The route of
  117's new islands (`isd2070.blank_island`, made from 2070's island form) builds a 117 island, not an 1800
  one. An object is named in the
  edit file as 117's are (`guid:<asset>` and its position, `islandedits.key`; 1800's objects carry no model config
  of their own), a prop by its `.prp` path. "Build" writes every island with an edit file (`1800_<island>.json`,
  `source` and `game: anno1800`) through `IslandFile1800`, in the layout it was read in, into the workspace's
  `anno1800-islands/data/<the island's path>` with a `modinfo.json`; "Build & install" copies that mod into the
  folder the setting "Anno 1800 mods folder" names (nothing is guessed, nothing written into the game's own folder
  unless the setting points there). An edit whose object the island no longer has is listed as unmatched in the
  Log, not dropped. An object that stores no `Mesh/Scale` or `Mesh/Orientation` (1800's at their defaults) gets one
  when an edit sets another value (`islandedits.set_object`).
- **1701** (`gameviewers.Isd1701Viewer` over `othergames.isd1701`): heights, the ground from the palette's textures
  (one per tile, blended where tiles meet; a texture is assumed to cover one tile), coast lines and rivers, the
  objects with their models: an object's GUID names an `.ocf` (one `.mcf` per variation), the `.mcf` a tree of
  mesh configs (Granny `.gr2` meshes, nested `.mcf`, vegetation, cloth) with offsets, turns, scales and its
  materials' `.tga` textures by name ([`formats/granny.md`](formats/granny.md) "Model configs";
  `othergames.grannymodels.read_1701`); the nearest level of detail is drawn, what a condition switches on
  (damaged) is not. 1701 counts 32 world units a tile: heights and models are scaled to the view's one unit a
  tile. Checked 2026-10-27 offscreen on `mis01_aztec_01`, `aztec_m01` (the Aztec village, animals, rocks) and
  `n_g01`, and every model of all 407 islands read: 57,955 of 64,961 objects with a model, 562 models (1,505
  parts) all drawn; markers stay for objects without a config (6,262 collision-test dummies, 508 waterfalls, the
  plateaus) and 5 configs of effects only (waterfall particles, volcano smoke).
- **2205** (`a7mviewer.A6mViewer` over `editor.formats.a7m.session`; looked at 2026-10-27 on `session_moon_01`,
  `session_earth_02`, `session_arctic_01`, `session_earth_06` and `portrait_bot`, and every one of the 24 maps read):
  a whole session map (1024 tiles, read at one height sample per tile), its heights, the ground from its material
  maps (composited in the material set's order, as the game bakes it; the set's ground-object layers left out), the
  sea (a plane at 0, not drawn when the session's `HasWater` is off: the Moon maps, the portrait scene), its rivers
  and lakes (the Bezier paths of its `River` and `Lake` editor objects, ribbons as 1800's: `session_earth_02` has one
  river and two lakes), its props (`PropGrid`: 53,515 on that map, 124,347 on `session_earth_06`, each a `.prp` file
  read as 1800's by `editor/formats/a7m/reader.prop_parts`; those placed more than 200 times are the grass layer) and the objects
  of its `GameObject`, `EditorObject` and `NaturePreset` lists with the model of their asset's variation
  (`assetmodels.AssetModels.read` with `ASSETS_2205` reads `assets.xml` and the files it includes, the DLCs' too). The object list ("On the
  island") names the editor folder each object sits in (`folder 1 / 7`: the ids from the top of the folder tree,
  `ObjectGroupFilterCollection`; the file stores no folder name). Drawn: `session_moon_01` 1,067 objects (1,057 with a
  model) and 1,323 props, 2,390 entries in 91 models; `session_earth_02` 1,842 objects (1,818) and 53,515 props,
  55,357 entries in 166 models, 131 folders; `session_arctic_01` 1,547 objects (1,511) and 3,209 props, 4,756 entries
  in 147 models; `session_earth_06` 126,318 entries in 156 models, 28 s from start to a drawn picture. Markers are left
  for objects whose model is the developers' placeholder (not shipped) or whose variation is past the asset's list; a
  few configs draw only decals, particles or lights. 117's rules for the game's look (`game_*`) are not 2205's and
  stay off. `portrait_bot` (a 64-tile scene without render heights, water or the material set it names) opens flat
  with the shipped `moon_01` ground, its one object a marker.
  Compared with 1800 (same engine family, one generation earlier): the objects are one flat list per group in
  `gamedata.data` with fixed-point positions, a `Direction` and a `Variation` index into the asset's model list, not
  per-area documents with float positions and `Mesh/Orientation` (`docs/formats/anno2205-map-format.md`); props are
  items with a `FileName` each, not `FileNames` plus an index; the ground ids sit per 4 x 4 material texels, not
  2 x 2; `CoarseHeightMap` is the same.
  **Editing and the build** (`editor/formats/a7m/edit2205`, `othergames.index2205`): objects and props move, turn, scale and are
  deleted; an object is copied (Ctrl+D, or Place from "Anno 2205" in Object Groups: a clone of the first object of its
  asset with an ID of its own, `<serial><kind>` with the next serial of that kind), a prop too. The edit file is
  `2205_<session>.json` (`source`, `game: anno2205`; an object named by `guid:<asset>` and its place, a second one at
  the same place `#2`). Heights, paint, rivers and lakes are edited and written (below); forest, blockers and the active and surf maps are off. The question was whether 2205 can
  load a changed session at all: it has **no mod folder and a loose `.a6m` is not read** for a session the game
  knows, because the game finds every file, a map's members (`<map>.a6m|gamedata.data`) included, through the
  archive index `maindata/file.db`, and a path that names a member never goes to a loose file, with or without
  `PreferLocalFiles` (`docs/formats/anno2205-map-format.md`, "How the game finds a map"). What the format allows is
  an archive of our own added to the index's list with the entries of the changed members pointing into it.
  "Build" does that into the workspace's `anno2205-islands/maindata/` (`anno-toolbox-sessions.rda`, the changed
  `gamedata.data` / `rd3d.data` of every session with an edit file, level 1 zlib as the game's archives hold them,
  and the patched `file.db`); "Build & install" puts the archive into the folder the setting "Anno 2205
  folder to patch" names - empty until set by hand, nothing is guessed - and points that install's index at it
  through the delivery the UI editor shares (`othergames/delivery2205`, island-editor-tools "Build, install,
  restore"): the game's own `file.db` is kept outside the workspace first, so a rebuild always starts from it, and
  "Restore the game's own file.db" (shown on the 2205 tab while ours is installed) removes the archive and leaves
  the index pointing at the UI's archive only when that is installed, the game's own otherwise. An edit whose object
  the session no longer has is listed as unmatched in the Log, a deleted object a label, object group or snap pair
  still names is listed as "still referenced" (it is deleted all the same). Checked 2026-10-27: all 24 maps give both
  members back byte for byte (`filedb.read` and `write` with the file's own name ids); the real `file.db` (82.9 MB)
  comes back byte for byte; on the installed game `session_moon_01` with one object moved, one deleted, one copied
  and a prop moved and one deleted built in 9 s, its new `gamedata.data` (6.3 MB) and `rd3d.data` read back at the
  positions the new index gives (seek, inflate: 10,967,592 and 249,298,222 bytes), 1,067 objects with unique IDs and
  1,322 props, the other entries of the index unchanged; `tests/test_island2205edit.py`. **Ground edits**
  (`editor/formats/a7m/sessionground`, formats/anno2205-map-format "Writing a map"): heights (the coarse map, the game's own heights,
  the tile heights, land bits, the sea's depth byte and the nine stored mesh levels), paint (the 4 x 4 material ids and the
  alphas, the used-material bits of the nodes) and rivers and lakes (fixed-point object positions); on `session_earth_02` a
  hill, a pit, a painted stroke and a lake level wrote in 0.7 s and read back with exactly those attributes changed
  (`tests/test_session2205ground.py`, synthetic and on the installed game). Not done: the game was
  never started with it, so whether it reads the extra archive and the repointed members is read from its code,
  not seen; the normal-map pages of an edited hill are rewritten from the slope of the edit (`editor/formats/a7m/sessionpages`: the shading follows, the detail a page had stays); no forest edits (trees are props). The game's own assets place from the Buildings dock
  ([Placing from the list](#placing-from-the-list)).

### What the other games' editing covers, and what is still open

**Built and tested:**

- 2205: heights, paint, rivers and lakes (`editor/formats/a7m/sessionground`), and the normal-map pages after a height
  edit (`editor/formats/a7m/sessionpages`, `models/bc1encode`). There are no blank 2205 islands: its sessions are baked
  sector maps.
- 1701: rivers, coast lines and properties (`editor/formats/isc/water`, `.isc` written by the build), and the river
  blocking lists redrawn (`editor/formats/isc/blockings`).
- Blank 1404 and 1701 islands (`App.open_blank`), with one plain ground, and the spray for 1404 and 1701.
- The tool table.
- The rows that needed no game: coast lines of 117, 1800 and 2205 (the window's coast gizmo works on them), lake
  outlines and the ford cells (see the rows above).
- The window's ford brush and overlay (the Grid tab's Ford group, on the active map's cell tool: `cellbrush`,
  `desktopgrids`), and point handles for the lake outlines (the water tool, `EditSession.move_lake_outline_point`).
- The environment grid. Its values are the game's `EnvironmentType` (the status row above carries the evidence), so the
  Grid tab's Environment group paints a zone (`cellbrush.ValueEdits`, `islandgrids.apply_environment`). The active map,
  ford, surf and environment brushes are one shared brush.
- The blocker shapes and the construction area (see the Blocker shapes and Construction area paragraphs of "Anno 2070
  editing").
- The slot list, the herd areas, the map pictures after a height edit, the session settings and the area ids ("Slots,
  herd areas, map pictures and session settings").
- The Slot list dialog, the Session tab with the game's ambient and vegetation names, the brush for the other area
  ids, the map pictures after a paint edit, tint pages, and an own material set for a vanilla island (each documented
  above).

The pictures of this work are in `out/agent-wp2/` (the editor's look, offscreen): `2205-before` and `2205-after` of
`session_earth_02` with a hill, a pit, a painted stroke and a lake level raised, and `blank-1404` of `n_l01` and
`blank-1701` of `n_g01` with their objects, rivers and lakes taken off. The audit is recorded in
`audit-2026-11.md`.

**Still open**, in the order of value:

- the river grid (the rule is not found: it does not follow the ribbon, see the row above)
- the air blocker grid (absent from the shipped islands that were checked, so there is nothing to read or name)
- fog (3 h; the scales are not settled)
- the flow brush (3 h; the vector scale is not settled: it is a byte pair whose scale no file or shader that was read
  gives)
- the picker of the other games' materials
- a live preview of the tint in the look of the game's own shaders
- the unnamed rules (they cannot be settled without the game or a reading of the executable)

**In-game checks:**

- A ford laid with the Grid tab's Lay ford can be crossed, and an erased one cannot.
- A zone painted with Paint zone changes the ambience there (the ambience may need a new game).
- A dragged lake outline point loads as the new shore (the ground is not carved).
- A 2205 session with a hill, a pit, a painted stroke and a lake level loads and shows them, and the ground of the hill
  is shaded by its slope (the pages go into our archive: the index must point at them).
- A blank 1404 and a blank 1701 island load and can be built on. They have one plain ground, not the donor's painting.
- A moved 1701 river and a changed clime load, and units cross or are held by the river as its new course says (the
  blocking lists are a fit, not the game's rule).
- A moved coast line and a ford on a 117 island load.
- A built island built with a moved build blocker shape or with Construction area strokes lets you build on exactly
  the tiles the build mode outlines (the outline follows `AreaIDs`; no game run yet).
- A moved coast line of a built island (`a2070_<island>.json` with `coast`) puts the harbour where the line was
  moved to.
- A vanilla 117 island with a slot moved, deleted or added lets the generator offer the slots where they stand (the
  `.a7minfo` list follows the objects).
- A herd area painted in the Grid tab is roamed by the herds, and a removed one is not.
- A vanilla island with a hill or a pit shows it on the minimap, the island browser and the strategy map's picture
  (the `_0.dds` needs the texture island build).
- An island with another `GlobalAmbientName` is lit by that ambient. An island with another ambient light or
  vegetation set chosen in Edit > Island properties > Session loads with it.
- A painted stroke on a vanilla island shows in its minimap and strategy-map picture in the material's colour.
- A tint stroke (Terrain tab, Tint) tints the ground in the island's tint pages after a build. The levels are written
  alike, so it should hold at every distance; the page orientation is a fit.
- A material that the palette added is drawn on a vanilla island from the set of its own
  (`<name>_materialset.cfg`).
- The Build area's Area id brush on an island with other area ids (a continental island, an Egyptian quadrant) lets
  you build on the tiles it paints.

Note: this work was built on 2026-11-12 and 2026-11-13.

## Language

The island editor's windows are in English or German. Choose the language in the settings (Games and folders,
"Language"); it applies the next time the app starts. Game data (asset and file names, the games' own texts) is shown
as the game has it. See [the suite's guide](suite.md#language).
