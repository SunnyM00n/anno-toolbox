"""Rivers and coast lines of an Anno 1701 island: the lines of its ``.isc`` header, read for the editor's water tool
(``network``) and written back from its edits (``apply``).

A line is a list of points (``m_Polygon``): a position in world units, a width and the length along the line up to
the point. The editor counts tiles (``isd1701.TILE`` world units), so points and widths are scaled here. A river's
build and path blocking lists (``RiverBlockings``, ``RiverPathBlockings``) are redrawn by an edit
(``formats.isc.blockings``). ``clear`` empties them for a blank island.
"""

from __future__ import annotations

import copy
import math
import struct

import numpy as np

from ....formats.isd2070 import Node
from ....infra.i18n import tr
from ....othergames.isd1701 import TILE, Island1701, list_items
from ... import groundedit
from . import blockings as isc_blockings

LISTS = ("Rivers", "Coastlines")
BLOCKINGS = ("RiverBlockings", "RiverPathBlockings")


def line_items(header: Node | None, name: str) -> list[Node]:
    """The lines of a list that have a polygon of two points or more, in file order (what an edit's index counts)."""
    node = header.find(name) if header is not None else None
    out = []
    for item in list_items(node) if node is not None else []:
        poly = item.find("m_Polygon")
        if poly is not None and sum(1 for p in list_items(poly) if p.find("position") is not None) >= 2:
            out.append(item)
    return out


def _points(item: Node) -> list[Node]:
    return [p for p in list_items(item.find("m_Polygon")) if p.find("position") is not None]


def _position(point: Node) -> tuple[float, float, float]:
    pos = point.find("position")
    data = pos.find("data")
    if data is not None and data.data is not None and len(data.data) == 12:
        return struct.unpack("<3f", data.data)
    return tuple(float(pos.value(n, 0)) for n in "xyz")


def _set_position(point: Node, xyz: tuple[float, float, float]) -> None:
    pos = point.find("position")
    data = pos.find("data")
    if data is not None and data.data is not None and len(data.data) == 12:
        data.data = struct.pack("<3f", *xyz)
        return
    for name, value in zip("xyz", xyz, strict=True):
        node = pos.find(name)
        if node is not None:
            node.text = f"{value:.6f}"


def _set_text(point: Node, name: str, value: float) -> None:
    node = point.find(name)
    if node is not None:
        node.text = f"{value:.6f}"


def network(isl: Island1701) -> dict:
    """The water as the edit session takes it (``EditSession.water_net``): rivers (points x, height, z in tiles and
    the width at each) and coast lines (x, z in tiles); 1701 has no lakes."""
    rivers = []
    for item in line_items(isl.header, "Rivers"):
        pts = [_position(p) for p in _points(item)]
        rivers.append(
            {
                "points": [[x / TILE, y / TILE, z / TILE] for x, y, z in pts],
                "widths": [_points(item)[k].float("width") / TILE for k in range(len(pts))],
            }
        )
    coasts = [
        {"points": [[x / TILE, z / TILE] for x, _y, z in map(_position, _points(item))]}
        for item in line_items(isl.header, "Coastlines")
    ]
    return {"rivers": rivers, "lakes": [], "coasts": coasts}


def ribbon(item: Node) -> tuple[np.ndarray, np.ndarray]:
    """A river's points (x, z) and widths in world units, as ``formats.isc.blockings`` takes them."""
    pts = _points(item)
    return (
        np.array([[p[0], p[2]] for p in map(_position, pts)], np.float64),
        np.array([p.float("width") for p in pts], np.float64),
    )


def _rewrite(item: Node, xyz: list[tuple[float, float, float]], widths: list[float] | None) -> None:
    """Give the line the points ``xyz`` (world units): each a copy of the old point nearest to it with its place,
    width and length along the line set."""
    old = _points(item)
    before = [_position(p) for p in old]
    poly = item.find("m_Polygon")
    keep = [c for c in poly.children if c not in old]
    run, rows = 0.0, []
    has_length = any(p_.float("distance") for p_ in old)  # the newer files carry the length; the older ones 0
    for k, p in enumerate(xyz):
        near = min(range(len(old)), key=lambda i: (before[i][0] - p[0]) ** 2 + (before[i][2] - p[2]) ** 2)
        node = copy.deepcopy(old[near])
        _set_position(node, p)
        if widths is not None:
            _set_text(node, "width", widths[k])
        if k:
            run += math.hypot(p[0] - xyz[k - 1][0], p[2] - xyz[k - 1][2])
        if has_length:
            _set_text(node, "distance", run)
        rows.append(node)
    poly.children = keep + rows


def apply(isl: Island1701, edits: dict) -> tuple[dict, list[str]]:
    """Put the edited rivers (``"k"`` -> ``points`` x, height, z in tiles, ``widths``) and coast lines (``"k"`` ->
    ``points`` x, z) on the header; returns what was written and the edits that match no line."""
    report, stale = {"rivers": 0, "coasts": 0}, []
    rivers, coasts = line_items(isl.header, "Rivers"), line_items(isl.header, "Coastlines")
    for k, change in sorted(edits.get("rivers", {}).items(), key=lambda kv: str(kv[0])):
        if not str(k).isdigit() or int(k) >= len(rivers) or not groundedit.river_edit_ok(change):
            stale.append(tr("river {k}", k=k))
            continue
        xyz = [tuple(float(v) * TILE for v in p) for p in change["points"]]
        before, others = ribbon(rivers[int(k)]), [ribbon(r) for i, r in enumerate(rivers) if i != int(k)]
        _rewrite(rivers[int(k)], xyz, [float(w) * TILE for w in change["widths"]])
        redrawn = isc_blockings.redraw(isl.header, max(isl.width, isl.height), before, ribbon(rivers[int(k)]), others)
        for name, counts in redrawn.items():
            for what, n in counts.items():
                report[f"{name} {what}"] = report.get(f"{name} {what}", 0) + n
        report["rivers"] += 1
    for k, change in sorted(edits.get("coast", {}).items(), key=lambda kv: str(kv[0])):
        if not str(k).isdigit() or int(k) >= len(coasts) or len(change["points"]) < 2:
            stale.append(tr("coast line {k}", k=k))
            continue
        old = [_position(p) for p in _points(coasts[int(k)])]
        level = sum(p[1] for p in old) / len(old)
        _rewrite(coasts[int(k)], [(float(x) * TILE, level, float(z) * TILE) for x, z in change["points"]], None)
        report["coasts"] += 1
    return report, stale


def clear(isl: Island1701) -> int:
    """A blank island has no river: the header's rivers and their blocking lists are emptied. Returns the rivers."""
    if isl.header is None:
        return 0
    rivers = isl.header.find("Rivers")
    count = len(list_items(rivers)) if rivers is not None else 0
    for name in ("Rivers", *BLOCKINGS):
        node = isl.header.find(name)
        if node is not None:
            node.children = [c for c in node.children if not (c.name == "i" or c.name.startswith("i0"))]
            _reset_count(node)
    return count


def _reset_count(node: Node) -> None:
    size = node.find("arraysize")
    if size is not None:
        size.text = "0"
