# The asset editor: a user's guide

The asset editor opens a game's assets in a tree. It shows the values of the asset you pick in forms made from the
game's own schema: the same properties, data sets, templates and limits that the game's data tools use. Your edits are
a **change set**, which is kept apart from the game's files. It is written as a ModOps mod for the mod loader, or as
the game's own files. The asset editor is an app of [the suite](suite.md) and also starts on its own:
`python -m annotoolbox --app assets`.

It needs your own install of the game to work. Nothing it writes has been loaded by a game yet, so treat a new mod as
untried until you have started the game with it and read `mods/mod-loader.log`.

## Games

| Game | Opens | Writes |
|---|---|---|
| Anno 117 | `assets.xml` and the texts; the schema from the game's own property files, with the descriptions of Anno 1800's files when that install is set | a ModOps mod, or the whole `assets.xml` as a loose file |
| Anno 1800 | the same, from the exported data | a ModOps mod, or the whole file |
| Anno 2205 | the topic files of its asset data as one tree (a group that names a file takes that file's groups below it); texts from `texts_<language>.xml` | the touched files whole, in an archive |
| Anno 2070, Anno 1404 | their asset files; texts from the `id=text` files under `data/loca/<language>/txt` | the touched files whole, packed into an archive the game loads over its own |
| Anno 1701 | `property.ini` (an asset per object), `balancingtabelle.txt` (its columns as a block of the asset), `gamevalues.xml` (one asset); texts as 2070's; [Anno 1701](#anno-1701) | the touched files whole, in a new archive the game loads over its own |

The first opening of a game scans its asset files (117: about 16 s, 1800: about 11 s). The scan, the schema and a copy
of the asset file are kept under `asset-cache/` in the workspace, so later openings take under a second.

## The window

**File > Open game** picks the game. The game folders are set in the island editor (File > Games and folders).

- **The tree** (left) lists the assets by file group (nested, with a group's own assets and its subgroups' under it),
  or by template (117 has no groups). Each asset has its icon: the picture its `IconFilename` names, or in 2070, 1404
  and 1701 the tile of an icon atlas that `icons.xml` gives its GUID (the Icons tab says which tile of which file). The
  pictures are read in the background, so the tree never waits for them, and the search starts when typing stops.
  2205's asset list names the files of its groups. They hang below the group that names them, so the tree is one tree
  and not one per file (a file that no group names stays a root of its own). The search box finds a GUID, a name or a
  template, with *find next*. The template filter narrows the list. The value filter takes conditions such as
  `Building/Upkeep >= 5` (`=`, `>=`, `<=`, `~` contains, `^` starts with, `in a;b`). Select several rows (or a group
  row) to cut or copy them.
- **Properties** shows the asset as a form: a block for each property, and a field for each value by its kind (number,
  text, choice from a data set, flags, a reference to another asset, a list or table). Each label says where the
  value comes from: *own* (this asset), *base* (the asset it is based on), *template* or *default*. A value you
  changed is bold. Right-click a label and choose *Reset value* to take your change back. Descriptions are the
  tooltips. A description that the game's schema lacks may come from Anno 1800's, or may be one written for this editor
  from what the property does; the tooltip says so.

  **View > Expert values** shows the properties that the schema marks as expert. Two more switches leave values out by
  default. **Secondary template values** are values that the asset's template lists as hidden; the form says how many
  it left out. **Values hidden by conditions**: Anno 1800's schema shows a value only while other values of the same
  property stand a certain way, for example a choice or a number. The form follows the asset's current values and shows
  the value as soon as its condition holds. **Original data** puts the game's own value beside every value the change
  set changed.

  The **...** button beside a value helps to fill it in. Beside a reference, it opens a search that lists only the
  assets the value may name (its allowed templates, its needed property). Beside a colour, it opens the colour dialog
  (a colour is a signed 32-bit number, alpha in the high byte). Beside a file name, it lists the game's files that match
  the value's filter.
- Other tabs:
  - **BalancingView**: a group's assets against the value paths you choose, editable like a table, with CSV out and
    in. A cell that the change set changed shows the game's value in its tooltip. **Save view...** keeps the columns
    under a name, the list beside it brings them back, and **Delete view** forgets one. The views of a game are the
    file `asset-views/<game>.json` of the project or workspace.
  - **TextView**: the texts that an asset's text values name, per language, with the word count of the asset's texts
    and, when a group is picked, of its assets.
  - **Icons**.
  - **Templates**: what a template defines.
  - **Usage**: the assets that name this one, both the game's and your change set's.
- **Checks** and **History** (bottom): the results of the checks, and every edit with undo and redo (Ctrl+Z, Ctrl+Y).

## Edit

- Change a value in its form. Each change is one undoable step. A list is a table with add and remove.
- **Edit > New asset from template** (Ctrl+N) makes an asset with the properties its template lists. **New asset from
  this one** makes a child that names this asset as its base. **Copy this asset** (Ctrl+D) copies it whole. All three
  take a GUID from the change set's range. **Edit > GUID range...** chooses the range: your own range, a publicly
  declared free range (with its source) or one you type, which is checked against the game's own GUIDs, the published
  ranges and your other projects. Inside a project it is the project's range, and choosing it is one undoable step.
  Where the game's file gives a group its own range (Anno 2205's `GUIDRangeMin` and `GUIDRangeMax`), a new asset beside
  an asset of that group takes its number from there. **Rename** and **Delete asset** work on any asset, the game's
  too; the file only records the change.
- **Edit > Cut / Copy / Paste** (Ctrl+Shift+X, C, V) work on the selected assets and paste after the asset you pick,
  in its group. Pasting makes copies with new GUIDs. Cutting an asset of your own change set moves it and keeps its
  GUID. Cutting one of the game's assets copies it and deletes the original, so its GUID changes, and whatever names
  it must be pointed at the new one.
- **Edit > Place this on the open island** (Ctrl+Shift+P; in the suite's window only) hands the asset to the island
  editor's Place tool: its model under the cursor on its build grid, on the island of the same game
  (island-editor-tools "Placing buildings").
- **Check > Sanity check** (F7) looks at what you touched. It finds references that do not exist, a needed property
  that is missing, a template that is not allowed, a choice that is not in its data set, a number out of range, a
  property or value the schema does not know, an asset you removed that others still name, and new GUIDs outside the
  range or used by another mod in the mods folder. It also finds a reference to an asset without any of the allowed
  properties, a variable marked as such with no name, a script or text group id that is no whole number, and an
  asset of a template the game no longer has. **Final check** renders the patches too and compares with the other
  mods.
- **The work runs off the window.** The checks, Build mod and Write the game's own files each run on a thread, under a
  line in the status bar ("checking...", "writing the mod..."). Only one job runs at a time; a second one says that the
  editor is busy. The questions stay on the window: the check's errors ask whether to build anyway, and the folders are
  asked for first. Each job reads a **snapshot** of the document, taken when it starts (`AssetDocument.snapshot`: a
  copy of the change set, with the game's files shared). Editing while a job runs is therefore safe, and the result is
  what the document was at the start. The check and the write of Build mod read the same snapshot, so what was checked
  is what is written. `tests/test_assetjobs.py` edits during each job.

## Print and word counts

**File > Print this asset...** (Ctrl+P) prints a page of the asset. The page has its name, GUID, template and base
asset, and every value it has with where it comes from (own, base, template, default). Where the change set changed a
value, the game's own value is printed too. Its texts follow, in the language of the TextView, with their word count.
**Print this asset to a PDF file...** writes the same page as a file. **Print the balancing table...** does the same
for the BalancingView's table. The word counts of the TextView (this asset, the picked group) and of **Export texts**
(the language) count words separated by white space.

## Write

- **File > Build mod (ModOps)** (Ctrl+B) writes a mod for the mod loader. Each changed value is one `merge` below the
  asset's `Values`, a replaced list is one `replace`, a reset is one `remove`, a new asset is one `addNextSibling`
  beside its anchor, and a removed asset is one `remove`. Texts are written per language, and `modinfo.json` is made
  from the change set.
- **File > Write the game's own files** writes the asset files you touched whole: the edited assets in place, the new
  ones beside their anchors, and the rest byte for byte. They are written as loose files in the game's layout, or
  packed into an archive. It also writes the text files you changed: the edited texts in place and new ones appended
  (for 2070 and 1404 a new text goes into a file named after the mod). The text files are written by the Texts app's
  writer ([`text-editor.md`](text-editor.md)): an entry written `<Text/>` is replaced, and a line break in a text of a
  line game becomes `<br/>`.
- In a project the change set is part of **Project > Build mod**, together with the islands and the interface edits.

## Open an existing mod

**File > Import mod** (a mod folder) or **Import profile** (117: a profile file) reads a mod's ModOps in the same way
the mod loader applies them: the mods in the loader's order, with conditions, includes and options. The difference to
the game's own files becomes a change set. The mod therefore opens in the forms, and can be edited and written again,
as ModOps or as whole files. A summary lists what the mod changed (values, blocks, whole assets, new, removed, texts)
and every operation that matched nothing, as the loader's log reports it.

Some things cannot be held by a change set: conditions on other mods, a later mod overriding an earlier one, options,
and files with no form such as templates and properties. They are reported, and the files are carried over unchanged
into the written mod. The change set holds what the mod and the mods inside its folder change. A mod that it requires
is applied to get the same result but stays a dependency (`Dependencies.Require` of the written mod), and its assets
and files are not copied in. Build mod never writes over the folder it imported.

## Open an archive mod

For the games that load archives (2070, 1404, 1701, 2205) **File > Import archive mod...** takes a `.rda` that changes
the game's files and compares it with the game, in the same way Import mod does for a mod folder. Each asset file the
archive holds is read as the game reads it and compared asset by asset (values set, blocks replaced, whole assets, new
and removed assets, with the finest form that gives the archive's asset back). Its text files become texts per language
(2070, 1404 and 1701: every `id=text` line that differs or is new; 2205: the entries of the XML files). Every other
member (models, textures, sounds) is written under `imported-archives/<name>/` in the workspace and carried: **Write
the game's own files** puts it into the archive again. The folder is emptied before each import, so importing an archive
again carries its files only. What the change set cannot hold is listed in the summary (a text
that was removed, an asset in two places). A member that names a place outside the mod (`..`, a drive) is left out and
listed.

A 2205 patch that is an archive plus an entry in the game's `file.db` is imported from the archive. The entry gives the
members their game paths. A `file.db` beside the archive is used, or else the game's own, which has the entry once the
patch is installed. It names the archive in its list and points each path at a position in it. A member whose position
an entry names is read as that path (`othergames.index2205.ArchiveIndex.paths_in`). This was checked on a synthetic
index only: no real patch was at hand to compare the positions with (a member inside a contiguous block may name its
position relative to the block).

Only the game can say whether the result loads. None of this was tried in a game.

## Anno 1701

Anno 1701 has no asset file or schema. The editor makes assets from three of its data files
([the formats](formats/anno1701-property-ini.md)) and writes them back as the game reads them:

- **`property.ini`**: an asset for each object, named by its `NAME`, with the object's own values in a block `Object`
  (`V100` to `V104`) and each `[PROPERTY]` block as a block `P<number>` (`V<value id>`). Whole numbers show as numbers,
  everything else as text. The template is the set of properties the object has (`Object P7`), so the tree by template
  groups objects of one kind. The names of the numbers are not in the data; the block and value numbers are the names.
- **`balancingtabelle.txt`**: the columns of the row with the asset's GUID are a block `Balancing` of that asset (the
  German headers with the umlauts spelled out, `Einflussradius`). A row without an object is an asset of the template
  `Balancing only`. BalancingView lists columns across a group.
- **`gamevalues.xml`**: the asset `gamevalues.xml` (GUID -1) with the game's settings as nested values.
- **Texts**: `data/loca/<language>/txt` as in 2070; a text is named by the asset's GUID. **Icons**: `icons.xml`.

**Write the game's own files** writes `property.ini`, the table and `gamevalues.xml` where they changed, and the
changed text files. An edited object's lines are written again, and every other byte stays as the game has it.
**Project > Build** puts them into one new `data<n>.rda`, which **Install** places in the game folder. The game's own
archives stay, and **Restore** removes the new one. A new asset takes its GUID from the change set's range. Choose a
range the game accepts (**Edit > GUID range**); what 1701 accepts is currently unknown. Icons and the checks work as
for the other games.

## Icons of the old games

2070, 1404 and 1701 keep an asset's icon in `icons.xml` (GUID -> atlas file id and tile). The **Icons** tab shows two
boxes (the atlas file id and the tile), a number for which icon of the entry (0 is the first; one past the last adds
one: 1404 has entries with variations) and **Set this icon** (one undo step). The tree row of the asset shows the new
icon at once. The change set keeps the icons (`icons`, GUID -> per icon `[file id, tile]`, `[]` for one left as it
is).

**Write the game's own files** writes `icons.xml` by editing the file's own text: the icon is edited, an empty entry
gets one, and the rest of the file stays as shipped (`sources/icons2070.set_icon`; every installed file takes an edit
and reads back, `tests/test_asseticons.py`). In 1404 it writes the file that has the GUID's entry, and a new GUID goes
to the add-on's file. 117, 1800 and 2205 have no `icons.xml`, so the boxes are hidden there.

Note: not tried in a game.

## Files

| File | What |
|---|---|
| `asset-edits/<game>.json` (in the project, else the workspace) | the change set of one game; an imported mod is `<game>-<ModID>.json` |
| `asset-views/<game>.json` (the same) | the saved balancing views of one game |
| `asset-cache/` (workspace) | the scan, the schema and a copy of the asset file per game build (1701: of the files made from its data) |
| `imported-archives/<name>/` (workspace) | the members of an imported archive mod the change set carries |
| `build/<mod id>/` (project) | the built mod |

## Open

Two things are still open: a loose edited file's checksum (see below) is not built yet, and no game has loaded what
the editor writes. [`suite.md`](suite.md), "What has been checked", says what is built and checked.

**The checksum files.** 117 and 1800 ship a `.checksum` file beside `assets.xml`, `templates.xml` and `properties.xml`
(1800 also beside the `pc` copies and the scenario folders' pairs). It holds 32 hex digits made from the file. 117
compares its three files with them when it starts, before the mod loader runs. The first check, on the archive map,
stops the game. The check on these files does not: a changed file only makes the game note the difference, and what it
uses that note for was not found (a multiplayer check is the first suspect).

A ModOps mod is applied afterwards, in memory, and does not touch the files, so **Build mod** is not affected.
**Write the game's own files** writes a changed `assets.xml` that no longer matches its checksum, and the editor writes
no checksum. 1800's code that reads these files was not found.

What to check in the game: start 117 once with a written `assets.xml` as a loose file, see that the main title is
reached and a new game starts, and then open the multiplayer lobby.

## Language

The asset editor's windows are in English or German. Choose the language in the settings (Games and folders,
"Language"); it applies the next time the app starts. Game data (asset and file names, the games' own texts) is shown
as the game has it. See [the suite's guide](suite.md#language).
