"""The binary tag tree (``BIN ``) of Anno 2070's savegames: read and written back byte for byte.

``read`` gives a ``Tree`` (the nodes as ``isd2070.Node``: ``text`` for a text value, ``data`` for bytes) and the tag
table in file order; ``write`` is its inverse. The layout is ``docs/save-editor.md``, "Anno 2070 in detail".
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .isd2070 import Node

MAGIC, MAP, END = b"BIN ", b"MAP ", b"END "
TEXT, CLOSE = -1, -1  # a length of -1: a NUL-terminated text follows; a tag number of -1 closes a node


class BinTreeError(ValueError):
    pass


@dataclass
class Tree:
    """The top-level nodes and the tag table (name -> number, in the order the file lists them)."""

    nodes: list[Node] = field(default_factory=list)
    tags: dict[str, int] = field(default_factory=dict)

    def find(self, name: str) -> Node | None:
        return next((n for n in self.nodes if n.name == name), None)

    def path(self, path: str) -> Node | None:
        first, _, rest = path.partition("/")
        node = self.find(first)
        return node.path(rest) if node is not None and rest else node


def _unnamed(name: str) -> bool:
    """``#<number>``: the name ``read`` gives a node whose number the file's table does not list."""
    return name.startswith("#") and name[1:].isdigit()


def is_bintree(b: bytes) -> bool:
    return b[:4] == MAGIC and len(b) >= 16 and b[-8:-4] == END


def _table(b: bytes) -> tuple[int, dict[int, str], dict[str, int]]:
    (map_at,) = struct.unpack_from("<I", b, len(b) - 4)
    if b[map_at : map_at + 4] != MAP:
        raise BinTreeError("no MAP table where the end says")
    (count,) = struct.unpack_from("<I", b, map_at + 4)
    names, order, i = {}, {}, map_at + 8
    for _ in range(count):
        n = b[i]
        name = b[i + 1 : i + n].decode("latin-1")
        (number,) = struct.unpack_from("<i", b, i + 1 + n)
        names[number] = name
        order[name] = number
        i += 1 + n + 4
    if b[i : i + 4] != END or i + 8 != len(b):
        raise BinTreeError("the MAP table does not end at END")
    return map_at, names, order


def read(b: bytes) -> Tree:
    if not is_bintree(b):
        raise BinTreeError("not a binary tag tree")
    end, names, order = _table(b)
    tree = Tree(tags=order)
    stack: list[Node] = []
    i = 4
    while i < end:
        (number,) = struct.unpack_from("<i", b, i)
        i += 4
        if number == CLOSE:
            if not stack:
                raise BinTreeError(f"a close without a node at {i - 4}")
            stack.pop()
            continue
        (size,) = struct.unpack_from("<i", b, i)
        i += 4
        node = Node(names.get(number, f"#{number}"))
        if size == TEXT:
            stop = b.index(b"\0", i)
            node.text = b[i:stop].decode("latin-1")
            i = stop + 1
        elif size > 0:
            node.data = b[i : i + size]
            i += size
        (stack[-1].children if stack else tree.nodes).append(node)
        stack.append(node)
    if stack:
        raise BinTreeError(f"{len(stack)} node(s) not closed")
    return tree


def write(tree: Tree) -> bytes:
    """The bytes of ``tree``; a tag the table lacks gets the next number."""
    tags = dict(tree.tags)
    out = bytearray(MAGIC)

    def emit(node: Node) -> None:
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
