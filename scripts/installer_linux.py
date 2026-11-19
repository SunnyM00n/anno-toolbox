"""The Linux parts of the Toolbox build: the packer's command, the release's file names, the tarball, the icon.

``build_app.py`` runs PyInstaller the same way on every system; on Linux the installer's packer (``vpk``) writes an
AppImage and a feed instead of ``Setup.exe``, and the build adds a plain tarball. What the files are and how the program
installs and updates itself from an AppImage: ``docs/installer.md``, "Linux". Nothing here starts a tool except the
tarball's own packing, so the tests run it on small synthetic folders.
"""

from __future__ import annotations

import gzip
import stat
import tarfile
from pathlib import Path

import installer_bundle as bundle

RUNTIME = "linux-x64"
ARCH = "x86_64"
FEED_FILES = ("releases.linux.json", "RELEASES-linux", "assets.linux.json")  # the update feed's index files
SUMS = (
    "SHA256SUMS-linux"  # the Windows job's release has SHA256SUMS; two jobs cannot write one file (docs/installer.md)
)
ICON = "anno-toolbox.png"  # beside the program: the .desktop entries' icon, copied out of the AppImage by the program
CATEGORIES = "Utility;Development"  # freedesktop.org's menu categories
DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Name=Anno Toolbox
Comment=Editors and tools for the Anno games' data
Exec=@DIR@/AnnoToolbox
Icon=@DIR@/anno-toolbox.png
Terminal=false
Categories=Utility;Development;
"""
INSTALL_SCRIPT = """#!/bin/sh
# Adds this folder's Anno Toolbox to the applications menu of the current user; remove it with: rm "$target"
set -eu
here=$(cd "$(dirname "$0")" && pwd)
target="${XDG_DATA_HOME:-$HOME/.local/share}/applications/anno-toolbox.desktop"
mkdir -p "$(dirname "$target")"
sed "s#@DIR@#$here#g" "$here/anno-toolbox.desktop.in" > "$target"
echo "written: $target"
"""


def appimage_name(edition: bundle.Edition) -> str:
    """The file the packer writes: ``<id>.AppImage``."""
    return f"{edition.pack_id}.AppImage"


def release_appimage_name(edition: bundle.Edition) -> str:
    """The AppImage as a release carries it: the architecture says what it is (the Windows files say ``win``)."""
    return f"{edition.pack_id}-linux-{ARCH}.AppImage"


def tarball_name(edition: bundle.Edition, version: str) -> str:
    return f"{edition.pack_id}-{bundle.semver(version)}-linux-{ARCH}.tar.gz"


def pack_command(
    vpk: str, edition: bundle.Edition, version: str, app: Path, out: Path,
    icon: Path | None = None, notes: Path | None = None,
) -> list[str]:  # fmt: skip
    """``vpk pack`` on Linux: an AppImage of the program folder with deltas against the earlier packages in ``out``. The
    packer has no shortcut option here (the program writes its own .desktop entry, ``suite/shortcuts.py``)."""
    cmd = [
        vpk, "pack", "--packId", edition.pack_id, "--packVersion", bundle.semver(version), "--packDir", str(app),
        "--mainExe", bundle.PROGRAM, "--packTitle", edition.product, "--packAuthors", "Anno Toolbox",
        "--outputDir", str(out), "--runtime", RUNTIME, "--categories", CATEGORIES, "--delta", "BestSpeed",
    ]  # fmt: skip
    if icon:
        cmd += ["--icon", str(icon)]
    if notes:
        cmd += ["--releaseNotes", str(notes)]
    return cmd


def release_files(out: Path, edition: bundle.Edition, version: str) -> list[Path]:
    """This release's files in ``out`` once the AppImage is renamed: the AppImage, the full and delta packages of
    ``version`` (the Linux channel's ``-linux-`` in their names), the feed's index files, the tarball. Those that exist."""
    sem, pid = bundle.semver(version), edition.pack_id
    names = [release_appimage_name(edition), f"{pid}-{sem}-linux-full.nupkg", f"{pid}-{sem}-linux-delta.nupkg"]
    names += [*FEED_FILES, tarball_name(edition, version)]
    return [out / n for n in names if (out / n).is_file()]


def write_icon(path: Path, size: int = 256) -> Path:
    """The program's icon as a PNG, drawn with Pillow (the project has no icon file): the toolbar's orange and water
    blue on a dark tile, a roof over a wave."""
    from PIL import Image, ImageDraw

    scale = 4  # drawn large, scaled down: smooth edges
    big = size * scale
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    unit = big / 64
    draw.rounded_rectangle((2 * unit, 2 * unit, 62 * unit, 62 * unit), radius=10 * unit, fill="#26292e")
    draw.polygon([(32 * unit, 12 * unit), (54 * unit, 34 * unit), (10 * unit, 34 * unit)], fill="#f28c28")
    draw.rectangle((16 * unit, 34 * unit, 48 * unit, 44 * unit), fill="#d6d6d6")
    for x in range(10, 54, 12):
        draw.arc((x * unit, 44 * unit, (x + 12) * unit, 56 * unit), 0, 180, fill="#5aa9e6", width=int(3 * unit))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.resize((size, size), Image.LANCZOS).save(path)
    return path


def add_files(app: Path) -> None:
    """Beside the program in the folder (so in the AppImage and the tarball): the icon, the .desktop template and the
    script that writes it for the folder it sits in."""
    write_icon(app / ICON)
    (app / "anno-toolbox.desktop.in").write_text(DESKTOP_TEMPLATE, encoding="utf-8", newline="\n")
    script = app / "install-desktop-entry.sh"
    script.write_text(INSTALL_SCRIPT, encoding="utf-8", newline="\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def make_tarball(app: Path, out: Path, edition: bundle.Edition, version: str) -> Path:
    """The program folder as ``<id>-<version>-linux-x86_64.tar.gz`` with ``AnnoToolbox/`` as its one top folder; the
    file modes are kept (the program, ``qsb`` and the script must stay executable)."""
    target = out / tarball_name(edition, version)
    with target.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=6) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
            tar.add(app, arcname=bundle.PROGRAM, recursive=False)
            for path in sorted(app.rglob("*")):
                tar.add(path, arcname=f"{bundle.PROGRAM}/{path.relative_to(app).as_posix()}", recursive=False)
    return target


def write_sums(out: Path, files: list[Path]) -> Path:
    """``SHA256SUMS-linux`` for this release's files, read back and checked at once (as ``build_app.write_sums``)."""
    sums = out / SUMS
    sums.write_text(bundle.sums_text(out, [f.name for f in files]), encoding="utf-8", newline="\n")
    if bad := bundle.verify_sums(out, sums.read_text(encoding="utf-8")):
        raise SystemExit(f"{SUMS} does not verify:\n  " + "\n  ".join(bad))
    return sums
