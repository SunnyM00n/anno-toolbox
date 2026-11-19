"""Lists kept between starts (docs/build-cache.md, "The list cache"): what an app fills from a game's files (the archive
index, an island list, a sound bank) is stored under a signature of its inputs and read back at once, and rebuilt
only when the signature changes. ``cached(name, scope, signature, build)`` is the entry; ``files_signature`` and
``folder_signature`` make the signature from names, sizes and times (no game file is read or hashed).

A stored list is one file in ``<cache root>/lists/``. It is only ever a copy: a missing, unreadable, torn or stale
file is rebuilt, never trusted, and deleting the folder is always safe (``cache clear lists``).
"""

from __future__ import annotations

import os
import pickle
from collections.abc import Callable, Iterable
from pathlib import Path

from ..sources.overlay import cache_base
from . import buildcache

LISTS_DIR = "lists"
MAGIC = b"anno-list-1\n"  # the file's first line; a format change is a new magic


def folder() -> Path:
    return cache_base() / LISTS_DIR


def files_signature(files: Iterable[Path | str], *extra: object) -> str:
    """What changes when any of ``files`` does or one is added or removed: each one's path, size and time, then
    ``extra`` (the reader's version, the game build, the language). A file that cannot be read counts as missing."""
    parts: list[object] = list(extra)
    for f in sorted(str(p) for p in files):
        try:
            st = os.stat(f)
            parts.append(f"{f}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{f}:-")
    return buildcache.digest(*parts)


def folder_signature(root: Path | str, *extra: object, suffixes: tuple[str, ...] = ()) -> str:
    """The same for every file below ``root`` (a mod folder, an extracted archive): the listing is walked, the files
    are not opened. ``suffixes`` limits it to those endings."""
    parts: list[object] = list(extra)
    stack = [str(root)]
    found: list[str] = []
    while stack:
        here = stack.pop()
        try:
            with os.scandir(here) as entries:
                for e in entries:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif not suffixes or e.name.lower().endswith(suffixes):
                        st = e.stat()
                        found.append(f"{e.path}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            found.append(f"{here}:-")
    return buildcache.digest(*parts, *sorted(found))


def _path(name: str, scope: str) -> Path:
    return folder() / f"{name}-{buildcache.digest(scope)[:16]}.pkl"


def load(name: str, scope: str, signature: str):
    """The list stored under ``name`` for ``scope`` when it was made for ``signature``, else None (also when it
    is damaged, or when caching is off)."""
    if not buildcache.enabled():
        return None
    try:
        with _path(name, scope).open("rb") as f:
            if f.readline() != MAGIC or f.readline().decode("ascii").strip() != signature:
                return None
            return pickle.load(f)
    except Exception:  # torn, truncated or from another version of a class: all the same, a rebuild
        return None


def store(name: str, scope: str, signature: str, payload: object) -> None:
    """Keep ``payload`` (plain data: dicts, lists, strings, numbers) for ``load``; a failure to write is no failure."""
    if not buildcache.enabled():
        return
    try:
        body = MAGIC + signature.encode("ascii") + b"\n" + pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
        buildcache.write_atomic(_path(name, scope), body, sync=False)
    except (OSError, pickle.PicklingError):
        pass


def cached[T](name: str, scope: str, signature: str, build: Callable[[], T]) -> T:
    """The stored list when it fits ``signature``, else ``build()``, stored."""
    found = load(name, scope, signature)
    if found is not None:
        return found
    made = build()
    store(name, scope, signature, made)
    return made
