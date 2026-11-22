"""The binary tag tree (``BIN ``) of Anno 2070's savegames: read and written back byte for byte.

``read`` gives a ``STree`` (the nodes as ``isd2070.SNode``: ``text`` for a text value, ``data`` for bytes) and the tag
table in file order; ``write`` is its inverse. The layout is ``docs/save-editor.md``, "Anno 2070 in detail".
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .isd2070 import SNode

MAGIC, MAP, END = b"BIN ", b"MAP ", b"END "
TEXT, CLOSE = -1, -1  # a length of -1: a NUL-terminated text follows; a tag number of -1 closes a node


class CBinTreeError(ValueError):
    pass


@dataclass
class STree:
    """The top-level nodes and the tag table (name -> number, in the order the file lists them)."""

    nodes: list[SNode] = field(default_factory=list)
    tags: dict[str, int] = field(default_factory=dict)

    def find(self, name: str) -> SNode | None:
        return next((name_length for name_length in self.nodes if name_length.name == name), None)

    def path(self, path: str) -> SNode | None:
        first, _, rest = path.partition("/")
        node = self.find(first)
        return node.path(rest) if node is not None and rest else node


def _unnamed(name: str) -> bool:
    """``#<number>``: the name ``read`` gives a node whose number the file's table does not list."""
    return name.startswith("#") and name[1:].isdigit()


def is_bintree(data: bytes) -> bool:
    return data[:4] == MAGIC and len(data) >= 16 and data[-8:-4] == END


def _table(data: bytes) -> tuple[int, dict[int, str], dict[str, int]]:
    (map_at,) = struct.unpack_from("<I", data, len(data) - 4)
    if data[map_at : map_at + 4] != MAP:
        raise CBinTreeError("no MAP table where the end says")
    (count,) = struct.unpack_from("<I", data, map_at + 4)
    names, order, i = {}, {}, map_at + 8
    for _ in range(count):
        name_length = data[i]
        name = data[i + 1 : i + name_length].decode("latin-1")
        (number,) = struct.unpack_from("<i", data, i + 1 + name_length)
        names[number] = name
        order[name] = number
        i += 1 + name_length + 4
    if data[i : i + 4] != END or i + 8 != len(data):
        raise CBinTreeError("the MAP table does not end at END")
    return map_at, names, order


def read(data: bytes) -> STree:
    if not is_bintree(data):
        raise CBinTreeError("not a binary tag tree")
    try:
        return _read(data)
    except (struct.error, IndexError) as exc:
        raise CBinTreeError(f"truncated binary tag tree: {exc}") from None


def _read(data: bytes) -> STree:
    end, names, order = _table(data)
    tree = STree(tags=order)
    stack: list[SNode] = []
    i = 4
    while i < end:
        (number,) = struct.unpack_from("<i", data, i)
        i += 4
        if number == CLOSE:
            if not stack:
                raise CBinTreeError(f"a close without a node at {i - 4}")
            stack.pop()
            continue
        (size,) = struct.unpack_from("<i", data, i)
        i += 4
        node = SNode(names.get(number, f"#{number}"))
        if size == TEXT:
            stop = data.index(b"\0", i)
            node.text = data[i:stop].decode("latin-1")
            i = stop + 1
        elif size > 0:
            node.data = data[i : i + size]
            i += size
        (stack[-1].children if stack else tree.nodes).append(node)
        stack.append(node)
    if stack:
        raise CBinTreeError(f"{len(stack)} node(s) not closed")
    return tree


def write(tree: STree) -> bytes:
    """The bytes of ``tree``; a tag the table lacks gets the next number."""
    tags = dict(tree.tags)
    out = bytearray(MAGIC)

    def emit(node: SNode) -> None:
        if node.name not in tags and not _unnamed(node.name):
            tags[node.name] = max(tags.values(), default=0) + 1
        out.extend(struct.pack("<i", int(node.name[1:]) if _unnamed(node.name) else tags[node.name]))
        if node.text is not None:
            out.extend(struct.pack("<i", TEXT) + node.text.encode("latin-1") + b"\0")
        elif node.data:
            out.extend(struct.pack("<i", len(node.data)) + node.data)
        else:
            out.extend(struct.pack("<i", 0))
        for child in node.children:
            emit(child)
        out.extend(struct.pack("<i", CLOSE))

    for node in tree.nodes:
        emit(node)
    map_at = len(out)
    out.extend(MAP + struct.pack("<I", len(tags)))
    for name, number in tags.items():
        raw = name.encode("latin-1") + b"\0"
        out.extend(bytes([len(raw)]) + raw + struct.pack("<i", number))
    out.extend(END + struct.pack("<I", map_at))
    return bytes(out)
