# Mod diff

The Toolbox's Mod diff app shows what a mod really changes in the game, how two versions of a mod differ, where mods
collide, and what an old game's archive mod replaces. This page is the user's guide and the design in one place. The
app is the package `moddiff/`. It is opened from the suite like the other apps (`docs/suite.md`), or from the Mods app
(see below).

The question behind every view is the **effective result**. The mod's patches are not read as text. They are run on
the game's files by the mod loader's rules (`modloader/`, `docs/engine-modloader.md`), in the loader's order and with
the mods' options and conditions. The files that come out are then compared with the game's. What the loader would
drop (an operation whose path selects nothing, a condition that does not hold) is therefore not a change, and is listed
on its own tab.

## The six questions

| Mode | Compares | Games |
|---|---|---|
| Mod against the game | One mod (with the mods nested in it and the ones it requires) run on the game's files, against the game | 117, 1800 |
| Profile conflicts | Every mod a profile switches on, run in the loader's order: who touches the same thing, who wins. 1800 has no profile: every mod not switched off in `activations.json` | 117, 1800 |
| Two versions of a mod | Two folders of one mod, each run alone on the game; what differs between the two results | 117, 1800 |
| Two mods | Two mods run together in the loader's order (a `LoadAfter` between them decides it) | 117, 1800 |
| Archive or loose files | A community `.rda` archive (2070, 1404, 1701, 2205) or a folder of loose files against the game's own files | 2070, 1404, 1701, 2205, and loose folders of 117/1800 |
| Two archives or folders | Each against the game, then what differs between the two results (two builds of an archive mod, two folders of loose files) | the same |

2070, 1404, 1701 and 2205 have no mod loader that patches. Their mods replace whole files, so the comparison is file by
file. The asset files and the text files among them are compared asset by asset and id by id, other XML by value, and
the rest by size and bytes. Two archives are compared in the same way as two versions: each one against the game, then
the two results against each other (`compare.two_archives`). Two archives of the same file name are told apart by their
folder.

## What a change is

A **target** is the thing a change belongs to: `(file, key)`.

- In an asset file (117's and 1800's `assets.xml`, the old games' asset lists) the key is the asset's GUID. The target
  is an asset that was **added**, **removed** or **changed**. A changed asset lists its **value changes**: the path
  from the asset (`Values/Building/Upkeep`, `Values/Costs/Item[0]/Amount`), the value before and the value after. A
  list that was only reordered shows no change. Entries that appeared or went show as `[+]` and `[-]`. The method is
  the version diff's (`docs/2070-versions.md`, "How the diff compares"): `sources/valuediff.py`, shared with that
  script. An added asset keeps its values compressed (a few hundred bytes: `Target.values()` gives them back), so a mod
  that adds a hundred thousand assets holds them in tens of megabytes.
- A GUID that a mod adds and that the game's file already has is reported as the later definition's values against the
  game's. The report notes "GUID n is added again" (which of the two the game keeps is not known).
- In a text file the key is the text id. A change has the one path `Text`.
- In any other XML file the file is the target (the key is empty) and the paths are from its root: `templates.xml`,
  `properties.xml`, a `.cfg` or `.fc` patch, engine settings.
- A file that is no patch (a model, a texture, a `.lua`, a sound bank) is a **file change**. It is either new or
  replaces the game's file, and it lists the mods that carry it. A replacement with the same bytes is reported as such:
  it does nothing.

Three things are left out: a changed asset whose only difference is the editor's bookkeeping
(`Standard/LastChangeTime` and the like), the binary infotips (`export.bin.xml`, kept as a file, not read), and
whatever only the game can decide (what it drops after patching).

## Conflicts

The mods are run in the loader's order. Every mod's **step** is what it changed relative to the result of the mods
before it. Two mods conflict where their steps meet:

| Kind | Meaning | Level |
|---|---|---|
| value | both set the same value path of the same target (the later one's value is what the game gets) | conflict when the values differ, **overlap** when they are the same |
| block | one replaced or removed a block (a list, a struct) the other changed inside | conflict |
| removed | one removed an asset another one changes, sets a value of, or adds again | conflict |
| duplicate | two mods add the same GUID | conflict |
| file | two mods carry a file of the same path (the later one's wins, whole, with no word in the log) | conflict |
| asset | both changed the same asset, in different values (they merge; order matters only for lists) | overlap |

Each conflict names its **winner** (the last mod of the order) and what **decided** the order: a `LoadAfter` between
the two (or the late phase), or the loader's default, the display name `[Category] Name` as bytes. Without a
`LoadAfter` the order can change when a mod is renamed. The list says so ("decided by name order"), because these are
the conflicts a mod author should fix by naming the dependency.

**A mod that sets a value an earlier mod already set** changes nothing in its step, so the step alone cannot see it.
The loader hands the run what each operation is about to put in (`Session.written`), and `moddiff/writes.py` reads the
single values that a `merge` or `replace` sets, by the path the changes use (`Values/Product/Price`). Such a mod counts
as touching the target (it is named in the target's mods). Its value is a row of the value conflict next to the
others, as an **overlap** (the same value twice loses nothing). Two mods that only set the value the game already has
change no target: the rows are there, the targets are not.

Inside a **list** (an `Item`, or a name that repeats) the entry is named by its **position in the list**
(`Values/Building/BuildCost/Item[0]/Amount`). The changes name it the same way: an entry that differs is named by its
position in the patched list, so a row and a change of the same entry carry the same number. An entry that only moved
is no change, and an entry that a mod inserts before it moves the number of the ones after it. `ModItem` (an item
found by its key) is read the same way. One that finds no item appends an entry, which is a change, not a set value.
What a `ModValue` gives (`Insert`, flags, a calculation) is known only after the operation: the loader tells the run
when it has run, and the value is then read from the document. Three cases are still left to the changes: an `Item`
that a merge appends, a `replace` of a block, and a value set through a path that is no plain node (`Content=`).

## The views

- **Changes**: a tree of files, below them the targets (added, removed, changed, with the mods that touched them), and
  below them the value changes (`path: before -> after`). A filter box matches file, key, name, template, mod, path and
  value. It is worked out off the window's thread, a quarter second after the last key. Selecting a target shows its
  values in full beside the tree.
- **Conflicts**: one row per conflict with its kind, target, path, the mods and their values in order, the winner and
  what decided it. A double click jumps to the target.
- **Dropped**: what the loader would log or skip (the `engine.Event`s), per mod, file and line, and the notes of the
  report (conditions on other mods, GUIDs added again, how 1800's set was made).
- The status line counts what the run found. The work runs off the window's thread, with progress, and a run in
  progress can be left by closing the window.

**No row is left out.** The tree and the conflict table are models (`difftree.TargetModel`, `tables.ConflictModel`) that
make a row's text only when the view asks for it. A report of a hundred and sixty thousand targets therefore shows at
once, every one can be reached, and there is no row limit. Opening the game (its file tree takes about half a minute on
117) starts in the background as soon as the game is chosen, so the first comparison does not pay for it.

**Export** (Report menu, Ctrl+E) writes the open comparison off the window's thread, in one of three forms:

- **Markdown, summary** (a report to attach to a bug or a mod page): the game, the mods and versions, the counts, the
  conflicts (the first 300), then per file the counts by kind and its first 25 targets with their first 8 values, the
  carried files grouped by folder (three levels, the largest groups first) and the dropped lines. The report says that
  it is a summary.
- **Markdown, every target and file**: all of it.
- **CSV** (for a spreadsheet): always complete, with one row per value change, conflict, added or removed target, file
  change and dropped line.

Markdown and CSV are both written line by line (`export.markdown_lines`, `export.csv_iter`).

## From the Mods app

The Mods app's **Check** menu has **Compare the selected mod in Mod diff** and **Compare the mods that are on in Mod diff**.
They ask the suite (`suite.links.DIFF_MODS`, answered by this app's `open_job`), which fills the tool bar and runs the
question. The entries are shown only where the suite has a Mod diff. A mod is passed by its ModID, a profile by its
name (1800: `activations.json`).

## Where the data comes from

- The game: `assetedit.assetgame.open_game` (the asset index with the byte range of every asset, cached in the
  workspace's `asset-cache` and shared with the Assets app). A patched asset file is read lazily from the index, so a
  mod that touches a hundred assets parses a hundred.
- The mods: `modmgr` (the folders the loader searches, a profile's mods, the order, `resolve`). A profile here is
  therefore the Mods app's profile, with the same dependency rules. A mod's file sizes come from its folder's listing
  (`modloader.mods.walk_sizes`), not from a `stat` per file.
- The run: `modloader.engine.Session`, one mod at a time, so each mod's step is known (`moddiff/effect.py`).
- The Assets app's mod import (`assetedit/modimport.py`) runs the same session over the same files: the game's readers
  (`Game.read_base`, `Game.lazy_ranges`, which `moddiff/base.GameBase` passes on too), the game's loader rules (1800's
  for a 1800 game) and the added assets kept as bytes.

## Per game

- 117 and 1800: every mode.
- 2205: archive mods (with the `file.db` index that names a patch's members), loose files, and the asset files in the
  same way as 117's.
- 2070, 1404 and 1701: archive mods and loose files; texts of the `id=text` kind are compared per id.

A game without installed files cannot be compared (the window says which folder is missing).

**1800's own rules** differ from 117's in how the set of mods is made, and the report says so. The evidence (read in
the game's code) and what is inferred are in `docs/engine-modloader.md` "Anno 1800's loader":

- A mod is on unless the `activations.json` of its mods folder lists it. The profile list of the window shows that
  file's name for 1800, and "Conflicts in a profile" means "every mod that is on" (`modloader.activations`,
  `jobs.profile_order`).
- A `.zip` in a mods folder holds mods in 1800 (a `modinfo.json` at its root, or a folder with one; a folder of folders
  is a collection). It is **read in place** (`modloader/zipmods.py`; nothing is unpacked). The files, sizes, includes
  and carried files of a zip mod come from the archive, the report is the same as a folder's, and the carried files'
  source reads `archive.zip|path inside`. Give a zip by its path (the menu of a field's `...` button has
  **Choose a .zip of mods...** under 1800) or by the ModID of a mod in a zip of the game's mods folders. A profile
  counts the zips in its notes. A `-` in front of a folder or a zip parks it (settled by the community documentation).
  117 does not unpack a zip: its mods are folders only.
- The dependency lists are top-level in `modinfo.json` (`LoadAfterIds` ...). The reader takes both shapes. The order is
  the same.
- The ModOps themselves: 1800's code was not read for them. The community loader's documentation and source (MIT, read
  for behaviour only) say which are 117's own: the short forms (`Merge=` and the others), a bare `<Asset>`,
  `Property=`, `MaxRepeat`, `ModItem`, `ModValue` and options. Under 1800 the first four are **left out and listed in
  Dropped** (a `feature` line). The type name is read in any case, as the source reads it. `ModItem`, `ModValue` and
  options are still applied as 117 applies them (what 1800 makes of them is unknown). `Group` with `MaxRepeat` loops in
  117, as the documentation and the source say. The patch targets are 1800's own
  (`data/config/export/main/asset/`). The table with its marks is in `engine-modloader.md` "Anno 1800's loader".

Note: the game's code was read in 2026-11.

## Speed and memory

The test case is the merged `anno2070` mod (160,650 added assets, 1,375 changed, 18 patch files, 476,754 carried files)
against 117. Before the work described here, a comparison took about 158 s and wrote an 88 MB Markdown report. The
numbers below are for the same mod and game, measured on a machine that other work was loading to 100 %. The CPU time
of the process is therefore the fair number, and the wall clock is not:

| | First round | Now |
|---|---|---|
| CPU time of the comparison | 124 s before, 36.8 s after | 47.9 s (a run on the loaded machine; 31 s to 48 s in three runs, the kept bytes and their searches cost a few seconds) |
| Wall clock of it, under that load | 207 s, then 62 s | 50 s |
| Markdown report | 88.4 MB | summary 0.1 MB (the full one, 88.5 MB, and the CSV, 104.7 MB, on request; written in 1 to 3 s) |
| An added asset's values kept in the report | 1,118 bytes of JSON text | 390 bytes compressed |
| **Peak memory of the process** | 2.4 GB (2,429 MB) | **1.3 GB (1,312 MB: the opening of the game's index, the run itself stays below it)** |

Where the time went (a profiled run, one core) and what was done:

| Cost | Before | Now |
|---|---|---|
| A `stat` for the size of each of 476,754 carried files | 66 s | 0 (sizes from the folder listing, `walk_sizes`) |
| Rebuilding the lookup table of `LineId` on every text operation (94 times over 49,000 entries) | 20 s | 12 builds: the table is kept while the children of a text that it does not read change (`PatchDoc.changed(tags=...)`) |
| Plain-path key reads by the element tree's path search (4.5 million) | 15 s | read by hand, a third of it |
| The same added asset read three times (two data trees, two JSONs) | 16 s | once, kept compressed |
| A text or data file read as data before and after every step | 4 s | once per file, and again only when an operation reached it |
| Opening the game's file tree | 27 s | the same, but started when the window opens, not when Compare is pressed |

**The peak.** A profile of the memory (the process's working set at each stage) found where the 1.1 GB of the run
went. The 14 text files and `assets.xml` of the mod were **all opened before the first operation** (870 MB: each
language's document is 55 MB, with the snapshot of its 49,000 texts). The patch file's tree and its line table took
110 MB, and the 160,000 added assets as element trees took the rest. Two changes followed, both along the loader's own
structure:

- **A file at a time.** A mod's patch files are applied in order. Each file's document is opened when its first patch
  file comes, compared with its snapshot when its last one is done, and **let go as soon as no later mod patches it**
  (`Run.step`, `Run._finish`: the target's result against the game is made at that moment, while the document is there).
  Only the file in work is held.
- **Added assets as bytes.** The loader keeps an asset of the game's file as its bytes until an operation looks inside
  (`PatchDoc.freeze`). An asset that a mod adds is kept the same way once it is in the document: its values are read
  first (packed), its tree is dropped, and its bytes go to the end of the document's buffer. A later operation that
  looks inside reads it back. The comparison reads it without keeping it (`PatchDoc.peek`). A search that needs every
  asset that holds a text (`//Values[Standard/GUID='5']`) scans the new bytes once per text it looks for. The Assets
  app's import keeps them the same way and reads each with `peek` when it turns the result into a change set.

What is left of the 1.3 GB is the game's own index and the 476,754 carried files' records. A record keeps the mod's own
path string where it is already lower case. It keeps its source as a pair (the mod's `source_root`, shared by all its
files, and the path), not as a joined string. On a synthetic set of that size the records took 99 MB instead of 216 MB
(`tracemalloc`; the real run was not measured again).

Note: measured on 2026-11-13 and 2026-11-14; the memory profile and the record sizes are from 2026-11-14.

## Not built, and not checked in the game

- Only data was compared: no mod was started in the game for this app. Every rule that `docs/engine-modloader.md` marks
  "not checked in game" is as uncertain here as in the loader reproduction.
- Conditions on other mods take the branch the compared set gives (for a single mod: none of the others is on). The
  report lists each one.
- Added assets are compared between two versions by their values. Their position in the file is not compared.
- 1800: `ModItem`, `ModValue`, options and `@guid` paths are applied as 117 applies them (the documentation says they
  are 117's; what 1800 does with them is not known). Whether an `activations.json` applies to its own folder only (as
  read here) or to all folders was not settled. For a zip with island files, the community documentation warns that
  they misbehave from a zip. No 1800 mod was run in the game.
- A `ModOp` that loops with `MaxRepeat` and `Condition` follows 117's code as it was read. The community source repeats
  a `Group` only. This is currently unverified.
