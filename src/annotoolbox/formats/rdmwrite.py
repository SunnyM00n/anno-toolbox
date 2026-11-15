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
from .rdmread import NO_INDEX, Animation, Box, ExtraStream, RdmFile, Skeleton, VertexElement


def _attrs(*rows: tuple[int, int, int, int]) -> tuple[VertexElement, ...]:
    return tuple(VertexElement(*r) for r in rows)


def pack_direction(x: float, y: float, z: float) -> bytes:
    """A normal, tangent or bitangent as a packed ``4b`` element: x, y, z in ``b / 127.5 - 1``, then 0."""
    return bytes(max(0, min(255, round((c + 1.0) * 127.5))) for c in (x, y, z)) + b"\0"


# Layouts as found in shipped 117 files (usage, type, subtype, count: rdmread.VertexElement)
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
class RdmSubmesh:
    index_start: int
    index_count: int
    material: int


@dataclass
class RdmMeshData:
    name: str
    vertices: bytes
    stride: int
    attributes: tuple[VertexElement, ...]  # the vertex layout, as ``rdmread`` reads it
    indices: list[int]
    materials: list[str]
    submeshes: list[RdmSubmesh]
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


class _Writer:
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
        self.fixups += [(base + off, target) for off, target in local_fixups]

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


def _header1(w: _Writer, size: int, lists: set[str]) -> None:
    """header1 with a pointer to each of the file's ``lists`` that is present (meshes, materials, skeletons,
    animations, extra streams)."""
    names = ("meta", "blob_to_mat", "skeletons", "animations", "extra_streams")
    ptrs = [(PTR, "export_info"), *((PTR, n if n in lists else None) for n in names)]
    w.struct_block("header1", [*ptrs, bytes(size - 24)])


def _lists(meshes: bool, materials: bool, skeletons: bool, animations: bool, extra: bool) -> set[str]:
    flags = {"meta": meshes, "blob_to_mat": materials, "skeletons": skeletons, "animations": animations}
    return {n for n, on in {**flags, "extra_streams": extra}.items() if on}


def _export_info(w: _Writer, source: str, exporter: str, placeholder: str) -> None:
    source, exporter = source or placeholder, exporter or placeholder
    parts = [(PTR, "export_name" if source else None), (PTR, "export_material" if exporter else None)]
    w.struct_block("export_info", [*parts, bytes(64)])
    w.text("export_name", source)
    w.text("export_material", exporter)


def _skeletons(w: _Writer, skeletons: list[Skeleton]) -> None:
    parts = []
    for si, s in enumerate(skeletons):
        if not s.joints:
            raise ValueError(f"skeleton {si} has no joints")
        parts += [(PTR, f"s{si}_joints"), struct.pack("<I", s.word), (PTR, f"s{si}_extra" if s.extra else None)]
        parts += [bytes(20)]
    w.struct_block("skeletons", parts, len(skeletons))
    for si, s in enumerate(skeletons):
        if s.extra:
            w.raw_block(f"s{si}_extra", s.extra, len(s.extra))
    for si, s in enumerate(skeletons):
        jparts = []
        for ji, j in enumerate(s.joints):
            parent = NO_INDEX if j.parent is None else j.parent
            body = struct.pack("<3f4fI6f", *j.position, *j.rotation, parent, *j.box_min, *j.box_max)
            jparts += [(PTR, f"s{si}_j{ji}_name" if j.name else None), body, bytes(24)]
        w.struct_block(f"s{si}_joints", jparts, len(s.joints))
        for ji, j in enumerate(s.joints):
            w.text(f"s{si}_j{ji}_name", j.name)


def _animations(w: _Writer, animations: list[Animation]) -> None:
    parts = []
    for ai, a in enumerate(animations):
        parts += [(PTR, f"a{ai}_name" if a.name else None), (PTR, f"a{ai}_tracks" if a.tracks else None)]
        parts += [struct.pack("<II", a.duration_ms, a.frame_rate), bytes(32)]
    w.struct_block("animations", parts, len(animations))
    for ai, a in enumerate(animations):
        w.text(f"a{ai}_name", a.name)
        if not a.tracks:
            continue
        tparts = [
            part
            for ti, t in enumerate(a.tracks)
            for part in (
                (PTR, f"a{ai}_t{ti}_joint" if t.joint else None),
                (PTR, f"a{ai}_t{ti}_keys" if t.keys else None),
                bytes(16),
            )
        ]
        w.struct_block(f"a{ai}_tracks", tparts, len(a.tracks))
        for ti, t in enumerate(a.tracks):
            w.text(f"a{ai}_t{ti}_joint", t.joint)
            if t.keys:
                payload = b"".join(struct.pack("<8f", *k.rotation, *k.position, k.time) for k in t.keys)
                w.raw_block(f"a{ai}_t{ti}_keys", payload, len(t.keys))


def _extra_streams(w: _Writer, streams: list[ExtraStream]) -> None:
    parts = []
    for k, s in enumerate(streams):
        w4, wc, w10, w14, _word = s.words
        parts += [(PTR, f"x{k}_desc"), struct.pack("<I", w4), (PTR, f"x{k}_data" if s.data else None)]
        parts += [struct.pack("<3I", wc, w10, w14), bytes(40)]
    w.struct_block("extra_streams", parts, len(streams))
    for k, s in enumerate(streams):
        w.struct_block(f"x{k}_desc", [(PTR, f"x{k}_elements"), struct.pack("<I", s.words[4]), bytes(16)])
        w.raw_block(f"x{k}_elements", b"".join(a.pack() for a in s.elements), len(s.elements))
        stride = sum(a.size for a in s.elements)
        if s.data:
            w.raw_block(f"x{k}_data", s.data, len(s.data) // stride if stride else 1)


def _mesh_blocks(w: _Writer, p: str, m: RdmMeshData, placeholder: str) -> None:
    """The blocks of one mesh record (``p`` prefixes their names): name, vertex description, index format,
    submeshes, vertices, indices."""
    wide = m.index_type == 5 if m.index_type is not None else max(m.indices) > 0xFFFF
    name = m.name or placeholder
    w.struct_block(p + "model_name", [(PTR, p + "model_name_str" if name else None), bytes(24)])
    w.text(p + "model_name_str", name)
    w.struct_block(p + "vert_id", [(PTR, p + "attributes"), struct.pack("<I", m.bones_per_vertex), bytes(16)])
    w.raw_block(p + "attributes", b"".join(a.pack() for a in m.attributes), len(m.attributes))
    w.raw_block(p + "unknown", struct.pack("<I", 5 if wide else 3) + bytes(16), 1)
    if m.submeshes:
        w.raw_block(
            p + "mesh_info",
            b"".join(struct.pack("<3I", s.index_start, s.index_count, s.material) + bytes(16) for s in m.submeshes),
            len(m.submeshes),
        )
    w.raw_block(p + "vertex", m.vertices, m.vertex_count)
    w.raw_block(p + "triangles", struct.pack(f"<{len(m.indices)}{'I' if wide else 'H'}", *m.indices), len(m.indices))


def _materials(w: _Writer, mesh: RdmMeshData, placeholder: str) -> None:
    if not mesh.materials:
        return
    textures = list(mesh.textures) + [""] * len(mesh.materials)
    names = [name or placeholder for name in mesh.materials]
    blob = []
    for i in range(len(mesh.materials)):
        blob += [(PTR, f"mat{i}"), bytes(24)]
    w.struct_block("blob_to_mat", blob, len(mesh.materials))
    for i in range(len(mesh.materials)):
        name_ptr = (PTR, f"mat{i}_name" if names[i] else None)
        w.struct_block(f"mat{i}", [name_ptr, (PTR, f"mat{i}_tex" if textures[i] else None), bytes(40)])
    for i, name in enumerate(names):
        w.text(f"mat{i}_name", name)
        w.text(f"mat{i}_tex", textures[i])


def write_rdm(
    mesh: RdmMeshData,
    *more: RdmMeshData,
    skeletons: list[Skeleton] | tuple[Skeleton, ...] = (),
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
    for m in meshes:
        if m.vertex_count * m.stride != len(m.vertices) or not m.vertices:
            raise ValueError("vertex buffer is not a whole number of vertices")
        if not m.indices or (complete and not m.submeshes):
            raise ValueError("mesh needs indices and a submesh")
        if m.skeleton is not None and m.skeleton >= len(skeletons):
            raise ValueError(f"mesh {m.name!r} names skeleton {m.skeleton} of {len(skeletons)}")
    if complete and not mesh.materials:
        raise ValueError("mesh needs a material")
    w = _Writer()
    lists = _lists(True, bool(mesh.materials), bool(skeletons), bool(animations), bool(extra_streams))
    _header1(w, HEADER1_SIZE, lists)
    _export_info(w, mesh.export_name, mesh.export_material, placeholder)
    prefixes = ["", *(f"m{k}_" for k in range(1, len(more) + 1))]
    meta = []
    for p, m in zip(prefixes, meshes, strict=True):
        meta += [(PTR, p + "model_name"), (PTR, p + "vert_id"), (PTR, p + "unknown"), (PTR, p + "vertex")]
        meta += [
            (PTR, p + "triangles"),
            (PTR, p + "mesh_info" if m.submeshes else None),
            struct.pack("<I", NO_INDEX if m.skeleton is None else m.skeleton),
        ]
        meta += [struct.pack("<6f", *m.bbox), bytes(40)]
    w.struct_block("meta", meta, len(prefixes))
    for p, m in zip(prefixes, meshes, strict=True):
        _mesh_blocks(w, p, m, placeholder)
    _materials(w, mesh, placeholder)
    if skeletons:
        _skeletons(w, list(skeletons))
    if animations:
        _animations(w, list(animations))
    if extra_streams:
        _extra_streams(w, list(extra_streams))
    return w.finish()


def write_file(rdm: RdmFile, placeholder: str = "") -> bytes:
    """An ``RdmFile`` as ``rdmread`` gives it, written back: every list as it is, the file's materials on the
    first mesh (little-endian; a mesh without submeshes or materials is written without them only where the
    writer allows it). ``read_rdm(write_file(f))`` equals ``f`` in every field the games read."""
    if not rdm.meshes:
        return write_animation_rdm(rdm.animations, rdm.source, rdm.exporter, rdm.skeletons, placeholder)
    meshes = []
    for m in rdm.meshes:
        subs = [RdmSubmesh(s.first_index, s.index_count, s.material) for s in m.submeshes]
        data = RdmMeshData(m.name, m.vertices, m.stride, m.elements, m.indices, [], subs, m.bbox, rdm.source)
        data.export_material, data.skeleton, data.bones_per_vertex = rdm.exporter, m.skeleton, m.bones_per_vertex
        data.index_type = m.index_type
        meshes.append(data)
    meshes[0].materials = [x.name for x in rdm.materials]
    meshes[0].textures = [x.texture for x in rdm.materials]
    return write_rdm(
        *meshes,
        skeletons=rdm.skeletons,
        animations=rdm.animations,
        extra_streams=rdm.extra_streams,
        placeholder=placeholder,
        complete=False,
    )


def write_animation_rdm(
    animations: list[Animation],
    source: str = "",
    exporter: str = "",
    skeletons: list[Skeleton] | tuple = (),
    placeholder: str = "x",
) -> bytes:
    """An animation file: the export info and the animations (2070 also ships a file of two with a skeleton
    and no mesh), what 2070 and 117 ship. ``placeholder`` as for ``write_rdm``."""
    if not animations:
        raise ValueError("an animation file needs an animation")
    w = _Writer()
    _header1(w, HEADER1_SIZE, _lists(False, False, bool(skeletons), True, False))
    _export_info(w, source, exporter, placeholder)
    if skeletons:
        _skeletons(w, list(skeletons))
    _animations(w, animations)
    return w.finish()
