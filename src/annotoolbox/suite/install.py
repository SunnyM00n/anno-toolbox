"""Where the installed program is, when the installer put it there (``docs/installer.md``, "The install").

On Windows the installed program runs from ``<root>\\current\\``; the root holds the updater (``Update.exe``) and a
launcher with the program's own name that starts whatever version is current, so a shortcut to the launcher survives
every update. On Linux the installed program is one AppImage file (its path is in ``APPIMAGE``; the updater replaces the
file), and that file is the launcher. Everything here answers from the running exe's place and starts nothing but the
Windows uninstaller.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..infra.tools import NO_WINDOW

UPDATER = "Update.exe"
CURRENT = "current"
APPIMAGE_UPDATER = "UpdateNix"  # the packer's updater inside an AppImage, beside the program
ICON = "anno-toolbox.png"  # beside the program on Linux (``scripts/installer_linux.py``): the .desktop entries' icon


def root(exe: str | None = None) -> Path | None:
    """The install root when ``exe`` (default: this program) runs from its ``current`` folder, else None (a Python
    environment, a source run, a program folder that was copied by hand, any Linux program)."""
    path = Path(exe or sys.executable)
    if getattr(sys, "frozen", False) is False and exe is None:
        return None
    return path.parent.parent if path.parent.name.lower() == CURRENT else None


def appimage(exe: str | None = None, environ: dict[str, str] | None = None) -> Path | None:
    """The AppImage file this program runs from (Linux), else None: the packaged program, started from a file of the
    packer's making (the updater sits beside the program inside the image), whose path the AppImage runtime puts into
    ``APPIMAGE``. A program folder started by hand (the tarball) is not one."""
    if sys.platform == "win32" or (getattr(sys, "frozen", False) is False and exe is None):
        return None
    path = (environ if environ is not None else os.environ).get("APPIMAGE", "")
    beside = Path(exe or sys.executable).parent / APPIMAGE_UPDATER
    return Path(path) if path and Path(path).is_file() and beside.is_file() else None


def installed(exe: str | None = None) -> bool:
    """Whether this program was put there by the installer: its root has the updater, or it runs from an AppImage."""
    where = root(exe)
    return (where is not None and (where / UPDATER).is_file()) or appimage(exe) is not None


def launcher(exe: str | None = None) -> Path | None:
    """The stable launcher (the root's program beside the updater; the AppImage file on Linux), or None when this is
    not an installed program. The packer names the root's launcher after the product (``Anno Toolbox.exe``), not
    after the program in ``current`` (``AnnoToolbox.exe``): a file of the program's name is taken first, else the
    root's one other ``.exe``."""
    if (image := appimage(exe)) is not None:
        return image
    where = root(exe)
    if where is None:
        return None
    same = where / Path(exe or sys.executable).name
    if same.is_file():
        return same
    others = [p for p in sorted(where.glob("*.exe")) if p.name.lower() != UPDATER.lower()]
    return others[0] if len(others) == 1 else None


def icon_file(exe: str | None = None) -> Path | None:
    """The icon PNG that ships beside the program (Linux), for the .desktop entries."""
    path = Path(exe or sys.executable).parent / ICON
    return path if path.is_file() else None


def uninstall_argv(where: Path) -> list[str]:
    """The updater's uninstall entry point; the program has quit by the time it removes the files."""
    return [str(where / UPDATER), "uninstall"]


def start_uninstall(where: Path, popen=subprocess.Popen) -> None:
    popen(uninstall_argv(where), close_fds=True, cwd=str(where), **NO_WINDOW)


def remove_appimage(image: Path) -> None:
    """Delete the AppImage file (Linux has no uninstaller): a running image stays usable until the program ends."""
    image.unlink()
