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


def edge_distance(corner_count: int, seam=(False, False, False, False)):
    """Corner distance (tiles) from the nearest grid edge, not counting the ``seam`` sides (west, north, east, south):
    there the grid meets another island's, whose ground must continue it."""
    import numpy as np

    index = np.arange(corner_count, dtype=np.float64)
    low, high = index, corner_count - 1 - index
    distance_x = np.minimum(np.inf if seam[0] else low, np.inf if seam[2] else high)
    distance_z = np.minimum(np.inf if seam[1] else low, np.inf if seam[3] else high)
    return np.minimum.outer(np.broadcast_to(distance_z, (corner_count,)), np.broadcast_to(distance_x, (corner_count,)))


def floor_heights(heights, inside, land_level: float = 0.0, seam=(False, False, False, False)):
    """``heights`` (corners, world units, numpy, rows z) with the sea bed laid: corners outside 2070's terrain
    (``inside`` False: the padding and 2070's empty chunks) at ``FLOOR``, and the deep ground within ``EDGE_BLEND``
    tiles of the grid edge blended to it. Ground higher than ``land_level + KEEP_ABOVE`` stays as it is, and the
    blend fades in over ``KEEP_FADE`` below that, so the coast, the harbors and an underwater island's plateau keep
    2070's heights, except within ``EDGE_SURE`` tiles of the grid edge, which always lies at the floor. On a ``seam``
    side (west, north, east, south) neither rule applies: that edge keeps its ground, which the island on the other
    side of the seam has the same. Returns a new array."""
    import numpy as np

    laid = np.where(inside, np.asarray(heights, np.float64), FLOOR)
    distance_tiles = edge_distance(laid.shape[0], seam)  # corner distance from the nearest grid edge, tiles
    blend_fraction = np.clip(1.0 - distance_tiles / EDGE_BLEND, 0.0, 1.0)
    blend_weight = (
        blend_fraction * blend_fraction * (3 - 2 * blend_fraction)
    )  # smooth: full at the edge, none from EDGE_BLEND in
    keep = land_level + KEEP_ABOVE
    keep_mask = np.clip((keep - laid) / KEEP_FADE, 0.0, 1.0)  # 0 above keep, 1 from KEEP_FADE below it
    keep_mask = np.where(distance_tiles <= EDGE_SURE, 1.0, keep_mask)
    return laid + (FLOOR - laid) * blend_weight * keep_mask


def is_flat(block) -> bool:
    """True when a node's heights (heightmap units) vary less than ``FLAT_NODE`` world units."""
    from .islandgrid import HEIGHT_UNITS

    return float(block.max() - block.min()) < FLAT_NODE * HEIGHT_UNITS
