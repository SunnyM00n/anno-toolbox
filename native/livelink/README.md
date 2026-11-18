# The link helper

The link helper is a small `version.dll` for **Anno 117: Pax Romana**. It lets the Anno Toolbox reload a mod's
files in the running game without a restart: assets and texts, ambient settings, models, model configs, effect
files (shaders and materials), UI scenes and sound banks. It also sends the object you pick in the game back to the
Toolbox, and tells the UI editor what an open scene's widgets show (their texts, pictures, visibility and values). The Toolbox works without it. Without the helper you restart the game, or use the game's own reload keys
for assets and texts.

This is an **unofficial** modding tool. It is not made, endorsed or supported by Ubisoft or the game's developers.
It runs under the normal game launcher and changes nothing about how the game starts or is licensed. Use it in
single player and at your own risk. Keep a copy of your saves.

## Which game build

The helper supports **one game build** of Anno 117: Pax Romana, update 3.0 (`Anno117.exe` of 392218104 bytes). Every
number it needs for that build is in `build117.h`. The target ids that name those numbers are in `targets.h`.

There is a build of the helper for each of three games (`game.h` picks the game, `game117.h`, `game1800.h` and
`game2205.h` name what is fixed to it: its folder under Documents, its pipe and its id). Anno 117 has everything
described here. For **Anno 1800** (`Anno1800.exe` of 430828056 bytes) and **Anno 2205** (`Anno2205.exe` of 172685744
bytes) the helper has only the build check and the link's handshake: it names the game to the Toolbox, answers
`ping`, and answers every other request with `unavailable`. It reads nothing from the game's code, and an
executable of another size, the subscription builds included, makes it inert.

**The build check.** Windows loads the helper with the game. The helper first forwards the 17 functions of the
system's own `version.dll`, so the game's version calls behave as before. Then it compares the running executable
with `build117.h`: its file size, its link time stamp and its image size. If any of the three differ, the helper does
nothing more. Its log says `library inert` and the game runs as if the helper were not there. On the right build, it
also checks the first bytes at every place it uses before it uses that place. A place whose bytes differ stays
unused, and so does every feature that needs it. After a game update the helper stays inert until a new release
supports that build.

## Building it

You need Windows and Microsoft's C compiler: Visual Studio 2022 or later, or its Build Tools, with the "Desktop
development with C++" workload (MSVC for x64, including `ml64`).

```
build.cmd              the helper: out\version.dll and its map, beside this file
build.cmd <folder>     the same into <folder>
build.cmd 1800         the helper for Anno 1800 (out\anno1800); build.cmd 2205 likewise
build.cmd test         the host tests (no game needed; Python 3.12 or later for the pipe test)
```

`build.cmd` works from any command prompt: when `cl` is not on the path, it finds the compiler through Visual
Studio's `vswhere`. The build is reproducible (`/Brepro`, no time stamps). The same sources and the same compiler give
the same bytes. Each release builds the helper on GitHub's Windows runner and attaches it to the release page with
its `SHA256SUMS` and a build attestation. You can check a downloaded copy with
`gh attestation verify version.dll --repo <owner>/<repository>`.

## Installing it

1. Take `version.dll` from the release page, or build it yourself.
2. Copy it into the game's executable folder, the one that holds `Anno117.exe` (`<install folder>\Bin\Win64`).
3. Start the game as usual, then start the Toolbox. Within a few seconds, the Toolbox's status bar says
   `linked to Anno 117 (build <build>)`.

The helper keeps its log in `<Documents>\Anno 117 - Pax Romana\log\helper-library.log`. Its settings are in
`helper-library.ini` in the same folder. The file is optional, one `key=value` per line:

| Key | Default | What |
|---|---|---|
| `live_link` | `1` | the link to the Toolbox; `0` switches it off |
| `live_link_models` | `1` | reload models, model configs and effect files for the link |
| `live_link_values` | `1` | the live widget values for the UI editor (`ui.values`: what an open scene's widgets show); `0` switches them off |
| `live_link_follow` | `1` | tells the UI editor's game mirror which scenes open and close (`ui.scene`); `0` switches it off |
| `live_link_scene_ms` | `250` | how often the open scenes are looked at while the mirror follows (at least 100) |
| `live_link_commands` | `0` | `1` enables the Toolbox's debug palette and its module list (single player only) |
| `live_link_edit_key` | `69` | with Ctrl+Shift, the key that sends the selected object to the Toolbox (69 = E; 0 none) |

## Uninstalling it

Delete `version.dll` from the game's executable folder. That is all: the helper changes no game file. You can also
delete `helper-library.ini` and `helper-library.log` from the game's `log` folder in your Documents.

## How it keeps the game safe

- It talks to the Toolbox through a named pipe that only the current Windows user can open. Both sides prove that
  they know a token written to the user's own folder before any request is answered.
- The set of messages is closed. Every reload runs the game's own reload code on the game's own frame, one request
  at a time. A request that arrives while a session loads or a save is written waits or is refused as busy.
- The debug commands are a fixed table of the game's own script calls, run inside a guard. They are off unless
  `live_link_commands=1`, and always refused in a game with more than one player.
- An access fault inside the helper switches the feature that caused it off and leaves the game running.

## The sources

| Folder | What |
|---|---|
| `framework/` | the core: the start, the build check, the targets, the guarded calls, logging, the settings, the script binding, the shared engine reads |
| `link/` | the live link (the pipe, the handshake, the messages, the reloads) and the live reload of models and effect files |
| `targets.h`, `build117.h`, `build1800.h`, `build2205.h` | the target ids and the numbers of each supported build |
| `game.h`, `game117.h`, `game1800.h`, `game2205.h` | what is fixed to a game, and which one this build is for |
| `version.def`, `framework/version_stubs.asm` | the 17 forwarded functions of the system's `version.dll` |

The `*_logic.c` files hold everything that can run without the game. The `*_test.c` files test them on the host
(`build.cmd test`). `link/pipe_run.py` runs the link with the engine replaced by stand-ins, against the Toolbox's
own client over a real pipe.

The sources are the core of a larger helper library and share its names: the prefix `hlib_` and the settings file
`helper-library.ini`. Some declarations in `hlib.h` also serve modules that are not part of this edition. The link
helper never starts those modules.
