"""Granny 2 ``.gr2`` files written (Anno 1404's and 1701's meshes, skeletons and animations): a record tree with the
type tree that describes it, in one stored section with its pointer fixups, under the header the games' runtime
reads. The type tag is not the runtime's own, so the runtime converts the file into its own types by member name
and a member left out reads as zero.

``write_file(root)`` is the entry, ``root`` a ``Rec`` of ``ROOT``; ``mesh_record``, ``skeleton_record``,
``model_record`` and ``animation_record`` build the standard records; ``grannyread`` reads the result back.
docs/formats/granny.md "Writing".
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from . import grannyread as gr

TAG = 0x80000001  # not a runtime's own tag: it converts by member name (granny.md "Writing")
POINTER = 8  # the pointer size written
SECTIONS = 8  # what the games' files carry; all but the first stay empty
HEADER_SIZE = gr.INFO_AT + 72 + SECTIONS * gr.SECTION_ENTRY
CRC_FROM = gr.INFO_AT + 72  # the header's CRC covers the section table and everything after it
_T = tuple  # a type: members (kind, name, target type or None, width)


def _m(kind: int, name: str, target: _T | None = None, width: int = 0) -> tuple:
    return (kind, name, target, width)


# fmt: off
INT32_T = (_m(gr.INT32, "Int32"),)
INT16_T = (_m(gr.INT16, "Int16"),)
REAL32_T = (_m(gr.REAL32, "Real32"),)
ART_TOOL = (_m(gr.STRING, "FromArtToolName"), _m(gr.INT32, "ArtToolMajorRevision"),
            _m(gr.INT32, "ArtToolMinorRevision"), _m(gr.INT32, "ArtToolPointerSize"), _m(gr.REAL32, "UnitsPerMeter"),
            _m(gr.REAL32, "Origin", None, 3), _m(gr.REAL32, "RightVector", None, 3), _m(gr.REAL32, "UpVector", None, 3),
            _m(gr.REAL32, "BackVector", None, 3))
EXPORTER = (_m(gr.STRING, "ExporterName"), _m(gr.INT32, "ExporterMajorRevision"),
            _m(gr.INT32, "ExporterMinorRevision"), _m(gr.INT32, "ExporterCustomization"),
            _m(gr.INT32, "ExporterBuildNumber"))
MATERIAL = (_m(gr.STRING, "Name"),)
BONE = (_m(gr.STRING, "Name"), _m(gr.INT32, "ParentIndex"), _m(gr.TRANSFORM, "Transform"),
        _m(gr.REAL32, "InverseWorldTransform", None, 16), _m(gr.REAL32, "LODError"))
SKELETON = (_m(gr.STRING, "Name"), _m(gr.REFERENCE_TO_ARRAY, "Bones", BONE), _m(gr.INT32, "LODType"))
VERTEX_DATA = (_m(gr.REFERENCE_TO_VARIANT_ARRAY, "Vertices"),)
GROUP = (_m(gr.INT32, "MaterialIndex"), _m(gr.INT32, "TriFirst"), _m(gr.INT32, "TriCount"))
TOPOLOGY = (_m(gr.REFERENCE_TO_ARRAY, "Groups", GROUP), _m(gr.REFERENCE_TO_ARRAY, "Indices", INT32_T),
            _m(gr.REFERENCE_TO_ARRAY, "Indices16", INT16_T))
MATERIAL_BINDING = (_m(gr.REFERENCE, "Material", MATERIAL),)
BONE_BINDING = (_m(gr.STRING, "BoneName"), _m(gr.REAL32, "OBBMin", None, 3), _m(gr.REAL32, "OBBMax", None, 3))
MESH = (_m(gr.STRING, "Name"), _m(gr.REFERENCE, "PrimaryVertexData", VERTEX_DATA),
        _m(gr.REFERENCE, "PrimaryTopology", TOPOLOGY), _m(gr.REFERENCE_TO_ARRAY, "MaterialBindings", MATERIAL_BINDING),
        _m(gr.REFERENCE_TO_ARRAY, "BoneBindings", BONE_BINDING))
MESH_BINDING = (_m(gr.REFERENCE, "Mesh", MESH),)
MODEL = (_m(gr.STRING, "Name"), _m(gr.REFERENCE, "Skeleton", SKELETON), _m(gr.TRANSFORM, "InitialPlacement"),
         _m(gr.REFERENCE_TO_ARRAY, "MeshBindings", MESH_BINDING))
CURVE_HEADER = (_m(gr.UINT8, "Format"), _m(gr.UINT8, "Degree"))
CURVE_KNOTS = (_m(gr.INLINE, "CurveDataHeader_DaK32fC32f", CURVE_HEADER), _m(gr.INT16, "Padding"),
               _m(gr.REFERENCE_TO_ARRAY, "Knots", REAL32_T), _m(gr.REFERENCE_TO_ARRAY, "Controls", REAL32_T))
CURVE_IDENTITY = (_m(gr.INLINE, "CurveDataHeader_DaIdentity", CURVE_HEADER), _m(gr.INT16, "Dimension"))
CURVE2 = (_m(gr.VARIANT_REFERENCE, "CurveData"),)
TRANSFORM_TRACK = (_m(gr.STRING, "Name"), _m(gr.INT32, "Flags"), _m(gr.INLINE, "OrientationCurve", CURVE2),
                   _m(gr.INLINE, "PositionCurve", CURVE2), _m(gr.INLINE, "ScaleShearCurve", CURVE2))
TRACK_GROUP = (_m(gr.STRING, "Name"), _m(gr.REFERENCE_TO_ARRAY, "TransformTracks", TRANSFORM_TRACK),
               _m(gr.TRANSFORM, "InitialPlacement"), _m(gr.INT32, "AccumulationFlags"),
               _m(gr.REAL32, "LoopTranslation", None, 3))
ANIMATION = (_m(gr.STRING, "Name"), _m(gr.REAL32, "Duration"), _m(gr.REAL32, "TimeStep"),
             _m(gr.REAL32, "Oversampling"), _m(gr.ARRAY_OF_REFERENCES, "TrackGroups", TRACK_GROUP),
             _m(gr.INT32, "DefaultLoopCount"), _m(gr.INT32, "Flags"))
ROOT = (_m(gr.REFERENCE, "ArtToolInfo", ART_TOOL), _m(gr.REFERENCE, "ExporterInfo", EXPORTER),
        _m(gr.STRING, "FromFileName"), _m(gr.ARRAY_OF_REFERENCES, "Materials", MATERIAL),
        _m(gr.ARRAY_OF_REFERENCES, "Skeletons", SKELETON), _m(gr.ARRAY_OF_REFERENCES, "VertexDatas", VERTEX_DATA),
        _m(gr.ARRAY_OF_REFERENCES, "TriTopologies", TOPOLOGY), _m(gr.ARRAY_OF_REFERENCES, "Meshes", MESH),
        _m(gr.ARRAY_OF_REFERENCES, "Models", MODEL), _m(gr.ARRAY_OF_REFERENCES, "TrackGroups", TRACK_GROUP),
        _m(gr.ARRAY_OF_REFERENCES, "Animations", ANIMATION))
# fmt: on
FORMAT_KNOTS, FORMAT_IDENTITY = 1, 2  # a curve's ``Format`` byte (DaK32fC32f, DaIdentity)


@dataclass(eq=False)
class Rec:
    """One record: its type and member values by name (a member left out is zero). Records are written once, so
    one ``Rec`` referred to twice is one record in the file."""

    type: _T
    values: dict = field(default_factory=dict)


@dataclass(frozen=True)
class VariantArray:
    """The value of a reference to a variant array: the record type and the records, each a tuple of its values."""

    type: _T
    rows: list


class _Writer:
    """One section being written: records, strings and type definitions laid out in order, with their fixups."""

    def __init__(self, pointer: int) -> None:
        self.p, self.data, self.fixups = pointer, bytearray(), []
        # by id: each entry keeps its object alive, so a record made on the fly cannot hand its id to the next one
        self._types: dict[int, tuple[_T, int]] = {}
        self._records: dict[int, tuple[Rec, int]] = {}
        self._strings: dict[str, int] = {}

    def alloc(self, size: int) -> int:
        self.data += bytes(-len(self.data) % 4)
        at = len(self.data)
        self.data += bytes(size)
        return at

    def point(self, at: int, target: int | None) -> None:
        if target is not None:
            self.fixups.append((at, 0, target))

    def string(self, text: str) -> int:
        if text not in self._strings:
            raw = text.encode("latin-1", "replace") + b"\0"
            at = self.alloc(len(raw))
            self.data[at : at + len(raw)] = raw
            self._strings[text] = at
        return self._strings[text]

    def type_of(self, members: _T) -> int:
        """A type definition (its members' names and target types first), once per type."""
        if id(members) not in self._types:
            names = [self.string(m[1]) for m in members]
            targets = [self.type_of(m[2]) if m[2] is not None else None for m in members]
            stride = gr.member_stride(self.p)
            at = self.alloc(stride * (len(members) + 1))
            for k, (kind, _name, _target, width) in enumerate(members):
                struct.pack_into("<I", self.data, at + k * stride, kind)
                struct.pack_into("<i", self.data, at + k * stride + 4 + 2 * self.p, width)
                self.point(at + k * stride + 4, names[k])
                self.point(at + k * stride + 4 + self.p, targets[k])
            self._types[id(members)] = (members, at)
        return self._types[id(members)][1]

    def size(self, members: _T) -> int:
        return sum(self._member_size(m) * max(m[3], 1) for m in members)

    def _member_size(self, m: tuple) -> int:
        return gr.field_size(m[0], self.p, self.size(m[2]) if m[0] == gr.INLINE else 0)

    def record(self, rec: Rec | None) -> int | None:
        if rec is None:
            return None
        if id(rec) not in self._records:
            at = self.alloc(self.size(rec.type))
            self._records[id(rec)] = (rec, at)
            self.fill(rec.type, rec.values, at)
        return self._records[id(rec)][1]

    def fill(self, members: _T, values: dict, at: int) -> None:
        """Write a record's members at ``at`` (its space taken), what they point to after the end."""
        for m in members:
            value = values.get(m[1], {} if m[0] == gr.TRANSFORM else None)  # a transform: identity
            if value is not None:
                self._member(m, value, at)
            at += self._member_size(m) * max(m[3], 1)

    def _member(self, m: tuple, value, at: int) -> None:
        kind, _name, target, width = m
        if kind in gr.SCALARS:
            items = tuple(value) if width > 1 else (value,)
            struct.pack_into(f"<{len(items)}{gr.SCALARS[kind]}", self.data, at, *items)
        elif kind == gr.INLINE:
            self.fill(target, value, at)
        elif kind == gr.STRING:
            self.point(at, self.string(value))
        elif kind == gr.REFERENCE:
            self.point(at, self.record(value if isinstance(value, Rec) else Rec(target, value)))
        elif kind == gr.REFERENCE_TO_ARRAY:
            struct.pack_into("<i", self.data, at, len(value))
            self.point(at + 4, self._array(target, value) if len(value) else None)
        elif kind == gr.ARRAY_OF_REFERENCES:
            struct.pack_into("<i", self.data, at, len(value))
            table = self.alloc(self.p * len(value)) if value else None
            self.point(at + 4, table)
            for k, rec in enumerate(value):
                self.point(table + k * self.p, self.record(rec))
        elif kind == gr.VARIANT_REFERENCE:
            self.point(at, self.type_of(value.type))
            self.point(at + self.p, self.record(value))
        elif kind == gr.REFERENCE_TO_VARIANT_ARRAY:
            self.point(at, self.type_of(value.type))
            struct.pack_into("<i", self.data, at + self.p, len(value.rows))
            self.point(at + self.p + 4, self._array(value.type, value.rows) if value.rows else None)
        elif kind == gr.TRANSFORM:
            t = {**IDENTITY_TRANSFORM, **value}
            struct.pack_into("<I16f", self.data, at, t["flags"], *t["position"], *t["orientation"], *t["scale_shear"])

    def _array(self, members: _T, rows) -> int:
        """Records in a row: a type of plain numbers takes tuples (or numbers), any other dicts of values."""
        size = self.size(members)
        at = self.alloc(size * len(rows))
        plain = all(m[0] in gr.SCALARS for m in members)
        fmt = "<" + "".join(f"{max(m[3], 1)}{gr.SCALARS[m[0]]}" for m in members) if plain else ""
        for k, row in enumerate(rows):
            if plain and not isinstance(row, dict):
                struct.pack_into(fmt, self.data, at + k * size, *(row if isinstance(row, tuple | list) else (row,)))
            else:
                self.fill(members, row, at + k * size)
        return at


IDENTITY_TRANSFORM = {"flags": 7, "position": (0.0, 0.0, 0.0), "orientation": (0.0, 0.0, 0.0, 1.0),
                      "scale_shear": (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)}  # fmt: skip


def write_file(root: Rec) -> bytes:
    """The ``.gr2`` of a root record (``ROOT``): one stored section with everything, seven empty ones; 64-bit
    pointers (the games' runtime failed on a 32-bit file of ours, granny.md "Writing")."""
    pointer = POINTER
    w = _Writer(pointer)
    root_type = w.type_of(root.type)
    root_at = w.record(root)
    data = bytes(w.data) + bytes(-len(w.data) % 4)
    data_at = HEADER_SIZE
    fixups_at = data_at + len(data)
    total = fixups_at + 12 * len(w.fixups)
    magic = next(m for m, p in gr.MAGICS.items() if p == pointer)
    head = bytearray(magic + struct.pack("<II8x", HEADER_SIZE, 0))
    head += struct.pack("<5I", 7, total, 0, 72, SECTIONS) + struct.pack("<4I", 0, root_type, 0, root_at)
    head += struct.pack("<I", TAG)
    head += bytes(gr.INFO_AT + 72 - len(head))
    for k in range(SECTIONS):
        size = len(data) if k == 0 else 0
        where = data_at if k == 0 else fixups_at
        fix = (fixups_at, len(w.fixups)) if k == 0 else (total, 0)
        head += struct.pack("<11I", gr.NONE, where, size, size, 4, size, size, *fix, total, 0)
    out = bytearray(head) + data + b"".join(struct.pack("<3I", *f) for f in w.fixups)
    struct.pack_into("<I", out, 40, zlib.crc32(out[CRC_FROM:]))
    return bytes(out)


# The standard records


def vertex_type(skinned: bool, colours: bool = False, frames: bool = False) -> _T:
    """The vertex record of a mesh: position, normal, the first UV set; a skin's four weights and bone numbers (as
    1701's files: bytes), the tangent frame and colours as floats (as 1404's files)."""
    members = [_m(gr.REAL32, "Position", None, 3)]
    if skinned:
        members += [_m(gr.NORMAL_UINT8, "BoneWeights", None, 4), _m(gr.UINT8, "BoneIndices", None, 4)]
    members += [_m(gr.REAL32, "Normal", None, 3)]
    members += [_m(gr.REAL32, "Tangent", None, 3), _m(gr.REAL32, "Binormal", None, 3)] if frames else []
    members += [_m(gr.REAL32, "DiffuseColor0", None, 4)] if colours else []
    return (*members, _m(gr.REAL32, "TextureCoordinates0", None, 2))


@dataclass
class MeshSpec:
    """A mesh to write: per vertex position, normal, UV (and a skin's bone numbers into ``bones``, weights 0-255,
    tangent and binormal, colours 0-1); per material group its material and triangle indices."""

    name: str
    positions: list
    normals: list
    uvs: list
    groups: list  # (material Rec, flat triangle indices)
    bones: list[str] = field(default_factory=list)
    bone_indices: list | None = None
    bone_weights: list | None = None
    colours: list | None = None
    tangents: list | None = None
    binormals: list | None = None


def mesh_record(spec: MeshSpec) -> Rec:
    """A ``Mesh`` record with its vertex data, topology (32-bit indices beyond 65535 vertices) and bindings."""
    skinned = spec.bone_indices is not None
    frames = spec.tangents is not None and spec.binormals is not None
    vtype = vertex_type(skinned, spec.colours is not None, frames)
    rows = []
    for k, p in enumerate(spec.positions):
        row = [*p]
        if skinned:
            row += [*spec.bone_weights[k], *spec.bone_indices[k]]
        row += [*spec.normals[k], *((*spec.tangents[k], *spec.binormals[k]) if frames else ())]
        row += [*(spec.colours[k] if spec.colours is not None else ()), *spec.uvs[k]]
        rows.append(tuple(row))
    materials, groups, indices = [], [], []
    for material, tris in spec.groups:
        if material not in materials:
            materials.append(material)
        groups.append({"MaterialIndex": materials.index(material), "TriFirst": len(indices) // 3,
                       "TriCount": len(tris) // 3})  # fmt: skip
        indices += tris
    wide = len(spec.positions) > 0xFFFF
    topology = Rec(TOPOLOGY, {"Groups": groups, "Indices" if wide else "Indices16": [(i,) for i in indices]})
    bones = [{"BoneName": b, "OBBMin": _bound(spec, min), "OBBMax": _bound(spec, max)} for b in spec.bones]
    return Rec(MESH, {"Name": spec.name, "PrimaryVertexData": Rec(VERTEX_DATA, {"Vertices": VariantArray(vtype, rows)}),
                      "PrimaryTopology": topology, "MaterialBindings": [{"Material": m} for m in materials],
                      "BoneBindings": bones})  # fmt: skip


def _bound(spec: MeshSpec, pick) -> tuple:
    return tuple(pick(p[k] for p in spec.positions) for k in range(3)) if spec.positions else (0.0, 0.0, 0.0)


def skeleton_record(name: str, bones: list[dict]) -> Rec:
    """A ``Skeleton``: per bone ``Name``, ``ParentIndex`` (-1 a root), ``Transform`` (rest in the parent: a dict of
    position, orientation, scale_shear) and ``InverseWorldTransform`` (16 floats, the move in the last row)."""
    return Rec(SKELETON, {"Name": name, "Bones": bones, "LODType": 0})


def model_record(name: str, skeleton: Rec | None, meshes: list[Rec]) -> Rec:
    return Rec(MODEL, {"Name": name, "Skeleton": skeleton, "MeshBindings": [{"Mesh": m} for m in meshes]})


def curve(knots: list[float], controls: list[tuple], degree: int = 1, dimension: int = 3) -> Rec:
    """A ``Curve2``'s data: knots and controls as plain floats (``DaK32fC32f``), the identity for no controls."""
    if not controls:
        return Rec(CURVE_IDENTITY, {"CurveDataHeader_DaIdentity": {"Format": FORMAT_IDENTITY}, "Dimension": dimension})
    if len(controls) == 1:  # one key: a knot-less constant reads as the control alone
        knots, degree = [0.0], 0
    return Rec(CURVE_KNOTS, {"CurveDataHeader_DaK32fC32f": {"Format": FORMAT_KNOTS, "Degree": degree},
                             "Knots": [(float(k),) for k in knots], "Controls": [(c,) for v in controls for c in v]})  # fmt: skip


def animation_record(name: str, duration: float, step: float, groups: list[tuple[str, list[dict]]]) -> Rec:
    """An ``Animation``: per track group (named after its model) its tracks, each ``{"Name": bone,
    "PositionCurve": curve, "OrientationCurve": curve, "ScaleShearCurve": curve}``."""
    made = []
    for group, tracks in groups:
        rows = [{"Name": t["Name"], "Flags": 0,
                 **{k: {"CurveData": t.get(k) or curve([], [], dimension=d)} for k, d in CURVE_DIMENSIONS.items()}}
                for t in tracks]  # fmt: skip
        made.append(Rec(TRACK_GROUP, {"Name": group, "TransformTracks": rows}))
    values = {"Name": name, "Duration": duration, "TimeStep": step, "Oversampling": 1.0, "TrackGroups": made}
    return Rec(ANIMATION, values)


CURVE_DIMENSIONS = {"OrientationCurve": 4, "PositionCurve": 3, "ScaleShearCurve": 9}


def root_record(name: str, units_per_meter: float = 1.0, **lists) -> Rec:
    """The file info: art tool info (y up, back = -z, as the games' files), the exporter, and the lists
    (``Materials``, ``Skeletons``, ``Meshes``, ``Models``, ``Animations``, ...) of record ``Rec``s."""
    art = {"FromArtToolName": "glTF", "ArtToolPointerSize": 64, "UnitsPerMeter": units_per_meter,
           "RightVector": (1.0, 0.0, 0.0), "UpVector": (0.0, 1.0, 0.0), "BackVector": (0.0, 0.0, -1.0)}  # fmt: skip
    meshes = lists.get("Meshes", [])
    lists.setdefault("VertexDatas", [m.values["PrimaryVertexData"] for m in meshes])
    lists.setdefault("TriTopologies", [m.values["PrimaryTopology"] for m in meshes])
    return Rec(ROOT, {"ArtToolInfo": art, "ExporterInfo": {"ExporterName": "glTF import"}, "FromFileName": name,
                      **lists})  # fmt: skip
