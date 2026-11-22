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


class CRdmReadError(ValueError):
    pass


@dataclass(frozen=True)
class SVertexElement:
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
class SSubmesh:
    first_index: int
    index_count: int
    material: int


@dataclass
class SMesh:
    name: str
    elements: tuple[SVertexElement, ...]
    bones_per_vertex: int  # the vertex description's word
    index_type: int
    vertex_count: int
    stride: int  # the vertex block's item size, as stored
    vertices: bytes  # little-endian
    indices: list[int]
    submeshes: list[SSubmesh]
    skeleton: int | None
    bbox: Box  # as stored; zeros in the short early record
    record_size: int  # 0x5c, or 0x44 for the early meshes without a box

    @property
    def format(self) -> str:
        return "_".join(element.code for element in self.elements)

    @property
    def format_stride(self) -> int:
        return sum(element.size for element in self.elements)

    def offsets(self) -> dict[str, list[int]]:
        """Element code -> byte offsets of each element with that code inside a vertex, in order."""
        out: dict[str, list[int]] = {}
        at = 0
        for element in self.elements:
            out.setdefault(element.code, []).append(at)
            at += element.size
        return out

    def element(self, index: int) -> list[tuple]:
        """The values of ``elements[index]`` for every vertex: floats for half and float types, the four
        bytes of a packed vector, integers for 16-bit types."""
        vertex_element = self.elements[index]
        at = sum(x.size for x in self.elements[:index])
        struct_format = {
            5: "4B",
            6: f"{vertex_element.count}e",
            7: f"{vertex_element.count}f",
            2: f"{vertex_element.count}H",
            3: f"{vertex_element.count}h",
        }.get(vertex_element.type)
        if struct_format is None:
            struct_format = f"{vertex_element.size}B"
        return [
            struct.unpack_from("<" + struct_format, self.vertices, vertex * self.stride + at)
            for vertex in range(self.vertex_count)
        ]

    def element_at(self, usage: int) -> int | None:
        """The index of the first element of ``usage`` (0 position, 1 normal, 4 UV, 5 colour, 6 weights, 7 bones)."""
        return next((i for i, element in enumerate(self.elements) if element.usage == usage), None)

    def column(self, usage: int) -> list[tuple] | None:
        """``element`` of the first element of ``usage``, None without one."""
        at = self.element_at(usage)
        return self.element(at) if at is not None else None

    def positions(self) -> list[Vec3]:
        for i, element in enumerate(self.elements):
            if element.usage == 0 and element.type in (6, 7) and element.count >= 3:
                return [(point[0], point[1], point[2]) for point in self.element(i)]
        return []

    def computed_bounds(self) -> Box | None:
        """The box of the positions (min x, y, z, max x, y, z), or None for a mesh without positions."""
        points = self.positions()
        if not points:
            return None
        low = [min(point[k] for point in points) for k in range(3)]
        high = [max(point[k] for point in points) for k in range(3)]
        return (low[0], low[1], low[2], high[0], high[1], high[2])


@dataclass
class SMaterial:
    name: str
    texture: str


@dataclass
class SJoint:
    name: str
    position: Vec3  # the inverse bind pose, as the rotation
    rotation: Quat  # quaternion x, y, z, w
    parent: int | None
    box_min: Vec3
    box_max: Vec3


@dataclass
class SSkeleton:
    joints: list[SJoint]
    word: int  # the record's +4: a small index in 2070's files, 117 reads a block offset there
    extra: bytes = b""  # the optional byte block at +8 (empty in every shipped 2070 file)


@dataclass
class SKeyframe:
    rotation: Quat
    position: Vec3
    time: float  # seconds


@dataclass
class STrack:
    joint: str
    keys: list[SKeyframe]


@dataclass
class SAnimation:
    name: str  # the model it moves (2070: the mesh name, ``..._lod0``)
    duration_ms: int
    frame_rate: int
    tracks: list[STrack]


@dataclass
class SExtraStream:
    """header1's sixth list: a second vertex stream (no shipped 2070 file has one); fields kept raw."""

    elements: tuple[SVertexElement, ...]
    words: tuple[int, int, int, int, int]  # record +4, +0xc, +0x10, +0x14 and the vertex description's word
    data: bytes


@dataclass
class SRdmFile:
    big_endian: bool
    source: str  # export info: the two strings
    exporter: str
    meshes: list[SMesh] = field(default_factory=list)
    materials: list[SMaterial] = field(default_factory=list)
    skeletons: list[SSkeleton] = field(default_factory=list)
    animations: list[SAnimation] = field(default_factory=list)
    extra_streams: list[SExtraStream] = field(default_factory=list)


class _CReader:
    """The file's bytes with the block rules of the engine (rdm.md "Container"): blocks, records, strings."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 0x14 or data[:3] != MAGIC:
            raise CRdmReadError("not an RDM file")
        self.data = data
        self.big = data[3] != 1
        self.endian = ">" if self.big else "<"
        if struct.unpack_from(self.endian + "3I", data, 4) != HEADER_WORDS:
            raise CRdmReadError("unexpected RDM header words")

    def u32(self, at: int) -> int:
        return struct.unpack_from(self.endian + "I", self.data, at)[0]

    def block(self, ref: int) -> tuple[int, int]:
        """``(count, item size)`` of the block at ``ref``; ``(0, 0)`` for "absent" (offset <= 8 or outside)."""
        if ref <= 8 or ref >= len(self.data):
            return 0, 0
        count, size_bytes = self.u32(ref - 8), self.u32(ref - 4)
        # items of 0 bytes: the count alone must not set how long a reader loops
        if ref + count * size_bytes > len(self.data) or (not size_bytes and count > len(self.data)):
            raise CRdmReadError(f"block at {ref:#x} ({count} x {size_bytes}) runs past the end")
        return count, size_bytes

    def records(self, ref: int, struct_size: int) -> list[bytes]:
        """Each item of a block, cut or zero-filled to ``struct_size`` (read its fields with ``words``)."""
        count, size_bytes = self.block(ref)
        out = []
        for i in range(count):
            raw = self.data[ref + i * size_bytes : ref + i * size_bytes + min(size_bytes, struct_size)]
            out.append(raw + bytes(struct_size - len(raw)))
        return out

    def words(self, record: bytes, count: int, at: int = 0) -> tuple[int, ...]:
        return struct.unpack_from(f"{self.endian}{count}I", record, at)

    def floats(self, record: bytes, count: int, at: int) -> tuple[float, ...]:
        return struct.unpack_from(f"{self.endian}{count}f", record, at)

    def vec3(self, record: bytes, at: int) -> Vec3:
        x, y, z = self.floats(record, 3, at)
        return (x, y, z)

    def quat(self, record: bytes, at: int) -> Quat:
        x, y, z, scalar = self.floats(record, 4, at)
        return (x, y, z, scalar)

    def string(self, ref: int) -> str:
        count, size_bytes = self.block(ref)
        if size_bytes == 1 or not count:
            return self.data[ref : ref + count].decode("utf-8", "replace")
        return bytes(self.data[ref + i * size_bytes] for i in range(count)).decode("utf-8", "replace")

    def string_ref(self, ref: int) -> str:
        recs = self.records(ref, STRING_REF)
        return self.string(self.words(recs[0], 1)[0]) if recs else ""

    def vertex_desc(self, ref: int) -> tuple[tuple[SVertexElement, ...], int]:
        recs = self.records(ref, VERTEX_DESC)
        if not recs:
            return (), 0
        table, word = self.words(recs[0], 2)
        elements = tuple(SVertexElement(*self.words(record, 4)) for record in self.records(table, 16))
        for element in elements:
            if element.type >= len(TYPE_SIZES):
                raise CRdmReadError(f"unknown vertex element type {element.type}")
            if element.size > len(self.data):  # the count would make a list longer than the file
                raise CRdmReadError(f"vertex element of {element.count} components")
        return elements, word

    def little(self, raw: bytes, widths: list[int], count: int, item: int) -> bytes:
        """``count`` items of ``item`` bytes with their fields (``widths``) in little-endian order."""
        if not self.big:
            return raw
        out = bytearray(raw)
        for i in range(count):
            at = i * item
            for field_size in widths:
                if field_size > 1 and at + field_size <= len(out):
                    out[at : at + field_size] = out[at : at + field_size][::-1]
                at += field_size
        return bytes(out)


def _widths(elements: tuple[SVertexElement, ...]) -> list[int]:
    return [TYPE_SIZES[element.type] for element in elements for _ in range(element.count)]


def _mesh(reader: _CReader, record: bytes, size_bytes: int) -> SMesh:
    name_ref, desc_ref, index_ref, vert_ref, idx_ref, sub_ref, skeleton_index = reader.words(record, 7)
    elements, word = reader.vertex_desc(desc_ref)
    index_format = reader.records(index_ref, INDEX_FORMAT)
    index_type = reader.words(index_format[0], 1)[0] if index_format else 3
    if index_type >= len(TYPE_SIZES) or TYPE_SIZES[index_type] not in (2, 4):
        raise CRdmReadError(f"unsupported index type {index_type}")
    count, stride = reader.block(vert_ref)
    vertices = reader.little(reader.data[vert_ref : vert_ref + count * stride], _widths(elements), count, stride)
    index_count, index_item_size = reader.block(idx_ref)
    index_item_bytes = TYPE_SIZES[index_type]
    if index_count and index_item_size != index_item_bytes:
        raise CRdmReadError(f"index block of {index_item_size}-byte items against index type {index_type}")
    index_fmt = f"{reader.endian}{index_count}{'H' if index_item_bytes == 2 else 'I'}"
    indices = list(struct.unpack_from(index_fmt, reader.data, idx_ref)) if index_count else []
    submeshes = [SSubmesh(*reader.words(submesh_record, 3)) for submesh_record in reader.records(sub_ref, SUBMESH)]
    return SMesh(
        name=reader.string_ref(name_ref),
        elements=elements,
        bones_per_vertex=word,
        index_type=index_type,
        vertex_count=count,
        stride=stride,
        vertices=vertices,
        indices=indices,
        submeshes=submeshes,
        skeleton=None if skeleton_index == NO_INDEX else skeleton_index,
        bbox=(*reader.vec3(record, 0x1C), *reader.vec3(record, 0x28)),
        record_size=size_bytes,
    )


def _skeleton(reader: _CReader, record: bytes) -> SSkeleton:
    joints_ref, word, extra_ref = reader.words(record, 3)
    joints = []
    for joint_record in reader.records(joints_ref, JOINT):
        parent = reader.words(joint_record, 1, 0x20)[0]
        joints.append(
            SJoint(
                name=reader.string(reader.words(joint_record, 1)[0]),
                position=reader.vec3(joint_record, 4),
                rotation=reader.quat(joint_record, 0x10),
                parent=None if parent == NO_INDEX else parent,
                box_min=reader.vec3(joint_record, 0x24),
                box_max=reader.vec3(joint_record, 0x30),
            )
        )
    count, size_bytes = reader.block(extra_ref)
    return SSkeleton(joints, word, reader.data[extra_ref : extra_ref + count * size_bytes] if count else b"")


def _animation(reader: _CReader, record: bytes) -> SAnimation:
    name_ref, tracks_ref, duration, rate = reader.words(record, 4)
    tracks = []
    for track_record in reader.records(tracks_ref, TRACK):
        joint_ref, keys_ref = reader.words(track_record, 2)
        keys = []
        for key_record in reader.records(keys_ref, KEYFRAME):
            keys.append(
                SKeyframe(
                    reader.quat(key_record, 0), reader.vec3(key_record, 0x10), reader.floats(key_record, 1, 0x1C)[0]
                )
            )
        tracks.append(STrack(reader.string(joint_ref), keys))
    return SAnimation(reader.string(name_ref), duration, rate, tracks)


def _extra_stream(reader: _CReader, record: bytes) -> SExtraStream:
    desc_ref, w4, data_ref, wc, w10, w14 = reader.words(record, 6)
    elements, word = reader.vertex_desc(desc_ref)
    count, size_bytes = reader.block(data_ref)
    data = reader.little(reader.data[data_ref : data_ref + count * size_bytes], _widths(elements), count, size_bytes)
    return SExtraStream(elements, (w4, wc, w10, w14, word), data)


def read_rdm(data: bytes) -> SRdmFile:
    """Every list of an ``.rdm`` (a mesh file, an animation file or both)."""
    reader = _CReader(bytes(data))
    try:
        h1 = reader.records(reader.u32(0x10), HEADER1)
        if not h1:
            raise CRdmReadError("header1 missing")
        export_ref, mesh_ref, mat_ref, skel_ref, anim_ref, extra_ref = reader.words(h1[0], 6)
        export_info = reader.records(export_ref, EXPORT_INFO)
        source, exporter = (reader.string(x) for x in reader.words(export_info[0], 2)) if export_info else ("", "")
        out = SRdmFile(reader.big, source, exporter)
        mesh_size = reader.block(mesh_ref)[1]
        out.meshes = [_mesh(reader, record, mesh_size) for record in reader.records(mesh_ref, MESH)]
        for ref in reader.records(mat_ref, MATERIAL_REF):
            material_record = reader.records(reader.words(ref, 1)[0], MATERIAL)
            name, texture = (
                (reader.string(x) for x in reader.words(material_record[0], 2)) if material_record else ("", "")
            )
            out.materials.append(SMaterial(name, texture))
        out.skeletons = [_skeleton(reader, record) for record in reader.records(skel_ref, SKELETON)]
        out.animations = [_animation(reader, record) for record in reader.records(anim_ref, ANIMATION)]
        out.extra_streams = [_extra_stream(reader, record) for record in reader.records(extra_ref, EXTRA_STREAM)]
    except struct.error as error:
        raise CRdmReadError(f"truncated RDM file: {error}") from error
    return out
