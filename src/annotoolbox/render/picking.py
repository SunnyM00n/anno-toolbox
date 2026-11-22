"""The maths of picking with a mouse, shared by the 3D views: the ray through a pixel, the nearest triangle it meets
(which part of a model is under the mouse), a ray against a plane or an axis (dragging a handle), a point on the
screen and the nearest point of a segment.

``ray(view_proj, size, x, y)`` gives the ray, ``nearest(origin, direction, vertices, triangles)`` the distance to the
first triangle it meets; ``CModelRenderer.pick`` puts them together over a scene. ``ray_plane`` and ``along_axis`` take
the ray as (origin, direction); ``to_screen`` and ``on_segment`` work in pixels.
"""

from __future__ import annotations

import math

import numpy as np


def ray(view_proj: np.ndarray, size_px: tuple[int, int], x: float, y: float) -> tuple[np.ndarray, np.ndarray]:
    """(origin, unit direction) in view space of the ray through pixel (x, y) (top left 0, 0) of a ``size`` view."""
    width_px, height_px = size_px
    ndc = np.array([2 * x / max(width_px, 1) - 1, 1 - 2 * y / max(height_px, 1)])
    inverse = np.linalg.inv(np.asarray(view_proj, float))
    near = inverse @ np.array([*ndc, -1.0, 1.0])
    far = inverse @ np.array([*ndc, 1.0, 1.0])
    near, far = near[:3] / near[3], far[:3] / far[3]
    direction = far - near
    return near, direction / max(float(np.linalg.norm(direction)), 1e-12)


def nearest(origin: np.ndarray, direction: np.ndarray, vertices: np.ndarray, triangles: np.ndarray) -> float | None:
    """The distance along the ray to the first of ``triangles`` ([m, 3] indices into ``vertices`` [n, 3]) it meets,
    either face; None when it meets none (Moeller-Trumbore, all triangles at once)."""
    if not len(triangles):
        return None
    corner0, corner1, corner2 = (vertices[triangles[:, k]] for k in range(3))
    edge1, edge2 = corner1 - corner0, corner2 - corner0
    edge_cross = np.cross(direction, edge2)
    determinant = np.einsum("ij,ij->i", edge1, edge_cross)
    not_parallel = np.abs(determinant) > 1e-12
    inverse_determinant = np.where(not_parallel, 1.0 / np.where(not_parallel, determinant, 1.0), 0.0)
    offset = origin - corner0
    bary_u = np.einsum("ij,ij->i", offset, edge_cross) * inverse_determinant
    offset_cross = np.cross(offset, edge1)
    bary_v = (offset_cross @ direction) * inverse_determinant
    ray_distance = np.einsum("ij,ij->i", edge2, offset_cross) * inverse_determinant
    hit = not_parallel & (bary_u >= 0) & (bary_v >= 0) & (bary_u + bary_v <= 1) & (ray_distance > 0)
    return float(ray_distance[hit].min()) if hit.any() else None


def ray_plane(ray: tuple, point, normal, tolerance: float = 1e-6) -> np.ndarray | None:
    """Where the ray (origin, direction) meets the plane through ``point`` with ``normal``; None when it is parallel
    (within ``eps``) or the plane lies behind the origin."""
    origin, direction = ray
    along = float(np.dot(direction, normal))
    if abs(along) < tolerance:
        return None
    ray_distance = float(np.dot(np.asarray(point, float) - origin, normal)) / along
    return np.asarray(origin, float) + ray_distance * np.asarray(direction, float) if ray_distance >= 0 else None


def along_axis(ray: tuple, point, axis, tolerance: float = 1e-6) -> float | None:
    """The distance along ``axis`` (a unit vector through ``point``) of the point nearest the ray; None when the
    axis is parallel to the ray (within ``eps``)."""
    origin, direction = ray
    axis = np.asarray(axis, float)
    cross = float(np.dot(direction, axis))
    denom = 1.0 - cross * cross
    if denom < tolerance:
        return None
    relative = np.asarray(point, float) - origin
    return float((np.dot(direction, relative) * cross - np.dot(axis, relative)) / denom)


def to_screen(view_proj: np.ndarray, size_px: tuple[int, int], points) -> list[tuple[float, float, float] | None]:
    """Each point (x, y, z) as (pixel x, pixel y from the top, depth in -1..1) in a ``size`` view; None behind the
    camera."""
    width_px, height_px = size_px
    homogeneous = np.c_[np.asarray(points, float).reshape(-1, 3), np.ones(len(points))]
    out: list[tuple[float, float, float] | None] = []
    for clip in homogeneous @ np.asarray(view_proj, float).T:
        out.append(
            None
            if clip[3] <= 1e-6
            else ((clip[0] / clip[3] + 1) / 2 * width_px, (1 - clip[1] / clip[3]) / 2 * height_px, clip[2] / clip[3])
        )
    return out


def on_segment(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> tuple[float, tuple, float]:
    """The point of the segment start-end nearest ``point``: (its place along the segment 0..1, the point, the distance)."""
    delta_x, delta_y = end[0] - start[0], end[1] - start[1]
    squared_length = delta_x * delta_x + delta_y * delta_y
    fraction = (
        0.0
        if squared_length == 0
        else max(0.0, min(1.0, ((point[0] - start[0]) * delta_x + (point[1] - start[1]) * delta_y) / squared_length))
    )
    nearest = (start[0] + fraction * delta_x, start[1] + fraction * delta_y)
    return fraction, nearest, math.hypot(point[0] - nearest[0], point[1] - nearest[1])
