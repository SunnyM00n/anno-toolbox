"""Write RDM files from scratch: meshes, skeletons and animations (the layout 117 and 2070 read).

Used by the mesh writers and the tests (which build their meshes with it). The block order
mirrors shipped files so every pointer points forward; the records are described in ``docs/formats/rdm.md``.
Every block is preceded by ``(count, item_size)``; pointers are absolute offsets of the block data and an
absent block is the pointer 0 (the game reads that as empty).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .rdmio import RDM_MAGIC
from .rdmread import NO_INDEX, Box, SAnimation, SExtraStream, SRdmFile, SSkeleton, SVertexElement


def _attrs(*rows: tuple[int, int, int, int]) -> tuple[SVertexElement, ...]:
    return tuple(SVertexElement(*row) for row in rows)


def pack_direction(x: float, y: float, z: float) -> bytes:
    """A normal, tangent or bitangent as a packed ``4b`` element: x, y, z in ``b / 127.5 - 1``, then 0."""
    return bytes(max(0, min(255, round((component + 1.0) * 127.5))) for component in (x, y, z)) + b"\0"


# Layouts as found in shipped 117 files (usage, type, subtype, count: rdmread.SVertexElement)
P4H, N4B, G4B, B4B, T2H = (0, 6, 0, 4), (1, 5, 6, 1), (2, 5, 6, 1), (3, 5, 6, 1), (4, 6, 0, 2)
C4B, C4B_2, C4C, I4B, W4B = (5, 5, 2, 1), (5, 5, 6, 1), (5, 5, 4, 1), (7, 5, 0, 1), (6, 5, 2, 1)
FORMAT_P4H = _attrs(P4H)
FORMAT_P4H_N4B_G4B_B4B_T2H = _attrs(P4H, N4B, G4B, B4B, T2H)
FORMAT_P4H_N4B_G4B_B4B_T2H_C4B = _attrs(P4H, N4B, G4B, B4B, T2H, C4B)
FORMAT_P4H_N4B_G4B_B4B_T2H_C4B_C4B = _attrs(P4H, N4B, G4B, B4B, T2H, C4B, C4B_2)
FORMAT_P4H_N4B_G4B_B4B_T2H_I4B_W4B = _attrs(P4H, N4B, G4B, B4B, T2H, I4B, W4B)
# 117 cloth: P3f N3f G3f B3f T2f + C4c (60 bytes), e.g. fashion_cloak_cloth.rdm; the logo variant has a second UV set
FORMAT_CLOTH_117 = _attrs((0, 7, 0, 3), (1, 7, 0, 3), (2, 7, 0, 3), (3, 7, 0, 3), (4, 7, 0, 2), C4C)
FORMAT_CLOTH_117_UV2 = _attrs((0, 7, 0, 3), (1, 7, 0, 3), (2, 7, 0, 3), (3, 7, 0, 3), (4, 7, 0, 2), (4, 7, 0, 2), C4C)
CLOTH_STRIDE = 60
HEADER1_SIZE = 0x30  # 117's mesh files have 0x34 (one more zero word); both readers zero-fill the difference


@dataclass
class SRdmSubmesh:
    index_start: int
    index_count: int
    material: int


@dataclass
class SRdmMeshData:
    name: str
    vertices: bytes
    stride: int
    attributes: tuple[SVertexElement, ...]  # the vertex layout, as ``rdmread`` reads it
    indices: list[int]
    materials: list[str]
    submeshes: list[SRdmSubmesh]
    bbox: Box
    export_name: str = "anno-toolbox"
    export_material: str = "cloth.rmp"
    skeleton: int | None = None  # index into the file's skeletons, None for a static mesh
    bones_per_vertex: int = 0  # the vertex description's word (117 vanilla: 0, 1, 3)
    textures: list[str] = field(default_factory=list)  # per material, the texture path string ("" for none)
    index_type: int | None = None  # 3 (16-bit) or 5 (32-bit); None: 5 only where an index needs it

    @property
    def vertex_count(self) -> int:
        return len(self.vertices) // self.stride


PTR = object()  # marker for a pointer field inside a struct description


class _CWriter:
    """Sequential block writer resolving named forward pointers at the end."""

    def __init__(self) -> None:
        # File header as shipped: magic, 0, 4, then the offset of header1 (0x1c).
        self.buf = bytearray(RDM_MAGIC + struct.pack("<III", 0, 4, 0x1C))
        self.blocks: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []

    def struct_block(self, name: str, parts: list, count: int = 1) -> None:
        """``parts``: bytes, or ``(PTR, target_block_name)`` for a pointer field (target None: absent)."""
        payload = bytearray()
        local_fixups: list[tuple[int, str]] = []
        for part in parts:
            if isinstance(part, tuple) and part[0] is PTR:
                if part[1] is not None:
                    local_fixups.append((len(payload), part[1]))
                payload += b"\0\0\0\0"
            else:
                payload += part
        self.raw_block(name, bytes(payload), count)
        base = self.blocks[name]
        self.fixups += [(base + offset, target) for offset, target in local_fixups]

    def raw_block(self, name: str, payload: bytes, count: int) -> None:
        if count <= 0 or len(payload) % count:
            raise ValueError(f"block {name}: payload of {len(payload)} bytes is not {count} equal items")
        self.buf += struct.pack("<II", count, len(payload) // count)
        self.blocks[name] = len(self.buf)
        self.buf += payload

    def text(self, name: str, text: str, empty: str | None = None) -> str | None:
        """A string block; returns its name, or None for an empty ``text`` (the pointer then stays 0)."""
        raw = (text or empty or "").encode("utf-8")
        if not raw:
            return None
        self.raw_block(name, raw, len(raw))
        return name

    def finish(self) -> bytes:
        for at, target in self.fixups:
            struct.pack_into("<I", self.buf, at, self.blocks[target])
        return bytes(self.buf)


def _header1(writer: _CWriter, size_bytes: int, lists: set[str]) -> None:
    """header1 with a pointer to each of the file's ``lists`` that is present (meshes, materials, skeletons,
    animations, extra streams)."""
    names = ("meta", "blob_to_mat", "skeletons", "animations", "extra_streams")
    ptrs = [(PTR, "export_info"), *((PTR, name if name in lists else None) for name in names)]
    writer.struct_block("header1", [*ptrs, bytes(size_bytes - 24)])


def _lists(meshes: bool, materials: bool, skeletons: bool, animations: bool, extra: bool) -> set[str]:
    flags = {"meta": meshes, "blob_to_mat": materials, "skeletons": skeletons, "animations": animations}
    return {name for name, on in {**flags, "extra_streams": extra}.items() if on}


def _export_info(writer: _CWriter, source: str, exporter: str, placeholder: str) -> None:
    source, exporter = source or placeholder, exporter or placeholder
    parts = [(PTR, "export_name" if source else None), (PTR, "export_material" if exporter else None)]
    writer.struct_block("export_info", [*parts, bytes(64)])
    writer.text("export_name", source)
    writer.text("export_material", exporter)


def _skeletons(writer: _CWriter, skeletons: list[SSkeleton]) -> None:
    parts = []
    for si, skeleton in enumerate(skeletons):
        if not skeleton.joints:
            raise ValueError(f"skeleton {si} has no joints")
        parts += [
            (PTR, f"s{si}_joints"),
            struct.pack("<I", skeleton.word),
            (PTR, f"s{si}_extra" if skeleton.extra else None),
        ]
        parts += [bytes(20)]
    writer.struct_block("skeletons", parts, len(skeletons))
    for si, skeleton in enumerate(skeletons):
        if skeleton.extra:
            writer.raw_block(f"s{si}_extra", skeleton.extra, len(skeleton.extra))
    for si, skeleton in enumerate(skeletons):
        jparts = []
        for ji, joint in enumerate(skeleton.joints):
            parent = NO_INDEX if joint.parent is None else joint.parent
            body = struct.pack("<3f4fI6f", *joint.position, *joint.rotation, parent, *joint.box_min, *joint.box_max)
            jparts += [(PTR, f"s{si}_j{ji}_name" if joint.name else None), body, bytes(24)]
        writer.struct_block(f"s{si}_joints", jparts, len(skeleton.joints))
        for ji, joint in enumerate(skeleton.joints):
            writer.text(f"s{si}_j{ji}_name", joint.name)


def _animations(writer: _CWriter, animations: list[SAnimation]) -> None:
    parts = []
    for ai, animation in enumerate(animations):
        parts += [
            (PTR, f"a{ai}_name" if animation.name else None),
            (PTR, f"a{ai}_tracks" if animation.tracks else None),
        ]
        parts += [struct.pack("<II", animation.duration_ms, animation.frame_rate), bytes(32)]
    writer.struct_block("animations", parts, len(animations))
    for ai, animation in enumerate(animations):
        writer.text(f"a{ai}_name", animation.name)
        if not animation.tracks:
            continue
        tparts = [
            part
            for ti, track in enumerate(animation.tracks)
            for part in (
                (PTR, f"a{ai}_t{ti}_joint" if track.joint else None),
                (PTR, f"a{ai}_t{ti}_keys" if track.keys else None),
                bytes(16),
            )
        ]
        writer.struct_block(f"a{ai}_tracks", tparts, len(animation.tracks))
        for ti, track in enumerate(animation.tracks):
            writer.text(f"a{ai}_t{ti}_joint", track.joint)
            if track.keys:
                payload = b"".join(struct.pack("<8f", *key.rotation, *key.position, key.time) for key in track.keys)
                writer.raw_block(f"a{ai}_t{ti}_keys", payload, len(track.keys))


def _extra_streams(writer: _CWriter, streams: list[SExtraStream]) -> None:
    parts = []
    for k, stream in enumerate(streams):
        w4, wc, w10, w14, _word = stream.words
        parts += [(PTR, f"x{k}_desc"), struct.pack("<I", w4), (PTR, f"x{k}_data" if stream.data else None)]
        parts += [struct.pack("<3I", wc, w10, w14), bytes(40)]
    writer.struct_block("extra_streams", parts, len(streams))
    for k, stream in enumerate(streams):
        writer.struct_block(f"x{k}_desc", [(PTR, f"x{k}_elements"), struct.pack("<I", stream.words[4]), bytes(16)])
        writer.raw_block(
            f"x{k}_elements", b"".join(element.pack() for element in stream.elements), len(stream.elements)
        )
        stride = sum(element.size for element in stream.elements)
        if stream.data:
            writer.raw_block(f"x{k}_data", stream.data, len(stream.data) // stride if stride else 1)


def _mesh_blocks(writer: _CWriter, prefix: str, mesh: SRdmMeshData, placeholder: str) -> None:
    """The blocks of one mesh record (``p`` prefixes their names): name, vertex description, index format,
    submeshes, vertices, indices."""
    wide = mesh.index_type == 5 if mesh.index_type is not None else max(mesh.indices) > 0xFFFF
    name = mesh.name or placeholder
    writer.struct_block(prefix + "model_name", [(PTR, prefix + "model_name_str" if name else None), bytes(24)])
    writer.text(prefix + "model_name_str", name)
    writer.struct_block(
        prefix + "vert_id", [(PTR, prefix + "attributes"), struct.pack("<I", mesh.bones_per_vertex), bytes(16)]
    )
    writer.raw_block(
        prefix + "attributes", b"".join(element.pack() for element in mesh.attributes), len(mesh.attributes)
    )
    writer.raw_block(prefix + "unknown", struct.pack("<I", 5 if wide else 3) + bytes(16), 1)
    if mesh.submeshes:
        writer.raw_block(
            prefix + "mesh_info",
            b"".join(
                struct.pack("<3I", submesh.index_start, submesh.index_count, submesh.material) + bytes(16)
                for submesh in mesh.submeshes
            ),
            len(mesh.submeshes),
        )
    writer.raw_block(prefix + "vertex", mesh.vertices, mesh.vertex_count)
    writer.raw_block(
        prefix + "triangles",
        struct.pack(f"<{len(mesh.indices)}{'I' if wide else 'H'}", *mesh.indices),
        len(mesh.indices),
    )


def _materials(writer: _CWriter, mesh: SRdmMeshData, placeholder: str) -> None:
    if not mesh.materials:
        return
    textures = list(mesh.textures) + [""] * len(mesh.materials)
    names = [name or placeholder for name in mesh.materials]
    blob = []
    for i in range(len(mesh.materials)):
        blob += [(PTR, f"mat{i}"), bytes(24)]
    writer.struct_block("blob_to_mat", blob, len(mesh.materials))
    for i in range(len(mesh.materials)):
        name_ptr = (PTR, f"mat{i}_name" if names[i] else None)
        writer.struct_block(f"mat{i}", [name_ptr, (PTR, f"mat{i}_tex" if textures[i] else None), bytes(40)])
    for i, name in enumerate(names):
        writer.text(f"mat{i}_name", name)
        writer.text(f"mat{i}_tex", textures[i])


def write_rdm(
    mesh: SRdmMeshData,
    *more: SRdmMeshData,
    skeletons: list[SSkeleton] | tuple[SSkeleton, ...] = (),
    animations=(),
    extra_streams=(),
    placeholder: str = "x",
    complete: bool = True,
) -> bytes:
    """One file holding ``mesh`` and the meshes in ``more``, with the ``skeletons`` the meshes name by index and
    any ``animations``; the materials and export strings are the first mesh's, and every submesh's material
    indexes that list. An empty name or export string is written as ``placeholder``; "" leaves it absent, which
    reads back as the empty string. ``complete`` wants a submesh in every mesh and a material (a game's file may
    lack them; ``write_file`` writes such a one as it is)."""
    meshes = (mesh, *more)
    for mesh_data in meshes:
        if mesh_data.vertex_count * mesh_data.stride != len(mesh_data.vertices) or not mesh_data.vertices:
            raise ValueError("vertex buffer is not a whole number of vertices")
        if not mesh_data.indices or (complete and not mesh_data.submeshes):
            raise ValueError("mesh needs indices and a submesh")
        if mesh_data.skeleton is not None and mesh_data.skeleton >= len(skeletons):
            raise ValueError(f"mesh {mesh_data.name!r} names skeleton {mesh_data.skeleton} of {len(skeletons)}")
    if complete and not mesh.materials:
        raise ValueError("mesh needs a material")
    writer = _CWriter()
    lists = _lists(True, bool(mesh.materials), bool(skeletons), bool(animations), bool(extra_streams))
    _header1(writer, HEADER1_SIZE, lists)
    _export_info(writer, mesh.export_name, mesh.export_material, placeholder)
    prefixes = ["", *(f"m{k}_" for k in range(1, len(more) + 1))]
    meta = []
    for prefix, mesh_data in zip(prefixes, meshes, strict=True):
        meta += [
            (PTR, prefix + "model_name"),
            (PTR, prefix + "vert_id"),
            (PTR, prefix + "unknown"),
            (PTR, prefix + "vertex"),
        ]
        meta += [
            (PTR, prefix + "triangles"),
            (PTR, prefix + "mesh_info" if mesh_data.submeshes else None),
            struct.pack("<I", NO_INDEX if mesh_data.skeleton is None else mesh_data.skeleton),
        ]
        meta += [struct.pack("<6f", *mesh_data.bbox), bytes(40)]
    writer.struct_block("meta", meta, len(prefixes))
    for prefix, mesh_data in zip(prefixes, meshes, strict=True):
        _mesh_blocks(writer, prefix, mesh_data, placeholder)
    _materials(writer, mesh, placeholder)
    if skeletons:
        _skeletons(writer, list(skeletons))
    if animations:
        _animations(writer, list(animations))
    if extra_streams:
        _extra_streams(writer, list(extra_streams))
    return writer.finish()


def write_file(rdm: SRdmFile, placeholder: str = "") -> bytes:
    """An ``SRdmFile`` as ``rdmread`` gives it, written back: every list as it is, the file's materials on the
    first mesh (little-endian; a mesh without submeshes or materials is written without them only where the
    writer allows it). ``read_rdm(write_file(f))`` equals ``f`` in every field the games read."""
    if not rdm.meshes:
        return write_animation_rdm(rdm.animations, rdm.source, rdm.exporter, rdm.skeletons, placeholder)
    meshes = []
    for rdm_mesh in rdm.meshes:
        submeshes = [
            SRdmSubmesh(submesh.first_index, submesh.index_count, submesh.material) for submesh in rdm_mesh.submeshes
        ]
        data = SRdmMeshData(
            rdm_mesh.name,
            rdm_mesh.vertices,
            rdm_mesh.stride,
            rdm_mesh.elements,
            rdm_mesh.indices,
            [],
            submeshes,
            rdm_mesh.bbox,
            rdm.source,
        )
        data.export_material, data.skeleton, data.bones_per_vertex = (
            rdm.exporter,
            rdm_mesh.skeleton,
            rdm_mesh.bones_per_vertex,
        )
        data.index_type = rdm_mesh.index_type
        meshes.append(data)
    meshes[0].materials = [material.name for material in rdm.materials]
    meshes[0].textures = [material.texture for material in rdm.materials]
    return write_rdm(
        *meshes,
        skeletons=rdm.skeletons,
        animations=rdm.animations,
        extra_streams=rdm.extra_streams,
        placeholder=placeholder,
        complete=False,
    )


def write_animation_rdm(
    animations: list[SAnimation],
    source: str = "",
    exporter: str = "",
    skeletons: list[SSkeleton] | tuple = (),
    placeholder: str = "x",
) -> bytes:
    """An animation file: the export info and the animations (2070 also ships a file of two with a skeleton
    and no mesh), what 2070 and 117 ship. ``placeholder`` as for ``write_rdm``."""
    if not animations:
        raise ValueError("an animation file needs an animation")
    writer = _CWriter()
    _header1(writer, HEADER1_SIZE, _lists(False, False, bool(skeletons), True, False))
    _export_info(writer, source, exporter, placeholder)
    if skeletons:
        _skeletons(writer, list(skeletons))
    _animations(writer, animations)
    return writer.finish()
