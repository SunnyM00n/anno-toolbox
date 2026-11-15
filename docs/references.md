# References: which files nothing names

Every game ships files that no other file names: a texture of a building the studio dropped, a model config nothing
places, an island that is in no map, a picture left from an old interface. This page says how the files of each game
name each other, what the tools take as a name, which files count as found by the program itself, and what a "not
referenced" list is worth. It is the guide to the viewer's **Not referenced** box and to the `unreferenced` command
(`docs/commands.md`, `docs/viewer.md`). The code is `refgraph/`.

**"Not referenced" means: no reference found in the data.** The game may still load the file by a name it builds
(a speech file by the number of its text, an island by the folder it lies in, a picture by its place). Read the list
as the files to look at first, never as the files that are safe to delete.

## How the files of a game name each other

A name is a path written as plain text in another file, with one of the suffixes the game's own files carry (and the
studio's source names: `.psd`, `.fbx`, `.tga`, `.png`, `.bmp`, `.jpg`). Names are written with either slash, in any
case, with spaces in a folder name (`third party/pirates/...`) and with letters beyond ASCII. One set of rules
(`models/fileresolve.py`, the same that the viewer and the island editor use) turns a name into the file the game
loads:

- a source texture name becomes its `_0.dds` (the newer games) or the file as written (the older ones);
- an `.fbx` becomes its `.rdm`;
- a mesh becomes the level files that exist.

A name that finds no file is dropped (the files check counts those: `viewer.md`, "How files are found"). A name that
is no path from the top is tried from the folder of the file that writes it.

| Game | What names what |
|---|---|
| Anno 117 | The asset files (`data/*/config/export/assets.xml` and the content packs') name configs (`.cfg`), island and map files (`TemplateFilename`: `.a7t`, `.a7m`), pictures; a config names nested configs, meshes (`.fbx`, read as `.rdm` with its `_lod0..4` levels), textures (`.psd`, read as `_0.dds` with its lower quality levels), particle files (`.rdp`), feedback files (`.fc`); a config's `.ifo` and baked `.bfg` belong to it by their name; the engine reads a `data/` config through its `shared/` copy, so the copy belongs to the config. Sound banks (`.bnk`) play media by id: `<id>.wem`, found through the bank and its `.json`; an `Audio` asset names a sound event, a `SoundBank` asset a bank, by Wwise id. Scripts (`.lua`) name their sequence files. An island's baked pages lie below `<island>/_gamedata/`. |
| Anno 1800 | The same engine family and the same names: asset files (`data/config/export/main/asset/assets.xml`, 290 MB, and the content packs'), configs, `.a7t` map templates by `TemplateFilename`, islands in pool folders and by `<NameOfIsland>`, properties (`.prp`, `.brp`), sound banks by media id. |
| Anno 2205 | Asset files (`data/config/game/asset/*.xml`: `<Filename>`) name configs; a config names meshes, textures and effects; sessions (`.a6m`, with their scripts and the `_a6mdata` pages below the session's folder) are named by `sessions.xml`; sound banks by media id; the Flash interface movies (`.feu`) name other movies, fonts and pictures (below, "Names inside binary interface files") and the Python scripts are read as bytes like any file. |
| Anno 2070 | `assets.xml` and the other files of `data/config/game` name configs (`.cfg`: `m_FileName`, textures, effects `.efc`, feedback `.fc`, info `.ifo`); islands (`.isd`) and the level files name their pictures and scripts; the old interface's templates (`.tpl`), backgrounds (`.bkg`, `.abm`) and dialogs (`.dlg`, which carry Lua) name their pictures; sounds (`.snd`) name their clips; the texts write picture names without a folder (`010c.jpg`). |
| Anno 1404 | The same shapes as 2070, with Granny meshes (`.gr2`, `.bg2`) in place of `.rdm`. |
| Anno 1701 | Mesh configs (`.mcf`), `.info` and `.ocf` files name Granny meshes (`.gr2`) and source textures (`.tga`; the engine bakes `x_0.dds` to `x_2.dds` from them), `.efc` effects, island files (`.isd`, `.isc`), the world files (`.www`, `.tww`). |

Media and baked data are not read for names (textures, meshes, sounds, videos, fonts, compiled shaders, baked island
pages, `.ifo`, `.isc`, `.dat`, `.bin`, `.db`). They hold no path, or none that a player or modder authored (Windows'
`thumbs.db` lists the pictures of its folder; it names nothing).

### The ways a name is written

`refgraph/scan.py` runs one finder per way (`file_refs`). Each finder yields the files that a place in the file
names.

| Way | Where | Found by |
|---|---|---|
| A path with a known suffix | every carrier | the regex of the game's suffixes; a name with spaces is put back together word by word until a file exists (`scan._with_spaces`) |
| The same path in UTF-16 | the credits texts of 2070, 1404 and 1701 (`loca/.../credits.txt`), 2070's compiled interface files (`.cdf`), 1404's `.rdu`, 2205's `.a6cam` | the same regex with a NUL after each character, run only on a file that holds a `.` + NUL |
| A bare file name | 2070's quest texts (`<Text>010c.jpg</Text>`, the picture lies in `graphics/ui/datalog/`) | when no file is found, the one file of the game that bears that name (two files of that name: none) |
| An island by its name | `<NameOfIsland>` of 117's and 1800's `Island` assets | every `.a7m` of that name, in any pool folder |
| A sound event or bank by its Wwise id | `<WwiseID>` of 117's and 1800's `Audio` and `SoundBank` assets (2205's assets carry none) | `refgraph/wwise.py`, below |
| A name joined by a script | Lua `"folder/x_" .. n .. ".bkg"`, Python `+` | the files that start with the literal head and end with the literal tail, below |
| A sound bank's media | `.bnk` files | the media ids of the bank, as the viewer finds them (`exporting.banks.media_file`) |

## What counts as found by the program

Each file gets one class (`refgraph/rules.py`, `classify`):

| Class | Meaning |
|---|---|
| referenced | another file names it |
| implied | not named itself, but part of something named: the other quality levels of a named texture (`x_0.dds` to `x_4.dds`, `x.dds`, the source name `x.tga`), the other levels of detail of a named mesh (`_lod0..4`), the textures that carry the name of a named Granny mesh (`x.gr2`, `x_0.dds`), the same-stem partners of a named file (a config's `.bfg`, `.ifo`, `.fc`, `.snd`; an island's `.a7minfo`, `.a7me`; a map's `.a7tinfo`, `.a7te`; a bank's `.json`, `.xml`, `.txt`; a picture `x.bkg.png` of `x.bkg`; the `.abm`, `.txt` and `.tfo` of a named texture), a `shared/` file and its `data/` file (117 reads one through the other: whichever is named, both are), everything below the `_gamedata` or `_a6mdata` folder of a named island or session |
| entry | found by its place, not through the data: the configuration, scripts, AI scripts (`ai`; the scripts call each other by module name), shaders, texts (`loca`), interface and fonts folders of the data root, the scenarios', campaign's and world events' folders, a `pool` folder (the engine picks the islands there by size), the install's own files |
| generated | compiled or baked copies the tools make (`.fxo`, `.ctt`, `.tmc`) |
| only named by unused files | named, but no live file names it (below): the meshes and textures of a config nothing names, the pictures of a screen nothing opens. The partners of such a file (a config's `.bfg` and `.ifo`, a mesh's other levels) are listed with it |
| unreferenced | none of the above: no reference found in the data |

### Live files: the verdict follows the whole chain

A verdict that looks one step only (a file that some other file names counts as referenced) calls the meshes of a
config nothing names "referenced", so its lists understate the unused files. The verdict therefore follows the chain
(`rules.reachable`, over the graph):

- A file is **live** when it is an entry, or a live file names it, or it is implied by a live file (its quality
  levels, its partners, the pages of a live island). The assets inside a live asset file are all live: the game loads
  every asset of the files it reads, so what they name is named by a live file.
- **referenced** is a file named by a live file. **implied** is a file that belongs to a live one. **only named by
  unused files** is a file that some file names but no live file does (with the partners and levels of such a file).
  The rest is **unreferenced**. A cycle of files that name each other, with no entry among them, is not live.
- The search runs in rounds (the files that became live, what they name, what belongs to those, again) until nothing
  new is live. A game's chain is some tens of rounds deep at most.
- The unused files are the unreferenced ones and the ones only named by unused files. The **Not referenced** box,
  the status line and the CSV (`class` column) list both; the CSV and `unreferenced --csv` tell them apart. The graph
  itself (the kept file, `references`, `referenced_by`) does not depend on the verdict: it says every file that names
  a file, live or not.

**Roots.** The chain needs the files that the game opens by a name of its own as entries. Without 1701's object
lists (`ocflist.ini`, `guid_ocflist.ini`: 791 and 783 names), which no file names, the chain turned most of 1701
into "only named by unused files" (15,859 files). The way to find such a root is to look at the unreferenced files
that name many others: they are the files the game opens by a name of its own, or the maps and test worlds nobody
opens. These were found that way:

- `sounddatabase.xml` (545 names in 2070, 1,053 and 1,084 in 1404; 1701's sound files lie below it);
- `ocflist.ini` and `guid_ocflist.ini` (1701);
- `soundbanksinfo.xml` and `.json` (2205's Wwise bank list);
- the `guidata` folder beside 2070's data folder (its interface templates and `path.map`, 1,078 names in one file);
- the shipped game modes' level folders of 1404 and 1701 (`multiplayer`, `tutorial`, `addon01`, `kidmode`,
  `presets`, `demo_*`; the developers' test worlds in the same folder tree are not entries).

They are entries (`rules.ENTRY_PLACES`). Some files stay unreferenced although they name much: test worlds, effect
and configuration files of models nothing places (their meshes are then "only named by unused files"), and the cfg
files of the units the game builds by a name of its own (2205's `inhabitants`), whose meshes are therefore listed as
well. An orphaned file is only as certain as the file that names it.

Two limits remain. An entry is live by its place, whatever names it. And nothing checks that the game really reads
the file that makes another live (an entry folder's script that nothing runs). Only the game says what it loads.

Note: the chain verdict and the roots were added on 2026-11-14.

## Names inside binary interface files

This section is documented from the installed games.

- **Phoenix (117 and 1800)** stores strings as UTF-8 (a varint length, a `01` byte, the bytes:
  `formats/phoenix-scene-format.md`), not as UTF-16. 117 has 4,824 such files (`data/ui/studio/generated/`, counted
  by the kind letters of the container: 3,180 `WI`, 958 `IM`, 243 `EN`, 195 `SC`, 74 `CP` and 174 of the other
  kinds). The only names in them are picture source names (`x.png`: widget, image, enum, atlas, scene and font-data
  files) and font names (`x.ttf`: the `FT` files). A UTF-8 name in plain ASCII is found by the ordinary regex, so no
  Phoenix reader is needed: 661 of the files name pictures under `data/ui/` (an entry folder, so no verdict changes;
  the References dock lists them). Sound events are not named in a Phoenix file (one string, `Stop_Zero`, in a
  scene). Neither are other scenes by name: scenes reference each other by GUID, which is not a file name.
- **UTF-16** appears in other files: the credits texts (UTF-16 with a byte order mark; they name the logos), 2070's
  compiled interface (`.cdf`: `SAVE_DIALOG.DLG`), 1404's template file (`.rdu`), 2205's camera files and logs, and the
  baked `.bfg` configs of 117 (seen in the ones of `graphics/ui/3d_objects`; the tools do not read them, because the
  config that bakes them names the same files). They are found by the wide regex.
- **What cannot be followed**: a name in a compiled or compressed container the tools do not read, and a scene named
  by its GUID.
- **2205's Flash movies (`.feu`)** were checked over its 89 movies (the container:
  `formats/anno2205-ui-format.md`). A movie writes its names as NUL-terminated strings: other movies (5,099 names,
  found), fonts (`Roboto_Light_Default.ffd`, 372, found), pictures and the atlases' tables. Read as written, only
  the pictures written as `ExtractedFireFiles\UIAssets\x.png` find a file (149 of 4,237 picture names). The rest name
  a folder that does not exist:
  - `ExtractedFireFiles\pngs\UIAssets\x.png` (1,728 names: `pngs` is the exporter's folder, the file lies below
    `data/ui/extractedfirefiles/uiassets/`);
  - the atlas entries `.\PNGs\icons\x.png` (2,320 names in `icon_library.feu` and `icon_library2.feu`: an entry of
    an atlas, the picture lies below `data/ui/extractedfirefiles/icons/`).

  `.\blobs\x.bfd` (an atlas table) lies below `data/ui/`. `Resolver.of` takes these forms in 2205
  (`scan.fire_names`): the leading `.\` is dropped, the `pngs` folder is dropped, and `extractedfirefiles/` is put in
  front of an atlas entry. Of the 4,244 picture and table names, 3,108 then find a file (1,727 and 14 of the second
  form). The other 1,136 are atlas entries that have no file of their own (the picture lies only inside the atlas:
  `.\PNGs\icons\icon_wool.png`; 1,104 of them) and 32 bare names the game ships nowhere
  (`icon_module_lab_agryculture.png`). `flash.xml` and byte noise that looks like a suffix are not counted.
  `data/ui/` is an entry folder, so no verdict changes. The References dock of a picture says which movies use it.

Note: documented and checked on 2026-11-14.

## Sound events by name

117 and 1800 name a sound event in the data only through its asset. An `Audio` asset has a `Name`
(`evt_sfx_inc_fire_cl_play`) and a `WwiseID`. The `WwiseID` is the event's id, which is the FNV-1 hash (32 bit) of
the lower-cased name (3671371067 for that one). No Lua script, Phoenix scene or other file of 117 holds an event name
(the bank info files hold all of them). A `SoundBank` asset gives the id of a bank, which is the bank's file name
(`<id>.bnk`, one per audio language).

`refgraph/wwise.py` follows both. A bank id finds the bank files. An event id finds the bank that holds the event
(the banks are read once, on the first event) and the media files the event plays (`exporting.banks.event_sounds`,
every branch of its containers). The edge is `assets.xml#guid -> bank, .wem files`, so the References dock of a
`.wem` says which asset plays it. In 117 this gives 16,453 assets that name sound files (96,919 edges to `.wem`
files, 13,706 assets to the voice bank). In 1800, 5,594 assets name banks (its media lie in the banks, so there is
no `.wem` edge). No verdict changes: a bank already names all of its media. Not followed: an event the game picks by
a name it builds (the voice lines by the id of their text), an event named only by code, and the music segments'
switches (a music track's sources are followed, the choice between them is the game's).

Note: the hash was checked on 2026-11-14.

## Scripts that join a name from pieces

Surveyed over every Lua, Python, `.csl`, `.dlg` and `.cdf` file of the installs.

| Game | Scripts | Names joined |
|---|---|---|
| Anno 117 | 2,530 Lua (2,501 are the generated type stubs; 29 are scenario, benchmark and module scripts) | none: no file name at all in a script |
| Anno 1800 | 31 Lua (scenario scripts, a JSON module) | none |
| Anno 2205 | 877 Python (the standard library and a benchmark) | none that name a game file |
| Anno 2070 | 15 Lua and the Lua inside the `.dlg` and `.cdf` interface files | 33 joins: 1 names files (`"data/config/gui/backgrounds/prototype/objectmenu_frames/ships/ships_multiselection_group_" .. n .. ".bkg"`), the rest are texts, window paths (`"PANEL/CONTENT/COLS/BAR" .. i`) or names built from a variable (`bodyBg .. "_rounded.bkg"`) |
| Anno 1404, 1701 | AI scripts (`.csl`) | none (they call each other by module name, which is why the `ai` folder is an entry) |

**Followed**: a literal head that names at least two folders, an expression, and a literal tail with a suffix, in one
statement (`scan.JOIN`). The edge goes to every file that starts with the head and ends with the tail (the ten
`ships_multiselection_group_<n>.bkg` of 2070), unless more than 500 files fit (a guess, not a name).

**Not followed**: a head that is a variable, a name built in several statements, `string.format` and `%s` holes, and
a name read from a table or from the data. They stay "not referenced" unless another file names them.

Note: surveyed on 2026-11-14.

## What the program loads by a name it builds (limits)

These are listed as entry where a folder says so. Otherwise they show as "not referenced" while the game uses them.

- **Speech and music**: a speech file is `<text id>.mp3` in the language folder, and a music track is chosen by the
  sound database. The folders are entries; a stray clip elsewhere is not.
- **Islands and map pictures**: a random-map island is picked from its pool folder (entry). The 2070 pictures
  `<island>_2070_0.dds` carry the island's name plus a suffix and show as not referenced. 1800's campaign islands
  (`data/sessions/islands/campaign/`) and a content pack's hub islands are named by no file at all (checked by text
  search of every file on 2026-11-14): the game loads them by a name its code builds, so they show as not referenced.
- **World files by title**: 2070's test worlds (`.abl`) name one another by the title in a `[TITLE=...]` line, not by file.
- **117's sources**: a `data/` model config that is only an assembly of parts names child configs; the studio's source
  meshes of a baked config (the `rdm` folders of `data/`) are named by no file, because the game reads the merged
  mesh of the `shared/` copy. They show as not referenced.
- **Stale copies**: 2205's `data/fcp02/sessions/session_earth_08/` (2,139 files) is an older copy of the session
  `sessions.xml` names under `data/dlc03/`; nothing names the copy.
- **Game logic that picks a file by id** other than a sound bank's media ids and Wwise ids.

## Using it

- **The viewer**: File > Find the unreferenced files builds the graph in the background. The first time, it reads
  every file that can name another: minutes for 117 and 1800, seconds for the others. The graph is kept beside the
  game's index and taken at once the next time the same archives are opened. The **Not referenced** box then filters
  the file list (with the kind and the search). The **References** dock lists what names the open file and what it
  names (an asset inside an asset file shows its GUID). File > Export the unreferenced files writes the list as CSV
  (`viewer.md`).
- **The command line**: `anno-toolbox unreferenced GAME [--csv FILE] [--who PATH]` (`commands.md`).
- **The graph** is `refgraph.graph.RefGraph`: `forward` (a file, or `file#guid` for an asset inside an asset file, to the
  files it names), `referenced_by`, `references`, `users`, `verdicts`. It is kept as JSON keyed by the archives' stamp
  and the graph's version.
- **One scan for "who uses this file"**. The viewer's **Used by** dock (File > Who uses the file) reads the same
  graph. For the names inside an asset, the graph keeps the **field** an asset names the file in (`Filename`,
  `Model`) and the asset's **name** (`fields`: the element tag the name is the value of, parallel to the asset's
  targets; `names`: the asset's name). Both are found while it reads the file, at no extra read.
  `RefGraph.users(path)` gives what the dock shows: the files that name the path (every file kind: a Lua script or an
  effect names files too) and the assets that do (`AssetUse`: GUID, name, field, asset file). File > Who uses the
  file builds the graph like File > Find the unreferenced files, and the two docks fill together. The first time
  takes minutes for 117 and 1800, because it reads every file that can name another, which is what finds the
  unreferenced ones. A name inside an asset is found as a field when the name is the whole text of a leaf element. A
  name written with spaces (a folder name of several words) or in an attribute has no field (the column is empty).

Note: until 2026-11-14 the **Used by** dock had its own scan (`models/fileusers.FileUsers`: the leaf values of every
config and of the asset files, which took seconds), kept in the viewer's cache as `file-users.json`. That file is no
longer written or read.

## Counts per game (the whole chain)

The counts are from the installs on the author's machine, with the graph built by `refgraph`. They give the classes
of every file after the chain verdict ("Live files" above). The roots of the previous section are in. The **one
step** columns are the verdict without the chain (a file named by any file is referenced), measured with the same
roots, so the change between the two is the chain alone. **Unused** is unreferenced plus only named by unused files,
the length of the "Not referenced" list.

| Game | Files | Referenced | Implied | Entry | Generated | Orphaned | Unreferenced | Unused | Referenced, one step | Unused, one step |
|---|---|---|---|---|---|---|---|---|---|---|
| Anno 117 | 399,766 | 73,968 | 170,146 | 100,230 | 27 | 26,618 | 28,777 | 55,395 | 86,240 | 28,777 |
| Anno 1800 | 373,002 | 36,203 | 195,784 | 113,299 | 5,042 | 4,010 | 18,664 | 22,674 | 38,323 | 18,664 |
| Anno 2205 | 107,701 | 10,895 | 46,405 | 42,491 | 0 | 1,208 | 6,702 | 7,910 | 11,437 | 6,702 |
| Anno 2070 | 33,217 | 9,376 | 6,799 | 15,157 | 0 | 254 | 1,631 | 1,885 | 9,530 | 1,631 |
| Anno 1404 | 41,549 | 11,373 | 8,060 | 19,717 | 7 | 397 | 1,995 | 2,392 | 11,663 | 1,995 |
| Anno 1701 | 49,055 | 9,986 | 6,499 | 27,888 | 0 | 1,470 | 3,212 | 4,682 | 10,671 | 3,212 |

Notes on the table:

- The files read for names: 117 29,632; 1800 30,864; 2205 5,667; 2070 5,999; 1404 8,178; 1701 8,741.
- 2205's one-step "Referenced" is 1,052 higher than in the earlier table below, and its entry count is 1,979 lower.
  The Flash movies' pictures are found (above), and a named file counts as referenced even where it lies in an entry
  folder.
- 2070's one-step row differs from the earlier table by 18 files (9,530 / 6,900 / 15,133 before the roots against
  9,521 / 6,891 / 15,151; the unreferenced count was the same, 1,654). The cause was not found. The new numbers are
  what the code gives.
- The roots took 23 files out of 2070's unreferenced list, 2 out of 1404's, 107 out of 1701's and 2 out of 2205's.
- The chain adds 26,618 files to 117's list (rdm 19,955, dds 5,220, baked data and info files of the same configs
  805, configs 474). The unused share goes from 7.2 % to 13.9 % of the files.
- The verdicts take about 23 s for 117 and 20 s for 1800 on the machine that built them (the first graph: 211 s and
  134 s), on the worker thread.

The unreferenced files without the chain, in two earlier versions of the scan (the "audit" column is the one-step
verdict before the roots, the other column is the first version):

| Game | Unreferenced (audit) | First version |
|---|---|---|
| Anno 117 | 28,777 | 51,553 |
| Anno 1800 | 18,664 | 18,766 |
| Anno 2205 | 6,704 | 6,884 |
| Anno 2070 | 1,654 | 2,664 |
| Anno 1404 | 1,997 | 2,771 |
| Anno 1701 | 3,319 | 3,933 |

What the unreferenced files mostly are (by suffix, the largest first):

- **117**: baked data (`.bfg`) 8,519 and configs 8,505 (the same configs: a config nothing names keeps its baked file
  unnamed), info files 5,688, textures 2,691, meshes 2,599, 416 feedback files, 196 particle files, 75 sounds (media in
  no bank).
- **1800**: textures 9,730, meshes 2,309, property files (`.prp`, `.brp`) 2,577, info files 1,089, configs 894 and
  their baked data 872, 828 JSON files (infotips by id), 15 campaign and hub islands with their pages.
- **2205**: textures 4,374 (2,139 files of the stale session copy above), meshes 773, pictures 541, configs 400 and info
  files 390 (a content pack's copy of a building the base game also has, configs nothing places), 71 particle files,
  32 logs, 14 sounds.
- **2070**: textures 759 and meshes 241 of unplaced models, 140 configs with their 137 info files, 136 pictures, 55
  effects, 41 sounds, 29 videos, 18 islands.
- **1404**: Granny meshes 508, pictures 503, textures 432, configs 135 and info files 141, clips 106, islands 44, effects 43.
- **1701**: pictures 875 (island previews), islands (`.isd`, `.isc`) 1,240, textures 404, world files 328, source
  textures 144, videos 50.

Note: counted on 2026-11-14, with the `refgraph` of that date.

### The 2070 count against earlier research

`editor-ideas.md` quotes about 1,400 files for 2070's Complete Edition from earlier research. The first graph listed
2,664. The research's list was six kinds (meshes, textures, model configs, videos, sounds, islands), and its counting
method was not kept, so it cannot be repeated. The same six kinds in the graph are 1,813 in the first version (of the
2,664: the other 851 were 229 info files, 297 pictures, 100 AI scripts, 74 interface pictures, 56 effects, 95
others) and 1,231 in the audit's version (of the 1,654: 423 other). The research's number sits between the two. The
likely cause is that the research followed only the names in configs and assets, where the graph reads every file,
and that neither counted a name written with a space or a picture named by its stem. The graph's number is the one
to use. Nothing checks it against the game, only against the spot checks below.

Note: the research is of 2026-11-09.

### How the lists were checked

Thirty random files of every game's "not referenced" list were searched for in the text of every file of that game
(the file's name after a slash, with or without a level suffix, in UTF-8 and UTF-16). The results, the causes found
and what is left are in `audit-2026-11.md`.

Note: checked on 2026-11-14, before and after the audit.

`tests/test_references.py` checks that files known to be used are found referenced in every installed game
(`ANNO*_DIR` set); the synthetic tests cover each rule and each way a name is written.
