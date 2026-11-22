"""The relayout of 2070 ``.rdm`` files into 117's: static and skinned meshes, skeletons, animations.

No rdm4 and no glTF: the meshes are read with ``rdmread`` and written with ``rdmwrite``, every vertex element
copied byte for byte into the 117 layout. What it does besides copying is listed per file in ``CNotes``
(``filled``: data the source never had, ``approximated``, ``dropped``). Format and rules: docs/formats/rdm.md.
"""

from __future__ import annotations

import struct
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import rdmwrite
from ..formats.rdmlayout import direction, parse
from ..formats.rdmread import CRdmReadError, SAnimation, SJoint, SMesh, SRdmFile, SSkeleton, SVertexElement, read_rdm
from ..formats.rdmwrite import pack_direction
from . import meshxform, tangents

SKINNED_FORMAT = "P4h_N4b_G4b_B4b_T2h_I4b_W4b"  # all vanilla skinned meshes with weights are this (3,184 of 3,190)
STATIC_FORMAT = "P4h_N4b_G4b_B4b_T2h"
MAX_JOINTS = 256  # a bone index is one byte (index * 255 read back)
STATIC_JOINT = "static_part"  # the joint a mesh without a skeleton hangs on: no animation names it, so it never moves
_FORMATS = {
    "P4h": rdmwrite.FORMAT_P4H,
    STATIC_FORMAT: rdmwrite.FORMAT_P4H_N4B_G4B_B4B_T2H,
    STATIC_FORMAT + "_C4b": rdmwrite.FORMAT_P4H_N4B_G4B_B4B_T2H_C4B,
    STATIC_FORMAT + "_C4b_C4b": rdmwrite.FORMAT_P4H_N4B_G4B_B4B_T2H_C4B_C4B,
    SKINNED_FORMAT: rdmwrite.FORMAT_P4H_N4B_G4B_B4B_T2H_I4B_W4B,
}


class CNativeError(ValueError):
    """A file the native path cannot convert; the message says why."""


@dataclass
class CNotes:
    """What the relayout did besides copying (each entry names the mesh or joint and the reason)."""

    m_filled: list[str] = field(default_factory=list)  # data the target layout has and the source never had
    m_approximated: list[str] = field(default_factory=list)
    m_dropped: list[str] = field(default_factory=list)

    def extend(self, other: CNotes) -> None:
        self.m_filled += other.m_filled
        self.m_approximated += other.m_approximated
        self.m_dropped += other.m_dropped


@dataclass
class SRelaid:
    data: bytes
    format: str  # the 117 layout, as a material declares it
    bones: int  # bones per vertex of a skinned mesh (the material's NumBonesPerVertex), 0 for a static one
    meshes: int
    notes: CNotes
    joints: frozenset[str] = frozenset()  # a skinned file's joint names, which its animations' tracks name
    models: frozenset[str] = frozenset()  # its mesh names, which an animation entry may be named for


def static_format(source_format: str) -> str:
    """The 117 layout a static 2070 mesh is written in, from the layout its material declares: the cut-out helpers
    stay ``P4h`` (a vanilla layout), vertex colours are kept as ``C4b`` (up to two, as vanilla's plants), everything
    else is the tangent-frame layout. Never ``I``/``W``: a static build has no bones."""
    parts = source_format.split("_")
    if parts == ["P4h"]:
        return "P4h"
    colours = min(sum(1 for part in parts if part.startswith("C")), 2)
    return STATIC_FORMAT + "_C4b" * colours


# Vertex rows


@dataclass(frozen=True)
class _SElement:
    usage: int
    type: int
    subtype: int
    count: int
    offset: int
    size: int


def _elements(mesh: SMesh) -> list[_SElement]:
    out, offset = [], 0
    for element in mesh.elements:
        out.append(_SElement(element.usage, element.type, element.subtype, element.count, offset, element.size))
        offset += element.size
    return out


def _nth(elements: list[_SElement], usage: int, ordinal: int) -> _SElement | None:
    found = [element for element in elements if element.usage == usage]
    return found[ordinal] if ordinal < len(found) else None


Column = Callable[[bytes, int], bytes]  # a source row and its vertex number -> the target element's bytes


def _column(
    target: SVertexElement,
    source: _SElement | None,
    base: int,
    notes: CNotes,
    who: str,
    weights: _SElement | None = None,
    computed: dict[int, list[bytes]] | None = None,
) -> Column:
    """The function that makes target element ``a`` of a vertex from the source row (``computed``: the columns
    :func:`_frame_columns` made for the elements the source lacks, by usage)."""

    def copy(offset: int, length_bytes: int) -> Column:
        return lambda row, _vertex: row[offset : offset + length_bytes]

    def fill(value: bytes, what: str) -> Column:
        notes.m_filled.append(f"{who}: {what}")
        return lambda row, _vertex: value

    usage = target.usage
    if usage == 0:
        if source is None or source.usage != 0 or source.count < 3:
            raise CNativeError(f"{who}: no position")
        if source.type == 6 and source.count == 4:
            return copy(source.offset, 8)
        if source.type in (6, 7):
            kind = "e" if source.type == 6 else "f"
            offset, length = source.offset, source.count
            return lambda row, _vertex: struct.pack(
                "<4e", *struct.unpack_from(f"<{length}{kind}", row, offset)[:3], 0.0
            )
        raise CNativeError(f"{who}: position type {source.type}")
    if usage in (1, 2, 3):
        if source is not None and source.type == 5:
            return copy(source.offset, 4)
        if source is not None and source.type == 7 and source.count == 3:
            offset = source.offset
            return lambda row, _vertex: pack_direction(*struct.unpack_from("<3f", row, offset))
        column = (computed or {}).get(usage)
        if column is None:
            raise CNativeError(f"{who}: no normal or tangent frame to copy or derive")
        return lambda _x, vertex: column[vertex]
    if usage == 4:
        if source is not None and source.type == 6 and source.count == 2:
            return copy(source.offset, 4)
        if source is not None and source.type == 7 and source.count == 2:
            offset = source.offset
            return lambda row, _vertex: struct.pack("<2e", *struct.unpack_from("<2f", row, offset))
        return fill(bytes(4), "texture coordinate absent in the source")
    if usage == 5:
        if source is not None and source.type == 5:
            return copy(source.offset, 4)
        return fill(b"\xff\xff\xff\xff", "vertex colour absent in the source")
    if usage == 6:
        if source is not None and source.type == 5:
            return copy(source.offset, 4)
        return fill(b"\xff\0\0\0", "bone weights absent in the source (rigid: full weight)")
    if usage == 7:
        if source is not None and source.type == 5:
            offset = source.offset
            if base == 0:
                return copy(offset, 4)
            if weights is None:  # one bone per vertex: the first slot, the rest point at joint 0
                return lambda row, _vertex: bytes((row[offset] + base, 0, 0, 0))
            weight_offset = weights.offset  # a slot that weighs nothing points at joint 0, as vanilla's do
            return lambda row, _vertex: bytes(
                (row[offset + k] + base) if k == 0 or row[weight_offset + k] else 0 for k in range(4)
            )
        notes.m_filled.append(f"{who}: bone index absent in the source (rigid: joint {base})")
        return lambda _x, _vertex: bytes((base, 0, 0, 0))
    raise CNativeError(f"{who}: unknown element usage {usage}")


def _dropped(mesh: SMesh, attributes: tuple[SVertexElement, ...], notes: CNotes, who: str, bones: bool) -> None:
    want = Counter(attribute.usage for attribute in attributes)
    have = Counter(element.usage for element in mesh.elements)
    for usage, count in have.items():
        if count > want[usage] and not (bones and usage in (6, 7)):
            notes.m_dropped.append(
                f"{who}: {count - want[usage]} x element {'PNGBTCWI'[usage]} has no place in the target layout"
            )


def _source_normals(mesh: SMesh) -> list[tangents.Vec3] | None:
    """The source's own normals (packed or float), or None when it has none in a form that decodes."""
    index = next(
        (
            i
            for i, element in enumerate(mesh.elements)
            if element.usage == 1 and (element.type, element.count) in ((5, 1), (7, 3))
        ),
        None,
    )
    if index is None:
        return None
    values = mesh.element(index)
    if mesh.elements[index].type == 5:
        return [direction(bytes(vertex)) for vertex in values]
    return [(vertex[0], vertex[1], vertex[2]) for vertex in values]


def _frame_columns(
    mesh: SMesh, mesh_elements: list[_SElement], attributes: tuple[SVertexElement, ...], notes: CNotes, who: str
):
    """The normal, tangent and bitangent columns a target layout asks for that the source lacks, derived from the
    positions, UVs and triangles (``tangents.frames``): usage -> one packed element per vertex. A source normal is
    kept and the tangent frame is built around it."""
    own = {usage: _nth(mesh_elements, usage, 0) for usage in (1, 2, 3)}
    missing = {
        usage
        for usage in (1, 2, 3)
        if any(attribute.usage == usage for attribute in attributes) and (own[usage] is None or own[usage].type != 5)
    }
    missing -= {usage for usage in (1,) if own[usage] is not None and own[usage].type == 7 and own[usage].count == 3}
    if not missing:
        return {}
    uv_index = next(
        (
            i
            for i, element in enumerate(mesh.elements)
            if element.usage == 4 and element.type in (6, 7) and element.count == 2
        ),
        None,
    )
    uvs = [(vertex[0], vertex[1]) for vertex in mesh.element(uv_index)] if uv_index is not None else None
    positions = mesh.positions()
    if len(positions) != mesh.vertex_count:
        raise CNativeError(f"{who}: no position to derive the normals from")
    normal, tangent, bitangent = tangents.frames(positions, uvs, mesh.indices, _source_normals(mesh))
    out: dict[int, list[bytes]] = {}
    if 1 in missing:
        out[1] = [pack_direction(*vertex) for vertex in normal]
        notes.m_filled.append(f"{who}: normal absent in the source: computed from the triangles")
    if missing & {2, 3}:
        out[2] = [pack_direction(*tangents.stored(vertex)) for vertex in tangent]
        out[3] = [pack_direction(*tangents.stored(vertex)) for vertex in bitangent]
        how = "computed from the UVs" if uvs is not None else "no UVs: an arbitrary frame around the normal"
        notes.m_filled.append(f"{who}: tangent frame absent in the source: {how}")
    return out


@dataclass(frozen=True)
class _SLayout:
    """What a mesh is written as: the target's elements, the joint index its bones start at (``base``), its skeleton
    and bones per vertex, whether it is a static use, the matrix baked in and a rigid mesh (whole on ``base``)."""

    attributes: tuple[SVertexElement, ...]
    base: int = 0
    skeleton: int | None = None
    bones: int = 0
    static: bool = False
    matrix: meshxform.Matrix | None = None
    rigid: bool = False


def _mesh_data(rdm: SRdmFile, mesh: SMesh, layout: _SLayout, notes: CNotes) -> rdmwrite.SRdmMeshData:
    attributes, base, matrix = layout.attributes, layout.base, layout.matrix
    who = f"mesh {mesh.name!r}"
    if mesh.stride != mesh.format_stride:
        raise CNativeError(f"{who}: vertex block of {mesh.stride} bytes against a layout of {mesh.format_stride}")
    if not mesh.indices or not mesh.vertex_count:
        raise CNativeError(f"{who}: no geometry")
    if max(mesh.indices) >= mesh.vertex_count:
        raise CNativeError(f"{who}: an index points past the last vertex")
    materials = [material.name for material in rdm.materials] or ["x"]
    textures = [material.texture for material in rdm.materials]
    submeshes = [
        rdmwrite.SRdmSubmesh(submesh.first_index, submesh.index_count, submesh.material) for submesh in mesh.submeshes
    ]
    if not submeshes:
        submeshes = [rdmwrite.SRdmSubmesh(0, len(mesh.indices), 0)]
        notes.m_filled.append(f"{who}: one submesh over all indices")
    if any(
        submesh.material >= len(materials) or submesh.index_start + submesh.index_count > len(mesh.indices)
        for submesh in submeshes
    ):
        raise CNativeError(f"{who}: a submesh points outside the indices or the materials")
    mesh_elements = _elements(mesh)
    seen: Counter[int] = Counter()
    frames = _frame_columns(mesh, mesh_elements, attributes, notes, who)
    plan = []
    for attribute in attributes:
        source = (
            None
            if layout.rigid and attribute.usage in (6, 7)
            else _nth(mesh_elements, attribute.usage, seen[attribute.usage])
        )  # rigid: whole to ``base``
        plan.append(_column(attribute, source, base, notes, who, _nth(mesh_elements, 6, 0), frames))
        seen[attribute.usage] += 1
    _dropped(
        mesh, attributes, notes, who, layout.bones > 0 or layout.static
    )  # a static use has no bones, which is not a loss
    indices = mesh.indices
    if matrix is not None:
        plan = [
            meshxform.column(attribute.usage, column, matrix)
            for attribute, column in zip(attributes, plan, strict=True)
        ]
        indices = meshxform.reversed_winding(indices) if meshxform.mirrored(matrix) else indices
    stride = mesh.stride
    rows = b"".join(
        b"".join(column(mesh.vertices[vertex * stride : (vertex + 1) * stride], vertex) for column in plan)
        for vertex in range(mesh.vertex_count)
    )
    out_stride = sum(attribute.size for attribute in attributes)
    box = mesh.computed_bounds() or mesh.bbox
    if matrix is not None:  # the box of what was written, half-float rounding included
        lows = [struct.unpack_from("<3e", rows, vertex * out_stride) for vertex in range(mesh.vertex_count)]
        box = (*(min(low[k] for low in lows) for k in range(3)), *(max(low[k] for low in lows) for k in range(3)))
    return rdmwrite.SRdmMeshData(
        mesh.name,
        rows,
        out_stride,
        attributes,
        indices,
        materials,
        submeshes,
        box,
        rdm.source,
        rdm.exporter,
        skeleton=layout.skeleton,
        bones_per_vertex=layout.bones,
        textures=textures,
    )


# Static meshes


def convert_static(
    data: bytes | SRdmFile,
    source_format: str = "",
    only: int | None = None,
    matrix: meshxform.Matrix | None = None,
    layout: str = "",
) -> SRelaid:
    """The 117 static mesh file of a 2070 mesh file: every mesh kept (or just mesh ``only``), vertices copied into
    :func:`static_format` (bone data left out: this is the static use of the mesh). ``matrix``: a linear transform
    baked into the vertices (a scale per axis and a turn): positions by the matrix, normals by its inverse transpose,
    tangents by the matrix (``meshxform``), the winding reversed where the matrix mirrors. ``layout`` names the
    layout to write instead (any ``rdmlayout`` name: another game's)."""
    rdm = _read(data)
    if not rdm.meshes or (only is not None and not 0 <= only < len(rdm.meshes)):
        raise CNativeError("no mesh in the file")
    format_name = layout or static_format(source_format or rdm.meshes[0].format)
    notes = CNotes()
    out = [
        _mesh_data(
            rdm, mesh, _SLayout(_FORMATS.get(format_name) or parse(format_name), static=True, matrix=matrix), notes
        )
        for k, mesh in enumerate(rdm.meshes)
        if only in (None, k)
    ]
    return SRelaid(rdmwrite.write_rdm(*out), format_name, 0, len(out), notes)


def _read(data: bytes | SRdmFile) -> SRdmFile:
    if isinstance(data, SRdmFile):
        return data
    try:
        return read_rdm(data)
    except CRdmReadError as exc:
        raise CNativeError(f"cannot read the mesh: {exc}") from exc


# Skeletons and skinned meshes


def merge_skeletons(rdm: SRdmFile, notes: CNotes, static_part: bool = False) -> tuple[SSkeleton, list[int], int | None]:
    """One skeleton from all of a file's: the joints appended in order with their parents shifted, each
    skeleton's first joint, and the index of the joint added for the meshes that name no skeleton (``static_part``:
    an identity joint of its own that no clip moves). 117 registers the skeleton of the file's first skin only, and
    79 of 2070's meshes (6 files) name another (rigid parts, one skeleton each)."""
    joints: list[SJoint] = []
    firsts: list[int] = []
    for skeleton_index, source_skeleton in enumerate(rdm.skeletons):
        if source_skeleton.extra:
            notes.m_dropped.append(
                f"skeleton {skeleton_index}: {len(source_skeleton.extra)} bytes of optional skeleton data"
            )
        firsts.append(len(joints))
        for joint in source_skeleton.joints:
            parent = None if joint.parent is None else joint.parent + firsts[-1]
            joints.append(SJoint(joint.name, joint.position, joint.rotation, parent, joint.box_min, joint.box_max))
    static_index = None
    if static_part:
        static_index = len(joints)
        joints.append(SJoint(STATIC_JOINT, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), None, (0.0,) * 3, (0.0,) * 3))
    if len(joints) > MAX_JOINTS:
        raise CNativeError(f"{len(joints)} joints in the file's skeletons: a bone index holds {MAX_JOINTS}")
    names = Counter(joint.name for joint in joints if joint.name)
    twice = sorted(name for name, count in names.items() if count > 1)
    if twice:
        raise CNativeError(f"joint name used in two skeletons ({twice[0]!r}): animation tracks find joints by name")
    for index, joint in enumerate(joints):
        if joint.parent is not None and not 0 <= joint.parent < index:
            raise CNativeError(f"joint {joint.name!r}: its parent does not come before it (117 drops such a skeleton)")
    return SSkeleton(joints, 0), firsts, static_index


def file_bones(rdm: SRdmFile) -> int:
    """Bones per vertex a converted file declares: 3 as vanilla's skinned materials, 4 where a vertex weighs four
    (the most ``W4b`` holds; the shader loops over NUM_BONES_PER_VERTEX weights)."""
    for mesh in rdm.meshes:
        if mesh.bones_per_vertex >= 4:
            return 4
    return 3


def convert_skinned(data: bytes | SRdmFile, layout: str = SKINNED_FORMAT, bones: int = 0) -> SRelaid:
    """The 117 skinned mesh file of a 2070 mesh file: every mesh kept, vertices in ``P4h_N4b_G4b_B4b_T2h_I4b_W4b``,
    the skeletons merged into the one skin 117 reads (a mesh's bone indices shifted by its skeleton's first joint;
    a rigid mesh bound to its skeleton's first joint; a mesh that names no skeleton is not skinned in 2070 and
    hangs on a joint of its own that nothing animates), the skeleton record's +4 word 0. ``layout`` and ``bones``
    (the vertex description's word) write another game's skin layout instead."""
    rdm = _read(data)
    if not rdm.meshes or not rdm.skeletons:
        raise CNativeError("no skinned mesh in the file")
    notes = CNotes()
    skeleton, firsts, static_index = merge_skeletons(rdm, notes, any(mesh.skeleton is None for mesh in rdm.meshes))
    bones = bones or file_bones(rdm)
    attributes = _FORMATS.get(layout) or parse(layout)
    out = []
    for mesh in rdm.meshes:
        if mesh.skeleton is not None and mesh.skeleton >= len(rdm.skeletons):
            raise CNativeError(f"mesh {mesh.name!r}: skeleton {mesh.skeleton} of {len(rdm.skeletons)}")
        if mesh.skeleton is None:
            notes.m_filled.append(
                f"mesh {mesh.name!r}: static mesh in a skinned file, bound to the joint {STATIC_JOINT!r}"
            )
            out.append(
                _mesh_data(
                    rdm,
                    mesh,
                    _SLayout(attributes, base=static_index or 0, skeleton=0, bones=bones, rigid=True),
                    notes,
                )
            )
            continue
        out.append(
            _mesh_data(rdm, mesh, _SLayout(attributes, base=firsts[mesh.skeleton], skeleton=0, bones=bones), notes)
        )
        limit = len(rdm.skeletons[mesh.skeleton].joints)
        bone_index_element = mesh.element_at(7)
        if (
            bone_index_element is not None
            and max((max(bone_indices) for bone_indices in mesh.element(bone_index_element)), default=0) >= limit
        ):
            raise CNativeError(f"mesh {mesh.name!r}: a bone index points past its skeleton's {limit} joints")
    joints = frozenset(j.name for j in skeleton.joints)
    data = rdmwrite.write_rdm(*out, skeletons=[skeleton])
    return SRelaid(data, layout, bones, len(out), notes, joints, frozenset(mesh.name for mesh in rdm.meshes))


# Animations


def _moved(animation: SAnimation, joints: set[str]) -> int:
    return sum(1 for track in animation.tracks if track.joint in joints and track.keys)


def pick_animation(animations: list[SAnimation], joints: set[str], models: set[str]) -> SAnimation:
    """The entry of an animation file that moves a mesh: the one with the most tracks on the mesh's joints, then the
    one named for one of its meshes. A 2070 file holds one entry per model it moves (the saw mill's work animation
    moves the mill, the log and the plank)."""
    return max(animations, key=lambda candidate: (_moved(candidate, joints), candidate.name in models))


def convert_animation(
    data: bytes | SRdmFile, joints: set[str] | None = None, models: set[str] | None = None
) -> tuple[bytes, CNotes]:
    """The animation file for 117: records, tracks and keys as they are (2070's are 117's), frame rate and
    durations kept, little-endian. A file of several entries is cut to the one that moves ``joints``; an entry that
    moves none of them is written without tracks, the mesh then stands still in it."""
    rdm = _read(data)
    notes = CNotes()
    if not rdm.animations:
        raise CNativeError("no animation in the file")
    if rdm.meshes:
        raise CNativeError("the file holds meshes as well as animations")
    if joints is None or len(rdm.animations) == 1:
        keep = rdm.animations[:1] if joints is not None else rdm.animations
    else:
        pick = pick_animation(rdm.animations, joints, models or set())
        keep = [pick]
        if not _moved(pick, joints):
            keep = [SAnimation(pick.name, pick.duration_ms, pick.frame_rate, [])]
    for entry in keep:
        for track in entry.tracks:
            if not track.keys:
                notes.m_approximated.append(f"track {track.joint!r}: no keys")
    skeletons = [
        SSkeleton(source_skeleton.joints, 0) for source_skeleton in rdm.skeletons
    ]  # the word is a 2070 index, 117 reads an offset
    return rdmwrite.write_animation_rdm(keep, rdm.source, rdm.exporter, skeletons), notes


def bones_of(data: bytes) -> int:
    """The bones per vertex a converted mesh file declares (its vertex description's word); 0 when unreadable."""
    try:
        meshes = read_rdm(data).meshes
    except CRdmReadError:
        return 0
    return max((mesh.bones_per_vertex for mesh in meshes), default=0)


def animation_count(data: bytes) -> int:
    """How many entries (models moved) an animation file has; 1 when it cannot be read."""
    try:
        return max(1, len(read_rdm(data).animations))
    except CRdmReadError:
        return 1
