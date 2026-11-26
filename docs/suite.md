# The tool suite: a user's guide

The suite is one window with an app for each kind of work on the Anno games' files. All apps are built on one shared
core: the games' file formats, their installs, the mod loader's rules, the settings, the theme and one undo stack.
Each app is also a separate program. The window puts them side by side and lets them share a project.

| App | What it edits | Guide |
|---|---|---|
| Islands | the islands of every Anno game, in 3D, as the game draws them | [`island-editor.md`](island-editor.md) |
| Assets | a game's assets (buildings, goods, needs, quests...) in forms made from the game's own schema | [`asset-editor.md`](asset-editor.md) |
| Viewer | any game's files as the game reads them: models in 3D with their parts and animations, textures, effects, sounds | [`viewer.md`](viewer.md) |
| UI | the interface files of Anno 117, 1800, 1404 and 1701 (scenes and widgets previewed with sample values, bindings as a node graph) and Anno 2205's Flash interface movies, pictures and icons, and the old interface of 2070, 1404 and 1701 (read only), on three tabs of one app (Phoenix, Flash and the old interface) | [`ui-editor.md`](ui-editor.md), [`ui/2205/flash-ui-editor.md`](ui/2205/flash-ui-editor.md) |
| Mods | the installed mods of 117 and 1800: profiles, the loader's order, problems before the start, the last start's log, install and remove | [`mod-manager.md`](mod-manager.md) |
| Objects | a model config of any game: its parts, materials, effects, lights, sequences and the info file's boxes, with the 3D view following; saved as a new file, a replacement or a patch | [`object-builder.md`](object-builder.md) |
| Sound | the Wwise banks of 117, 1800 and 2205 (events, containers, sounds, music, clips replaced) and the older games' sound database | [`sound-editor.md`](sound-editor.md) |
| Texts | a game's texts in two languages side by side: untranslated and outdated rows, placeholder and markup checks, a glossary, CSV / XLIFF / PO in and out, written into a project as ModOps or text files | [`text-editor.md`](text-editor.md) |
| Mod diff | what a mod changes in the game (asset by asset, value by value), two versions of a mod, conflicts between the mods of a profile, archive mods against the game; a report as Markdown or CSV | [`mod-diff.md`](mod-diff.md) |
| Maps | a game's sessions as maps: 117's and 1800's map templates, 2070's, 1404's and 1701's worlds; islands, random slots and start positions placed from above, the size and playable area, checks, a map grown on any side with every position moved, written as a mod | [`map-editor.md`](map-editor.md) |
| Saves | every game's savegames: found in their folders, read as a summary (version, mods, players, money, sessions and islands, population, quests), a tree of every part and forms; the values whose layout is known (117: the save's name, the player, money; 2070: money) written as a new file beside the original, never over it; the mods a save needs, linked to the Mods app | [`save-editor.md`](save-editor.md) |
| Particles | any game's particle effects: 117's, 1800's and 2205's `.rdp`, 2070's and 1404's `.efc`, 1701's brace `.efc`, opened from a file or a model; the emitters as a tree of forms, curves as graphs, colours as pickers, played live (play, pause, scrub, loop), undo; saved into the project as the effect at its path, a copy for one model or a new file | [`particle-editor.md`](particle-editor.md) |
| Converter | single files, assets and islands of any game into other formats: models (RDM, Granny, glTF), textures (DDS, PNG, EXR, BC7), sounds (Wwise, WAV, Ogg), movies, UI files, texts, islands, RDA archives, data; each entry a command of the command line with its options as a form, run on a list of files or folders, into a folder of yours or the project's | [`converter-app.md`](converter-app.md) |

The suite needs **your own installs** of the games to work. It carries no game files on its own, and nothing it writes
contains any.

## Start it

```bash
pip install -e ".[gui]"           # the full Toolbox: PySide6, moderngl, numpy, scipy, Pillow
python -m annotoolbox --app islands     # the suite's window, on the Islands app
python -m annotoolbox --app assets      # ... on the Asset editor
python -m annotoolbox --app ui          # ... on the UI editor (every game's interface files)
python -m annotoolbox --app islands --smoke   # open every app once and close: a check that they start
python -m annotoolbox                         # the island editor on its own
```

**Without the need to separately install Python.** The Toolbox ships as a Windows installer
(`AnnoToolbox-win-Setup.exe`), the one way to install it. The lines above run a developer's checkout. The installer:

- installs per user and needs no administrator;
- starts the program with a short setup page (language, game folders, the Start menu entry and desktop shortcut, the
  update check);
- adds `AnnoToolbox.com` (the console build, beside `AnnoToolbox.exe`) for the command line.

It carries no game files on its own. The games are automatically searched for on your computer.
[`installer.md`](installer.md) says what goes in, how updates and uninstall work (Help > *Setup...* and
*Uninstall...*), the move from the older installer, why the installer is unsigned and how to verify a download.

**Names.** The suite is called "Anno Toolbox". The package is `annotoolbox`, and the command and the repository are
`anno-toolbox`. The commands are `anno-toolbox rdm ...` and `anno-toolbox export ...` in a checkout, and
`AnnoToolbox rdm ...` and `AnnoToolbox export ...` in the installed program. On Windows `AnnoToolbox.com` runs in a
terminal and `AnnoToolbox.exe` is the window, as with `devenv.com`. On Linux there is one program.

Started without a command, the installed console program (`.com`) prints its help, and the window program (`.exe`)
opens the window. A checkout's `anno-toolbox` alone opens the window.

The Toolbox ships as the installer only. It is not on PyPI. A checkout of the repository (its README) installs light
(`pip install -e .`) or full (`pip install -e ".[gui]"`).

**Two installs of a checkout, one program.** `pip install -e .` is the **light install**. It has no window and uses only
the standard library. It gives you the command line:

- `<toolbox> rdm export|import|inspect|diff|validate|relayout|layouts`: the models of the games as glTF and back
  ([`rdm-gltf.md`](rdm-gltf.md))
- `<toolbox> export`: any game file as stored and in a converted form ([`exporting.md`](exporting.md))
- `<toolbox> texture bc7` and `<toolbox> sound wem`: pictures as BC7 textures with their mips, audio files as Wwise
  sounds (numpy and Pillow, or ffmpeg for Vorbis; [`converter-app.md`](converter-app.md))

The Converter app (`--app converter`) runs these commands, and every other command that turns one file into another
format, from a window: [`converter-app.md`](converter-app.md).

`pip install -e ".[gui]"` adds the windows. Starting a window without it says so, and says what to install. The
core (the file formats, the games' installs and the mod writing) imports no window library.

The Viewer, the Islands app and the Assets app share the exports. The viewer has **File > Export...**, **Export the
selection or folder...**, **Export the files the filter shows...** and **Import a model...**. An object's **Export the
model** in the island editor and an asset field's **Export** button ask the viewer through the suite's links
(`export_files`), and the viewer's dialog opens on those files.

The window has the apps down its left side (each with a key, "Keys of the suite" below) and the **Project** button
under them. An app's page is made the first time you open it, so the window opens as fast as its first app. Until it
shows, a start-up screen with the program's name and the step being loaded is up. When you close the window, every app
that holds unsaved work or a running job asks first, all of them before any app lets go of its work: a Cancel in one
keeps every app as it was. A running project import or build asks as well; closing then stops the import, which keeps
its work and is offered again at the next start. The game folders are set once, in Settings > Settings…, and are the same
for every app. The settings are kept under `%LOCALAPPDATA%/anno-toolbox`.

## Keys of the suite

- **Switching apps**: Ctrl+1 to Ctrl+9 for the first nine apps in the switcher's order, **Ctrl+0** for the tenth, then
  **Ctrl+Shift+1** and **Ctrl+Shift+2** for the eleventh and twelfth (the scheme goes on to Ctrl+Shift+9). The key is
  in each app's tool tip in the switcher.
- **Menus** have an access key on every entry (Alt, then the underlined letter). The letters are the same in English
  and German, and no key is given twice in a menu. The island editor's ribbon has them on its tabs and buttons and
  takes Tab for keyboard focus (a click leaves the keys with the view). A button whose menu entry is the same action
  shares its letter.
- **Search**: Ctrl+F goes to the search or filter box of the window (the file lists, the asset tree, Texts, Sound,
  Maps, Mod diff, the UI and Flash editors).
- **Buttons and fields**:
  - Play in Sound is Ctrl+Space.
  - Texts' two voice buttons have access keys.
  - In the target text, Tab goes on to the next control.
  - Mod diff's Compare and the Saves app's Look again are F5, and Open a file is Ctrl+O.
  - The Mods app's Remove the selected mod is Del.
  - The road piece tool saves with Ctrl+S (S alone picks its select tool).
- **Closing or leaving** with unkept edits asks **Save / Discard / Cancel** in every app.
- **A greyed control says why** in its tool tip or status text. The cases are:
  - an install while a job runs;
  - a Write button without an editable save;
  - the rendering quality without an island of 117 or 1800;
  - an island property that is only shown;
  - an island build target without a route.

Note: these keys are the decisions of 2026-11-17.

## The menu bar

The window has **one menu bar**, one place for the menus every app has. The apps' own bars are hidden while their page
is in front.

| Menu | What is in it |
|---|---|
| **File** | *Project...* (the Project window: new, open, build, install), *Open game* (every app follows, "One game for every app"), **Save** (the front app's save, greyed with the reason when it has no document), then the front app's own File entries, *Quit* (Ctrl+Q) |
| the front app's menus | Edit, View, Rendering, Tools, Check, Add, Map, Profile, ... as the app has them, between File and Settings; they change with the app |
| **Settings** | *Settings...* (the one settings window: language, theme, update check; game folders and workspace; the 3D views' device, hardware acceleration and the game's own shaders; the island editor's start values and Performance switches), *Game link...*, *Debug commands*, *Manage apps*, *Setup...*, *Uninstall...*, *Remove unused game shaders...* |
| **Window** | *Full screen* (F11), *Apps* (the switcher's keys), then the app's own docks and panels (the Layout menu of the island editor is its Window menu here) |
| **Help** | the app's own help (the island editor's *Controls...*), *Check for updates...*, *Check for updates at start*, *About the Toolbox...* |

The apps' own windows (`desktop`, `viewer`, ... run alone) keep all their menus. These apps take part in the common
entries:

- the island editor (Save, Settings, Quit, its Layout as Window);
- Assets (Save change set);
- the UI editor (Save into the project; the Flash editor's Save edit file);
- the viewer (game only);
- the object builder, the sound editor, the text editor, the map editor and the particle editor (game and Save).

The Mods app, Mod diff, the Saves app and the Converter have no document of their own, so their File has the central
entries only. How it is built: `docs/tool-suite.md` section 33.

Note: the single menu bar was requested on 2026-11-19.

## One game for every app

The apps of the window work on **one game at a time**. Picking a game in one app switches every other app to it. The
ways to pick are File > Open game in the Viewer, the game box of the Mods app, and a link that takes an app to
another game. You do not have to repeat the choice app by app.

**What happens on a switch**

| The app | does this |
|---|---|
| holds unsaved work | asks **Save / Discard / Cancel**, with the app's name in the title of the question |
| holds an open file of the old game | closes it: the Maps app its map, the Objects app its object, the Particles app its effect, the Viewer its model with the Parts, Properties, Sequences, Checks and Inspect panels |
| cannot show the game (a game it does not support, or one whose folder is not set) | stays as it is and says so in its status line |
| was not opened yet | starts on the selected game when you first open it; if it cannot show that game, on the game it had last |

**Cancel cancels the whole switch.** Every app with unsaved work is asked first, the app you picked the game in
before the others, and nothing is closed until all of them agreed. A Cancel in any of them leaves every app on the
game and the file it had, the app you picked in included. Only what you saved with Save on the way stays saved.
The other choice was rejected: that the cancelling app stays behind and the rest switches. It leaves the window on two
games without showing which app is on which, and it closes files in apps you are not looking at.

**Apps that hold more than one game**

- **Islands** lists every game at once. Its selected game is the game tab in front in the Islands panel. On a
  switch the panel turns to that game's tab; the open island stays open, whatever its game, and nothing is asked.
  Turning a game's tab by hand picks that game for the other apps. If one of them cancels, they stay where they
  were and the panel keeps the tab you turned to, because it is only a list.
- **UI** has two pages. The Anno 2205 page holds that one game and keeps its work whatever is selected; selecting
  2205 brings it to the front, and selecting another game brings the other page to the front on that game. A click
  on one of the two page buttons picks that page's game.
- **Mod diff** offers the games of the question chosen. A game the question does not offer is not shown, and the
  status line says so.

**For developers**

- The selection is `Settings.game` in the settings file. An app's own `apps[<app>].game` stays underneath as the
  game that app was last on: it is what the app starts on alone (its own command), and in the window when it
  cannot show the selected game. An app started alone does not change the selection.
- The window announces a change with the signal `CSuiteWindow.game_changed(game)`; `CSuiteWindow.select_game(game)`
  switches from code. `suite/gamesync.py` holds the rules above.
- An app reports a pick with one call at the top of its `open_game`: `gamesync.may_switch(self, key, <its leave
  question>)`. Alone, that only asks the app's own question. As a page of the window it also asks the others and
  sends them to the game; False means cancelled, and the app opens nothing.
- What the window needs to know of an app is its `SAppGame` (`SAppSpec.game`, the table `APP_GAMES` in
  `suite/apps.py`): the games it shows, the game it is on, its leave question and how it opens a game. An app
  without one, such as an app an extension adds, is left alone.

Not handled: a game that fails to open in the app you picked it in (a broken install) leaves that app on the old
game while the others have switched. Pick again once the folder is right.

Note: one game for every app is the decision of 2026-11-17.

## A project

A **project** is one folder for one mod. Every app keeps its work in it, so one build writes one mod with all of it:

| What | Where, inside the project folder |
|---|---|
| The mod's identity and its GUID range | `project.json` |
| Island edits (Islands) | `island-edits/` |
| The asset change set (Assets) | `asset-edits/<game>.json` |
| The Flash interface edits (UI, the Anno 2205 page) | `ui-edits/anno2205.json` |
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

**Importing a whole mod** (Anno 117 and Anno 1800): there are three ways.

- **Import a mod as a project...** in the Project window (a mod folder or a `.zip`).
- **Mods > Import the selected mod as a project...** in the Mods app.
- `anno-toolbox mod import <mod> <new folder>` on the command line.

The mod is copied into a new project folder and never changed. Files are hard-linked where the disk allows (one
volume) and copied otherwise. The Toolbox always writes an edit as a new file, so the mod stays as it was. The mod's
`modinfo` is kept as its author wrote it (a `.jsonc` too). The build changes only the fields you edit in the Project
window.

Files no app edits are carried unchanged. The report lists them by extension, with what each app is to take later. It
also lists:

- the nested mods (built back at their own paths);
- the dependencies (kept, never copied in);
- the GUID range found from the assets the mod adds.

**Stop** ends an import and removes the half-made project. With nothing edited, **Build mod** gives the same paths and
the same bytes as the mod (`--verify` on the command line checks it). Only the game can tell that it loads the same.
Details and what is still planned: `docs/mod-import.md`.

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

## The shader progress bar

While the game look's shaders are prepared (once per game build, at the start and in the background after it), a
small progress bar sits beside the game link in the status bar. It shows "Shaders: n of m" for the stage's jobs. The
bar only moves for the compiles of an island opened meanwhile. Its tooltip names the compile running now, and it is
gone when nothing compiles.

The *Pause* button beside it holds back the jobs that have not started (the running one ends). *Resume* goes on where
it stopped. A view that needs a shader while the preparation is paused still compiles that one itself, so nothing
waits for the button. The compiles run on worker threads and report through a signal, never on the window's thread
(`infra/shadertrack.py`, `suite/shaderbar.py`; tool-suite.md "The start-up stage").

## Updates

The installed program looks for a newer release once a day at the start and tells you in the status bar. Help >
*Check for updates...* asks at once. Nothing is downloaded until you press *Download and install*. Only the changed
parts are fetched, and the program restarts into the new version. How it works and how to switch it off:
[`installer.md`](installer.md), "Updates".

## Apps of your own

Under the app switcher, **Manage apps** lists the apps other people added: Python apps (an installed package's entry
point, or a folder in the apps folder) and standalone programs (a manifest with a command line). The Toolbox lists them
beside the built-in apps, isolates each so a broken one only shows its error, and refuses one written for another API
version with the reason. Added apps run code with your rights: add only what you trust. How to write one:
[`app-api.md`](app-api.md).

## What has been checked

The apps, the project and the packing are tested on synthetic data, and the windows were looked at offscreen on the
author's installs. **Nothing the suite writes has been loaded by a game yet.** Only the game can confirm that an
edit is drawn or that a mod loads. The notes of each app list what was looked at and what is still open.

## Language

Every app's windows, menus, tool tips, dialogs and messages are in English or German. English is the default. The
language is a setting of the suite (Islands, File > Settings, General page, "Language") and is shared by all apps. The
setting lists every file found in the suite's `infra/lang/` folder. It takes effect the next time an app is started, so
a window is never half in one language. Setting the environment variable `ANNO_TOOLBOX_LANG` to `en` or `de` overrides
it for one run. A text that the chosen language's file lacks is shown in English. To add another language, copy
`en.json` to `xx.json` in that folder and translate the values (the ids on the left stay as they are).

The German texts follow the games' own German interface where it has the word (Insel, Mod, Baumenü, Gebäude), and the
modding community's words otherwise. Some things stay as they are: the games' own names (assets, buildings, goods,
properties), file names and paths, the mod loader's log, and the command line's output.
`tool-suite.md` says how strings are marked and how
the language files are kept.
