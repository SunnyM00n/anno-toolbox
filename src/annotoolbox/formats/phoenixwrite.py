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
    for field in node["fields"]:
        if "raw" in field:
            out += field["raw"]
        else:
            gaps = field.get("gaps") or [b""] * len(field["objs"])
            out += field["head"] + b"".join(
                encode_object(child) + gap for child, gap in zip(field["objs"], gaps, strict=True)
            )
    return bytes(out)


def type_order(schema: dict, root: dict, guid_of: dict[str, str] | None = None) -> list[str]:
    """The type table order: pre-order over all objects; an object's own type first, then the records of its
    chain (Object first) as the serialization reaches their fields, each followed by the objects in those fields.
    A chain record ``guid_of`` lacks did not exist in the version the file was saved in and is left out."""
    types: list[str] = []

    def add(node: dict) -> None:
        type_hex = f"{node['type']:08x}"
        if type_hex not in types:
            types.append(type_hex)
        for record in type_chain(schema, node["schema_type"], guid_of):
            if record not in types:
                types.append(record)
            for field in node["fields"]:
                if field["record"] == record:
                    for child in field["objs"]:
                        add(child)

    add(root)
    return types


def shape_tokens(root: dict, types: list[str]) -> list[tuple]:
    """The shape prefix tokens: ``40 1 typeIndex`` for the root and every object in a 0x4200/0x4201 field
    (embedded 0x4000 and boxed objects have none), ``42 count`` for a non-empty object list, ``code>>8 count``
    for a non-empty list of plain values, and ``51 count`` after the object's fields for each such list with
    field flag 0x10000 (the PropertyIdPath lists)."""
    tokens: list[tuple] = []

    def typed(field: dict) -> None:  # the count (kind 0), then per value its code, an object as an object
        codes, objs = [code for _, code, _ in typed_values(field)], iter(field["objs"])
        tokens.extend([(0, len(codes))] if codes else [])
        for code in codes:
            walk(next(objs), True) if code in TYPED_OBJECTS else tokens.append((code >> 8, 1))

    def walk(node: dict, token: bool) -> None:
        if token:
            tokens.append((0x40, 1, types.index(f"{node['type']:08x}")))
        late = []
        for field in node["fields"]:
            code = int(field["field"]["code"], 16)
            if code == TYPED_VALUES:
                typed(field)
                continue
            if "raw" in field:
                if code & 1 and field["raw"][:1] != b"\x00":
                    length = leb128.decode(field["raw"])[0]
                    tokens.append((code >> 8, length))
                    if int(field["field"]["flags"], 16) & 0x10000:
                        late.append((0x51, length))
                continue
            if code == 0x4201:
                tokens.append((0x42, len(field["objs"])))
            for child in field["objs"]:
                walk(child, code in (0x4200, 0x4201))
        tokens.extend(late)

    walk(root, True)
    return tokens


def encode_file(
    schema: dict, head: bytes, root: dict, guid_of: dict[str, str], kind_order: list[int] | None = None
) -> bytes:
    """A whole PHX file from its object tree. ``head`` is the 16 fixed bytes (magic, tool version, serializer
    version, constant, flags); ``guid_of`` gives the schema GUID (layout version) per type hash. The kind totals
    go in descending kind order, as the current editor writes them, or in ``kind_order`` (the order a decoded
    file had: editors up to 17.19 wrote another, phoenix-scene-format.md "Shape prefix") when it holds the same kinds."""
    schema = full_schema(schema)
    types = type_order(schema, root, guid_of)
    tokens = shape_tokens(root, types)
    counts = [0] * len(types)
    sums: dict[int, int] = {}
    for token in tokens:
        if token[0] == 0x40:
            counts[token[2]] += 1
        else:
            sums[token[0]] = sums.get(token[0], 0) + token[1]
    out = bytearray(head[:16]) + leb128.encode(len(types))
    for type_hash, count in zip(types, counts, strict=False):
        out += struct.pack("<I", int(type_hash, 16)) + uuid.UUID(guid_of[type_hash]).bytes_le + leb128.encode(count)
    untyped = sums.pop(0, 0)  # the typed value lists' counts go into the varint after the totals
    out += leb128.encode(len(sums))
    order = kind_order if kind_order is not None and sorted(kind_order) == sorted(sums) else sorted(sums, reverse=True)
    for k in order:
        out += bytes([k]) + leb128.encode(sums[k])
    body = b"".join(bytes([token[0]]) + b"".join(leb128.encode(x) for x in token[1:]) for token in tokens)
    out += leb128.encode(untyped) + leb128.encode(len(tokens)) + leb128.encode(len(body)) + body + b"\x02"
    return bytes(out) + encode_object(root)


def header(kind: str, tool: tuple[int, int, int] = TOOL_VERSION) -> bytes:
    """The 16 fixed bytes of a file of ``kind`` (``WI``, ``IM``, ``SC`` ...)."""
    return b"PHX" + kind.encode("ascii") + bytes(tool) + struct.pack("<HIH", SERIALIZER, MAGIC_CONST, FLAGS)


def save(document) -> bytes:
    """A decoded file (``phoenixread.load``) back to bytes, under its own header."""
    return encode_file(
        document.m_schema, document.m_data[:16], document.m_root, document.m_guid_of, document.m_kind_order
    )


def write_file(schema: dict, kind: str, root: dict, guid_of: dict[str, str]) -> bytes:
    """A new file of ``kind`` from a tree built with ``phoenixedit``."""
    return encode_file(schema, header(kind), root, guid_of)
