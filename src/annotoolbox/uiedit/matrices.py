"""The 2D affine matrices the preview places widgets with: a widget's own matrix from its parent's, a point or a
rectangle through one, and the scale it applies.

``Matrix`` is (xx, yx, xy, yy, offset_x, offset_y); ``mul``, ``apply``, ``invert`` and ``bounds`` are the entries (``layout`` places
with them, the canvas and the dump comparison read them).
"""

from __future__ import annotations

import math

Matrix = tuple[float, float, float, float, float, float]  # x' = xx x + xy y + offset_x, y' = yx x + yy y + offset_y
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mul(matrix: Matrix, other: Matrix) -> Matrix:
    """``matrix`` after ``other`` (a point goes through ``other`` first)."""
    xx, yx, xy, yy, offset_x, offset_y = matrix
    other_xx, other_yx, other_xy, other_yy, other_offset_x, other_offset_y = other
    return (
        xx * other_xx + xy * other_yx,
        yx * other_xx + yy * other_yx,
        xx * other_xy + xy * other_yy,
        yx * other_xy + yy * other_yy,
        xx * other_offset_x + xy * other_offset_y + offset_x,
        yx * other_offset_x + yy * other_offset_y + offset_y,
    )


def apply(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    return matrix[0] * x + matrix[2] * y + matrix[4], matrix[1] * x + matrix[3] * y + matrix[5]


def translate(x: float, y: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, x, y)


def scale(scale_x: float, scale_y: float) -> Matrix:
    return (scale_x, 0.0, 0.0, scale_y, 0.0, 0.0)


def rotate(degrees: float) -> Matrix:
    radians = math.radians(degrees)
    return (math.cos(radians), math.sin(radians), -math.sin(radians), math.cos(radians), 0.0, 0.0)


def invert(matrix: Matrix) -> Matrix:
    xx, yx, xy, yy, offset_x, offset_y = matrix
    determinant = xx * yy - yx * xy or 1e-12
    inverse_xx, inverse_yx, inverse_xy, inverse_yy = (
        yy / determinant,
        -yx / determinant,
        -xy / determinant,
        xx / determinant,
    )
    return (
        inverse_xx,
        inverse_yx,
        inverse_xy,
        inverse_yy,
        -(inverse_xx * offset_x + inverse_xy * offset_y),
        -(inverse_yx * offset_x + inverse_yy * offset_y),
    )


def bounds(matrix: Matrix, width_px: float, height_px: float) -> tuple[float, float, float, float]:
    """(min x, min y, max x, max y) of the local rectangle 0..width_px x 0..height_px after ``matrix``."""
    corners = [apply(matrix, x, y) for x, y in ((0, 0), (width_px, 0), (0, height_px), (width_px, height_px))]
    return (
        min(corner[0] for corner in corners),
        min(corner[1] for corner in corners),
        max(corner[0] for corner in corners),
        max(corner[1] for corner in corners),
    )


def union_size(rects: list[tuple[float, float, float, float]]) -> tuple[float, float]:
    """The size of the union of the rectangles (a container's size, as the game measures it: docs/ui/117/layout.md
    "ConsiderSize and the size of a container")."""
    if not rects:
        return (0.0, 0.0)
    return (
        max(rect[2] for rect in rects) - min(rect[0] for rect in rects),
        max(rect[3] for rect in rects) - min(rect[1] for rect in rects),
    )


def own_scale(matrix: Matrix) -> tuple[float, float]:
    """The scale a matrix applies along x and y (a rotation aside)."""
    return math.hypot(matrix[0], matrix[1]), math.hypot(matrix[2], matrix[3])
