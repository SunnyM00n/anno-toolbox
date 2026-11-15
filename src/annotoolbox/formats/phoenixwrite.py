"""Encode a Phoenix object tree (``phoenixread``) back into a ``PHX??`` file.

``save`` rewrites a decoded file, ``write_file`` writes a tree built with ``phoenixedit`` under a fresh header. The
type table and the shape prefix are derived from the tree (docs/formats/phoenix-scene-format.md, "Shape prefix and
type table"); ``save`` reproduces every shipped 117 file and 93 % of 1800's byte for byte.
"""

from __future__ import annotations

import struct
import uuid

from . import leb128
from .phoenix import (
    FLAGS,
    MAGIC_CONST,
    SERIALIZER,
    TOOL_VERSION,
    TYPED_OBJECTS,
    TYPED_VALUES,
    full_schema,
    type_chain,
    typed_values,
)


def encode_object(node: dict) -> bytes:
    """An object tree node back to bytes (the root frame is guid + hash, inline ones ``02 flags [guid] hash``)."""
    out = bytearray()
    if node["flags"] is None:
        out += node["guid"]
    else:
        out += bytes([2, node["flags"]]) + (node["guid"] if node["flags"] & 2 else b"")
    out += struct.pack("<I", node["type"]) + leb128.encode(node["n"]) + node["mask"]
    for e in node["fields"]:
        if "raw" in e:
            out += e["raw"]
        else:
            gaps = e.get("gaps") or [b""] * len(e["objs"])
            out += e["head"] + b"".join(encode_object(o) + g for o, g in zip(e["objs"], gaps, strict=True))
    return bytes(out)


def type_order(schema: dict, root: dict, guid_of: dict[str, str] | None = None) -> list[str]:
    """The type table order: pre-order over all objects; an object's own type first, then the records of its
    chain (Object first) as the serialization reaches their fields, each followed by the objects in those fields.
    A chain record ``guid_of`` lacks did not exist in the version the file was saved in and is left out."""
    types: list[str] = []

    def add(node: dict) -> None:
        h = f"{node['type']:08x}"
        if h not in types:
            types.append(h)
        for rec in type_chain(schema, node["schema_type"], guid_of):
            if rec not in types:
                types.append(rec)
            for e in node["fields"]:
                if e["record"] == rec:
                    for o in e["objs"]:
                        add(o)

    add(root)
    return types


def shape_tokens(root: dict, types: list[str]) -> list[tuple]:
    """The shape prefix tokens: ``40 1 typeIndex`` for the root and every object in a 0x4200/0x4201 field
    (embedded 0x4000 and boxed objects have none), ``42 count`` for a non-empty object list, ``code>>8 count``
    for a non-empty list of plain values, and ``51 count`` after the object's fields for each such list with
    field flag 0x10000 (the PropertyIdPath lists)."""
    toks: list[tuple] = []

    def typed(e: dict) -> None:  # the count (kind 0), then per value its code, an object as an object
        codes, objs = [c for _, c, _ in typed_values(e)], iter(e["objs"])
        toks.extend([(0, len(codes))] if codes else [])
        for c in codes:
            walk(next(objs), True) if c in TYPED_OBJECTS else toks.append((c >> 8, 1))

    def walk(node: dict, token: bool) -> None:
        if token:
            toks.append((0x40, 1, types.index(f"{node['type']:08x}")))
        late = []
        for e in node["fields"]:
            code = int(e["field"]["code"], 16)
            if code == TYPED_VALUES:
                typed(e)
                continue
            if "raw" in e:
                if code & 1 and e["raw"][:1] != b"\x00":
                    n = leb128.decode(e["raw"])[0]
                    toks.append((code >> 8, n))
                    if int(e["field"]["flags"], 16) & 0x10000:
                        late.append((0x51, n))
                continue
            if code == 0x4201:
                toks.append((0x42, len(e["objs"])))
            for o in e["objs"]:
                walk(o, code in (0x4200, 0x4201))
        toks.extend(late)

    walk(root, True)
    return toks


def encode_file(
    schema: dict, head: bytes, root: dict, guid_of: dict[str, str], kind_order: list[int] | None = None
) -> bytes:
    """A whole PHX file from its object tree. ``head`` is the 16 fixed bytes (magic, tool version, serializer
    version, constant, flags); ``guid_of`` gives the schema GUID (layout version) per type hash. The kind totals
    go in descending kind order, as the current editor writes them, or in ``kind_order`` (the order a decoded
    file had: editors up to 17.19 wrote another, phoenix-scene-format.md "Shape prefix") when it holds the same kinds."""
    schema = full_schema(schema)
    types = type_order(schema, root, guid_of)
    toks = shape_tokens(root, types)
    counts = [0] * len(types)
    sums: dict[int, int] = {}
    for t in toks:
        if t[0] == 0x40:
            counts[t[2]] += 1
        else:
            sums[t[0]] = sums.get(t[0], 0) + t[1]
    out = bytearray(head[:16]) + leb128.encode(len(types))
    for h, c in zip(types, counts, strict=False):
        out += struct.pack("<I", int(h, 16)) + uuid.UUID(guid_of[h]).bytes_le + leb128.encode(c)
    untyped = sums.pop(0, 0)  # the typed value lists' counts go into the varint after the totals
    out += leb128.encode(len(sums))
    order = kind_order if kind_order is not None and sorted(kind_order) == sorted(sums) else sorted(sums, reverse=True)
    for k in order:
        out += bytes([k]) + leb128.encode(sums[k])
    body = b"".join(bytes([t[0]]) + b"".join(leb128.encode(x) for x in t[1:]) for t in toks)
    out += leb128.encode(untyped) + leb128.encode(len(toks)) + leb128.encode(len(body)) + body + b"\x02"
    return bytes(out) + encode_object(root)


def header(kind: str, tool: tuple[int, int, int] = TOOL_VERSION) -> bytes:
    """The 16 fixed bytes of a file of ``kind`` (``WI``, ``IM``, ``SC`` ...)."""
    return b"PHX" + kind.encode("ascii") + bytes(tool) + struct.pack("<HIH", SERIALIZER, MAGIC_CONST, FLAGS)


def save(d) -> bytes:
    """A decoded file (``phoenixread.load``) back to bytes, under its own header."""
    return encode_file(d.s, d.b[:16], d.root, d.guid_of, d.kind_order)


def write_file(schema: dict, kind: str, root: dict, guid_of: dict[str, str]) -> bytes:
    """A new file of ``kind`` from a tree built with ``phoenixedit``."""
    return encode_file(schema, header(kind), root, guid_of)
