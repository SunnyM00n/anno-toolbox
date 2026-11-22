"""A save as a tree to browse: the parts, then each document's nodes, made a level at a time (``Item.children``);
a value's readings (``readings``). A FileDB value that is itself a document (117's ``SessionData/BinaryData``) opens as
one. Read only; the Qt model is ``savetree``.
"""

from __future__ import annotations

import struct
import threading
from dataclasses import dataclass, field

from ..formats import bintree, filedb, isd2070
from .container import BINTREE, FILEDB, TAGTREE, CPart, SSaveFile

PREVIEW = 24  # bytes of a value shown in its row
NESTED_MIN = 64  # a value shorter than this is never read as a document


def nested_document(data: bytes) -> bool:
    """Whether a FileDB value is a FileDB document of its own (a version 3 or 2 tail that fits)."""
    if len(data) < NESTED_MIN or filedb.version(data) not in (2, 3):
        return False
    tags_off, attrs_off, align = struct.unpack_from("<III", data, len(data) - 16)
    return align == filedb.ALIGN and tags_off <= attrs_off < len(data)


@dataclass
class CItem:
    m_name: str
    m_size: int | None  # a value's bytes, None for a node that holds others
    m_preview: str
    m_source: object = field(repr=False)
    m_parent: CItem | None = field(default=None, repr=False)
    m_error: str = ""
    _m_children: list[CItem] | None = field(default=None, repr=False)
    _m_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def has_children(self) -> bool:
        source = self.m_source
        if isinstance(source, CPart):
            return source.m_kind in (FILEDB, BINTREE, TAGTREE)
        if isinstance(source, filedb.STag | isd2070.SNode):
            return bool(source.children)
        return isinstance(source, filedb.SAttrib) and nested_document(source.data)

    def children(self) -> list[CItem]:
        """The items below, made once (a nested document or a part's tree is read here: off the window's thread)."""
        with self._m_lock:
            if self._m_children is None:
                try:
                    self._m_children = [_item(child, self) for child in _kids(self.m_source)]
                except (ValueError, IndexError, struct.error) as e:
                    self.m_error = str(e)
                    self._m_children = []
            return self._m_children

    def loaded(self) -> list[CItem]:
        """The children made so far (none when not opened yet)."""
        return self._m_children or []

    def path(self) -> str:
        names, node = [], self
        while node is not None:
            names.append(node.m_name)
            node = node.m_parent
        return "/".join(reversed(names))


def _kids(source) -> list:
    if isinstance(source, CPart):
        tree = source.tree()
        if isinstance(tree, bintree.STree):
            return tree.nodes
        return tree.children if tree is not None else []
    if isinstance(source, filedb.SAttrib):
        return filedb.read(source.data).children
    return list(getattr(source, "children", []))


def readings(data: bytes) -> list[tuple[str, str]]:
    """What a value's bytes read as: integers, floats, text (only the readings its size allows)."""
    out = []
    for label, fmt in (
        ("u8", "<B"),
        ("u16", "<H"),
        ("i32", "<i"),
        ("u32", "<I"),
        ("f32", "<f"),
        ("i64", "<q"),
        ("f64", "<d"),
    ):
        if len(data) == struct.calcsize(fmt):
            out.append((label, str(struct.unpack(fmt, data)[0])))
    if len(data) == 8:
        out.append(("2 x u32", " ".join(str(value) for value in struct.unpack("<2I", data))))
    if data and len(data) % 2 == 0 and all(0x20 <= byte < 0x7F for byte in data[::2][:64]) and not any(data[1::2][:64]):
        out.append(("UTF-16", data.decode("utf-16-le", "replace")))
    if data and all(0x20 <= byte < 0x7F or byte in (9, 10, 13) for byte in data[:256]):
        out.append(("text", data.decode("latin-1")))
    if len(data) % 4 == 0 and 4 < len(data) <= 4096:
        values = struct.unpack(f"<{len(data) // 4}I", data)
        out.append(("u32 list", " ".join(str(value) for value in values[:64]) + (" ..." if len(values) > 64 else "")))
    out.append(("bytes", data[:256].hex(" ") + (" ..." if len(data) > 256 else "")))
    return out


def _preview(data: bytes) -> str:
    found = dict(readings(data))
    for label in ("UTF-16", "text", "u32", "u16", "u8", "i64"):
        if label in found:
            return found[label][:80]
    return data[:PREVIEW].hex(" ") + (" ..." if len(data) > PREVIEW else "")


def _item(source, parent: CItem | None) -> CItem:
    if isinstance(source, filedb.SAttrib):
        nested = nested_document(source.data)
        text = "FileDB document" if nested else _preview(source.data)  # i18n: ok (a format's name)
        return CItem(source.name, len(source.data), text, source, parent)
    if isinstance(source, isd2070.SNode):
        if source.text is not None:
            return CItem(source.name, len(source.text), source.text[:80], source, parent)
        if source.data is not None:
            return CItem(source.name, len(source.data), _preview(source.data), source, parent)
        return CItem(source.name, None, "", source, parent)
    return CItem(source.name, None, "", source, parent)


def roots(save: SSaveFile) -> list[CItem]:
    """One item per part of the save."""
    return [
        CItem(part.m_name, None, f"{part.m_kind}, {len(part.m_doc):,} bytes", part, None, part.m_error)
        for part in save.parts
    ]


def value_of(item: CItem) -> bytes | None:
    """The bytes of a value item (text as Latin-1), None for a node."""
    source = item.m_source
    if isinstance(source, filedb.SAttrib):
        return source.data
    if isinstance(source, isd2070.SNode):
        return source.text.encode("latin-1") if source.text is not None else source.data
    return None


def search(items: list[CItem], text: str, limit: int = 500) -> list[CItem]:
    """The items made so far whose name holds ``text`` (no case), depth first."""
    text, out, stack = text.lower(), [], list(reversed(items))
    while stack and len(out) < limit:
        item = stack.pop()
        if text in item.m_name.lower():
            out.append(item)
        stack.extend(reversed(item.loaded()))
    return out
