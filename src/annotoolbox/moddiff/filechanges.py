"""The files a mod carries that are no patches: each is new, or replaces the game's file of its path, or is that file.

``mod_files(base, mod, compare_bytes)`` lists a mod's, ``classify`` makes one record from any source (a mod's file, an
archive member, a loose file) and ``digest`` is how two copies are compared. ``docs/mod-diff.md`` "What a change is".
"""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Callable
from functools import partial
from pathlib import Path

from ..modloader.mods import MODINFO_NAMES, CMod, patch_target
from .base import CGameBase
from .model import NEW, REPLACED, SAME, SFileChange


def digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def classify(
    base: CGameBase,
    path: str,
    size_bytes: int,
    read: Callable[[], bytes],
    compare: bool,
    source: str | tuple[str, str] = "",
) -> SFileChange:
    """The record of one file. ``compare``: read it (for its digest, and to tell a file that is the game's own, ``SAME``)."""
    data = read() if compare else None
    sha = digest(data) if data is not None else ""
    if not base.exists(path):
        return SFileChange(path, NEW, size_bytes, digest=sha, source=source)
    if data is None:
        return SFileChange(path, REPLACED, size_bytes, source=source)
    theirs = base.read_base(path)
    return SFileChange(
        path, SAME if theirs is not None and theirs == data else REPLACED, size_bytes, digest=sha, source=source
    )


def read_source(source: str | tuple[str, str]) -> bytes:
    """The bytes of a ``SFileChange.source``: a file, or a member of a ``.zip`` (``archive.zip|path inside``); a pair is
    the mod's ``source_root`` and the file's path in it."""
    archive, bar, member = (source if isinstance(source, str) else "".join(source)).partition("|")
    if bar and archive.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            return z.read(member)
    return Path(archive).read_bytes()


def is_content(rel: str) -> bool:
    """Whether a mod's file is carried whole: no patch, no include, not the ``modinfo``."""
    low = rel.lower()
    return patch_target(rel) is None and low not in MODINFO_NAMES and not low.endswith(".include.xml")


def mod_files(base: CGameBase, mod: CMod, compare_bytes: int) -> list[SFileChange]:
    """The files of ``mod`` that are carried whole, by path; at most ``compare_bytes`` of them are compared with the
    game's byte for byte (the sizes come from the folder listing, a mod may hold half a million files)."""
    out, root = [], mod.source_root  # one root string for the mod: a mod may carry half a million files
    for rel in mod.files():
        if not is_content(rel):
            continue
        low = rel.lower()
        path = rel if low == rel else low  # the mod's own string where it is lower case already
        compare = len(out) < compare_bytes
        read = partial(mod.read, rel) if compare else bytes
        out.append(classify(base, path, mod.size_of(rel), read, compare, (root, rel)))
    return out
