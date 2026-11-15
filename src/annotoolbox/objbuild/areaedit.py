"""Editing the info file's polygons (the build blocker and the other blockers), no Qt: the points of an area, a
point moved, put in, taken out, a whole footprint drawn, each an undoable ``models.cfgdoc.Edit`` of the ``.ifo``.

``area_at(doc, path)`` is the entry (the area a part is, None for any other part); ``Area`` holds its points and the
number style of the file (floats ``xf zf``, or the older games' fixed point ``x z``: 4,096 to the unit).
docs/tool-suite.md section 23, "Handles in the view".
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

from ..formats import isd2070
from ..models import cfgdoc, infofile
from ..models.cfgdoc import Doc, Edit, Node
from ..render import picking
from .shapes import BOXES

GRID = 0.5  # the unit the shipped footprints' points lie on (docs/tool-suite.md, "The grid, measured")
MIN_POINTS = 3


def snap(value: float, step: float = GRID) -> float:
    return round(value / step) * step


@dataclass
class Area:
    """A polygon on the ground: its part's ``node``, the ``Position`` nodes in order and the file's number style."""

    path: str
    node: Node
    fixed: bool

    @property
    def positions(self) -> list[Node]:
        return [c for c in self.node.children if c.name == "Position"]

    def points(self) -> list[tuple[float, float]]:
        return infofile.points(self.node, self.fixed)


def area_at(doc: Doc, path: str, older_game: bool = False) -> Area | None:
    """The area the part at ``path`` is: a node that is no box and holds at least two ``Position`` nodes."""
    part = doc.find(path)
    if part is None or part.node.name in BOXES or sum(c.name == "Position" for c in part.node.children) < 2:
        return None
    return Area(path, part.node, infofile.fixed_style(doc, older_game))


def move_point(area: Area, index: int, x: float, z: float) -> list[Edit]:
    """The edits that put point ``index`` at (x, z)."""
    position = area.positions[index]
    edits = []
    for axis, value in (("x", x), ("z", z)):
        leaf = infofile.coordinate(position, axis)
        if leaf is not None and (leaf.text or "") != infofile.text(value, area.fixed):
            edits.append(cfgdoc.set_value(leaf, infofile.text(value, area.fixed), f"move point {index + 1}"))
    return edits


def _new_position(area: Area, like: Node, x: float, z: float) -> Node:
    node = copy.deepcopy(like)
    for axis, value in (("x", x), ("z", z)):
        leaf = infofile.coordinate(node, axis)
        if leaf is not None:
            leaf.text = infofile.text(value, area.fixed)
    return node


def insert_point(area: Area, index: int, x: float, z: float) -> Edit:
    """A point at (x, z) put in as number ``index`` (a copy of its neighbour keeps the file's style)."""
    like = area.positions[min(index, len(area.positions)) - 1]
    at = area.node.children.index(area.positions[index]) if index < len(area.positions) else (
        area.node.children.index(area.positions[-1]) + 1)  # fmt: skip
    return cfgdoc.insert(area.node, at, [_new_position(area, like, x, z)], "add a point")


def remove_point(area: Area, index: int) -> Edit | None:
    """The edit that takes point ``index`` out (None: a polygon keeps three)."""
    if len(area.positions) <= MIN_POINTS:
        return None
    return cfgdoc.remove(area.node, area.positions[index], "remove a point")


def nearest_side(points: list[tuple[float, float]], x: float, z: float) -> tuple[int, float, float, float]:
    """(index the new point would take, its x, its z, distance) on the side of the closed polygon nearest (x, z)."""
    best = (0, x, z, math.inf)
    for k, (a, b) in enumerate(zip(points, [*points[1:], *points[:1]], strict=True)):
        _t, (px, pz), distance = picking.on_segment((x, z), a, b)
        if distance < best[3]:
            best = (k + 1, px, pz, distance)
    return best


def replace_points(area: Area, points: list[tuple[float, float]]) -> list[Edit]:
    """The edits that give an area the polygon ``points`` (its old points out, new ones in at the first one's place)."""
    like, node = area.positions[0], area.node
    old, made = list(node.children), [_new_position(area, like, x, z) for x, z in points]
    new = [c for c in old if c.name != "Position" or c is like]
    new[new.index(like) : new.index(like) + 1] = made

    def put(children: list[Node]):
        return lambda: node.children.__setitem__(slice(None), children)

    return [cfgdoc.Edit("redraw the footprint", put(new), put(old))]


def _top(doc: Doc) -> Node | None:
    """The element the areas sit in (``Info``, even when it holds nothing yet), None for a file without one."""
    return doc.root.children[0] if len(doc.root.children) == 1 else doc.parts().node if doc.root.children else None


def has_footprint(doc: Doc) -> bool:
    top = _top(doc)
    return top is not None and any(c.name == infofile.FOOTPRINT for c in top.children)


def new_footprint(doc: Doc, points: list[tuple[float, float]], older_game: bool = False) -> Edit | None:
    """The edit that adds a ``BuildBlocker`` of ``points`` to the file (None: no top element to put it in)."""
    top = _top(doc)
    if top is None:
        return None
    fixed = infofile.fixed_style(doc, older_game)
    a, b = ("x", "z") if fixed else ("xf", "zf")
    nl = doc.newline.decode()
    body = "".join(
        f"<Position><{a}>{infofile.text(x, fixed)}</{a}><{b}>{infofile.text(z, fixed)}</{b}></Position>"
        for x, z in points
    )
    node = isd2070.parse(f"<{infofile.FOOTPRINT}>{body}</{infofile.FOOTPRINT}>{nl}".encode()).children[0]
    return cfgdoc.insert(top, len(top.children), [node], "draw the footprint")
