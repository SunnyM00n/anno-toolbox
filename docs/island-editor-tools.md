# Island editor: the tools, the checks and the edit file

This page says what you can do in the editor: the tools, the checks, the edit file the island build applies and the road
piece editor.

Part of [Island editor](island-editor.md).

## Tools

Every change goes through the edit document (`editsession.EditSession`) and can be undone. Each change keeps how to
take itself back, in the edit file and in the view.

**Objects**: select an object, prop or marker, then use these keys:

- W moves it.
- E turns it. E again tilts it about the model's own two horizontal axes, one at a time (there is no free rotation
  sphere, because it tilts models by accident).
- R scales it. A prop may stretch along one axis, as a waterfall does to reach its pool.
- Del deletes, Ctrl+D duplicates (two tiles off), Esc deselects and F focuses.

You can also type the place, turn, tilts and scale into Properties. An object is named in the edit file by its model
and the position the build gave it (`islandedits.key`). An object without a model config of its own (a slot, a
herd, a marker) is named by its asset, `guid:<GUID>|x|y|z`.

**Heights** (H; S back to Select): raise, lower, flatten (to the height where the stroke began) or smooth, with a
radius in tiles and a strength. You paint with the right button, and a ring on the ground shows the brush. The edit
file keeps the change per tile corner. The build adds it to the corner heights right after the builder has computed
them and before anything is generated from them (heightmaps, height pages, LOD meshes, water maps), so the island is
built from the hand-shaped ground. Objects keep their own height; move them to follow.

**Paint** (P): the Material Gallery shows the island's material set (n_l03: the 30 ported 2070 ground textures) and
117's own ground materials, the 90 distinct ones of its 16 shipped material sets (`terrain2070.vanilla_materials`).
They are grouped by the game they come from (newest first; another game's island shows its own), and each group is in
its draw order, bottom layer first. Pick one and paint. The edit file keeps whole strokes (material, radius, strength,
the points), so a long stroke is a few points, not thousands of texels.

The build hands the strokes to the builder (`islandedits.apply_paint`), whose material weights lay them over the
composited 2070 layers with a soft round brush, in painting order (`islandground.paint_strokes`). The line between a
stroke's points is filled in at a quarter radius (`islandground.stroke_cover`), and the editor's preview does the
same. Without this, a fast drag, which records points far apart, would come out as a row of dots.

An island's set holds 32 materials (the material maps store IDs in 5 bits), and 2070's take 30. The first pick of a
117 material therefore gives it a free ID, and the edit file records it (`"materials"`). The build then writes the
island a set of its own, `a2070_materialset_<island>.cfg`: the 2070 set with each added material's entry copied from
its vanilla set (`terrain2070.island_material_set`). A painted material that the island's 2070 layers do not use gets
a weight layer of its own (`islandground.painted_layers`).

*Layer order between games*: 117 draws an island's materials in its material set's list order
(rendering/terrain-materials 1.4), 2070 a chunk's layers in ascending `SplatID` (rendering-2070 2.1); the island build
carries that into the weights chunk by chunk. Between games there is no shared scale, so a material brought in from
another game is placed by its family (`materialorder`): its name gives water, sand, soil, grass or rock, ranked as
2070's SplatIDs rank them (water 1; beach 770-780; grass and earth 800-825; mountain 840-870), and it goes into the
island's list before the first of the island's own materials of a higher family. A 117 grass painted on a converted
island therefore lies over the island's sand and water and under its rock, as both games draw grass under rock; the
island's own materials never move. The build writes the island's set in that order (`terrain2070.material_draw_order`)
and encodes the material maps' opacities in it (`IslandBuilder.material_rank`); with no added material both are as
before, byte for byte. Not yet seen in the game.

**Rivers and lakes** (L): the tool shows the river network as the island build computed it, from `<island>.water.json`
beside the built island. Each river is a ribbon along its centreline with a handle per point, and each lake is a
handle at its level. On a vanilla 117 or 1800 island it shows the island's own rivers and lakes ([Ground edits of
vanilla islands](island-editor.md#ground-edits-of-vanilla-islands)). Click a handle and drag the gizmo: a river point
moves freely, its height being the water level there, and a lake moves only up and down. Properties sets a point's
width or a lake's level by number. Del removes a river point (two stay), and Ctrl+D inserts one halfway to the next.

The edit file keeps a changed river whole and a lake by its level, numbered by the island build's network. The builder
takes the edits before it cuts anything (`IslandBuilder(water_edits=...)`): an edited river replaces the computed one
before its bed and banks are carved, and a lake level replaces 2070's before the basin is dug. Banks, waterfalls and
the water maps therefore follow the hand-set water. A width is the network's (the river's core; the carved channel
adds its banks).

**Forest** (T): plant or clear forest tiles with the brush's radius. You can plant only on land: 117 grows no tree in
the water. The edit file keeps the tiles that differ from the build. The build sets them in the island's
`TreeMap` right after the converted forest is placed (`islandedits.apply_forest`), and a planted tile joins the
nearest forest chunk, so the game's chunk-wise clearing still reaches it. On a 2070 island planting makes tree objects
and clearing deletes them (Anno 2070 editing above).

**The model library** (Object Groups, searched): **2070** - every variation of 2070's landscape assets (mountains,
stones, vegetation, trees, blocking and selectable props, simple and explorable objects; 392 with the Complete
Edition; the developers' `testdata/` models do not ship and are left out) - **117** - the game's model configs under
`graphics/library`, `roman`, `celtic` and `roman_celtic` of every DLC (9,333) - and **117 editor-only**, what
Ubisoft's own island editor places and the game never offers (`islands/editorassets.py`): 311 assets whose template
says `OnlyVisibleInEditor` or that carry `PlacableInEditor` / `AddToEditorObjectGrid` - 171 position markers, 48
herds, 40 audio spots, 31 fish and bird flocks, 15 sequence dummies, build blockers, feedback-grid modifiers and the
map-exit blocker. A click picks one; a 2070 model is made drawable in the mod first, as the island build does
(`IslandEditor.convert2070`; the full source only: an edition without the model build lists no 2070 models here). The Place tool (A) then shows it under the cursor: a click places it, `[` and `]` turn
it by 15 degrees, S goes back to Select to move it. A placed model is an `added_objects` entry; a 2070 one also
keeps its `"source"` (the 2070 config), so the build converts it again into an empty folder. An editor-only object
is written by cloning one instance of its kind from the vanilla islands (all eight kinds occur in the 89 shipped
ones; quest links and the source island's folder are dropped) and giving it the asset, an ID and the place; the turn
goes into `Direction` too, as every turned vanilla point object carries it (0..2pi, right after `Position`). Left
out: rivers, lakes and coastlines (splines), and the kinds that carry a Bezier path (third-party feedback areas,
Bezier paths, work-area slots); fog banks, fish shoals, ambient and music areas and cameras live in a session, not on
an island.

### Placing buildings

The **Buildings** dock (beside Object Groups) lists what the open island's game can place: its buildings and every
other asset with a model. The list is read from the game's own asset files the first time an island of that game opens
(Anno 117 takes about 8 seconds; later islands open the list at once).

1. Type a name, a GUID or a template into the search. **Buildings only** narrows the list to buildings. The groups
   follow the game's build menu where it has one (117, 1800), and otherwise the asset files' groups (2070, 1404, 2205)
   or the template. A row shows the asset's icon where the game names one, and its tooltip shows the model and the
   ground it may stand on.
2. Click a row (or drag it onto the view). The Place tool (A) shows the model under the cursor, standing on its build
   grid, with its footprint on the ground. The footprint is **green** where the game's rules allow it and **red**
   where not, with the reason in the status bar: off the island, needs land, needs the coast, needs water, or over
   another building.
3. `[` and `]` turn it in the game's steps (eighths in Anno 117, quarters in the other games). Hold **Shift** to place
   it off the grid (the Snap places setting applies then).
4. Click to place it. A red footprint still places, with the warning in the status bar, and Check rules lists it.
   Place again, or press S for Select. The new building moves, turns, copies (Ctrl+D, Ctrl+C / V), deletes and undoes
   like any object.

The asset editor's **Edit > Place this on the open island** (Ctrl+Shift+P) and the viewer's **File > Place on the open
island** hand an asset or a model to the same tool in the suite's window. The island must be of the same game.

The build writes the placed building as a copy of an existing object. It takes the first of these that exists:

1. an object of the same asset on the island
2. an object of the same template on the island
3. an object of that asset or template from another island of the game (117 and 1800: it then carries the blocks of
   its template)
4. a plain object of the island carrying the asset (2070, 1404, 1701 and 2205 have no such blocks)

1701's footprints come from each model's `.info` file (the `NoBuildings` cells shifted by the `.ocf`'s offsets), and
from the shipped islands' occupation grid for the few models whose info holds none (landscape pieces). The live check
also warns about ground that slopes, blocked ground (2070, 1404) and a rock or cliff in the footprint. A selected
building has its footprint drawn on the ground, red where it may not stand.
[Placing from the list](island-editor.md#placing-from-the-list) says what was checked and what is still open.

### Further terrain tools, the object brush, the grids

**Relief** (Terrain tab; `terraintools`; the height brush's other modes, so H still picks them and the brush falloff
applies): **Noise** adds repeatable noise (value noise, two octaves, 3 tiles between its points: the same place always
gives the same relief, so a second stroke deepens it); **Terrace** pulls the ground half way to the nearest step of one
unit each stamp; **Ramp** is drawn from one point to another and lays a straight slope between the ground's own heights at
both ends, the band across it blending back by the falloff (the slope follows the mouse while the button is held, the
ground the stroke began on is what it is laid over); **Spike removal** puts a corner that stands more than 0.4 units off
the median of its eight neighbours on that median, and leaves a gentle slope alone. Each stroke is one height edit in the
edit file and one undo step.

**Spray model** (Object tab, key O; `objectbrush`): sprays the library's pick (a model or an editor-only asset; in 1404 and 1701, where trees are objects, the trees too; a forest
tree has the forest brush) along the stroke within the radius, at least the spacing apart, only on ground above the sea,
turned at random and scaled by up to 15 % either way (an editor-only asset keeps its size). A stroke places added
objects and is one undo step; it is random per stroke, the edit file keeps the objects, not the dice.

**Active map** (Grid tab, key G; `cellbrush`, `islands/islandinfo`; vanilla 117 and 1800 islands): the cells of 8 x 8 tiles
the game draws and copies into the session. Draw cells and Erase cells set the cells the brush touches; the view outlines
the drawn region on the ground and crosses the cells the edits changed. The build writes the cells into the render data's
`ActiveMap` and the `.a7minfo`'s `ActiveMapGrid` and cuts the island's `ActiveMapRect` round the drawn cells (a rectangle
set in Island properties wins). A cell the island's stored meshes and height pages do not reach is drawn by nothing:
erasing is safe, drawing is for cells the island has ground in. Not seen in the game.

**Ford** (Grid tab, key G with the active map's tool; `cellbrush`, `islandgrids`; vanilla 117 and 1800 islands with a ford
grid): the river tiles units may cross. Lay ford and Erase ford set the tiles the brush's centre reaches; a ford lies on a river
tile in every island checked, so the brush lays none off the river (it erases anywhere). The view marks the fords in the
active map's tool with points on the ground and crosses the tiles the edits changed; the river tiles show fainter, so the
places a ford can go are seen. The edit file keeps `ford` (`"x,z"` -> 1 or 0), only what differs from the island's own, and the
build writes it into `WorldManager/FordGrid`. The active map and the ford share one cell brush (`cellbrush.CellEdits`):
the tool is the same, the Grid group picks the grid. Not seen in the game.

**Herd area** (Grid tab, key G with the active map's tool; `cellbrush.HerdEdits`, `islands/herdareas`; vanilla 117 and 1800 islands
and converted ones with a herd grid): the tiles the island's herds roam. Add herds and Remove herds set the tiles the brush's
centre reaches; only land that is no river is offered. The view outlines the area and crosses the tiles the edits changed.
The edit file keeps `herd` (`"x,z"` -> 1 or 0); the build sets the tiles in `AnimalManager/HerdGrid` and draws the
`HerdAreas` again, one entry per connected region (a patch of under 40 tiles is no area and is dropped). Not seen in the game.

**Environment** (Grid tab, key G with the active map's tool; `cellbrush`, `islandgrids`; vanilla 117 and 1800 islands with the grid): the audio ambience zone of each 4 x 4 tiles (`WorldManager/EnvironmentGrid`). Pick a zone in the Zone box (Meadow, Forest, Mountain, Sea, River crossing, ...; the game's `EnvironmentType`) and Paint zone sets the cells the brush's centre reaches; the view shows each cell's zone as a coloured point on the ground and crosses the cells the edits changed. The edit file keeps `environment` (`"x,z"` -> the zone's number), only what differs from the island's own. A zone changes the sound ambience the island plays there; nothing in the ground or the rules reads it that was found. Not seen in the game.

**Surf** (Terrain tab, key U; `cellbrush`, `islands/surfmap`; vanilla 117 and 1800 islands): byte 1 of the render data's
sea map (`WaterInfos`, one cell per 2 x 2 tiles), the coast waves the sea shader draws. Waves sets the cells under the brush
to the brush's strength (0.1 to 1 of 255), Calm to 0; the view marks cells with weaker waves in blue and the changed ones
in orange. Vanilla islands carry 255 nearly everywhere and lower values along some coasts (roman_island_medium_03: 25,170 of
25,600 cells at 255). A height edit writes the depth byte only; the two bytes do not touch.

**Camera** (the status bar's Camera box; `desktop/flycamera`): Orbit turns about a pivot; Free fly turns about the camera's
own eye (the left button looks round, the wheel flies forward and back, the arrow keys slide along the ground, Page Up and
Down lift) and answers everything the renderer and the tools ask of the orbit camera, so the picture stays where it is on a
switch. Its look range is where the shadows centre and detail is picked.

**A prop's tint** (Properties, Tint): a colour button with an alpha, for a prop that carries one (the island's `PropGrid`
instances have `Color`). Kept as `color` in the prop's edit record (a moved prop keeps it), copied and pasted with the prop,
written into the `PropGrid`. The view does not tint props yet.

### Island settings

Edit > Island properties (`islandprops`, `desktop/desktopforms`) shows the settings of the open island, one tab per kind:

- **Island** (vanilla 117 and 1800): the grid (fixed by the island's files, shown), where the start coast faces (degrees;
  an island may leave it unset) and the active rectangle (x0, z0, x1, z1 in whole 8-tile cells, the size the map generator
  reads). Written into the island's `.a7minfo` beside it: a loose file the game reads over its own (`meta`).
- **Pool registration** (an island saved as a new one): `IslandRegion`, `IslandDifficulty`, `IslandType`,
  `IslandPlaceholderSize` with the values the game's own registrations use as choices; the problems (`islandmeta.check`)
  show as you type, and the values are kept all the same (`registration`).
- **Island properties** (2070): clime (0 above water, 1 underwater), difficulty and sea level at the top of the `.isd`, the
  size shown (`properties`). 1404's islands have none of the three; nothing is invented for them.

OK writes each tab as one undo step and keeps only what differs from the island.

### New islands and a mod's archive

**A new 117 island** (`blankisland`): the vanilla island of a grid cleared and its ground shaped into a plateau island, saved as
`new<grid>`. **A new 1800 island** (the Native list's New island (shaped)): the same shaping on any vanilla 1800 island of any
grid (`blank_edits(..., source=)`), which the build replaces in place; New blank island keeps the vanilla terrain. The ground
is shaped on the first open only; the heights are the mapper's own from then on. **A new 1404 or 1701 island** (their Native lists' New blank island and New island (shaped), `App.open_blank`): one of the
game's islands cleared in place like 1800's (objects, props and rivers off at the build, `"clear"` in the edit file), the
shaped one also reshaped by the same plateau rule; the ground textures are the donor's. **A new 2205 island does not exist**:
its sessions are whole maps, edited as they are (formats/anno2205-map-format). **A new 2070 island**
(`editor/formats/isd/blank`; the 2070 list's New island…): `isd2070.blank_island` in 2070's own form, named by its edit file
(`source` and `new`: `size`, `seed`) and laid over the game's files for the editor and the build, written into the patch
archive as `data/levels/islands/normal/new<size>.isd` with no far-view picture; whether 2070 offers it is not known.

**Open island from a mod** (File menu): picks a mod folder and lists its islands, loose and packed in its `.rda` archives
(`Files(archives=True)`: a loose file wins over an archive member of the same path, a later archive over an earlier one,
and an archive that cannot be read is listed in `Files.unreadable`, not fatal); the game's own mod loader opens none
itself.

### Several objects, snapping, layers

**Selection** (`selection`): besides the one object a click picks, a selection holds several - added one by one,
toggled, every shown entry inside a box on the ground, or every entry of one model (or editor-only asset). Moved,
turned (round their centre, each object turned with its place), scaled (sizes and distances), deleted or
duplicated together, each operation is one step of the undo stack (`one_change` folds the steps an operation makes
into one). **Copy** turns entries into a clipboard of plain data (each item's model or asset, its offset from the
centre, turn and scale; no forest trees), which survives JSON, so it can be pasted on another island; **paste**
places it round a point, optionally turned and each item set onto the ground, as one change; a prop comes back as a
prop. In the window (`desktop/desktopgroup`): Shift+click adds an object to the selection or takes it out, a gizmo
move carries the rest along, Del and Ctrl+D act on all; Edit > Copy (Ctrl+C), Paste (Ctrl+V, at the cursor's spot on
the ground), Select the same model (Ctrl+Shift+A), Snap places, Snap turns, Snap onto the ground, Brush falloff,
Selection to a layer and Layers (a Hidden and a Locked box per layer). A locked layer's objects take no click; a
hidden layer's are hidden in the view the way a right-click hides, so Show hidden brings them back for the moment.

**Snapping** (`snapping.Snap`: a place mode, a turn step, onto the ground): tile centres, tile corners, half and
quarter tiles, the diagonal lattice, the 8-tile cells of the active map; turns by eighths, 15 degrees or quarters.
Building grid (`footprints`): a building with a footprint is moved so that the turned footprint's corners stand on
whole tile corners (on an odd eighth, its origin on the diagonal lattice). A model without a footprint falls back to
the tile centres. A turn by the gizmo takes the turn setting and then puts the building back on its grid. The rules are
in [Placed objects](island-editor.md#placed-objects).

**Edit layers** (`editlayers`): named groups that a mapper makes, hidden or locked as a whole. The edit file keeps them
under `layers` (`{name: {"hidden", "locked", "members": [keys]}}`). A placed object carries its layer in its own
record (`"layer"`), because its key changes with a save. The build reads neither. Every change can be undone.

**Brush falloff** (`brushes.falloff`): smooth (the cosine the brushes always had, so older strokes paint the same),
linear, sharp, sphere, constant.

### Game rules

What the editor's own checks (`islandrules`, `islandmeta`, `modcheck`) and snapping hold an island to, and where
each rule comes from:

| Rule | Value | Source |
|---|---|---|
| A 117 or 1800 island's grid | a multiple of 64 tiles; the game ships 64 to 768 | the `.a7me` dimensions and the page pyramid of every vanilla island |
| A tile is land | its four corners average over 0.0625 | the land bit of the island's water grid |
| Height range | about 128 world units either way | the coarse height map's 16 bits at 1/256 unit |
| Size classes (settling cost, land tax) | up to 9,000 land tiles Small, 18,000 Medium, 36,000 Large, 72,000 Extra large, beyond Continental | the game's island economy data (editor-ideas "Island size classes"); the class bounds read as the most land a class holds |
| Ground materials per island | 32 (IDs of 5 bits) | the material map encoding |
| Islands on one map | 127 | the game's island ids |
| Slot size by grid | 128-256 untagged, up to 320 Medium, larger Large | the vanilla registrations |
| Registration values | the ones the game's own `RandomIsland` assets use (`islandmeta.known_values`); another is turned into the default without a word | the vanilla assets |
| Turned objects | an odd eighth puts the origin where `x + z` and `z - x` are whole (tile corners and centres) | the placement rules (`117-features.md`) |
| Slot turns | rounded to the nearest eighth; below 22.5 degrees either way none | the slot data |
| Island archive | the members packed at zlib level 1 (`78 01`); another level fails to load | seen in game 2026-09 |
| Mod folder | `modinfo.json` with a `ModID`; an `assets.xml` patch rooted in `ModOps`, each operation naming a `GUID` or `Path`; an island's `.a7minfo` beside its `.a7m`; every `FilePath` in the mod or the game | the mod loader's log |

### Build, install, restore

For the games that load archives (2070, 1404, 1701, 2205) one record per game (`archivedelivery.IslandDelivery`)
holds what differs: the files the edits make (`edited`), the archives they are packed into (`package`), the install
with the game's own files kept first (`install`), the restore, and whether an archive of the editor is in the game
(`is_ours`). `App.build_archives(key, install)`, `App.restore(key)` and `App.installed(key)` loop over it, and so does
the suite's project build. The build's folder in the workspace holds the archives by their path in the install
(2070: `anno2070-patch/maindata/patch9.rda`). Installing into 2205 needs the setting `patch2205`, for a project too.
Nothing is built when no edit changes a file.

The game's own files are kept outside the workspace, in one folder per install under the tool's data folder
(`%LOCALAPPDATA%\anno-toolbox\game-backups\<game>-<hash of the install folder>`, `othergames/gamebackups.py`;
`ANNO_GAME_BACKUPS` moves it). A changed workspace setting therefore cannot lose the copy that a restore needs. A copy
that an earlier version kept in the workspace is moved there (copied, compared, then removed) the first time it is
looked for. A fresh copy is taken whenever the file in the game is not ours, which covers an update and the
launcher's file check. When a file of ours lies in the game and the game's copy is missing, the build, the
install and the restore are refused ("verify the files in the launcher"). A 1404 archive or a 2070 `patch9.rda`
built without it would lose the game's files.

2205's archive index `maindata/file.db` has one owner, `othergames/delivery2205.py`: the island editor's sessions
and the UI editor's changed files each lie in an archive of their own, and the index in the game is always the
game's own one pointed at every archive of ours that is installed. Installing or taking out one of them
recomposes it from the kept copy, so the two compose in any order (`tests/test_delivery2205.py`).

The island editor's mod folders (the 117 and 1800 installs, a port's mod, `island port --out`) are written
beside their place and swapped in (`modloader/modswap.py`, `place_folder` and `staged`): a copy that fails
leaves the installed mod as it was, the old copy goes to the recycle bin, the mod id is one folder name (never
`..`), and a folder that holds anything but this mod (another mod's `modinfo.json`, files without one) is never
replaced.

## The edit file

`island-edits/<name>.json` (`islands/islandedits.py`), coordinates in Anno's own axes, no game data:

```json
{"version": 1,
 "objects": {"<cfg>|<x>|<y>|<z>": {"position": [x, y, z], "rotation": [x, y, z, w], "scale": 1}},
 "props": {"<cfg>|<x>|<y>|<z>": {"deleted": true}},
 "added_objects": [{"cfg": "...", "source": "<2070 cfg>", "position": [...], "rotation": [...], "scale": 1},
                   {"guid": 1003227, "template": "Herd", "name": "Herd Sheep", "position": [...], "rotation": [...]},
                   {"guid": 2955, "template": "Production", "clone": true, "variation": 0, "plain": true, ...}],
 "added_props": [],
 "heights": {"z,x": world units added to that corner},
 "paint": [{"material": id, "radius": tiles, "strength": 0.5, "points": [[x, z], ...]}],
 "materials": {"30": {"file": "<117 material cfg>", "set": "<its material set>"}},
 "rivers": {"0": {"points": [[x, water, z], ...], "widths": [...]}},
 "lakes": {"0": 4.5},
 "forest": {"x,z": 1},
 "meta": {"start_coast": degrees, "active_rect": [x0, z0, x1, z1]},
 "active": {"cx,cz": 1 or 0},
 "surf": {"cx,cz": 0..255},
 "area": {"x,z": 1 construction area, 0 none, or another area's id},
 "herd": {"x,z": 1 or 0},
 "session": {"ambient": "amb_roman_01", "vegetation": "...", "playable_area": [x0, z0, x1, z1]},
 "registration": {"IslandType": "Normal;Starter", ...},
 "properties": {"clime": 0, "difficulty": 0, "sea_level": -28},
 "new": {"size": 128, "seed": 1}}
```

`meta`, `active` and `surf` are applied to vanilla 117 and 1800 islands (`islandinfo`, `surfmap`); `area`, `herd` and `session` to those and to built islands (`islandedits`, `herdareas`, `sessionsettings`; island-editor "Slots, herd areas, map pictures and session settings"); `registration` to an island
saved as a new one; `properties` and `new` to 2070-format islands (`editor/formats/isd/props`, `editor/formats/isd/blank`). A prop's record
may carry `"color": [r, g, b, a]`. An added object with `clone` is a copy of an object of the island (its asset, else its
template); `plain` (placed from the Buildings list) lets a plain object of the island stand in when there is neither.

A 117 island's file adds `"source"` and `"target"`, another game's `"source"` and `"game"`, a blank 1800 island
`"clear": true` (`editor/formats/a7m/edit117.clear`). Anno is left-handed and
`rdmglb` writes meshes untransformed, so the view mirrors x once (view x = size - x); every position and rotation is
reflected on the way in and back on saving (quaternion (x, -y, -z, w)), and the file stays in Anno's coordinates.
