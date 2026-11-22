"""Build the game look's Direct3D 12 module (``native/d3d12/``): NVRHI and the DirectX headers at their pinned commits,
nanobind from the build's Python, MSVC from Visual Studio's build tools, into ``render/_nativegpu.pyd`` of the package.

Usage, requirements and the result: ``--help``, ``native/d3d12/README.md`` and scripts/README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native" / "d3d12"
BUILD = ROOT / "build" / "native"
MODULE = "_nativegpu"
VSWHERE = (
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    / "Microsoft Visual Studio/Installer/vswhere.exe"
)
EPILOG = """\
The pinned sources (native/d3d12/deps.json) are fetched by commit into build/native/deps/ once; the build goes to
build/native/<python>/ and the module is copied into the package's render folder, where the Toolbox and
scripts/build_app.py find it. Needs Windows, Visual Studio's C++ build tools (vswhere finds them; inside a developer
prompt the one set up is used), CMake and Ninja (both come with the build tools), git, and nanobind in the Python that
runs this (pip install nanobind==<deps.json's version>). --test also builds and runs the layer's own test program.
"""


def deps() -> dict:
    return json.loads((NATIVE / "deps.json").read_text(encoding="utf-8"))


def fetch(name: str, spec: dict, into: Path) -> Path:
    """A source at its pinned commit (a shallow fetch of that commit only); kept for later builds."""
    target_dir = into / f"{name}-{spec['commit'][:12]}"
    if (target_dir / ".fetched").is_file():
        return target_dir
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)
    git = ["git", "-C", str(target_dir)]
    for command in (
        ["init", "-q"],
        ["fetch", "-q", "--depth", "1", spec["url"], spec["commit"]],
        ["checkout", "-q", "FETCH_HEAD"],
    ):
        subprocess.run(git + command, check=True)
    got = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    if got != spec["commit"]:
        raise SystemExit(f"{name}: fetched {got}, pinned {spec['commit']}")
    (target_dir / ".fetched").write_text(spec["commit"] + "\n", encoding="utf-8", newline="\n")
    return target_dir


def msvc_env() -> dict[str, str]:
    """The environment of Visual Studio's x64 developer prompt (the current one when it is set up already)."""
    if os.environ.get("VCToolsInstallDir"):
        return dict(os.environ)
    if not VSWHERE.is_file():
        raise SystemExit("no Visual Studio found (vswhere.exe is missing): install the C++ build tools")
    found = subprocess.run([str(VSWHERE), "-latest", "-products", "*", "-requires",
                            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                           check=True, capture_output=True, text=True).stdout.strip()  # fmt: skip
    vcvars = Path(found) / "VC/Auxiliary/Build/vcvars64.bat"
    if not vcvars.is_file():
        raise SystemExit(f"no vcvars64.bat under {found}")
    out = subprocess.run(f'cmd /s /c ""{vcvars}" >nul && set"', check=True, capture_output=True, text=True, shell=True)
    env = {}
    for line in out.stdout.splitlines():
        key, equals_sign, value = line.partition("=")
        if equals_sign:
            env[key] = value
    return env


def package_render() -> Path:
    """The package's render folder (``src/<package>/render``): the full source's or the public edition's."""
    found = sorted(path.parent for path in (ROOT / "src").glob("*/render/nativegpu.py"))
    if not found:
        raise SystemExit("no src/<package>/render/nativegpu.py: is this the repository's root?")
    return found[0]


def build(is_test: bool = False, is_fresh: bool = False, destination: Path | None = None) -> Path:
    """Fetch the sources, configure and compile the native module, and put it at ``destination`` (its path is returned)."""
    if sys.platform != "win32":
        raise SystemExit("the module is Windows only (Direct3D 12)")
    spec = deps()
    sources = BUILD / "deps"
    nvrhi = fetch("nvrhi", spec["sources"]["nvrhi"], sources)
    headers = fetch("directx-headers", spec["sources"]["directx-headers"], sources)
    out = BUILD / f"cp{sys.version_info.major}{sys.version_info.minor}"
    if is_fresh and out.exists():
        shutil.rmtree(out)
    env = msvc_env()
    configure = ["cmake", "-S", str(NATIVE), "-B", str(out), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                 f"-DNVRHI_DIR={nvrhi.as_posix()}", f"-DDIRECTX_HEADERS_DIR={headers.as_posix()}",
                 f"-DPython_EXECUTABLE={Path(sys.executable).as_posix()}", f"-DMODULE_VERSION={spec['version']}",
                 f"-DNVRHI_REVISION={spec['sources']['nvrhi']['commit']}"]  # fmt: skip
    subprocess.run(configure, check=True, env=env)
    targets = [MODULE] + (["nativegpu_test"] if is_test else [])
    subprocess.run(["cmake", "--build", str(out), "--target", *targets], check=True, env=env)
    if is_test:
        subprocess.run([str(out / "nativegpu_test.exe")], check=True)
    built = next(out.glob(f"{MODULE}*.pyd"))
    target = (destination or package_render()) / f"{MODULE}.pyd"
    shutil.copy2(built, target)
    print(f"{target} ({target.stat().st_size // 1024} KB)")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog=EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    parser.add_argument("--test", action="store_true", help="also build and run the layer's test program")
    parser.add_argument("--fresh", action="store_true", help="configure from scratch")
    parser.add_argument("--dest", type=Path, help="where the module goes (default: the package's render folder)")
    args = parser.parse_args(argv)
    build(args.test, args.fresh, args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
