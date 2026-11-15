"""What the builder draws over the 3D view from an object's info and feedback files: boxes (bounding, mesh, hit,
intersect boxes and dummies), the areas on the ground (the build blocker, the footprint, and the other blockers) and
the feedback's dummies, as lines in the model's space. No Qt.

``info_shapes(doc)`` and ``feedback_shapes(doc)`` are the entry; each ``Shape`` keeps the path of its part in the
document, so the selected part shows. 2070 and 1404 write an ``.ifo`` in fixed point (4,096 per unit), the newer
games in floats (``xf``); 1701's ``.info`` is grid cells (``info1701``). docs/tool-suite.md section 23.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from ..models import cfgdoc, info1701, infofile
from ..models.cfgdoc import Doc, Node
from ..models.meshxform import qrotate

BOXES = ("BoundingBox", "MeshBoundingBox", "HitBox", "IntersectBox", "Dummy", "SelectionBox")
EDGES = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
CROSS = 0.08  # half the size of a dummy's cross without extents


@dataclass
class Shape:
    """Lines in the model's space: ``points`` and the ``segments`` between them; ``kind`` (``box``, ``area``,
    ``dummy``), the part's ``name`` and its ``path`` in the document."""

    kind: str
    name: str
    path: str
    points: list[tuple[float, float, float]] = field(default_factory=list)
    segments: list[tuple[int, int]] = field(default_factory=list)


def _vector(block: Node | None, names: str, fixed: bool, default: float = 0.0) -> tuple[float, ...]:
    """A value block's components (``xf`` or ``x``), in the order of ``names``."""
    if block is None:
        return tuple(default for _ in names)
    return tuple(infofile.value(infofile.coordinate(block, n), fixed, default) for n in names)


def _child(node: Node, name: str) -> Node | None:
    return next((c for c in node.children if c.name == name), None)


def box(name: str, path: str, node: Node, fixed: bool) -> Shape:
    """A box of an ``.ifo``: its centre, rotation and half sizes."""
    centre = np.array(_vector(_child(node, "Position"), "xyz", fixed))
    q = np.array(_vector(_child(node, "Rotation"), "xyzw", fixed))
    q = q / np.linalg.norm(q) if np.linalg.norm(q) > 1e-9 else np.array([0.0, 0.0, 0.0, 1.0])
    half = np.array(_vector(_child(node, "Extents"), "xyz", fixed))
    return _cuboid(name, path, centre, q, half, "dummy" if node.name == "Dummy" else "box")


def _cuboid(name: str, path: str, centre, q, half, kind: str = "box") -> Shape:
    """The twelve edges of a box: ``centre``, rotation ``q`` (x y z w) and half sizes (none: a dummy's cross size)."""
    centre, half = np.asarray(centre, float), np.asarray(half, float)
    if not half.any():
        half = np.full(3, CROSS)
    corners = [centre + qrotate(q, half * np.array(s)) for s in
               ((x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1))]  # fmt: skip
    return Shape(kind, name, path, [tuple(map(float, c)) for c in corners], list(EDGES))


def area(name: str, path: str, node: Node, fixed: bool) -> Shape | None:
    """A polygon on the ground (a blocker): its ``Position`` points in order, closed."""
    points = [(x, 0.0, z) for x, z in infofile.points(node, fixed)]
    if len(points) < 2:
        return None
    return Shape("area", name, path, points, [(k, (k + 1) % len(points)) for k in range(len(points))])


def info_shapes(doc: Doc, origin: tuple[float, float] = (0.0, 0.0)) -> list[Shape]:
    """Every box and ground area of an ``.ifo``; of a 1701 ``.info`` its cells and boxes (``origin``: the object's
    place past the grid point, in tiles)."""
    text = doc.to_bytes().decode("utf-8", "replace")
    if info1701.is_info1701(text):
        return _shapes1701(doc, text, origin)
    out = []
    for part in doc.parts().walk():
        node = part.node
        fixed = not any(c.name.endswith("f") for p in node.children for c in p.children if p.name == "Position")
        name = _text(node, "Name") or node.name
        if node.name in BOXES:
            out.append(box(name, part.path, node, fixed))
        elif any(c.name == "Position" for c in node.children) and node.name not in BOXES:
            found = area(node.name, part.path, node, fixed)
            if found is not None:
                out.append(found)
    return out


def _shapes1701(doc: Doc, text: str, origin: tuple[float, float]) -> list[Shape]:
    """A 1701 ``.info`` over the model (its space is tiles): each block's cells merged into rectangles on the
    ground, the bounding box and the dummies' boxes."""
    info = info1701.read_info(text)
    paths = {p.node.name: p.path for p in doc.parts().walk()}
    out = []
    if any(info.box[0]) or any(info.box[1]):
        lo, hi = np.array(info.box[0]), np.array(info.box[1])
        out.append(_cuboid("BoundingBox", paths.get("BoundingBox", ""), (lo + hi) / 2, (0, 0, 0, 1), (hi - lo) / 2))
    for name, cells in info.blocks.items():
        for polygon in info1701.rectangles(cells, origin):
            points = [(x, 0.0, z) for x, z in polygon]
            out.append(Shape("area", name, paths.get(name, ""), points, [(k, (k + 1) % 4) for k in range(4)]))
    for name, (centre, extents) in info.dummies.items():
        out.append(_cuboid(name, paths.get(name, ""), centre, (0, 0, 0, 1), extents, "dummy"))
    return out


def feedback_shapes(doc: Doc) -> list[Shape]:
    """The named dummies of an ``.fc`` (``DummyRoot`` and its groups) as small crosses at their place."""
    out = []
    for part in doc.parts().walk():
        node = part.node
        pos = _child(node, "Position")
        if node.name != "i" or pos is None or _child(node, "Name") is None:
            continue
        at = _position(pos)
        if at is None:
            continue
        s = CROSS
        points = [(at[0] + dx, at[1] + dy, at[2] + dz) for dx, dy, dz in
                  ((-s, 0, 0), (s, 0, 0), (0, -s, 0), (0, s, 0), (0, 0, -s), (0, 0, s))]  # fmt: skip
        out.append(Shape("dummy", _text(node, "Name"), part.path, points, [(0, 1), (2, 3), (4, 5)]))
    return out


def _position(pos: Node) -> tuple[float, float, float] | None:
    """A feedback dummy's place: three floats (117, 1800, 2205) or 2070's three fixed-point int64 (x, z, height)."""
    if pos.data is not None and len(pos.data) >= 24:
        x, z, y = (v / infofile.FIXED for v in struct.unpack_from("<3q", pos.data))
        return x, y, z
    if pos.children:
        return _vector(pos, "xyz", False)
    return None


def _text(node: Node, name: str) -> str:
    found = _child(node, name)
    return cfgdoc.shown(found.text) if found is not None and found.text else ""
