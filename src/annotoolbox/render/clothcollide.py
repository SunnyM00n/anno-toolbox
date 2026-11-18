"""The shapes a cloth collides with (a config's ``COLLISION`` parts) in the cloth's own space, as the game's collide
them: a vertex too near is put on the surface plus the cloth's thickness (the plane: just behind it), and its previous
position set to it (no bounce, no sliding).

``collider(kind, matrix, values)`` makes one from a part's ``CollisionType`` (its name or index), its placement in the
cloth's space and its fields; each has ``push(p, prev, thickness)``. docs/engine-cloth.md "Collision shapes".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .clothsim import field_number, inverse

INSIDE = 0.001  # the game puts a vertex this much past the surface
KINDS = ("Plane", "Cylinder", "Sphere", "Box")  # CollisionType is an index into these


def _settle(p: np.ndarray, prev: np.ndarray, hit: np.ndarray, moved: np.ndarray) -> None:
    p[hit] = moved
    prev[hit] = moved


def _push_out(p: np.ndarray, prev: np.ndarray, near: np.ndarray, reach: float) -> None:
    """The game's push from a surface point ``near`` per vertex: closer than ``reach`` goes out to it (+0.001 of
    the distance); a vertex on its point (d = 0) is left as the game leaves it, not finite."""
    d = p - near
    dist = np.linalg.norm(d, axis=1)
    hit = dist < reach
    if hit.any():
        _settle(p, prev, hit, near[hit] + d[hit] * (reach / dist[hit] + INSIDE)[:, None])


@dataclass(frozen=True)
class Capsule:
    """A ``Cylinder``: the segment ``a`` to ``b`` (its full ``Length`` scaled) with rounded ends, ``radius``."""

    a: np.ndarray
    b: np.ndarray
    radius: float

    def push(self, p: np.ndarray, prev: np.ndarray, thickness: float) -> None:
        axis = self.b - self.a
        span = float(axis @ axis)
        t = np.clip(((p - self.a) @ axis) / span, 0.0, 1.0) if span > 0 else np.zeros(len(p))
        _push_out(p, prev, self.a + t[:, None] * axis, self.radius + thickness)


@dataclass(frozen=True)
class Sphere:
    """A unit sphere scaled by the part: ``radius`` its scale (the file's ``Radius`` is not read)."""

    centre: np.ndarray
    radius: float

    def push(self, p: np.ndarray, prev: np.ndarray, thickness: float) -> None:
        _push_out(p, prev, np.broadcast_to(self.centre, p.shape), self.radius + thickness)


@dataclass(frozen=True)
class Plane:
    """The free side is where ``normal . (p - point)`` is positive; a vertex on the other goes to 0.001 behind the
    plane. The cloth's thickness is not used (``thin``)."""

    point: np.ndarray
    normal: np.ndarray
    thin = True

    def push(self, p: np.ndarray, prev: np.ndarray, thickness: float = 0.0) -> None:
        s = (p - self.point) @ self.normal - thickness
        hit = s < 0
        if hit.any():
            _settle(p, prev, hit, p[hit] - (s[hit] + INSIDE)[:, None] * self.normal)


@dataclass(frozen=True)
class Box:
    """A box of half sizes ``half`` placed by ``matrix`` (its space -> the cloth's, scale included): only a shell
    as thick as the cloth pushes, from the vertex's nearest point of the box; ``inv`` is the matrix's inverse."""

    matrix: np.ndarray
    half: np.ndarray
    inv: np.ndarray

    def push(self, p: np.ndarray, prev: np.ndarray, thickness: float) -> None:
        inv = self.inv
        local = p @ inv[:3, :3].T + inv[:3, 3]
        q = np.clip(local, -self.half, self.half) @ self.matrix[:3, :3].T + self.matrix[:3, 3]
        _push_out(p, prev, q, thickness)


def kind_name(kind) -> str:
    """A ``CollisionType`` as its name: the files write the name, the game reads it as an index."""
    text = str(kind or "").strip()
    # isdecimal: a digit int() cannot read (a superscript) is a name like any other, not an index
    return KINDS[int(text)] if text.isdecimal() and int(text) < len(KINDS) else text


def collider(kind, matrix: np.ndarray, values: Mapping[str, str]) -> Capsule | Sphere | Plane | Box | None:
    """A collision part as a shape in the cloth's space (``matrix``: the part's space -> the cloth's, its uniform
    scale ``s`` the mean of the axes' lengths); None for a kind without a shape."""
    kind = kind_name(kind)
    m = np.asarray(matrix, float)
    centre = m[:3, 3]
    scale = float(np.mean(np.linalg.norm(m[:3, :3], axis=0)))
    if kind == "Cylinder":
        half = 0.5 * field_number(values, "Length", 1.0) * m[:3, 1]
        return Capsule(centre - half, centre + half, field_number(values, "Radius", 0.5) * scale)
    if kind == "Sphere":
        return Sphere(centre, scale)
    if kind == "Plane":
        n = m[:3, 1] / max(float(np.linalg.norm(m[:3, 1])), 1e-12)
        return Plane(centre, n)
    if kind == "Box":
        inv = inverse(m)
        half = np.array([field_number(values, f"Extents.{a}", 0.5) for a in "xyz"])
        return Box(m, half, inv) if inv is not None else None  # a box scaled to nothing holds nothing
    return None
