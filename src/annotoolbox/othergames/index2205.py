"""Anno 2205's archive index, ``maindata/file.db``: which archive holds each file and where (1800's
``maindata/file.db`` and the History Editions' ``ahe/file.db`` are the same tree in another FileDB layout).

The game reads the index at start and finds every file through it, so a changed file reaches the game only when
its entry points at the new bytes (docs/formats/anno2205-map-format.md, "How the game finds a map").
``ArchiveIndex`` reads the index, points entries at bytes in an archive of our own (added to its archive list) and
writes it back; an unchanged index comes out byte for byte as it was. Used by ``editor/formats/a7m/edit2205.py``.
"""

from __future__ import annotations

import copy
import struct
from dataclasses import dataclass

from ..formats import filedb
from ..formats.rda import RdaEntry

FILE_DB = "maindata/file.db"
# attribute name -> (struct format, field of IndexEntry); a zero is not written (the game reads it as the default)
FIELDS = (
    ("ArchiveFileIndex", "<I", "archive"),
    ("Position", "<Q", "position"),
    ("CompressedSize", "<Q", "packed"),
    ("UncompressedSize", "<Q", "size"),
    ("ModificationTime", "<Q", "mtime"),
    ("Flags", "<I", "flags"),
)


@dataclass(frozen=True)
class IndexEntry:
    archive: int  # index into the archive list
    position: int  # absolute offset in that archive
    packed: int
    size: int
    mtime: int = 0
    flags: int = 0  # 1 compressed, 2 encrypted, 4 resident, 8 deleted


class ArchiveIndex:
    """``file.db`` as a tree: entries by their full lower-case path (``a.a6m|gamedata.data`` names a member)."""

    def __init__(self, raw: bytes) -> None:
        self.ids = filedb.name_ids(raw)
        self.version = filedb.version(raw)  # 2205 and the History Editions write layout 1, 1800 layout 3
        self.root = filedb.read(raw).find("ArchiveMap")
        if self.root is None or self.root.find("FileTree") is None:
            raise ValueError("not an Anno 2205 archive index: no ArchiveMap / FileTree")
        self.archives = self.root.find("ArchiveFiles")
        self._tags: dict[str, filedb.Tag] = {}
        self._collect(self.root.find("FileTree"))

    def _collect(self, tag: filedb.Tag) -> None:
        for child in tag.children:
            if isinstance(child, filedb.Tag):
                name = child.find("FileName")
                if name is not None:
                    self._tags[name.data.decode("utf-16-le").lower()] = child
                else:
                    self._collect(child)

    def archive_names(self) -> list[str]:
        out = []
        for item in self.archives.children:
            if isinstance(item, filedb.Tag):
                out.append(next(a for a in item.children if not isinstance(a, filedb.Tag)).data.decode("utf-16-le"))
        return out

    def archive_index(self, name: str) -> int:
        """The place of an archive in the list, appended (opened by the game like the rest) when it is not there."""
        names = self.archive_names()
        if name in names:
            return names.index(name)
        item = copy.deepcopy(next(c for c in reversed(self.archives.children) if isinstance(c, filedb.Tag)))
        item.children[0].data = name.encode("utf-16-le")
        self.archives.children.append(item)
        return len(names)

    def entry(self, path: str) -> IndexEntry | None:
        tag = self._tags.get(path.lower())
        if tag is None:
            return None
        values = {}
        for attr, fmt, field in FIELDS:
            a = tag.find(attr)
            values[field] = struct.unpack(fmt, a.data)[0] if a is not None else 0
        return IndexEntry(**values)

    def paths_in(self, archive: str) -> dict[int, str]:
        """Position in the archive named ``archive`` -> the path whose entry points there (empty when the list does
        not name it): how a patch archive's members get their game paths."""
        names = self.archive_names()
        if archive not in names:
            return {}
        slot = names.index(archive)
        out = {}
        for path in self._tags:
            found = self.entry(path)
            if found is not None and found.archive == slot:
                out[found.position] = path
        return out

    def point(self, path: str, archive: str, member: RdaEntry) -> None:
        """Make ``path`` read ``member`` (an entry of the archive named ``archive``, as ``rda.RdaArchive`` lists it)."""
        tag = self._tags.get(path.lower())
        if tag is None:
            raise KeyError(path)
        new = IndexEntry(
            self.archive_index(archive), member.offset, member.compressed_size, member.size, 0, member.flags
        )
        tag.children = [tag.find("FileName")]  # a resident buffer (flag 4) would hold the old bytes: dropped
        for attr, fmt, field in FIELDS:
            value = getattr(new, field)
            if value:
                tag.children.append(filedb.Attrib(attr, struct.pack(fmt, value)))

    def write(self) -> bytes:
        return filedb.write(filedb.Tag("", [self.root]), *self.ids, version=self.version)
