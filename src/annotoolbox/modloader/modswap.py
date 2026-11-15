"""Putting a mod folder into a mods folder, and taking one out, without losing what was there.

``check_folder_name`` says whether a name is one safe folder name inside the mods folder; ``place_folder`` is the
entry: it copies (or moves) a folder in through a staging folder, and ``swap_in`` puts the staged folder in place (the
old one is renamed aside first, put back on a failure, and goes to the recycle bin only afterwards); ``recycle`` is the
bin, and removes a junction or symlink as a link, never its target. ``check_holds`` says whether a folder may be
replaced by a mod, ``staged`` writes a mod beside its folder and swaps it in. Stdlib only. ``docs/mod-manager.md``
"Install and remove".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

STAGING = ".modmgr-"  # the prefix of the folders in progress: a dot, so the loader skips them
REMOVED = ".removed"  # where a mod goes when the system has no recycle bin
ASIDE = "old"  # the old copy's name inside the staging folder
RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"{d}{i}" for d in ("com", "lpt") for i in range(1, 10))})
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# SHFileOperationW flags: FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI and FOF_WANTNUKEWARNING, which
# keeps the shell asking when the item cannot go to a bin (a network share) and would be destroyed for good
FOF_RECYCLE = 0x4 | 0x10 | 0x40 | 0x400 | 0x4000

Trash = Callable[[Path], object]


class InstallError(ValueError):
    """An install or removal that cannot be done; the message says why."""


def check_folder_name(name: str) -> str:
    """``name`` when it is one folder name the loader would read (no path, no device name, not parked), else an
    ``InstallError`` saying why."""
    bad = (
        not name
        or name in (".", "..")
        or BAD_CHARS.search(name)
        or name.endswith((".", " "))
        or name.split(".")[0].lower() in RESERVED
        or name.startswith(("-", "."))
    )
    if bad:
        raise InstallError(f"{name!r} is not a folder name the mods folder can take: not installed")
    return name


def is_link(path: Path) -> bool:
    """A symlink or (on Windows) a junction: a folder that stands for another one."""
    return os.path.islink(path) or os.path.isjunction(path)


def _recycle_windows(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    class FileOp(ctypes.Structure):
        _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("func", wintypes.UINT),
            ("source", wintypes.LPCWSTR),
            ("dest", wintypes.LPCWSTR),
            ("flags", ctypes.c_ushort),
            ("aborted", wintypes.BOOL),
            ("mappings", ctypes.c_void_p),
            ("title", wintypes.LPCWSTR),
        ]

    delete = 3
    op = FileOp(None, delete, f"{os.path.abspath(path)}\0", None, FOF_RECYCLE, False, None, None)
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))  # type: ignore[attr-defined]
    if code or op.aborted:
        raise OSError(f"the recycle bin refused {path.name} (code {code})")


def recycle(path: Path) -> str:
    """Move a file or folder to the recycle bin; where there is none (not Windows), into a ``.removed`` folder beside
    it, which the loader skips. A junction or symlink is only unlinked: what it points at is not ours. Returns where
    it went."""
    path = Path(os.path.abspath(path))  # never ``resolve``: that would follow a link to its target
    if is_link(path):
        if os.path.islink(path):
            os.unlink(path)
        else:
            os.rmdir(path)
        return "removed as a link; its target is untouched"
    if os.name == "nt":
        _recycle_windows(path)
        return "the recycle bin"
    keep = path.parent / REMOVED / f"{path.name}-{datetime.now():%Y%m%d-%H%M%S}"
    keep.parent.mkdir(exist_ok=True)
    shutil.move(str(path), str(keep))
    return str(keep)


def swap_in(staged: Path, dest: Path, trash: Trash = recycle) -> list[str]:
    """Make ``staged`` (a folder in a dot-named staging folder on the same volume) the folder ``dest``. An existing
    ``dest`` is renamed aside first and renamed back when the new one cannot be placed; only once the new copy is
    there does the old one go to ``trash``. A bin that refuses leaves it parked beside as ``.modmgr-old-...``; the
    returned notes say so."""
    staged, dest = Path(staged), Path(dest)
    aside = staged.parent / ASIDE
    had_old = os.path.lexists(dest)
    try:
        if had_old:
            os.replace(dest, aside)
        try:
            os.replace(staged, dest)
        except OSError:
            if had_old:
                os.replace(aside, dest)
            raise
    except OSError as exc:
        raise InstallError(f"{dest.name} could not be installed: {exc.strerror or exc}") from exc
    if not had_old:
        return []
    try:
        trash(aside)
        return []
    except OSError as exc:
        parked = dest.parent / f"{STAGING}old-{dest.name}-{datetime.now():%Y%m%d-%H%M%S}"
        os.replace(aside, parked)
        return [f"the old copy could not go to the recycle bin ({exc.strerror or exc}): it is kept as {parked.name}"]


def place_folder(source: Path, dest: Path, trash: Trash = recycle, *, move: bool = False, ignore=None) -> list[str]:
    """Copy (``move``: move) the folder ``source`` to ``dest`` through a staging folder beside it, so a copy that fails
    half way leaves ``dest`` as it was; then ``swap_in``. ``ignore``: as ``shutil.copytree`` takes it. Returns its
    notes."""
    source, dest = Path(source), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=STAGING, dir=dest.parent) as stage:
        staged = Path(stage) / dest.name
        try:
            if move:
                shutil.move(str(source), str(staged))
            else:
                shutil.copytree(source, staged, ignore=ignore)
        except OSError as exc:
            raise InstallError(f"{dest.name} could not be copied: {exc.strerror or exc}") from exc
        return swap_in(staged, dest, trash)


def check_holds(folder: Path, mod_id: str) -> None:
    """An ``InstallError`` unless the mod ``mod_id`` may replace ``folder`` whole: it is not there, empty, or a mod
    folder whose ``modinfo.json`` names that id (a game's user folder, a save folder or another mod never is)."""
    folder = Path(folder)
    if not os.path.lexists(folder) or (folder.is_dir() and not any(folder.iterdir())):
        return
    info = folder / "modinfo.json"
    try:
        found = json.loads(info.read_text(encoding="utf-8-sig")).get("ModID") if info.is_file() else None
    except (OSError, ValueError, AttributeError):  # AttributeError: not a JSON object
        found = None
    if found != mod_id:
        held = f"the mod {found!r}" if found else "no mod of this id"
        raise InstallError(f"{folder} holds {held}: it is not replaced by {mod_id!r}")


@contextmanager
def staged(dest: Path, mod_id: str, trash: Trash = recycle, log: Callable[[str], None] | None = None):
    """Write a mod into a staging folder (the ``with`` value) that becomes ``dest`` when the block ends without an
    error; a block that fails leaves ``dest`` as it was. ``dest`` must be an absolute path ending in the checked
    ``mod_id`` and hold that mod or nothing (``check_holds``); the old copy goes to ``trash``."""
    dest = Path(dest)
    if not dest.is_absolute() or dest.name != check_folder_name(mod_id):
        raise InstallError(f"refusing to write {dest}: not an absolute path ending in the mod id {mod_id!r}")
    check_holds(dest, mod_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=STAGING, dir=dest.parent) as stage:
        folder = Path(stage) / dest.name
        folder.mkdir()
        yield folder
        for note in swap_in(folder, dest, trash):
            (log or (lambda _n: None))(note)
