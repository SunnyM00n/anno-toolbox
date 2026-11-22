"""What the builder draws over the 3D view from an object's info and feedback files: boxes (bounding, mesh, hit,
intersect boxes and dummies), the areas on the ground (the build blocker, the footprint, and the other blockers) and
the feedback's dummies, as lines in the model's space. No Qt.

``info_shapes(document)`` and ``feedback_shapes(document)`` are the entry; each ``SShape`` keeps the path of its part in the
document, so the selected part shows. 2070 and 1404 write an ``.ifo`` in fixed point (4,096 per unit), the newer
games in floats (``xf``); 1701's ``.info`` is grid cells (``info1701``). docs/tool-suite.md section 23.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from ..models import cfgdoc, info1701, infofile
from ..models.cfgdoc import SDoc, SNode
from ..models.meshxform import qrotate

BOXES = ("BoundingBox", "MeshBoundingBox", "HitBox", "IntersectBox", "Dummy", "SelectionBox")
EDGES = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
CROSS = 0.08  # half the size of a dummy's cross without extents


@dataclass
class SShape:
    """Lines in the model's space: ``points`` and the ``segments`` between them; ``kind`` (``box``, ``area``,
    ``dummy``), the part's ``name`` and its ``path`` in the document."""

    kind: str
    name: str
    path: str
    points: list[tuple[float, float, float]] = field(default_factory=list)
    segments: list[tuple[int, int]] = field(default_factory=list)


def _vector(block: SNode | None, names: str, is_fixed: bool, default: float = 0.0) -> tuple[float, ...]:
    """A value block's components (``xf`` or ``x``), in the order of ``names``."""
    if block is None:
        return tuple(default for _ in names)
    return tuple(infofile.value(infofile.coordinate(block, name), is_fixed, default) for name in names)


def _child(node: SNode, name: str) -> SNode | None:
    return next((child for child in node.children if child.name == name), None)


def box(name: str, path: str, node: SNode, is_fixed: bool) -> SShape:
    """A box of an ``.ifo``: its centre, rotation and half sizes."""
    centre = np.array(_vector(_child(node, "Position"), "xyz", is_fixed))
    quaternion = np.array(_vector(_child(node, "Rotation"), "xyzw", is_fixed))
    quaternion = (
        quaternion / np.linalg.norm(quaternion) if np.linalg.norm(quaternion) > 1e-9 else np.array([0.0, 0.0, 0.0, 1.0])
    )
    half = np.array(_vector(_child(node, "Extents"), "xyz", is_fixed))
    return _cuboid(name, path, centre, quaternion, half, "dummy" if node.name == "Dummy" else "box")


def _cuboid(name: str, path: str, centre, quaternion, half, kind: str = "box") -> SShape:
    """The twelve edges of a box: ``centre``, rotation ``quaternion`` (x y z w) and half sizes (none: a dummy's cross size)."""
    centre, half = np.asarray(centre, float), np.asarray(half, float)
    if not half.any():
        half = np.full(3, CROSS)
    corners = [centre + qrotate(quaternion, half * np.array(sign)) for sign in
               ((x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1))]  # fmt: skip
    return SShape(kind, name, path, [tuple(map(float, corner)) for corner in corners], list(EDGES))


def area(name: str, path: str, node: SNode, is_fixed: bool) -> SShape | None:
    """A polygon on the ground (a blocker): its ``Position`` points in order, closed."""
    points = [(x, 0.0, z) for x, z in infofile.points(node, is_fixed)]
    if len(points) < 2:
        return None
    return SShape("area", name, path, points, [(k, (k + 1) % len(points)) for k in range(len(points))])


def info_shapes(document: SDoc, origin: tuple[float, float] = (0.0, 0.0)) -> list[SShape]:
    """Every box and ground area of an ``.ifo``; of a 1701 ``.info`` its cells and boxes (``origin``: the object's
    place past the grid point, in tiles)."""
    text = document.to_bytes().decode("utf-8", "replace")
    if info1701.is_info1701(text):
        return _shapes1701(document, text, origin)
    out = []
    for part in document.parts().walk():
        node = part.node
        is_fixed = not any(
            child.name.endswith("f")
            for position_holder in node.children
            for child in position_holder.children
            if position_holder.name == "Position"
        )
        name = _text(node, "Name") or node.name
        if node.name in BOXES:
            out.append(box(name, part.path, node, is_fixed))
        elif any(child.name == "Position" for child in node.children) and node.name not in BOXES:
            found = area(node.name, part.path, node, is_fixed)
            if found is not None:
                out.append(found)
    return out


def _shapes1701(document: SDoc, text: str, origin: tuple[float, float]) -> list[SShape]:
    """A 1701 ``.info`` over the model (its space is tiles): each block's cells merged into rectangles on the
    ground, the bounding box and the dummies' boxes."""
    box_info = info1701.read_info(text)
    paths = {part.node.name: part.path for part in document.parts().walk()}
    out = []
    if any(box_info.box[0]) or any(box_info.box[1]):
        low, high = np.array(box_info.box[0]), np.array(box_info.box[1])
        out.append(
            _cuboid("BoundingBox", paths.get("BoundingBox", ""), (low + high) / 2, (0, 0, 0, 1), (high - low) / 2)
        )
    for name, cells in box_info.blocks.items():
        for polygon in info1701.rectangles(cells, origin):
            points = [(x, 0.0, z) for x, z in polygon]
            out.append(SShape("area", name, paths.get(name, ""), points, [(k, (k + 1) % 4) for k in range(4)]))
    for name, (centre, extents) in box_info.dummies.items():
        out.append(_cuboid(name, paths.get(name, ""), centre, (0, 0, 0, 1), extents, "dummy"))
    return out


def feedback_shapes(document: SDoc) -> list[SShape]:
    """The named dummies of an ``.fc`` (``DummyRoot`` and its groups) as small crosses at their place."""
    out = []
    for part in document.parts().walk():
        node = part.node
        position_node = _child(node, "Position")
        if node.name != "i" or position_node is None or _child(node, "Name") is None:
            continue
        anchor = _position(position_node)
        if anchor is None:
            continue
        arm = CROSS
        points = [(anchor[0] + offset_x, anchor[1] + offset_y, anchor[2] + offset_z) for offset_x, offset_y, offset_z in
                  ((-arm, 0, 0), (arm, 0, 0), (0, -arm, 0), (0, arm, 0), (0, 0, -arm), (0, 0, arm))]  # fmt: skip
        out.append(SShape("dummy", _text(node, "Name"), part.path, points, [(0, 1), (2, 3), (4, 5)]))
    return out


def _position(position_node: SNode) -> tuple[float, float, float] | None:
    """A feedback dummy's place: three floats (117, 1800, 2205) or 2070's three fixed-point int64 (x, z, height)."""
    if position_node.data is not None and len(position_node.data) >= 24:
        x, z, y = (raw_value / infofile.FIXED for raw_value in struct.unpack_from("<3q", position_node.data))
        return x, y, z
    if position_node.children:
        return _vector(position_node, "xyz", False)
    return None


def _text(node: SNode, name: str) -> str:
    found = _child(node, name)
    return cfgdoc.shown(found.text) if found is not None and found.text else ""
