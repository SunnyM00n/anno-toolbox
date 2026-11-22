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

from ....formats.isd2070 import SNode
from ....infra.i18n import tr
from ....othergames.isd1701 import TILE, CIsland1701, list_items
from ... import groundedit
from . import blockings as isc_blockings

LISTS = ("Rivers", "Coastlines")
BLOCKINGS = ("RiverBlockings", "RiverPathBlockings")


def line_items(header: SNode | None, name: str) -> list[SNode]:
    """The lines of a list that have a polygon of two points or more, in file order (what an edit's index counts)."""
    node = header.find(name) if header is not None else None
    items = []
    for item in list_items(node) if node is not None else []:
        polygon = item.find("m_Polygon")
        if polygon is not None and sum(1 for point in list_items(polygon) if point.find("position") is not None) >= 2:
            items.append(item)
    return items


def _points(item: SNode) -> list[SNode]:
    return [point for point in list_items(item.find("m_Polygon")) if point.find("position") is not None]


def _position(point: SNode) -> tuple[float, float, float]:
    position_node = point.find("position")
    data = position_node.find("data")
    if data is not None and data.data is not None and len(data.data) == 12:
        return struct.unpack("<3f", data.data)
    return tuple(float(position_node.value(axis, 0)) for axis in "xyz")


def _set_position(point: SNode, xyz: tuple[float, float, float]) -> None:
    position_node = point.find("position")
    data = position_node.find("data")
    if data is not None and data.data is not None and len(data.data) == 12:
        data.data = struct.pack("<3f", *xyz)
        return
    for name, value in zip("xyz", xyz, strict=True):
        node = position_node.find(name)
        if node is not None:
            node.text = f"{value:.6f}"


def _set_text(point: SNode, name: str, value: float) -> None:
    node = point.find(name)
    if node is not None:
        node.text = f"{value:.6f}"


def network(island: CIsland1701) -> dict:
    """The water as the edit session takes it (``CEditSession.water_net``): rivers (points x, height, z in tiles and
    the width at each) and coast lines (x, z in tiles); 1701 has no lakes."""
    rivers = []
    for item in line_items(island.m_header, "Rivers"):
        positions = [_position(point) for point in _points(item)]
        rivers.append(
            {
                "points": [[x / TILE, y / TILE, z / TILE] for x, y, z in positions],
                "widths": [_points(item)[k].float("width") / TILE for k in range(len(positions))],
            }
        )
    coasts = [
        {"points": [[x / TILE, z / TILE] for x, _y, z in map(_position, _points(item))]}
        for item in line_items(island.m_header, "Coastlines")
    ]
    return {"rivers": rivers, "lakes": [], "coasts": coasts}


def ribbon(item: SNode) -> tuple[np.ndarray, np.ndarray]:
    """A river's points (x, z) and widths in world units, as ``formats.isc.blockings`` takes them."""
    points = _points(item)
    return (
        np.array([[position[0], position[2]] for position in map(_position, points)], np.float64),
        np.array([position.float("width") for position in points], np.float64),
    )


def _rewrite(item: SNode, xyz: list[tuple[float, float, float]], widths: list[float] | None) -> None:
    """Give the line the points ``xyz`` (world units): each a copy of the old point nearest to it with its place,
    width and length along the line set."""
    old = _points(item)
    before = [_position(position) for position in old]
    length_along, new_points = 0.0, []
    has_length = any(
        old_point.float("distance") for old_point in old
    )  # the newer files carry the length; the older ones 0
    for k, position in enumerate(xyz):
        nearest = min(
            range(len(old)), key=lambda i: (before[i][0] - position[0]) ** 2 + (before[i][2] - position[2]) ** 2
        )
        node = copy.deepcopy(old[nearest])
        _set_position(node, position)
        if widths is not None:
            _set_text(node, "width", widths[k])
        if k:
            length_along += math.hypot(position[0] - xyz[k - 1][0], position[2] - xyz[k - 1][2])
        if has_length:
            _set_text(node, "distance", length_along)
        new_points.append(node)
    isc_blockings.set_list(item.find("m_Polygon"), new_points)  # a numbered list counts and names its points


def apply(island: CIsland1701, edits: dict) -> tuple[dict, list[str]]:
    """Put the edited rivers (``"k"`` -> ``points`` x, height, z in tiles, ``widths``) and coast lines (``"k"`` ->
    ``points`` x, z) on the header; returns what was written and the edits that match no line."""
    report, stale = {"rivers": 0, "coasts": 0}, []
    rivers, coasts = line_items(island.m_header, "Rivers"), line_items(island.m_header, "Coastlines")
    for line_key, change in sorted(edits.get("rivers", {}).items(), key=lambda entry: str(entry[0])):
        if not str(line_key).isdigit() or int(line_key) >= len(rivers) or not groundedit.river_edit_ok(change):
            stale.append(tr("islands.river_k", k=line_key))
            continue
        xyz = [tuple(float(coordinate) * TILE for coordinate in point) for point in change["points"]]
        before, others = (
            ribbon(rivers[int(line_key)]),
            [ribbon(river) for i, river in enumerate(rivers) if i != int(line_key)],
        )
        _rewrite(rivers[int(line_key)], xyz, [float(width) * TILE for width in change["widths"]])
        redrawn = isc_blockings.redraw(
            island.m_header, max(island.m_width, island.m_height), before, ribbon(rivers[int(line_key)]), others
        )
        for name, counts in redrawn.items():
            for what, count in counts.items():
                report[f"{name} {what}"] = report.get(f"{name} {what}", 0) + count
        report["rivers"] += 1
    for line_key, change in sorted(edits.get("coast", {}).items(), key=lambda entry: str(entry[0])):
        if not str(line_key).isdigit() or int(line_key) >= len(coasts) or len(change.get("points") or ()) < 2:
            stale.append(tr("islands.coast_line_k", k=line_key))
            continue
        old = [_position(point) for point in _points(coasts[int(line_key)])]
        level = sum(point[1] for point in old) / len(old)
        _rewrite(coasts[int(line_key)], [(float(x) * TILE, level, float(z) * TILE) for x, z in change["points"]], None)
        report["coasts"] += 1
    return report, stale


def clear(island: CIsland1701) -> int:
    """A blank island has no river: the header's rivers and their blocking lists are emptied. Returns the rivers."""
    if island.m_header is None:
        return 0
    rivers = island.m_header.find("Rivers")
    count = len(list_items(rivers)) if rivers is not None else 0
    for name in ("Rivers", *BLOCKINGS):
        node = island.m_header.find(name)
        if node is not None:
            isc_blockings.set_list(node, [])
    return count
