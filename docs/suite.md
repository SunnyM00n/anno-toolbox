# The tool suite: a user's guide

The suite is one window with an app for each kind of work on the Anno games' files. All apps are built on one shared
core: the games' file formats, their installs, the mod loader's rules, the settings, the theme and one undo stack.
Each app is also a separate program. The window puts them side by side and lets them share a project.

| App | What it edits | Guide |
|---|---|---|
| Islands | the islands of every Anno game, in 3D, as the game draws them | [`island-editor.md`](island-editor.md) |
| Assets | a game's assets (buildings, goods, needs, quests...) in forms made from the game's own schema | [`asset-editor.md`](asset-editor.md) |
| UI 2205 | Anno 2205's Flash interface movies, pictures and icons | [`ui/2205/flash-ui-editor.md`](ui/2205/flash-ui-editor.md) |
| Viewer | any game's files as the game reads them: models in 3D with their parts and animations, textures, effects, sounds | [`viewer.md`](viewer.md) |
| UI | Anno 117's and 1800's interface files: scenes and widgets previewed with sample values, bindings as a node graph | [`ui-editor.md`](ui-editor.md) |
| Mods | the installed mods of 117 and 1800: profiles, the loader's order, problems before the start, the last start's log, install and remove | [`mod-manager.md`](mod-manager.md) |
| Objects | a model config of any game: its parts, materials, effects, lights, sequences and the info file's boxes, with the 3D view following; saved as a new file, a replacement or a patch | [`object-builder.md`](object-builder.md) |
| Sound | the Wwise banks of 117, 1800 and 2205 (events, containers, sounds, music, clips replaced) and the older games' sound database | [`sound-editor.md`](sound-editor.md) |
| Texts | a game's texts in two languages side by side: untranslated and outdated rows, placeholder and markup checks, a glossary, CSV / XLIFF / PO in and out, written into a project as ModOps or text files | [`text-editor.md`](text-editor.md) |
| Mod diff | what a mod changes in the game (asset by asset, value by value), two versions of a mod, conflicts between the mods of a profile, archive mods against the game; a report as Markdown or CSV | [`mod-diff.md`](mod-diff.md) |
| Maps | a game's sessions as maps: 117's and 1800's map templates, 2070's, 1404's and 1701's worlds; islands, random slots and start positions placed from above, the size and playable area, checks, a map grown on any side with every position moved, written as a mod | [`map-editor.md`](map-editor.md) |
| Saves | every game's savegames: found in their folders, read as a summary (version, mods, players, money, sessions and islands, population, quests), a tree of every part and forms; the values whose layout is known (117: the save's name, the player, money; 2070: money) written as a new file beside the original, never over it; the mods a save needs, linked to the Mods app | [`save-editor.md`](save-editor.md) |
| Particles | any game's particle effects: 117's, 1800's and 2205's `.rdp`, 2070's and 1404's `.efc`, 1701's brace `.efc`, opened from a file or a model; the emitters as a tree of forms, curves as graphs, colours as pickers, played live (play, pause, scrub, loop), undo; saved into the project as the effect at its path, a copy for one model or a new file | [`particle-editor.md`](particle-editor.md) |

The suite needs **your own installs** of the games to work. It carries no game files on its own, and nothing it writes
contains any.

## Start it

```bash
pip install -e ".[gui]"           # the full Toolbox: PySide6, moderngl, numpy, scipy, Pillow
python -m annotoolbox --app islands     # the suite's window, on the Islands app
python -m annotoolbox --app assets      # ... on the Asset editor
python -m annotoolbox --app ui          # ... on the UI editor (117 and 1800)
python -m annotoolbox --app islands --smoke   # open every app once and close: a check that they start
python -m annotoolbox                         # the island editor on its own
```

**Without the need to separately install Python.** The Toolbox is also provided as a standard Windows installer
(`AnnoToolbox-<version>.msi`). It installs per user, needs no administrator, and adds a Start-menu shortcut and
`anno-toolbox.exe` for the command line. It carries no game files on its own; the games are automatically searched for
on your computer. [`installer.md`](installer.md) says what goes in, how upgrades and uninstall work, why the installer
is unsigned and how to verify a download.

**Names.** The suite is called "Anno Toolbox". The package is `annotoolbox`, and the command and the repository are
`anno-toolbox`. The commands are `anno-toolbox rdm ...`, `anno-toolbox export ...` and `anno-toolbox` alone for the
window. `anno-toolbox` is the light install, and `anno-toolbox[gui]` is the full one; both install from the
repository (its README), not from PyPI.

**Two installs, one program.** `pip install <toolbox>` is the **light install**. It has no window and uses only the
standard library. It gives you the command line:

- `<toolbox> rdm export|import|inspect|diff|validate|relayout|layouts`: the models of the games as glTF and back
  ([`rdm-gltf.md`](rdm-gltf.md))
- `<toolbox> export`: any game file as stored and in a converted form ([`exporting.md`](exporting.md))

`pip install "<toolbox>[gui]"` adds the windows. Starting a window without it says so, and says what to install. The
core (the file formats, the games' installs and the mod writing) imports no window library.

The Viewer, the Islands app and the Assets app share the exports. The viewer has **File > Export...**, **Export the
selection or folder...**, **Export the files the filter shows...** and **Import a model...**. An object's **Export the
model** in the island editor and an asset field's **Export** button ask the viewer through the suite's links
(`export_files`), and the viewer's dialog opens on those files.

The window has the apps down its left side (Ctrl+1, Ctrl+2, Ctrl+3) and the **Project** button under them. An app's
page is made the first time you open it, so the window opens as fast as its first app. When you close the window, every
app that holds unsaved work asks first. The game folders are set once, in the island editor (File > Games and folders),
and are the same for every app. The settings are kept under `%LOCALAPPDATA%/anno-toolbox`.

## A project

A **project** is one folder for one mod. Every app keeps its work in it, so one build writes one mod with all of it:

| What | Where, inside the project folder |
|---|---|
| The mod's identity and its GUID range | `project.json` |
| Island edits (Islands) | `island-edits/` |
| The asset change set (Assets) | `asset-edits/<game>.json` |
| The Flash interface edits (UI 2205) | `ui-edits/anno2205.json` |
| The edited maps (Maps), the reports of their growth, the GUIDs of new maps | `map-edits/<game>/`, `map-edits/reports/`, `map-edits/map-guids.json` |
| The GUIDs of new islands, kept so a savegame finds them again | `island-guids.json` |
| The built mod, or the built archives | `build/<mod id>/` |

A folder without a `project.json`, such as the workspace of an older version, is a project with default values.
Nothing moves, and the edit files stay where they were. **Use the workspace** in the Project window goes back to it.

**Project > New** takes an empty folder. Give the mod's id (lower-case letters, digits and `-`), the game and a GUID
range. The range is the part of the GUID space that your new assets and islands take their numbers from. Choose one
that no other mod you play with uses. **Choose...** offers three ways:

- **My own range** is the default for a new project. It is 100,000 numbers of a reserve the suite keeps for you in its
  settings, outside every published and every vanilla range and apart from your other projects.
- **A publicly declared free range** is the community's personal-use range or a free block of the registry, with its
  source. A range you share must be claimed with the community first; the window links how.
- **A range you type** is checked by the window against the game's own GUIDs, Ubisoft's safe range, the published
  ranges and your other projects.

A project whose islands are numbered cannot move its range away from those numbers.

**New islands** that are saved as new ones (not replacing the game's) take the next free number of the range, and keep
it. `island-guids.json` remembers the number, so a savegame that holds the island still finds it after the next build.
A project that already built islands before the range existed keeps those islands' numbers. Only islands added later
use the range.

## Build and install

**Build mod** collects what each app holds for the project's game and writes `build/<mod id>/`. **Build & install**
also copies it to the place the game reads it from. The log lists what each app contributed and what the checks found.

| Game | What the build writes | Install |
|---|---|---|
| Anno 117, Anno 1800 | one mod folder: one `modinfo.json`, the ModOps files of every app merged into one per path (the apps' operations in order), the island files | copied into the game's mods folder (a setting) |
| Anno 2070 | `maindata/patch9.rda`: the edited islands and, whole, every asset file the change set touches, over the files of the game's own `patch9.rda` | replaces `patch9.rda`; the game's own is kept in the workspace first |
| Anno 1404 | the last `data` archive of each group a file is read from, holding the edited islands and asset files beside what it held | replaces those archives; the game's own are kept first |
| Anno 2205 | one archive with the changed files (session maps, assets, interface files) and the game's archive index pointed at it | the archive and the index are copied into the 2205 folder to patch (a setting); the game's own index is kept first |

For the mod games, the build stops when two apps write one file in ways that cannot be merged, and it names the path.
For the older games, two apps cannot write the same game file with different contents either. After a build, the island
editor's mod check and the mod loader's lint run over the mod and list what the loader would silently drop.

To undo an install in an older game, use the island editor's restore buttons. They know the kept files.

## The link to the running game

The status bar of the window shows whether a running Anno is connected ("game link: not connected"). The link is a
named pipe that the helper library opens in the game when it is switched on in its settings. The suite looks for it
every few seconds.

While no game is connected, the apps tell you after a build what to do by hand ("press Shift+F3 in the game", "restart
the game"). Once a game is connected, they ask it to reload what it can, and say so. In a session the game is paused
for an asset reload and then set going again. An object you ask to edit in the game opens in the app that edits it.

Note: none of this has been tried in a game yet.

[`live-link.md`](live-link.md) says what each kind of file needs and how the link works: section 8 for the part built
into the suite and section 9 for the transport. The debug-command palette (`suite/debugcommands`, `suite/linkpalette`)
is described in `debug-palette.md`.

## What has been checked

The apps, the project and the packing are tested on synthetic data, and the windows were looked at offscreen on the
author's installs. **Nothing the suite writes has been loaded by a game yet.** Only the game can confirm that an
edit is drawn or that a mod loads. The notes of each app list what was looked at and what is still open.

## Language

Every app's windows, menus, tool tips, dialogs and messages are in English or German. English is the default. The
language is a setting of the suite (Islands, File > Games and folders, "Language") and is shared by all apps. The
setting lists every file found in the suite's `infra/lang/` folder. It takes effect the next time an app is started, so
a window is never half in one language. Setting the environment variable `ANNO_TOOLBOX_LANG` to `en` or `de` overrides
it for one run. A text that the German file lacks is shown in English. To add another language, copy `en.json` to
`xx.json` in that folder and translate the values.

The German texts follow the games' own German interface where it has the word (Insel, Mod, Baumenü, Gebäude), and the
modding community's words otherwise. Some things stay as they are: the games' own names (assets, buildings, goods,
properties), file names and paths, the mod loader's log, and the command line's output.
`tool-suite.md` says how strings are marked and how
the language files are kept.
