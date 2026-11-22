"""RTPC curves, layer containers and attenuations of a Wwise bank, read and written (versions 112, 132 to 135, 172).

``SRtpc`` is one entry of an RTPC list (a game parameter driving a property along a curve of ``SPoint``s); a node base
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
from .bnk import SHircObject
from .bnkmusic import CReader

INTERPOLATIONS = 10  # 0 to 9: the curve shapes Wwise knows (4 linear, 9 constant)
NO_CURVE = 255  # a curve-to-use slot that names none


@dataclass
class SPoint:
    x: float
    y: float
    interp: int = 4


@dataclass
class SRtpc:
    """One RTPC entry: a game parameter that drives a property of the object along a curve."""

    parameter: int
    kind: int
    accum: int
    driven: int  # the property it drives (in a node base the ids of ``bnkgraph.layout(version).prop_ids``)
    curve_id: int
    scaling: int
    points: list[SPoint] = field(default_factory=list)


def read_points(reader: CReader, count: int) -> list[SPoint]:
    return [SPoint(reader.f32(), reader.f32(), reader.u32()) for _ in range(count)]


def render_points(points: list[SPoint]) -> bytes:
    return b"".join(struct.pack("<ffI", point.x, point.y, point.interp) for point in points)


def check_curve(positions: Sequence[float], interpolations: Sequence[int], minimum: int = 2) -> None:
    """``ValueError`` for a curve's points the engine would not take: under ``minimum`` points, an x that is not
    finite, x going backwards, an interpolation outside 0 to 9 (``INTERPOLATIONS``)."""
    if len(positions) < minimum:
        raise ValueError(f"a curve has {minimum} point{'s' if minimum != 1 else ''} or more")
    if not all(math.isfinite(x) for x in positions):
        raise ValueError("a curve's points are finite numbers")
    if any(following < previous for previous, following in zip(positions, positions[1:], strict=False)):
        raise ValueError("a curve's x values do not go backwards")
    if any(not 0 <= i < INTERPOLATIONS for i in interpolations):
        raise ValueError(f"the interpolation of a point is 0 to {INTERPOLATIONS - 1}")


def check_points(points: list[SPoint]) -> None:
    """``check_curve`` of a curve's points (two or more) and their y values finite."""
    if not all(math.isfinite(point.y) for point in points):
        raise ValueError("a curve's points are finite numbers")
    check_curve([point.x for point in points], [point.interp for point in points])


# RTPC lists


def read_rtpcs(reader: CReader) -> list[SRtpc]:
    out = []
    for _ in range(reader.u16()):
        head = (reader.u32(), reader.u8(), reader.u8(), reader.u8(), reader.u32(), reader.u8())
        out.append(SRtpc(*head, read_points(reader, reader.u16())))
    return out


def render_rtpcs(rtpcs: list[SRtpc]) -> bytes:
    out = struct.pack("<H", len(rtpcs))
    for rtpc in rtpcs:
        out += struct.pack(
            "<IBBBIBH",
            rtpc.parameter,
            rtpc.kind,
            rtpc.accum,
            rtpc.driven,
            rtpc.curve_id,
            rtpc.scaling,
            len(rtpc.points),
        )
        out += render_points(rtpc.points)
    return out


def rtpc_span(hirc_object: SHircObject, version: int, children: Sequence[int] | None = None) -> tuple[int, int]:
    """Where the RTPC list of an object's node base sits in its payload (the last block of the node base). In the older
    banks the object's own end is checked: a Sound's payload ends with the list, a container's child list (the ids
    ``children``, when given) follows at its gap; ``ValueError`` where it does not, so a layout we misread is refused."""
    reader = CReader(hirc_object.payload)
    reader.m_pos = bnkgraph.base_start(hirc_object, version)
    bnkbase.skip_before_rtpc(reader, version)
    start = reader.m_pos
    bnkmusic.skip_rtpc_list(reader)
    if bnkgraph.family(version) != bnkgraph.NEW:
        _check_node_end(hirc_object, reader.m_pos, children)
    return start, reader.m_pos


def _check_node_end(hirc_object: SHircObject, end: int, children: Sequence[int] | None) -> None:
    if hirc_object.type == bnkgraph.SOUND:
        is_whole = end == len(hirc_object.payload)
    elif children is None:
        is_whole = True
    else:
        expected_end = end + bnkgraph.CHILD_GAP[hirc_object.type]
        is_whole = hirc_object.payload[expected_end : expected_end + 4 + 4 * len(children)] == struct.pack(
            f"<{1 + len(children)}I", len(children), *children
        )
    if not is_whole:
        raise ValueError(
            f"the node base of {hirc_object.type_name} {hirc_object.id} does not end where the object goes on"
        )


# layer containers


@dataclass
class SLayer:
    """A layer of a layer container: its own RTPC entries, the game parameter that moves it and its associations (a
    child and the curve its volume follows)."""

    id: int
    rtpcs: list[SRtpc]
    parameter: int
    kind: int
    curves: list[tuple[int, list[SPoint]]]


def read_layers(payload: bytes, offset_bytes: int) -> tuple[list[SLayer], bytes]:
    """The layers behind a layer container's child list that ends at ``at``, and the bytes after them (the
    continuous-validation flag, absent in 112); ``ValueError`` when more than one byte is left."""
    reader = CReader(payload)
    reader.m_pos = offset_bytes
    layers = []
    for _ in range(reader.u32()):
        ident, rtpcs, parameter, kind = reader.u32(), read_rtpcs(reader), reader.u32(), reader.u8()
        curves = [(reader.u32(), read_points(reader, reader.u32())) for _ in range(reader.u32())]
        layers.append(SLayer(ident, rtpcs, parameter, kind, curves))
    rest = payload[reader.m_pos :]
    if len(rest) > 1:
        raise ValueError(f"{len(rest)} bytes follow the layers")
    return layers, rest


def render_layers(layers: list[SLayer], rest: bytes) -> bytes:
    out = struct.pack("<I", len(layers))
    for layer in layers:
        out += struct.pack("<I", layer.id) + render_rtpcs(layer.rtpcs) + struct.pack("<IB", layer.parameter, layer.kind)
        out += struct.pack("<I", len(layer.curves))
        for child, points in layer.curves:
            out += struct.pack("<II", child, len(points)) + render_points(points)
    return out + rest


# attenuations


@dataclass
class SCurve:
    scaling: int
    points: list[SPoint]


@dataclass
class SAttenuation:
    """An Attenuation object: the height-spread byte (172), the cone block (kept as read), per slot (volume dry, the two
    auxiliary volumes, low-pass, high-pass, spread, focus ...) the curve it uses, the curves and the RTPC entries."""

    height: int | None
    cone: bytes  # the cone flag, and the 20 bytes of the cone when it is on
    use: list[int]
    curves: list[SCurve]
    rtpcs: list[SRtpc]


def read_attenuation(hirc_object: SHircObject, version: int) -> SAttenuation:
    """An attenuation by its version's layout; ``ValueError`` when it does not read to the last byte."""
    layout = bnkgraph.layout(version)
    reader = CReader(hirc_object.payload)
    height = reader.u8() if layout.attenuation_height else None
    flag = reader.take(1)
    cone = flag + (reader.take(20) if flag[0] else b"")
    use = list(reader.take(layout.attenuation_slots))
    curves = [SCurve(reader.u8(), read_points(reader, reader.u16())) for _ in range(reader.u8())]
    out = SAttenuation(height, cone, use, curves, read_rtpcs(reader))
    if not reader.done():
        raise ValueError(f"{len(hirc_object.payload) - reader.m_pos} bytes follow the attenuation")
    return out


def render_attenuation(attenuation: SAttenuation, version: int) -> bytes:
    layout = bnkgraph.layout(version)
    out = (
        struct.pack("<B", attenuation.height) if layout.attenuation_height and attenuation.height is not None else b""
    ) + attenuation.cone
    out += bytes(attenuation.use) + struct.pack("<B", len(attenuation.curves))
    for curve in attenuation.curves:
        out += struct.pack("<BH", curve.scaling, len(curve.points)) + render_points(curve.points)
    return out + render_rtpcs(attenuation.rtpcs)
