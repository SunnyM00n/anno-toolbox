# The mod manager: a user's guide

This is the **Mods** app of the tool suite ([`suite.md`](suite.md)). It lists the mods installed for Anno 117 and
Anno 1800, and switches them on and off in the game's profiles. It shows the order the mod loader gives them and what
is wrong before you start the game, and it reads what the last start said. It also has an **Installs** page for the
archive games (see below). The rules it follows are the loader's, as recorded in
`engine-modloader.md`. Where that document says "not checked in game", so does this guide.

Start it with `python -m annotoolbox --app mods`, or press the **Mods** button of the suite. The game folders and
the mods folders are the suite's settings (File > Settings in the island editor). An empty mods folder
setting means the standard place, `<Documents>/Anno 117 - Pax Romana/mods` or `<Documents>/Anno 1800/mods`.

## The window

- **Game** and **Profile** at the top. The profile open here is not always the active one (the one the game starts
  with, marked `(active)`).
- **The mod tree**: every folder the loader searches. These are the game's `mods/.ubi` and `mods`, the Documents
  `mods`, and for 117 mod.io's folder under the Windows public folder. 1800 has the first three of these but not
  mod.io's. A check box switches a mod on or off in the open profile. A mod inside another mod's folder hangs below it
  and follows it. Grey rows are folders the loader would not take, with the reason: no or bad `modinfo.json`, a wrong
  game number, a name starting with `-` or `.` (parked), or a second copy of a ModID that the loader keeps at its
  higher version.
- **Details** (right): name, ModID, version, category, folder, where it was found, its state in the profile,
  description, difficulty, the four game-setup flags, its dependencies (`Require`, `Optional`, `LoadAfter`,
  `Incompatible`, `Deprecate`), options, nested mods, its problems and what the last start's log said about it.
- **Load order**, **Problems** and **Last start** tabs below.

## Profiles

`mods/profile.txt` names the active profile, and `mods/profiles/<name>.txt` lists mods by ModID, one per line. A `#` in
front switches a mod off. `EnableNewMods` (or `# EnableNewMods`) switches on every unlisted mod of the mods folders,
but never Ubisoft's `.ubi` examples.
The manager writes exactly that layout, the game's own comments included, and changes only the line of the mod you
touched.
Your comments, blank lines, order and line ends stay. The menu
**Profile** has: new (empty), copy, rename (the active profile follows), delete (the active one is refused), make
active, and "No mods at the next start" (an empty `ActiveProfile`). A file is written whole beside the old one and then
moved over it.

A mod listed twice (`foo` and `# foo`) counts by its last line, in the tree, the load order and the asset editor's
import alike.
The same holds for two `ActiveProfile` lines, and "make active" rewrites both.
The rule is inferred (see `engine-modloader.md` "Where mods live"). A profile saved in ANSI (Windows-1252) or with a
byte order mark is written back the way it was read, and each line keeps its own line end.

A one-word `# note` line reads as a switched-off mod, as it does in the game. The game rewrites the active profile at
every start and hot reload, with its own `# not installed` notes.
Your ids stay.

## Order and problems

The **Load order** tab shows what the loader computes, with the same code the asset editor's mod import runs: the
display name `[Category] Name` as bytes, `LoadAfter`, the late phase, nested mods with their top mod, and `Deprecate`d
mods left out. The column "Why here" says how a mod came in. The **Problems** tab is filled at every change, from the
profile alone:

| Rule | Meaning |
|---|---|
| M01 | a `Require`d mod is missing, or installed but not on in this profile (the loader logs it and loads the mod anyway; a DLC named there also shows, the manager does not know the DLCs) |
| M02 | a `LoadAfter` circle |
| M03 | an `Incompatible` pair is on together (both load) |
| M05 | a normal mod wants to load after a late one |
| M06-M08 | notes: a mod another one replaces, two copies of a ModID, an `Optional` mod that is not on |
| C01-C03 | notes: a folder the loader skips, a second copy, a profile line with no installed mod |
| I01-I07 | `modinfo.json` problems (117's rules: `Anno` 8, `Difficulty`, `ModID`, `Version`, `GameSetup`) |

**Check > Check the mods that are on** (F6) runs in the background and adds the slow findings to the same tab. The
first is the mod lint of every mod that is on (`modlint`: the silent mistakes of `engine-modloader.md`). The second
needs the game's folder to be set: every ModOp is applied to the game's files in the loader's order, and each operation
that matches nothing is listed as `op:nomatch` with its file and line. Without the install only the lint runs. 
The status line names the mod the check is at, and **Stop** at its right ends the check before the next mod.
A stopped check reports nothing.
The findings are those of the profile as it was checked.
They go when another profile is picked, when a mod of the profile is switched or `EnableNewMods` changes, and when the
mods folder is read again.
A check that ends after such a change says so and is not shown.

**Check > Compare the selected mod in Mod diff** and **Compare the mods that are on in Mod diff** open the Mod diff app
([`mod-diff.md`](mod-diff.md)) with that question and run it (a mod against the game; the open profile's conflicts, in
the loader's order). They are shown only where the suite has a Mod diff; the app on its own has none. For 1800 the
question is "every mod that is not switched off in `activations.json`".

## Last start

This tab reads `mods/mod-loader.log` per mod: enabled, loaded, the patch time (the time between its `Load` line and
the next), and every line that is a problem (a level other than information, a file position, "No matching node" and
the other loss messages). The summary line gives the loader's own count, the total time and the log's age. The log is
rewritten at each start. It is the only feedback the game gives.

## Install and remove

**Mods > Install a mod folder / a zip** copies each mod it holds into the mods folder. It takes a folder, a folder of
mods, or a zip with the mod at its top or one folder down. A zip entry that reaches outside its folder is refused. The
folder gets the mod's own folder name, or its ModID. A name that is not one plain folder name (a path, `..`, a drive, a
device name such as `con`) is refused. Every mod of one install is checked first: if one is refused, none is installed.
The copy runs in the background.
The status line says what it is at (the zip being unpacked, the mod being copied).
**Stop** at its right ends it between two files unpacked, two folders copied or two mods.
The staging folder is then removed, and a mod that is not in whole is not in at all.
The status line names the mods that were installed until then, and the running game is told of those.

An older copy of the same ModID is replaced in three steps. The new one is copied into a staging folder, the old
folder is renamed aside, and the new one is renamed into its place (the old one comes back when that fails). Only then
does the old one go to the recycle bin (it is kept as `.modmgr-old-...` if the bin refuses). A folder with another
ModID under the same name is never overwritten.

**Install the built project** does the same for the open project's build (the Project window). It refuses a folder
that holds another mod.

**Import the selected mod as a project...** makes a copy of the selected mod in a new project folder (hard-linked
where the disk allows) and opens it as the project of the other apps; the installed mod is never changed. The copy runs
in the background and **Stop** ends it (`docs/mod-import.md`).

**Remove** moves the mod's folder to the recycle bin, or to a `.removed` folder beside it where the system has no bin.
A mod folder that is a junction or symlink (a working copy linked in) is removed as the link only; its target is never
touched. The shell is told to warn when a folder cannot go to the bin (a network drive) instead of deleting it
silently. A nested mod is removed with its whole mod. The profiles keep the mod's line. The copy runs in the
background. The code is `modloader/modswap.py` (``place_folder``, ``swap_in``, ``recycle``).

## The game is running

The loader reads profiles when the game starts and at a hot reload. A banner appears while the game's process (or a
live-link game) is there, and every profile change then asks first. There is one trap: **Shift+F3 loads the active
profile**, and the game rewrites that file. A change to another profile does nothing in the running game, and a change
to the active profile can be written over. **Game > Reload in the running game** asks the live link
([`live-link.md`](live-link.md)) for the asset and text reload and names the active profile in its answer. Without a
link it says to press Shift+F3 with the hot reload mod on. Every switch, install and removal makes one announcement to
the link and puts its sentence on the status line.

Nothing here blocks the window.
These all run on a background thread (`suite/background`) and say their result on the status line when done:

- the look for the game's process (about half a second per look, every 5 seconds while the page is shown)
- the reload (the game answers within seconds, up to 3 minutes)
- the install of the project's build and every other install
- every removal (the move to the recycle bin), check and restore

A change that asks first uses the last look's answer, so a game that started in the last 5 seconds is noticed one tick
late.

## Installs and loose files

The window's second page, **Installs**, is for the games that have no mod folder: Anno 2205, 2070, 1404 and 1701. Their
islands go into an archive (2070: `patch9.rda`, 1404: the last `data` archive of each group, 1701: a new `data<n>.rda`,
2205: an archive of ours plus the changed `file.db`). The island editor keeps the game's own file first
([`island-editor-tools.md`](island-editor-tools.md) "Build, install, restore").

- **What the Toolbox installed**, one row per game: whether an archive of ours is in the install, which files, and
  which copies of the game's own files are kept for the way back (they live in the workspace). **Restore the game's own
  files** is the island editor's own restore (the same code). It asks first and runs in the background. A game whose
  folder is not set shows "no folder set". The Flash UI editor's `flash-ui.rda` for 2205 is restored in that editor.
- **Loose files** (below the list): the other way to change a game's files, with no archive touched. With
  `PreferLocalFiles` on in `engine.ini`, the games that read it take a loose file under the install (`data/...`) over
  the archive's copy. The page shows where each game's `engine.ini` is, whether the setting is on, and the loose mods
  installed.
  The `engine.ini` files are under `Documents`.
  Only 2205's folder name was seen, and the others are the games' usual names.
  The first folder that holds a file is used.

  **Turn PreferLocalFiles on/off** changes only that value in `engine.ini`. The file's own spelling and encoding stay,
  and the file as it was is kept once in the workspace under `loose-mods/<game>/`.

  **Install a folder of loose files** copies a folder laid out like the install:

  - A file that is already there is kept and comes back on **Remove** (also when the mod's file was deleted by hand).
  - A file changed since the install stays and is named (its kept original stays in the workspace too).
  - A path another loose mod owns is refused.
  - Nothing leaves the install folder (no `..`, drive, stream name or `a.` path, and no link inside the install that
    leads out).

  An install that fails half way puts every file back. Every write (the files, the manifest, `engine.ini`) is made
  whole beside the old file and then moved over it. Manifests are in the workspace under `loose-mods/<game>/mods/`.

**Off until confirmed.** Whether each game reads loose files was never seen in a game, and 2070 is expected not to
read them (archives always first). The loose actions are therefore off for every game. The table `LOOSE_CONFIRMED` in
`modmgr/archivegames.py` lists the games where it was seen to work, each with its date
(`{"anno1404": "2026-11-20"}`), and the page enables the actions for exactly those. Nothing is written to a game that
is not listed.

**The check, per game** (about five minutes each; do it on a copy of the install if the game's files matter to you):

1. Start the game once so that it writes `engine.ini` and a log, then close it.
2. In a scratch folder, lay out one small file that the game reads at start and shows, in the game's own folders (a
   text or tuning file under `data/...`, taken from the game's archive with the Viewer's export). Change one visible
   value in it (a number, a name).
3. Temporarily add the game to `LOOSE_CONFIRMED` (or construct `CLooseGame(key, install, workspace, force=True)` in a
   Python shell). Turn **PreferLocalFiles on**, run **Install a folder of loose files** with that folder, and start the
   game.
4. If the change shows, loose files win in that game. If it does not, remove the loose mod, turn the setting off, and
   leave the game out of the table. Write the result in `engine-notes.md` with the date either way.
5. Also note where the game looked: relative to the install folder (what the page assumes), or to another folder.

A game that fails the check keeps its archive delivery. A game that passes can take loose-file mods from the same page
and, later, a loose delivery in place of its archive.

## What has been checked

Tests on synthetic mods folders and profiles cover the profile round trip, the profiles folder, the catalog, the plan,
the log, install and remove (the recycle bin replaced by a fake), the guard and the window offscreen. The process list
is never run in a test: `tests/conftest.py` replaces `gameguard.system_lister`. The tests also cover the Installs page,
`engine.ini` edits and loose mods on synthetic folders. The installed games are only read, when `ANNO*_DIR` names them.

**Not tried in a game:**

- loose files in any archive game (see above)
- the Installs page's restore on a real install
- that the game reads a profile written here (the layout is copied from its own file)
- that an empty `ActiveProfile` written as `ActiveProfile ` starts it without mods
- 1800's profiles. The same layout is assumed. 1800 is listed, checked and installed to, but its profile behaviour and
  mod.io folder were not seen. The game's code decides by `activations.json` instead of a profile
  (`engine-modloader.md` "Anno 1800's loader"), which this app does not show yet
- the recycle bin on other systems

## Language

The mod manager's windows are in English or German. Choose the language in the settings (General page,
"Language"); it applies the next time the app starts. Game data (asset and file names, the games' own texts) is shown
as the game has it. See [the suite's guide](suite.md#language).
