"""Editing the info file's polygons (the build blocker and the other blockers), no Qt: the points of an area, a
point moved, put in, taken out, a whole footprint drawn, each an undoable ``models.cfgdoc.SEdit`` of the ``.ifo``.

``area_at(document, path)`` is the entry (the area a part is, None for any other part); ``SArea`` holds its points and the
number style of the file (floats ``xf zf``, or the older games' fixed point ``x z``: 4,096 to the unit).
docs/tool-suite.md section 23, "Handles in the view".
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

from ..formats import isd2070
from ..models import cfgdoc, infofile
from ..models.cfgdoc import SDoc, SEdit, SNode
from ..render import picking
from .shapes import BOXES

GRID = 0.5  # the unit the shipped footprints' points lie on (docs/tool-suite.md, "The grid, measured")
MIN_POINTS = 3


def snap(value: float, step: float = GRID) -> float:
    return round(value / step) * step


@dataclass
class SArea:
    """A polygon on the ground: its part's ``node``, the ``Position`` nodes in order and the file's number style."""

    path: str
    node: SNode
    fixed: bool

    @property
    def positions(self) -> list[SNode]:
        return [child for child in self.node.children if child.name == "Position"]

    def points(self) -> list[tuple[float, float]]:
        return infofile.points(self.node, self.fixed)


def area_at(document: SDoc, path: str, is_older_game: bool = False) -> SArea | None:
    """The area the part at ``path`` is: a node that is no box and holds at least two ``Position`` nodes."""
    part = document.find(path)
    if part is None or part.node.name in BOXES or sum(child.name == "Position" for child in part.node.children) < 2:
        return None
    return SArea(path, part.node, infofile.fixed_style(document, is_older_game))


def move_point(area: SArea, index: int, x: float, z: float) -> list[SEdit]:
    """The edits that put point ``index`` at (x, z)."""
    position = area.positions[index]
    edits = []
    for axis, value in (("x", x), ("z", z)):
        leaf = infofile.coordinate(position, axis)
        if leaf is not None and (leaf.text or "") != infofile.text(value, area.fixed):
            edits.append(cfgdoc.set_value(leaf, infofile.text(value, area.fixed), f"move point {index + 1}"))
    return edits


def _new_position(area: SArea, like: SNode, x: float, z: float) -> SNode:
    node = copy.deepcopy(like)
    for axis, value in (("x", x), ("z", z)):
        leaf = infofile.coordinate(node, axis)
        if leaf is not None:
            leaf.text = infofile.text(value, area.fixed)
    return node


def insert_point(area: SArea, index: int, x: float, z: float) -> SEdit:
    """A point at (x, z) put in as number ``index`` (a copy of its neighbour keeps the file's style)."""
    like = area.positions[min(index, len(area.positions)) - 1]
    insert_index = area.node.children.index(area.positions[index]) if index < len(area.positions) else (
        area.node.children.index(area.positions[-1]) + 1)  # fmt: skip
    return cfgdoc.insert(area.node, insert_index, [_new_position(area, like, x, z)], "add a point")


def remove_point(area: SArea, index: int) -> SEdit | None:
    """The edit that takes point ``index`` out (None: a polygon keeps three)."""
    if len(area.positions) <= MIN_POINTS:
        return None
    return cfgdoc.remove(area.node, area.positions[index], "remove a point")


def nearest_side(points: list[tuple[float, float]], x: float, z: float) -> tuple[int, float, float, float]:
    """(index the new point would take, its x, its z, distance) on the side of the closed polygon nearest (x, z)."""
    best = (0, x, z, math.inf)
    for k, (start, end) in enumerate(zip(points, [*points[1:], *points[:1]], strict=True)):
        _along, (nearest_x, nearest_z), distance = picking.on_segment((x, z), start, end)
        if distance < best[3]:
            best = (k + 1, nearest_x, nearest_z, distance)
    return best


def replace_points(area: SArea, points: list[tuple[float, float]]) -> list[SEdit]:
    """The edits that give an area the polygon ``points`` (its old points out, new ones in at the first one's place)."""
    like, node = area.positions[0], area.node
    old, made = list(node.children), [_new_position(area, like, x, z) for x, z in points]
    new = [child for child in old if child.name != "Position" or child is like]
    new[new.index(like) : new.index(like) + 1] = made

    def put(children: list[SNode]):
        return lambda: node.children.__setitem__(slice(None), children)

    return [cfgdoc.SEdit("redraw the footprint", put(new), put(old))]


def _top(document: SDoc) -> SNode | None:
    """The element the areas sit in (``Info``, even when it holds nothing yet), None for a file without one."""
    return (
        document.root.children[0]
        if len(document.root.children) == 1
        else document.parts().node
        if document.root.children
        else None
    )


def has_footprint(document: SDoc) -> bool:
    top = _top(document)
    return top is not None and any(child.name == infofile.FOOTPRINT for child in top.children)


def new_footprint(document: SDoc, points: list[tuple[float, float]], is_older_game: bool = False) -> SEdit | None:
    """The edit that adds a ``BuildBlocker`` of ``points`` to the file (None: no top element to put it in)."""
    top = _top(document)
    if top is None:
        return None
    fixed = infofile.fixed_style(document, is_older_game)
    x_field, z_field = ("x", "z") if fixed else ("xf", "zf")
    newline = document.newline.decode()
    body = "".join(
        f"<Position><{x_field}>{infofile.text(x, fixed)}</{x_field}><{z_field}>{infofile.text(z, fixed)}</{z_field}></Position>"
        for x, z in points
    )
    node = isd2070.parse(f"<{infofile.FOOTPRINT}>{body}</{infofile.FOOTPRINT}>{newline}".encode()).children[0]
    return cfgdoc.insert(top, len(top.children), [node], "draw the footprint")
