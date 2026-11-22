# The app API: add your own app to the Toolbox

The Toolbox's apps (Islands, Assets, Viewer, ...) are built in. Anyone can add an app of their own. It is either a
Python app that runs inside the Toolbox's window, or a program that already exists (written in any language) that the
Toolbox starts.

This page is the contract. The code is in these modules:

- `suite/appapi.py`: what an app declares and receives.
- `suite/appplugins.py`: finding and isolating apps.
- `suite/appprograms.py`: programs.
- `suite/appmanage.py`: the Manage apps window.

Two small examples are in [`examples/apps/`](examples/apps/hello-app/app.py).

## What an app can plug into

| Plug | Python app | Program |
|---|---|---|
| A place in the app switcher (name, icon, summary) | yes | yes (a page with a Start button) |
| The page itself, a `QWidget` in the Toolbox's window | yes | no (it is its own window) |
| A window of its own that the switcher entry opens, beside the Toolbox's window (`window=True`; `make` returns it) | yes | it is its own window |
| The suite's one game selection (`docs/suite.md`, "One game for every app") | yes (`games`, `game_key()`, `open_game(key)` on the page) | its `{game}` and `{game_dir}` follow it |
| A share of the project's mod (the Project window's build) | yes (`part`) | no |
| Links to and from the other apps (`suite/links.py`: show a file in the Viewer, edit an object, ...) | yes (`links`) | no |
| "Open with" for the file types it opens | by its page | yes (`file_types`) |
| The settings, the open project, the game trees, the runner, the status bar | yes (`CAppContext`) | the values in its command line and environment |

## The two kinds of external app

### 1. Python apps

An app is an `SExternalApp` (`suite.appapi`):

```python
from annotoolbox.suite.appapi import API_VERSION, SExternalApp

def make(context):              # called when the app is first shown
    ...                         # returns a QWidget

def register():
    return SExternalApp(key="my-app", title="My app", make=make, api_version=API_VERSION,
                       summary="What it does", icon="app_external", games=("anno117",))
```

`key` is a lowercase letter, then letters, digits, `-` or `_` (2 to 32 characters). It may not be one of the built-in
apps' keys. `icon` is a key of the suite's icons or the absolute path of a picture. `title` and `summary` are plain
texts (or ids of the Toolbox's own language files).

With `window=True` the switcher entry opens the window `make` returns instead of switching to a page. It opens the
window once, then brings it to the front. Use it for a tool that stays open beside the apps.

There are two ways to hand the Toolbox an app:

- **An installed package** (`pip install`) declares an entry point of the group `anno_toolbox.apps` whose value is the
  `SExternalApp`, a function returning one, or a dict of its fields:

  ```toml
  [project.entry-points."anno_toolbox.apps"]
  my-app = "my_package.toolbox:register"
  ```

  This works in a checkout or in any Python that has the package.
  The installed program (the frozen Toolbox) cannot import pip packages, so it uses the next way.
- **An app folder** in the apps folder. The apps folder is `apps` in the Toolbox's config folder.
  Manage apps > Open the apps folder opens it, and `ANNO_TOOLBOX_APPS_DIR` names another one.
  A subfolder holds an `app.json` and the code:

  ```json
  { "api_version": 1, "name": "My app", "entry": "app.py:register" }
  ```

  `entry` is a Python file inside the folder and the function that returns the `SExternalApp` (`register` when
  only the file is named).
  The file runs with the Toolbox's own interpreter, and its folder is on `sys.path`, so it can import its neighbours.
  It can import the Toolbox's `annotoolbox` modules and, where the Toolbox has them, PySide6.
  Libraries beyond that are the app's to bring along in its folder.

**Security.** A Python app runs code with your rights, in the Toolbox's process. The Toolbox cannot check it.
Put only what you wrote or trust into the apps folder or the Python you run.
Switch an app off in Manage apps when you doubt it: a disabled app's code is never run.

### 2. Programs (any language)

A manifest, `<name>.json` in the apps folder or `app.json` in a subfolder (without `entry`):

```json
{
  "api_version": 1,
  "name": "Notepad",
  "summary": "Windows' Notepad, started from the Toolbox.",
  "icon": "icon.png",
  "command": ["notepad.exe", "{file}"],
  "file_types": [".txt", ".xml"],
  "games": ["anno117", "anno2070"],
  "requires": [],
  "cwd": ""
}
```

The Toolbox lists the program in the switcher. Its page shows the command line it would run now and a Start button
(and a file picker when the command has `{file}`).

The Toolbox starts the program as a process of its own and embeds nothing.
A program whose `file_types` match a file is offered in **Open a file with a program** (Manage apps).

`command` is a list (a string is split like a shell would). It is run without a shell, so each value reaches the
program as one argument, never parsed again.
A relative program path is looked for next to the manifest first, then on the `PATH`.

| Placeholder | Value |
|---|---|
| `{game}` | the game key (`anno117`, `anno2070`, `anno1800`, ...): the selected game when the program handles it, else the first of its `games` that has a folder |
| `{game_dir}` | that game's install folder |
| `{project}` | the open project's folder |
| `{mods_dir}` | Anno 117's mods folder |
| `{workspace}` | the workspace folder |
| `{language}` | the interface language (`en`, `de`) |
| `{file}` | the file to open (empty when started without one) |

Each value is also in the program's environment as `ANNO_TOOLBOX_GAME_DIR`, `ANNO_TOOLBOX_PROJECT`, and so on.
An argument holding a placeholder with no value is left out.
A name in `requires` makes the start refuse, saying which value is missing.
Other keys in the manifest are kept in `Program.extra` for the program's own use.
A program runs as you, so the same security note applies.

## What the Toolbox gives a Python app

`make(context)` and a part's `render(project, context, log)` get an `CAppContext`:

| Member | What |
|---|---|
| `context.settings`, `save_settings()` | the settings record (game folders, language, theme) and writing it |
| `context.state` | a dict of the app's own, kept in the settings file under its key |
| `context.language`, `context.tr(id)` | the interface language; the Toolbox's own texts (an app brings its own) |
| `context.project_folder`, `context.project()` | the open project's folder; its identity, game and GUID range |
| `context.mods_dir`, `context.game_dir(game)`, `context.selected_game` | the folders and the suite's selected game |
| `context.open_game(game)` | a game's files, read only, through the same overlay of archives every app uses |
| `context.background.run(work, callback)` | the shared runner: `work` off the window's thread, `callback(result)` back on it (the exception when `work` raised) |
| `context.say(text)` | a line in the status bar |
| `context.show_app(key)` | bring another app's page to the front (the switcher's keys); False when there is none |
| `context.shell` | the window, for what the list above does not cover; anything not named here may change |

Opening a game indexes its archives, so do it through `background`.
The suite's `links` are named in `suite/links.py`.
An app that answers one lists it under `links`, and its handler gets the page first.

A **project part** (`SExternalApp.part`, an `SAppPart`) is the app's share of the mod the Project window builds.
`has_work(project)` says whether it holds anything.
`render(project, context, log)` returns game path -> bytes.

A path that leaves the mod (`..`, a drive) stops the build with the app's name.
A part that raises (in `has_work` or `render`) stops the build with the app's name too.
The build does not skip the part, since a silently incomplete mod is worse than a visible failure.

## Closing

The Toolbox closes in two steps, so that no page lets go of its work before every page agreed.

1. The Toolbox asks each page: `page.can_close()` returns True or False.
   The page asks its person about unsaved work or a running job there, and releases nothing.
   A False keeps the whole Toolbox open and brings that page to the front.
2. Only when every page said yes does the Toolbox close the pages one after another (`page.close()`).
   The page's `closeEvent` then lets go of its resources and does not ask the same questions again.

`can_close` is optional.
A page without it counts as agreeing, so apps written before it keep working.
A page that has it and also asks in its own `closeEvent` should ask only when it was not asked in step 1.
`annotoolbox.suite.widgets.may_close(page)` does that: it is True when the Toolbox already got the page's yes,
and calls `page.can_close()` otherwise (when the page closes on its own, or inside the Toolbox's windows of its own).

The Toolbox's own windows of its own (Project, the link palette) take part in the same way.
The Project window asks when an import or a build is running.
An import that is stopped by closing keeps its work, and the next start offers to resume it.

## Failures stay with the app

Every app is loaded and made alone.
An app is shown in the switcher (and in Manage apps) as failed, with its error, in these cases:

- its entry point does not import,
- its function raises,
- its declaration is wrong,
- its key is taken, or
- its page maker raises.

The Toolbox and every other app stay usable.

For a manifest in the apps folder, nothing an app declares is read before its `api_version` is checked.
A refused app's code therefore does not run.
An entry point's package is imported before it can be asked.

## Versions and the stability promise

`API_VERSION` (now 1) is the version of this contract. An app declares the one it was written for.

The Toolbox refuses an app in three cases, each with the reason in Manage apps:

- it declares a newer version than the Toolbox has ("written for a newer Toolbox: update the Toolbox"),
- it declares one older than `OLDEST_API` ("ask the author for an update"), or
- it declares none.

What is stable inside a version: the names and meanings in this page, `SExternalApp`, `SAppPart`, `CAppContext`'s members
named above, the manifest keys, the placeholders and the entry point group.

A change that stays compatible keeps the version.
Examples are a new optional field, a new `CAppContext` member and a new placeholder.

A change that an existing app could notice raises `API_VERSION`.
Examples are a removed or renamed member and a different meaning.
The Toolbox then keeps accepting the previous version for at least one minor release of the Toolbox (`OLDEST_API`),
noted in the release notes.

What is not stable: anything of the Toolbox's modules this page does not name, and the look of its pages.

## Manage apps

The **Manage apps** entry under the app switcher (and in the Settings menu) lists every added app.
It shows each app's kind, status (Loaded, Refused, Failed, Disabled), where it came from and its error.

It can also do these things:

- switch an app on or off (at the next start of the Toolbox),
- open the apps folder, and
- open a file with a program that handles its kind.

Disabled ids are kept in `disabled.json` in the apps folder.

## Not yet

- Programs are started, not embedded; a Python app's page is the only embedding.
- No app store, no signing of apps, no sandbox: an app is as trusted as the person who installed it.
- Apps are loaded at the start; a new one needs a restart (Manage apps says so).
