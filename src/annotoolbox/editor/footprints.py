"""A building's footprint: the ``BuildBlocker`` polygons of its model's info file (``<model>.ifo``, in tiles around
the object's origin), turned and placed on the island, tested for overlap and against the tile grid.

The game turns a building in eighths (45 degrees) and tests its footprint per quarter tile; a footprint's corners
stand on whole tile corners when the building is on its grid (a quarter turn keeps them there), and a building on an
odd eighth has its origin on the diagonal lattice (``snapping.diagonal``). ``on_grid`` and ``snap_origin`` apply
those rules; ``overlap`` is the depth two footprints reach into each other. Rules and sources: island-editor
"Placed objects", 117-features "Where a diagonal building may stand".
"""

from __future__ import annotations

import math

import numpy as np

from ..models import infofile
from .snapping import diagonal, to_step

EIGHTH = math.pi / 4
TOLERANCE = 0.02  # tiles: a corner this near a whole number is on the grid
TOUCH = 0.05  # tiles: footprints reaching this far into each other overlap (edge to edge is not)

Polygons = list[list[tuple[float, float]]]


def info_path(cfg: str) -> str:
    """The info file beside a model config (1701's ``.mcf``: an ``.info``)."""
    stem, extension = cfg.rsplit(".", 1)
    return stem + (".info" if extension.lower() == "mcf" else ".ifo")


def read_footprint(ifo_text: str, ocf_text: str = "") -> Polygons:
    """The ``BuildBlocker`` polygons of an info file (each at least three points), as (x, z) lists in tiles
    (``models.infofile.footprint``: the older games' fixed point is turned into tiles; 1701's ``.info`` gives its
    footprint tiles round the origin its object config ``ocf_text`` offsets)."""
    return infofile.footprint(ifo_text, ocf_text)


def turn_of(rotation) -> float:
    """The turn about the vertical a placed quaternion (x, y, z, w) makes, in radians."""
    return 2.0 * math.atan2(rotation[1], rotation[3])


def world(polygons: Polygons, position, rotation) -> list[np.ndarray]:
    """The polygons turned by ``rotation`` and moved to ``position`` (x, y, z): arrays of (x, z) rows."""
    angle = turn_of(rotation)
    cosine, sine = math.cos(angle), math.sin(angle)
    out = []
    for polygon in polygons:
        corners = np.asarray(polygon, float)
        out.append(
            np.stack(
                [corners[:, 0] * cosine + corners[:, 1] * sine, -corners[:, 0] * sine + corners[:, 1] * cosine], axis=1
            )
            + (position[0], position[2])
        )
    return out


def eighth_of(rotation) -> float | None:
    """The eighth (0 to 7) the turn is, None when it is not one."""
    k = turn_of(rotation) / EIGHTH
    return round(k) % 8 if abs(k - round(k)) * EIGHTH <= TOLERANCE else None


def on_grid(polygons: Polygons, position, rotation) -> bool:
    """Whether the footprint stands on the tile grid: for a quarter turn every corner on a whole tile corner, for an
    odd eighth the origin on the diagonal lattice."""
    eighth = eighth_of(rotation)
    if eighth is None:
        return False
    if eighth % 2:
        x, z = position[0], position[2]
        return math.dist((x, z), diagonal(x, z)) <= TOLERANCE
    corners = np.concatenate(world(polygons, position, rotation))
    return bool(np.all(np.abs(corners - np.round(corners)) <= TOLERANCE))


def snap_origin(polygons: Polygons, position, rotation) -> tuple[float, float]:
    """The origin nearest ``position`` that puts the footprint on the grid (``on_grid``)."""
    x, z = float(position[0]), float(position[2])
    k = eighth_of(rotation)
    if k is None or k % 2:
        return diagonal(x, z)
    corner = world(polygons, (0.0, 0.0, 0.0), rotation)[0][0]
    return to_step(x, 1.0, -float(corner[0])), to_step(z, 1.0, -float(corner[1]))


def hull(points: np.ndarray) -> np.ndarray:
    """The convex hull of (x, z) points, counter-clockwise."""
    sorted_points = sorted(map(tuple, points))
    if len(sorted_points) < 3:
        return np.asarray(sorted_points)

    def half(seq):
        out = []
        for point in seq:
            while (
                len(out) >= 2
                and (out[-1][0] - out[-2][0]) * (point[1] - out[-2][1])
                - (out[-1][1] - out[-2][1]) * (point[0] - out[-2][0])
                <= 0
            ):
                out.pop()
            out.append(point)
        return out

    lower, upper = half(sorted_points), half(reversed(sorted_points))
    return np.asarray(lower[:-1] + upper[:-1])


def inside(points: np.ndarray, hull: np.ndarray) -> np.ndarray:
    """Which points lie inside a convex polygon (counter-clockwise, as ``hull`` makes it)."""
    if len(hull) < 3:
        return np.zeros(len(points), bool)
    edge = np.roll(hull, -1, axis=0) - hull
    rel = points[:, None, :] - hull[None, :, :]
    cross = edge[None, :, 0] * rel[:, :, 1] - edge[None, :, 1] * rel[:, :, 0]
    return np.all(cross >= -1e-9, axis=1)


def cell_grid(
    polygons: list[np.ndarray], step_tiles: float = 1.0, margin: int = 0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The ``step``-tile cells over the footprint ``polys``, ``margin`` cells wider on each side (cell ``i`` spans
    ``[i * step, (i + 1) * step]``): their centres (x, z rows), which of them lie under the footprint, and which
    are in the ring of cells touching it (a centre one step away from one under it, itself not under it)."""
    points = np.concatenate(polygons)
    min_x, min_z = np.floor(points.min(axis=0) / step_tiles).astype(int) - margin
    max_x, max_z = np.ceil(points.max(axis=0) / step_tiles).astype(int) + margin
    grid_z, grid_x = np.mgrid[min_z:max_z, min_x:max_x]
    centres = (np.stack([grid_x.ravel(), grid_z.ravel()], axis=1) + 0.5) * step_tiles
    under = np.zeros(len(centres), bool)
    near = np.zeros(len(centres), bool)
    for polygon in polygons:
        shell = hull(polygon)
        under |= inside(centres, shell)
        for offset_x, offset_z in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            near |= inside(centres + (offset_x * step_tiles, offset_z * step_tiles), shell)
    return centres, under, near & ~under


def cell_centres(polygons: list[np.ndarray], step_tiles: float = 1.0) -> np.ndarray:
    """The centres (x, z rows) of the ``step``-tile cells under the footprint ``polys``; a cell is under it when
    its centre is."""
    centres, under, _ = cell_grid(polygons, step_tiles)
    return centres[under]


def overlap(first: np.ndarray, second: np.ndarray) -> float:
    """How deep the convex hulls of two polygons reach into each other along the axis they part on least (the
    separating-axis test); 0 when they only touch or are apart."""
    best = math.inf
    for polygon in (hull(first), hull(second)):
        edge = np.roll(polygon, -1, axis=0) - polygon
        for edge_x, edge_z in edge:
            normal = np.array([-edge_z, edge_x])
            length = float(np.hypot(*normal))
            if length == 0:
                continue
            normal /= length
            first_projection, second_projection = first @ normal, second @ normal
            depth = min(first_projection.max(), second_projection.max()) - max(
                first_projection.min(), second_projection.min()
            )
            if depth <= 0:
                return 0.0
            best = min(best, depth)
    return 0.0 if best is math.inf else float(best)


def snap_place(position, rotation, polygons: Polygons, ground: bool = False, height_at=None) -> list[float]:
    """``position`` moved onto the footprint's grid (``snap_origin``), onto the ground when asked."""
    x, z = snap_origin(polygons, position, rotation)
    y = float(height_at(x, z)) if ground and height_at is not None else float(position[1])
    return [x, y, z]
