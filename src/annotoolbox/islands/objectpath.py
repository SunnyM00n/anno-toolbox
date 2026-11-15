"""A placed object's own path (a work area slot's ``BezierPath``) follows the object when it is moved or turned.

``follow`` is called by ``islandedits.set_object`` after the object's pose was written. The path's points, tangents and
bounds are in world coordinates (the first point is the object's position), so a move without this leaves the path
where the object was; the turn sense is the one the shipped objects show (island-editor "Placed objects").
"""

from __future__ import annotations

import math
import struct

from ..formats import filedb

TOLERANCE = 1e-3  # world units: bounds this close to their points' are taken to be those points' bounds
Vec = tuple[float, float, float]


def _vector(tag: filedb.Tag, name: str) -> Vec | None:
    attr = tag.find(name)
    return struct.unpack("<3f", attr.data) if attr is not None and len(attr.data) == 12 else None


def _box(points: list[Vec]) -> tuple[Vec, Vec]:
    return (
        tuple(min(p[i] for p in points) for i in range(3)),
        tuple(max(p[i] for p in points) for i in range(3)),
    )


class _Pose:
    """A move from ``old`` to ``new`` and a turn: what happens to a point, a tangent and a box."""

    def __init__(self, old: Vec, new: Vec, heading_turn: float) -> None:
        self.old, self.new = old, new
        self.cos, self.sin = math.cos(-heading_turn), math.sin(-heading_turn)  # Direction is the turn's negative

    def turn(self, v: Vec) -> Vec:
        return (v[0] * self.cos + v[2] * self.sin, v[1], -v[0] * self.sin + v[2] * self.cos)

    def point(self, p: Vec) -> Vec:
        x, y, z = self.turn((p[0] - self.old[0], p[1], p[2] - self.old[2]))
        return (self.new[0] + x, y + self.new[1] - self.old[1], self.new[2] + z)

    def box(self, low: Vec, high: Vec) -> tuple[Vec, Vec]:
        """The box that holds the given one after the move and the turn."""
        return _box(
            [self.point((x, y, z)) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
        )


def follow(obj: filedb.Tag, old: Vec, new: Vec, heading_turn: float) -> None:
    """Move the object's path from ``old`` to ``new`` position and turn it by ``heading_turn`` (the change of the
    object's ``Direction``, radians) about the object's place. Nothing happens for an object without a path."""
    path = obj.path("BezierPath/Path")
    curve = path.find("BezierCurve") if path is not None else None
    points = [c for c in curve.children if isinstance(c, filedb.Tag) and c.find("p") is not None] if curve else []
    if not points:
        return
    pose = _Pose(old, new, heading_turn)
    was = [_vector(p, "p") for p in points]
    low, high = _vector(path, "Minimum"), _vector(path, "Maximum")
    tight = (
        low is not None
        and high is not None
        and all(abs(a - b) <= TOLERANCE for a, b in zip((*_box(was)[0], *_box(was)[1]), (*low, *high), strict=True))
    )
    for point, p in zip(points, was, strict=True):
        point.find("p").data = struct.pack("<3f", *pose.point(p))
        for name in ("i", "o"):  # the tangents turn with the path and keep their length
            if (tangent := _vector(point, name)) is not None:
                point.find(name).data = struct.pack("<3f", *pose.turn(tangent))
    if low is not None and high is not None:
        # a box with slack of its own keeps it: the box that holds the old one, moved and turned
        low, high = _box([pose.point(p) for p in was]) if tight else pose.box(low, high)
        path.find("Minimum").data = struct.pack("<3f", *low)
        path.find("Maximum").data = struct.pack("<3f", *high)
