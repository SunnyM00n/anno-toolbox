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
# 2070's street cells (no model: laid like decals) draw between the passes before and after the streets
STREET_CELLS = 3
STREET_KIND = "street"
LIFT = 0.004  # above the ground it is laid on, against z-fighting
MAX_CELLS = 64  # a side's cells at most (a large decal follows the ground more coarsely)


def pass_of(kind: str, on_streets: bool) -> int:
    """The pass of a decal of ``Type`` ``kind``: a vehicle track's own whatever ``OnStreets`` says, else before or
    after the streets (``Terrain-Baked`` and an unknown name read as ``Terrain``, as the game reads them)."""
    if kind.strip().lower() == "vehicle":
        return VEHICLE
    if kind == STREET_KIND:
        return STREET_CELLS
    return AFTER_STREETS if on_streets else BEFORE_STREETS


def read_decal(node) -> tuple[str, bool]:
    """A DECAL node's ``Type`` and ``OnStreets`` (``models.configtree.SNode``; the game's defaults: Terrain, 0)."""
    return (node.get("Type") or "Terrain").strip(), node.number("OnStreets", 0.0) != 0


def conform(extents: tuple[float, float], place: np.ndarray, height_at, step_tiles: float,
            lift_m: float = LIFT) -> tuple[np.ndarray, np.ndarray]:  # fmt: skip
    """The decal of half sizes ``extents`` (x, z) at ``place`` (4x4, column vectors) laid on ``height_at(x, z)``
    (arrays in the place's space): a grid of about ``step`` per cell, each vertex at the ground's height plus
    ``lift``, the normal up, u along x and v along z as on the flat rectangle. (vertices [n, 8], indices [m])"""
    extent_x, extent_z = (max(float(extent), 1e-6) for extent in extents)
    sides = [
        int(min(MAX_CELLS, max(1, np.ceil(2 * extent * _scale(place, k) / max(step_tiles, 1e-6)))))
        for k, extent in ((0, extent_x), (2, extent_z))
    ]
    cells_x, cells_z = sides
    coord_u, coord_v = np.meshgrid(np.linspace(0, 1, cells_x + 1), np.linspace(0, 1, cells_z + 1))
    local = np.stack(
        [(coord_u * 2 - 1) * extent_x, np.zeros_like(coord_u), (coord_v * 2 - 1) * extent_z, np.ones_like(coord_u)], -1
    ).reshape(-1, 4)
    world = local @ np.asarray(place, float).T
    world[:, 1] = height_at(world[:, 0], world[:, 2]) + lift_m
    up_normals = np.broadcast_to([0.0, 1.0, 0.0], (len(world), 3))
    verts = np.concatenate([world[:, :3], up_normals, coord_u.reshape(-1, 1), coord_v.reshape(-1, 1)], 1).astype("f4")
    i = np.arange(cells_x)[None, :] + np.arange(cells_z)[:, None] * (cells_x + 1)
    quads = np.stack([i, i + 1, i + cells_x + 2, i, i + cells_x + 2, i + cells_x + 1], -1).reshape(-1)
    return verts, quads.astype("u4")


def _scale(place, axis: int) -> float:
    return float(np.linalg.norm(np.asarray(place, float)[:3, axis]))


def heights_sampler(heights: np.ndarray, step_tiles: float, size_tiles: float):
    """``height_at(x, z)`` in the island view's space (x mirrored: Anno's x is ``size_tiles - x``) of a height grid (rows
    z, ``step_tiles`` apart), bilinear, clamped at the edges."""
    grid_width = heights.shape[0]

    def height_at(x, z):
        scaled_x = np.clip((size_tiles - np.asarray(x, float)) / step_tiles, 0, grid_width - 1)
        scaled_z = np.clip(np.asarray(z, float) / step_tiles, 0, grid_width - 1)
        grid_x, grid_z = (
            np.minimum(scaled_x.astype(int), grid_width - 2),
            np.minimum(scaled_z.astype(int), grid_width - 2),
        )
        fraction_x, fraction_z = scaled_x - grid_x, scaled_z - grid_z
        grid = heights
        top = grid[grid_z, grid_x] * (1 - fraction_x) + grid[grid_z, grid_x + 1] * fraction_x
        bottom = grid[grid_z + 1, grid_x] * (1 - fraction_x) + grid[grid_z + 1, grid_x + 1] * fraction_x
        return top * (1 - fraction_z) + bottom * fraction_z

    return height_at
