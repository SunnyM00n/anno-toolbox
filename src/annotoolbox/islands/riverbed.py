"""2070's river bed under the converted rivers (inland-water "What is built", the bed paint): 2070 draws its river
bed texture over the ground under every ribbon in its ground pass, wherever the ground lies far enough under the
ribbon lifted by ``BED_LIFT``; here the same test lays the bed as a ground material, per material texel, against the
ground the game draws.

``lay_river_bed`` is the entry (``islandground.CGroundMixin.material_weights`` calls it); the 2070 material set carries
the bed as a material of its own (``terrain2070.build_material_set``), with ``bed_curve`` as its coverage curve.
"""

from __future__ import annotations

import math

from .islandgrid import AREA_ISLAND, HEIGHT_UNITS, ground_at, read_sparse
from .islandterrain import coarse_weight, page_ground_sampler, smooth_sample
from .rivercourse import is_fall_step, smooth_river

RIVER_BED = -1  # the bed's key among the 2070 ground texture GUIDs (``material_of``): 2070's are positive
BED_DIFFUSE = "data/graphics/effects/river/n_ground_diff_0.dds"  # what 2070's river manager loads for its bed
BED_NORMAL = "data/graphics/effects/river/n_ground_norm_0.dds"
BED_NAME = "river_bed"
# river.fx RiverGround: the ribbon lowered by (1 - vertex alpha), lifted by BED_LIFT; a pixel shows the bed where
# (lift over the ground) x (bed texture alpha) x (vertex alpha) reaches BED_REF
BED_LIFT = 0.2
BED_REF = 0.05
CURVE_TOP = 1.0  # world units of lift the coverage curve spans (``bed_curve``); above, the curve's last value
CURVE_STEPS = 256
FINEST = 8  # material texels a tile at the finest level (reported there)
SAMPLE_STEP = 0.5  # tiles between the course samples the ribbon is laid from (``smooth_river``)


def drawn_ground(heights, per_tile: int, area=None):
    """The ground as the game draws it near the camera (world units, ``per_tile`` samples a tile at their centres,
    rows z): the height pages' surface over the coarse map the corner ``heights`` (heightmap units) give
    (``islandterrain.page_ground_sampler``, held to the coarse map on the tile grid ``area``). What the water checks
    and the bed paint measure against (inland-water section 4, "Coarse copies at the water")."""
    import numpy as np

    corners = np.asarray(heights, np.float64)
    tile_count = corners.shape[0] - 1
    positions = np.arange(2 * tile_count + 1) * tile_count / (2 * tile_count)
    sample = page_ground_sampler(
        smooth_sample(corners, positions, positions), coarse_weight(area) if area is not None else None
    )
    xs = (np.arange(tile_count * per_tile) + 0.5) / per_tile
    out = np.empty((len(xs), len(xs)))
    for band_start in range(0, len(xs), 256):  # in bands: the cubic's arrays over a whole fine grid are large
        out[band_start : band_start + 256] = sample(xs, xs[band_start : band_start + 256]) / HEIGHT_UNITS
    return out


def island_area(builder):
    """The tile grid of the island area (``AreaIDs``) of a builder that has written it, else None."""
    import numpy as np

    game_manager = getattr(builder, "game_session_manager", None)
    ids = game_manager.path("AreaIDs") if game_manager is not None else None
    if ids is None:
        return None
    tile_count = builder.size
    return np.asarray(read_sparse(ids), np.int64)[:tile_count, :tile_count] == AREA_ISLAND


def drawn_at(grid, per_tile: int, xs, zs):
    """A ``drawn_ground`` grid at the points (xs, zs) in tiles, bilinear."""
    return ground_at(grid, xs * per_tile - 0.5, zs * per_tile - 0.5)


def bed_curve(alpha):
    """The share of the bed texture's texels that pass 2070's test at each lift times vertex alpha from 0 to
    ``CURVE_TOP`` (``CURVE_STEPS`` values): ``alpha`` the texture's alphas (0..1). 2070 discards per pixel, the
    texture's alpha making the edge ragged; 117 blends per texel, so the share is the bed's weight there."""
    import numpy as np

    sorted_alpha = np.sort(np.asarray(alpha, np.float64).ravel())
    lifts = np.linspace(0.0, CURVE_TOP, CURVE_STEPS)
    need = np.where(lifts > 0, BED_REF / np.maximum(lifts, 1e-9), np.inf)
    return (1.0 - np.searchsorted(sorted_alpha, need, side="left") / max(len(sorted_alpha), 1)).astype(np.float32)


def cover(curve, lift_m):
    """The bed's weight at a lift (world units, lift x vertex alpha) from its ``bed_curve``."""
    import numpy as np

    curve_position = np.clip(np.asarray(lift_m, np.float64), 0.0, CURVE_TOP) / CURVE_TOP * (len(curve) - 1)
    return np.interp(curve_position, np.arange(len(curve)), curve)


def _ends(points, samples) -> tuple[float, float, float]:
    """A river's length along its course samples and the lengths of its first and last stretch between its points,
    over which 2070's vertex alpha rises from 0 at a piece's end to 1 at its next point (117 fades the ribbon there,
    ``riverlook.RENDER_BLEND_BOTH``)."""
    lengths = [
        math.hypot(there[0] - here[0], there[2] - here[2]) for here, there in zip(samples, samples[1:], strict=False)
    ]
    first = math.hypot(points[1][0] - points[0][0], points[1][2] - points[0][2])
    last = math.hypot(points[-1][0] - points[-2][0], points[-1][2] - points[-2][2])
    return sum(lengths), max(first, 1e-6), max(last, 1e-6)


def river_cover(river, ground, per_tile: int, curve, falls=None):
    """The bed's weight per texel (``ground``: the drawn ground, world units, ``per_tile`` texels a tile) under one
    river ((points (x, water, z), widths)): inside its ribbon, by 2070's test against the ribbon lowered by
    ``1 - alpha`` (0 at the river's ends, 1 past its first and last stretch) and lifted by ``BED_LIFT``."""
    import numpy as np

    points, widths = river
    out = np.zeros(ground.shape, np.float32)
    best = np.full(ground.shape, np.inf)  # the distance to the stretch a texel took its weight from
    samples = smooth_river(points, widths, SAMPLE_STEP, falls)
    if len(samples) < 2:
        return out
    total, first, last = _ends(points, samples)
    side_texels = ground.shape[0]
    run = 0.0
    for start_sample, end_sample in zip(samples, samples[1:], strict=False):
        (start_x, start_water, start_z, start_width), (end_x, end_water, end_z, end_width) = start_sample, end_sample
        segment_length = math.hypot(end_x - start_x, end_z - start_z)
        reach = max(start_width, end_width) / 2 + 1
        min_x, max_x = (
            max(0, int((min(start_x, end_x) - reach) * per_tile)),
            min(side_texels, int((max(start_x, end_x) + reach) * per_tile) + 1),
        )
        min_z, max_z = (
            max(0, int((min(start_z, end_z) - reach) * per_tile)),
            min(side_texels, int((max(start_z, end_z) + reach) * per_tile) + 1),
        )
        if min_x >= max_x or min_z >= max_z or segment_length <= 0:
            run += segment_length
            continue
        xs = (np.arange(min_x, max_x) + 0.5) / per_tile
        zs = (np.arange(min_z, max_z) + 0.5) / per_tile
        grid_x, grid_z = np.meshgrid(xs, zs)
        along = ((grid_x - start_x) * (end_x - start_x) + (grid_z - start_z) * (end_z - start_z)) / (
            segment_length * segment_length
        )
        fraction = np.clip(along, 0, 1)
        distance = np.hypot(
            grid_x - start_x - fraction * (end_x - start_x), grid_z - start_z - fraction * (end_z - start_z)
        )
        inside = (distance <= (start_width + (end_width - start_width) * fraction) / 2) & (
            along >= 0 if run == 0 else True
        )
        inside &= along <= 1 if run + segment_length >= total - 1e-6 else True
        water = (
            np.where(fraction > 0, end_water, start_water)
            if is_fall_step(start_sample, end_sample, falls)
            else start_water + (end_water - start_water) * fraction
        )
        course_position = run + fraction * segment_length
        alpha = np.clip(np.minimum(course_position / first, (total - course_position) / last), 0.0, 1.0)
        lift = (water - (1.0 - alpha) + BED_LIFT - ground[min_z:max_z, min_x:max_x]) * alpha
        # each texel takes the stretch nearest to it, as 2070's quads between cross-sections do: a later stretch's
        # round end behind it would carry its larger alpha back towards the river's start
        near, win = best[min_z:max_z, min_x:max_x], out[min_z:max_z, min_x:max_x]
        take = inside & (distance < near)
        near[take] = distance[take]
        win[take] = cover(curve, lift)[take]
        run += segment_length
    return out


def lay_river_bed(builder, materials: list[int], weights, per_tile: int, material_of: dict):
    """The bed laid over the material ``weights`` ([materials, H, W], ``materials`` their IDs) under every river of the
    builder, where the 2070 set carries the bed (``material_of[RIVER_BED]``) and its coverage curve
    (``builder.paint_curves[RIVER_BED]``); returns the weights. The heights must be final."""
    import numpy as np

    material_id = material_of.get(RIVER_BED)
    curve = (getattr(builder, "paint_curves", None) or {}).get(RIVER_BED)
    rivers = getattr(builder, "rivers", None) or []
    if material_id is None or curve is None or material_id not in materials or not rivers:
        return weights
    ground = drawn_ground(np.asarray(builder.heights, np.float64), per_tile, island_area(builder))
    ground = ground[: weights.shape[1], : weights.shape[2]]
    bed = np.zeros(ground.shape, np.float32)
    for river in rivers:
        np.maximum(bed, river_cover(river, ground, per_tile, curve, getattr(builder, "falls", None)), out=bed)
    layer_index = materials.index(material_id)
    weights *= 1.0 - bed
    weights[layer_index] += bed
    if per_tile == FINEST:
        builder.report["river bed texels"] = int((bed > 0.5).sum())
    return weights
