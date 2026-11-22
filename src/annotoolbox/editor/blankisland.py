"""A new Anno 117 island from scratch at any of the game's grid sizes, without another game's island to start from.

The game reads an island only with the whole file set its grid size needs, so a new island starts on a vanilla
island of that grid (``donor117.DONORS``: 128 to 768 tiles): the build clears its objects and props (``"clear"``)
and its ground is shaped by an ordinary height edit into a lobed plateau at the island's own land level with a
beach and a shelf into the sea, the grid's edge left as it was so the sea bed meets its neighbours. ``blank_edits``
returns the edit file; it is saved as a new island (``target`` ``new``) under its own name (island-editor "Full spec").
"""

from __future__ import annotations

import math
import random

import numpy as np

from ..islands.donor117 import DONORS, GRID_SIZES

PREFIX = "new"  # a blank island's name: new<grid>
REACH = 0.34  # the coast's mean distance from the centre, as a share of the grid
BEACH = 4.0  # tiles from the coast to the plateau
SHELF = 6.0  # tiles the sea bed takes to reach its depth
EDGE = 8.0  # tiles along the grid's edge blended back to the island's own ground
LAND = 0.0625  # a corner over this is land (the land bit's rule)


def name(grid: int) -> str:
    return f"{PREFIX}{grid}"


def grid_of(island: str) -> int | None:
    """The grid of a blank island's name (``new256`` -> 256), None for any other name."""
    digits = island.removeprefix(PREFIX)
    return int(digits) if island.startswith(PREFIX) and digits.isdigit() and int(digits) in DONORS else None


def donor(grid: int) -> str:
    """The vanilla island a blank island of ``grid`` tiles starts on (its ``.a7m`` game path)."""
    if grid not in DONORS:
        raise ValueError(f"no island grid of {grid} tiles: one of {', '.join(map(str, GRID_SIZES))}")
    return DONORS[grid] + ".a7m"


def coast_distance(grid: int, seed: int = 1) -> np.ndarray:
    """Per corner, the tiles inside a gently lobed coast (negative out at sea)."""
    rnd = random.Random(seed)
    lobes = [(k, rnd.uniform(0.04, 0.12) / k**0.5, rnd.uniform(0, 2 * math.pi)) for k in (2, 3, 5, 7)]
    z_grid, x_grid = np.mgrid[0 : grid + 1, 0 : grid + 1].astype(np.float64)
    centre = grid / 2
    angle = np.arctan2(z_grid - centre, x_grid - centre)
    reach = REACH * grid * (1 + sum(amplitude * np.cos(k * angle + phase) for k, amplitude, phase in lobes))
    return reach - np.hypot(x_grid - centre, z_grid - centre)


def land_level(corners: np.ndarray) -> float:
    """The island's commonest land height (to a twentieth of a unit): where its builders put the plateau."""
    land = corners[corners > LAND]
    if not land.size:
        return 0.5
    values, counts = np.unique(np.round(land * 20) / 20, return_counts=True)
    return float(values[counts.argmax()])


def target_heights(corners: np.ndarray, seed: int = 1) -> np.ndarray:
    """The blank island's corner heights on the grid of ``corners`` (the vanilla island's, rows z)."""
    grid = corners.shape[0] - 1
    coast = coast_distance(grid, seed)
    land = land_level(corners)
    edge = corners[[0, -1], :].ravel().tolist() + corners[:, [0, -1]].ravel().tolist()
    bed = min(float(np.median(edge)), -1.0)
    shaped = np.where(
        coast >= BEACH, land, np.where(coast >= 0, land * coast / BEACH, bed * (1 - np.exp(coast / SHELF)))
    )
    z_grid, x_grid = np.mgrid[0 : grid + 1, 0 : grid + 1]
    to_edge = np.minimum(np.minimum(x_grid, grid - x_grid), np.minimum(z_grid, grid - z_grid))
    keep = np.clip(1 - to_edge / EDGE, 0, 1)  # 1 at the edge: the vanilla ground
    return (shaped * (1 - keep) + corners * keep).astype(np.float32)


def blank_edits(corners: np.ndarray, grid: int, forest=(), seed: int = 1, source: str | None = None) -> dict:
    """The edit file of a blank island of ``grid`` tiles on the vanilla island whose corner heights are
    ``corners`` and whose forest tiles are ``forest`` ((x, z) pairs): cleared, reshaped, its forest cleared, saved
    as a new island named ``name(grid)``. With ``source`` (the game path of a vanilla island of any grid, from a game
    that replaces its islands in place) the island is cleared and reshaped where it stands: no new name, no target."""
    if corners.shape != (grid + 1, grid + 1):
        raise ValueError(f"the heights are {corners.shape}, a {grid}-tile grid has {grid + 1} corners a side")
    delta = target_heights(corners, seed) - corners
    heights = {
        f"{z},{x}": round(float(delta[z, x]), 3) for z, x in zip(*np.nonzero(np.abs(delta) >= 1e-3), strict=True)
    }
    if source is not None:
        return {"source": source, "clear": True, "heights": heights}
    return {
        "source": donor(grid),
        "target": "new",
        "name": name(grid),
        "clear": True,
        "heights": heights,
        "forest": {f"{x},{z}": 0 for x, z in forest},
    }


PLAIN_RADIUS = 32.0  # tiles: the brush a plain ground is laid with; its dabs sit a quarter radius apart


def plain_ground(size_tiles: int, material: int) -> list[dict]:
    """The paint stroke that lays ``material`` over a whole grid of ``size_tiles`` tiles, so a blank island of an older game
    does not keep the donor island's ground: one serpentine line of dabs, covering every tile at least nine tenths
    (``islandground.stroke_cover``'s soft brush), the way an ordinary paint edit lays it."""
    step = PLAIN_RADIUS / 4
    along = [min(float(size_tiles), k * step) for k in range(math.ceil(size_tiles / step) + 1)]
    points = [[x, z] for row, z in enumerate(along) for x in (along if row % 2 == 0 else along[::-1])]
    return [{"material": int(material), "radius": PLAIN_RADIUS, "strength": 1.0, "points": points}]


def dominant_material(ground: dict, weights: bytes) -> int | None:
    """The material most of the island's ground is (``IslandEditor.ground()`` and its weight maps), None without any."""
    count = len(ground.get("materials", []))
    if not count:
        return None
    share = np.frombuffer(weights, np.uint8).reshape(count, -1).sum(axis=1, dtype=np.int64)
    return int(ground["materials"][int(share.argmax())]["id"])
