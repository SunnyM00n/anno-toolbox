"""Any game's savegame opened read-only: the archive's parts, each unpacked by what it starts with and its document
recognised by what it is (``docs/save-editor.md``, "The containers"). ``open_bytes`` is the entry; a part's tree is
read when first asked for (``Part.tree``) and kept.
"""

from __future__ import annotations

import threading
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import bintree, filedb, isd2070, rda, savegame117, savegame2070

TAIL, ZLIB, ZZZZ, STORED = "tail", "zlib", "zzzz", "stored"  # how a part is packed
FILEDB, BINTREE, TAGTREE, PICTURE, BYTES = "filedb", "bintree", "tagtree", "picture", "bytes"  # what it holds
PICTURE_HEADS = (b"\xff\xd8\xff", b"\x89PNG", b"DDS ")
ZLIB_HEADS = (0x01, 0x5E, 0x9C, 0xDA)


@dataclass
class Part:
    """One file of the save's archive: its stored bytes, how it is packed, its document and what that is."""

    name: str
    raw: bytes
    packing: str
    doc: bytes
    kind: str
    error: str = ""
    _tree: object = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def tree(self):
        """The document's tree (``filedb.Tag``, ``bintree.Tree`` or ``isd2070.Node``), None for pictures and bytes."""
        with self._lock:
            if self._tree is None and self.kind in (FILEDB, BINTREE, TAGTREE):
                self._tree = read_document(self.doc, self.kind)
            return self._tree


@dataclass
class SaveFile:
    """A save as opened: the file's bytes (the copy the app works on), its archive version and its parts."""

    path: Path
    game: str
    data: bytes
    archive: str  # the RDA version ("2.2"), "" for a file that is one document
    parts: list[Part]

    def part(self, name: str) -> Part | None:
        return next((p for p in self.parts if p.name == name), None)


def unpack(raw: bytes, name: str = "part") -> tuple[str, bytes]:
    """How a part is packed and its document: 117's tail, a bare zlib stream, 2070's ``ZZZZ``, or stored."""
    if savegame2070.is_packed(raw):
        return ZZZZ, savegame2070.unpack(raw, name)
    if len(raw) > 6 and raw[0] == 0x78 and raw[1] in ZLIB_HEADS:
        try:
            return TAIL, savegame117.unpack_part(raw, name)
        except savegame117.SaveError:
            try:
                return ZLIB, zlib.decompressobj().decompress(raw)
            except zlib.error:
                pass
    return STORED, raw


def kind_of(doc: bytes) -> str:
    if bintree.is_bintree(doc):
        return BINTREE
    if doc.startswith(PICTURE_HEADS):
        return PICTURE
    if filedb.version(doc) in (2, 3):
        return FILEDB
    if doc.lstrip()[:1] == b"<":
        return TAGTREE
    try:  # version 1 has no magic: it is one when it reads
        filedb.read(doc)
    except ValueError:
        return BYTES
    return FILEDB


def read_document(doc: bytes, kind: str):
    if kind == FILEDB:
        return filedb.read(doc)
    if kind == BINTREE:
        return bintree.read(doc)
    return isd2070.parse(doc)


def _part(name: str, raw: bytes) -> Part:
    try:
        packing, doc = unpack(raw, name)
    except (savegame2070.Save2070Error, savegame117.SaveError) as e:
        return Part(name, raw, STORED, raw, BYTES, str(e))
    return Part(name, raw, packing, doc, kind_of(doc))


def open_bytes(path: Path, data: bytes, game: str = "") -> SaveFile:
    """A save's bytes as parts; a file that is no archive is one part of its own."""
    path = Path(path)
    version = rda.detect_version(data[:64])
    if version is None:
        return SaveFile(path, game, data, "", [_part(path.name, data)])
    try:
        raw = rda.RdaArchive.from_bytes(data, path.name).read_all()
    except ValueError as e:  # RdaError is one
        raise ValueError(f"{path.name}: the archive does not read ({e})") from None
    return SaveFile(path, game, data, version.name, [_part(n, r) for n, r in raw.items()])


def open_file(path: Path, game: str = "") -> SaveFile:
    """Read the file once (the app's copy) and open it."""
    return open_bytes(Path(path), Path(path).read_bytes(), game)
