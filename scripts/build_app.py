"""Package the Anno Toolbox as a Windows program (a folder with two exes) and as an installer (.msi).

PyInstaller in folder mode over the Toolbox's window and command line (``suite/toolbox.py``), then WiX for the
.msi. Usage, requirements and the result: ``--help``, ``docs/installer.md`` and scripts/README.md.
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
import installer_wix as wix

try:  # the public edition's rules need the full source's layers; an exported tree is the public edition already
    import installer_edition as rules
except ImportError:
    rules = None

ROOT = bundle.ROOT
TOOLBOX_EXCLUDES = ("tkinter", "pytest", "IPython", "matplotlib")  # in the build's environment, never used
EPILOG = """\
The Toolbox: PyInstaller's folder mode over suite/toolbox.py (the `gui` extra: PySide6, moderngl), into
out/installer/<edition>/AnnoToolbox/: AnnoToolbox.exe (the window) and anno-toolbox.exe (the command line: rdm,
export, or any window with --app), texconv (MIT) in tools/, the schemas read beside the program, the licences. The
games are not part of it: the program finds them on the machine it runs on (suite.settings.detect).

--source TREE builds from another tree: the exported public edition (src/annotoolbox) gives the public build, which is
checked against the layers of the full source (installer_edition.py). --smoke starts every app once; --msi writes the installer with WiX
(tools/wix, tools/README.md) and SHA256SUMS beside it; --sign 'COMMAND {file}' signs the exes, our libraries and the .msi (off by default);
--sign-file PATH signs any built file the same way (a library built elsewhere).
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
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

import PySide6
_qt = os.path.dirname(PySide6.__file__)

a = Analysis(
    [{launcher!r}],
    pathex=[{src!r}],
    binaries=[(os.path.join(_qt, n), "PySide6") for n in os.listdir(_qt) if n.split(".")[0] in {baker!r}],
    hiddenimports=collect_submodules({package!r}) + collect_submodules("glcontext"),
    datas=collect_data_files({package!r}),
    excludes={excludes!r},
)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name={program!r}, console=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name={command!r}, console=True)
COLLECT(gui, cli, a.binaries, a.datas, name={program!r})
"""


# Qt's shader baker and its library, by file stem: PyInstaller collects PySide6's modules, not its tools
SHADER_BAKER = ("qsb", "Qt6ShaderTools")


def spec_text(launcher: Path, src: Path, edition: bundle.Edition) -> str:
    """The PyInstaller spec: one analysis, two exes (windowed and console) in one folder."""
    return SPEC.format(
        src=str(src), launcher=str(launcher), package=edition.package, excludes=list(TOOLBOX_EXCLUDES),
        program=bundle.PROGRAM, command=edition.command, baker=list(SHADER_BAKER),
    )  # fmt: skip


def pyinstaller_env(src: Path) -> dict[str, str]:
    """The build's environment: the tree's ``src`` on PYTHONPATH, where PyInstaller's helper processes import from."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(src), env.get("PYTHONPATH", "")]))
    return env


# The Toolbox


def add_beside_program(app: Path, tree: Path, edition: bundle.Edition, texconv: Path | None) -> None:
    """What the program reads next to its exes: the schemas (``paths.REPO_ROOT`` is this folder), texconv, notices."""
    for source, rel in bundle.runtime_files(tree):
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, app / rel)
    if texconv is not None:
        (app / "tools").mkdir(exist_ok=True)
        shutil.copy2(texconv, app / "tools" / texconv.name)
        shutil.copy2(ROOT / "tools" / "README.md", app / "tools" / "README.md")
    for name in bundle.LICENSE_FILES:
        if (tree / name).is_file():
            shutil.copy2(tree / name, app / name)
    (app / "THIRD-PARTY.txt").write_text(bundle.third_party_text(bundle.third_party()), encoding="utf-8", newline="\n")


def check_program(app: Path, tree: Path, edition: bundle.Edition, work: Path) -> list[str]:
    """Everything that must hold for the folder: the edition's rules, the package data, the schemas beside it."""
    package_dir = tree / "src" / edition.package
    problems = rules.folder_violations(edition, app) if rules else []
    toc = next(iter(sorted(work.rglob("PYZ-00.toc"))), None)
    if toc is not None and rules:
        problems += rules.pyz_violations(edition, toc.read_text(encoding="utf-8", errors="replace"))
    bundled = app / "_internal" / edition.package
    problems += [f"package data missing: {p}" for p in bundle.package_data_missing(package_dir, bundled)]
    for rel in bundle.RUNTIME_FILES[:1]:
        if not (app / rel).is_file():
            problems.append(f"missing beside the program: {rel}")
    return problems + baker_problems(app)


def baker_problems(app: Path) -> list[str]:
    """The 3D views bake their shaders with Qt's baker at run time: it must be in the folder and start there."""
    qsb = app / "_internal" / "PySide6" / "qsb.exe"
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
    cmd = [sys.executable, "-m", "PyInstaller", str(spec), "--noconfirm", "--clean"]
    cmd += ["--distpath", str(dist), "--workpath", str(work / "pyi")]
    subprocess.run(cmd, check=True, cwd=work, env=pyinstaller_env(tree / "src"))
    return dist / bundle.PROGRAM


def smoke_toolbox(app: Path, edition: bundle.Edition) -> list[str]:
    """The console exe: its version, an rdm help, and every app of the suite started once (own settings folder)."""
    exe = str(app / (edition.command + ".exe"))
    problems = []
    with tempfile.TemporaryDirectory(prefix="toolbox-smoke-", ignore_cleanup_errors=True) as settings:
        env = dict(os.environ, LOCALAPPDATA=settings)
        for argv in (["--version"], ["rdm", "--help"], ["--app", "islands", "--smoke"]):
            done = subprocess.run([exe, *argv], capture_output=True, text=True, timeout=600, env=env, cwd=app)
            print(f"{edition.command} {' '.join(argv)} -> exit {done.returncode}")
            print(done.stdout.strip())
            if done.returncode != 0:
                problems.append(f"{' '.join(argv)}: exit {done.returncode} {done.stderr.strip()[-400:]}")
    return problems


def make_msi(app: Path, tree: Path, edition: bundle.Edition, out: Path, scope: str, sign_cmd: str | None) -> Path:
    version = bundle.msi_version(bundle.package_version(tree / "src" / edition.package))
    tool = wix.find_wix()
    if tool is None:
        raise SystemExit(
            "WiX not found: dotnet tool install wix --version 6.0.2 --tool-path tools/wix (tools/README.md)"
        )
    files = bundle.folder_files(app)
    if sign_cmd:
        wix.sign(sign_cmd, [str(app / f) for f in wix.signable(files, edition.package)])
    work = out / "msi"
    work.mkdir(parents=True, exist_ok=True)
    source, licence = work / "toolbox.wxs", work / "licence.rtf"
    source.write_text(wix.wxs(edition, version, files, scope), encoding="utf-8", newline="\n")
    licence.write_text(wix.rtf(wix.licence_page(tree)), encoding="ascii", newline="\n")
    msi = out / f"{bundle.PROGRAM}-{version}{edition.suffix}.msi"
    wix.build_msi(tool, source, app, licence, msi, scope)
    if sign_cmd:
        wix.sign(sign_cmd, [str(msi)])
    return msi


def write_sums(out: Path, msi: Path, edition: bundle.Edition) -> Path:
    """SHA256SUMS next to the .msi: the .msi, both exes and our own libraries; read back and checked at once."""
    app = out / bundle.PROGRAM
    packed = [f"{bundle.PROGRAM}/{f}" for f in wix.signable(bundle.folder_files(app), edition.package)]
    sums = out / "SHA256SUMS"
    sums.write_text(bundle.sums_text(out, [msi.name, *packed]), encoding="utf-8", newline="\n")
    if bad := bundle.verify_sums(out, sums.read_text(encoding="utf-8")):
        raise SystemExit("SHA256SUMS does not verify:\n  " + "\n  ".join(bad))
    return sums


def build_toolbox(args) -> int:
    tree = Path(args.source).resolve() if args.source else ROOT
    edition = bundle.edition_of(tree, args.edition)
    package_dir = tree / "src" / edition.package
    if rules and (bad := rules.edition_violations(edition, package_dir)):
        raise SystemExit("the source tree is not the public edition:\n  " + "\n  ".join(bad))
    out = args.out or ROOT / "out" / "installer" / edition.key
    texconv = bundle.find_texconv(tree, dict(os.environ))
    if texconv is None and not args.no_texconv:
        raise SystemExit("missing texconv (tools/README.md); --no-texconv leaves it out")
    started = time.time()
    if not args.skip_bundle:
        app = run_pyinstaller(tree, edition, out / "build", out)
        add_beside_program(app, tree, edition, texconv)
    app = out / bundle.PROGRAM
    print(f"folder: {app} ({sum(p.stat().st_size for p in app.rglob('*') if p.is_file()) / 2**20:.0f} MB, "
          f"{time.time() - started:.0f} s)")  # fmt: skip
    problems = check_program(app, tree, edition, out / "build")
    if args.smoke:
        problems += smoke_toolbox(app, edition)
    if problems:
        raise SystemExit("the program folder has problems:\n  " + "\n  ".join(problems))
    if args.msi:
        started = time.time()
        msi = make_msi(app, tree, edition, out, args.scope, args.sign)
        print(f"installer: {msi} ({msi.stat().st_size / 2**20:.0f} MB, {time.time() - started:.0f} s)")
        print(f"checksums: {write_sums(out, msi, edition)} (verified)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", metavar="TREE", help="the source tree (default: this repository, the full edition)")
    ap.add_argument("--edition", choices=sorted(bundle.EDITIONS), help="assert the edition the tree must be")
    ap.add_argument("--out", type=Path, help="folder for the result (default out/installer/<edition>)")
    ap.add_argument("--smoke", action="store_true", help="start every app once")
    ap.add_argument("--msi", action="store_true", help="also write the .msi")
    ap.add_argument("--scope", choices=["perUser", "perMachine"], default="perUser", help="the .msi's install scope")
    ap.add_argument(
        "--sign", metavar="COMMAND", help="signing command with {file}; signs the exes, our libraries, the .msi"
    )
    ap.add_argument("--sign-file", metavar="PATH", action="append", help="sign this file with --sign and stop")
    ap.add_argument("--skip-bundle", action="store_true", help="reuse the program folder of the last build")
    ap.add_argument("--no-texconv", action="store_true", help="leave texconv out of the program")
    args = ap.parse_args(argv)
    if args.sign_file:
        if not args.sign:
            raise SystemExit("--sign-file needs --sign COMMAND")
        wix.sign(args.sign, args.sign_file)
        return 0
    return build_toolbox(args)


if __name__ == "__main__":
    sys.exit(main())
