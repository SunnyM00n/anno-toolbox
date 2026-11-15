"""Synthetic .rdm files built byte by byte, independent of the writer under test (shapes as 2070 ships them)."""

import struct

from annotoolbox.formats.rdmio import HEADER1_OFFSET, RDM_MAGIC


class _FlatBuilder:
    """Assemble an RDM-like file block by block, resolving pointers by name."""

    def __init__(self) -> None:
        self.buf = bytearray(RDM_MAGIC + b"\0" * (0x14 - len(RDM_MAGIC)))
        self.fixups: list[tuple[int, str]] = []
        self.blocks: dict[str, int] = {}

    def block(self, name: str, payload: bytes, count: int = 1) -> int:
        self.buf += struct.pack("<II", count, len(payload) // count)
        self.blocks[name] = ptr = len(self.buf)
        self.buf += payload
        return ptr

    def ptr(self, target: str | None, at: int) -> bytes:
        if target is not None:
            self.fixups.append((at, target))
        return b"\0\0\0\0"

    def finish(self) -> bytes:
        for at, target in self.fixups:
            struct.pack_into("<I", self.buf, at, self.blocks[target])
        return bytes(self.buf)


def build_rdm(meta_size: int, vertices: list[tuple[float, float, float]]) -> bytes:
    b = _FlatBuilder()
    h1 = len(b.buf) + 8
    assert h1 == HEADER1_OFFSET
    b.block("header1", b.ptr("export", h1) + b.ptr("meta", h1 + 4) + b.ptr("blob_to_mat", h1 + 8) + b"\0" * 36)
    e = len(b.buf) + 8
    b.block("export", b.ptr("exp_name", e) + b"\0" * 68)
    b.block("exp_name", b"export\0", count=7)
    m = len(b.buf) + 8
    meta = (
        b.ptr("model_name", m) + b.ptr("vert_id", m + 4) + b"\0" * 4 + b.ptr("vertex", m + 12) + b"\0" * 8 + b"\xff" * 4
    )
    b.block("meta", meta + b"\0" * (meta_size - len(meta)))
    mn = len(b.buf) + 8
    b.block("model_name", b.ptr("name", mn) + b"\0" * 24)
    b.block("name", b"mesh\0", count=5)
    vi = len(b.buf) + 8
    b.block("vert_id", b.ptr("ids", vi) + b"\0" * 20)
    b.block("ids", b"\0" * 16, count=1)
    stride = 24
    b.block(
        "vertex",
        b"".join(struct.pack("<4e", x, y, z, 0) + b"\0" * (stride - 8) for x, y, z in vertices),
        count=len(vertices),
    )
    bm = len(b.buf) + 8
    b.block("blob_to_mat", b.ptr("mat", bm) + b"\0" * 24)
    mt = len(b.buf) + 8
    b.block("mat", b.ptr("mat_name", mt) + b"\0" * 44)
    b.block("mat_name", b"10 - Default", count=12)
    return b.finish()


# P4h N4b G4b B4b T2h I4b W4b
ELEMENTS = [(0, 6, 0, 4), (1, 5, 6, 1), (2, 5, 6, 1), (3, 5, 6, 1), (4, 6, 0, 2), (7, 5, 0, 1), (6, 5, 2, 1)]
STRIDE = 32
NO_PARENT = 0xFFFFFFFF


class Builder:
    """Lay out an RDM file block by block; a ``str`` part is a pointer to the block of that name."""

    def __init__(self) -> None:
        self.buf = bytearray(b"RDM\x01" + struct.pack("<IIII", 0x14, 0, 4, 0x1C))
        self.fixups: list[tuple[int, str]] = []
        self.blocks: dict[str, int] = {}

    def block(self, name: str, parts: list, count: int = 1) -> None:
        payload = bytearray()
        refs = []
        for part in parts:
            if isinstance(part, str):
                refs.append((len(payload), part))
                payload += b"\0\0\0\0"
            else:
                payload += part
        self.buf += struct.pack("<II", count, len(payload) // count)
        self.blocks[name] = start = len(self.buf)
        self.buf += payload
        self.fixups += [(start + at, target) for at, target in refs]

    def finish(self) -> bytes:
        for at, target in self.fixups:
            struct.pack_into("<I", self.buf, at, self.blocks[target])
        return bytes(self.buf)


def build_skinned(joints: list[tuple[str, int]], vertices: list[tuple[bytes, bytes]], word: int = 1, hashes=False):
    """A one-mesh skinned file: ``joints`` are (name, parent), ``vertices`` (bone indices, weights)."""
    b = Builder()
    b.block("header1", ["export", "mesh", bytes(4), "skin", bytes(32)])
    b.block("export", [b"\0" * 72])
    b.block(
        "mesh",
        ["mesh_name", "vert_id", "index_fmt", "vertices", "indices", "submeshes", struct.pack("<I", 0)]
        + [struct.pack("<6f", -1, -1, -1, 1, 1, 1), b"\0" * 40],
    )
    b.block("mesh_name", [b"\0" * 28])
    b.block("vert_id", ["elements", struct.pack("<I", word), b"\0" * 16])
    b.block("elements", [struct.pack("<4I", *e) for e in ELEMENTS], count=len(ELEMENTS))
    b.block("index_fmt", [struct.pack("<5I", 3, 0, 0, 0, 0)])
    rows = [
        struct.pack("<4e", float(i), 0, 0, 1) + b"\x80" * 12 + b"\0" * 4 + idx + w
        for i, (idx, w) in enumerate(vertices)
    ]
    b.block("vertices", rows, count=len(vertices))
    b.block("indices", [struct.pack("<3H", 0, 0, 0)], count=3)
    b.block("submeshes", [struct.pack("<3I", 0, 3, 0) + b"\0" * 16])
    b.block("skin", ["joints", "hashes" if hashes else b"\0" * 4, b"\0" * 24])
    b.block(
        "joints",
        [part for i, (_n, parent) in enumerate(joints) for part in (f"jname{i}", _joint_tail(i, parent))],
        count=len(joints),
    )
    if hashes:
        b.block("hashes", [struct.pack("<I", 1000 + i) for i in range(len(joints))], count=len(joints))
    for i, (name, _p) in enumerate(joints):
        raw = name.encode()
        b.block(f"jname{i}", [raw], count=len(raw))
    return b.finish()


def _joint_tail(i: int, parent: int) -> bytes:
    """Position marks the joint (x = its index), rotation identity, then the parent and padding."""
    return struct.pack("<3f4fI", float(i), 0, 0, 0, 0, 0, 1, parent) + b"\0" * 48
