"""What goes into the Toolbox installer: the editions, the version, the files beside the program, the notices.

The pure parts of ``build_app.py`` (the PyInstaller folder and the installer's packing) and ``installer_wix.py`` (the
migration package); every rule is in ``docs/installer.md``. Nothing here starts a tool, so the tests run it on small synthetic trees.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = "AnnoToolbox"  # the window's exe; the console exe is the command's name
RUNTIME_FILES = (  # read beside the program at run time (paths.REPO_ROOT / ...); the apps stop without the first
    "docs/formats/phoenix-schema.json",
    "docs/formats/phoenix-schema-1800.json",
    "docs/formats/enums-117.json",
)
LICENSE_FILES = ("LICENSE", "LICENSE-UBISOFT.md")
# What the bundle's libraries are, by distribution name; the version and licence line are read from the build's
# environment (``third_party``). Python, Qt, the boot loader and texconv are not distributions.
THIRD_PARTY = (
    "PySide6",
    "shiboken6",
    "moderngl",
    "glcontext",
    "numpy",
    "scipy",
    "Pillow",
    "etcpak",
    "miniaudio",
    "velopack",
)
THIRD_PARTY_FIXED = (
    ("Python", "PSF-2.0", "the interpreter and its standard library"),
    ("Qt", "LGPL-3.0", "the Qt libraries are separate files in _internal/PySide6 and can be replaced"),
    ("PyInstaller", "GPL-2.0-with-exception", "the boot loader; the exception allows bundled programs of any licence"),
    ("texconv", "MIT", "Microsoft DirectXTex, in tools/"),
    # the game look's compiled module (native/d3d12/THIRD-PARTY.md: the commits and the licence texts)
    ("NVRHI", "MIT", "NVIDIA's rendering layer, built into the render module _nativegpu.pyd"),
    ("DirectX-Headers", "MIT", "Microsoft's Direct3D 12 headers, built into _nativegpu.pyd"),
    ("nanobind", "BSD-3-Clause", "the module's Python binding, built into _nativegpu.pyd"),
)


@dataclass(frozen=True)
class Edition:
    key: str  # "full" (this repository: with its extension parts) or "public" (the exported tree)
    package: str  # the folder under src/
    product: str  # the product's name in Windows ("Apps and features", the Start menu)
    install_folder: str
    upgrade_code: str  # stable for ever: a newer version of this edition replaces the older one through it
    registry_key: str
    suffix: str  # in the migration package's file name

    @property
    def command(self) -> str:
        return "anno-toolbox"

    @property
    def pack_id(self) -> str:
        """The installer's id of the program: its folder under ``%LOCALAPPDATA%`` and its update feed's name."""
        return PROGRAM + ("Full" if self.key == "full" else "")


EDITIONS = {
    "full": Edition(
        "full", "", "Anno Toolbox (full edition)", "Anno Toolbox Full",
        "{529726BE-CA44-4717-B621-38733B4B08DA}", r"Software\AnnoToolboxFull", "-full",
    ),
    "public": Edition(
        "public", "annotoolbox", "Anno Toolbox", "Anno Toolbox",
        "{A922A368-B6B5-4050-88CD-C1F589B86AD7}", r"Software\AnnoToolbox", "",
    ),
}  # fmt: skip


def edition_of(tree: Path, wanted: str | None = None) -> Edition:
    """The edition a source tree is: its one package under ``src/`` says it (``annotoolbox`` is the public one);
    ``wanted`` must agree when given. The result carries the package's name."""
    found = sorted(d.name for d in (tree / "src").glob("*") if (d / "__init__.py").is_file())
    if len(found) != 1:
        raise SystemExit(f"{tree}: expected exactly one package under src/, found {found}")
    edition = EDITIONS["public" if found[0] == EDITIONS["public"].package else "full"]
    if wanted and wanted != edition.key:
        raise SystemExit(f"{tree} is the {edition.key} edition, not {wanted}")
    return dataclasses.replace(edition, package=found[0])


def package_version(package_dir: Path) -> str:
    """``__version__`` of the package's ``__init__.py``."""
    found = re.search(r'^__version__\s*=\s*"([^"]+)"', (package_dir / "__init__.py").read_text("utf-8"), re.M)
    if not found:
        raise SystemExit(f"{package_dir}: no __version__")
    return found.group(1)


def semver(version: str) -> str:
    """A version the installer's packer takes (SemVer 2): ``0.2.0rc1`` becomes ``0.2.0-rc1``, ``1.0`` becomes ``1.0.0``."""
    found = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?[-.]?([0-9A-Za-z.]*)", version.strip())
    if not found:
        raise SystemExit(f"cannot make a SemVer version of {version!r}")
    major, minor, patch, tail = found.groups()
    return f"{major}.{minor}.{patch or 0}" + (f"-{tail}" if tail else "")


def msi_version(version: str) -> str:
    """A version Windows Installer takes: up to three numbers (the upgrade check ignores a fourth), the rest dropped."""
    numbers = re.match(r"\d+(?:\.\d+){0,2}", version)
    if not numbers:
        raise SystemExit(f"cannot make an installer version of {version!r}")
    parts = [int(p) for p in numbers.group(0).split(".")]
    parts += [0] * (3 - len(parts))
    if parts[0] > 255 or parts[1] > 255 or parts[2] > 65535:
        raise SystemExit(f"{version}: too large for an installer version (255.255.65535)")
    return ".".join(map(str, parts))


# What sits beside the program


def runtime_files(tree: Path) -> list[tuple[Path, str]]:
    """``(source, path inside the program folder)`` of the files read beside the program, those the tree has."""
    return [(tree / rel, rel) for rel in RUNTIME_FILES if (tree / rel).is_file()]


def package_data_missing(package_dir: Path, bundled: Path) -> list[str]:
    """The package's non-Python files (language files, schemas, tables) that the bundled copy lacks."""
    out = []
    for file in sorted(package_dir.rglob("*")):
        rel = file.relative_to(package_dir)
        if file.is_file() and file.suffix not in (".py", ".pyc") and "__pycache__" not in rel.parts:
            if not (bundled / rel).is_file():
                out.append(rel.as_posix())
    return out


def find_texconv(tree: Path, environ: dict[str, str]) -> Path | None:
    """texconv: ``ANNO_TEXCONV``, the tree's ``tools/``, the checkout's, then PATH."""
    explicit = environ.get("ANNO_TEXCONV")
    options = [Path(explicit)] if explicit else []
    options += [tree / "tools" / "texconv.exe", ROOT / "tools" / "texconv.exe"]
    if on_path := shutil.which("texconv"):
        options.append(Path(on_path))
    return next((p for p in options if p.is_file()), None)


def third_party(names=THIRD_PARTY) -> list[tuple[str, str, str]]:
    """``(name, version, licence)`` of the libraries found in this Python environment."""
    out = []
    for name in names:
        try:
            meta = importlib.metadata.metadata(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        licence = meta.get("License-Expression") or (meta.get("License") or "").splitlines()[:1] or ["see its package"]
        out.append((name, meta["Version"], licence if isinstance(licence, str) else licence[0]))
    return out


def third_party_text(found: list[tuple[str, str, str]]) -> str:
    """THIRD-PARTY.txt: what the program folder carries besides this project's code."""
    lines = ["Third-party software in this folder", ""]
    lines += [f"{name} {version} - {licence}" for name, version, licence in found]
    lines += [f"{name} - {licence} ({note})" for name, licence, note in THIRD_PARTY_FIXED]
    lines += ["", "Each project's licence text is in its folder under _internal/ where it ships one."]
    return "\n".join(lines) + "\n"


def folder_files(folder: Path) -> list[str]:
    """Every file of the program folder, relative with "/", sorted (the installer's file list)."""
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


# Release verification


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sums_text(root: Path, rel_paths: list[str]) -> str:
    """SHA256SUMS in the format of ``sha256sum``: ``<hash> *<path>``, one line per file, paths relative to ``root``."""
    return "".join(f"{sha256_file(root / rel)} *{rel}\n" for rel in sorted(rel_paths))


def verify_sums(root: Path, text: str) -> list[str]:
    """What is wrong with ``root`` against a SHA256SUMS text: a line that is malformed, a file missing or changed."""
    problems = []
    for line in text.splitlines():
        found = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if not found:
            problems.append(f"malformed line: {line!r}")
        elif not (root / found.group(2)).is_file():
            problems.append(f"{found.group(2)}: missing")
        elif sha256_file(root / found.group(2)) != found.group(1):
            problems.append(f"{found.group(2)}: hash differs")
    return problems or ([] if text.strip() else ["empty SHA256SUMS"])
