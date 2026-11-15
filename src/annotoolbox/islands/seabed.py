"""The sea bed of an island: every island grid ends at one floor height, so that neighbouring grids and flat floor
islands between them meet without a step, and the whole grid is drawn (island-maps "The sea bed").

``floor_heights`` is a shaping step on the height field before anything is written from it; ``is_flat`` tells the
stored meshes which nodes can be drawn coarse. An island merged against another at a seam (``islandvariant``) keeps
the ground on that side.
"""

from __future__ import annotations

FLOOR = -40.0  # world units: 2070's sea bed around its islands; every grid edge and every floor island lies here
EDGE_BLEND = 8  # tiles from the grid edge over which 2070's deep ground blends to FLOOR
KEEP_ABOVE = -20.0  # world units under the island's land level: ground above this is never moved
KEEP_FADE = 10.0  # world units below KEEP_ABOVE over which the blend comes in fully
EDGE_SURE = 2  # tiles from the grid edge within which even kept ground joins the floor: a 2070 footprint that fills
# its grid (island-terrain "The grid") has no padding, and its edge must still meet the floor
FLAT_NODE = 0.05  # world units: a stored-mesh node whose heights vary less is drawn as a 2x2 grid


def edge_distance(n: int, seam=(False, False, False, False)):
    """Corner distance (tiles) from the nearest grid edge, not counting the ``seam`` sides (west, north, east, south):
    there the grid meets another island's, whose ground must continue it."""
    import numpy as np

    idx = np.arange(n, dtype=np.float64)
    lo, hi = idx, n - 1 - idx
    dx = np.minimum(np.inf if seam[0] else lo, np.inf if seam[2] else hi)
    dz = np.minimum(np.inf if seam[1] else lo, np.inf if seam[3] else hi)
    return np.minimum.outer(np.broadcast_to(dz, (n,)), np.broadcast_to(dx, (n,)))


def floor_heights(heights, inside, land_level: float = 0.0, seam=(False, False, False, False)):
    """``heights`` (corners, world units, numpy, rows z) with the sea bed laid: corners outside 2070's terrain
    (``inside`` False: the padding and 2070's empty chunks) at ``FLOOR``, and the deep ground within ``EDGE_BLEND``
    tiles of the grid edge blended to it. Ground higher than ``land_level + KEEP_ABOVE`` stays as it is, and the
    blend fades in over ``KEEP_FADE`` below that, so the coast, the harbours and an underwater island's plateau keep
    2070's heights, except within ``EDGE_SURE`` tiles of the grid edge, which always lies at the floor. On a ``seam``
    side (west, north, east, south) neither rule applies: that edge keeps its ground, which the island on the other
    side of the seam has the same. Returns a new array."""
    import numpy as np

    h = np.where(inside, np.asarray(heights, np.float64), FLOOR)
    dist = edge_distance(h.shape[0], seam)  # corner distance from the nearest grid edge, tiles
    t = np.clip(1.0 - dist / EDGE_BLEND, 0.0, 1.0)
    w = t * t * (3 - 2 * t)  # smooth: full at the edge, none from EDGE_BLEND in
    keep = land_level + KEEP_ABOVE
    m = np.clip((keep - h) / KEEP_FADE, 0.0, 1.0)  # 0 above keep, 1 from KEEP_FADE below it
    m = np.where(dist <= EDGE_SURE, 1.0, m)
    return h + (FLOOR - h) * w * m


def is_flat(block) -> bool:
    """True when a node's heights (heightmap units) vary less than ``FLAT_NODE`` world units."""
    from .islandgrid import HEIGHT_UNITS

    return float(block.max() - block.min()) < FLAT_NODE * HEIGHT_UNITS
