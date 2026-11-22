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


def _settle(positions: np.ndarray, previous_positions: np.ndarray, hit: np.ndarray, moved: np.ndarray) -> None:
    positions[hit] = moved
    previous_positions[hit] = moved


def _push_out(positions: np.ndarray, previous_positions: np.ndarray, near: np.ndarray, reach: float) -> None:
    """The game's push from a surface point ``near`` per vertex: closer than ``reach`` goes out to it (+0.001 of
    the distance); a vertex on its point (d = 0) is left as the game leaves it, not finite."""
    offset = positions - near
    dist = np.linalg.norm(offset, axis=1)
    hit = dist < reach
    if hit.any():
        _settle(positions, previous_positions, hit, near[hit] + offset[hit] * (reach / dist[hit] + INSIDE)[:, None])


@dataclass(frozen=True)
class SCapsule:
    """A ``Cylinder``: the segment ``start`` to ``end`` (its full ``Length`` scaled) with rounded ends, ``radius``."""

    start: np.ndarray
    end: np.ndarray
    radius_m: float

    def push(self, positions: np.ndarray, previous_positions: np.ndarray, thickness_m: float) -> None:
        axis = self.end - self.start
        span = float(axis @ axis)
        along = np.clip(((positions - self.start) @ axis) / span, 0.0, 1.0) if span > 0 else np.zeros(len(positions))
        _push_out(positions, previous_positions, self.start + along[:, None] * axis, self.radius_m + thickness_m)


@dataclass(frozen=True)
class SSphere:
    """A unit sphere scaled by the part: ``radius`` its scale (the file's ``Radius`` is not read)."""

    centre: np.ndarray
    radius_m: float

    def push(self, positions: np.ndarray, previous_positions: np.ndarray, thickness: float) -> None:
        _push_out(
            positions, previous_positions, np.broadcast_to(self.centre, positions.shape), self.radius_m + thickness
        )


@dataclass(frozen=True)
class SPlane:
    """The free side is where ``normal . (p - point)`` is positive; a vertex on the other goes to 0.001 behind the
    plane. The cloth's thickness is not used (``thin``)."""

    point: np.ndarray
    normal: np.ndarray
    thin = True

    def push(self, positions: np.ndarray, previous_positions: np.ndarray, thickness: float = 0.0) -> None:
        signed_distance = (positions - self.point) @ self.normal - thickness
        hit = signed_distance < 0
        if hit.any():
            _settle(
                positions,
                previous_positions,
                hit,
                positions[hit] - (signed_distance[hit] + INSIDE)[:, None] * self.normal,
            )


@dataclass(frozen=True)
class SBox:
    """A box of half sizes ``half`` placed by ``matrix`` (its space -> the cloth's, scale included): only a shell
    as thick as the cloth pushes, from the vertex's nearest point of the box; ``inv`` is the matrix's inverse."""

    matrix: np.ndarray
    half: np.ndarray
    inv: np.ndarray

    def push(self, positions: np.ndarray, previous_positions: np.ndarray, thickness: float) -> None:
        inv = self.inv
        local = positions @ inv[:3, :3].T + inv[:3, 3]
        nearest = np.clip(local, -self.half, self.half) @ self.matrix[:3, :3].T + self.matrix[:3, 3]
        _push_out(positions, previous_positions, nearest, thickness)


def kind_name(kind) -> str:
    """A ``CollisionType`` as its name: the files write the name, the game reads it as an index."""
    text = str(kind or "").strip()
    # isdecimal: a digit int() cannot read (a superscript) is a name like any other, not an index
    return KINDS[int(text)] if text.isdecimal() and int(text) < len(KINDS) else text


def collider(kind, matrix: np.ndarray, values: Mapping[str, str]) -> SCapsule | SSphere | SPlane | SBox | None:
    """A collision part as a shape in the cloth's space (``matrix``: the part's space -> the cloth's, its uniform
    scale ``s`` the mean of the axes' lengths); None for a kind without a shape."""
    kind = kind_name(kind)
    affine = np.asarray(matrix, float)
    centre = affine[:3, 3]
    scale = float(np.mean(np.linalg.norm(affine[:3, :3], axis=0)))
    if kind == "Cylinder":
        half = 0.5 * field_number(values, "Length", 1.0) * affine[:3, 1]
        return SCapsule(centre - half, centre + half, field_number(values, "Radius", 0.5) * scale)
    if kind == "Sphere":
        return SSphere(centre, scale)
    if kind == "Plane":
        normal = affine[:3, 1] / max(float(np.linalg.norm(affine[:3, 1])), 1e-12)
        return SPlane(centre, normal)
    if kind == "Box":
        inv = inverse(affine)
        half = np.array([field_number(values, f"Extents.{axis_name}", 0.5) for axis_name in "xyz"])
        return SBox(affine, half, inv) if inv is not None else None  # a box scaled to nothing holds nothing
    return None
