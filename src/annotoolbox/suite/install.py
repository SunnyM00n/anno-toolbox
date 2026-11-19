"""Where the installed program is, when the installer put it there (``docs/installer.md``, "The install").

The installed program runs from ``<root>\\current\\``; the root holds the updater (``Update.exe``) and a launcher with the
program's own name that starts whatever version is current, so a shortcut to the launcher survives every update.
Everything here answers from the running exe's place and starts nothing but the uninstaller.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..infra.tools import NO_WINDOW

UPDATER = "Update.exe"
CURRENT = "current"


def root(exe: str | None = None) -> Path | None:
    """The install root when ``exe`` (default: this program) runs from its ``current`` folder, else None (a Python
    environment, a source run, a program folder that was copied by hand)."""
    path = Path(exe or sys.executable)
    if getattr(sys, "frozen", False) is False and exe is None:
        return None
    return path.parent.parent if path.parent.name.lower() == CURRENT else None


def installed(exe: str | None = None) -> bool:
    """Whether this program was put there by the installer: its root has the updater."""
    where = root(exe)
    return where is not None and (where / UPDATER).is_file()


def launcher(exe: str | None = None) -> Path | None:
    """The stable launcher (the root's program beside the updater), or None when this is not an installed program.
    The packer names the root's launcher after the product (``Anno Toolbox.exe``), not after the program in
    ``current`` (``AnnoToolbox.exe``): a file of the program's name is taken first, else the root's one other
    ``.exe``."""
    where = root(exe)
    if where is None:
        return None
    same = where / Path(exe or sys.executable).name
    if same.is_file():
        return same
    others = [p for p in sorted(where.glob("*.exe")) if p.name.lower() != UPDATER.lower()]
    return others[0] if len(others) == 1 else None


def uninstall_argv(where: Path) -> list[str]:
    """The updater's uninstall entry point; the program has quit by the time it removes the files."""
    return [str(where / UPDATER), "uninstall"]


def start_uninstall(where: Path, popen=subprocess.Popen) -> None:
    popen(uninstall_argv(where), close_fds=True, cwd=str(where), **NO_WINDOW)
