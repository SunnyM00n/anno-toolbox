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
    """The stable launcher (the root's copy of the program's name), or None when this is not an installed program."""
    where = root(exe)
    name = Path(exe or sys.executable).name
    return where / name if where is not None and (where / name).is_file() else None


def uninstall_argv(where: Path) -> list[str]:
    """The updater's uninstall entry point; the program has quit by the time it removes the files."""
    return [str(where / UPDATER), "uninstall"]


def start_uninstall(where: Path, popen=subprocess.Popen) -> None:
    popen(uninstall_argv(where), close_fds=True, cwd=str(where), **NO_WINDOW)
