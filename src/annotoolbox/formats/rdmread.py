"""Read a whole ``.rdm`` the way the 2070 engine does: meshes, materials, skeletons, animations, extra streams.

The format, its records and the engine's reading rules are in ``docs/formats/rdm.md``. Both byte orders are
read; vertex and index data come back little-endian. A record shorter than the engine's struct reads with
zeros at the end, a longer one is cut, exactly as the game copies it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"RDM"
HEADER_WORDS = (0x14, 0, 4)  # the u32s after the magic and the byte-order byte
LETTERS = "PNGBTCWI"  # element usage 0..7
TYPE_SIZES = (1, 1, 2, 2, 4, 4, 2, 4, 8, 4)  # bytes per component by field type (rdm.md "Field types")
NO_INDEX = 0xFFFFFFFF  # a static mesh's skeleton, a root joint's parent

# record sizes as the engine's structs (rdm.md "Records")
# fmt: off
HEADER1, EXPORT_INFO, MESH, STRING_REF, VERTEX_DESC, INDEX_FORMAT, SUBMESH = 0x30, 0x48, 0x5C, 0x1C, 0x18, 0x14, 0x1C
MATERIAL_REF, MATERIAL, SKELETON, JOINT, ANIMATION, TRACK, KEYFRAME, EXTRA_STREAM = 0x1C, 0x30, 0x20, 0x54, 0x30, 0x18, 0x20, 0x40
# fmt: on


Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]  # x, y, z, w
Box = tuple[float, float, float, float, float, float]  # min x, y, z, max x, y, z


class RdmReadError(ValueError):
    pass


@dataclass(frozen=True)
class VertexElement:
    """One entry of a vertex description (four u32s): ``usage`` indexes ``PNGBTCWI``, ``type`` the field types,
    ``subtype`` tells the packed four-byte kinds apart (rdm.md "Vertices"). The writer takes the same tuples."""

    usage: int
    type: int
    subtype: int
    count: int

    @property
    def size(self) -> int:
        return TYPE_SIZES[self.type] * self.count

    def pack(self) -> bytes:
        return struct.pack("<4I", self.usage, self.type, self.subtype, self.count)

    @property
    def code(self) -> str:
        """The engine's name: letter, count and kind (``N4b``; a packed byte vector always counts 4)."""
        letter = LETTERS[self.usage] if self.usage < len(LETTERS) else "?"
        if self.type == 5:
            return f"{letter}4{'c' if self.subtype in (3, 4, 5) else 'b'}"
        kind = {2: "s", 6: "h", 7: "f"}.get(self.type, "?")
        return f"{letter}{self.count}{kind}"


@dataclass
class Submesh:
    first_index: int
    index_count: int
    material: int


@dataclass
class Mesh:
    name: str
    elements: tuple[VertexElement, ...]
    bones_per_vertex: int  # the vertex description's word
    index_type: int
    vertex_count: int
    stride: int  # the vertex block's item size, as stored
    vertices: bytes  # little-endian
    indices: list[int]
    submeshes: list[Submesh]
    skeleton: int | None
    bbox: Box  # as stored; zeros in the short early record
    record_size: int  # 0x5c, or 0x44 for the early meshes without a box

    @property
    def format(self) -> str:
        return "_".join(e.code for e in self.elements)

    @property
    def format_stride(self) -> int:
        return sum(e.size for e in self.elements)

    def offsets(self) -> dict[str, list[int]]:
        """Element code -> byte offsets of each element with that code inside a vertex, in order."""
        out: dict[str, list[int]] = {}
        at = 0
        for e in self.elements:
            out.setdefault(e.code, []).append(at)
            at += e.size
        return out

    def element(self, index: int) -> list[tuple]:
        """The values of ``elements[index]`` for every vertex: floats for half and float types, the four
        bytes of a packed vector, integers for 16-bit types."""
        e = self.elements[index]
        at = sum(x.size for x in self.elements[:index])
        fmt = {5: "4B", 6: f"{e.count}e", 7: f"{e.count}f", 2: f"{e.count}H", 3: f"{e.count}h"}.get(e.type)
        if fmt is None:
            fmt = f"{e.size}B"
        return [struct.unpack_from("<" + fmt, self.vertices, v * self.stride + at) for v in range(self.vertex_count)]

    def positions(self) -> list[Vec3]:
        for i, e in enumerate(self.elements):
            if e.usage == 0 and e.type in (6, 7) and e.count >= 3:
                return [(p[0], p[1], p[2]) for p in self.element(i)]
        return []

    def computed_bounds(self) -> Box | None:
        """The box of the positions (min x, y, z, max x, y, z), or None for a mesh without positions."""
        pos = self.positions()
        if not pos:
            return None
        lo = [min(p[k] for p in pos) for k in range(3)]
        hi = [max(p[k] for p in pos) for k in range(3)]
        return (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])


@dataclass
class Material:
    name: str
    texture: str


@dataclass
class Joint:
    name: str
    position: Vec3  # the inverse bind pose, as the rotation
    rotation: Quat  # quaternion x, y, z, w
    parent: int | None
    box_min: Vec3
    box_max: Vec3


@dataclass
class Skeleton:
    joints: list[Joint]
    word: int  # the record's +4: a small index in 2070's files, 117 reads a block offset there
    extra: bytes = b""  # the optional byte block at +8 (empty in every shipped 2070 file)


@dataclass
class Keyframe:
    rotation: Quat
    position: Vec3
    time: float  # seconds


@dataclass
class Track:
    joint: str
    keys: list[Keyframe]


@dataclass
class Animation:
    name: str  # the model it moves (2070: the mesh name, ``..._lod0``)
    duration_ms: int
    frame_rate: int
    tracks: list[Track]


@dataclass
class ExtraStream:
    """header1's sixth list: a second vertex stream (no shipped 2070 file has one); fields kept raw."""

    elements: tuple[VertexElement, ...]
    words: tuple[int, int, int, int, int]  # record +4, +0xc, +0x10, +0x14 and the vertex description's word
    data: bytes


@dataclass
class RdmFile:
    big_endian: bool
    source: str  # export info: the two strings
    exporter: str
    meshes: list[Mesh] = field(default_factory=list)
    materials: list[Material] = field(default_factory=list)
    skeletons: list[Skeleton] = field(default_factory=list)
    animations: list[Animation] = field(default_factory=list)
    extra_streams: list[ExtraStream] = field(default_factory=list)


class _Reader:
    """The file's bytes with the block rules of the engine (rdm.md "Container"): blocks, records, strings."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 0x14 or data[:3] != MAGIC:
            raise RdmReadError("not an RDM file")
        self.data = data
        self.big = data[3] != 1
        self.endian = ">" if self.big else "<"
        if struct.unpack_from(self.endian + "3I", data, 4) != HEADER_WORDS:
            raise RdmReadError("unexpected RDM header words")

    def u32(self, off: int) -> int:
        return struct.unpack_from(self.endian + "I", self.data, off)[0]

    def block(self, ref: int) -> tuple[int, int]:
        """``(count, item size)`` of the block at ``ref``; ``(0, 0)`` for "absent" (offset <= 8 or outside)."""
        if ref <= 8 or ref >= len(self.data):
            return 0, 0
        count, size = self.u32(ref - 8), self.u32(ref - 4)
        if ref + count * size > len(self.data):
            raise RdmReadError(f"block at {ref:#x} ({count} x {size}) runs past the end")
        return count, size

    def records(self, ref: int, struct_size: int) -> list[bytes]:
        """Each item of a block, cut or zero-filled to ``struct_size`` (read its fields with ``words``)."""
        count, size = self.block(ref)
        out = []
        for i in range(count):
            raw = self.data[ref + i * size : ref + i * size + min(size, struct_size)]
            out.append(raw + bytes(struct_size - len(raw)))
        return out

    def words(self, rec: bytes, n: int, at: int = 0) -> tuple[int, ...]:
        return struct.unpack_from(f"{self.endian}{n}I", rec, at)

    def floats(self, rec: bytes, n: int, at: int) -> tuple[float, ...]:
        return struct.unpack_from(f"{self.endian}{n}f", rec, at)

    def vec3(self, rec: bytes, at: int) -> Vec3:
        x, y, z = self.floats(rec, 3, at)
        return (x, y, z)

    def quat(self, rec: bytes, at: int) -> Quat:
        x, y, z, w = self.floats(rec, 4, at)
        return (x, y, z, w)

    def string(self, ref: int) -> str:
        count, size = self.block(ref)
        if size == 1 or not count:
            return self.data[ref : ref + count].decode("utf-8", "replace")
        return bytes(self.data[ref + i * size] for i in range(count)).decode("utf-8", "replace")

    def string_ref(self, ref: int) -> str:
        recs = self.records(ref, STRING_REF)
        return self.string(self.words(recs[0], 1)[0]) if recs else ""

    def vertex_desc(self, ref: int) -> tuple[tuple[VertexElement, ...], int]:
        recs = self.records(ref, VERTEX_DESC)
        if not recs:
            return (), 0
        table, word = self.words(recs[0], 2)
        elements = tuple(VertexElement(*self.words(r, 4)) for r in self.records(table, 16))
        for el in elements:
            if el.type >= len(TYPE_SIZES):
                raise RdmReadError(f"unknown vertex element type {el.type}")
        return elements, word

    def little(self, raw: bytes, widths: list[int], count: int, item: int) -> bytes:
        """``count`` items of ``item`` bytes with their fields (``widths``) in little-endian order."""
        if not self.big:
            return raw
        out = bytearray(raw)
        for i in range(count):
            at = i * item
            for w in widths:
                if w > 1 and at + w <= len(out):
                    out[at : at + w] = out[at : at + w][::-1]
                at += w
        return bytes(out)


def _widths(elements: tuple[VertexElement, ...]) -> list[int]:
    return [TYPE_SIZES[e.type] for e in elements for _ in range(e.count)]


def _mesh(r: _Reader, rec: bytes, size: int) -> Mesh:
    name_ref, desc_ref, index_ref, vert_ref, idx_ref, sub_ref, skel = r.words(rec, 7)
    elements, word = r.vertex_desc(desc_ref)
    fmt = r.records(index_ref, INDEX_FORMAT)
    index_type = r.words(fmt[0], 1)[0] if fmt else 3
    if index_type >= len(TYPE_SIZES) or TYPE_SIZES[index_type] not in (2, 4):
        raise RdmReadError(f"unsupported index type {index_type}")
    count, stride = r.block(vert_ref)
    vertices = r.little(r.data[vert_ref : vert_ref + count * stride], _widths(elements), count, stride)
    n_idx, isize = r.block(idx_ref)
    width = TYPE_SIZES[index_type]
    if n_idx and isize != width:
        raise RdmReadError(f"index block of {isize}-byte items against index type {index_type}")
    index_fmt = f"{r.endian}{n_idx}{'H' if width == 2 else 'I'}"
    indices = list(struct.unpack_from(index_fmt, r.data, idx_ref)) if n_idx else []
    submeshes = [Submesh(*r.words(s, 3)) for s in r.records(sub_ref, SUBMESH)]
    return Mesh(
        name=r.string_ref(name_ref),
        elements=elements,
        bones_per_vertex=word,
        index_type=index_type,
        vertex_count=count,
        stride=stride,
        vertices=vertices,
        indices=indices,
        submeshes=submeshes,
        skeleton=None if skel == NO_INDEX else skel,
        bbox=(*r.vec3(rec, 0x1C), *r.vec3(rec, 0x28)),
        record_size=size,
    )


def _skeleton(r: _Reader, rec: bytes) -> Skeleton:
    joints_ref, word, extra_ref = r.words(rec, 3)
    joints = []
    for j in r.records(joints_ref, JOINT):
        parent = r.words(j, 1, 0x20)[0]
        joints.append(
            Joint(
                name=r.string(r.words(j, 1)[0]),
                position=r.vec3(j, 4),
                rotation=r.quat(j, 0x10),
                parent=None if parent == NO_INDEX else parent,
                box_min=r.vec3(j, 0x24),
                box_max=r.vec3(j, 0x30),
            )
        )
    count, size = r.block(extra_ref)
    return Skeleton(joints, word, r.data[extra_ref : extra_ref + count * size] if count else b"")


def _animation(r: _Reader, rec: bytes) -> Animation:
    name_ref, tracks_ref, duration, rate = r.words(rec, 4)
    tracks = []
    for t in r.records(tracks_ref, TRACK):
        joint_ref, keys_ref = r.words(t, 2)
        keys = []
        for k in r.records(keys_ref, KEYFRAME):
            keys.append(Keyframe(r.quat(k, 0), r.vec3(k, 0x10), r.floats(k, 1, 0x1C)[0]))
        tracks.append(Track(r.string(joint_ref), keys))
    return Animation(r.string(name_ref), duration, rate, tracks)


def _extra_stream(r: _Reader, rec: bytes) -> ExtraStream:
    desc_ref, w4, data_ref, wc, w10, w14 = r.words(rec, 6)
    elements, word = r.vertex_desc(desc_ref)
    count, size = r.block(data_ref)
    data = r.little(r.data[data_ref : data_ref + count * size], _widths(elements), count, size)
    return ExtraStream(elements, (w4, wc, w10, w14, word), data)


def read_rdm(data: bytes) -> RdmFile:
    """Every list of an ``.rdm`` (a mesh file, an animation file or both)."""
    r = _Reader(bytes(data))
    try:
        h1 = r.records(r.u32(0x10), HEADER1)
        if not h1:
            raise RdmReadError("header1 missing")
        export_ref, mesh_ref, mat_ref, skel_ref, anim_ref, extra_ref = r.words(h1[0], 6)
        info = r.records(export_ref, EXPORT_INFO)
        source, exporter = (r.string(x) for x in r.words(info[0], 2)) if info else ("", "")
        out = RdmFile(r.big, source, exporter)
        mesh_size = r.block(mesh_ref)[1]
        out.meshes = [_mesh(r, rec, mesh_size) for rec in r.records(mesh_ref, MESH)]
        for ref in r.records(mat_ref, MATERIAL_REF):
            mat = r.records(r.words(ref, 1)[0], MATERIAL)
            name, texture = (r.string(x) for x in r.words(mat[0], 2)) if mat else ("", "")
            out.materials.append(Material(name, texture))
        out.skeletons = [_skeleton(r, rec) for rec in r.records(skel_ref, SKELETON)]
        out.animations = [_animation(r, rec) for rec in r.records(anim_ref, ANIMATION)]
        out.extra_streams = [_extra_stream(r, rec) for rec in r.records(extra_ref, EXTRA_STREAM)]
    except struct.error as exc:
        raise RdmReadError(f"truncated RDM file: {exc}") from exc
    return out
