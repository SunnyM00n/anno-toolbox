# Anno Toolbox

**Unofficial.** A fan project, not made, endorsed or supported by Ubisoft (see [`LICENSE`](LICENSE)).

One program for modding every 3D-era Anno game: 117, 1800, 2205, 2070, 1404 and 1701. All its apps are built on one
shared core: the games' file formats, the installs and the mod loader's rules.

- **An island editor**: open any game's islands in 3D, drawn with the game's own shaders; move objects, sculpt and
  paint the ground, shape rivers and lakes, plant forests, place models - and build it all as a mod
- **An asset editor**: buildings, goods, needs, quests... as forms built from the game's own schema, written as ModOps
- **A viewer**: models with their parts and animations, textures, effects and sounds
- **UI editors**: 117 and 1800 interface files with live previews, and 2205's Flash UI
- **A map editor**: place islands, slots and start positions from above, and grow a map on any side
- **A text editor**: two languages side by side, with checks and CSV / XLIFF / PO import and export
- **A sound editor**: the Wwise banks of 117, 1800 and 2205
- **A particle editor**: effects as forms and curves, played live
- **An object builder**: a model config of any game - parts, materials, effects, lights, sequences - with the 3D view
  following along
- **Island ports**: move islands between regions, and between Anno 117 and 1800
- **Saves, mods and mod diffs**: read savegames, manage mod profiles and load order, and see what a mod really changes
- **Problems before the start**: the Mods app shows what the game's mod loader would silently drop, and the last
  start's log
- **Quality settings**: Low to Ultra for the 117 and 1800 views, so you don't need a high-end GPU
- **Unused files**: the asset editor shows which files nothing references
- **English and German** interface

It also includes a command-line interface to convert rdm files (models to glTF and back) and to export any game file.

**A ToolOne link recreation**: the release includes an optional link helper that hooks into the game engine (currently Anno
117 only) to recreate the studio's ToolOne link to the game - reloading assets, UI elements and more on the fly - with
guards to make sure it doesn't destabilize the engine. Note: the link helper is specific to one game version, and a build
check makes sure it only runs on the engine build it was made for. Its source is in `native/livelink`,
and the guide is [`docs/live-link.md`](docs/live-link.md).

The Toolbox needs **your own installs** of the games to work. It carries no game files on its own, and nothing it
writes contains any. A **project** is one folder per mod. Every app keeps its work there, and one build writes one mod
with all of it. Guides: [the suite](docs/suite.md), [the asset editor](docs/asset-editor.md) and
[the island editor](docs/island-editor.md). Some guides name design notes (such as `tool-suite.md`) that belong to
the larger project the Toolbox comes from. They are not part of this repository.

## Games

| Game | What the editor does with its islands |
|---|---|
| Anno 117: Pax Romana | opens the islands, edits objects, heights, ground, rivers, lakes and forest; builds a mod that replaces the island or adds a new one |
| Anno 1800 | the same, built as a mod (it is assumed that the mod loader replaces an island by its path) |
| Anno 2070 | opens the `.isd` islands, edits them, builds a `patch9.rda` |
| Anno 2205 | opens the sessions, moves and deletes objects and props, builds an archive and the game's file index |
| Anno 1404, Anno 1701 | opens the islands with their Granny models, edits objects (1701: also heights and ground), builds an archive |

[`docs/island-editor.md`](docs/island-editor.md) says what was checked in the game and what was not. Only the game
can confirm that an edit is drawn.

## Install

Download the Windows installer, `AnnoToolbox-win-Setup.exe`, from the
[releases page](https://github.com/SunnyM00n/anno-toolbox/releases/latest). It needs no Python, and later versions arrive as
updates. The installer includes the Direct3D 12 module that draws the island view; a source checkout builds it itself
(`python scripts/build_native.py`, [`native/d3d12/README.md`](native/d3d12/README.md)). There is no pip install, and
the package is not on PyPI.

**Linux support is experimental.** Since the release after 0.2.0, the releases page also has an AppImage and a tarball
(`AnnoToolbox-linux-x86_64.AppImage`). They were only run with software rendering in WSL, never on a real desktop with
a GPU or with the games under Proton. The Direct3D 12 module and the game's own shaders are Windows only, so on Linux
the Toolbox draws its own look through OpenGL (or Vulkan). Please report what does not work on the
[issue tracker](https://github.com/SunnyM00n/anno-toolbox/issues).

The command line has two commands. Use `AnnoToolbox.com` (beside `AnnoToolbox.exe`), or `AnnoToolbox rdm ...` in a terminal:

- `rdm`: the models of the games as glTF 2.0 and back, inspect, diff, validate and relayout
  ([`docs/rdm-gltf.md`](docs/rdm-gltf.md))
- `export`: any game file as stored and in a converted form, with an index ([`docs/exporting.md`](docs/exporting.md))

```bash
anno-toolbox rdm export model.rdm -o out/ --lods     # .rdm to glTF 2.0 (.glb)
anno-toolbox rdm import out/model.glb -o back/        # glTF to .rdm, the report says what was dropped or refused
anno-toolbox rdm inspect model.rdm                    # every element of a file
anno-toolbox rdm diff a.rdm b.rdm --values            # compare two files element by element
anno-toolbox export textures/ -o out/ --format png,exr    # a folder's textures as PNG and EXR, plus the files as stored
```

## Start the apps

```bash
python -m annotoolbox           # the island editor; File > Games and folders the first time
python -m annotoolbox path/in/the/game/island.a7m --mod path/to/a/mod/folder   # open one island at once
python -m annotoolbox --games anno1800                                        # the editor for 1800 only
python -m annotoolbox --app viewer      # one window with every app, opened on the viewer (Project under the switcher)
python -m annotoolbox --app islands --smoke   # every app opened once and closed: a check that they start
```

Running from source needs Python 3.12 or newer and an OpenGL 3.3 driver. `texconv` converts the textures
(see [`tools/README.md`](tools/README.md)). Settings and the editor's workspace are stored under
`%LOCALAPPDATA%/anno-toolbox`.

In a checkout (`pip install -e ".[dev,gui]"`), the island view draws with the game's own shaders once the
Direct3D 12 module is built: `pip install nanobind==2.9.2` and `python scripts/build_native.py` (Visual Studio's C++
build tools; [`native/d3d12/README.md`](native/d3d12/README.md)). Without the module, the view draws the Toolbox's own look.

`python scripts/build_app.py` packages the editor with PyInstaller as a folder with an .exe (it builds the module
first), for a computer without Python. With `--pack` it builds the installer.

## Tests

```bash
pip install -e ".[dev]" && python -m pytest -q          # light install: the tests that need no window
pip install -e ".[dev,gui]" && python -m pytest -q      # with the window tests
```

The tests build small synthetic islands, so they need no installed game.

## Licence

The code is under the MIT License, see [`LICENSE`](LICENSE). Ubisoft and its affiliates may also use the code without
any attribution or other condition ([`LICENSE-UBISOFT.md`](LICENSE-UBISOFT.md)). Contributions are accepted under
both. By sending one you agree that it is licensed under the MIT License with that additional permission, and you
confirm that you may do so. Sign your commits off with `git commit -s` (the Developer Certificate of Origin).
