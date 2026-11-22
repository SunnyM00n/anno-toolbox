"""Writing a built mod's files into its folder without holding them in memory, and only what changed
(docs/mod-import.md, section 3.2). Stdlib only.

A part gives each file as bytes, or as ``CFileRef`` (a path to take the file from: hard-linked where the disk allows,
else copied). ``sync(out, files)`` makes the folder hold exactly ``files``: a file that is already the same one
(the same inode, or the same size and time) stays, a stale one goes, the rest is linked, copied or written. A file is
never written in place - the old one is removed first - so a hard link never carries an edit back to its source.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import sys
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..modloader.mods import walk_sizes

CHUNK = 2000  # files handled between two chances to stop
REPORT = 20000  # files between two progress lines
WORKERS = 8
LINK_GIVE_UP = 3  # failed links in a row after which the rest is copied
LONG_PATH = "\\\\?\\"  # the prefix that lifts Windows' 260 character limit


class CFileRef(str):
    """A file to take a part's content from, by path: a ``str`` so that half a million of them stay small."""


def read(data: bytes | CFileRef | Path) -> bytes:
    return Path(data).read_bytes() if isinstance(data, CFileRef | Path) else data


def read_all(files: dict[str, bytes | CFileRef | Path]) -> dict[str, bytes]:
    """A part's files with every content read (only for a small set: a test, a delivery)."""
    return {path: read(data) for path, data in files.items()}


def same(first: bytes | CFileRef | Path, second: bytes | CFileRef | Path) -> bool:
    """Whether two files' content is equal (bytes, or files read in chunks)."""
    if isinstance(first, bytes) and isinstance(second, bytes):
        return first == second
    if isinstance(first, bytes) or isinstance(second, bytes):
        return read(first) == read(second)
    try:
        if os.path.samestat(os.stat(first), os.stat(second)):
            return True
    except OSError:
        return False
    return filecmp.cmp(first, second, shallow=False)


def _current(source: str, destination: str) -> bool:
    """Whether ``destination`` is already ``source`` for this purpose: one inode, or the same size and modification
    time (a copy made with ``copy2`` keeps both)."""
    try:
        source_stat, destination_stat = os.stat(source), os.stat(destination)
    except OSError:
        return False
    return os.path.samestat(source_stat, destination_stat) or (
        source_stat.st_size == destination_stat.st_size and source_stat.st_mtime_ns == destination_stat.st_mtime_ns
    )


def remove(path: str | Path) -> None:
    """Delete one name of a file. A read-only file on Windows goes with its attribute left as it is: the attribute
    is the file's, so clearing it for a hard link would clear it on the mod's own file too."""
    try:
        os.unlink(path)
    except PermissionError:
        if sys.platform != "win32" or not _delete_read_only(os.path.abspath(path)):
            raise


def _delete_read_only(path: str) -> bool:
    """Windows: delete ``path`` although it is read-only (a delete that ignores the attribute, Windows 10 1809 on)."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    dword = wintypes.DWORD
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, dword, dword, wintypes.LPVOID, dword, dword, wintypes.HANDLE]
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    long_path = path if path.startswith(LONG_PATH[:2]) else LONG_PATH + path
    # DELETE access, every share mode, OPEN_EXISTING, the link itself (no reparse) with backup semantics
    handle = kernel.CreateFileW(long_path, 0x00010000, 7, None, 3, 0x02200000, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return False
    try:
        # FileDispositionInfoEx: DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE
        flags = wintypes.DWORD(0x1 | 0x2 | 0x10)
        return bool(kernel.SetFileInformationByHandle(handle, 21, ctypes.byref(flags), ctypes.sizeof(flags)))
    finally:
        kernel.CloseHandle(handle)


class CPlacer:
    """Puts one file at its place: link, else copy; remembers when linking does not work here."""

    def __init__(self, use_links: bool = True) -> None:
        self.m_use_links = use_links
        self.m_failed = 0

    def put(self, source: str, destination: str) -> bool:
        """``source`` at ``destination``, where no file is yet; whether it is a hard link (else a copy)."""
        if self.m_use_links and self.m_failed < LINK_GIVE_UP:
            try:
                os.link(source, destination)
                self.m_failed = 0
                return True
            except OSError:
                self.m_failed += 1
        shutil.copy2(source, destination)
        return False

    def place(self, source: str, destination: str) -> str:
        """``source`` at ``destination`` whatever is there: ``"linked"``, ``"copied"`` or ``"kept"``."""
        if _current(source, destination):
            return "kept"
        if os.path.lexists(destination):
            remove(destination)
        return "linked" if self.put(source, destination) else "copied"


def make_parents(root: str | Path, rels: list[str], made: set[str]) -> None:
    """The folders the files ``rels`` of ``root`` need; ``made`` remembers them between calls."""
    for rel in rels:
        parent = os.path.dirname(os.path.join(root, rel))
        if parent not in made:
            os.makedirs(parent, exist_ok=True)
            made.add(parent)


def _remove_stale(out: Path, wanted: set[str]) -> int:
    """Remove what ``out`` holds that is not wanted (files, then the folders left empty); returns the files removed."""
    removed = 0
    folders = []
    for here, base, _dirs, names in walk_sizes(out):
        folders.append(here)
        for name, _size in names:
            rel = name if base == "." else f"{base}/{name}"
            if rel not in wanted:
                remove(Path(here, name))
                removed += 1
    for here in reversed(folders):
        if here != out:
            try:
                here.rmdir()  # only an empty folder goes
            except OSError:
                pass
    return removed


def sync(
    out: Path,
    files: dict[str, bytes | CFileRef | Path],
    say: Callable[[str], None] = lambda _t: None,
    use_links: bool = True,
) -> Counter[str]:
    """Make ``out`` hold exactly ``files`` (mod path -> content). Returns the counts: ``linked``, ``copied``,
    ``written``, ``kept``, ``removed``. ``say`` is called between chunks and may raise to stop (what is placed stays)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    counts["removed"] = _remove_stale(out, set(files))
    made: set[str] = set()
    placer = CPlacer(use_links)
    names = sorted(files)

    def one(rel: str) -> str:
        dest = os.path.join(out, rel)
        data = files[rel]
        if isinstance(data, CFileRef | Path):
            return placer.place(str(data), dest)
        if os.path.lexists(dest):
            if os.path.isfile(dest) and os.path.getsize(dest) == len(data) and Path(dest).read_bytes() == data:
                return "kept"
            remove(dest)  # never in place: it may be a hard link to a source file
        Path(dest).write_bytes(data)
        return "written"

    with ThreadPoolExecutor(WORKERS) as pool:
        for start in range(0, len(names), CHUNK):
            chunk = names[start : start + CHUNK]
            make_parents(out, chunk, made)
            counts.update(pool.map(one, chunk))
            done = start + len(chunk)
            say(f"{done} / {len(names)}" if done // REPORT != start // REPORT else "")  # "": only a chance to stop
    return counts
