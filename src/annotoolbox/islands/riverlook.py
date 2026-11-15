"""2070's river look translated to 117's river objects (inland-water "What is built"): each river point's bank band
``b``, depth scale ``d`` and speed foam ``s`` as 2070's ribbon builder feeds them to its river shader, and the river
and lake settings' colour as 117's absorption and scattered light.

``RiverFields`` reads the points of an island's river pieces and samples them anywhere on the converted rivers;
``flow_speeds`` turns them into 117's flow speeds per spline point, ``look_fields`` into a river's or a lake's look.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

# a point without them carries 2070's defaults (its serializer and its ribbon builder fill w 1, b 1, d 0.4, s 0.5)
DEFAULT_FIELDS = (1.0, 0.4, 0.5)  # b, d, s
BANK_MIN = 0.25  # 2070 draws no bank band narrower than this
FOAM_GREEN = 8.0  # river.fx scales the bank-foam green by this: the band's edge foams at 8 b / w, saturated
DEPTH_MIN = 0.05  # world units: the shortest depth scale kept (d = 0 would make the water opaque at its surface)
# 117's flow (rendering/water.md 1.3, 1.7): speed = u / FLOW_MAX; speed foam intensity sat(speed + 0.25)^4; the bank
# foam at the very edge sat(5 x 0.95^4 x speed), full from SHORE_FULL_FLOW. FLOW_CALM: 117's own calm flow, the floor
# for the motion 2070 does not vary (its ripples scroll at one speed everywhere; ``s`` only adds foam)
FLOW_MAX = 8.0
FLOW_CALM = 1.0
SHORE_FULL_FLOW = FLOW_MAX / (5 * 0.95**4)
# 117's look of a river (rendering/water.md 0.1-0.2): absorbed by the complement of the colours (a grey absorbs evenly),
# light scattered back by ScatterStrength x ScatterColor. 2070 mixes towards the colour times the light; deep 117 water
# returns about 1/(4 pi) of the sun per unit albedo where a lit ground of that colour returns 1/pi: SCATTER_GAIN.
# No cap on the strength: 2070's d = 0 pieces are opaque colour (its gorge cascades), which a capped strength drew black
NEUTRAL = (0.5, 0.5, 0.5)
SCATTER_GAIN = 4.0
ALBEDO_MAX = 0.95
RENDER_BLEND_BOTH = 3  # RenderFlags: IWF_BLEND_AT_START | IWF_BLEND_AT_END (2070 fades every piece at both ends)


@dataclass(frozen=True)
class Fields:
    """One point's 2070 fields: width ``w``, bank band ``b``, depth scale ``d``, speed foam ``s``."""

    w: float
    b: float
    d: float
    s: float


def read_point(point) -> Fields:
    """The fields of one ``m_Points`` item of a 2070 river piece (``isd2070.Node``), with 2070's defaults."""
    b, d, s = DEFAULT_FIELDS
    return Fields(
        float(point.value("w", "1")), float(point.value("b", str(b))), float(point.value("d", str(d))),
        float(point.value("s", str(s))),
    )  # fmt: skip


def visible(fields: list[Fields]) -> list[Fields]:
    """A piece's fields as they show: 2070 draws a piece's first and last point at opacity 0, so their values (d 0.21
    and s 0.5 at the median, against 0.5 and 0.17 inside) are never seen; they take their neighbour's."""
    if len(fields) < 3:
        return list(fields)
    return [fields[1], *fields[1:-1], fields[-2]]


class RiverFields:
    """2070's per-point fields of an island's river pieces (``pieces``: (points (x, z, ...), ``Fields`` per point)),
    sampled at any point on the converted rivers from the nearest stretch of a piece."""

    def __init__(self, pieces) -> None:
        self.segments = []
        for pts, fields in pieces:
            shown = visible(list(fields))
            for k in range(len(pts) - 1):
                self.segments.append((pts[k][:2], pts[k + 1][:2], shown[k], shown[k + 1]))
            if len(pts) == 1:
                self.segments.append((pts[0][:2], pts[0][:2], shown[0], shown[0]))

    def at(self, x: float, z: float) -> Fields:
        """The fields at (x, z): interpolated along the nearest piece stretch; 2070's defaults without pieces."""
        if not self.segments:
            return Fields(1.0, *DEFAULT_FIELDS)
        best, out = math.inf, None
        for (ax, az), (bx, bz), fa, fb in self.segments:
            dx, dz = bx - ax, bz - az
            t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz or 1e-9)))
            dist = math.hypot(x - ax - t * dx, z - az - t * dz)
            if dist < best:
                best, out = dist, (fa, fb, t)
        fa, fb, t = out
        return Fields(*(getattr(fa, k) + (getattr(fb, k) - getattr(fa, k)) * t for k in "wbds"))


def bank_foam(f: Fields) -> float:
    """How strongly 2070 foams a ribbon's edge (0..1): the bank band's green, ``8 b / w`` saturated; none without a
    band (``b`` under ``BANK_MIN``, or not narrower than the river)."""
    if f.b < BANK_MIN or f.b >= f.w or f.w <= 0:
        return 0.0
    return min(1.0, FOAM_GREEN * f.b / f.w)


def flow_speeds(fields: list[Fields]) -> list[tuple[float, float, float]]:
    """117's flow (left, centre, right) per spline point from 2070's fields: the centre so 117's speed-foam
    intensity equals 2070's speed foam ``s`` (``(u / 8 + 0.25)^4 = s``), the banks so 117's bank foam is full where
    2070's edge foams fully (``bank_foam``); never under ``FLOW_CALM``."""
    out = []
    for f in fields:
        centre = FLOW_MAX * (max(0.0, min(1.0, f.s)) ** 0.25 - 0.25)
        shore = SHORE_FULL_FLOW * bank_foam(f)
        out.append(tuple(round(max(FLOW_CALM, v), 3) for v in (shore, centre, shore)))
    return out


def depth_scale(d: float) -> float:
    """The view depth (world units) at which 2070's river shows its colour fully: the vertex red ``clamp(d, 0, 1)``
    read as ``(2 r)^2`` (river.fx)."""
    return max(DEPTH_MIN, (2 * max(0.0, min(1.0, d))) ** 2)


def river_view_depth(points, fields: list[Fields]) -> float:
    """A river's one ``ViewDepth`` (117 has no per-point one): the depth scale (``depth_scale``) of its points, each
    weighted by the length of course it stands for. 117's water keeps 1 % of the ground at ``ViewDepth``, 2070's
    none at its depth scale: the lake rule (``look_fields``) for an opaque colour."""
    n = len(points)
    if n == 0:
        return depth_scale(DEFAULT_FIELDS[1])
    if n == 1:
        return depth_scale(fields[0].d)
    seg = [math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(points, points[1:], strict=False)]
    weight = [(seg[k - 1] if k else 0.0) / 2 + (seg[k] if k < n - 1 else 0.0) / 2 for k in range(n)]
    total = sum(weight) or 1.0
    return sum(w * depth_scale(f.d) for w, f in zip(weight, fields, strict=True)) / total


def scatter(colour, view_depth: float) -> tuple[tuple[float, float, float], float]:
    """``ScatterColor`` and ``ScatterStrength`` for 2070's water colour at a view depth: the colour's hue, and a
    strength whose share of the light against the neutral absorption (``ln 100 / view_depth``) is the colour's
    brightness times ``SCATTER_GAIN`` (deep 117 water then returns about what 2070 mixes in), the share at most
    ``ALBEDO_MAX``."""
    r, g, b = colour[:3]
    top = max(r, g, b, 1e-6)
    albedo = min(ALBEDO_MAX, SCATTER_GAIN * top)
    absorption = math.log(100) / max(view_depth, 1e-6)
    return (r / top, g / top, b / top), absorption * albedo / (1 - albedo)


def look_fields(kind: str, values: dict, view_depth: float | None = None) -> dict[str, tuple[float, ...]]:
    """117's look fields of a river or lake object (rendering/water.md 0.1) for a 2070 setting (``values``: ``Color``,
    a lake's ``MaxDepth``): a neutral absorption reaching 1 % at the view depth, 2070's colour as the scattered light.
    A river's view depth is given (``river_view_depth``); a lake's is where 117 lets through what 2070's lake does at
    MaxDepth, ``1 - alpha`` (1 % for an opaque colour)."""
    r, g, b, a = values["Color"]
    if view_depth is None:
        depth = values.get("MaxDepth", 1.0)
        view_depth = depth * math.log(100) / -math.log(max(1.0 - a, 0.01))
    hue, strength = scatter((r, g, b), view_depth)
    return {
        "ColorShallow": NEUTRAL,
        "ColorDeep": NEUTRAL,
        "ScatterColor": hue,
        "ScatterStrength": (strength,),
        "ViewDepth": (view_depth,),
    }


def set_look(obj, kind: str, fields: dict[str, tuple[float, ...]], render_flags: int | None = None) -> int:
    """Write look fields into a cloned river or lake object's block (``kind``), adding a field the template lacks
    (vanilla's rivers carry ``ScatterStrength`` and ``RenderFlags`` on some objects only); returns the fields set."""
    from ..formats import filedb

    block = obj.find(kind)
    if block is None:
        return 0
    values = {name: struct.pack(f"<{len(vec)}f", *vec) for name, vec in fields.items()}
    if render_flags is not None:
        values["RenderFlags"] = struct.pack("<I", render_flags)
    for name, data in values.items():
        tag = block.find(name)
        if tag is None:
            block.children.append(filedb.Attrib(name, data))
        else:
            tag.data = data
    return len(values)
