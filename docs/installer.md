# The Windows installer

The Anno Toolbox is provided as a Windows program, a folder with `AnnoToolbox.exe` and `AnnoToolbox.com`. It is also
provided as an installer, `Setup.exe`, that sets the program up for one user and keeps it up to date from the project's
releases. The installer is **Velopack** (MIT licence; [docs.velopack.io](https://docs.velopack.io)).

This page says what goes in and what never does. It describes how the program installs, starts, updates and
uninstalls, how a download is verified, how people on the older installer move over, and how to build it.
[`suite.md`](suite.md) says how the Toolbox is used.

## What goes in

| In the program folder | What |
|---|---|
| `AnnoToolbox.exe` | The window: every app of the suite, with no console. A shortcut starts it with no arguments. The installed program then opens the suite's window on the island editor (`--app islands`). See the notes below the table for the logs |
| `AnnoToolbox.com` | The same program built as a console program. See the notes below the table |
| `_internal/` | Python, Qt (PySide6, with its shader baker `qsb.exe`, which the 3D views need), moderngl, numpy, scipy, Pillow, the `velopack` module and the package with its data files (the interface languages, the descriptions, the tables) |
| `_internal/<package>/render/_nativegpu.pyd` | The game look's Direct3D 12 module. See the notes below the table |
| `docs/formats/` | The three files the program reads next to its exes: the Phoenix schemas of 117 and 1800 and the enum table of 117 |
| `tools/` | texconv (Microsoft DirectXTex, MIT) and the Vorbis codebook file `packed_codebooks_aoTuV_603.bin` of ww2ogg (BSD-3-Clause) with its notice `ww2ogg-COPYING.txt`; the program finds both there. Linux has the codebook file only |
| `LICENSE`, `LICENSE-UBISOFT.md`, `THIRD-PARTY.txt` | The licences and the list of the libraries with their versions and licences (written from the build's environment) |

Notes on `AnnoToolbox.exe`:

- An error that nothing caught goes to `%LOCALAPPDATA%\anno-toolbox\logs` and into a message box.
- The program's log lines (a lost game link, an update that failed) go to `toolbox.log` in that folder.
- That log is 512 KB a file at most, with two older files kept.
- Only the 20 newest error reports stay.

Notes on `AnnoToolbox.com`:

- It sits beside the window's exe under the same name, in the way Visual Studio has `devenv.com` and `devenv.exe`.
- In a terminal, `AnnoToolbox rdm ...` runs this file, because the shell's `PATHEXT` puts `.com` before `.exe`.
- It is the light command line (`AnnoToolbox rdm ...`, `AnnoToolbox export ...`).
- Started without a command, it prints its help.
- For a check, `AnnoToolbox --app islands --smoke` starts every app once, gives the exit code and prints one line per
  app.
- PyInstaller builds it as an `.exe`, and the build renames it.
- A checkout's own `anno-toolbox` command is unchanged.
- The separate `anno-toolbox.exe` of earlier builds is gone, because an update replaces the whole `current` folder and
  so none is left behind.

Notes on `_nativegpu.pyd`:

- It uses NVRHI (MIT), the DirectX headers (MIT) and nanobind (BSD-3-Clause).
- It is about 1 MB, with its C++ runtime linked in.
- It binds the game's own compiled shaders (`rendering/native-backend.md`).
- Without it, or without Direct3D 12, the island view draws its own look.
- `scripts/build_native.py` builds it ([`../native/d3d12/README.md`](../native/d3d12/README.md)).
- Its licences are in `THIRD-PARTY.txt`.

**What never goes in.** No game file of any Anno game goes in: not an archive, a model, a texture, a sound or a schema
extracted from a game's data. The games are automatically searched for on the computer the program runs on: the install
settings come first, then Steam's and Ubisoft's registrations. A user without a game can use the parts that need none.
Nothing the program writes (a mod, an export, an edit file) is part of the installer either.

## The install

- **Per user, no administrator.** `Setup.exe` puts the program into `%LOCALAPPDATA%\AnnoToolbox`. That folder holds:
  - the updater (`Update.exe`),
  - a launcher with the program's own name (`AnnoToolbox.exe`) that starts whichever version is current,
  - the downloaded packages (`packages\`), and
  - the program itself in `current\` (each update replaces `current\`).

  Nothing outside that folder is changed, except the shortcuts the person chose (below) and the entry in Windows'
  *Apps* list.
- **`Setup.exe` shows a small progress window and no pages.** It has no choices; the choices are the setup page's
  (below). When it is done it **starts the program**, as the old installer's "Start Anno Toolbox" box did. That first
  start is the one that opens the setup page. Velopack's documentation says that the installer launches the application
  for the first time after the install, and that `Setup.exe --silent` suppresses the launch. This was not seen on this
  machine: see "What was not run".
- **The *Apps* list.** Windows' Settings > Apps lists the program with the name and the publisher the build gave the
  packer (`--packTitle`, `--packAuthors`: "Anno Toolbox", the project's name). It also shows the version of the release
  and an *Uninstall* button, which runs the installer's uninstaller. The icon is the packer's `--icon`. The project has
  no icon file yet, so the entry shows the exe's own icon (`build_app.py --icon FILE.ico` sets one).
- **Folder.** `Setup.exe --installto <folder>` installs elsewhere. The person who wants that runs it from a prompt.
- No file association is made. The Toolbox has no document type of its own that Windows should open by double click.
- **Never touched, by an install, an update or an uninstall:** the settings and caches in
  `%LOCALAPPDATA%\anno-toolbox` (the suite's settings file, the game indexes, the logs). A new install therefore finds
  the games, the theme and the project list again. Only the uninstall dialog's checkbox (below) removes them.
- **A source run** (a developer's checkout) has no installer. It has no shortcuts and no *Uninstall* entry. It has no
  self-update either: the update notice sends it to the release page. It has no setup page of the installer's kind, but
  the page still opens once for a person who has no settings file yet.

### Shortcuts

The packer is told to make **no** shortcuts (`--shortcuts None`), so an update never brings back one the person removed.
The setup page has two ticked boxes, **Start menu entry** and **Desktop shortcut**, as the old installer offered. The
program makes and removes the `.lnk` files itself through Windows' shell-link interface (`suite/shortcuts.py`, `ctypes`,
no extra library). The choice is kept in the settings (`shortcut_start_menu`, `shortcut_desktop`). The page, opened
again from Help > *Setup...*, changes it.

- A shortcut points at the **launcher in the install root**, never at the versioned `current\` folder, so it survives
  every update. It has no arguments, because the installed program opens the suite's window by default.
- The packer names that launcher after the product (`Anno Toolbox.exe`), not after the program (`AnnoToolbox.exe`). The
  program therefore looks for its own name first and then for the root's one other `.exe` (`install.launcher`).
- The files are `Anno Toolbox.lnk` in the user's Start menu folder (*Programs*) and on the user's desktop. The folders
  come from Windows' known-folder call, so a moved desktop or a OneDrive-redirected one is found.
- The uninstall removes the shortcuts **the program made**: an `Anno Toolbox.lnk` in those two folders whose target is
  inside the install root. A shortcut of the same name that points elsewhere (the old installer's, a person's own)
  stays.
- Tests use a temp folder as the Start menu and the desktop. No test touches the real ones.

Note: 0.2.0 looked for its own name only, found no launcher and made no shortcut (its page did not offer them). The
next start of a fixed version makes the chosen shortcuts **once**, when the program never made any. The `shortcuts-made`
mark in the data folder records that it did. A shortcut removed after that is never brought back.

### The start-up calls

The installer starts the program itself at four moments and expects an immediate exit. `suite/installevents.run()` is
therefore **the first call of the program** (`suite/toolbox.main`), before Qt and before the arguments are read.
Velopack's documentation says that the builder runs first in the main process. `AnnoToolbox.com` (the console build)
goes through the same call. Outside an installed program (not packaged, or no `velopack` module) the call does nothing.

| Moment | Time limit | What the program does |
|---|---|---|
| After the install (`after-install`) | 30 s | Removes the old updater's download folder (`updates` in the data folder). No window |
| After an update (`after-update`) | 15 s | The same clean-up |
| First run (`first-run`: the first normal start after an install, not after an update) | none (a normal start) | Leaves the mark `first-run` in the data folder. The shell then opens the setup page and deletes the mark when it is answered |
| Before the uninstall (`before-uninstall`) | 30 s | Removes the program's own shortcuts, and the mark. Removes the settings and caches **only** on a fresh request (see "Uninstall") |

The old updater's download folder holds the downloaded installers and patches of 0.1.x, 100 MB or more each.

A call that fails is logged and never raised, so it cannot stop the installer. A call may not show a window. Calls
cannot cancel the install or the uninstall.

## First-run setup

The setup page (`suite/setuppage.py`; its choices and their rules are in `suite/setupchoices.py`) opens in three cases:

- at the first start after the installer (the mark above),
- at the first start of a person who has no settings file yet (a source run), and
- from Help > *Setup...* at any time.

Once it has been answered (*Save* or *Later*), the setting `setup_done` keeps it from opening by itself again. It shows:

- **The interface language**, a combo of the languages found. A change applies when an app is next started.
- **The games' install folders**, one line per game of the suite. They are the folders the settings found when they
  were loaded. That is the same detection as the Settings window: the install settings, Steam's and Ubisoft's
  registrations, and the drives. Each folder has *found* or *not found* beside it, and a *Browse...* button.
  - A start looks for the games that are not set once a day at most, because a drive that sleeps made every start wait.
    Help > *Setup...* looks again at once.
  - A folder that is not that game's install is refused and named. The old value stays.
  - A button opens the island editor's own Settings window for the rest (mods folder, workspace, theme).
    The page does not copy that dialog. A folder typed in the page and not saved yet stays when that dialog closes.
- **The Start menu entry and the desktop shortcut** (only in an installed program). Both are ticked unless the old
  install had neither (see "The move").
- **Check for updates at start** (the `check_updates` setting; the same box as Help's).
- **A pointer to the link helper's guide** ([`live-link.md`](live-link.md)). The helper connects the Toolbox to a
  running game, and it is never installed for the person.
- **Remove the older installation**, only when the Apps list still holds one of the old Windows-installer entries (the
  person who ran `Setup.exe` by hand). Its settings stay.

*Later* marks the page done without changing anything. The work behind *Save* runs on a thread of its own: the old
install's removal first, then the shortcuts. A problem is reported in a message.

## Updates

The public edition looks for a newer release of itself (`suite/updates.py`, `suite/updatenotice.py`). The full source
never does: `RELEASES`, the release page's `owner/name`, is empty there, and the export sets it to the public
repository. With it empty there is no Help menu entry and nothing asks the network.

- **What is sent.** At a check, one HTTPS request goes to `https://api.github.com/repos/<RELEASES>/releases/latest`
  (timeout 10 s) with the header `User-Agent: anno-toolbox/<version>` and nothing else. It carries no identifier, no
  list of installed games and no settings. The link helper's check (below) adds one more request, for the helper's
  checksum file. After a click on "Download and install", the installer's updater reads the repository's release list
  (Velopack's GitHub source, `https://github.com/<RELEASES>`) and our `SHA256SUMS`, and downloads the update packages.
  That request is the updater's own and carries its own User-Agent.
- **When.** At the start, at most once a day, on a background thread. The date of the last answered check is in the
  settings (`last_update_check`). Help > *Check for updates...* asks every time and says "up to date" or shows the new
  version. A check can fail when the computer is offline, when GitHub refuses the request (rate limit) or when the
  answer cannot be read. At the start, a failed check is only one log line. Asked by hand, it shows a short message.
- **Which version.** The tag `vX.Y.Z` of the latest release is compared with the package's `__version__`, by number.
  A suffix such as `rc1` counts as older than the plain version. The release must carry `SHA256SUMS`.
- **The notice.** A found update shows "Version X is available" in the status bar. A click opens a dialog with the
  version, the release's text (as plain text) and the buttons *Download and install* and *Later*. **Nothing is
  downloaded without that click.** A second notice, "A new link helper is available", is described below.
- **The flow in an installed program** (`updatenotice.fetch`, then `updaterun`, which calls `updates.stage` and
  `updates.apply`). The status bar only says that a version is available. The update itself runs in the launcher window
  (the start-up screen, `suite/splash.py`), not in a pop-up of its own:
  0. *hand-over*: "Download and install" closes the main window the usual way. Its Save / Discard / Cancel questions
     run, and a cancel stops the update with nothing started. The launcher window then opens on the update. It stays
     open past other windows and ends the program itself;
  1. *check* ("Checking version X..."): the installer's updater reads the release feed (`releases.win.json`) of the
     repository, and the program compares the feed with `SHA256SUMS` (below). A feed that offers another version than
     the release says, or whose hashes differ from `SHA256SUMS`, stops here, before any package is fetched;
  2. *download* ("Downloading version X... N%", with a bar): only what changed is fetched, as **delta packages** (the
     changed parts of the changed files) when one fits the installed version, else the full package. The updater checks
     each package against the hashes of its own feed;
  3. *apply* ("Installing version X...", the bar full): the updater swaps `current\` for the new version and
     **restarts the program**, which then starts with its normal start-up screen. Settings and caches stay;
  - *failure*, in any step: the launcher window names the step ("The update did not work"), the updater's error and the
    path of the log it wrote (the error logs, `<appdata>/logs`). It has the buttons *Start the Toolbox* (a normal start
    in a new process) and *Close*. The work runs on a thread, so the window never freezes. A failure before step 3
    changed nothing in the old installation.
- **Where files go.** The packages go to `packages\` in the install root. That folder is the updater's own, and it
  removes the old packages. The program writes nothing of its own for an update.
- **https only.** Our requests refuse a URL that is not https and a redirect that leaves https (`updates._request`,
  `updates._opened`). The updater's source is an https address.
- **Without the installer** (a source run, or a program folder copied by hand, with no updater in the root): the dialog
  says the version is out and offers *Open the release page*, where the new `Setup.exe` is. Nothing is downloaded. The
  installer is the way the Toolbox ships. A pip install by hand is meant mainly for development and is such a source
  run, so the dialog gives no pip line either.
- **Switching it off.** Untick Help > *Check for updates at start* (the `check_updates` setting; also on the setup
  page). The entry for a check by hand stays.

Note: decided on 2026-11-17 that the installer is the way the Toolbox ships.

### Verifying an update

Three layers apply, all before the old files are replaced:

1. **The updater's own hashes.** Each package is checked against the SHA1 and SHA256 of the release feed. A delta that
   does not rebuild the same full package is refused, and the full package is used.
2. **Our `SHA256SUMS`.** The release carries it. It lists `Setup.exe`, the full and delta packages of the release, the
   feed's index files, and the exes. Before the download, `updates.verify_feed` requires two things. The feed's SHA256
   of every package it is about to fetch must be the one `SHA256SUMS` lists, and the feed's version must be the
   release's.
3. **The build attestation.** GitHub's CI signs a statement that these exact files were built from the tagged commit of
   this repository by the release workflow (below, "Verifying a download"). The program does not check it itself,
   because it needs GitHub's tools. A person can check it. The attestation also covers `Setup.exe` and `SHA256SUMS`.

Layer 2 is a cross-check of two lists, not a second download. It shows that the feed and the attested checksum file say
the same. A change of the feed alone (a different package behind the same name) is caught. A change of both lists is
what the attestation of layer 3 is for.

### The link helper's notice

The release carries the link helper (`version.dll`, with `live-link-SHA256SUMS`; [`live-link.md`](live-link.md)
section 15). At each check, the updater reads that checksum file, and only that file. The latest release is looked at
even when the program is up to date. The updater compares the file with the hash the last checked release had
(`helper_seen` in the settings), so a release that carries the same helper again announces nothing. The first check only
records the hash.

An installed helper that already is the new one announces nothing either. It is found at
`<Anno 117>/Bin/Win64/version.dll` and recognised as ours by its marker text or its hash. The installed file is never
the reference, since a build of the full helper library has the marker but never the release's hash.

A new helper shows the status-bar notice "A new link helper is available". A click opens the release page in the
browser. **The helper is never downloaded or installed by the program.** It goes into the game's folder, so the person
fetches it, verifies it as in "Verifying a download", and puts it there.

## Uninstall

- **From Windows' *Apps* list** (Settings > Apps > Anno Toolbox > Uninstall): the installer's uninstaller runs the
  `before-uninstall` call, which removes the program's shortcuts. It then removes `%LOCALAPPDATA%\AnnoToolbox` and the
  *Apps* entry. **The settings and caches stay**, as they did with the old installer.
- **From the program: Help > *Uninstall...*** (an installed program only; a source run does not show the entry). A
  dialog says the program closes and is removed. It names the data folder (`%LOCALAPPDATA%\anno-toolbox`) with its size,
  and has a box **Also delete my settings and caches**, off by default. The size is counted off the window's thread, so
  the dialog opens at once and says *measuring...* until it is known. *Uninstall* then does four things:
  - it closes the window the usual way (a cancel of one of its questions stops it),
  - it leaves the answer for the uninstaller,
  - it starts the installer's uninstaller (`<root>\Update.exe uninstall`, as a list of arguments), and
  - it quits.
- **How the box's answer reaches the deletion.** The program is gone by the time the uninstaller runs the
  `before-uninstall` call, so the dialog leaves a request file (`uninstall-request.json` in the data folder, with the
  time). The call deletes the settings and caches only for a request that is **at most ten minutes old**. A request from
  an uninstall that was cancelled never deletes anything later, and a failed start of the uninstaller withdraws it at
  once. The call moves the data folder aside by a rename, which is instant, and has a separate `rmdir` remove it. A big
  cache therefore does not run into the call's 30 seconds. Without a request the call leaves the folder alone.
- The shortcuts are removed by the call in both ways (see "Shortcuts").
- The whole install folder is removed by the uninstaller. A program that was running from a copied folder has no
  uninstaller to start, and the menu entry is not shown.

## The move from v0.1.x

**What the old program does.** A 0.1.x program (the Windows-installer versions) reads only the release page's *latest*
release. It needs that release to carry **`AnnoToolbox-<that version>.msi` and `SHA256SUMS`**. It downloads the `.msi`
and checks its SHA256 against `SHA256SUMS`. It then closes itself and runs `msiexec /i` on the file, in Windows' own
installer window, with progress. A release without the `.msi` makes the old program answer "the release page's answer
is not usable" and offer nothing. Its code is fixed, because it is on people's computers, so the move has to arrive as
an `.msi` with that name.

**What every release of the 0.2 line therefore carries: a migration package**, `AnnoToolbox-<version>.msi`. It is built
with the same WiX 6.0.2 as before and used for this one job (`scripts/installer_wix.py`):

1. It has the **same upgrade code** as the old installer and a higher version, so Windows removes the old install as a
   major upgrade and installs the package. The removal covers the old install's files, its Start menu entry, its
   desktop shortcut and its registry records. The install is per user with no administrator, the same as the old one.
2. It carries the release's `Setup.exe` (`%LOCALAPPDATA%\Anno Toolbox Migration\Setup.exe`) and **runs it, as the user,
   once its own install has finished**. `Setup.exe` installs the new program and starts it. The setup page then opens
   on that start, in front of the person who clicked "Download and install" in the old program.
3. Before the old install is removed, a registry search reads which of its shortcuts existed (the old installer's
   component records). The package writes the answer, and its own product code, to
   `HKCU\Software\AnnoToolboxMigration`.
4. **The program removes the package's own leftover.** The package registers itself in the *Apps* list, which would
   show the migration entry beside the new program. At its first start, the program reads the product code from that
   key. It runs `msiexec /x <code> /qn /norestart` on a thread of its own, using the `msiexec` of the Windows folder's
   `System32` and the arguments as a list. Windows' installer allows one install at a time, and the package may still
   be finishing when the program starts. The call is therefore tried again every 5 seconds for a minute, and a
   still-busy answer is left for the next start (the key stays until the removal worked). Then the *Apps* list shows
   only the new program.
5. **Settings and caches are never touched** by any of this. They live in `%LOCALAPPDATA%\anno-toolbox`, outside every
   folder the old install owned. The old install's records that the removal deletes are its own registry values, not
   the program's settings.
6. **The shortcut choice carries over.** The new program makes no shortcut by itself. The setup page starts from the
   old install's choice: a person who had the Start menu entry and no desktop shortcut finds exactly that ticked.
7. The old updater's download folder (`updates` in the data folder) is removed by the `after-install` call.

**Who does not get the move automatically.** The old program only looks at the *latest* release. Every 0.2.x release
carries the package named after its own version, so a 0.1.x install moves through whichever 0.2.x is the latest when it
checks. **The package is built only while the version is below 0.3.0** (`MIGRATION_BELOW` in `scripts/build_app.py`).
The release workflow builds it through that script, and the log says "none for ..." from 0.3.0. From 0.3.0 on, an old
install that had not moved sees "not usable" and must **install `Setup.exe` once by hand** from the releases page. The
setup page then offers that older install for removal (its settings stay). The same two sentences go into the notes of
every 0.2.x release and of 0.3.0 (the ready line below).

**A person who runs `Setup.exe` by hand while the old install is there** has two entries and, while the old one stays,
two Start menu entries. The setup page finds the old installer's entries in the *Apps* list. They are the
Windows-installer entries of "Anno Toolbox": `WindowsInstaller` set, with a product code as the key. The page offers to
remove them with its box. This detection reads the per-user *Apps* registry key. Its layout for a per-user installer
package was not seen on a real install (see the checklist).

**Ready line for the release notes** (0.2.x and 0.3.0):

> Moving from 0.1.x: the program's own "Download and install" in 0.1.x brings you to the new installer in this release
> (your settings and caches are kept). From version 0.3.0 on this bridge is no longer built: if your 0.1.x program says
> it cannot check for updates, download `AnnoToolbox-win-Setup.exe` from this page and run it once; it offers to
> remove the older installation, and your settings stay.

**How long the leftover removal stays.** The program's removal of the migration package's entry costs one registry read
at a start when there is nothing to do. It stays while any supported release carries the package. Once no supported
release does (0.2.x out of support), these can go together:

- `migrate.py`,
- its call in `suite/shell.py`,
- the migration key, and
- `scripts/installer_wix.py` (the signing helpers move to `build_app.py`).

The old-install offer on the setup page can stay, because a person can still have an old install.

## What replaced the Windows-installer code, and what stays

| Was | Now |
|---|---|
| The full program as an `.msi`, written by WiX (`installer_wix.wxs`, a component per folder, its pages, its upgrade rules) | `Setup.exe` and the update packages from `vpk pack` (`build_app.py --pack`) |
| `AnnoToolbox-<old>-to-<new>.msp` patches, `updates.choose`, `download_update`, `start_installer`, `patched-version.txt` | The installer's delta packages; `updates.stage` and `updates.apply` |
| The installer's welcome, licence and feature pages, per-machine scope, `INSTALLFOLDER` on the command line | The setup page (choices after the install), `Setup.exe --installto`; the licences ship in the program folder |
| WiX (the full program, UI extension) | WiX only for the migration package, without its UI extension (`installer_wix.py`, about 130 lines) |
| `installer_bundle.py`, the PyInstaller folder, texconv, the schemas, `SHA256SUMS`, signing, the attestation | Unchanged in purpose (signing now goes through the packer) |
| The update check, its once-a-day gate, the notice and its dialog, the link helper's notice | Kept; only the install step behind "Download and install" changed |

## The unsigned installer and Windows' reputation check

`Setup.exe` and the exes are **not signed**. A code-signing certificate costs money and needs an identity check, and an
unsigned file is what a small free project has today. Windows reacts in two places:

- **SmartScreen** shows "Windows protected your PC" when `Setup.exe` (or the migration package) is run from a download,
  with the publisher "Unknown". Click *More info*, then *Run anyway* to start it. SmartScreen's reputation grows with
  downloads of the same file, and a new version starts from zero again. A program started by the updater is not
  downloaded by a browser and gets no such page. The migration package is started by the old program.
- A browser may flag the download as "uncommon". Verify the download instead (below).

`Setup.exe` is an unofficial fan project's installer. The program is not made, endorsed or supported by Ubisoft, and it
carries no game files. Install only a copy you got from the project's own releases page.

**A self-signed certificate is not offered.** Installing our own root certificate would make the user's PC trust
anything signed with that key, for every program and not only this one. It also does not clear SmartScreen, which looks
at the publisher's reputation and not at whether the chain is locally trusted. No guide of ours asks for such a step.

### Signing, when it exists

The build has an optional signing step, which is off by default.

- `--sign 'COMMAND {file}'` is passed to the packer (`--signTemplate`, the command run once per file with `{{file}}`).
  The packer signs the program's exes and libraries and `Setup.exe`, and the migration package is signed the same way.
- `--sign-file PATH` signs any built file in the same way (used for a library built elsewhere).
- The command is yours. It can be `signtool sign /fd SHA256 /tr <timestamp url> /td SHA256 /a {file}` with a
  certificate in the store, or a script that hands the file to a signing service and waits for the result.

Who signs:

- **Public edition.** Once the repository is open source (an OSI licence) and its release is built by the repository's
  own CI, the free open-source signing of the **SignPath Foundation** can sign it. The CI builds and uploads the
  unsigned files, and SignPath signs them after checking that they come from that CI build. The publisher that Windows
  shows is then "SignPath Foundation". The release workflow carries that job, switched off and commented, until the
  project is onboarded there.
- **Any other build** stays unsigned.

## Verifying a download

Each release has `SHA256SUMS` next to `Setup.exe` and the packages, in the format of `sha256sum`. It lists:

- `Setup.exe`,
- the full and delta packages of the release,
- the update feed's index files,
- the migration package (while it is built), and
- `AnnoToolbox.exe`, `AnnoToolbox.com` and any library our package ships, as they are in the program folder.

The release's files carry **build provenance attestations** made by GitHub's CI. An attestation is a signed statement
that this exact file was built from this repository's tagged commit by the release workflow.

```powershell
Get-FileHash .\AnnoToolbox-win-Setup.exe -Algorithm SHA256       # compare with the line in SHA256SUMS
gh attestation verify .\AnnoToolbox-win-Setup.exe --repo <owner>/anno-toolbox
gh attestation verify .\SHA256SUMS --repo <owner>/anno-toolbox
```

```bash
sha256sum -c --ignore-missing SHA256SUMS     # the exes in it exist only after an install
```

The attestation says where the file came from, and the checksum says that it was not changed on the way. Neither is a
signature that a Windows dialog shows. The build itself writes `SHA256SUMS` and reads it back
(`installer_bundle.verify_sums`). A mismatch stops the build.

## Linux

**Linux support is experimental.** It was built and run in WSL with software rendering only, never on a real desktop
with a GPU or with the games under Proton. The program says so on the first-start page and in Help > About, with a link
to the issue tracker. Please report what does not work there.

From the first release after 0.2.0 the releases page also carries a Linux download. Only the Toolbox runs on Linux:
there is no link helper for it. The Windows-only features stay off: the Bink video screens, the RAD-encoded videos, the
GPU BC7 encoder and the Direct3D 12 island view.

| File | What |
|---|---|
| `AnnoToolbox-linux-x86_64.AppImage` | The release: one file, run it (`chmod +x`, double click) or put it anywhere. It holds the same program as the Windows folder, without texconv (the codebook file is in it) and without the Direct3D 12 module |
| `AnnoToolbox-<version>-linux-x86_64.tar.gz` | The same program folder as a plain archive, for a computer without FUSE or for a look inside. It has no installer and no updater (the update notice sends to the release page); `install-desktop-entry.sh` in it writes a menu entry for the folder it sits in |
| `AnnoToolbox-<version>-linux-full.nupkg`, `-linux-delta.nupkg`, `releases.linux.json`, `RELEASES-linux`, `assets.linux.json` | The update feed of the AppImage: the packer's (Velopack's) Linux channel, delta packages included. The names carry `linux` so they never meet the Windows files on the same release |
| `SHA256SUMS-linux` | The checksums of the Linux files above. Both lists are attested by the release workflow |

Notes on the table:

- The AppImage has one program file, `AnnoToolbox`. It is the window, and started with a command (`AnnoToolbox rdm ...`)
  it is the command line too, so there is no split into two files.
- The Windows job attaches `SHA256SUMS`, and two jobs cannot write one file. That is why the Linux files have their own
  list.

**What the system provides.** The AppImage brings Python, Qt (PySide6), numpy, scipy, Pillow, moderngl, miniaudio and the
packer's updater. Qt's window plugin loads a few system libraries that are on every desktop distribution: `libEGL`,
`libGL`, `libxkbcommon`, `libfontconfig`, `libdbus-1`, and on X11 `libxcb-cursor0` (Qt 6.5 and later). A minimal
container needs `apt install libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libxcb-cursor0`. Starting an
AppImage itself needs FUSE 2 (`libfuse2`; Ubuntu 22.04 and later: `libfuse2t64` on 24.04) or the flag
`--appimage-extract-and-run`. The 3D views need OpenGL 3.3 (Mesa is enough). The release is built on **Ubuntu 22.04
(glibc 2.35)**, the oldest system it supports, and runs on newer ones.

**The install.** There is none: an AppImage is the program. It keeps the settings in `~/.config/anno-toolbox`, its
data and logs in `~/.local/share/anno-toolbox` and its caches in `~/.cache/anno-toolbox` (the XDG folders). The
first start opens the setup page for a person with no settings file yet. The packer fires no first-run call on Linux
that was seen: see "What was verified, Linux". The setup page's two boxes are **Applications menu entry** and
**Desktop shortcut**. They write `anno-toolbox.desktop` into `~/.local/share/applications` and the desktop folder
(`XDG_DESKTOP_DIR`), pointing at the AppImage file, with the icon copied to `~/.local/share/icons`
(`suite/shortcuts.py`). If the AppImage is moved, the entry points at nothing: open Help > *Setup...* again.

**Updates.** The flow is the same as on Windows (above):

- The check reads `releases/latest` and needs `SHA256SUMS-linux` on the release. A release without Linux files says so
  and offers nothing.
- `updates.verify_feed` ties the packer's feed to that file.
- The download is the delta when one fits.
- The packer's updater (`UpdateNix`, inside the AppImage) replaces the AppImage file and restarts it. The launcher
  window, the steps and the failure page are the same, and the failure page's buttons start the AppImage file again.

The link helper's notice does not exist on Linux. A tarball copy has no updater and says "download the new version".

**Uninstall.** Help > *Uninstall...* (an AppImage only) asks the same question with the box *Also delete my settings and
caches*. It then does four things:

- it closes the window,
- it removes the desktop entries that point at the AppImage, and the icon,
- it removes the three XDG folders when the box is ticked (each moved aside at once, removed apart from the program by
  `rm -rf`), and
- it deletes the AppImage file.

With no box ticked the settings stay.

**Build it** on Linux. It needs Python 3.12 or newer with the `gui`, `music` and `convert` extras, PyInstaller, the
`velopack` module, and the packer, which needs .NET 8 (`dotnet tool install vpk --version 1.2.161 --tool-path tools/vpk`):

```bash
python scripts/build_app.py --smoke --pack      # out/installer/full/: the folder, the AppImage, the feed, the tarball
```

The script is the same as on Windows. What it does differently on Linux (`scripts/installer_linux.py`):

- one program file and no texconv (the Vorbis codebook file ships),
- no Direct3D 12 module,
- `vpk pack` writes an AppImage, with an icon PNG drawn by the script packed in,
- the AppImage is renamed after its architecture,
- the tarball is made,
- `SHA256SUMS-linux` is written, and
- `--smoke` also starts the AppImage with `--appimage-extract-and-run`.

`.github/workflows/release.yml` has a `linux` job (Ubuntu 22.04) that does this on a tag and attaches the files next to
the Windows ones. A `v0.2.0` tag builds the Windows files only. The public repository's CI workflow has the same build
without publishing (`linux-build`). It runs only when started by hand, never on a push.

Note: the experimental status was written on 2026-11-19. The Linux download after 0.2.0 was decided on 2026-11-18.

## Build it

The build needs these:

- the build's own Python environment with the `gui` extra, PyInstaller, the `velopack` module and nanobind
  (`pip install -e ".[gui]" pyinstaller==6.22.3 velopack==1.2.161 nanobind==2.9.2`)
- for the game look's module, Visual Studio's C++ build tools (2022 or later, with CMake and Ninja) and git:
  `build_app.py` runs `scripts/build_native.py` when the module is missing or older than `native/d3d12/`
  ([`../native/d3d12/README.md`](../native/d3d12/README.md)); `--no-native` builds a program without it
- texconv in `tools/`, and the Vorbis codebook file `packed_codebooks_aoTuV_603.bin` there too (the build stops without it; the notice text is written by the build)
- **vpk**, the installer's packer, a .NET tool installed per user with no administrator
  (`dotnet tool install vpk --version 1.2.161 --tool-path tools/vpk`; [`../tools/README.md`](../tools/README.md)).
  **Its version is the version of the `velopack` module**, pinned exactly in both places.
- for the migration package, WiX 6.0.2 (`dotnet tool install wix --version 6.0.2 --tool-path tools/wix`)

```bash
python scripts/build_app.py --edition public --smoke --pack    # the public edition
python scripts/build_app.py --skip-bundle --pack                # only the packing again, from the folder of the last build
python scripts/build_app.py --pack --delta-from https://github.com/<owner>/<repo>       # with deltas from the last release
python scripts/build_app.py --pack --sign 'signtool sign /fd SHA256 /a {file}'          # signed
```

The result is in `out/installer/public/`:

- `AnnoToolbox/` (the program folder),
- `AnnoToolbox-win-Setup.exe`,
- `AnnoToolbox-<version>-full.nupkg` (and `-delta.nupkg` when an earlier release was there),
- the feed's index files (`releases.win.json`, `RELEASES`, `assets.win.json`),
- `AnnoToolbox-<version>.msi` (the migration package, while it is built),
- `SHA256SUMS`, and
- `upload-list.txt` (what a release attaches).

The build runs these steps:

1. The PyInstaller spec is written: one analysis, two exes and one folder. The console exe is renamed `AnnoToolbox.com`
   after the build.
2. The schemas, texconv, the Vorbis codebooks with their notice and the licences are put next to the exes.
3. The folder is checked: the package data, the schemas, and that Qt's shader baker is there and starts.
4. `--smoke` runs the console build (`AnnoToolbox.com`) with `--version`, with `rdm --help`, and with every app of the
   suite started once. It uses a settings folder of its own.
5. `vpk pack` packs the folder with these options: `--packId AnnoToolbox`, the version (a `rc` suffix becomes SemVer's
   `-rc1`), `--packTitle`, `--packAuthors`, `--runtime win-x64`, `--shortcuts None`, `--noPortable` and
   `--delta BestSpeed`. **The deltas are made against the earlier release's full package, which has to be in the output
   folder.** `--delta-from` fetches it with `vpk download github`. The first release has none, and says so.
6. For the public edition and a version below `MIGRATION_BELOW` (0.3.0), WiX writes the migration package with that
   `Setup.exe` inside.
7. The checksums are written and verified.

**What the folder check does.** The package's non-Python files (the language files, the tables) must be in the bundle
(`package_data_missing`). The schemas must sit next to the exes.

The public edition's release is built in the public repository by `.github/workflows/release.yml` (written by the
export from `scripts/editor_public/release.yml`). On a tag `v<version>` the workflow does these steps:

- it checks that the tag is the package's version,
- it builds on a Windows runner,
- it packs, with the deltas from the earlier release,
- it writes `SHA256SUMS`,
- it attests the files, and
- it attaches them to the release.

`upload-list.txt` names the files to attach, so the earlier release's packages that the delta download left in the
folder are not uploaded again. The workflow's action versions are pinned by commit.

**The versions it pins:**

| Tool | Version | Where |
|---|---|---|
| `actions/checkout` | v7.0.1 (by commit) | `ci.yml`, `release.yml` |
| `actions/setup-python` | v7.1.0 (by commit) | both |
| `actions/upload-artifact` | v7.0.2 (by commit) | `release.yml` |
| `actions/attest-build-provenance` | v4.2.2 (by commit) | `release.yml` |
| Python | 3.12, 3.13, 3.14 in the tests; 3.14 for the release | both |
| PyInstaller | 6.22.3 (exact) | `release.yml` |
| `vpk` (the .NET tool) and the `velopack` module | 1.2.161, both exact | `release.yml`, `build_app.VPK_VERSION` |
| texconv (DirectXTex) | `may2026`, by its SHA256 | `release.yml` |
| ww2ogg codebooks | commit `14ed9b0d`, by their SHA256 | `release.yml` (both jobs) |
| WiX | 6.0.2 (the migration package only) | `release.yml`, `installer_wix.WIX_VERSION` |

The `velopack` module was released on 2026-09-29, and the tool is the same release on NuGet.

The libraries of the `gui` extra are floors (`PySide6>=6.7`, `moderngl>=5.10`, `numpy>=1.26`, `scipy>=1.11`,
`Pillow>=10`), so the release builds with the newest of each. The floors say what the code needs at least. **WiX 7 is
out (7.0.1) but stays unused.** It asks for an acceptance of its Open Source Maintenance Fee EULA, which is the owner's
decision, not a build step.

Note: the versions were looked up on 2026-11-16, and every pinned tool was at its latest release then. The newest
libraries on that date were PySide6 6.12.0, moderngl 5.13.0, numpy 2.5.5, scipy 1.18.1 and Pillow 13.0.0.

## What was verified, and what was not

Run on the build machine (2026-11-16, Windows 11):

- the whole test set of this page's logic:
  - the packer's command,
  - the migration package's source, read as XML,
  - the feed against the checksums, with a stand-in updater,
  - the start-up calls' logic,
  - the old install's removal, with a stand-in for the registry and the process,
  - the setup page's choices and windows,
  - the uninstall's request and delete logic, with temp folders, and
  - the shortcuts: real shell links made, read back and removed in a temp folder;
- **`vpk pack` itself.** vpk 1.2.161, installed per user with no administrator, packed a test folder into `Setup.exe`, a
  full package and, for a second version, a delta (`3 patched, 1 unchanged, 1 new`). It used
  `--shortcuts None --noPortable --runtime win-x64` and wrote the feed;
- the velopack module was installed and its API read (the names of the start-up calls, `UpdateManager`, `GithubSource`
  and `UpdateInfo`).

The game look's module (2026-11-17): `scripts/build_native.py --test` built it and ran its test program on this machine,
and the tests loaded it. `build_app.py`'s step that builds it and checks the folder for it was not run through a whole
PyInstaller build yet (item 1).

**Not run, and only a real release or the owner can check.** No installer, `Setup.exe` or `msiexec` was run on this
machine, as agreed.

1. A real release through the workflow. The YAML and the commands were read, and the packer and the build functions were
   run by hand on a test folder. The full PyInstaller build and the WiX build of the migration package were not run in
   this session.
2. **`Setup.exe`**: that it installs per user, shows the *Apps* entry with the right name, publisher and version, and
   **starts the program, which opens the setup page**. The first-run call must have fired on that start.
3. **The shortcut target**: that the install root holds a launcher named `AnnoToolbox.exe`. Velopack's documentation
   says the root has a stub of the program's name. The program makes no shortcut when it does not find one.
4. An **update** from one release to the next: the feed check, the delta download, the restart into the new version,
   that shortcuts made by the setup page still work, and that the settings stayed.
5. **Help > Uninstall...**, with and without the box: the updater's `uninstall` entry point (named in Velopack's CLI
   reference), the removal of the shortcuts, the data folder removal after the program has exited, and that an uninstall
   from the *Apps* list keeps the data.
6. **The move from 0.1.x**, from a real 0.1.2 install. "Download and install" in 0.1.2 runs the migration package, the
   old install and its shortcuts are replaced, `Setup.exe` runs as the user, the program starts on the setup page with
   the old shortcut choice, the migration entry disappears from the *Apps* list (the key
   `HKCU\Software\AnnoToolboxMigration` is gone), and the settings and caches are as they were. Also whether Windows'
   installer lets the package start `Setup.exe` after its last step without a prompt, and the 1618 retry in practice.
7. The per-user registry layout the old-install detection reads (`Software\Microsoft\Windows\CurrentVersion\Uninstall`
   keyed by the product code), and the old installer's component records the package searches.
8. Signing (there is no certificate), and a start on a computer that has no Python installed.

### Linux (2026-11-18)

Run in WSL on Ubuntu 24.04 (**glibc 2.39**), with Python 3.12.3, PySide6 6.12, PyInstaller 6.22.3, velopack 1.2.161 and
vpk 1.2.161 on .NET 8 (installed per user). A build there links to older symbols, so it runs where this one runs. The
release's 2.35 floor itself was not seen, because that is not the release's 22.04 system.

`build_app.py --smoke --pack` did the following:

- it built the program folder (375 MB),
- it checked the folder (the package data, and that Qt's shader baker is there and starts),
- it ran the console smoke (`--version`, `rdm --help`, all 13 apps started once, Qt offscreen),
- it packed the AppImage (152 MB), the full update package (151 MB), the feed and the tarball (147 MB),
- it wrote `SHA256SUMS-linux` and read it back, and
- it started the AppImage itself with `--appimage-extract-and-run` (the same three starts, exit 0).

Further results:

- The window program started from the AppImage stayed up for 20 s offscreen.
- The tarball's program started (`--version`) and its `install-desktop-entry.sh` wrote a valid entry.
- `ldd` of the program shows no missing library on that system.
- The desktop entry, its reading back, the removal by the uninstall event, the AppImage's place check, the tarball and
  the release names are covered by `tests/suite/test_linux_package.py`.

**Not run:**

- a real desktop session (X11, Wayland),
- FUSE (WSL has none: only extract-and-run),
- Ubuntu 22.04 itself, and a second distribution,
- the updater's download and restart (one version only: no delta, no feed on a server; the packer's `download github` on
  Linux was not run),
- whether the packer fires its first-run and uninstall calls for an AppImage (the program arms them only when
  `APPIMAGE` names the file; the setup page opens anyway for a person with no settings file),
- a double click on the created `.desktop` file (GNOME's "trust" prompt: the program asks `gio` to mark it trusted),
- Help > Uninstall's removal of a real AppImage,
- the release workflow's `linux` job and the CI's `linux-build` (read, not run), and
- attesting the Linux files.

Note: this page was written on 2026-11-16 for the move to Velopack in 0.2.0. The earlier Windows-installer results (a
per-user install, smoke start and uninstall of the old `.msi`, 2026-11-13) no longer describe the shipped installer.
