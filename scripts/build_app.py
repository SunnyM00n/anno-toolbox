"""Package the Anno Toolbox as a Windows program (a folder with two exes) and as an installer with updates.

PyInstaller in folder mode over the Toolbox's window and command line (``suite/toolbox.py``), then the installer's
packer (``vpk``: ``Setup.exe``, the full and delta update packages) and, for the public edition, the small migration
``.msi`` that WiX builds. Usage, requirements and the result: ``--help``, ``docs/installer.md`` and scripts/README.md.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import installer_bundle as bundle
import installer_linux as linux
import installer_wix as wix

try:  # the public edition's rules need the full source's layers; an exported tree is the public edition already
    import installer_edition as rules
except ImportError:
    rules = None

ROOT = bundle.ROOT
WINDOWS = sys.platform == "win32"  # the other system the program is built for is Linux (docs/installer.md, "Linux")
LINUX = sys.platform.startswith("linux")
TOOLBOX_EXCLUDES = ("tkinter", "pytest", "IPython", "matplotlib")  # in the build's environment, never used
VPK_VERSION = "1.2.161"  # the packer and the velopack wheel are pinned to the same version (docs/installer.md)
MIGRATION_BELOW = (0, 3, 0)  # releases below this version carry the migration .msi (docs/installer.md)
UPLOAD_LIST = "upload-list.txt"  # what a release carries, one name per line (the release workflow reads it)
FEED_FILES = ("releases.win.json", "RELEASES", "assets.win.json")  # the update feed's index files
EPILOG = """\
The Toolbox: PyInstaller's folder mode over suite/toolbox.py (the `gui` extra: PySide6, moderngl), into
out/installer/<edition>/AnnoToolbox/: AnnoToolbox.exe (the window) and AnnoToolbox.com (the same program built as a
console program: the command line rdm, export, or any window with --app; a terminal's PATHEXT picks it before the .exe)
on Windows, one AnnoToolbox program on Linux (--pack there writes an AppImage, its feed and a tarball; docs/installer.md
"Linux"), texconv (MIT) in tools/ (Windows), the Vorbis codebooks (BSD-3-Clause) in tools/, the schemas read beside the program, the licences, and
the game look's Direct3D 12 module (render/_nativegpu.pyd, built first by scripts/build_native.py when it is missing
or older than native/d3d12/; --no-native leaves it out). The games are not part of it: the program finds them on the
machine it runs on (suite.settings.detect).

--source TREE builds from another tree: the exported public edition (src/annotoolbox) gives the public build, which is
checked against the layers of the full source (installer_edition.py). --smoke starts every app once. --pack runs the
installer's packer (vpk, tools/README.md) on the folder: Setup.exe, the full update package and, when an earlier
release is in the output folder, the delta; for the public edition also the migration .msi (WiX, tools/wix); then
SHA256SUMS beside them. --delta-from REPO_URL first downloads the earlier release's packages for the delta.
--sign 'COMMAND {file}' signs the program's files (vpk) and the migration .msi (off by default); --sign-file PATH signs
any built file the same way. --icon and --notes pass an icon and a Markdown notes file to the packer.
"""
LAUNCHER = """import multiprocessing
import sys

from __PACKAGE__.suite.toolbox import main

multiprocessing.freeze_support()
sys.exit(main())
"""
SPEC = """# written by scripts/build_app.py
import os
import sys
sys.path.insert(0, {src!r})
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

import PySide6
_qt = os.path.dirname(PySide6.__file__)
try:  # the package's metadata carries its entry points: the apps it adds to the Toolbox (docs/app-api.md)
    _meta = copy_metadata({package!r})
except Exception:
    _meta = []

a = Analysis(
    [{launcher!r}],
    pathex=[{src!r}],
    binaries=[(os.path.join(_qt, n), "PySide6") for n in os.listdir(_qt) if n.split(".")[0] in {baker!r}],
    hiddenimports=collect_submodules({package!r}) + collect_submodules("glcontext") + ["velopack"]
    + (["PySide6.QtShaderTools"] if sys.platform.startswith("linux") else []),  # pulls in qsb's Qt library
    datas=collect_data_files({package!r}) + _meta,
    excludes={excludes!r},
)
pyz = PYZ(a.pure)
programs = [EXE(pyz, a.scripts, [], exclude_binaries=True, name={program!r}, console=False)]
if sys.platform == "win32":  # Linux has one program; Windows a console build beside it (renamed to .com after)
    programs.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name={console!r}, console=True))
COLLECT(*programs, a.binaries, a.datas, name={program!r})
"""


# Qt's shader baker and its library, by file stem: PyInstaller collects PySide6's modules, not its tools
SHADER_BAKER = ("qsb", "Qt6ShaderTools")


def spec_text(launcher: Path, source: Path, edition: bundle.Edition) -> str:
    """The PyInstaller spec: one analysis, two exes (windowed and console) in one folder."""
    return SPEC.format(
        src=str(source), launcher=str(launcher), package=edition.package, excludes=list(TOOLBOX_EXCLUDES),
        program=bundle.PROGRAM, console=bundle.CONSOLE_BUILD, baker=list(SHADER_BAKER),
    )  # fmt: skip


def pyinstaller_env(source: Path) -> dict[str, str]:
    """The build's environment: the tree's ``src`` on PYTHONPATH, where PyInstaller's helper processes import from."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(source), env.get("PYTHONPATH", "")]))
    return env


# The Toolbox


def add_beside_program(app: Path, tree: Path, texconv: Path | None, codebooks: Path) -> None:
    """What the program reads next to its exes: the schemas (``paths.REPO_ROOT`` is this folder), texconv, the Vorbis
    codebooks with their notice, the licences."""
    for source, rel in bundle.runtime_files(tree):
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, app / rel)
    if texconv is not None:
        shutil.copy2(texconv, app / "tools" / texconv.name)
        shutil.copy2(ROOT / "tools" / "README.md", app / "tools" / "README.md")
    (app / "tools").mkdir(exist_ok=True)
    shutil.copy2(codebooks, app / "tools" / bundle.CODEBOOKS)
    (app / "tools" / bundle.CODEBOOKS_NOTICE).write_text(bundle.CODEBOOKS_LICENCE, encoding="utf-8", newline="\n")
    for name in bundle.LICENSE_FILES:
        if (tree / name).is_file():
            shutil.copy2(tree / name, app / name)
    notices = bundle.third_party_text(bundle.third_party(), windows=WINDOWS)
    (app / "THIRD-PARTY.txt").write_text(notices, encoding="utf-8", newline="\n")


NATIVE_MODULE = "render/_nativegpu.pyd"  # the game look's Direct3D 12 module, in the package (native/d3d12/README.md)


def native_module(tree: Path, edition: bundle.Edition) -> Path:
    """The game look's compiled module in the tree's package, built by the tree's scripts/build_native.py when it is
    missing or older than its sources (needs Visual Studio's C++ build tools)."""
    target = tree / "src" / edition.package / NATIVE_MODULE
    sources = [path for path in (tree / "native" / "d3d12").rglob("*") if path.is_file()]
    newest = max((path.stat().st_mtime for path in sources), default=0.0)
    if not target.is_file() or target.stat().st_mtime < newest:
        subprocess.run([sys.executable, str(tree / "scripts" / "build_native.py")], check=True, cwd=tree)
    return target


def check_program(app: Path, tree: Path, edition: bundle.Edition, work: Path, native: bool = True) -> list[str]:
    """Everything that must hold for the folder: the edition's rules, the package data, the schemas beside it."""
    package_dir = tree / "src" / edition.package
    problems = rules.folder_violations(edition, app) if rules else []
    toc = next(iter(sorted(work.rglob("PYZ-00.toc"))), None)
    if toc is not None and rules:
        problems += rules.pyz_violations(edition, toc.read_text(encoding="utf-8", errors="replace"))
    bundled = app / "_internal" / edition.package
    problems += [f"package data missing: {path}" for path in bundle.package_data_missing(package_dir, bundled)]
    for rel in bundle.RUNTIME_FILES[:1]:
        if not (app / rel).is_file():
            problems.append(f"missing beside the program: {rel}")
    for name in (bundle.CODEBOOKS, bundle.CODEBOOKS_NOTICE):
        if not (app / "tools" / name).is_file():
            problems.append(f"missing beside the program: tools/{name}")
    if native and not (bundled / NATIVE_MODULE).is_file():
        problems.append(
            f"the game look's module missing: {edition.package}/{NATIVE_MODULE} (--no-native leaves it out)"
        )
    return problems + baker_problems(app)


def baker_problems(app: Path) -> list[str]:
    """The 3D views bake their shaders with Qt's baker at run time: it must be in the folder and start there."""
    qsb = app / "_internal" / "PySide6" / exe_name("qsb")
    if not qsb.is_file():
        return [f"Qt's shader baker missing: {qsb.relative_to(app)} (the 3D views would draw nothing)"]
    done = subprocess.run([str(qsb), "--version"], capture_output=True, text=True, timeout=60, check=False)
    return [] if done.returncode == 0 else [f"Qt's shader baker does not start: exit {done.returncode}"]


def run_pyinstaller(tree: Path, edition: bundle.Edition, work: Path, dist: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    launcher = work / "launcher.py"
    launcher.write_text(LAUNCHER.replace("__PACKAGE__", edition.package), encoding="utf-8", newline="\n")
    spec = work / "toolbox.spec"
    spec.write_text(spec_text(launcher, tree / "src", edition), encoding="utf-8", newline="\n")
    command = [sys.executable, "-m", "PyInstaller", str(spec), "--noconfirm", "--clean"]
    command += ["--distpath", str(dist), "--workpath", str(work / "pyi")]
    subprocess.run(command, check=True, cwd=work, env=pyinstaller_env(tree / "src"))
    if WINDOWS:  # the devenv way: AnnoToolbox.exe and AnnoToolbox.com, so a terminal's PATHEXT picks the console build
        (dist / bundle.PROGRAM / f"{bundle.CONSOLE_BUILD}.exe").replace(dist / bundle.PROGRAM / console_name())
    return dist / bundle.PROGRAM


def smoke_toolbox(app: Path, edition: bundle.Edition) -> list[str]:
    """The console exe: its version, an rdm help, and every app of the suite started once (own settings folder)."""
    return run_smoke([str(app / console_name())], app, console_name())


def console_name() -> str:
    """The file that runs the command line: ``AnnoToolbox.com`` on Windows, the one program on Linux."""
    return bundle.PROGRAM + ".com" if WINDOWS else bundle.PROGRAM


def smoke_env(settings: str) -> dict[str, str]:
    """The environment of a smoke start: the settings and caches in a folder of their own, Qt without a display."""
    if WINDOWS:
        return dict(os.environ, LOCALAPPDATA=settings)
    return dict(
        os.environ, HOME=settings, XDG_DATA_HOME=settings, XDG_CONFIG_HOME=settings, XDG_CACHE_HOME=settings,
        QT_QPA_PLATFORM=os.environ.get("QT_QPA_PLATFORM", "offscreen"),
    )  # fmt: skip


def run_smoke(command: list[str], cwd: Path, label: str) -> list[str]:
    """The three smoke starts of ``command`` (an exe, or an AppImage with its own flags); what failed."""
    problems = []
    with tempfile.TemporaryDirectory(prefix="toolbox-smoke-", ignore_cleanup_errors=True) as settings:
        env = smoke_env(settings)
        for argv in (["--version"], ["rdm", "--help"], ["--app", "islands", "--smoke"]):
            done = subprocess.run([*command, *argv], capture_output=True, text=True, timeout=600, env=env, cwd=cwd)
            print(f"{label} {' '.join(argv)} -> exit {done.returncode}")
            print(done.stdout.strip())
            if done.returncode != 0:
                problems.append(f"{' '.join(argv)}: exit {done.returncode} {done.stderr.strip()[-400:]}")
    return problems


def exe_name(name: str) -> str:
    """A program's file name on this system (``.exe`` on Windows only)."""
    return f"{name}.exe" if WINDOWS else name


def find_vpk() -> Path | None:
    """The installer's packer: ``ANNO_VPK``, ``tools/vpk/vpk.exe`` (``dotnet tool install vpk --tool-path tools/vpk``),
    then PATH."""
    options = [Path(os.environ["ANNO_VPK"])] if os.environ.get("ANNO_VPK") else []
    options.append(ROOT / "tools" / "vpk" / exe_name("vpk"))
    if on_path := shutil.which("vpk"):
        options.append(Path(on_path))
    return next((path for path in options if path.is_file()), None)


def pack_command(
    vpk: str, edition: bundle.Edition, version: str, app: Path, out: Path,
    icon: Path | None = None, notes: Path | None = None, sign: str | None = None,
) -> list[str]:  # fmt: skip
    """``vpk pack`` for the program folder: no shortcuts (the setup page makes the chosen ones), no portable bundle,
    deltas against the earlier packages in ``out``, x64."""
    command = [
        vpk, "pack", "--packId", edition.pack_id, "--packVersion", bundle.semver(version), "--packDir", str(app),
        "--mainExe", f"{bundle.PROGRAM}.exe", "--packTitle", edition.product, "--packAuthors", wix.MANUFACTURER,
        "--outputDir", str(out), "--runtime", "win-x64", "--shortcuts", "None", "--noPortable", "--delta", "BestSpeed",
    ]  # fmt: skip
    if icon:
        command += ["--icon", str(icon)]
    if notes:
        command += ["--releaseNotes", str(notes)]
    if sign:
        command += ["--signTemplate", sign.replace("{file}", "{{file}}")]
    return command


def download_command(vpk: str, repo_url: str, out: Path, token: str = "") -> list[str]:
    """``vpk download github``: the earlier release's packages into ``out``, where the packer finds them for the delta."""
    command = [vpk, "download", "github", "--repoUrl", repo_url, "--outputDir", str(out)]
    return command + (["--token", token] if token else [])


def release_files(out: Path, edition: bundle.Edition, version: str) -> list[Path]:
    """This release's files in ``out`` (earlier releases' packages that the download left there are not): Setup.exe, the
    full and delta packages of ``version``, the feed's index files. Only those that exist."""
    sem, pid = bundle.semver(version), edition.pack_id
    names = [f"{pid}-win-Setup.exe", f"{pid}-{sem}-full.nupkg", f"{pid}-{sem}-delta.nupkg", *FEED_FILES]
    return [out / name for name in names if (out / name).is_file()]


def pack(app: Path, tree: Path, edition: bundle.Edition, out: Path, arguments) -> list[Path]:
    """Run the packer (after the earlier release's download, when asked) and return this release's files."""
    vpk = find_vpk()
    if vpk is None:
        raise SystemExit(f"vpk not found: dotnet tool install vpk --version {VPK_VERSION} --tool-path tools/vpk")
    version = bundle.package_version(tree / "src" / edition.package)
    if arguments.delta_from:
        token = os.environ.get("GITHUB_TOKEN", "")
        done = subprocess.run(download_command(str(vpk), arguments.delta_from, out, token), check=False)
        print("earlier release:", "downloaded" if done.returncode == 0 else "none found (no delta this time)")
    if LINUX:
        icon = arguments.icon or linux.write_icon(out / "build" / linux.ICON)
        subprocess.run(linux.pack_command(str(vpk), edition, version, app, out, icon, arguments.notes), check=True)
        return []
    subprocess.run(
        pack_command(str(vpk), edition, version, app, out, arguments.icon, arguments.notes, arguments.sign), check=True
    )
    return release_files(out, edition, version)


def carries_migration(edition: bundle.Edition, version: str) -> bool:
    """Whether a release of ``version`` carries the migration .msi: the public edition's 0.2.x releases, for the old
    updater that reads only the latest release. From ``MIGRATION_BELOW`` on it is skipped."""
    return edition.key == "public" and tuple(map(int, bundle.msi_version(version).split("."))) < MIGRATION_BELOW


def make_migration(tree: Path, edition: bundle.Edition, out: Path, setup: Path, sign_command: str | None) -> Path:
    """The migration package of the public edition, with this release's ``Setup.exe`` inside."""
    tool = wix.find_wix()
    if tool is None:
        raise SystemExit(f"WiX not found: dotnet tool install wix --version {wix.WIX_VERSION} --tool-path tools/wix")
    version = bundle.msi_version(bundle.package_version(tree / "src" / edition.package))
    msi = wix.build_migration(tool, edition, version, setup, out)
    if sign_command:
        wix.sign(sign_command, [str(msi)])
    return msi


def write_sums(out: Path, files: list[Path], edition: bundle.Edition) -> Path:
    """SHA256SUMS: the release's files and the program's own exes and libraries, read back and checked at once."""
    app = out / bundle.PROGRAM
    packed = [f"{bundle.PROGRAM}/{file_name}" for file_name in wix.signable(bundle.folder_files(app), edition.package)]
    sums = out / "SHA256SUMS"
    sums.write_text(bundle.sums_text(out, [*(path.name for path in files), *packed]), encoding="utf-8", newline="\n")
    if bad := bundle.verify_sums(out, sums.read_text(encoding="utf-8")):
        raise SystemExit("SHA256SUMS does not verify:\n  " + "\n  ".join(bad))
    return sums


def build_toolbox(arguments) -> int:
    tree = Path(arguments.source).resolve() if arguments.source else ROOT
    edition = bundle.edition_of(tree, arguments.edition)
    package_dir = tree / "src" / edition.package
    if rules and (bad := rules.edition_violations(edition, package_dir)):
        raise SystemExit("the source tree is not the public edition:\n  " + "\n  ".join(bad))
    out = arguments.out or ROOT / "out" / "installer" / edition.key
    texconv = bundle.find_texconv(tree, dict(os.environ))
    codebooks = bundle.find_codebooks(tree, dict(os.environ))
    if codebooks is None:
        raise SystemExit(f"missing {bundle.CODEBOOKS} (tools/README.md): Wwise Vorbis sounds need it in the program")
    native = WINDOWS and not arguments.no_native  # the Direct3D 12 module and texconv are Windows only
    if texconv is None and not arguments.no_texconv and WINDOWS:
        raise SystemExit("missing texconv (tools/README.md); --no-texconv leaves it out")
    if native and not arguments.skip_bundle:
        print(f"native module: {native_module(tree, edition)}")
    started = time.time()
    if not arguments.skip_bundle:
        app = run_pyinstaller(tree, edition, out / "build", out)
        add_beside_program(app, tree, texconv, codebooks)
        if LINUX:
            linux.add_files(out / bundle.PROGRAM)
    app = out / bundle.PROGRAM
    print(f"folder: {app} ({sum(path.stat().st_size for path in app.rglob('*') if path.is_file()) / 2**20:.0f} MB, "
          f"{time.time() - started:.0f} s)")  # fmt: skip
    problems = check_program(app, tree, edition, out / "build", native=native)
    if arguments.smoke:
        problems += smoke_toolbox(app, edition)
    if problems:
        raise SystemExit("the program folder has problems:\n  " + "\n  ".join(problems))
    if arguments.pack:
        pack_release(app, tree, edition, out, arguments)
    return 0


def pack_release(app: Path, tree: Path, edition: bundle.Edition, out: Path, arguments) -> None:
    """The packer, the files only this system's release carries, the checksums and the list of what a release attaches."""
    started = time.time()
    files = pack(app, tree, edition, out, arguments)
    version = bundle.package_version(tree / "src" / edition.package)
    if LINUX:
        files = finish_linux(app, edition, out, version, arguments)
    for file_path in files:
        print(f"release file: {file_path.name} ({file_path.stat().st_size / 2**20:.1f} MB)")
    print(f"packed in {time.time() - started:.0f} s")
    if LINUX:
        sums = linux.write_sums(out, files)
    else:
        if carries_migration(edition, version):
            setup = out / f"{edition.pack_id}-win-Setup.exe"
            files.append(make_migration(tree, edition, out, setup, arguments.sign))
            print(f"migration package: {files[-1]} ({files[-1].stat().st_size / 2**20:.0f} MB)")
        else:
            print(f"migration package: none for {edition.key} {version} (carried below {MIGRATION_BELOW})")
        sums = write_sums(out, files, edition)
    names = [*(path.name for path in files), sums.name]
    (out / UPLOAD_LIST).write_text("\n".join(names) + "\n", encoding="utf-8", newline="\n")
    print(f"checksums: {sums} (verified); the files of the release are listed in {UPLOAD_LIST}")


def finish_linux(app: Path, edition: bundle.Edition, out: Path, version: str, arguments) -> list[Path]:
    """The AppImage under its release name, the tarball, and (with ``--smoke``) a start of the AppImage itself."""
    built = out / linux.appimage_name(edition)
    final = out / linux.release_appimage_name(edition)
    built.replace(final)
    linux.make_tarball(app, out, edition, version)
    if arguments.smoke:
        env_flag = "--appimage-extract-and-run"  # no FUSE needed: a CI runner or a container may have none
        problems = run_smoke([str(final), env_flag], out, final.name)
        if problems:
            raise SystemExit("the AppImage does not start:\n  " + "\n  ".join(problems))
    return linux.release_files(out, edition, version)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", metavar="TREE", help="the source tree (default: this repository, the full edition)")
    parser.add_argument("--edition", choices=sorted(bundle.EDITIONS), help="assert the edition the tree must be")
    parser.add_argument("--out", type=Path, help="folder for the result (default out/installer/<edition>)")
    parser.add_argument("--smoke", action="store_true", help="start every app once")
    parser.add_argument("--pack", action="store_true", help="also run the installer's packer (Setup.exe, updates)")
    parser.add_argument(
        "--delta-from", metavar="REPO_URL", help="download the earlier release's packages first (deltas)"
    )
    parser.add_argument("--icon", type=Path, help="the setup's icon file (.ico)")
    parser.add_argument("--notes", type=Path, help="a Markdown file with the release notes for the packer")
    parser.add_argument(
        "--sign",
        metavar="COMMAND",
        help="signing command with {file}; signs the program's files and the migration .msi",
    )
    parser.add_argument("--sign-file", metavar="PATH", action="append", help="sign this file with --sign and stop")
    parser.add_argument("--skip-bundle", action="store_true", help="reuse the program folder of the last build")
    parser.add_argument("--no-texconv", action="store_true", help="leave texconv out of the program")
    parser.add_argument(
        "--no-native", action="store_true", help="leave the game look's module out (the editor look draws)"
    )
    arguments = parser.parse_args(argv)
    if arguments.sign_file:
        if not arguments.sign:
            raise SystemExit("--sign-file needs --sign COMMAND")
        wix.sign(arguments.sign, arguments.sign_file)
        return 0
    return build_toolbox(arguments)


if __name__ == "__main__":
    sys.exit(main())
