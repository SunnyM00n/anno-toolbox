"""FileDB, the binary tree format of Anno 1800/117 level data, in its three layouts (``docs/formats/filedb.md``).

Used by island files (``.a7m`` archive members ``gamedata.data`` and ``rd3d.data``, ``.a7minfo``) and terrain tiles
(``.tmc``). 117 writes version 3, 1800's files come in all three; ``read`` tells them apart by the tail, ``write``
writes the version it is given. Attribute content is untyped; the readers of a particular file decide how to
interpret it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import leb128

MAGIC = -3
MAGIC_V2 = -2
ALIGN = 8


@dataclass
class Attrib:
    name: str
    data: bytes

    def u32(self) -> int:
        return struct.unpack_from("<I", self.data)[0]


@dataclass
class Tag:
    name: str
    children: list = field(default_factory=list)

    def find(self, name: str):
        """First direct child (tag or attribute) with this name, or None."""
        return next((c for c in self.children if c.name == name), None)

    def findall(self, name: str) -> list:
        return [c for c in self.children if c.name == name]

    def path(self, path: str):
        """Follow a ``/``-separated path of child names."""
        node = self
        for part in path.split("/"):
            node = node.find(part) if isinstance(node, Tag) else None
            if node is None:
                return None
        return node

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Tag):
                yield from c.iter()
            else:
                yield c


def _pad(n: int) -> int:
    return (n + ALIGN - 1) // ALIGN * ALIGN


def version(b: bytes) -> int:
    """The layout of a document: 3 or 2 by the magic at its end, else 1 (which has none)."""
    if len(b) >= 16:
        magic = struct.unpack_from("<i", b, len(b) - 4)[0]
        if magic in (MAGIC, MAGIC_V2):
            return -magic
    return 1


def _read_names(b: bytes, off: int) -> dict[int, str]:
    (count,) = struct.unpack_from("<I", b, off)
    ids = struct.unpack_from(f"<{count}H", b, off + 4)
    pos = off + 4 + 2 * count
    names = {}
    for i in ids:
        end = b.index(b"\0", pos)
        names[i] = b[pos:end].decode("utf-8")
        pos = end + 1
    return names


def _read_names_v1(b: bytes, off: int) -> tuple[dict[int, str], int]:
    """A v1 name table (varint count, then ``name\0`` + uint16 id each) and the offset after it."""
    count, pos = leb128.decode(b, off)
    names = {}
    for _ in range(count):
        end = b.index(b"\0", pos)
        names[struct.unpack_from("<H", b, end + 1)[0]] = b[pos:end].decode("utf-8")
        pos = end + 3
    return names, pos


def _tables(b: bytes) -> tuple[int, dict[int, str], dict[int, str]]:
    """End of the node stream, tag names, attribute names."""
    if version(b) == 1:
        (off,) = struct.unpack_from("<I", b, len(b) - 4)
        tags, pos = _read_names_v1(b, off)
        return off, tags, _read_names_v1(b, pos)[0]
    tags_off, attrs_off, align, _ = struct.unpack_from("<IIIi", b, len(b) - 16)
    if align != ALIGN:
        raise ValueError(f"not a FileDB document (alignment {align})")
    return tags_off, _read_names(b, tags_off), _read_names(b, attrs_off)


def _check_size(size: int, pos: int, end: int) -> None:
    """A negative size would walk the cursor backwards for ever, an overrunning one past the node stream."""
    if size < 0 or pos + size > end:
        raise ValueError(f"FileDB attribute of {size} bytes at {pos} leaves the node stream")


def read(b: bytes) -> Tag:
    """Parse a FileDB document (any version); returns a root tag (name ``""``) holding the top-level nodes."""
    try:
        end, tag_names, attr_names = _tables(b)
    except (struct.error, ValueError, IndexError) as e:
        raise ValueError(f"not a FileDB document: {e}") from None
    v1 = version(b) == 1
    root = Tag("")
    stack = [root]
    i = 0
    try:
        while i < end:
            if v1:
                (nid,) = struct.unpack_from("<H", b, i)
                i += 2
            else:
                size, nid = struct.unpack_from("<iI", b, i)
                i += 8
            if nid == 0:
                if len(stack) == 1:
                    break  # the stream ends with a close at the top level
                stack.pop()
            elif nid < 0x8000:
                t = Tag(tag_names.get(nid, f"#{nid}"))
                stack[-1].children.append(t)
                stack.append(t)
            else:
                if v1:
                    size, i = leb128.decode(b, i)
                _check_size(size, i, end)
                stack[-1].children.append(Attrib(attr_names.get(nid, f"#{nid}"), b[i : i + size]))
                i += size if v1 else _pad(size)
    except (struct.error, IndexError) as e:
        raise ValueError(f"truncated FileDB node stream at {i}: {e}") from None
    return root


def _table_items(names: dict[str, int], order: list[str]) -> list[tuple[str, int]]:
    """``order``: names in table order (known ones first, as given, then new ones by id)."""
    rank = {n: k for k, n in enumerate(order)}
    return sorted(
        ((n, i) for n, i in names.items() if not n.startswith("#")), key=lambda kv: (rank.get(kv[0], len(rank)), kv[1])
    )


def _name_table(names: dict[str, int], order: list[str]) -> bytes:
    items = _table_items(names, order)
    out = struct.pack("<I", len(items)) + struct.pack(f"<{len(items)}H", *[i for _, i in items])
    out += b"".join(n.encode("utf-8") + b"\0" for n, _ in items)
    return out + b"\0" * (_pad(len(out)) - len(out))


def _name_table_v1(names: dict[str, int], order: list[str]) -> bytes:
    items = _table_items(names, order)
    return leb128.encode(len(items)) + b"".join(n.encode("utf-8") + b"\0" + struct.pack("<H", i) for n, i in items)


def write(
    root: Tag, tag_ids: dict[str, int] | None = None, attr_ids: dict[str, int] | None = None, version: int = 3
) -> bytes:
    """Serialize a root tag (as returned by ``read``) in FileDB ``version`` 1, 2 or 3. Name ids are
    assigned in order of first use unless given (pass the ids of an original document, ``name_ids``,
    to reproduce it byte for byte)."""
    if version not in (1, 2, 3):
        raise ValueError(f"FileDB version {version} is not known")
    tag_order = list(tag_ids or {})
    attr_order = list(attr_ids or {})
    tag_ids = dict(tag_ids or {})
    attr_ids = dict(attr_ids or {})
    used_tags: dict[str, int] = {}
    used_attrs: dict[str, int] = {}
    out = bytearray()
    nodes = 0

    def head(size: int, nid: int) -> bytes:
        return struct.pack("<H", nid) if version == 1 else struct.pack("<iI", size, nid)

    def emit(node) -> None:
        nonlocal nodes
        nodes += 1
        if isinstance(node, Tag):
            if node.name.startswith("#"):
                tag_ids[node.name] = int(node.name[1:])
            if node.name not in tag_ids:
                tag_ids[node.name] = max(tag_ids.values(), default=1) + 1
            used_tags[node.name] = tag_ids[node.name]
            out.extend(head(0, tag_ids[node.name]))
            for c in node.children:
                emit(c)
            out.extend(head(0, 0))
        else:
            if node.name.startswith("#"):
                attr_ids[node.name] = int(node.name[1:])
            if node.name not in attr_ids:
                attr_ids[node.name] = max(attr_ids.values(), default=0x8000) + 1
            used_attrs[node.name] = attr_ids[node.name]
            if version == 1:
                out.extend(head(0, attr_ids[node.name]) + leb128.encode(len(node.data)) + node.data)
            else:
                out.extend(head(len(node.data), attr_ids[node.name]))
                out.extend(node.data + b"\0" * (_pad(len(node.data)) - len(node.data)))

    for c in root.children:
        emit(c)
    out.extend(head(0, 0))
    tags_off = len(out)
    if version == 1:
        out += _name_table_v1(used_tags, tag_order) + _name_table_v1(used_attrs, attr_order)
        return bytes(out + struct.pack("<I", tags_off))
    out += _name_table(used_tags, tag_order)
    attrs_off = len(out)
    out += _name_table(used_attrs, attr_order)
    if version == 3:
        out += struct.pack("<II", 0, nodes + 1)
    out += struct.pack("<IIIi", tags_off, attrs_off, ALIGN, -version)
    return bytes(out)


def name_ids(b: bytes) -> tuple[dict[str, int], dict[str, int]]:
    """The tag and attribute name ids of a document, in table order (for byte-exact rewrites)."""
    _, tags, attrs = _tables(b)
    return {n: i for i, n in tags.items()}, {n: i for i, n in attrs.items()}


# Patching in place (version 3 only)


class Span:
    """A node of a version 3 document with its place in the bytes: ``header`` is where its 8-byte head starts,
    ``start`` and ``size`` the content of an attribute."""

    __slots__ = ("name", "is_tag", "header", "start", "size", "children", "parent")

    def __init__(self, name: str, is_tag: bool, header: int, start: int = 0, size: int = 0, parent=None) -> None:
        self.name = name
        self.is_tag = is_tag
        self.header = header
        self.start = start
        self.size = size
        self.parent = parent
        self.children: list[Span] = []

    def find(self, name: str) -> Span | None:
        return next((c for c in self.children if c.name == name), None)

    def findall(self, name: str) -> list[Span]:
        return [c for c in self.children if c.name == name]

    def path(self, path: str) -> Span | None:
        node = self
        for part in path.split("/"):
            node = node.find(part) if node is not None else None
        return node

    def value(self, b: bytes) -> bytes:
        return b[self.start : self.start + self.size]


def scan(b: bytes, stop=None) -> Span:
    """Parse a version 3 document keeping every node's offsets. ``stop(attribute)`` returning true ends the scan
    early (a big document where the wanted nodes come first); the tree is then partial."""
    if version(b) != 3:
        raise ValueError("not a FileDB version 3 document")
    tags_off, tag_names, attr_names = _tables(b)
    root = cur = Span("", True, -1)
    i = 0
    while i < tags_off:
        size, nid = struct.unpack_from("<iI", b, i)
        header = i
        i += 8
        if nid == 0:
            if cur is root:
                break
            cur = cur.parent
        elif nid < 0x8000:
            node = Span(tag_names.get(nid, f"#{nid}"), True, header, parent=cur)
            cur.children.append(node)
            cur = node
        else:
            _check_size(size, i, tags_off)
            node = Span(attr_names.get(nid, f"#{nid}"), False, header, i, size, cur)
            cur.children.append(node)
            i += _pad(size)
            if stop is not None and stop(node):
                break
    return root


def patch(b: bytes, edits: list[tuple[Span, bytes]]) -> bytes:
    """New contents for attributes of ``scan``'s tree, in one pass. Each size is rewritten and the two name-table
    offsets in the tail move by the change of padded length; tags carry no size, so nothing else does."""
    out = bytearray()
    pos = delta = 0
    for node, new in sorted(edits, key=lambda e: e[0].start):
        if node.is_tag or node.start < pos:
            raise ValueError(f"cannot patch {node.name!r}: a tag, or edited twice")
        out += b[pos : node.header] + struct.pack("<i", len(new)) + b[node.header + 4 : node.header + 8]
        out += new + b"\0" * (_pad(len(new)) - len(new))
        pos = node.start + _pad(node.size)
        delta += _pad(len(new)) - _pad(node.size)
    out += b[pos:]
    tags_off, attrs_off = struct.unpack_from("<II", out, len(out) - 16)
    struct.pack_into("<II", out, len(out) - 16, tags_off + delta, attrs_off + delta)
    return bytes(out)


def dump(node, depth: int = 0, max_bytes: int = 32, limit: int | None = None, out: list | None = None) -> list[str]:
    """Readable outline (tags indented, attributes with size and leading bytes)."""
    out = [] if out is None else out
    for c in node.children:
        if limit is not None and len(out) >= limit:
            break
        if isinstance(c, Tag):
            out.append("  " * depth + f"<{c.name}> ({len(c.children)})")
            dump(c, depth + 1, max_bytes, limit, out)
        else:
            text = ""
            if c.data and all(32 <= x < 127 for x in c.data):
                text = f' "{c.data.decode()}"'
            out.append("  " * depth + f"{c.name} [{len(c.data)}] {c.data[:max_bytes].hex(' ')}{text}")
    return out
