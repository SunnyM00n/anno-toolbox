"""Builders of RDM mesh files for the tests: static and skinned meshes, animations, a vertex layout by name, and the
same file in the big-endian order."""

from __future__ import annotations

import math
import struct

from annotoolbox.formats.rdmio import HEADER1_OFFSET, RDM_MAGIC
from annotoolbox.formats.rdmread import SAnimation, SJoint, SKeyframe, SSkeleton, STrack, SVertexElement
from annotoolbox.formats.rdmwrite import SRdmMeshData, SRdmSubmesh, pack_direction, write_animation_rdm, write_rdm

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


def u32_at(data: bytes, off: int, endian: str = ">") -> int:
    return struct.unpack_from(endian + "I", data, off)[0]


def _blocks(data: bytes, endian: str) -> dict[int, tuple[int, int, str]]:
    """Every block in the file: offset -> (count, item size, how to swap it)."""
    out: dict[int, tuple[int, int, str]] = {}

    def visit(ptr: int, kind: str | None) -> None:
        if ptr == 0 or ptr in out:
            return
        if ptr < 8 or ptr > len(data):
            raise RdmEndianError(f"pointer {ptr:#x} outside the file")
        count, size = u32_at(data, ptr - 8, endian), u32_at(data, ptr - 4, endian)
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
                    visit(u32_at(data, base + off, endian), target)

    visit(HEADER1_OFFSET, "header1")
    meta = u32_at(data, HEADER1_OFFSET + 4, endian)
    if meta:
        vertices = u32_at(data, meta + 4 * META_VERTICES, endian)
        if vertices in out:
            count, size, _ = out[vertices]
            out[vertices] = (count, size, "vertices")
    return out


def _vertex_widths(data: bytes, stride: int, endian: str) -> list[tuple[int, bool]]:
    """The width of every field of one vertex, from the mesh's attribute table."""
    meta = u32_at(data, HEADER1_OFFSET + 4, endian)
    table = u32_at(data, u32_at(data, meta + 4 * META_VERT_ID, endian), endian)
    count = u32_at(data, table - 8, endian)
    widths: list[tuple[int, bool]] = []
    for i in range(count):
        _, kind, _, n = struct.unpack_from(endian + "4I", data, table + ATTRIBUTE_SIZE * i)
        if kind not in KIND_FIELD:
            raise RdmEndianError(f"unknown vertex element kind {kind}")
        widths += [KIND_FIELD[kind]] * n
    if sum(w for w, _ in widths) != stride:
        raise RdmEndianError(f"vertex layout of {sum(w for w, _ in widths)} bytes against a stride of {stride}")
    return widths


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


def to_little_endian(data: bytes) -> bytes:
    """The same mesh with little-endian words (unchanged if it already is one)."""
    return data if not is_big_endian(data) else _swap(data, ">", LITTLE_ENDIAN_MAGIC)


def to_big_endian(data: bytes) -> bytes:
    """The other way round. Swapping is its own inverse, so this is what tests convert with."""
    return data if is_big_endian(data) else _swap(data, "<", BIG_ENDIAN_MAGIC)


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


def _joint_tail(i: int, parent: int) -> bytes:
    """Position marks the joint (x = its index), rotation identity, then the parent and padding."""
    return struct.pack("<3f4fI", float(i), 0, 0, 0, 0, 0, 1, parent) + b"\0" * 48


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


P4H, N4B, G4B, B4B, T2H = (0, 6, 0, 4), (1, 5, 6, 1), (2, 5, 6, 1), (3, 5, 6, 1), (4, 6, 0, 2)


C4B, C4B_2, I4B, W4B = (5, 5, 2, 1), (5, 5, 6, 1), (7, 5, 0, 1), (6, 5, 2, 1)


def layout(*rows) -> tuple[SVertexElement, ...]:
    return tuple(SVertexElement(*r) for r in rows)


def quad_grid(n: int) -> tuple[list[tuple[float, float, float]], list[int]]:
    """An n x n sheet of quads, a little bent so its normals differ."""
    pos = [(x * 0.5, 0.1 * math.sin(x + z), z * 0.5) for z in range(n) for x in range(n)]
    pos[0] = (-0.0, pos[0][1], -0.0)  # a negative zero, which a mirror must keep
    tri = []
    for z in range(n - 1):
        for x in range(n - 1):
            a, b, c, d = z * n + x, z * n + x + 1, (z + 1) * n + x, (z + 1) * n + x + 1
            tri += [a, c, b, b, c, d]
    return pos, tri


def _row(p, k: int, *, colours: int = 0, uv2: bool = False, bones=None, weights=None) -> bytes:
    out = struct.pack("<4e", *p, 1.0 if k % 2 else 0.0)  # a 4th position value that varies
    out += pack_direction(0.1, 0.9734, 0.2059)[:3] + bytes((k % 3,))  # a unit normal; a 4th byte that varies
    out += pack_direction(0.99, -0.05, 0.1) + pack_direction(0.0, 0.2, 0.98)
    out += struct.pack("<2e", p[0] / 4, p[2] / 4)
    if uv2:
        out += struct.pack("<2e", 0.5 + p[0], 0.25 - p[2])
    for c in range(colours):
        out += bytes(((k * 7 + c) % 256, 128, 255 - k % 256, 200 + c))
    if bones is not None:
        out += bytes(bones) + bytes(weights)
    return out


def static_meshes() -> list[SRdmMeshData]:
    pos, tri = quad_grid(4)
    lay_a = layout(P4H, N4B, G4B, B4B, T2H, C4B, C4B_2)
    rows_a = [_row(p, k, colours=2) for k, p in enumerate(pos)]
    a = SRdmMeshData("house_lod0", b"".join(rows_a), 32, lay_a, tri, ["roof", "wall"], [], (0.0,) * 6, "scene.max")
    a.submeshes = [SRdmSubmesh(0, 30, 1), SRdmSubmesh(30, len(tri) - 30, 0)]
    a.bbox = (-0.5, -0.25, -0.5, 2.0, 0.5, 2.0)  # looser than the vertices', as some shipped boxes are
    a.export_material, a.textures = "Tangent.rmp", ["data/roof_diff.psd", ""]
    pos_b, tri_b = quad_grid(3)
    lay_b = layout(P4H, N4B, G4B, B4B, T2H, (4, 6, 0, 2))
    rows_b = [_row(p, k, uv2=True) for k, p in enumerate(pos_b)]
    box_b = (0.0, -0.1, 0.0, 1.0, 0.1, 1.0)
    b = SRdmMeshData("", b"".join(rows_b), 28, lay_b, tri_b, [], [SRdmSubmesh(0, len(tri_b), 0)], box_b)
    return [a, b]


def static_file() -> bytes:
    """Two meshes (the second without a name), two materials, one with a texture path; written without placeholders."""
    return write_rdm(*static_meshes(), placeholder="")


def one_mesh_file() -> bytes:
    """The first mesh of ``static_file`` alone (the tests' byte-order helper swaps a file of one mesh)."""
    return write_rdm(static_meshes()[0], placeholder="")


def _turned(angle: float, axis=(0.0, 1.0, 0.0)) -> tuple[float, float, float, float]:
    s = math.sin(angle / 2)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(angle / 2))


def skeletons() -> list[SSkeleton]:
    def joint(name, parent, pos, angle, box=0.2):
        return SJoint(name, pos, _turned(angle), parent, (-box, -box, -box), (box, 2 * box, box))

    first = SSkeleton([joint("root", None, (0.1, 0.0, -0.2), 0.3), joint("arm", 0, (-1.0, -0.5, 0.0), -0.7)], 0)
    first.joints.append(joint("hand", 1, (-2.0, -0.5, 0.1), 1.1))
    second = SSkeleton([joint("door", None, (3.0, 0.0, 0.0), 0.0, 0.0)], 1)
    return [first, second]


def skinned_meshes() -> list[SRdmMeshData]:
    pos, tri = quad_grid(3)
    lay = layout(P4H, N4B, G4B, B4B, T2H, I4B, W4B)
    rows = []
    for k, p in enumerate(pos):
        bones = (k % 3, (k + 1) % 3, 0, 2)
        weights = (120, 100, 0, 35) if k % 2 else (255, 0, 0, 0)
        rows.append(_row(p, k, bones=bones, weights=weights))
    body = SRdmMeshData(
        "crane_lod0", b"".join(rows), 32, lay, tri, ["crane"], [SRdmSubmesh(0, len(tri), 0)], (0.0,) * 6
    )
    body.skeleton, body.bones_per_vertex, body.export_name = 0, 4, "crane.max"
    body.bbox = (0.0, -0.1, 0.0, 1.0, 0.1, 1.0)
    rigid_pos, rigid_tri = quad_grid(2)
    rigid_lay = layout(P4H, N4B, T2H)
    rigid_rows = [
        struct.pack("<4e", *p, 0.0) + pack_direction(0, 1, 0) + struct.pack("<2e", 0.5, 0.5) for p in rigid_pos
    ]
    rigid = SRdmMeshData(
        "door_lod0", b"".join(rigid_rows), 16, rigid_lay, rigid_tri, [], [SRdmSubmesh(0, 6, 0)], (0.0,) * 6
    )
    rigid.skeleton = 1
    rigid.bbox = (0.0, 0.0, 0.0, 0.5, 0.0, 0.5)
    return [body, rigid]


def skinned_file() -> bytes:
    return write_rdm(*skinned_meshes(), skeletons=skeletons(), placeholder="")


def rdm_animations() -> list[SAnimation]:
    def keys(times, angle, move):
        return [SKeyframe(_turned(angle * t, (0.0, 0.6, 0.8)), (move * t, 0.25, -move), t) for t in times]

    work = SAnimation("crane_lod0", 2000, 30, [STrack("root", keys((0.0, 1.0, 2.0), 0.4, 0.5))])
    work.tracks += [STrack("arm", keys((0.0, 0.5, 2.0), -0.9, 0.0)), STrack("Point001", keys((0.0, 2.0), 0.1, 1.0))]
    work.tracks.append(STrack("hand", []))  # an empty track: kept
    work.tracks.append(STrack("arm", keys((0.0, 2.0), 0.2, 0.1)))  # a second track on one joint, as 117 ships
    door = SAnimation("door_lod0", 1000, 60, [STrack("door", keys((0.0, 1.0), 1.5, 0.0))])
    return [work, door]


def animation_file() -> bytes:
    """Two entries, one per model it moves (as 2070 ships), one track on no joint of the mesh, one without keys."""
    return write_animation_rdm(rdm_animations(), "crane.max", "anim.rmp", placeholder="")


C4C, I4B, W4B = (5, 5, 4, 1), (7, 5, 0, 1), (6, 5, 2, 1)


IDENTITY_ROTATION = (0.0, 0.0, 0.0, 1.0)


def attrs(*rows):
    return tuple(SVertexElement(*r) for r in rows)


def vertex(i: int, *, normal=(10, 20, 30, 0), tangent=None, uv=(0.25, 0.5), colour=None, bones=None, weights=None):
    out = struct.pack("<4e", float(i), 2.0 * i, -1.0, 0.0) + bytes(normal)
    if tangent is not None:
        out += bytes(tangent) * 1 + bytes((7, 8, 9, 0))
    out += struct.pack("<2e", *uv)
    for extra in (colour, bones, weights):
        if extra is not None:
            out += bytes(extra)
    return out


def mesh_data(name="part", *, layout, rows, skeleton=None, bones=0, materials=("wood",), submeshes=None):
    n = len(rows)
    stride = len(rows[0])
    return SRdmMeshData(
        name,
        b"".join(rows),
        stride,
        attrs(*layout),
        [0, 1, 2, 2, 1, min(3, n - 1)],
        list(materials),
        submeshes or [SRdmSubmesh(0, 6, 0)],
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),  # stored as the early files have it: the real box comes later
        "scene.max",
        "Tangent.rmp",
        skeleton=skeleton,
        bones_per_vertex=bones,
    )


def joint(name, parent, x=0.0):
    return SJoint(name, (x, 0.0, 0.0), IDENTITY_ROTATION, parent, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


SKIN_LAYOUT = (P4H, N4B, T2H, I4B, W4B)


def rigid_parts_file():
    """2070's way: three rigid parts of one file, one skeleton each (the airport), the last also weighted."""
    sk = [
        SSkeleton([joint("root", None), joint("arm", 0, 1.0)], 0),
        SSkeleton([joint("part1", None, 2.0)], 1),
        SSkeleton([joint("part2", None, 3.0), joint("hand", 0, 4.0)], 2),
    ]
    rows0 = [vertex(i, bones=(i % 2, 0, 0, 0), weights=(255, 0, 0, 0)) for i in range(4)]
    rows1 = [vertex(i) for i in range(4)]  # no bone elements at all
    rows2 = [vertex(i, bones=(0, 1, 0, 0), weights=(100, 155, 0, 0)) for i in range(4)]
    lay_rigid = (P4H, N4B, T2H)
    meshes = [
        mesh_data("a", layout=SKIN_LAYOUT, rows=rows0, skeleton=0, bones=1),
        mesh_data("b", layout=lay_rigid, rows=rows1, skeleton=1),
        mesh_data("c", layout=SKIN_LAYOUT, rows=rows2, skeleton=2, bones=2),
    ]
    return write_rdm(*meshes, skeletons=sk)
