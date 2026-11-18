"""The program's own Start menu entry and desktop shortcut: made, removed and read back by the program itself.

The installer makes none (the setup page asks, ``docs/installer.md`` "Shortcuts"). A shortcut points at the install
root's launcher, never at the versioned folder, so it survives updates. The Windows shell-link interface is called through
``ctypes`` (no extra library); a test passes its own folders, so no real Start menu or desktop is touched.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

TITLE = "Anno Toolbox"  # the shortcut's name


@dataclass(frozen=True)
class Places:
    start_menu: Path  # the user's Start menu, Programs
    desktop: Path


def _guid(text: str):
    """A Windows GUID structure (an interface, class or folder id) from its text."""
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    raw = uuid.UUID(text)
    return GUID(raw.time_low, raw.time_mid, raw.time_hi_version, (ctypes.c_ubyte * 8)(*raw.bytes[8:]))


def known_folder(guid: str) -> Path | None:
    """A Windows known folder by its id (the desktop and the Start menu can be moved); None off Windows."""
    if sys.platform != "win32":
        return None
    import ctypes

    ident = _guid(guid)
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(ident), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def places() -> Places | None:
    programs = known_folder("A77F5D77-2E2B-44C3-A6A2-ABA601054A51")  # FOLDERID_Programs (the user's)
    desktop = known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")  # FOLDERID_Desktop
    return Places(programs, desktop) if programs and desktop else None


def link_path(folder: Path, title: str = TITLE) -> Path:
    return folder / f"{title}.lnk"


def _method(obj, index: int, restype, *argtypes):
    import ctypes

    table = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])


def _shell_link():
    """A shell link object and its file interface (IShellLinkW, IPersistFile); the caller releases both."""
    import ctypes

    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)  # a second call from a thread that has it is fine (S_FALSE)
    link = ctypes.c_void_p()
    clsid, iid = _guid("00021401-0000-0000-C000-000000000046"), _guid("000214F9-0000-0000-C000-000000000046")
    if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(link)) != 0:
        raise OSError("the shell could not make a link object")
    persist = ctypes.c_void_p()
    query = _method(link, 0, ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p)
    try:
        query(link, ctypes.byref(_guid("0000010B-0000-0000-C000-000000000046")), ctypes.byref(persist))
    except OSError:
        _release(link)  # no file interface: the link object must not stay behind
        raise
    return link, persist


def _release(*objects) -> None:
    import ctypes

    for obj in objects:
        if obj:
            _method(obj, 2, ctypes.c_ulong)(obj)


def create_link(path: Path, target: Path, description: str = "", arguments: str = "") -> None:
    """Write the shortcut ``path`` to ``target`` (the folder is made); ``OSError`` when the shell refuses."""
    if sys.platform != "win32":
        raise OSError("shortcuts are made on Windows only")
    import ctypes

    path.parent.mkdir(parents=True, exist_ok=True)
    link, persist = _shell_link()
    try:
        w = ctypes.c_wchar_p
        _method(link, 20, ctypes.HRESULT, w)(link, str(target))  # SetPath
        _method(link, 9, ctypes.HRESULT, w)(link, str(target.parent))  # SetWorkingDirectory
        _method(link, 17, ctypes.HRESULT, w, ctypes.c_int)(link, str(target), 0)  # SetIconLocation
        if description:
            _method(link, 7, ctypes.HRESULT, w)(link, description)  # SetDescription
        if arguments:
            _method(link, 11, ctypes.HRESULT, w)(link, arguments)  # SetArguments
        _method(persist, 6, ctypes.HRESULT, w, ctypes.c_int)(persist, str(path), 1)  # IPersistFile.Save
    except OSError as exc:
        raise OSError(f"could not write {path}: {exc}") from exc
    finally:
        _release(persist, link)


def link_target(path: Path) -> Path | None:
    """Where the shortcut ``path`` points; None when it is not a readable shortcut."""
    if sys.platform != "win32" or not path.is_file():
        return None
    import ctypes

    link, persist = _shell_link()
    try:
        _method(persist, 5, ctypes.HRESULT, ctypes.c_wchar_p, ctypes.c_ulong)(persist, str(path), 0)  # Load
        buf = ctypes.create_unicode_buffer(520)
        _method(link, 3, ctypes.HRESULT, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong)(
            link, buf, 520, None, 0
        )  # GetPath
        return Path(buf.value) if buf.value else None
    except OSError:
        return None
    finally:
        _release(persist, link)


def sync(
    start_menu: bool,
    desktop: bool,
    target: Path,
    where: Places,
    make: Callable[[Path, Path, str], None] = lambda p, t, d: create_link(p, t, d),
) -> list[str]:
    """Make the shortcuts that are chosen, remove the ones that are not (only ours: named ``TITLE``). Returns what
    went wrong, one text per shortcut that could not be made or removed."""
    problems = []
    for wanted, folder in ((start_menu, where.start_menu), (desktop, where.desktop)):
        path = link_path(folder)
        try:
            if wanted:
                make(path, target, TITLE)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:  # a shortcut in use or write-protected: said, and the other one is still done
            problems.append(str(exc))
    return problems


def remove_ours(where: Places, root: Path, read: Callable[[Path], Path | None] = link_target) -> list[Path]:
    """Delete the shortcuts named ``TITLE`` in ``where`` that point into the install ``root``; the others (the old
    installer's, a person's own) stay. Returns what was removed."""
    removed = []
    for folder in (where.start_menu, where.desktop):
        path = link_path(folder)
        target = read(path) if path.is_file() else None
        if target is not None and _inside(target, root):
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def _inside(path: Path, root: Path) -> bool:
    return os.path.normcase(str(path)).startswith(os.path.normcase(str(root)).rstrip("\\/") + os.sep)


def present(where: Places) -> tuple[bool, bool]:
    """(Start menu entry, desktop shortcut) as they are on disk."""
    return link_path(where.start_menu).is_file(), link_path(where.desktop).is_file()
