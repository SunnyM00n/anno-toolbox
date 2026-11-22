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


class CFileDBError(ValueError):
    """A document that is not FileDB, is cut short, or an edit that cannot be made to it."""


@dataclass
class SAttrib:
    name: str
    data: bytes

    def u32(self) -> int:
        try:
            return struct.unpack_from("<I", self.data)[0]
        except struct.error as error:
            raise CFileDBError(f"attribute {self.name!r} of {len(self.data)} bytes is no uint32") from error


@dataclass
class STag:
    name: str
    children: list = field(default_factory=list)

    def find(self, name: str):
        """First direct child (tag or attribute) with this name, or None."""
        return next((child for child in self.children if child.name == name), None)

    def findall(self, name: str) -> list:
        return [child for child in self.children if child.name == name]

    def path(self, path: str):
        """Follow a ``/``-separated path of child names."""
        node = self
        for part in path.split("/"):
            node = node.find(part) if isinstance(node, STag) else None
            if node is None:
                return None
        return node

    def iter(self):
        yield self
        for child in self.children:
            if isinstance(child, STag):
                yield from child.iter()
            else:
                yield child


def _pad(length_bytes: int) -> int:
    return (length_bytes + ALIGN - 1) // ALIGN * ALIGN


def version(data: bytes) -> int:
    """The layout of a document: 3 or 2 by the magic at its end, else 1 (which has none)."""
    if len(data) >= 16:
        magic = struct.unpack_from("<i", data, len(data) - 4)[0]
        if magic in (MAGIC, MAGIC_V2):
            return -magic
    return 1


def _read_names(data: bytes, offset_bytes: int) -> dict[int, str]:
    (count,) = struct.unpack_from("<I", data, offset_bytes)
    ids = struct.unpack_from(f"<{count}H", data, offset_bytes + 4)
    position = offset_bytes + 4 + 2 * count
    names = {}
    for i in ids:
        end = data.index(b"\0", position)
        names[i] = data[position:end].decode("utf-8")
        position = end + 1
    return names


def _read_names_v1(data: bytes, offset_bytes: int) -> tuple[dict[int, str], int]:
    """A v1 name table (varint count, then ``name\0`` + uint16 id each) and the offset after it."""
    count, position = leb128.decode(data, offset_bytes)
    names = {}
    for _ in range(count):
        end = data.index(b"\0", position)
        names[struct.unpack_from("<H", data, end + 1)[0]] = data[position:end].decode("utf-8")
        position = end + 3
    return names, position


def _tables(data: bytes) -> tuple[int, dict[int, str], dict[int, str]]:
    """End of the node stream, tag names, attribute names."""
    try:
        return _read_tables(data)
    except CFileDBError:
        raise
    except (struct.error, IndexError, ValueError) as error:  # ValueError: a name without its end, bad UTF-8
        raise CFileDBError(f"not a FileDB document: {error}") from None


def _read_tables(data: bytes) -> tuple[int, dict[int, str], dict[int, str]]:
    if version(data) == 1:
        (offset,) = struct.unpack_from("<I", data, len(data) - 4)
        tags, position = _read_names_v1(data, offset)
        return offset, tags, _read_names_v1(data, position)[0]
    tags_off, attrs_off, align, _ = struct.unpack_from("<IIIi", data, len(data) - 16)
    if align != ALIGN:
        raise CFileDBError(f"not a FileDB document (alignment {align})")
    return tags_off, _read_names(data, tags_off), _read_names(data, attrs_off)


def _check_size(size_bytes: int, position: int, end: int) -> None:
    """A negative size would walk the cursor backwards for ever, an overrunning one past the node stream."""
    if size_bytes < 0 or position + size_bytes > end:
        raise CFileDBError(f"FileDB attribute of {size_bytes} bytes at {position} leaves the node stream")


def read(data: bytes) -> STag:
    """Parse a FileDB document (any version); returns a root tag (name ``""``) holding the top-level nodes."""
    end, tag_names, attr_names = _tables(data)
    v1 = version(data) == 1
    root = STag("")
    stack = [root]
    i = 0
    try:
        while i < end:
            if v1:
                (nid,) = struct.unpack_from("<H", data, i)
                i += 2
            else:
                size, nid = struct.unpack_from("<iI", data, i)
                i += 8
            if nid == 0:
                if len(stack) == 1:
                    break  # the stream ends with a close at the top level
                stack.pop()
            elif nid < 0x8000:
                tag = STag(tag_names.get(nid, f"#{nid}"))
                stack[-1].children.append(tag)
                stack.append(tag)
            else:
                if v1:
                    size, i = leb128.decode(data, i)
                _check_size(size, i, end)
                stack[-1].children.append(SAttrib(attr_names.get(nid, f"#{nid}"), data[i : i + size]))
                i += size if v1 else _pad(size)
    except (struct.error, IndexError, ValueError) as error:
        if isinstance(error, CFileDBError):
            raise
        raise CFileDBError(f"truncated FileDB node stream at {i}: {error}") from None
    return root


def _table_items(names: dict[str, int], order: list[str]) -> list[tuple[str, int]]:
    """``order``: names in table order (known ones first, as given, then new ones by id)."""
    rank = {name: k for k, name in enumerate(order)}
    return sorted(
        ((name, i) for name, i in names.items() if not name.startswith("#")),
        key=lambda kv: (rank.get(kv[0], len(rank)), kv[1]),
    )


def _name_table(names: dict[str, int], order: list[str]) -> bytes:
    items = _table_items(names, order)
    out = struct.pack("<I", len(items)) + struct.pack(f"<{len(items)}H", *[i for _, i in items])
    out += b"".join(name.encode("utf-8") + b"\0" for name, _ in items)
    return out + b"\0" * (_pad(len(out)) - len(out))


def _name_table_v1(names: dict[str, int], order: list[str]) -> bytes:
    items = _table_items(names, order)
    return leb128.encode(len(items)) + b"".join(
        name.encode("utf-8") + b"\0" + struct.pack("<H", i) for name, i in items
    )


def write(
    root: STag, tag_ids: dict[str, int] | None = None, attr_ids: dict[str, int] | None = None, version: int = 3
) -> bytes:
    """Serialize a root tag (as returned by ``read``) in FileDB ``version`` 1, 2 or 3. Name ids are
    assigned in order of first use unless given (pass the ids of an original document, ``name_ids``,
    to reproduce it byte for byte)."""
    if version not in (1, 2, 3):
        raise CFileDBError(f"FileDB version {version} is not known")
    tag_order = list(tag_ids or {})
    attr_order = list(attr_ids or {})
    tag_ids = dict(tag_ids or {})
    attr_ids = dict(attr_ids or {})
    used_tags: dict[str, int] = {}
    used_attrs: dict[str, int] = {}
    out = bytearray()
    nodes = 0

    def head(size_bytes: int, nid: int) -> bytes:
        return struct.pack("<H", nid) if version == 1 else struct.pack("<iI", size_bytes, nid)

    def emit(node) -> None:
        nonlocal nodes
        nodes += 1
        if isinstance(node, STag):
            if node.name.startswith("#"):
                tag_ids[node.name] = int(node.name[1:])
            if node.name not in tag_ids:
                tag_ids[node.name] = max(tag_ids.values(), default=1) + 1
            used_tags[node.name] = tag_ids[node.name]
            out.extend(head(0, tag_ids[node.name]))
            for child in node.children:
                emit(child)
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

    for child in root.children:
        emit(child)
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
    return {name: i for i, name in tags.items()}, {name: i for i, name in attrs.items()}


# Patching in place (version 3 only)


class CSpan:
    """A node of a version 3 document with its place in the bytes: ``header`` is where its 8-byte head starts,
    ``start`` and ``size_bytes`` the content of an attribute."""

    __slots__ = ("m_name", "m_is_tag", "m_header", "m_start", "m_size_bytes", "m_children", "m_parent")

    def __init__(
        self, name: str, is_tag: bool, header: int, start: int = 0, size_bytes: int = 0, parent: CSpan | None = None
    ) -> None:
        self.m_name = name
        self.m_is_tag = is_tag
        self.m_header = header
        self.m_start = start
        self.m_size_bytes = size_bytes
        self.m_parent: CSpan | None = parent
        self.m_children: list[CSpan] = []

    def find(self, name: str) -> CSpan | None:
        return next((c for c in self.m_children if c.m_name == name), None)

    def findall(self, name: str) -> list[CSpan]:
        return [c for c in self.m_children if c.m_name == name]

    def path(self, path: str) -> CSpan | None:
        node = self
        for part in path.split("/"):
            node = node.find(part) if node is not None else None
        return node

    def value(self, data: bytes) -> bytes:
        return data[self.m_start : self.m_start + self.m_size_bytes]


def scan(data: bytes, stop=None) -> CSpan:
    """Parse a version 3 document keeping every node's offsets. ``stop(attribute)`` returning true ends the scan
    early (a big document where the wanted nodes come first); the tree is then partial."""
    if version(data) != 3:
        raise CFileDBError("not a FileDB version 3 document")
    try:
        return _scan(data, stop)
    except (struct.error, IndexError) as error:
        raise CFileDBError(f"truncated FileDB node stream: {error}") from None


def _scan(data: bytes, stop) -> CSpan:
    tags_off, tag_names, attr_names = _tables(data)
    root = current = CSpan("", True, -1)
    i = 0
    while i < tags_off:
        size, nid = struct.unpack_from("<iI", data, i)
        header = i
        i += 8
        if nid == 0:
            if current is root:
                break
            current = current.m_parent
        elif nid < 0x8000:
            node = CSpan(tag_names.get(nid, f"#{nid}"), True, header, parent=current)
            current.m_children.append(node)
            current = node
        else:
            _check_size(size, i, tags_off)
            node = CSpan(attr_names.get(nid, f"#{nid}"), False, header, i, size, current)
            current.m_children.append(node)
            i += _pad(size)
            if stop is not None and stop(node):
                break
    return root


def patch(data: bytes, edits: list[tuple[CSpan, bytes]]) -> bytes:
    """New contents for attributes of ``scan``'s tree, in one pass. Each size is rewritten and the two name-table
    offsets in the tail move by the change of padded length; tags carry no size, so nothing else does."""
    out = bytearray()
    position = delta = 0
    for node, new in sorted(edits, key=lambda edit: edit[0].m_start):
        if node.m_is_tag or node.m_start < position:
            raise CFileDBError(f"cannot patch {node.m_name!r}: a tag, or edited twice")
        out += (
            data[position : node.m_header] + struct.pack("<i", len(new)) + data[node.m_header + 4 : node.m_header + 8]
        )
        out += new + b"\0" * (_pad(len(new)) - len(new))
        position = node.m_start + _pad(node.m_size_bytes)
        delta += _pad(len(new)) - _pad(node.m_size_bytes)
    out += data[position:]
    tags_off, attrs_off = struct.unpack_from("<II", out, len(out) - 16)
    struct.pack_into("<II", out, len(out) - 16, tags_off + delta, attrs_off + delta)
    return bytes(out)


def dump(node, depth: int = 0, max_bytes: int = 32, limit: int | None = None, out: list | None = None) -> list[str]:
    """Readable outline (tags indented, attributes with size and leading bytes)."""
    out = [] if out is None else out
    for child in node.children:
        if limit is not None and len(out) >= limit:
            break
        if isinstance(child, STag):
            out.append("  " * depth + f"<{child.name}> ({len(child.children)})")
            dump(child, depth + 1, max_bytes, limit, out)
        else:
            text = ""
            if child.data and all(32 <= x < 127 for x in child.data):
                text = f' "{child.data.decode()}"'
            out.append("  " * depth + f"{child.name} [{len(child.data)}] {child.data[:max_bytes].hex(' ')}{text}")
    return out
