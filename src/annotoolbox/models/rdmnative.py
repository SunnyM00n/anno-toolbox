"""The relayout of 2070 ``.rdm`` files into 117's: static and skinned meshes, skeletons, animations.

No rdm4 and no glTF: the meshes are read with ``rdmread`` and written with ``rdmwrite``, every vertex element
copied byte for byte into the 117 layout. What it does besides copying is listed per file in ``Notes``
(``filled``: data the source never had, ``approximated``, ``dropped``). Format and rules: docs/formats/rdm.md.
"""

from __future__ import annotations

import struct
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import rdmwrite as w
from ..formats.rdmlayout import direction
from ..formats.rdmread import Animation, Joint, Mesh, RdmFile, RdmReadError, Skeleton, VertexElement, read_rdm
from ..formats.rdmwrite import pack_direction
from . import meshxform, tangents

SKINNED_FORMAT = "P4h_N4b_G4b_B4b_T2h_I4b_W4b"  # all vanilla skinned meshes with weights are this (3,184 of 3,190)
STATIC_FORMAT = "P4h_N4b_G4b_B4b_T2h"
MAX_JOINTS = 256  # a bone index is one byte (index * 255 read back)
STATIC_JOINT = "static_part"  # the joint a mesh without a skeleton hangs on: no animation names it, so it never moves
_FORMATS = {
    "P4h": w.FORMAT_P4H,
    STATIC_FORMAT: w.FORMAT_P4H_N4B_G4B_B4B_T2H,
    STATIC_FORMAT + "_C4b": w.FORMAT_P4H_N4B_G4B_B4B_T2H_C4B,
    STATIC_FORMAT + "_C4b_C4b": w.FORMAT_P4H_N4B_G4B_B4B_T2H_C4B_C4B,
    SKINNED_FORMAT: w.FORMAT_P4H_N4B_G4B_B4B_T2H_I4B_W4B,
}


class NativeError(ValueError):
    """A file the native path cannot convert; the message says why."""


@dataclass
class Notes:
    """What the relayout did besides copying (each entry names the mesh or joint and the reason)."""

    filled: list[str] = field(default_factory=list)  # data the target layout has and the source never had
    approximated: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    def extend(self, other: Notes) -> None:
        self.filled += other.filled
        self.approximated += other.approximated
        self.dropped += other.dropped


@dataclass
class Relaid:
    data: bytes
    format: str  # the 117 layout, as a material declares it
    bones: int  # bones per vertex of a skinned mesh (the material's NumBonesPerVertex), 0 for a static one
    meshes: int
    notes: Notes
    joints: frozenset[str] = frozenset()  # a skinned file's joint names, which its animations' tracks name
    models: frozenset[str] = frozenset()  # its mesh names, which an animation entry may be named for


def static_format(source_format: str) -> str:
    """The 117 layout a static 2070 mesh is written in, from the layout its material declares: the cut-out helpers
    stay ``P4h`` (a vanilla layout), vertex colours are kept as ``C4b`` (up to two, as vanilla's plants), everything
    else is the tangent-frame layout. Never ``I``/``W``: a static build has no bones."""
    parts = source_format.split("_")
    if parts == ["P4h"]:
        return "P4h"
    colours = min(sum(1 for p in parts if p.startswith("C")), 2)
    return STATIC_FORMAT + "_C4b" * colours


# Vertex rows


@dataclass(frozen=True)
class _Element:
    usage: int
    type: int
    subtype: int
    count: int
    offset: int
    size: int


def _elements(m: Mesh) -> list[_Element]:
    out, at = [], 0
    for e in m.elements:
        out.append(_Element(e.usage, e.type, e.subtype, e.count, at, e.size))
        at += e.size
    return out


def _nth(els: list[_Element], usage: int, n: int) -> _Element | None:
    found = [e for e in els if e.usage == usage]
    return found[n] if n < len(found) else None


Column = Callable[[bytes, int], bytes]  # a source row and its vertex number -> the target element's bytes


def _column(
    a: VertexElement,
    src: _Element | None,
    base: int,
    notes: Notes,
    who: str,
    weights: _Element | None = None,
    computed: dict[int, list[bytes]] | None = None,
) -> Column:
    """The function that makes target element ``a`` of a vertex from the source row (``computed``: the columns
    :func:`_frame_columns` made for the elements the source lacks, by usage)."""

    def copy(o: int, n: int) -> Column:
        return lambda x, _v: x[o : o + n]

    def fill(value: bytes, what: str) -> Column:
        notes.filled.append(f"{who}: {what}")
        return lambda x, _v: value

    u = a.usage
    if u == 0:
        if src is None or src.usage != 0 or src.count < 3:
            raise NativeError(f"{who}: no position")
        if src.type == 6 and src.count == 4:
            return copy(src.offset, 8)
        if src.type in (6, 7):
            kind = "e" if src.type == 6 else "f"
            o, n = src.offset, src.count
            return lambda x, _v: struct.pack("<4e", *struct.unpack_from(f"<{n}{kind}", x, o)[:3], 0.0)
        raise NativeError(f"{who}: position type {src.type}")
    if u in (1, 2, 3):
        if src is not None and src.type == 5:
            return copy(src.offset, 4)
        if src is not None and src.type == 7 and src.count == 3:
            o = src.offset
            return lambda x, _v: pack_direction(*struct.unpack_from("<3f", x, o))
        column = (computed or {}).get(u)
        if column is None:
            raise NativeError(f"{who}: no normal or tangent frame to copy or derive")
        return lambda _x, v: column[v]
    if u == 4:
        if src is not None and src.type == 6 and src.count == 2:
            return copy(src.offset, 4)
        if src is not None and src.type == 7 and src.count == 2:
            o = src.offset
            return lambda x, _v: struct.pack("<2e", *struct.unpack_from("<2f", x, o))
        return fill(bytes(4), "texture coordinate absent in the source")
    if u == 5:
        if src is not None and src.type == 5:
            return copy(src.offset, 4)
        return fill(b"\xff\xff\xff\xff", "vertex colour absent in the source")
    if u == 6:
        if src is not None and src.type == 5:
            return copy(src.offset, 4)
        return fill(b"\xff\0\0\0", "bone weights absent in the source (rigid: full weight)")
    if u == 7:
        if src is not None and src.type == 5:
            o = src.offset
            if base == 0:
                return copy(o, 4)
            if weights is None:  # one bone per vertex: the first slot, the rest point at joint 0
                return lambda x, _v: bytes((x[o] + base, 0, 0, 0))
            wo = weights.offset  # a slot that weighs nothing points at joint 0, as vanilla's do
            return lambda x, _v: bytes((x[o + k] + base) if k == 0 or x[wo + k] else 0 for k in range(4))
        notes.filled.append(f"{who}: bone index absent in the source (rigid: joint {base})")
        return lambda _x, _v: bytes((base, 0, 0, 0))
    raise NativeError(f"{who}: unknown element usage {u}")


def _dropped(m: Mesh, attrs: tuple[VertexElement, ...], notes: Notes, who: str, bones: bool) -> None:
    want = Counter(a.usage for a in attrs)
    have = Counter(e.usage for e in m.elements)
    for usage, n in have.items():
        if n > want[usage] and not (bones and usage in (6, 7)):
            notes.dropped.append(
                f"{who}: {n - want[usage]} x element {'PNGBTCWI'[usage]} has no place in the target layout"
            )


def _source_normals(m: Mesh) -> list[tangents.Vec3] | None:
    """The source's own normals (packed or float), or None when it has none in a form that decodes."""
    index = next((i for i, e in enumerate(m.elements) if e.usage == 1 and (e.type, e.count) in ((5, 1), (7, 3))), None)
    if index is None:
        return None
    values = m.element(index)
    if m.elements[index].type == 5:
        return [direction(bytes(v)) for v in values]
    return [(v[0], v[1], v[2]) for v in values]


def _frame_columns(m: Mesh, els: list[_Element], attrs: tuple[VertexElement, ...], notes: Notes, who: str):
    """The normal, tangent and bitangent columns a target layout asks for that the source lacks, derived from the
    positions, UVs and triangles (``tangents.frames``): usage -> one packed element per vertex. A source normal is
    kept and the tangent frame is built around it."""
    own = {u: _nth(els, u, 0) for u in (1, 2, 3)}
    missing = {u for u in (1, 2, 3) if any(a.usage == u for a in attrs) and (own[u] is None or own[u].type != 5)}
    missing -= {u for u in (1,) if own[u] is not None and own[u].type == 7 and own[u].count == 3}
    if not missing:
        return {}
    uv_at = next((i for i, e in enumerate(m.elements) if e.usage == 4 and e.type in (6, 7) and e.count == 2), None)
    uvs = [(v[0], v[1]) for v in m.element(uv_at)] if uv_at is not None else None
    positions = m.positions()
    if len(positions) != m.vertex_count:
        raise NativeError(f"{who}: no position to derive the normals from")
    normal, tangent, bitangent = tangents.frames(positions, uvs, m.indices, _source_normals(m))
    out: dict[int, list[bytes]] = {}
    if 1 in missing:
        out[1] = [pack_direction(*v) for v in normal]
        notes.filled.append(f"{who}: normal absent in the source: computed from the triangles")
    if missing & {2, 3}:
        out[2] = [pack_direction(*tangents.stored(v)) for v in tangent]
        out[3] = [pack_direction(*tangents.stored(v)) for v in bitangent]
        how = "computed from the UVs" if uvs is not None else "no UVs: an arbitrary frame around the normal"
        notes.filled.append(f"{who}: tangent frame absent in the source: {how}")
    return out


@dataclass(frozen=True)
class _Layout:
    """What a mesh is written as: the target's elements, the joint index its bones start at (``base``), its skeleton
    and bones per vertex, whether it is a static use, the matrix baked in and a rigid mesh (whole on ``base``)."""

    attrs: tuple[VertexElement, ...]
    base: int = 0
    skeleton: int | None = None
    bones: int = 0
    static: bool = False
    matrix: meshxform.Matrix | None = None
    rigid: bool = False


def _mesh_data(rdm: RdmFile, m: Mesh, layout: _Layout, notes: Notes) -> w.RdmMeshData:
    attrs, base, matrix = layout.attrs, layout.base, layout.matrix
    who = f"mesh {m.name!r}"
    if m.stride != m.format_stride:
        raise NativeError(f"{who}: vertex block of {m.stride} bytes against a layout of {m.format_stride}")
    if not m.indices or not m.vertex_count:
        raise NativeError(f"{who}: no geometry")
    if max(m.indices) >= m.vertex_count:
        raise NativeError(f"{who}: an index points past the last vertex")
    materials = [x.name for x in rdm.materials] or ["x"]
    textures = [x.texture for x in rdm.materials]
    subs = [w.RdmSubmesh(s.first_index, s.index_count, s.material) for s in m.submeshes]
    if not subs:
        subs = [w.RdmSubmesh(0, len(m.indices), 0)]
        notes.filled.append(f"{who}: one submesh over all indices")
    if any(s.material >= len(materials) or s.index_start + s.index_count > len(m.indices) for s in subs):
        raise NativeError(f"{who}: a submesh points outside the indices or the materials")
    els = _elements(m)
    seen: Counter[int] = Counter()
    frames = _frame_columns(m, els, attrs, notes, who)
    plan = []
    for a in attrs:
        src = (
            None if layout.rigid and a.usage in (6, 7) else _nth(els, a.usage, seen[a.usage])
        )  # rigid: whole to ``base``
        plan.append(_column(a, src, base, notes, who, _nth(els, 6, 0), frames))
        seen[a.usage] += 1
    _dropped(m, attrs, notes, who, layout.bones > 0 or layout.static)  # a static use has no bones, which is not a loss
    indices = m.indices
    if matrix is not None:
        plan = [meshxform.column(a.usage, f, matrix) for a, f in zip(attrs, plan, strict=True)]
        indices = meshxform.reversed_winding(indices) if meshxform.mirrored(matrix) else indices
    stride = m.stride
    rows = b"".join(
        b"".join(f(m.vertices[v * stride : (v + 1) * stride], v) for f in plan) for v in range(m.vertex_count)
    )
    out_stride = sum(a.size for a in attrs)
    box = m.computed_bounds() or m.bbox
    if matrix is not None:  # the box of what was written, half-float rounding included
        lows = [struct.unpack_from("<3e", rows, v * out_stride) for v in range(m.vertex_count)]
        box = (*(min(p[k] for p in lows) for k in range(3)), *(max(p[k] for p in lows) for k in range(3)))
    return w.RdmMeshData(
        m.name,
        rows,
        out_stride,
        attrs,
        indices,
        materials,
        subs,
        box,
        rdm.source,
        rdm.exporter,
        skeleton=layout.skeleton,
        bones_per_vertex=layout.bones,
        textures=textures,
    )


# Static meshes


def convert_static(
    data: bytes | RdmFile,
    source_format: str = "",
    only: int | None = None,
    matrix: meshxform.Matrix | None = None,
) -> Relaid:
    """The 117 static mesh file of a 2070 mesh file: every mesh kept (or just mesh ``only``), vertices copied into
    :func:`static_format` (bone data left out: this is the static use of the mesh). ``matrix``: a linear transform
    baked into the vertices (a scale per axis and a turn): positions by the matrix, normals by its inverse transpose,
    tangents by the matrix (``meshxform``), the winding reversed where the matrix mirrors."""
    rdm = _read(data)
    if not rdm.meshes or (only is not None and not 0 <= only < len(rdm.meshes)):
        raise NativeError("no mesh in the file")
    fmt = static_format(source_format or rdm.meshes[0].format)
    notes = Notes()
    out = [
        _mesh_data(rdm, m, _Layout(_FORMATS[fmt], static=True, matrix=matrix), notes)
        for k, m in enumerate(rdm.meshes)
        if only in (None, k)
    ]
    return Relaid(w.write_rdm(*out), fmt, 0, len(out), notes)


def _read(data: bytes | RdmFile) -> RdmFile:
    if isinstance(data, RdmFile):
        return data
    try:
        return read_rdm(data)
    except RdmReadError as exc:
        raise NativeError(f"cannot read the mesh: {exc}") from exc


# Skeletons and skinned meshes


def merge_skeletons(rdm: RdmFile, notes: Notes, static_part: bool = False) -> tuple[Skeleton, list[int], int | None]:
    """One skeleton from all of a file's: the joints appended in order with their parents shifted, each
    skeleton's first joint, and the index of the joint added for the meshes that name no skeleton (``static_part``:
    an identity joint of its own that no clip moves). 117 registers the skeleton of the file's first skin only, and
    79 of 2070's meshes (6 files) name another (rigid parts, one skeleton each)."""
    joints: list[Joint] = []
    firsts: list[int] = []
    for si, s in enumerate(rdm.skeletons):
        if s.extra:
            notes.dropped.append(f"skeleton {si}: {len(s.extra)} bytes of optional skeleton data")
        firsts.append(len(joints))
        for j in s.joints:
            parent = None if j.parent is None else j.parent + firsts[-1]
            joints.append(Joint(j.name, j.position, j.rotation, parent, j.box_min, j.box_max))
    static_at = None
    if static_part:
        static_at = len(joints)
        joints.append(Joint(STATIC_JOINT, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), None, (0.0,) * 3, (0.0,) * 3))
    if len(joints) > MAX_JOINTS:
        raise NativeError(f"{len(joints)} joints in the file's skeletons: a bone index holds {MAX_JOINTS}")
    names = Counter(j.name for j in joints if j.name)
    twice = sorted(n for n, c in names.items() if c > 1)
    if twice:
        raise NativeError(f"joint name used in two skeletons ({twice[0]!r}): animation tracks find joints by name")
    for i, j in enumerate(joints):
        if j.parent is not None and not 0 <= j.parent < i:
            raise NativeError(f"joint {j.name!r}: its parent does not come before it (117 drops such a skeleton)")
    return Skeleton(joints, 0), firsts, static_at


def file_bones(rdm: RdmFile) -> int:
    """Bones per vertex a converted file declares: 3 as vanilla's skinned materials, 4 where a vertex weighs four
    (the most ``W4b`` holds; the shader loops over NUM_BONES_PER_VERTEX weights)."""
    for m in rdm.meshes:
        if m.bones_per_vertex >= 4:
            return 4
    return 3


def convert_skinned(data: bytes | RdmFile) -> Relaid:
    """The 117 skinned mesh file of a 2070 mesh file: every mesh kept, vertices in ``P4h_N4b_G4b_B4b_T2h_I4b_W4b``,
    the skeletons merged into the one skin 117 reads (a mesh's bone indices shifted by its skeleton's first joint;
    a rigid mesh bound to its skeleton's first joint; a mesh that names no skeleton is not skinned in 2070 and
    hangs on a joint of its own that nothing animates), the skeleton record's +4 word 0."""
    rdm = _read(data)
    if not rdm.meshes or not rdm.skeletons:
        raise NativeError("no skinned mesh in the file")
    notes = Notes()
    skeleton, firsts, static_at = merge_skeletons(rdm, notes, any(m.skeleton is None for m in rdm.meshes))
    bones = file_bones(rdm)
    out = []
    for m in rdm.meshes:
        if m.skeleton is not None and m.skeleton >= len(rdm.skeletons):
            raise NativeError(f"mesh {m.name!r}: skeleton {m.skeleton} of {len(rdm.skeletons)}")
        if m.skeleton is None:
            notes.filled.append(f"mesh {m.name!r}: static mesh in a skinned file, bound to the joint {STATIC_JOINT!r}")
            out.append(
                _mesh_data(
                    rdm,
                    m,
                    _Layout(_FORMATS[SKINNED_FORMAT], base=static_at or 0, skeleton=0, bones=bones, rigid=True),
                    notes,
                )
            )
            continue
        out.append(
            _mesh_data(
                rdm, m, _Layout(_FORMATS[SKINNED_FORMAT], base=firsts[m.skeleton], skeleton=0, bones=bones), notes
            )
        )
        limit = len(rdm.skeletons[m.skeleton].joints)
        idx = next((i for i, e in enumerate(m.elements) if e.usage == 7), None)
        if idx is not None and max((max(v) for v in m.element(idx)), default=0) >= limit:
            raise NativeError(f"mesh {m.name!r}: a bone index points past its skeleton's {limit} joints")
    joints = frozenset(j.name for j in skeleton.joints)
    data = w.write_rdm(*out, skeletons=[skeleton])
    return Relaid(data, SKINNED_FORMAT, bones, len(out), notes, joints, frozenset(m.name for m in rdm.meshes))


# Animations


def _moved(anim: Animation, joints: set[str]) -> int:
    return sum(1 for t in anim.tracks if t.joint in joints and t.keys)


def pick_animation(anims: list[Animation], joints: set[str], models: set[str]) -> Animation:
    """The entry of an animation file that moves a mesh: the one with the most tracks on the mesh's joints, then the
    one named for one of its meshes. A 2070 file holds one entry per model it moves (the saw mill's work animation
    moves the mill, the log and the plank)."""
    return max(anims, key=lambda a: (_moved(a, joints), a.name in models))


def convert_animation(
    data: bytes | RdmFile, joints: set[str] | None = None, models: set[str] | None = None
) -> tuple[bytes, Notes]:
    """The animation file for 117: records, tracks and keys as they are (2070's are 117's), frame rate and
    durations kept, little-endian. A file of several entries is cut to the one that moves ``joints``; an entry that
    moves none of them is written without tracks, the mesh then stands still in it."""
    rdm = _read(data)
    notes = Notes()
    if not rdm.animations:
        raise NativeError("no animation in the file")
    if rdm.meshes:
        raise NativeError("the file holds meshes as well as animations")
    if joints is None or len(rdm.animations) == 1:
        keep = rdm.animations[:1] if joints is not None else rdm.animations
    else:
        pick = pick_animation(rdm.animations, joints, models or set())
        keep = [pick]
        if not _moved(pick, joints):
            keep = [Animation(pick.name, pick.duration_ms, pick.frame_rate, [])]
    for a in keep:
        for t in a.tracks:
            if not t.keys:
                notes.approximated.append(f"track {t.joint!r}: no keys")
    skeletons = [Skeleton(s.joints, 0) for s in rdm.skeletons]  # the word is a 2070 index, 117 reads an offset
    return w.write_animation_rdm(keep, rdm.source, rdm.exporter, skeletons), notes


def bones_of(data: bytes) -> int:
    """The bones per vertex a converted mesh file declares (its vertex description's word); 0 when unreadable."""
    try:
        meshes = read_rdm(data).meshes
    except RdmReadError:
        return 0
    return max((m.bones_per_vertex for m in meshes), default=0)


def animation_count(data: bytes) -> int:
    """How many entries (models moved) an animation file has; 1 when it cannot be read."""
    try:
        return max(1, len(read_rdm(data).animations))
    except RdmReadError:
        return 1
