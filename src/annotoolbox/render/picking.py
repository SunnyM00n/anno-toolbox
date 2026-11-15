"""The maths of picking with a mouse, shared by the 3D views: the ray through a pixel, the nearest triangle it meets
(which part of a model is under the mouse), a ray against a plane or an axis (dragging a handle), a point on the
screen and the nearest point of a segment.

``ray(view_proj, size, x, y)`` gives the ray, ``nearest(origin, direction, vertices, triangles)`` the distance to the
first triangle it meets; ``ModelRenderer.pick`` puts them together over a scene. ``ray_plane`` and ``along_axis`` take
the ray as (origin, direction); ``to_screen`` and ``on_segment`` work in pixels.
"""

from __future__ import annotations

import math

import numpy as np


def ray(view_proj: np.ndarray, size: tuple[int, int], x: float, y: float) -> tuple[np.ndarray, np.ndarray]:
    """(origin, unit direction) in view space of the ray through pixel (x, y) (top left 0, 0) of a ``size`` view."""
    w, h = size
    ndc = np.array([2 * x / max(w, 1) - 1, 1 - 2 * y / max(h, 1)])
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
    a, b, c = (vertices[triangles[:, k]] for k in range(3))
    e1, e2 = b - a, c - a
    p = np.cross(direction, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = origin - a
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ direction) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
    return float(t[hit].min()) if hit.any() else None


def ray_plane(ray: tuple, point, normal, eps: float = 1e-6) -> np.ndarray | None:
    """Where the ray (origin, direction) meets the plane through ``point`` with ``normal``; None when it is parallel
    (within ``eps``) or the plane lies behind the origin."""
    origin, direction = ray
    along = float(np.dot(direction, normal))
    if abs(along) < eps:
        return None
    t = float(np.dot(np.asarray(point, float) - origin, normal)) / along
    return np.asarray(origin, float) + t * np.asarray(direction, float) if t >= 0 else None


def along_axis(ray: tuple, point, axis, eps: float = 1e-6) -> float | None:
    """The distance along ``axis`` (a unit vector through ``point``) of the point nearest the ray; None when the
    axis is parallel to the ray (within ``eps``)."""
    origin, direction = ray
    axis = np.asarray(axis, float)
    cross = float(np.dot(direction, axis))
    denom = 1.0 - cross * cross
    if denom < eps:
        return None
    rel = np.asarray(point, float) - origin
    return float((np.dot(direction, rel) * cross - np.dot(axis, rel)) / denom)


def to_screen(view_proj: np.ndarray, size: tuple[int, int], points) -> list[tuple[float, float, float] | None]:
    """Each point (x, y, z) as (pixel x, pixel y from the top, depth in -1..1) in a ``size`` view; None behind the
    camera."""
    w, h = size
    hom = np.c_[np.asarray(points, float).reshape(-1, 3), np.ones(len(points))]
    out: list[tuple[float, float, float] | None] = []
    for c in hom @ np.asarray(view_proj, float).T:
        out.append(None if c[3] <= 1e-6 else ((c[0] / c[3] + 1) / 2 * w, (1 - c[1] / c[3]) / 2 * h, c[2] / c[3]))
    return out


def on_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> tuple[float, tuple, float]:
    """The point of the segment a-b nearest ``p``: (its place along the segment 0..1, the point, the distance)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    q = (a[0] + t * dx, a[1] + t * dy)
    return t, q, math.hypot(p[0] - q[0], p[1] - q[1])
