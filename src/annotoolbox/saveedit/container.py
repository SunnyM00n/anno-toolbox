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
class CPart:
    """One file of the save's archive: its stored bytes, how it is packed, its document and what that is."""

    m_name: str
    m_raw: bytes
    m_packing: str
    m_doc: bytes
    m_kind: str
    m_error: str = ""
    _m_tree: object = field(default=None, repr=False)
    _m_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def tree(self) -> filedb.STag | bintree.STree | isd2070.SNode | None:
        """The document's tree (``filedb.STag``, ``bintree.STree`` or ``isd2070.SNode``), None for pictures and bytes."""
        with self._m_lock:
            if self._m_tree is None and self.m_kind in (FILEDB, BINTREE, TAGTREE):
                self._m_tree = read_document(self.m_doc, self.m_kind)
            return self._m_tree


@dataclass
class SSaveFile:
    """A save as opened: the file's bytes (the copy the app works on), its archive version and its parts."""

    path: Path
    game: str
    data: bytes
    archive: str  # the RDA version ("2.2"), "" for a file that is one document
    parts: list[CPart]

    def part(self, name: str) -> CPart | None:
        return next((part for part in self.parts if part.m_name == name), None)


def unpack(raw: bytes, name: str = "part") -> tuple[str, bytes]:
    """How a part is packed and its document: 117's tail, a bare zlib stream, 2070's ``ZZZZ``, or stored."""
    if savegame2070.is_packed(raw):
        return ZZZZ, savegame2070.unpack(raw, name)
    if len(raw) > 6 and raw[0] == 0x78 and raw[1] in ZLIB_HEADS:
        try:
            return TAIL, savegame117.unpack_part(raw, name)
        except savegame117.CSaveError:
            try:
                return ZLIB, zlib.decompressobj().decompress(raw)
            except zlib.error:
                pass
    return STORED, raw


def kind_of(document: bytes) -> str:
    if bintree.is_bintree(document):
        return BINTREE
    if document.startswith(PICTURE_HEADS):
        return PICTURE
    if filedb.version(document) in (2, 3):
        return FILEDB
    if document.lstrip()[:1] == b"<":
        return TAGTREE
    try:  # version 1 has no magic: it is one when it reads
        filedb.read(document)
    except ValueError:
        return BYTES
    return FILEDB


def read_document(document: bytes, kind: str) -> filedb.STag | bintree.STree | isd2070.SNode:
    if kind == FILEDB:
        return filedb.read(document)
    if kind == BINTREE:
        return bintree.read(document)
    return isd2070.parse(document)


def _part(name: str, raw: bytes) -> CPart:
    try:
        packing, document = unpack(raw, name)
    except (savegame2070.CSave2070Error, savegame117.CSaveError) as e:
        return CPart(name, raw, STORED, raw, BYTES, str(e))
    return CPart(name, raw, packing, document, kind_of(document))


def open_bytes(path: Path, data: bytes, game: str = "") -> SSaveFile:
    """A save's bytes as parts; a file that is no archive is one part of its own."""
    path = Path(path)
    version = rda.detect_version(data[:64])
    if version is None:
        return SSaveFile(path, game, data, "", [_part(path.name, data)])
    try:
        raw = rda.CRdaArchive.from_bytes(data, path.name).read_all()
    except ValueError as e:  # CRdaError is one
        raise ValueError(f"{path.name}: the archive does not read ({e})") from None
    return SSaveFile(path, game, data, version.name, [_part(name, part_raw) for name, part_raw in raw.items()])


def open_file(path: Path, game: str = "") -> SSaveFile:
    """Read the file once (the app's copy) and open it."""
    return open_bytes(Path(path), Path(path).read_bytes(), game)
