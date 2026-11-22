"""Granny 2 skeletons, skins and animations (Anno 1404's and 1701's ``.gr2``), read from the type trees
``grannyread`` walks: the bones with their rest transforms, each mesh's bone bindings and the animations' transform
tracks, whose curves (B-splines in every packed format the games ship) are evaluated here, without numpy.

``read_skeletons(root)`` and ``read_animations(root)`` are the entries; ``SCurve.at(time_s)`` samples a curve and
``STransformTrack.at(time_s)`` a bone's position, orientation and scale-shear; ``bone_names(mesh)`` maps a skinned mesh's
bone numbers. docs/formats/granny.md "Skeletons", "Animations" and "Curves".
"""

from __future__ import annotations

import bisect
import math
import struct
from dataclasses import dataclass

from . import grannyread as gr

IDENTITY = {3: (0.0, 0.0, 0.0), 4: (0.0, 0.0, 0.0, 1.0), 9: (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)}
# the 16 scale/offset pairs a packed quaternion's components pick by their 4-bit selectors (granny.md "Curves")
# fmt: off
_QUAT_SCALES = (1.4142135, 0.70710677, 0.35355338, 0.35355338, 0.35355338, 0.17677669, 0.17677669, 0.17677669,
                -1.4142135, -0.70710677, -0.35355338, -0.35355338, -0.35355338, -0.17677669, -0.17677669, -0.17677669)
_QUAT_OFFSETS = (-0.70710677, -0.35355338, -0.53033006, -0.17677669, 0.17677669, -0.17677669, -0.088388346, 0.0,
                 0.70710677, 0.35355338, 0.53033006, 0.17677669, -0.17677669, 0.17677669, 0.088388346, -0.0)
# fmt: on
Vector = tuple[float, ...]


@dataclass(frozen=True)
class SCurve:
    """A curve as Granny evaluates it: ``knots`` in seconds, one control per knot (each ``dimension`` values), its
    ``degree``; a control without knots is a constant, no control at all the identity of its dimension."""

    dimension: int
    degree: int
    knots: tuple[float, ...]
    controls: tuple[Vector, ...]
    format: str = ""  # the packed format it was stored in (``DaK32fC32f``, ``D4nK16uC15u``, ...)

    def at(self, time_s: float) -> Vector:
        """The value at ``time_s`` seconds: held before the first knot; past the last its last span goes on, as the
        runtime evaluates it (an animation's last knot can fall short of its duration)."""
        if not self.controls:
            return IDENTITY.get(self.dimension, (0.0,) * self.dimension)
        if len(self.controls) == 1 or not self.knots:
            return self.controls[0]
        return _sample(self.knots, self.controls, self.degree, time_s)

    @property
    def constant(self) -> bool:
        return len(self.controls) <= 1 or not self.knots


def _sample(knots: tuple[float, ...], controls: tuple[Vector, ...], degree: int, time_s: float) -> Vector:
    """De Boor's algorithm on Granny's knot array: knot ``i`` ends the span whose last control is ``i``; knots and
    controls before the first repeat it (granny.md "Curves")."""
    knot_count = len(knots)
    i = min(
        bisect.bisect_right(knots, time_s), knot_count - 1
    )  # the first knot after t; past the last, its span goes on
    if degree <= 0:
        return controls[i]

    def u(k: int) -> float:
        return knots[min(max(k, 0), knot_count - 1)]

    points = [controls[min(max(j, 0), knot_count - 1)] for j in range(i - degree, i + 1)]
    for level in range(1, degree + 1):
        for j in range(degree, level - 1, -1):
            span_start, span_end = u(i - degree + j - 1), u(i + j - level)
            blend = (time_s - span_start) / (span_end - span_start) if span_end - span_start > 1e-12 else 0.0
            points[j] = tuple(
                (1.0 - blend) * earlier + blend * later for earlier, later in zip(points[j - 1], points[j], strict=True)
            )
    return points[degree]


def _floats(value) -> list[float]:
    if isinstance(value, gr.SGrannyArray):
        return [component for record in value.records() for component in record]
    if isinstance(value, tuple | list):  # an empty array reads as an empty list
        return [float(number) for number in value if isinstance(number, int | float)]
    return [] if value is None else [value]


def _rows(values: list[float], values_per_row: int) -> list[Vector]:
    return (
        [tuple(values[k : k + values_per_row]) for k in range(0, len(values) - values_per_row + 1, values_per_row)]
        if values_per_row
        else []
    )


def _trunc_float(bits: int) -> float:
    """A float kept as its top 16 bits (``OneOverKnotScaleTrunc``)."""
    return struct.unpack("<f", struct.pack("<I", (bits & 0xFFFF) << 16))[0]


def _quaternion(
    first_word: int, second_word: int, third_word: int, scales: list[float], offsets: list[float], bits: int
) -> Vector:
    """One packed unit quaternion: three components of ``bits`` (15 or 7), the top bits of the second and third
    word naming the one left out, the first word's its sign; each component scaled by its own selector."""
    top, mask = 1 << bits, (1 << bits) - 1
    largest = ((second_word & top) >> (bits - 1)) | ((third_word & top) >> bits)
    components = [0.0] * 4
    for step, value in enumerate((first_word, second_word, third_word), 1):
        component = (largest + step) % 4
        components[component] = (value & mask) * scales[component] + offsets[component]
    components[largest] = math.sqrt(max(0.0, 1.0 - sum(x * x for x in components))) * (-1 if first_word & top else 1)
    return tuple(components)


def read_curve(curve2, dimension: int) -> SCurve:
    """A ``Curve2`` record (its ``CurveData`` variant) as a ``SCurve`` of ``dimension`` (3 position, 4 orientation,
    9 scale and shear). Raises ``CGrannyError`` for a packed format this reader does not know."""
    data = curve2["CurveData"] if curve2 is not None else None
    if data is None:
        return SCurve(dimension, 0, (), (), "DaIdentity")
    head = data.m_members[0].name
    kind = head.removeprefix("CurveDataHeader_")
    degree = int(data[head]["Degree"])
    if kind == "DaIdentity":
        return SCurve(dimension, 0, (), (), kind)
    if kind in ("DaConstant32f", "D3Constant32f", "D4Constant32f"):
        return SCurve(dimension, 0, (), (_expand(_floats(data["Controls"]), dimension),), kind)
    if kind == "DaK32fC32f":
        knots = _floats(data["Knots"])
        controls = _floats(data["Controls"])
        values_per_knot = len(controls) // len(knots) if knots else 0
        return SCurve(
            dimension,
            degree,
            tuple(knots),
            tuple(_expand(row, dimension) for row in _rows(controls, values_per_knot)),
            kind,
        )
    if kind == "DaKeyframes32f":
        controls = _rows(_floats(data["Controls"]), int(data["Dimension"]))
        knots = tuple(float(k) for k in range(len(controls)))
        return SCurve(dimension, degree, knots, tuple(_expand(row, dimension) for row in controls), kind)
    return _packed(data, kind, degree, dimension)


def _packed(data, kind: str, degree: int, dimension: int) -> SCurve:
    """The quantized formats: n knots, then the controls, in one integer array; scaled and offset."""
    raw = [int(value) if not isinstance(value, float) else value for value in _floats(data["KnotsControls"])]
    if kind in ("D4nK16uC15u", "D4nK8uC7u"):
        knot_count, bits = len(raw) // 4, 15 if kind == "D4nK16uC15u" else 7
        scale = float(data["OneOverKnotScale"])
        if scale == 0:
            raise gr.CGrannyError(f"curve format {kind}: a knot scale of zero")
        selectors = int(data["ScaleOffsetTableEntries"])
        selector_list = [(selectors >> shift) & 0xF for shift in (0, 4, 8, 12)]  # one selector per quaternion component
        scales = [_QUAT_SCALES[selector] / ((1 << bits) - 1) for selector in selector_list]
        offsets = [_QUAT_OFFSETS[selector] for selector in selector_list]
        controls = [
            _quaternion(*raw[knot_count + 3 * k : knot_count + 3 * k + 3], scales, offsets, bits)
            for k in range(knot_count)
        ]
        return SCurve(dimension, degree, tuple(k / scale for k in raw[:knot_count]), tuple(controls), kind)
    knot_scale = _trunc_float(int(data["OneOverKnotScaleTrunc"])) if "OneOverKnotScaleTrunc" in data else 1.0
    if knot_scale == 0:
        raise gr.CGrannyError(f"curve format {kind}: a knot scale of zero")
    if kind in ("DaK16uC16u", "DaK8uC8u"):
        scale_offsets = _floats(data["ControlScaleOffsets"])
        values_per_knot = len(scale_offsets) // 2
        scales, offsets = scale_offsets[:values_per_knot], scale_offsets[values_per_knot:]
    else:
        scales = _floats(data["ControlScales"] if "ControlScales" in data else data["ControlScale"])
        offsets = _floats(data["ControlOffsets"] if "ControlOffsets" in data else data["ControlOffset"])
        values_per_knot = 1 if kind[2:4] == "I1" else 3
    knot_count = len(raw) // (1 + values_per_knot)
    rows = _rows(raw[knot_count:], values_per_knot)
    if kind.startswith("D3I1"):  # one value along a fixed direction
        controls = [
            tuple(
                row[0] * control_scale + control_offset
                for control_scale, control_offset in zip(scales, offsets, strict=True)
            )
            for row in rows
        ]
    elif kind.startswith("D9I1"):  # a uniform scale
        controls = [_diagonal((row[0] * scales[0] + offsets[0],) * 3) for row in rows]
    elif kind.startswith("D9I3"):  # a scale per axis
        controls = [
            _diagonal(
                tuple(
                    value * control_scale + control_offset
                    for value, control_scale, control_offset in zip(row, scales, offsets, strict=True)
                )
            )
            for row in rows
        ]
    elif kind in ("D3K16uC16u", "D3K8uC8u", "DaK16uC16u", "DaK8uC8u"):
        controls = [
            _expand(
                [
                    value * control_scale + control_offset
                    for value, control_scale, control_offset in zip(row, scales, offsets, strict=True)
                ],
                dimension,
            )
            for row in rows
        ]
    else:
        raise gr.CGrannyError(f"curve format {kind} is not read")
    return SCurve(dimension, degree, tuple(k / knot_scale for k in raw[:knot_count]), tuple(controls), kind)


def _diagonal(scales: Vector) -> Vector:
    return (scales[0], 0.0, 0.0, 0.0, scales[1], 0.0, 0.0, 0.0, scales[2])


def _expand(values, dimension: int) -> Vector:
    """A control of fewer components than the curve's dimension (a short constant) padded from the identity."""
    values = tuple(float(component) for component in values)
    return values[:dimension] + IDENTITY.get(dimension, (0.0,) * dimension)[len(values) :]


# Skeletons


@dataclass(frozen=True)
class SBone:
    """One bone: its name, parent (None for a root), rest transform in its parent (``position``, ``orientation``
    x y z w, ``scale_shear`` row by row) and the inverse of its rest place in the model, 16 values as stored (for row
    vectors: the move in the last row)."""

    name: str
    parent: int | None
    position: Vector
    orientation: Vector
    scale_shear: Vector
    inverse_world: Vector


@dataclass(frozen=True)
class SSkeleton:
    name: str
    bones: tuple[SBone, ...]

    def index(self) -> dict[str, int]:
        return {bone.name: k for k, bone in enumerate(self.bones)}


def read_skeletons(root) -> list[SSkeleton]:
    """The file's skeletons, bones parent first as Granny keeps them."""
    out = []
    for skeleton in root.get("Skeletons") or []:
        if skeleton is None:
            continue
        bones = []
        for bone in skeleton["Bones"] or []:
            parent, transform = int(bone["ParentIndex"]), bone["Transform"]
            bones.append(SBone(bone["Name"], parent if parent >= 0 else None, tuple(transform["position"]),
                              tuple(transform["orientation"]), tuple(transform["scale_shear"]), tuple(bone["InverseWorldTransform"])))  # fmt: skip
        out.append(SSkeleton(skeleton["Name"], tuple(bones)))
    return out


def bone_names(mesh) -> list[str]:
    """A mesh's bone bindings by name, in the order its vertices' ``BoneIndices`` count them (a rigid mesh: the one
    bone it moves with)."""
    return [binding["BoneName"] if binding is not None else "" for binding in mesh.get("BoneBindings") or []]


def model_skeletons(root) -> dict[str, int]:
    """Mesh name -> the number of its model's skeleton in ``Skeletons`` (a mesh no model binds is left out)."""
    skeletons = list(root.get("Skeletons") or [])
    out: dict[str, int] = {}
    for model in root.get("Models") or []:
        if model is None or model["Skeleton"] is None:
            continue
        skeleton_index = next(
            (
                k
                for k, skeleton in enumerate(skeletons)
                if skeleton is not None and skeleton.m_at == model["Skeleton"].m_at
            ),
            None,
        )
        for binding in model["MeshBindings"] or []:
            mesh = binding["Mesh"] if binding is not None else None
            if mesh is not None and skeleton_index is not None:
                out.setdefault(mesh["Name"], skeleton_index)
    return out


# Animations


@dataclass(frozen=True)
class STransformTrack:
    """One bone's motion: its name and three curves (position, orientation, scale and shear)."""

    name: str
    position: SCurve
    orientation: SCurve
    scale_shear: SCurve

    def at(self, time_s: float) -> tuple[Vector, Vector, Vector]:
        """The bone's (position, unit orientation, scale-shear) in its parent at ``time_s`` seconds."""
        orientation = self.orientation.at(time_s)
        length = math.sqrt(sum(component * component for component in orientation)) or 1.0
        return (
            self.position.at(time_s),
            tuple(component / length for component in orientation),
            self.scale_shear.at(time_s),
        )


@dataclass(frozen=True)
class STrackGroup:
    """The tracks of one model (Granny's track group, named after the model)."""

    name: str
    tracks: tuple[STransformTrack, ...]

    def by_name(self) -> dict[str, STransformTrack]:
        return {track.name: track for track in self.tracks}


@dataclass(frozen=True)
class SAnimation:
    """An animation: its name (the studio's ``.max`` file), length and frame step in seconds, its track groups."""

    name: str
    duration: float
    time_step: float
    groups: tuple[STrackGroup, ...]

    def group_for(self, skeleton: SSkeleton | None) -> STrackGroup | None:
        """The track group that names the most bones of ``skeleton`` (the first group without one)."""
        if not self.groups:
            return None
        if skeleton is None:
            return self.groups[0]
        names = set(skeleton.index())
        return max(self.groups, key=lambda group: sum(track.name in names for track in group.tracks))


def read_animations(root) -> list[SAnimation]:
    """The file's animations with every transform track's curves decoded."""
    out = []
    for animation in root.get("Animations") or []:
        if animation is None:
            continue
        groups = []
        for group in animation["TrackGroups"] or []:
            if group is not None:
                tracks = tuple(STransformTrack(track["Name"], read_curve(track["PositionCurve"], 3),
                                              read_curve(track["OrientationCurve"], 4), read_curve(track["ScaleShearCurve"], 9))
                               for track in group["TransformTracks"] or [] if track is not None)  # fmt: skip
                groups.append(STrackGroup(group["Name"], tracks))
        out.append(
            SAnimation(animation["Name"], float(animation["Duration"]), float(animation["TimeStep"]), tuple(groups))
        )
    return out
