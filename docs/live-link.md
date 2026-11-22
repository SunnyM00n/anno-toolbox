# The live link: the tool suite and the running game

The tool suite (`tool-suite.md`; the asset editor first, then the UI editor, the viewer and the
island editor) has a live link to the running game.
The link is **a modding feature for every mod developer**, not for this project's mods alone.
A modder edits an asset, a text, a model, a UI scene or an island in the suite.
The change shows in the running Anno 117 (and, where it can be done, Anno 1800) without restarting the game.
It also works the other way: pick an object in the game and open it in the editor.
This document describes what the link does, its protocol, its safety rules, what a modder needs and how it degrades.

Built:

- the reload of models, model configs and effect files by a key or a file to save (section 1.3), and
- the link itself (section 9).
  It consists of the suite's client, the game side's pipe, the handshake, and the requests for assets, texts, ambient
  files, models, effects, select, jump and read.
  The player's selection and "edit this object" come back through it.

The scene reload, live widget values, banks, the session reload and the companion mod's key binding followed (sections
10 and 11).
It is tested against a stand-in for the game and, for the pipe, against the game side's own code outside the game.
Nothing was tried in a game.
Not built: the companion mod's on-screen label (section 9.4).
The steps and estimates are in `roadmap.md` "The live link".

Note: designed on 2026-11-07.
The transport, the reloads of assets, texts, scenes, models and effects, select, jump, read, the panel and the Anno
1800 hand-over were built on 2026-11-12.

## 1. What the game can reload while it runs

This section describes the game's code on update 3.0.
"Follows" means the game re-reads it by itself once asked.
"Restart" means nothing in the game re-reads it.

| Kind of file | Live? | How the game is asked | What to expect |
|---|---|---|---|
| Asset values, templates, properties (`assets.xml` ModOps) | **yes** | the mod loader's hot reload (`Mods:LoadMods()`; Ubisoft's `hot-reload-ubi` mod binds it to Shift+F3) | every enabled mod is patched again, then the asset database is rebuilt; the game freezes for the patching (see the note below the table) |
| Assets, rebuild only | yes | Lua `CAssetDataManager:ExecuteFilter()` | the asset database rebuilt from what the last hot reload patched, without patching again; useful only once something else changed the patched data |
| Texts (`texts_<language>.xml` ModOps) | **yes** (seen 2026-11-07) | the same hot reload | a changed text of a small probe mod showed after Shift+F3 in a session, both ways; Shift+F3 does nothing on the title screen |
| UI scenes (Phoenix files) | **with the helper library** | the game has a scene reload of its own that a retail build never triggers; the helper library triggers it | the named screens are closed and opened again with their scene read anew; an open panel loses its scroll and typed text (a brand-new scene file needs a hot reload first, see the note below the table) |
| Ambient files (light, sky, fog, weather) | expected | Lua `CEnvironmentManager:ReloadAmbientSettings()` | the ambient settings read again; not tried |
| Models (`.rdm`) and model configs (`.cfg`) | **with the helper library** (built, not tried) | the game keeps its own reload of a changed model and of a changed model config, which a retail build never triggers; the helper library triggers it (sections 1.1, 1.3) | restart without the library. Anno 1800 has a developer reload of every model behind an engine switch |
| Textures (`.dds`) | **yes, indirectly** (seen 2026-11-07) | Shift+F3, then anything that requests the texture again (placing a building that uses it) | every object with that texture switched to the new file at once |
| Shaders and material definitions (`.fx`) | **with the helper library** (built, not tried) | the game keeps its own reload of a changed effect file, its material definitions and its shader variants; the helper library triggers it (sections 1.1, 1.3) | restart without the library (a mod's shader is compiled the first time it is used) |
| Sound banks (`.bnk`, `.wem`) | partly | the game reloads its localized banks (voices) when the audio language changes; one bank on request later with the helper library | restart for other banks today |
| Islands (`.a7m`, `.a7t`, the island's pages) | save and load | save, then load the save (later one request of the link) | the island files are read again at the load; the objects (slots among them) come from the save (`island-updates.md`) |

The suite always says which of these a change needs, so a modder is never left wondering whether a reload took
effect.

Notes on the table:

- A hot reload freezes the game for the patching.
  The freeze takes seconds for a small mod and half a minute for a very large one.
- Objects already placed keep what they took over when they were built.
  A new building shows in the build menu of a new game only (`engine-notes.md`).
- A brand-new scene file needs a hot reload first, because the loader must see the new path.

Note: read on 2026-11-07.
The text and texture reloads were seen in the game on the same day.

### 1.1 Reloading what needs a restart today

This section describes the game's code on update 3.0.
Nothing of it was tried in a game.

- **The engine keeps file-change handlers** for models, model configs, effect files with their material definitions,
  particles and post effects.
  They were written for the studio's tools: they re-read one changed file and update what is already on screen.
  A retail build has nothing that tells them a file changed.
  The helper library can do that, one file at a time, at a point in the frame where the renderer is not drawing.
  This is the game's own reload, not a reload of ours.
- **Models**: a changed `.rdm` is read again with all its levels of detail, and the objects already placed show the
  new mesh.
- **Model configs**: a changed `.cfg` (materials, texture paths, switches) is parsed again and applied to the objects
  that use it, with their animation kept.
  When both changed, run the model first, then its config.
- **Effect files**: a changed `.fx` is read again, its material definitions are re-applied to every material on
  screen, and its shader variants are compiled again (a hitch of up to seconds, as on first use).
  A broken effect file keeps the old one.
  Includes (`.fxh`) are not followed: the suite asks for every effect file that includes the changed one.
- **Textures**: the game's own texture reload is cut from the retail build.
  A changed file at a path the game already loaded stays as it was until a restart.
  A new path (a renamed texture in a changed config) loads.
- **Sound banks**: the game unloads and loads its localized banks when the audio language changes.
  It switches the banks of a session when the session changes.
  The helper library can do the same for one bank of the modder's own mod.
  What plays from that bank stops, and a looping sound comes back only when the game starts it again.
- **Islands**: there is no handler.
  Save and load instead: the island's files are read again at the load.
  A new island path needs a hot reload first so the loader sees it.
  What a reload carries (`island-updates.md`):
  - The build area, heights and pages, water and river grids, coast and river lines, decoration and props, herds and
    the ambient come from the files.
  - The slots, sound spots, markers, fertilities and deposits stay as the save has them (a new-game change).

  The update that enlarged Cinis's build area (1.6.1) reached old saves this way.

Note: read on 2026-11-07 (second pass).
What an island reload carries was added on 2026-11-14.

### 1.3 Reloading a model, a model config or an effect file (helper library, built, not tried)

The helper library triggers the game's own reload of these files, so a modder testing a mesh or a shader does not
restart the game.
It works with any mod enabled in the active profile, not only this project's.

1. **Switch it on**: `live_reload=1` in the helper library's settings
   (`<Documents>/Anno 117 - Pax Romana/log/helper-library.ini`; for this project's played mod in
   `data/helper-settings.ini`, then `install`).
   It is off by default.
2. **Start the game** with the mod enabled.
   At start the library notes the write time of every `.rdm`, `.cfg`, `.fx` and `.fxh` of the enabled mods (one log
   line says how many).
3. **Change files** in the mod's folder (rebuild the mod, or copy a file over), then either
   - press **Ctrl+Shift+R** in the game, or
   - save the file **`<Documents>/Anno 117 - Pax Romana/mods/live-reload.txt`** (any content, or none).

   Every file whose write time changed since the last reload is reloaded: models first, then model configs, then effect
   files, one a frame.
   A changed include (`.fxh`) reloads every effect file of the same mod whose `#include` line names it.
4. **To reload exactly some files**, write their paths into `live-reload.txt`, one a line.
   A path is relative to the mod's folder (`data/graphics/.../house_lod0.rdm`), written as `<mod id>:<path>`, or an
   absolute path inside an enabled mod's folder.
   `#` starts a comment.
   An include named there is refused.
   Name the effect files that include it instead (the suite finds them the same way, by the `#include` lines of the
   mod's `.fx` files).
5. **Read the result** in `<Documents>/Anno 117 - Pax Romana/log/helper-library.log`, a line per file.
   An example is `live reload: model data/graphics/buildings/ark/ark_lod0.rdm of anno2070 (request 2): reloaded in
   14.2 ms by the engine's model handler, its objects rebound`.

What to expect:

- Objects already placed show the changed model.
  A changed config (materials, texture paths, switches) applies to the objects that use it.
  A model the game has not loaded yet is skipped ("not loaded in this game"), and it is read fresh when first used.
  A model whose levels of detail are still loading waits a few frames.
- **A replaced vanilla model**: about half of the vanilla model configs are drawn from a baked copy under `shared/`,
  with its own models (`engine-modloader.md` "Vanilla models and model configs: the `shared/` copies").
  A mod's file at the `data/` path of such a model or config is never read.
  So the reload finds it "not loaded in this game", and nothing changes, before or after a restart.
  Change the `shared/` files instead (their `_lod0` to `_lod4` models, or the `shared/` config), or give the asset a
  new config path.
  The mods' own models at new paths (all of this project's) reload as described.
- A changed effect file is read again and its shader variants compile again, so expect a hitch of up to seconds.
  A broken effect file keeps the old one.
- A changed texture (`.dds`) at a path the game already loaded does not reload (a new path does).
- Nothing is reloaded in a multiplayer game.
  A reload asked during a loading screen waits until it ends.
- Settings:
  - `live_reload_key` is the key with Ctrl+Shift, a Windows virtual-key code (82 = R; 0 none).
  - `live_reload_models` and `live_reload_effects` are each 1 by default.
  - `live_reload_poll_ms` is how often the file is looked at (500).
  - `live_reload_wait_frames` is how long a request may wait (600 frames).
- The live link's `reload.models` and `reload.effects` messages use the same reload (section 9).

Note: built on 2026-11-07.

### 1.2 What saves a restart without the helper library

| Change | Try | Expected |
|---|---|---|
| Asset values, texts | Shift+F3 (`hot-reload-ubi`, or this project's `anno2070-dev`) | as above |
| Ambient files, the asset database | a key binding of the same kind (`ShortcutConfig`) for `CEnvironmentManager:ReloadAmbientSettings()` or `CAssetDataManager:ExecuteFilter()` | as above; not tried |
| Voice lines (localized banks) | the audio language to another and back in the options (or `CLanguageManager:SetAudioLanguage` from a key binding) | the localized banks loaded again; not tried |
| Textures | texture quality one step down and up in the options | every texture queued again; whether a changed file shows is not known |
| Islands, perhaps models and textures | save, then load the save (or quit to the title and load) | the island files read again; models and textures only if the game dropped them with the session (not known) |

## 2. How it works

```
  suite (asset editor, UI editor, viewer, islands)          Anno 117
  ------------------------------------------------          --------------------------------------------
  edit -> build the mod's files into the mod folder  --->   (the files on disk, as a normal mod build)
  message "reload assets"       ---- local pipe ---->       helper library: checks, queues, runs on the
                                                            next frame what a key binding would run
  status "reloaded in 2.1 s" <-----------------------       the game's own reload, confirmed
  "open in editor" (selection) <---------------------       the player picks an object, presses the key
```

- **The suite writes the mod, the game reads it.** Every change goes through the suite's normal build.
  The files are the same files, at the same paths, that "build mod" writes into the mod's folder.
  The link carries **no file content** and no change that exists only in memory.
  When the game crashes, or the link drops, the mod on disk is exactly what the editor shows.
  A later start of the game loads the same thing.
  Undo in the editor is a new build and a new reload.
- **The game side runs the game's own reloads.** The helper library adds no reload of its own.
  It asks the game the way a key binding or the developer tools would (section 1), at the point in the frame where the
  game itself would.
  It then reports what happened: the mod loader's log lines, the time, and whether the asset rebuild ran (the game can
  skip it without a word, for instance while a session loads).
- **Both directions.** Editor to game: reload, select an object, move the camera to it, read live values.
  Game to editor: what the player selected, and "edit this object" from a key in the game.

## 3. The transport

- **A named pipe of the current Windows user**, opened by the helper library when the link is switched on:
  `\\.\pipe\anno117-live-link-<process id>`.
  A pipe needs no network port, so there is no firewall question and no port clash, and no web page can reach it.
  (The studio's 2010 tool opened a socket and Windows asked.)
  The pipe accepts local clients of the same user only, one client at a time.
- **A session file** tells the suite where the pipe is: `<Documents>/Anno 117 - Pax Romana/live-link/session.json`.
  It holds the game build, helper library version, pipe name, protocol version and a fresh random token.
  It is written at the start and deleted at the end of the game, and it lives in the user's own folder.
- **A handshake with the token**: the game sends a random challenge, and the suite answers with a keyed hash of it and
  the token.
  A client that cannot read the session file cannot connect.
  The token changes with every start.
- **Frames**: a 4-byte length, then one JSON object (UTF-8), at most 1 MB a frame. Every request has an `id`, and
  every answer carries it back. Events carry none.

## 4. The messages

Protocol version 1. Requests from the suite:

| Request | Arguments | Answer |
|---|---|---|
| `hello` | `protocol`, `client` (name, version), `proof` (the handshake) | `welcome`: game, build, helper library version, the enabled mods (ids, folders), what this build supports |
| `files.changed` | `mod` (a ModID of an enabled mod), `paths` (relative to the mod folder) | `ok` with what each path needs (`reload.assets`, `reload.scenes`, `restart`, ...): the suite asks once, the link decides by the rules of section 1 |
| `reload.assets` | `mode`: `full` (patch and rebuild, as Shift+F3) or `rebuild` | after the game's confirmation: `done` with the time, the loader's warnings (`No matching node`, `Can't find file to patch`, ...) or `skipped` with the reason |
| `reload.texts` | - | the same |
| `reload.scenes` | `states` (UI state names) or `files` (scene files of the mod) | `done` per screen, or `busy` (a loading screen, a transition) |
| `reload.ambient` | - | `done` |
| `reload.models` (later) | `mod`, `paths` (`.rdm`, then `.cfg`) | `done` per file, `unavailable` while a model still loads |
| `reload.effects` (later) | `mod`, `paths` (`.fx`) | `done` per file, or the effect file's error and "old effect kept" |
| `reload.banks` (later) | `mod`, `banks` (bank names of that mod) | `done` per bank |
| `reload.session` (later) | - | `done` after the save and the load, or `busy` |
| `select` | `object` (an object id) or `guid` (the first or next object of that asset in the current session) | `ok` with the object |
| `jump` | `object`, or `session` and `position` | `ok` |
| `read` | `guid` and `fields` (value paths such as `Building/BuildCost`), or `object` and fields from a fixed list | the values as the game holds them now |
| `subscribe` / `unsubscribe` | `topics`: `selection`, `reload`, `session`, `log`, `ui.scene` (section 18) | `ok` |
| `ping`, `bye` | - | `pong`; the link closes |

Events from the game:

- `selection`: the selected objects (id, GUID, name, session).
- `edit.request`: the player pressed "edit this object".
  The suite opens the object's asset, and the island editor opens its island.
- `reload`: a reload the player started by hand, so the suite knows the game is current.
- `session`: entered, left.
- `log`: new mod loader lines.
- `ui.scene`: a UI scene opened or closed, with its state's and its file's name (section 18).

Anything else is refused with `unknown`.
A message the game build cannot serve is answered `unavailable`.

## 5. Safety

1. **Off unless the modder switches it on**: in the full helper library, with `live_link=1` in its settings.
   The link helper (section 15) exists for the link and has it on unless `live_link=0`.
   The link cannot switch itself on, and a game started without it has no pipe.
2. **Local and for one user**: the current user's pipe only, remote clients refused, one client, the token handshake.
3. **No code execution**: a closed set of messages with typed arguments (numbers, names matching `[A-Za-z0-9_./-]`).
   The link never takes script text, a command line or a native address from the client.
   The Lua it runs is built from fixed templates.
4. **No file writes by the game side** but these, all under `<Documents>/Anno 117 - Pax Romana/`:
   - `live-link/session.json`, the session file with the pipe's name and token, deleted when the game ends (and
     when the link cannot come up);
   - `log/helper-library.log`, the helper library's log (past 8 MB it becomes `log/helper-library.old.log`, one older
     file kept);
   - the save `livelink.a8s` in the profile's save folder (`accounts/<account>/<profile>/`), written by the game's
     own save for `reload.session` and loaded again, overwritten by the next one.

   The paths a client names must lie inside the folder of a mod enabled in the active profile.
   Anything else is refused.
   The suite writes the files, inside the project's mod folder only.
5. **No game rules changed**: the link reloads data, selects, moves the camera and reads values.
   It does not place, delete or change objects, money or anything a savegame keeps.
   **One exception, behind a second opt-in** (`live_link_commands=1`, section 13): the debug commands of the suite's
   palette.
   They are a closed list of the game's own cheat calls, which do change the game's rules in a single-player game.
   Without the second switch the game answers `unavailable` and says nothing about them in its welcome.
6. **Never in multiplayer**: every request but `ping` is refused while a multiplayer game runs (a reload on one
   computer would split the game).
7. **Visible**: while a client is connected, the game window's title says so.
   With the companion mod (section 6) so does a small label in a corner of the screen.
   Each request is a line in the helper library's log.
8. **Limits**: a few requests a second, and at most one reload at a time.
   A reload asked while one runs is answered `busy`.
9. **The asset reload is the one that can hurt** (section 12): the game replaces its whole asset database, and objects
   already placed keep what they had.
   The link waits for the game's own end of that rebuild and keeps its own asset reads off meanwhile.
   It refuses while the game saves, loads, changes session or holds another scheduled data action.
   It tells the suite when the game stops drawing frames.

## 6. Using it with your own mod

- **What a modder needs**:
  - Anno 117 and the mod loader as usual.
  - Their mod in the mods folder and enabled in the active profile.
  - The suite with the project pointed at that mod folder.
  - The **link helper** installed next to the game (section 15).

  Nothing goes into their mod for the link.
  Optional: the small **companion mod** that the suite can install (its own ModID), which adds the on-screen label and
  a key for "edit this object" in the game.
- **What the public helper library edition must contain**:
  - its core (the check that refuses an unknown game build, the settings, the log, fault containment),
  - what lets it act at the moment and in the way the game's own key bindings do,
  - the script table for the companion mod, and
  - the live link module.

  It contains none of this project's gameplay modules.
  It runs in a game without the Anno 2070 mod.
- **Without the helper library** the suite still works, by hand.
  It builds the mod and says "press Shift+F3" (with `hot-reload-ubi` enabled) for assets and texts, or "restart" for
  the rest.
  The selection and the scene reload are not available.
  A game update that the helper library does not know yet leaves it inert, and the suite falls back to the same route
  on its own.
- **Anno 1800**: no helper library exists for 1800.
  The game keeps a developer command that starts an external asset tool from its `Tools/ToolOne/` folder with the
  selected object's GUID.
  The suite can put a start file there that hands the GUID to the running suite (one way, game to editor; how a player
  reaches the command is still open).
  1800's mods are reloaded when its in-game mod browser closes.
  What follows that reload was not read.

## 7. What only the game can confirm

- That a changed value shows in an open panel.
  Texts follow a hot reload (seen on 2026-11-07, see the note at the end of this section).
- That the scene reload reloads a changed scene of a mod and that the screen works afterwards.
- That `CAssetDataManager:ExecuteFilter()` and `CEnvironmentManager:ReloadAmbientSettings()` do what their names say.
- The tries of section 1.2: a changed island after a save and load; a changed model and texture after quitting to the
  title and loading; a changed texture after a texture quality switch; a changed voice line after an audio language
  switch; a changed text after a text language switch; the Lua names in a key binding.
- How long a hot reload takes with one small mod enabled, and that the game stays responsive after it.
- The link itself: connect, the label, a reload of each kind, select and jump, "edit this object", the refusals
  (another user, a wrong token, a path outside the mod, multiplayer). The list for the first try is in section 9.5.
- The reload of section 1.3: a changed model, model config and effect file shown without a restart, the hitch of an
  effect's compile, a broken effect file kept out, a changed include reaching its effects, nothing in multiplayer.

Note: checked on 2026-11-07 with a probe mod in its own profile, in a session.
The hot reload reads the *active* profile.
The game had reset `profile.txt` to `default`, so a reload with the tested mod off in that profile dropped the mod.

## 8. Toolbox side: the seam in the suite (the transport behind it is section 9)

The suite carries the client side of this document as an interface without a transport.
Every app therefore uses the link the same way, whatever transport is behind it.
It is tested on a double.
Nothing was tried in a game.

- **The interface** (`suite/livelink`): `CLiveLink` has these typed methods, one each:
  - `files_changed(paths, mod)`,
  - `reload(kind, paths, mod)` (kinds `assets`, `texts`, `scenes`, `ambient`, `models`, `effects`, `banks`,
    `session`),
  - `select`, `jump`, `read`, `subscribe` and `unsubscribe`.

  `on(event, handler)` follows the events (`SELECTION`, `EDIT_REQUEST`, `RELOAD`, `SESSION_EVENT`, `LOG`, `STALLED`).
  Every answer is a `Reply` (`ok`, `done`, `skipped`, `busy`, `unavailable`, `refused`, `not connected`).
  `capabilities()` / `supports(kind)` say which reloads the connected game takes (none when not connected).
  A request for a kind the game cannot serve is answered `unavailable` without being sent.
  A path that leaves the mod's folder is `refused` before anything is sent (section 5).
- **Not connected** is the link a suite starts with (`CNotConnected`).
  Nothing leaves, and each request is kept in `sent` and logged.
  The answer's message is the manual route of section 6:
  - "press Shift+F3 in the game" for assets and texts,
  - "restart the game" for scenes, ambient files and banks,
  - "restart, or switch on `live_reload` and press Ctrl+Shift+R" for models and effects, and
  - "save and load" for a session.

  A changed texture gets the note of section 1 (Shift+F3, then use it again; a loaded path needs a restart).
- **The transport** is one extension point: `livelink.register_connector(fn)`.
  `fn()` reads the session file (section 3), does the handshake and returns a connected `CLiveLink` subclass that sets
  `_transmit` and calls `set_status` and `emit`.
  `livelink.connect()` tries the connectors.
  Calls are safe from any thread.
  Events arrive on the transport's thread, and the window marshals them.
- **One registry**: `livelink.current()` is the one link every app takes, `livelink.use(link)` replaces it, and
  `livelink.on(event, handler)` follows whichever link is current.
  The suite window shows the status in its status bar ("game link: not connected", or the game and build) and keeps it
  up to date.
- **What an app calls** (`suite/linkannounce`):
  - `announce(kinds, mod_id, paths)` sends `files.changed` and the reloads and returns a `Notice`.
    The `text` of the notice goes on the app's status line (the manual route when not connected).
  - `announce_install(mod, folder, before)` does the same for a mod folder that was just written.
    It finds the changed files by content (`snapshot` before, then `changed_since`) and sorts them into reload kinds
    (`classify`).
  - An app whose change is only in the project (the files reach the game with the build and install) says so with
    `is_deployed=False` and sends nothing.
  - A file whose name the link refuses (a space, a non-ASCII letter) is left out on its own.
    The rest is sent and the text names it, and it never claims a reload the game was not asked for.
  - With nothing before (a first install), the files are listed without being read.
  - A handler of an event that fails is logged, and the other handlers still run.
- **Game to editor** (`suite/linkroute`): the window routes `edit.request` (an asset `guid`, a `file`, an `island`) to
  the app that provides `show_asset`, `view_file` or `open_island` in `suite/links`.
  It routes `selection` to an app that provides `game_selection`, so no app is imported.
  The asset editor opens a game that is not open yet in the background (the status line says so, and the window stays
  usable).
  It serves the one asset asked for once the game is open (a later request replaces a pending one) and answers the
  link "pending" meanwhile.
  A game whose folder is not set is reported on the status line, never in a dialog over the user's work.

| App | Where it calls the seam | State |
|---|---|---|
| Project (all apps' output) | `project.install`: after the copy, `announce_install` for the files that differ from the installed copy | built |
| Asset editor | `CAssetWindow.build_mod`: `announce((assets, texts), mod)` | built |
| UI editor | `CUiWindow.save`: `announce((scenes,), ..., is_deployed=False)`; the scene reaches the game through the project install | built |
| Island editor | `App._copy_mod` (its own install): `announce_install`, so an island file gives `reload.session` | built |
| Game to editor | the window: `show_asset` (asset editor), `view_file` (viewer), `open_island` (island editor: `App.find_island` by path or file name), `game_selection` (island editor, quiet: selects the open island's objects of the selected assets, the nearest to the game's position, and flies there; `editor/gameselection`) | built, tested on doubles; the selection payload's shape and coordinates are assumed |
| Viewer | writes nothing; the asset editor's "View" is the other direction | nothing to call |
| Object builder (models, configs, effects) | after it writes a `.rdm`, `.cfg` or `.fx`: `announce_files(mod, paths)` (or its project install through `project.install`) | not built yet |
| Sound editor | after a bank is written: `announce((banks,), mod, paths)` | not built yet |
| Mod manager | after a profile switch: `announce((assets, texts), mod)`, after an install `announce_install` (`modmgr.manager`); the hot reload reads the active profile (section 7) | built |

Without a game the suite says what to do instead. With one it says "game reloaded: assets, texts". Both are one
sentence from `announce`, so no app words the fallback itself.

Note: the seam was built on 2026-11-09.

## 9. The transport, built (nothing tried in a game)

### 9.1 What was built

- **The suite's client** (`suite/linkprotocol`, `suite/linkclient`) has these parts:
  - the wire protocol as one module that both ends are written against,
  - the pipe client over the standard library (no extra package),
  - the handshake,
  - requests with their timeouts,
  - the game's events on the transport's thread, and
  - a retry every three seconds, so a game started later is found and a lost link is found again (`ANNO_LIVE_LINK=0`
    switches the search off).

  It is the connector behind the seam of section 8.
  Apps need no change: the status bar and every app's status line start saying "game reloaded: ..." the moment a game
  answers.
  A long list of files (a first install names thousands) goes in parts of 256.
  A changed include (`.fxh`) is replaced by the effect files of the mod that include it before it is sent.
- **The game side** (the helper library's `live_link` module, with a host-tested protocol part) has these parts:
  - the pipe thread,
  - the session file,
  - the handshake,
  - the closed message set,
  - a few requests a second, one request at a time, and
  - the answers.

  The reloads run on the game's own frame, as a key binding would (section 1).
  It also brings the model and effect reload of section 1.3 up for the link (`live_link_models=1`, default), so
  `reload.models` and `reload.effects` need no second switch.
  It runs in a game without the Anno 2070 mod, so any mod can be edited live.
- **Scenes, widget values, the panel, Anno 1800** (the second round; section 10): `reload.scenes`, the game link panel
  behind the status bar, reloads asked from a window that never block it, and the start file for Anno 1800's
  open-in-editor command.
- **A stand-in game for tests** (`tests/fakegame.py`): the same protocol in Python, over a socket pair or a real named
  pipe.
  The client's tests (`tests/suite/test_linkclient.py`) run against it.

### 9.2 Switching it on and using it

1. In the helper library's settings (`<Documents>/Anno 117 - Pax Romana/log/helper-library.ini`) set `live_link=1`.
   Optional settings:
   - `live_link_models=0` (no model and effect reload),
   - `live_link_edit_key` (a virtual-key code, default 69 = E; the key with Ctrl+Shift sends the selected object to
     the suite; 0 none), and
   - `live_link_selection_ms` and `live_link_log_ms` (how often the selection and the loader's log are looked at,
     500).
2. Start the game with your mod enabled, then start the suite.
   The status bar says "game link: anno117 <build>" within a few seconds.
   The game window's title ends in "[live link]" while the suite is connected.
   The game's log (`log/helper-library.log`) has a line for every request.
3. Save in the suite as before.
   The asset editor's build, the project's install and the island editor's copy tell the game what changed (section
   8), and the game reloads it.

### 9.3 The messages as built

Frames: a 4-byte little-endian length, then one JSON object.
The game sends `{"challenge", "protocol"}` first.
The suite answers `hello` with `proof` (HMAC-SHA256 keyed with the session file's token over `client:<challenge>`) and
a fresh `nonce`.
The game's `welcome` carries its own proof (`game:<nonce>`), so a program that only squats the pipe name is not trusted
either.
A request is `{"id", "type", "args"}`, an answer is `{"id", "status", "message", "data"}`, and an event is
`{"event", "data"}`.
A request over 96 KB, a frame over 1 MB, an unknown name or an argument outside its type is refused.

| Request | What the game does | Answer |
|---|---|---|
| `files.changed` | nothing: it names what each path needs | `ok`, `data.needs`: a reload kind, `textures` (asset reload, then use it again) or `restart` per path |
| `reload.assets` | the mod pass (as Shift+F3), then waits for the mod loader's "Patching completed" line | `done` with the seconds, the warnings and the first complaint; `skipped` when no such line came in 10 s; `busy` while a session loads. `mode: rebuild` asks for the rebuild alone and answers at once (the game gives no confirmation) |
| `reload.texts` | the same pass (texts follow it) | the same |
| `reload.ambient` | the ambient settings read again | `ok` at once (no confirmation) |
| `reload.models`, `reload.effects` | the model and effect reload of section 1.3 for each path, models first | `done` when any file went through (`data.files`: reloaded, handed, skipped as not loaded, refused, with its time and reason), `skipped` when none was loaded, `refused`, `busy` after 30 s of waiting |
| `reload.scenes` | the game's own scene reload for the UI states in `states` (the suite names them: a scene file is named after its state, a widget file is looked up in the scenes that place it); the screens are left and entered again, so an open panel loses its scroll and typed text | `done` when the scenes are loaded (the sentence names the states, and the names that are no state of the game), `skipped` when no state is named or the UI did not start it in 10 s, `busy` while a reload runs or a session loads |
| `reload.banks` | each named bank (`banks`: the bank's name, as its `.bnk` file is named) is unloaded and loaded again through the sound engine's own calls (a whole bank first, a prepared one when that is refused); the Init bank is never touched | `done` when any bank went through (`data.banks`: per bank its id, the sound engine's result and `reloaded`, `not unloaded`, `not loaded again`, `refused`, `no answer`), `refused`, `skipped` when no answer came in 10 s |
| `reload.session` | the game saves under the name `livelink`, the file is awaited, the game loads it again (its island files are read anew) | `done` once the load was asked for, `skipped` when no save file appeared in 30 s, `busy` while a session loads |
| `select` | selects the object `object`, or the first object of the asset `guid` in the current session | `ok`; `skipped` when the session has none |
| `jump` | the camera to `object`, to the session whose GUID is `session`, or to `position` (three numbers) inside that session | `ok`; a `position` without its `session` is `refused` |
| `read` | the values of `fields` of an asset (`guid`) as the game holds them now; of an object only from a fixed list (`GUID`, `ID`, `SessionGuid`, `Direction`, `Visible`, `IsBlueprint`, `Health/CurrentHitPoints`, `Position/x`, `Position/y`, `Position/z`) | `ok`, `data.values`; `unavailable` when the game hands back no text |
| `ui.values` | what the widgets of one open UI scene show now: of the UI states in `states` whose scene is open, the widgets of the widget file `file` (its GUID; none: the scene file's own widgets), each with only its text, image id, visibility and own property values (section 17) | `ok`, `data.scenes`; `skipped` when no named scene is open or the file is not in them; `unavailable` on a build whose widget classes the helper does not know |
| `debug.list` | which commands of the palette's catalogue the game binds right now and the cheats it registers itself; only with `live_link_commands=1` (section 13) | `ok`, `data.commands`: id to `available` and `state` (true, false or null), `data.cheats`: names |
| `debug.run` | one command of the catalogue with its typed `values`; the game maps the id to a fixed call and runs it | `done` (`data.state` where the game can read the toggle back), `refused`, `busy`, `unavailable` for a method the build does not bind |
| `debug.cheat` | one of the cheats the game itself registers, by `name`, which must be on the game's list at that moment | `done`, `refused`, `busy`, `unavailable` |
| `subscribe`, `unsubscribe`, `ping`, `bye` | as section 4 | `ok`, `ok` with `pong`, the link closes |

Every request but `ping`, `hello`, `subscribe`, `unsubscribe` and `files.changed` is refused in a multiplayer game.
One request runs at a time (`busy`), and more than about ten a second are `busy`.
The events:

- `selection`: the selected object, `{"objects": [{"id", "guid", "session"}]}`, or an empty list when nothing is
  selected.
  It is one object, and the game says which of several.
- `edit.request`: Ctrl+Shift+E in the game (`game`, `object`, `guid`, `session`).
- `reload`: a mod pass the player started by hand, noticed from the loader's log.
- `log`: new loader lines (`data.lines`, at most 20 per poll).
  When more came, the last entry reads "n lines skipped".
- `ui.scene`: a UI state with a scene opened or closed, its name and its scene file's name (section 18; topic
  `ui.scene`, sent only when subscribed).

`reload.banks` is answered `unavailable` by a game build whose sound calls the library does not know, and so is
`reload.scenes`.
The suite then shows the manual route of section 6.
A changed `.bnk` file of a mod becomes a `reload.banks` for the bank of that name.
A sound file alone (`.wem`) is refused, because it reloads with its bank.

### 9.4 What is not built

- **The companion mod's on-screen label**: the window title and the panel stand in (the key binding is built, section
  9.6).
  The label is not a file of the companion mod.
  A label is a UI panel, and the library fills panels only from the one binding manifest of the Anno 2070 mod (the
  first one found; a second is left alone; off in a game without that mod unless forced).
  So the label belongs to that mod's own UI, as a panel bound to `HelperLink:Connected()`.
  A standalone companion would need the library to merge manifests first.
  Neither is built or tried.
- **Anno 1800: how a player reaches the command** (research; the hand-over works whenever the game runs it).

### 9.5 The checks in the game (the first try of the link)

1. `live_link=1`, start the game with one small mod, start the suite.
   The status bar names the game, the window's title has "[live link]", and the log has "live link: ready on ..." and
   "hello (request 0): ok".
2. Change a value in an asset of the mod, save in the asset editor.
   Expect "game reloaded: assets" and the value changed in the game (a building placed afterwards).
   The log says how long the pass took.
   Then try a text.
3. `reload.assets` with `mode: rebuild` and `reload.ambient`: do the Lua names in the library's templates exist in the
   game?
   A wrong one is a Lua error in the game's log, not a crash.
4. Change a model and a model config of the mod, install.
   The placed object shows it, and a model not yet used is "skipped".
5. Select a building in the game.
   The suite's selection event arrives (the island editor selects its object when the open island holds it), and
   Ctrl+Shift+E opens its asset in the asset editor.
   If nothing arrives, the game hands back no text for the selection command, and `read` is then `unavailable` too.
6. `select` by object id and `jump` from a tool (the suite has no button yet: `livelink.current().select(object=...)`
   in a console), and `read` of a field such as `Building/BuildCost`.
7. The refusals: a second suite window (the pipe takes one client), the session file edited to a wrong token, a file
   outside the mod, a started multiplayer game.
8. A reload asked during a loading screen, and one while the mod pass of a large mod runs (the game freezes for it, and
   the suite waits).
9. The suite started first, the game second: found within a few seconds.
   The game closed: the status bar goes back to "not connected" and the session file is gone.
   After a crash the file stays, and the suite ignores it when no pipe answers.
10. The scene reload (section 10.1): save a scene file of the mod in the UI editor, build and install, with the screen
    it belongs to open.
    Expect "game reloaded: scenes", the screen working afterwards, an open panel starting again, and our own panels
    (the UI binding) showing their values again within a second.
    A widget file in a scene that is not open: the answer is `skipped`.
    A scene of a state that is not in the game: the sentence names it.
11. The panel: click the status bar's game link.
    Check the rows, the game's log lines while it is open, "Connect now" with the game started after the suite, and
    "Reload assets and texts".
12. Anno 1800: in the panel "Anno 1800: open in the editor ..." with the game's folder, then the game's own command
    with an object selected.
    The asset opens in the asset editor.
13. A reload asked while a long mod pass runs: the asking app's window stays usable and says "game link: reloading ..."
    and then the answer.
14. Banks (R3): a mod with its own sound bank; change the bank, install, then run
    `livelink.current().reload(livelink.BANKS, ["data/sound/<name>.bnk"], "<mod>")` in a console.
    Expect `reloaded`, and the new sound plays the next time it starts (a looping sound stays silent until it is
    started again).
    For a prepared bank, the answer's `prepared` is true.
    The log line of the library names each step's result.
15. The session reload (R4): `reload.session` in a session.
    A save called `livelink` appears in the profile's folder, the game loads it and a changed island file shows.
    Confirm these points:
    - the save command and the load command ran (`Game:Save`, `Game:Load`; a wrong name is a Lua error in the game's
      log and the answer is `skipped` after 30 s),
    - no popup holds the save, and
    - what the save's quota does to a second reload.
16. `select` with a `guid` (a building of the session): the first object is selected.
    If `skipped` although one stands there, the object group the game hands back is not indexed like a table (the log
    shows the Lua error).
17. `jump` with `session` and `position`: the camera moves (the game's own debug camera command).

### 9.6 The companion mod

`suite/linkcompanion` writes a small mod (the panel's "Companion mod ..." button, or
`write_companion(<mods folder>)`).
The mod is one key binding (default Ctrl+Alt+E, in sessions) in the game's key binding asset.
It calls the library's script table `HelperLink:EditSelected()`, which sends the selected object to the suite as the
library's own Ctrl+Shift+E does.
The table also gives `HelperLink:Connected()` for a label to bind to.
The binding is the same kind of ModOp as the quick-reload mod's (`mod/modxml.shortcut_binding`, whose Shift+F3 was seen
to work).
The on-screen label is not built.
What the game has to confirm (nothing of it was tried):

- that Ctrl+Alt+E is free;
- that a key binding command can call a table the library registers (otherwise a Lua error in the game's log, and
  the key does nothing);
- that the table is registered when the library runs without the 2070 mod;
- that the object selected arrives in the suite.

Note: the transport was built on 2026-11-12.
The reason for the missing label was worked out on 2026-11-13.

## 10. The second round (nothing tried in a game)

### 10.1 The scene reload

`reload.scenes`.
The game keeps a reload of UI scenes for development, driven by a list of UI state names in a file that a retail build
never watches.
The library starts the same reload by filling in that list itself, so the game does everything else.
It marks the states, leaves and enters them on its own thread, loads the scene files anew, and clears its busy flag
once the scenes are in.
Which files change which states is the suite's part (`suite/linkscenes`):

- a scene file is named after its state (`data/ui/studio/generated/maintitlescene` is `MainTitle`);
- a widget file is looked up in the scenes that place it (the UI editor knows them and registers a resolver);
- anything else names no state and is not sent.

A scene file new to the mod makes the suite run the mod pass first, because the loader has not mapped the file yet.
After the reload every widget is a new object.
The library's own UI binding forgets what it wrote and found, and writes its values again.

### 10.2 The panel

Clicking the status bar's game link opens `suite/linkpanel`.
It shows these things:

- the connection,
- the reloads the game takes,
- the mods the game has enabled,
- the session file,
- the game's latest log lines (the panel subscribes to them while it is open),
- "Connect now" and "Reload assets and texts", and
- the Anno 1800 set-up below.

The debug-command palette is described in `debug-palette.md`.
It sends the game's own cheat and debug commands as typed ids.
The requests it needs from this link are listed in its section 5.
The shell opens it from the status bar.

### 10.3 Asking without waiting

A reload answers after the game's confirmation, which can take as long as a mod pass.
`linkannounce.announce_in_background` runs the announcement on the suite's background thread when a game is connected.
It says "game link: reloading ..." at once, and the answer's sentence when it comes.
With no game it is instant and runs where it is.
The asset editor's build, the mod manager's profile switches and removals and the panel use it.
The UI editor's save and the install paths already run off the window's thread or send nothing.

### 10.4 Anno 1800

1800 keeps a developer command that starts `Tools/ToolOne/launch.bat -startGUID <guid>` for the selected object.
`suite/linkhandover` writes a `launch.bat` there that leaves the GUID in a request file under the suite's data folder.
It never replaces a `launch.bat` that is not its own.
A thread of the suite's window turns the request into the same `edit.request` that Anno 117's link sends.
This is one way, with no library and no pipe.
How a player reaches the game's command is not known yet.
The game starts the file by an unquoted command line, so it only starts from a game folder whose path has no space.
The suite refuses to write it elsewhere.
The file prints nothing and returns at once (the game never reads its output).
The suite never offers 1800's own data reload as a way to reload that game.
That reload swaps the asset database beside a running session with no check of the session, a pause or a save.

### 10.5 The library's public edition

The link module, its framing and checks and the command runner are one self-contained cut of the library.
The cut holds the core, the UI frame entry, the command call and the module, and no gameplay module.
It is published as the **link helper**, with the numbers of the one game build it supports.
The release attaches it built (section 15).

Note: the second round was built on 2026-11-12.
The link helper is published since 2026-11-15.

## 11. Where the third round stopped

Built and host-tested, nothing run in a game:

- banks,
- the session reload,
- select by GUID,
- a position inside a session,
- the companion mod's key binding and the library's `HelperLink` table,
- the UI editor's live label texts,
- the asset editor's background open for link requests, and
- the test hang guard (`tests/hangguard.py`).

The in-game checks are 9.5 items 15 to 18 and section 9.6.

**The test run's exit code 127.** A full run with the game folders set ended with exit code 127 under Git Bash although
every test had passed.
It was no test and no link code.
The Python process aborted during its own shutdown, after the last test and after pytest's last step.
The event log shows `0xC0000409` in the C runtime, and Git Bash prints 127 for it.
A full run opens about 1,500 top-level Qt windows and never closes them, and tearing those down inside the
interpreter's finalization aborts.
Only whole runs showed it (the Qt files alone, the other files alone and every sixth of the suite ended normally).
`tests/qtcleanup.py` deletes the open windows at the end of the session (`conftest.py`, `pytest_sessionfinish`;
`tests/test_qtcleanup.py`).
With it, a full run ends with pytest's own exit code.

The long runs showed other things that are not link code.
`test_radvideo` (`test_a_batch_does_not_end_while_an_output_still_grows`) and `test_modmgr_window`
(`test_the_projects_build_is_installed_off_the_window_thread`, stuck once inside a message box) each ended a long run on
the hang guard once while the machine was busy.
Their causes were not looked into.

Next: the companion mod's on-screen label (section 9.4: not a file of the companion mod), and the in-game checks above
and in section 12 (the asset reload's guards).

Note: the third round stopped on 2026-11-12. The exit code was traced on 2026-11-13, the day the guards of
section 12 were built.

## 12. Asset reloads: what the link guards (host-tested with the game stubbed, nothing run in a game)

The game's own asset reload (what Shift+F3 does, and what `reload.assets` and `reload.texts` end in) replaces the whole
asset database.
The old database is freed, an empty one stands in while the files load, and objects already placed are not pointed at
the new data.
A game without the link has the same risks.
The link adds the guards below.
Its answers keep to the closed message set, one request at a time, and the refusals in multiplayer and while a session
loads.

- **Confirmed by the game, not assumed.** The answer waits for the game's own end of the rebuild (the lines it writes
  to its log).
  It says `skipped` when the game never started the rebuild within 10 s (the game drops a reload without a line in
  several states).
  It says `error` when the rebuild started and did not end within 60 s (the game's file wait has no limit of its own).
  A rebuild asked for alone (`mode: rebuild`) is confirmed the same way.
  A reload the player started by hand is noticed from the same lines.
- **A pending state.** From the request to the game's end line, the library's own asset lookups answer "none".
  So every module that looks an asset up fresh each frame skips its work instead of reading a database in the middle
  of its swap.
  A table made from asset data is built again afterwards.
  When the game never began the rebuild, the state ends by itself after 90 s.
  Once the game's start line is there, only its end line ends it.
  While it holds, a player the library cannot read counts as a human, so the multiplayer refusal stays in force.
- **Busy, not run.** Every reload (assets, texts, scenes, models, effects, banks, ambient, session) and every debug
  change is answered `busy` in these cases:
  - while the game saves (a save file was written in the last two seconds, or is being written),
  - while a session transition runs,
  - while a session loads,
  - while another asset reload runs, and
  - for an asset reload, while the game holds a scheduled scenario action that a reload would overwrite.

  The test runs again right before the pass (after the wait for the pause) and before a scene reload is handed over.
  The model and effect reload's queue waits on the same test, whoever asked for it.
- **A watchdog for the unbounded wait.** When no UI frame comes for 30 s after an asset request, the library's pipe
  thread sends a `stalled` event (no topic needed).
  The suite's status line then says that the game seems to hang in the asset load.
  A file of the pass may be broken or missing, or a very large mod pass can take this long.
  The pipe thread calls nothing in the game.
  `live_link_stall_s` changes the 30.
- **What the modder is told.** A confirmed answer says that objects already placed keep the data from before the
  reload, and that save and load (`reload.session`) gives a clean state.
- **Paused for the reload.** In a running single-player session, the link pauses the game before the mod pass (or the
  rebuild alone) with the game's own pause call, `Pause:Paused()`.
  This is the full pause the game's menus use, and the pause key's `Pause:Toggle()` is the same manager.
  The link waits up to 3 s for the pause to land (it is a game command and takes effect on the next tick) and runs the
  pass.
  After the game's end line, it sets the speed it found again with the game's speed buttons (`Pause:Played()`,
  `Pause:FastForward()`, `Pause:TripleFastForward()`).
  It leaves the speed alone in these cases:
  - the game was paused already (by the player or a menu),
  - outside a session (the title screen),
  - the player changed the speed in between, and
  - after an `error` answer (a rebuild that did not end), where the game stays paused and the answer says so.

  A pause that does not land within the 3 s does not stop the reload.
  The answer's data carries `"pausedByLink": true` and `"resumeAsked"`.
  The game's asset rebuild runs while it is paused.
  Its 100 ms schedule is timed by the same wall clock as the pause's own ticks (read from the game, not tried).
  Outside a session, pause by hand as before (the panel's button says so).

Host test: the link's engine side with stand-ins for the game runs against the suite's client over the real pipe.
It covers these cases:

- confirmation,
- silent drop,
- busy,
- refusal,
- one request at a time,
- the stall warning,
- the pause and the speed set again,
- the speed left alone when paused already or outside a session,
- a pause that never lands, and
- busy over a scheduled scenario action.

The stability guards at the end of `tests/suite/test_linkclient.py` are the suite's half.

To check in a game:

- that the log lines come when the game rebuilds;
- that `busy` stops a reload during a save and a session switch;
- that no placed object crashes after a reload of a changed building (it keeps the old data);
- that the stall warning comes when a file of the pass is broken;
- the checks of the pause and the scenario guard in section 12.1.

Note: the guards were built on 2026-11-13 and the pause on 2026-11-14.
The rule that only the game's end line ends the pending state, and the queue's wait on the busy test, were added on
2026-11-15.

### 12.1 In-game checks of the pause and the scenario guard (not run)

1. A session at speed Fast, `reload.assets`.
   - The game pauses before the game freezes for the pass (does the pause banner show for this full pause?).
   - The answer comes after the rebuild, then the game runs at Fast again.
   - The library's log has "the game is paused (speed 5)" and "the pause for the request ends (speed 3 before, 5 now):
     set again".

   Repeat at Normal and Fastest.
2. The game paused by the player (the pause key) or in the pause menu, `reload.assets`.
   There is no pause call in the library's log, and the game stays as it was.
   Does the reload still finish while paused (the rebuild's end line comes)?
3. Unpause by hand while the pass runs: the answer comes, and the speed stays the one chosen.
4. On the title screen, `reload.assets`: no pause call (outside a session), and the reload runs as before.
5. A console `CAssetDataManager.TriggerScenarioTest = <guid>` (careful: a missing core asset closes the game) or
   `TriggerRevertScenarios()` and, before the game ran it, `reload.assets`.
   The answer is `busy` with the sentence about a scheduled scenario action.
   In practice the game runs the action within a frame, so this needs a quick hand.
   The library's log no longer says the manager is not located.

Note: the checks were listed on 2026-11-14.

## 13. The debug commands (host-tested with the game stubbed, nothing run in a game)

The suite's debug palette (docs/debug-palette.md) sends the game's own cheat calls through the link.
The message set stays closed.
`debug.run` carries a command **id** and `values`, a new argument kind (an object of names to numbers or bools, at
most 8 entries, no text).
The game maps the id to a fixed call of its own table, puts the typed value in as a number or `true`/`false`, and runs
it inside a guard.
So a method the build does not bind answers `unavailable` and never breaks the game.
The suite checks the id and the values against its catalogue before anything is sent (`debug_run` returns `refused`
for what does not fit).
The game checks them again against its own table.
The table holds the game's own commands: the cheats and the actions of the game's debug pages and its script console
(debug-palette.md section 1.4).
A module of the full helper library may add rows after them (the 2070 console, debug-palette.md section 7), but never
replace one.
The link helper holds the game's own rows only.

- **A second opt-in.** `live_link_commands=1` in the helper library's settings, besides `live_link=1`.
  A game with the link on and this off answers `unavailable` to every `debug.*` request and does not list `debug` in
  its welcome.
  The suite's `supports("debug")` is then false and the palette's buttons stay off.
- **Single player only, and only when the game is quiet.** Refused in a multiplayer game.
  `busy` while a session loads, a save was written a moment ago, a session transition runs or an asset reload is in
  flight (the same rules as section 12).
- **Client**: `debug_list()`, `debug_run(command, values)`, `debug_cheat(name)` on the link.
- **To check in a game**:
  - the list names the commands the build binds (the selection cheats are expected missing on a retail build),
  - a toggle's state comes back,
  - a command with an argument does what its name says,
  - `destroy_selected` is really destructive (the palette asks first),
  - the cheat list is what the game registers, and
  - whether a cheat changes what a save keeps (not known; use a throwaway save).

Note: built on 2026-11-13.
The table held the cheats only at that date, and the debug pages and script console actions were added on 2026-11-19.

## 14. The game side's modules (host-tested with the game stubbed, nothing run in a game)

The helper library's modules can be listed and, where a module allows it, switched while the game runs.
Two requests are available.
They are behind the same second opt-in as the debug commands (`live_link_commands=1`) and refused in a multiplayer
game:

- **`modules.list`** answers every module with these fields:
  - its `name`,
  - its `class` (`game`: on by default; `dev`: a developer's tool, off; `experimental`: built, not yet seen in a game,
    off until it is; `2070-mode.md` "Feature classes"),
  - whether it runs (`on`),
  - whether it may be switched now (`switchable`), and
  - when not, `why`.
- **`modules.set`** takes `module` (a name) and `on` (`true` or `false`, a new argument kind) and answers `done` or
  `refused` with the game's reason.
  Only a module that just listens to the game's events and can put back what it changed is switchable.
  One that changes the game at start, adds assets or changes the engine's own work is not.
  A module that changes the simulation is never switched in a game of several players.
  The answer is `busy` while the game is not quiet, as in section 13.
  Switchable modules:
  - the farm growth (switched off, every growing field is set grown on its next steps), and
  - the production module (switched off, its productivity corrections are taken off the farms on its next steps).
- **Client**: `modules_list()` and `modules_set(module, on)` on the link.
- **Panel**: the game link panel's "List modules" fills a small list, one tick per module.
  A module that cannot be switched is greyed, and its reason is the tooltip.
- **To check in a game**:
  - the list names every module with its class as the start log does,
  - switching a switchable module off and on again changes its behaviour and nothing else, and
  - a module that may not be switched says why.

Note: built on 2026-11-14; the list of switchable modules is of that date.

## 15. The link helper (built and host-tested, nothing run in a game)

The game side of the link is a download of its own: the **link helper**.
It is a `version.dll` for Anno 117 that holds the link, the reload of models, model configs and effect files, and
nothing else.
Its source is in the Toolbox's repository (`native/livelink/`, with its own README).
Each release builds it on GitHub's Windows runner and attaches `version.dll`, `live-link-SHA256SUMS` and a build
attestation to the release page.
The build is reproducible, so a copy built from the same tag with the same compiler has the same checksum.

- **One game build.** The helper supports Anno 117 update 3.0.1 (`Anno117.exe` of 388925432 bytes).
  At start it compares the running executable's file size, time stamp and image size with the build it was made for.
  On any other build it stays inert.
  It forwards the system's version calls, as Windows' own `version.dll` would, its log says `library inert`, and the
  game runs as without it.
  A game update therefore needs a new release of the helper.
- **Installing.** Copy `version.dll` from the release page into the game's executable folder, the one with
  `Anno117.exe` (`<install folder>\Bin\Win64`).
  The link is on by default in this build (`live_link=0` in `<Documents>/Anno 117 - Pax Romana/log/helper-library.ini`
  switches it off).
  Start the game, then the Toolbox.
  **Uninstalling**: delete that `version.dll`.
  The helper changes no game file.
- **The debug palette.** `live_link_commands=1` in the same file enables the debug commands (section 13) and the module
  list (section 14).
  Without it the game answers `unavailable` and the palette's buttons stay off.
- **What the Toolbox shows without it.** The status bar says `game link: not connected`.
  A save that would reload something in the game says instead what to do by hand: Shift+F3 with the hot reload mod for
  assets and texts, a restart for the rest (section 6).
  The game link panel's search answers "no game answered".
  Nothing else changes, because every editor works without the helper.
- **The Toolbox tells about a new helper.** The update check (`installer.md` "Updates") compares the release's
  `live-link-SHA256SUMS` with the SHA256 of the installed `Bin\Win64\version.dll` when it is ours.
  It is ours when the marker text of its log is in the file, or when it is the last release's helper.
  Otherwise the check compares with the hash the last release had.
  A difference shows a notice in the status bar with a link to the release page.
  The program never downloads or installs the helper, because it goes into the game's folder.
  The person fetches it, verifies it and copies it.
  This was not seen with a real release yet.
- **To check in a game**:
  - the helper loads beside a clean install and the start log ends with the link's lines,
  - the Toolbox connects, and
  - a second game build leaves it inert.

Note: built on 2026-11-15.

## 16. Anno 1800 and Anno 2205 (the helper's core and the Toolbox's client are built; nothing tried in a game)

The link helper was looked at for the two other games of the same engine family.
It was read from the games' files, and none of it was tried in a game.
16.1 says what is built since.
The tables below are the plan for the rest.

**Installing works the same way in both.** Both games load a `version.dll` from their executable folder at start.
1800 asks for it itself, and 2205 asks through two of its own libraries.
Each game gets a helper of its own, made for its one game build, with the same build check as 117's (an unknown build
leaves it inert).
2205 is no longer updated, so its helper would never need a new build.
1800's subscription build and 2205's are not supported (as with 117).

**What each game will offer** (the plan; "title screen" means no session is open):

| Request | Anno 117 (built) | Anno 1800 (planned) | Anno 2205 (planned, after more research) |
|---|---|---|---|
| assets and texts | in a session and at the title screen | **at the title screen only**: 1800's own data reload replaces the asset database while a session runs without the checks 117's has, so in a session the suite says "save, quit to the title, reload, load" (the game itself reloads mods only when its mod browser closes, at the title screen) | at the title screen only, from the game's archive index or loose files (2205 has no mod folder) |
| models and model configs | yes | yes: 1800 keeps the same reload handlers | configs yes; models no (2205 keeps no model reload) |
| effect files | yes | yes, includes followed by the game itself | not known yet |
| UI scenes | yes | not known: 1800 has a UI reload command whose effect is untested; until then `unavailable` | not known (2205's interface is Flash) |
| sound banks | yes | planned, the same sound engine | not known yet |
| the session (islands) | save and load | planned | yes: 2205 has a session reload of its own |
| select an object | yes | no: the game has no "select this object" call; `unavailable` | no |
| jump | yes | yes, to an object or a position | partly (around an object; by id not known) |
| read, the selection event, "edit this object" | yes | yes, through the game's script values | yes, through the game's script calls |

**2205's helper changes nothing in the game's code.**
It works only through the game's own script interpreter and its start script, so it needs one small start file
installed by the suite besides the helper.
That design is not read to the end yet.

**Without a helper**, today:

- 1800: the one-way "open in the editor" of section 10.4.
  For data, leave a session and reopen the mod browser, or restart.
- 2205: two of the game's own script calls can be bound to keys in its `key.config`.
  They are `session.reloadSession()` (the current session again) and `game.reloadData()` (the data; at the title
  screen only, for the same reason as 1800).
  Not tried.

**The Toolbox's side** (planned): the client finds the game by the session file in that game's own Documents folder and
takes the game from the welcome, so the asset editor opens the right game.
The panel and the manual routes say the game's own way.
A release attaches one helper per game, each as its own download, and the update check compares each with the helper
in that game's folder.

Order and size:

1. the shared core,
2. the Toolbox client,
3. 1800 (about 46 agent hours to a helper with every request but the UI reload, 49 to 52 with it), and
4. 2205 after its research (about 10 hours of research, then 25 to 35).

Probes that need no code (not run):

1. 1800: `TextSources.TextSourceRoots.UIBindings.ReloadUI()` from a key binding or a mod's script in a session: does
   the interface reload?
2. 1800: change a value of an enabled mod, open and close the mod browser at the title screen: does the value show?
3. 2205: the 117 link helper copied beside `Anno2205.exe`: does its log (`helper-library.log` under 117's Documents
   folder) get the line that it stays inert?
   That proves the helper is loaded.
4. 2205: `session.reloadSession()` and `game.reloadData()` on keys in `key.config`: what each reloads, and whether the
   game stays stable afterwards.

Note: researched on 2026-11-16.

### 16.1 What the core and the Toolbox brought (host-tested with the game stubbed)

**The helper's core is per game.** Everything the core had fixed to Anno 117 now sits in one small header per game
(`game117.h`, `game1800.h`, `game2205.h`, chosen by `game.h`).
Each header holds these things:

- the game's folder under Documents (where the log, the settings file and the session file live),
- the pipe's name,
- the id the welcome gives (`anno117`, `anno1800`, `anno2205`, the Toolbox's game keys),
- the game's own log and the mod loader's files, and
- whether an engine side exists.

Each game has its build header with the build check (`build117.h`, `build1800.h`, `build2205.h`): the executable's file
size, link time stamp and image size.
The build script has a target per game (`build.cmd`, `build.cmd 1800`, `build.cmd 2205`), and its tests run once per
game.

**For 1800 and 2205 the helper has no engine side yet**: its target table is empty.
It checks the build, and then it does what needs no game address.
It opens the pipe and the session file, names the game in the welcome (no reload kind, no mod), answers `ping`,
`subscribe` and `bye`, and answers every other request with `unavailable`.
The subscription builds of both games are another size and stay inert.
The 2205 build check reads the file's size and the header's plain values and touches nothing else of the executable.
2205's helper never patches its code.

**The Toolbox's client knows the game.** It looks for the session file of each game in that game's own Documents folder
(Anno 117, Anno 1800, Anno 2205; the first game that answers is linked).
It checks that the pipe, the session file and the welcome all name the same game.
The status bar and the debug palette say `linked to Anno 1800 (build ...)` and so on.
The game panel names that game's session file, and the asset editor takes the game from the link.
The manual route has each game's own words when no link is up or the game takes no reload:

- 117's Shift+F3 and restarts,
- 1800's mod list at its title screen (the one-way open in the editor of section 10.4 stays as it was),
- 2205's restart or a key for its data reload (not tried), and
- "save and load" for a session in each.

**What is open**: everything that needs an engine address (the plan's steps for 1800 and 2205 above), and the first
try in a game.
The first try covers the proxy route in 2205 (probe 3 above).
With the new 1800 and 2205 helpers it also covers the line in the helper's log that the link came up without an engine
side, and the Toolbox's "linked to ..." for it.

Note: built on 2026-11-17.

## 17. Live widget values: `ui.values` (host-tested with the game stubbed, nothing run in a game)

The UI editor's **Take the live values from the running game** ([`ui-editor.md`](ui-editor.md), "Live values") fills
the open scene's preview with what the game shows in it now.
The request asks for those values only.

**The request.** `ui.values` takes two arguments:

- `states` is required.
  It names the UI states whose scenes show the open file, and the Toolbox names them as for the scene reload (section
  10.1).
- `file` is the GUID of the widget file the editor has open.
  It is left out when the open file is a scene file, whose own widgets are asked.

The game looks at those states only, and only at a state whose scene is open now.

**The answer.** `ok` with

```json
{"file": "<guid or null>",
 "scenes": [{"state": "HUD", "cut": false,
             "widgets": [{"guid": "...", "crc": "1a2b3c4d", "name": "Amount", "visible": true,
                          "text": "1,234 t", "image": "data/ui/...", "values": {"7": 2.5}}]}],
 "cut": false}
```

- The widgets are those the open file places itself.
  In the scene, that is the first instance of the widget file (`file`), and in it every widget the file holds.
  The widgets of another widget file placed inside it are not included, because they belong to that file.
  A widget file placed twice is answered for its first instance.
- Per widget:
  - `guid` (the instance's GUID in the file), `crc` (the CRC of its name, as the file stores it) and `name`: the
    editor matches its elements by these.
  - `visible`: the widget's own Visible as the game holds it.
  - `text`: a label's text when the game has filled it.
  - `image`: the picture an image widget draws by id (its `DynamicImageId`: a file path, an icon's or a portrait's id).
  - `values`: the widget's own property values by UniqueId, numbers, switches and texts only.
    An asset a binding assigned to a widget comes as such a number (its GUID).
    So does a portrait's GUID where the file keeps one in its own properties.
- **Never answered**:
  - a widget's class and its other built-in properties,
  - its stored fields, flags, matrices and rectangles,
  - the hashes of fields and the scene's load state,
  - a widget of another file or of another scene, and
  - a value of another kind (a vector, a colour, a game object).

  A property that holds a game object is left out, and the game object behind it is not read.
  No picture is sent, only the reference the widget draws.
- Other answers:
  - `skipped` when no named state is a UI state of the game, when none of them has its scene open, or when the file is
    not placed in the open scenes.
  - `refused` for a `file` that is no GUID.
  - `unavailable` when the helper does not know the widget classes of this game build (the welcome then does not list
    `values`), and with `live_link_values=0` in the helper library's settings.

**The guards.** They are the same as for the other requests: refused in a multiplayer game, `busy` while a session
loads, a save is written, the session changes or an asset reload runs, and one request at a time.
The values are read on the game's UI frame, under the scene's own lock, and nothing is written.
Every read is guarded, so a widget that cannot be read is left out with what it holds.
The answer is at most 100 KB and 2,000 widgets.
Past either limit it ends with the widget that no longer fits and says `"cut": true`.
Measured against the game's own 136 scenes (their widget trees as laid out in the game):

- the largest file any of them places has 146 widgets and comes to about 36 KB in this form,
- a widget holds at most 42 property values, and
- a scene holds at most 18,974 widgets (the walk looks at up to 40,000).

**How the editor uses it.** Each widget's text replaces the label's sample text.
`visible` and `image` replace the element's Visible and dynamic image, and `values` replace the instance's property
values.
All of them are matched by the instance GUID, else by the name CRC.
A widget that matches no element is left out.

**What the Toolbox stores.** Every answer taken is recorded on the modder's own computer, one file per game and UI
file.
The files are in `ui-values/<game>/` below the Toolbox's workspace (or its data folder in `%LOCALAPPDATA%` without
one).
A recording holds the answer's widgets with the keys above and nothing else, the game build the link reported, the
time and the states asked.
Taking the values again replaces it.
It is never written into a project or a mod.
No image is stored: icons and portraits are read from the modder's install when the preview draws them.
The cases this leaves open are listed below.

**To check in a game** (none of it was run):

1. Open a scene in the UI editor that the game shows (the HUD in a session), take the live values.
   The preview shows the game's texts, and the helper's log has a line
   `values of <state>: n widget(s) looked at, m answered`.
2. A widget file placed in that scene: only its widgets change.
   A file in no open scene: `skipped`.
3. A label the game hides, and an image with an icon or a portrait: the preview hides it and draws that picture.
4. The pictures the game makes at run time.
   The game's own scenes name most pictures by their file (`data\ui\fhd\...`, `data\ui\2kimages\...`).
   Some are named by an id of a texture it made itself (`rtt_<n>`, `tex_<n>`: a render target, a composed portrait),
   and no file of the install holds those.
   The editor's "List what the game assigned" marks them, and the preview draws its stand-in for them.
   A small local copy of such a picture would need its pixels from the running game, which the link does not read, so
   none is kept.
5. The guards: busy while loading a session, refused in a game of several players.

Note: built on 2026-11-17.
The measured numbers of the guards are of the same date.

## 18. Following the game's scenes: the `ui.scene` event (host-tested with the game stubbed, nothing run in a game)

The UI editor's **game mirror** ([`ui-editor.md`](ui-editor.md), "The game mirror") shows the scenes the game has open
now, stacked as the game shows them, each filled with its live values (section 17).
To know which scenes those are, it follows one event, `ui.scene`.
The game says when a UI state with a scene opens or closes, and nothing else.

**Switching it on.** The suite subscribes to the topic `ui.scene` (`subscribe` with `topics: ["ui.scene"]`) while the
mirror is open and not paused, and unsubscribes when it pauses or closes.
A game that sends the event lists `follow` in its welcome.
With `live_link_follow=0` in the helper library's settings it does not, and a subscription to the topic is accepted but
never answered with an event.

**The event.** One per change, in the order the game made them:

```json
{"event": "ui.scene",
 "data": {"change": "opened", "state": "ConstructionMenu", "scene": "constructionmenuscene",
          "parent": "HUD", "depth": 5, "order": 12, "window": [2560, 1440]}}
```

- `change`: `opened` or `closed`.
- `state`: the UI state's own name (as in `ui-states.txt`).
- `scene`: the name of its scene file, as the game names it.
  It is the state's name in lower case with `scene` after it, as the game's scene files are named.
- `parent` and `depth`: the state's parent state and how deep it sits in the state tree (an `opened` event only).
- `order`: a number that grows with every opening the game side saw, so that a later opening has a higher one.
  A `closed` event repeats the number of its opening.
  The scenes are stacked by it: a scene opened later is drawn over one opened earlier.
  The game's draw order of scenes is taken to be the order of state activation (*inferred*, not measured).
  Scenes the game already had open when the subscription began come first, all at once, sorted by `depth`, then by the
  state's id.
- `window`: the size of the game window's drawing area in pixels, when the game side can read it (the mirror lays the
  scenes out at that size).

**Never carried**: a widget, a value, a text, an object, a GUID of the scene's content, an address, and the state's
handlers or data.
The values of a scene come only through `ui.values` (section 17), with its own guards.

**Rates and guards.** The game side looks at its UI states on its UI frame, at most every 250 ms (`live_link_scene_ms`,
at least 100), and only while a client is connected and subscribed.
It reads only whether each state is open and has a scene, its name and its parent's, and it changes nothing.
A new subscription (or a new client) starts from nothing, so its first events name every scene already open.
A client that may have missed events subscribes again to get the whole set, because the game drops events when the
client does not read.
At most 64 events are sent per look, and the rest follow on the next look.

**The Toolbox's polling.** While the mirror follows, it asks `ui.values` for the shown scenes, one request at a time.
It sends at most four a second in all, and slower while the values do not change (down to one a second per scene).
It never asks while a request still waits.
So the link's own limit (about ten requests a second) is never reached by the mirror.

**The edit loop** (the mirror's "Push edits to the game"): an edit of a scene or widget file the game has open is
kept in the project.
It is written into the installed copy of the project's mod (its own folder in the mods folder, only when that folder
holds the project's mod), and sent as `reload.scenes` (section 10.1).
After `done` the mirror takes the values of that scene again.
A reload resets what the player had open in that scene (its scroll, a typed text).
Nothing else changes in the game, because the push is the scene reload the link already has.

**To check in a game** (none of it was run):

1. With the mirror open in a session: the HUD's scenes appear, each laid out at the game window's size.
2. Open a building's menu, the construction menu, the diplomacy screen.
   Each appears over the others as it opens and goes when it closes, and the helper's log has no `could not be read`
   lines.
3. A tooltip or a popup: whether it opens a state of its own (and so appears) or lives inside another scene.
4. That the stacking by `order` is the game's: a popup opened after a menu is drawn over it in the game too.
5. Pause the mirror: the helper's log shows the unsubscribe and no more looks.
   Resume: every open scene again.
6. Push an edit of the construction menu's scene: the game reloads it, and the mirror shows the edit with live values.

Note: designed and built on 2026-11-17.
