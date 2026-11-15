"""Granny 2 skeletons, skins and animations (Anno 1404's and 1701's ``.gr2``), read from the type trees
``grannyread`` walks: the bones with their rest transforms, each mesh's bone bindings and the animations' transform
tracks, whose curves (B-splines in every packed format the games ship) are evaluated here, without numpy.

``read_skeletons(root)`` and ``read_animations(root)`` are the entries; ``Curve.at(t)`` samples a curve and
``TransformTrack.at(t)`` a bone's position, orientation and scale-shear; ``bone_names(mesh)`` maps a skinned mesh's
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
class Curve:
    """A curve as Granny evaluates it: ``knots`` in seconds, one control per knot (each ``dimension`` values), its
    ``degree``; a control without knots is a constant, no control at all the identity of its dimension."""

    dimension: int
    degree: int
    knots: tuple[float, ...]
    controls: tuple[Vector, ...]
    format: str = ""  # the packed format it was stored in (``DaK32fC32f``, ``D4nK16uC15u``, ...)

    def at(self, t: float) -> Vector:
        """The value at ``t`` seconds: held before the first knot; past the last its last span goes on, as the
        runtime evaluates it (an animation's last knot can fall short of its duration)."""
        if not self.controls:
            return IDENTITY.get(self.dimension, (0.0,) * self.dimension)
        if len(self.controls) == 1 or not self.knots:
            return self.controls[0]
        return _sample(self.knots, self.controls, self.degree, t)

    @property
    def constant(self) -> bool:
        return len(self.controls) <= 1 or not self.knots


def _sample(knots: tuple[float, ...], controls: tuple[Vector, ...], degree: int, t: float) -> Vector:
    """De Boor's algorithm on Granny's knot array: knot ``i`` ends the span whose last control is ``i``; knots and
    controls before the first repeat it (granny.md "Curves")."""
    n = len(knots)
    i = min(bisect.bisect_right(knots, t), n - 1)  # the first knot after t; past the last, its span goes on
    if degree <= 0:
        return controls[i]

    def u(k: int) -> float:
        return knots[min(max(k, 0), n - 1)]

    d = [controls[min(max(j, 0), n - 1)] for j in range(i - degree, i + 1)]
    for r in range(1, degree + 1):
        for j in range(degree, r - 1, -1):
            lo, hi = u(i - degree + j - 1), u(i + j - r)
            a = (t - lo) / (hi - lo) if hi - lo > 1e-12 else 0.0
            d[j] = tuple((1.0 - a) * p + a * q for p, q in zip(d[j - 1], d[j], strict=True))
    return d[degree]


def _floats(value) -> list[float]:
    if isinstance(value, gr.GrannyArray):
        return [c for r in value.records() for c in r]
    if isinstance(value, tuple | list):  # an empty array reads as an empty list
        return [float(v) for v in value if isinstance(v, int | float)]
    return [] if value is None else [value]


def _rows(values: list[float], width: int) -> list[Vector]:
    return [tuple(values[k : k + width]) for k in range(0, len(values) - width + 1, width)] if width else []


def _trunc_float(bits: int) -> float:
    """A float kept as its top 16 bits (``OneOverKnotScaleTrunc``)."""
    return struct.unpack("<f", struct.pack("<I", (bits & 0xFFFF) << 16))[0]


def _quaternion(a: int, b: int, c: int, scales: list[float], offsets: list[float], bits: int) -> Vector:
    """One packed unit quaternion: three components of ``bits`` (15 or 7), the top bits of the second and third
    word naming the one left out, the first word's its sign; each component scaled by its own selector."""
    top, mask = 1 << bits, (1 << bits) - 1
    largest = ((b & top) >> (bits - 1)) | ((c & top) >> bits)
    q = [0.0] * 4
    for step, value in enumerate((a, b, c), 1):
        at = (largest + step) % 4
        q[at] = (value & mask) * scales[at] + offsets[at]
    q[largest] = math.sqrt(max(0.0, 1.0 - sum(x * x for x in q))) * (-1 if a & top else 1)
    return tuple(q)


def read_curve(curve2, dimension: int) -> Curve:
    """A ``Curve2`` record (its ``CurveData`` variant) as a ``Curve`` of ``dimension`` (3 position, 4 orientation,
    9 scale and shear). Raises ``GrannyError`` for a packed format this reader does not know."""
    data = curve2["CurveData"] if curve2 is not None else None
    if data is None:
        return Curve(dimension, 0, (), (), "DaIdentity")
    head = data.members[0].name
    kind = head.removeprefix("CurveDataHeader_")
    degree = int(data[head]["Degree"])
    if kind == "DaIdentity":
        return Curve(dimension, 0, (), (), kind)
    if kind in ("DaConstant32f", "D3Constant32f", "D4Constant32f"):
        return Curve(dimension, 0, (), (_expand(_floats(data["Controls"]), dimension),), kind)
    if kind == "DaK32fC32f":
        knots = _floats(data["Knots"])
        controls = _floats(data["Controls"])
        width = len(controls) // len(knots) if knots else 0
        return Curve(
            dimension, degree, tuple(knots), tuple(_expand(r, dimension) for r in _rows(controls, width)), kind
        )
    if kind == "DaKeyframes32f":
        controls = _rows(_floats(data["Controls"]), int(data["Dimension"]))
        knots = tuple(float(k) for k in range(len(controls)))
        return Curve(dimension, degree, knots, tuple(_expand(r, dimension) for r in controls), kind)
    return _packed(data, kind, degree, dimension)


def _packed(data, kind: str, degree: int, dimension: int) -> Curve:
    """The quantized formats: n knots, then the controls, in one integer array; scaled and offset."""
    raw = [int(v) if not isinstance(v, float) else v for v in _floats(data["KnotsControls"])]
    if kind in ("D4nK16uC15u", "D4nK8uC7u"):
        n, bits = len(raw) // 4, 15 if kind == "D4nK16uC15u" else 7
        scale = float(data["OneOverKnotScale"])
        selectors = int(data["ScaleOffsetTableEntries"])
        sel = [(selectors >> s) & 0xF for s in (0, 4, 8, 12)]  # one selector per quaternion component
        scales = [_QUAT_SCALES[s] / ((1 << bits) - 1) for s in sel]
        offsets = [_QUAT_OFFSETS[s] for s in sel]
        controls = [_quaternion(*raw[n + 3 * k : n + 3 * k + 3], scales, offsets, bits) for k in range(n)]
        return Curve(dimension, degree, tuple(k / scale for k in raw[:n]), tuple(controls), kind)
    knot_scale = _trunc_float(int(data["OneOverKnotScaleTrunc"])) if "OneOverKnotScaleTrunc" in data else 1.0
    if kind in ("DaK16uC16u", "DaK8uC8u"):
        so = _floats(data["ControlScaleOffsets"])
        width = len(so) // 2
        scales, offsets = so[:width], so[width:]
    else:
        scales = _floats(data["ControlScales"] if "ControlScales" in data else data["ControlScale"])
        offsets = _floats(data["ControlOffsets"] if "ControlOffsets" in data else data["ControlOffset"])
        width = 1 if kind[2:4] == "I1" else 3
    n = len(raw) // (1 + width)
    rows = _rows(raw[n:], width)
    if kind.startswith("D3I1"):  # one value along a fixed direction
        controls = [tuple(r[0] * s + o for s, o in zip(scales, offsets, strict=True)) for r in rows]
    elif kind.startswith("D9I1"):  # a uniform scale
        controls = [_diagonal((r[0] * scales[0] + offsets[0],) * 3) for r in rows]
    elif kind.startswith("D9I3"):  # a scale per axis
        controls = [_diagonal(tuple(v * s + o for v, s, o in zip(r, scales, offsets, strict=True))) for r in rows]
    elif kind in ("D3K16uC16u", "D3K8uC8u", "DaK16uC16u", "DaK8uC8u"):
        controls = [_expand([v * s + o for v, s, o in zip(r, scales, offsets, strict=True)], dimension) for r in rows]
    else:
        raise gr.GrannyError(f"curve format {kind} is not read")
    return Curve(dimension, degree, tuple(k / knot_scale for k in raw[:n]), tuple(controls), kind)


def _diagonal(s: Vector) -> Vector:
    return (s[0], 0.0, 0.0, 0.0, s[1], 0.0, 0.0, 0.0, s[2])


def _expand(values, dimension: int) -> Vector:
    """A control of fewer components than the curve's dimension (a short constant) padded from the identity."""
    values = tuple(float(v) for v in values)
    return values[:dimension] + IDENTITY.get(dimension, (0.0,) * dimension)[len(values) :]


# Skeletons


@dataclass(frozen=True)
class Bone:
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
class Skeleton:
    name: str
    bones: tuple[Bone, ...]

    def index(self) -> dict[str, int]:
        return {b.name: k for k, b in enumerate(self.bones)}


def read_skeletons(root) -> list[Skeleton]:
    """The file's skeletons, bones parent first as Granny keeps them."""
    out = []
    for s in root.get("Skeletons") or []:
        if s is None:
            continue
        bones = []
        for b in s["Bones"] or []:
            parent, t = int(b["ParentIndex"]), b["Transform"]
            bones.append(Bone(b["Name"], parent if parent >= 0 else None, tuple(t["position"]),
                              tuple(t["orientation"]), tuple(t["scale_shear"]), tuple(b["InverseWorldTransform"])))  # fmt: skip
        out.append(Skeleton(s["Name"], tuple(bones)))
    return out


def bone_names(mesh) -> list[str]:
    """A mesh's bone bindings by name, in the order its vertices' ``BoneIndices`` count them (a rigid mesh: the one
    bone it moves with)."""
    return [b["BoneName"] if b is not None else "" for b in mesh.get("BoneBindings") or []]


def model_skeletons(root) -> dict[str, int]:
    """Mesh name -> the number of its model's skeleton in ``Skeletons`` (a mesh no model binds is left out)."""
    skeletons = list(root.get("Skeletons") or [])
    out: dict[str, int] = {}
    for model in root.get("Models") or []:
        if model is None or model["Skeleton"] is None:
            continue
        at = next((k for k, s in enumerate(skeletons) if s is not None and s.at == model["Skeleton"].at), None)
        for binding in model["MeshBindings"] or []:
            mesh = binding["Mesh"] if binding is not None else None
            if mesh is not None and at is not None:
                out.setdefault(mesh["Name"], at)
    return out


# Animations


@dataclass(frozen=True)
class TransformTrack:
    """One bone's motion: its name and three curves (position, orientation, scale and shear)."""

    name: str
    position: Curve
    orientation: Curve
    scale_shear: Curve

    def at(self, t: float) -> tuple[Vector, Vector, Vector]:
        """The bone's (position, unit orientation, scale-shear) in its parent at ``t`` seconds."""
        q = self.orientation.at(t)
        n = math.sqrt(sum(c * c for c in q)) or 1.0
        return self.position.at(t), tuple(c / n for c in q), self.scale_shear.at(t)


@dataclass(frozen=True)
class TrackGroup:
    """The tracks of one model (Granny's track group, named after the model)."""

    name: str
    tracks: tuple[TransformTrack, ...]

    def by_name(self) -> dict[str, TransformTrack]:
        return {t.name: t for t in self.tracks}


@dataclass(frozen=True)
class Animation:
    """An animation: its name (the studio's ``.max`` file), length and frame step in seconds, its track groups."""

    name: str
    duration: float
    time_step: float
    groups: tuple[TrackGroup, ...]

    def group_for(self, skeleton: Skeleton | None) -> TrackGroup | None:
        """The track group that names the most bones of ``skeleton`` (the first group without one)."""
        if not self.groups:
            return None
        if skeleton is None:
            return self.groups[0]
        names = set(skeleton.index())
        return max(self.groups, key=lambda g: sum(t.name in names for t in g.tracks))


def read_animations(root) -> list[Animation]:
    """The file's animations with every transform track's curves decoded."""
    out = []
    for a in root.get("Animations") or []:
        if a is None:
            continue
        groups = []
        for g in a["TrackGroups"] or []:
            if g is not None:
                tracks = tuple(TransformTrack(t["Name"], read_curve(t["PositionCurve"], 3),
                                              read_curve(t["OrientationCurve"], 4), read_curve(t["ScaleShearCurve"], 9))
                               for t in g["TransformTracks"] or [] if t is not None)  # fmt: skip
                groups.append(TrackGroup(g["Name"], tracks))
        out.append(Animation(a["Name"], float(a["Duration"]), float(a["TimeStep"]), tuple(groups)))
    return out
