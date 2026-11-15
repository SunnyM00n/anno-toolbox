"""A config's DECAL as the views lay it: on the terrain under it (a grid over its rectangle at the terrain's height,
as the game's high-poly decal follows the ground) and in the pass its data picks (117's rule, rendering/material-
passes.md "Decals": ``OnStreets`` 0 before the streets, 1 after them, ``Type Vehicle`` after the opaque scene).

``pass_of(kind, on_streets)`` is a decal's pass (``BEFORE_STREETS``, ``AFTER_STREETS``, ``VEHICLE``); ``conform`` the
grid of one decal laid on a height function; ``read_decal`` a DECAL node's own fields. Used by the viewer
(``viewer.modelrender``) and the island editor's view (``editor.desktop.desktopdecals``).
"""

from __future__ import annotations

import numpy as np

BEFORE_STREETS, AFTER_STREETS, VEHICLE = 0, 1, 2  # the decal passes in the order the frame draws them
LIFT = 0.004  # above the ground it is laid on, against z-fighting
MAX_CELLS = 64  # a side's cells at most (a large decal follows the ground more coarsely)


def pass_of(kind: str, on_streets: bool) -> int:
    """The pass of a decal of ``Type`` ``kind``: a vehicle track's own whatever ``OnStreets`` says, else before or
    after the streets (``Terrain-Baked`` and an unknown name read as ``Terrain``, as the game reads them)."""
    if kind.strip().lower() == "vehicle":
        return VEHICLE
    return AFTER_STREETS if on_streets else BEFORE_STREETS


def read_decal(node) -> tuple[str, bool]:
    """A DECAL node's ``Type`` and ``OnStreets`` (``models.configtree.Node``; the game's defaults: Terrain, 0)."""
    return (node.get("Type") or "Terrain").strip(), node.number("OnStreets", 0.0) != 0


def conform(extents: tuple[float, float], place: np.ndarray, height_at, step: float,
            lift: float = LIFT) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """The decal of half sizes ``extents`` (x, z) at ``place`` (4x4, column vectors) laid on ``height_at(x, z)``
    (arrays in the place's space): a grid of about ``step`` per cell, each vertex at the ground's height plus
    ``lift``, the normal up, u along x and v along z as on the flat rectangle. (vertices [n, 8], indices [m])"""
    ex, ez = (max(float(e), 1e-6) for e in extents)
    sides = [
        int(min(MAX_CELLS, max(1, np.ceil(2 * e * _scale(place, k) / max(step, 1e-6))))) for k, e in ((0, ex), (2, ez))
    ]
    nx, nz = sides
    u, v = np.meshgrid(np.linspace(0, 1, nx + 1), np.linspace(0, 1, nz + 1))
    local = np.stack([(u * 2 - 1) * ex, np.zeros_like(u), (v * 2 - 1) * ez, np.ones_like(u)], -1).reshape(-1, 4)
    world = local @ np.asarray(place, float).T
    world[:, 1] = height_at(world[:, 0], world[:, 2]) + lift
    up = np.broadcast_to([0.0, 1.0, 0.0], (len(world), 3))
    verts = np.concatenate([world[:, :3], up, u.reshape(-1, 1), v.reshape(-1, 1)], 1).astype("f4")
    i = np.arange(nx)[None, :] + np.arange(nz)[:, None] * (nx + 1)
    quads = np.stack([i, i + 1, i + nx + 2, i, i + nx + 2, i + nx + 1], -1).reshape(-1)
    return verts, quads.astype("u4")


def _scale(place, axis: int) -> float:
    return float(np.linalg.norm(np.asarray(place, float)[:3, axis]))


def heights_sampler(heights: np.ndarray, step: float, size: float):
    """``height_at(x, z)`` in the island view's space (x mirrored: Anno's x is ``size - x``) of a height grid (rows
    z, ``step`` apart), bilinear, clamped at the edges."""
    w = heights.shape[0]

    def height_at(x, z):
        gx = np.clip((size - np.asarray(x, float)) / step, 0, w - 1)
        gz = np.clip(np.asarray(z, float) / step, 0, w - 1)
        x0, z0 = np.minimum(gx.astype(int), w - 2), np.minimum(gz.astype(int), w - 2)
        fx, fz = gx - x0, gz - z0
        h = heights
        top = h[z0, x0] * (1 - fx) + h[z0, x0 + 1] * fx
        bottom = h[z0 + 1, x0] * (1 - fx) + h[z0 + 1, x0 + 1] * fx
        return top * (1 - fz) + bottom * fz

    return height_at
