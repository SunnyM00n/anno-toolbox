"""Swap an RDM mesh between byte orders, to build the big-endian files 2070 ships (a test helper).

The two forms are the same layout field for field: a file header, blocks each preceded by
``(count, item size)``, and absolute offsets. Structural blocks are four-byte fields, strings stay, the index
block is u16 each, and the vertex block follows the mesh's attribute table (a packed byte vector is a four-byte
word like any other). ``rdmread`` reads both orders; this makes the big-endian one from a little-endian one.
"""

from __future__ import annotations

import struct

from annotoolbox.formats.rdmio import HEADER1_OFFSET

# block type -> (item size, pointer field offsets and the block types they lead to; None: a string or raw data)
LAYOUT: dict[str, tuple[int, list[tuple[int, str | None]]]] = {
    "header1": (48, [(0, "export_info"), (4, "meta"), (8, "blob_to_mat"), (12, "blob_to_joint"), (16, "anim_meta")]),
    "export_info": (72, [(0, None), (4, None)]),
    "model_name": (28, [(0, None)]),
    "vert_id": (24, [(0, None)]),
    "blob_to_mat": (28, [(0, "mat")]),
    "mat": (48, [(0, None), (4, None)]),
    "blob_to_joint": (32, [(0, "joint")]),
    "joint": (84, [(0, None)]),
    "anim_meta": (0, [(0, None), (4, "anim_inner")]),
    "anim_inner": (0, [(0, None), (4, None)]),
}

BIG_ENDIAN_MAGIC = b"RDM\x00"
LITTLE_ENDIAN_MAGIC = b"RDM\x01"
# meta block pointers, in order: the model name, the attribute table, a 20-byte block, the
# vertices, the indices, and a 28-byte block
META_MODEL_NAME, META_VERT_ID, META_UNKNOWN, META_VERTICES, META_INDICES, META_MESH_INFO = range(6)
ATTRIBUTE_SIZE = 16  # usage, kind, unit, count
# element kind -> (width in bytes, whether its bytes have an order). A "byte" element is a packed
# four-byte vector: a normal or a tangent, whose components are stored the other way round as well.
KIND_FIELD = {5: (4, True), 6: (2, True), 7: (4, True)}


class RdmEndianError(ValueError):
    pass


def is_big_endian(data: bytes) -> bool:
    return data[:4] == BIG_ENDIAN_MAGIC


def _u32(data: bytes, off: int, endian: str = ">") -> int:
    return struct.unpack_from(endian + "I", data, off)[0]


def _blocks(data: bytes, endian: str) -> dict[int, tuple[int, int, str]]:
    """Every block in the file: offset -> (count, item size, how to swap it)."""
    out: dict[int, tuple[int, int, str]] = {}

    def visit(ptr: int, kind: str | None) -> None:
        if ptr == 0 or ptr in out:
            return
        if ptr < 8 or ptr > len(data):
            raise RdmEndianError(f"pointer {ptr:#x} outside the file")
        count, size = _u32(data, ptr - 8, endian), _u32(data, ptr - 4, endian)
        how = "bytes" if size == 1 else "u16" if size == 2 else "u32"
        out[ptr] = (count, size, how)
        if kind is None:
            return
        fields = LAYOUT[kind][1] if kind in LAYOUT else []
        if kind == "meta":
            fields = [(0, "model_name"), (4, "vert_id"), (8, None), (12, None), (16, None), (20, None)]
        for i in range(count):
            base = ptr + i * size
            for off, target in fields:
                if base + off + 4 <= len(data):
                    visit(_u32(data, base + off, endian), target)

    visit(HEADER1_OFFSET, "header1")
    meta = _u32(data, HEADER1_OFFSET + 4, endian)
    if meta:
        vertices = _u32(data, meta + 4 * META_VERTICES, endian)
        if vertices in out:
            count, size, _ = out[vertices]
            out[vertices] = (count, size, "vertices")
    return out


def _vertex_widths(data: bytes, stride: int, endian: str) -> list[tuple[int, bool]]:
    """The width of every field of one vertex, from the mesh's attribute table."""
    meta = _u32(data, HEADER1_OFFSET + 4, endian)
    table = _u32(data, _u32(data, meta + 4 * META_VERT_ID, endian), endian)
    count = _u32(data, table - 8, endian)
    widths: list[tuple[int, bool]] = []
    for i in range(count):
        _, kind, _, n = struct.unpack_from(endian + "4I", data, table + ATTRIBUTE_SIZE * i)
        if kind not in KIND_FIELD:
            raise RdmEndianError(f"unknown vertex element kind {kind}")
        widths += [KIND_FIELD[kind]] * n
    if sum(w for w, _ in widths) != stride:
        raise RdmEndianError(f"vertex layout of {sum(w for w, _ in widths)} bytes against a stride of {stride}")
    return widths


def to_little_endian(data: bytes) -> bytes:
    """The same mesh with little-endian words (unchanged if it already is one)."""
    return data if not is_big_endian(data) else _swap(data, ">", LITTLE_ENDIAN_MAGIC)


def to_big_endian(data: bytes) -> bytes:
    """The other way round. Swapping is its own inverse, so this is what tests convert with."""
    return data if is_big_endian(data) else _swap(data, "<", BIG_ENDIAN_MAGIC)


def _swap(data: bytes, endian: str, magic: bytes) -> bytes:
    out = bytearray(data)
    out[:4] = magic

    def swap(at: int, width: int, n: int) -> None:
        for k in range(n):
            i = at + k * width
            out[i : i + width] = data[i : i + width][::-1]

    swap(4, 4, (HEADER1_OFFSET - 4) // 4)  # the words between the magic and the first block
    for ptr, (count, size, how) in _blocks(data, endian).items():
        swap(ptr - 8, 4, 2)  # the block's own count and item size
        if how == "bytes":
            continue
        if how == "u16":
            swap(ptr, 2, count * size // 2)
        elif how == "u32":
            swap(ptr, 4, count * size // 4)
        else:  # vertices: one field at a time, as the attribute table describes them
            widths = _vertex_widths(data, size, endian)
            for i in range(count):
                at = ptr + i * size
                for width, ordered in widths:
                    if ordered:
                        swap(at, width, 1)
                    at += width
    return bytes(out)
