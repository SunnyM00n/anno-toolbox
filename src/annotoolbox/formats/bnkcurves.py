"""RTPC curves, layer containers and attenuations of a Wwise bank, read and written (versions 112, 132 to 135, 172).

``Rtpc`` is one entry of an RTPC list (a game parameter driving a property along a curve of ``Point``s); a node base
ends in such a list (``rtpc_span``: found in every version's banks), a layer and an attenuation have lists of their own.
``read_layers`` / ``render_layers`` the layers behind a layer container's child list, ``read_attenuation`` /
``render_attenuation`` an Attenuation object. Layouts and the counts they were read from: ``docs/sound/bank-objects.md``
"RTPC curves, layer containers and attenuations".
"""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field

from . import bnkbase, bnkgraph, bnkmusic
from .bnk import HircObject
from .bnkmusic import Reader

POINT_SIZE = 12
INTERPOLATIONS = 10  # 0 to 9: the curve shapes Wwise knows (4 linear, 9 constant)
NO_CURVE = 255  # a curve-to-use slot that names none


@dataclass
class Point:
    x: float
    y: float
    interp: int = 4


@dataclass
class Rtpc:
    """One RTPC entry: a game parameter that drives a property of the object along a curve."""

    parameter: int
    kind: int
    accum: int
    driven: int  # the property it drives (in a node base the ids of ``bnkgraph.layout(version).prop_ids``)
    curve_id: int
    scaling: int
    points: list[Point] = field(default_factory=list)


def read_points(r: Reader, count: int) -> list[Point]:
    return [Point(r.f32(), r.f32(), r.u32()) for _ in range(count)]


def render_points(points: list[Point]) -> bytes:
    return b"".join(struct.pack("<ffI", p.x, p.y, p.interp) for p in points)


def check_curve(xs: Sequence[float], interps: Sequence[int], minimum: int = 2) -> None:
    """``ValueError`` for a curve's points the engine would not take: under ``minimum`` points, an x that is not
    finite, x going backwards, an interpolation outside 0 to 9 (``INTERPOLATIONS``)."""
    if len(xs) < minimum:
        raise ValueError(f"a curve has {minimum} point{'s' if minimum != 1 else ''} or more")
    if not all(math.isfinite(x) for x in xs):
        raise ValueError("a curve's points are finite numbers")
    if any(b < a for a, b in zip(xs, xs[1:], strict=False)):
        raise ValueError("a curve's x values do not go backwards")
    if any(not 0 <= i < INTERPOLATIONS for i in interps):
        raise ValueError(f"the interpolation of a point is 0 to {INTERPOLATIONS - 1}")


def check_points(points: list[Point]) -> None:
    """``check_curve`` of a curve's points (two or more) and their y values finite."""
    if not all(math.isfinite(p.y) for p in points):
        raise ValueError("a curve's points are finite numbers")
    check_curve([p.x for p in points], [p.interp for p in points])


# RTPC lists


def read_rtpcs(r: Reader) -> list[Rtpc]:
    out = []
    for _ in range(r.u16()):
        head = (r.u32(), r.u8(), r.u8(), r.u8(), r.u32(), r.u8())
        out.append(Rtpc(*head, read_points(r, r.u16())))
    return out


def render_rtpcs(rtpcs: list[Rtpc]) -> bytes:
    out = struct.pack("<H", len(rtpcs))
    for e in rtpcs:
        out += struct.pack("<IBBBIBH", e.parameter, e.kind, e.accum, e.driven, e.curve_id, e.scaling, len(e.points))
        out += render_points(e.points)
    return out


def rtpc_span(obj: HircObject, version: int, children: Sequence[int] | None = None) -> tuple[int, int]:
    """Where the RTPC list of an object's node base sits in its payload (the last block of the node base). In the older
    banks the object's own end is checked: a Sound's payload ends with the list, a container's child list (the ids
    ``children``, when given) follows at its gap; ``ValueError`` where it does not, so a layout we misread is refused."""
    r = Reader(obj.payload)
    r.pos = bnkgraph.base_start(obj, version)
    bnkbase.skip_before_rtpc(r, version)
    start = r.pos
    bnkmusic.skip_rtpc_list(r)
    if bnkgraph.family(version) != bnkgraph.NEW:
        _check_node_end(obj, r.pos, children)
    return start, r.pos


def _check_node_end(obj: HircObject, end: int, children: Sequence[int] | None) -> None:
    if obj.type == bnkgraph.SOUND:
        ok = end == len(obj.payload)
    elif children is None:
        ok = True
    else:
        at = end + bnkgraph.CHILD_GAP[obj.type]
        ok = obj.payload[at : at + 4 + 4 * len(children)] == struct.pack(
            f"<{1 + len(children)}I", len(children), *children
        )
    if not ok:
        raise ValueError(f"the node base of {obj.type_name} {obj.id} does not end where the object goes on")


# layer containers


@dataclass
class Layer:
    """A layer of a layer container: its own RTPC entries, the game parameter that moves it and its associations (a
    child and the curve its volume follows)."""

    id: int
    rtpcs: list[Rtpc]
    parameter: int
    kind: int
    curves: list[tuple[int, list[Point]]]


def read_layers(payload: bytes, at: int) -> tuple[list[Layer], bytes]:
    """The layers behind a layer container's child list that ends at ``at``, and the bytes after them (the
    continuous-validation flag, absent in 112); ``ValueError`` when more than one byte is left."""
    r = Reader(payload)
    r.pos = at
    layers = []
    for _ in range(r.u32()):
        ident, rtpcs, parameter, kind = r.u32(), read_rtpcs(r), r.u32(), r.u8()
        curves = [(r.u32(), read_points(r, r.u32())) for _ in range(r.u32())]
        layers.append(Layer(ident, rtpcs, parameter, kind, curves))
    rest = payload[r.pos :]
    if len(rest) > 1:
        raise ValueError(f"{len(rest)} bytes follow the layers")
    return layers, rest


def render_layers(layers: list[Layer], rest: bytes) -> bytes:
    out = struct.pack("<I", len(layers))
    for layer in layers:
        out += struct.pack("<I", layer.id) + render_rtpcs(layer.rtpcs) + struct.pack("<IB", layer.parameter, layer.kind)
        out += struct.pack("<I", len(layer.curves))
        for child, points in layer.curves:
            out += struct.pack("<II", child, len(points)) + render_points(points)
    return out + rest


# attenuations


@dataclass
class Curve:
    scaling: int
    points: list[Point]


@dataclass
class Attenuation:
    """An Attenuation object: the height-spread byte (172), the cone block (kept as read), per slot (volume dry, the two
    auxiliary volumes, low-pass, high-pass, spread, focus ...) the curve it uses, the curves and the RTPC entries."""

    height: int | None
    cone: bytes  # the cone flag, and the 20 bytes of the cone when it is on
    use: list[int]
    curves: list[Curve]
    rtpcs: list[Rtpc]


def read_attenuation(obj: HircObject, version: int) -> Attenuation:
    """An attenuation by its version's layout; ``ValueError`` when it does not read to the last byte."""
    rec = bnkgraph.layout(version)
    r = Reader(obj.payload)
    height = r.u8() if rec.attenuation_height else None
    flag = r.take(1)
    cone = flag + (r.take(20) if flag[0] else b"")
    use = list(r.take(rec.attenuation_slots))
    curves = [Curve(r.u8(), read_points(r, r.u16())) for _ in range(r.u8())]
    out = Attenuation(height, cone, use, curves, read_rtpcs(r))
    if not r.done():
        raise ValueError(f"{len(obj.payload) - r.pos} bytes follow the attenuation")
    return out


def render_attenuation(a: Attenuation, version: int) -> bytes:
    rec = bnkgraph.layout(version)
    out = (struct.pack("<B", a.height) if rec.attenuation_height and a.height is not None else b"") + a.cone
    out += bytes(a.use) + struct.pack("<B", len(a.curves))
    for c in a.curves:
        out += struct.pack("<BH", c.scaling, len(c.points)) + render_points(c.points)
    return out + render_rtpcs(a.rtpcs)
