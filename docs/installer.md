# The Windows installer

The Anno Toolbox is provided as a Windows program (a folder with two exes) and as a standard Windows installer, an
`.msi`. This page says what goes in and what never does. It also
says how the Toolbox installs, upgrades and uninstalls, how a download is verified, and how to build it.
[`suite.md`](suite.md) says how the Toolbox is used.

## What goes in

| In the program folder | What |
|---|---|
| `AnnoToolbox.exe` | The window: every app of the suite, with no console. The Start-menu shortcut starts it (`--app islands`: the suite's window, on the island editor), and so does the installer's last page while its "Start Anno Toolbox" box is ticked. An error that nothing caught goes to `%LOCALAPPDATA%\anno-toolbox\logs` and into a message box |
| `anno-toolbox.exe` | The same program as a console program: the light command line (`anno-toolbox rdm ...`, `anno-toolbox export ...`) and, for a check, `anno-toolbox --app islands --smoke` (every app is started once; it gives the exit code and one line per app) |
| `_internal/` | Python, Qt (PySide6, with its shader baker `qsb.exe`, which the 3D views need), moderngl, numpy, scipy, Pillow and the package with its data files (the interface languages, the descriptions, the tables) |
| `docs/formats/` | The three files the program reads next to its exes: the Phoenix schemas of 117 and 1800 and the enum table of 117 |
| `tools/` | texconv (Microsoft DirectXTex, MIT); the program finds it there |
| `LICENSE`, `LICENSE-UBISOFT.md`, `THIRD-PARTY.txt` | The licences and the list of the libraries with their versions and licences (written from the build's environment) |

**What never goes in.** No game file of any Anno game goes in: not an archive, a model, a texture, a sound or a schema
extracted from a game's data. The games are automatically searched for on the computer the program runs on (the install
settings first, then Steam's and Ubisoft's registrations). A user without a game can use the parts that need none.
Nothing the program writes (a mod, an export, an edit file) is part of the installer either.

## Install scope, upgrades, uninstall

- **The installer's pages**: welcome, the licence, what to install, progress and finish. The "What to install" page
  holds the program and two parts of it, a **Start menu entry** and a **desktop shortcut**. Both are ticked unless the
  user unticks them. An upgrade is meant to keep the choice of the older version (not yet checked in practice). The
  last page has a ticked "Start Anno Toolbox" box, which starts the program after a fresh install or an upgrade.
- **Per user, no administrator** (default). The program goes to `%LOCALAPPDATA%\Programs\Anno Toolbox`. The Start-menu shortcut is the user's own,
  and the registrations are under `HKCU`. `--scope perMachine` makes a machine-wide installer from the same source
  (Program Files, `HKLM`; it asks for administrator rights). The shipped installer is per user.
- **The folder can be chosen on the command line**: `msiexec /i AnnoToolbox-<version>.msi INSTALLFOLDER="D:\Tools\Toolbox"`.
  The folder is recorded in the registry, so an upgrade and an uninstall find it again.
- **Upgrades.** Each edition has a fixed `UpgradeCode` (`installer_bundle.EDITIONS`; it never changes). The
  installer's version is the package's `__version__` (three numbers; a suffix like `rc1` is dropped). Installing a newer
  `.msi` replaces the older one. This is a major upgrade: the old files are removed and the new ones are put in. An
  older `.msi` is refused with a message. Component GUIDs are derived from the edition and the folder, so they stay the
  same between versions.
- **Uninstall** from Windows' *Apps* page or with `msiexec /x <msi>`. Every file the installer put in, the folders it
  made and the shortcut are removed. The installer creates nothing else, so nothing else is removed. **Settings stay.**
  They live in `%LOCALAPPDATA%\anno-toolbox` and are written by the program, not by the installer. A reinstall
  therefore finds the games, the theme and the project list again. To remove them as well, delete that folder after the
  uninstall.
- No file association is made. The Toolbox has no document type of its own that Windows should open by double click.

## Updates

The public edition looks for a newer release of itself (`suite/updates.py`, `suite/updatenotice.py`). The full source
never does: `RELEASES`, the release page's `owner/name`, is empty there, and the export sets it to the public
repository. With it empty there is no Help menu entry and nothing asks the network.

- **What is sent.** One HTTPS request to `https://api.github.com/repos/<RELEASES>/releases/latest` (timeout 10 s) with
  the header `User-Agent: anno-toolbox/<version>` and nothing else: no identifier, no list of installed games, no
  settings. After a click on "Download and install", two more requests to the release's file addresses.
- **When.** At the start, at most once a day (the date of the last answered check is in the settings,
  `last_update_check`), on a background thread. Help > *Check for updates...* asks every time and says "up to date" or
  shows the new version. A check can fail when the computer is offline, when GitHub refuses the request (rate limit)
  or when the answer cannot be read. At the start, a failed check is only one log line. When asked by hand, it shows a
  short message.
- **Which version.** The tag `vX.Y.Z` of the latest release is compared with the package's `__version__`, by number.
  A suffix such as `rc1` counts as older than the plain version. The release must carry `AnnoToolbox-<version>.msi`
  and `SHA256SUMS`. Without both files, it is not offered as an update.
- **The notice.** A found update shows "Version X is available" in the status bar. A click opens a dialog with the
  version, the release's text (as plain text) and the buttons *Download and install* and *Later*. Nothing is
  downloaded without that click.
- **Where files go, how they are verified.** Both files go to the `updates` folder of the program's data folder
  (`%LOCALAPPDATA%\anno-toolbox\updates`). The `.msi`'s SHA256 must equal its line in `SHA256SUMS`. A file that does
  not match is deleted, and nothing is installed. After a good download, older downloads in that folder are deleted.
  The checksum shows that the file was not changed on the way. The build attestation (below) shows where it came from.
- **Install (the installed program only).** The window is closed the usual way, so its Save / Discard / Cancel questions
  run. If the person cancels one of them, nothing is installed. Otherwise `msiexec /i <msi>` starts apart from the
  program (with no console window) and the program quits. The `.msi` is a major upgrade: settings and caches in
  `%LOCALAPPDATA%` stay, and the shortcut choices should carry over.
- **A Python install (`pip`).** The dialog says the version is out and gives
  `pip install --upgrade "anno-toolbox[gui] @ git+https://github.com/<RELEASES>@v<version>"`; nothing is downloaded.
- **Switching it off.** Untick Help > *Check for updates at start* (the `check_updates` setting). The entry for a check
  by hand stays.
- **Not verified here.** The download from GitHub and the hand-over to `msiexec` need a real release. The tests stand
  in for the network. Also unchecked: that an upgrade keeps the shortcut choices of the older version.

## The unsigned installer and Windows' reputation check

The installer and the exes are **not signed**. A code-signing certificate costs money and needs an identity check, and
an unsigned file is what a small free project has today. Windows reacts in two places:

- **SmartScreen** shows "Windows protected your PC" when the `.msi` is run from a download, with the publisher
  "Unknown". Click *More info*, then *Run anyway* to start it. SmartScreen's reputation grows with downloads of the
  same file, and a new version starts from zero again.
- **The installer's own page** shows "Unknown publisher" in the elevation prompt (a per-user install needs none).

The installer's licence page says that the program is an unofficial fan project, that it is not signed and what to do
about it. It also shows the licence and the third-party list. A browser may also flag an `.msi` as "uncommon". Verify
the download instead (see the next section).

**A self-signed certificate is not offered.** Installing our own root certificate would make the user's PC trust
anything signed with that key, for every program and not only this one. It also does not clear SmartScreen, which
looks at the publisher's reputation and not at whether the chain is locally trusted. No guide of ours asks for such a
step.

### Signing, when it exists

The build has an optional signing step, which is off by default. `--sign 'COMMAND {file}'` runs the command once per
file on the exes, on the libraries and extension modules of our own package, and on the finished `.msi`.
`--sign-file PATH` signs any built file in the same way (used for a library built elsewhere). The command is yours:
`signtool sign /fd SHA256 /tr <timestamp url> /td SHA256 /a {file}` with a certificate in the store, or a script that
hands the file to a signing service and waits for the result.

- **Public edition.** Once the repository is open source (an OSI licence) and its release is built by the repository's
  own CI, the free open-source signing of the **SignPath Foundation** can sign it. The CI builds and uploads the
  unsigned files, and SignPath signs them after checking that they come from that CI build. The publisher that Windows
  shows is then "SignPath Foundation". The release workflow carries that job, switched off and commented, until the
  project is onboarded there.
- **Any other build** stays unsigned.

## Verifying a download

Each release has `SHA256SUMS` next to the `.msi`, in the format of `sha256sum`. It also lists the two exes and any
library our package ships, as they are in the program folder. The release's files carry **build provenance
attestations** made by GitHub's CI. An attestation is a signed statement that this exact file was built from this
repository's tagged commit by the release workflow.

```powershell
Get-FileHash .\AnnoToolbox-<version>.msi -Algorithm SHA256        # compare with the line in SHA256SUMS
gh attestation verify .\AnnoToolbox-<version>.msi --repo <owner>/anno-toolbox
gh attestation verify .\SHA256SUMS --repo <owner>/anno-toolbox
```

```bash
sha256sum -c --ignore-missing SHA256SUMS     # the exes in it exist only after an install
```

The attestation says where the file came from, and the checksum says that it was not changed on the way. Neither is a
signature that a Windows dialog shows. The build itself writes `SHA256SUMS` and reads it back
(`installer_bundle.verify_sums`). A mismatch stops the build.

## Build it

The build needs three things:

- the build's own Python environment with the `gui` extra and PyInstaller (`pip install -e ".[gui]" pyinstaller`)
- texconv in `tools/`
- for the installer, WiX 6.0.2 (`dotnet tool install wix --version 6.0.2 --tool-path tools/wix`, then
  `wix extension add WixToolset.UI.wixext/6.0.2` in that folder; [`../tools/README.md`](../tools/README.md))

```bash
python scripts/build_app.py --edition public --smoke --msi    # the public edition
python scripts/build_app.py --skip-bundle --msi                 # only the installer again, from the folder of the last build
python scripts/build_app.py --msi --sign 'signtool sign /fd SHA256 /a {file}'       # signed
```

The result is in `out/installer/public/`: `AnnoToolbox/` (the program folder), `AnnoToolbox-<version>.msi`
and `SHA256SUMS`. The build runs these steps:

1. The PyInstaller spec is written (one analysis, two exes, one folder).
2. The schemas, texconv and the licences are put next to the exes.
3. The folder is checked (the package data, the schemas, and that Qt's shader baker is there and starts).
4. `--smoke` runs the console exe (`--version`, `rdm --help`, and every app of the suite started once, with a settings
   folder of its own).
5. The `.msi` is written by WiX from the folder (a component per folder, the file list written by
   `installer_wix.wxs`).
6. The checksums are written and verified.

**What the folder check does.** The package's non-Python files (the language files, the tables) must be in the bundle
(`package_data_missing`). The schemas must sit next to the exes.

The public edition's release is built in the public repository by `.github/workflows/release.yml` (written by the
export from `scripts/editor_public/release.yml`). On a tag `v<version>` it checks that the tag is the package's
version, builds on a Windows runner, writes `SHA256SUMS`, attests the `.msi` and `SHA256SUMS`, and attaches them to the
release. Its action versions are pinned by commit.

## What was verified, and what was not

These results are from the build machine (Windows 11, Python 3.14, PyInstaller 6.22.3, WiX 6.0.2):

| | Result |
|---|---|
| Program folder | 288 MB, built in 76 s |
| `.msi` | 103 MB, 60 s |
| Smoke start of every app from the folder | eight apps and the Project window, exit 0 |

Three things were run on the build machine: a per-user install of the public `.msi` into a temporary folder (57 s), the
installed console exe's smoke start of every app (exit 0) and the uninstall (49 s). The shortcut and the registrations
appeared and went, and no file or folder was left. The installer records a folder chosen on the command line in the
registry and reads it back, so an uninstall finds it again. An **administrative install** (`msiexec /a`) needs
elevation and was not run.

**Not run here:**

- the release workflow: it runs on GitHub's servers; its YAML and the build commands it calls were read, and the
  commands were run by hand
- signing (there is no certificate)
- a start on a computer that has no Python installed
- an upgrade from an older installed version (there is no older release yet)

Note: verified on 2026-11-13.
