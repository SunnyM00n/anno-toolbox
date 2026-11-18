"""The 2D affine matrices the preview places widgets with: a widget's own matrix from its parent's, a point or a
rectangle through one, and the scale it applies.

``Matrix`` is (a, b, c, d, tx, ty); ``mul``, ``apply``, ``invert`` and ``bounds`` are the entries (``layout`` places
with them, the canvas and the dump comparison read them).
"""

from __future__ import annotations

import math

Matrix = tuple[float, float, float, float, float, float]  # x' = a x + c y + tx, y' = b x + d y + ty
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(m: Matrix, n: Matrix) -> Matrix:
    """``m`` after ``n`` (a point goes through ``n`` first)."""
    a, b, c, d, tx, ty = m
    e, f, g, h, ux, uy = n
    return (a * e + c * f, b * e + d * f, a * g + c * h, b * g + d * h, a * ux + c * uy + tx, b * ux + d * uy + ty)


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def translate(x: float, y: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, x, y)


def scale(sx: float, sy: float) -> Matrix:
    return (sx, 0.0, 0.0, sy, 0.0, 0.0)


def rotate(degrees: float) -> Matrix:
    r = math.radians(degrees)
    return (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0.0, 0.0)


def invert(m: Matrix) -> Matrix:
    a, b, c, d, tx, ty = m
    det = a * d - b * c or 1e-12
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * tx + ic * ty), -(ib * tx + id_ * ty))


def bounds(m: Matrix, w: float, h: float) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of the local rectangle 0..w x 0..h after ``m``."""
    pts = [apply(m, x, y) for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def union_size(rects: list[tuple[float, float, float, float]]) -> tuple[float, float]:
    """The size of the union of the rectangles (a container's size, as the game measures it: docs/ui/117/layout.md
    "ConsiderSize and the size of a container")."""
    if not rects:
        return (0.0, 0.0)
    return (max(r[2] for r in rects) - min(r[0] for r in rects), max(r[3] for r in rects) - min(r[1] for r in rects))


def own_scale(m: Matrix) -> tuple[float, float]:
    """The scale a matrix applies along x and y (a rotation aside)."""
    return math.hypot(m[0], m[1]), math.hypot(m[2], m[3])
