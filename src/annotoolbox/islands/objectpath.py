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


def _vector(tag: filedb.STag, name: str) -> Vec | None:
    attr = tag.find(name)
    return struct.unpack("<3f", attr.data) if attr is not None and len(attr.data) == 12 else None


def _box(points: list[Vec]) -> tuple[Vec, Vec]:
    return (
        tuple(min(point[i] for point in points) for i in range(3)),
        tuple(max(point[i] for point in points) for i in range(3)),
    )


class _CPose:
    """A move from ``old`` to ``new`` and a turn: what happens to a point, a tangent and a box."""

    def __init__(self, old: Vec, new: Vec, heading_turn: float) -> None:
        self.m_old, self.m_new = old, new
        self.m_cos, self.m_sin = math.cos(-heading_turn), math.sin(-heading_turn)  # Direction is the turn's negative

    def turn(self, vector: Vec) -> Vec:
        return (
            vector[0] * self.m_cos + vector[2] * self.m_sin,
            vector[1],
            -vector[0] * self.m_sin + vector[2] * self.m_cos,
        )

    def point(self, position: Vec) -> Vec:
        x, y, z = self.turn((position[0] - self.m_old[0], position[1], position[2] - self.m_old[2]))
        return (self.m_new[0] + x, y + self.m_new[1] - self.m_old[1], self.m_new[2] + z)

    def box(self, low: Vec, high: Vec) -> tuple[Vec, Vec]:
        """The box that holds the given one after the move and the turn."""
        return _box(
            [self.point((x, y, z)) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
        )


def follow(object_tag: filedb.STag, old: Vec, new: Vec, heading_turn: float) -> None:
    """Move the object's path from ``old`` to ``new`` position and turn it by ``heading_turn`` (the change of the
    object's ``Direction``, radians) about the object's place. Nothing happens for an object without a path."""
    path = object_tag.path("BezierPath/Path")
    curve = path.find("BezierCurve") if path is not None else None
    points = (
        [child for child in curve.children if isinstance(child, filedb.STag) and child.find("p") is not None]
        if curve
        else []
    )
    if not points:
        return
    pose = _CPose(old, new, heading_turn)
    was = [_vector(tag, "p") for tag in points]
    low, high = _vector(path, "Minimum"), _vector(path, "Maximum")
    tight = (
        low is not None
        and high is not None
        and all(
            abs(was_value - stored_value) <= TOLERANCE
            for was_value, stored_value in zip((*_box(was)[0], *_box(was)[1]), (*low, *high), strict=True)
        )
    )
    for point, position in zip(points, was, strict=True):
        point.find("p").data = struct.pack("<3f", *pose.point(position))
        for name in ("i", "o"):  # the tangents turn with the path and keep their length
            if (tangent := _vector(point, name)) is not None:
                point.find(name).data = struct.pack("<3f", *pose.turn(tangent))
    if low is not None and high is not None:
        # a box with slack of its own keeps it: the box that holds the old one, moved and turned
        low, high = _box([pose.point(position) for position in was]) if tight else pose.box(low, high)
        path.find("Minimum").data = struct.pack("<3f", *low)
        path.find("Maximum").data = struct.pack("<3f", *high)
