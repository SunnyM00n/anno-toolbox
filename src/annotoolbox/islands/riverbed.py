"""2070's river bed under the converted rivers (inland-water "What is built", the bed paint): 2070 draws its river
bed texture over the ground under every ribbon in its ground pass, wherever the ground lies far enough under the
ribbon lifted by ``BED_LIFT``; here the same test lays the bed as a ground material, per material texel, against the
ground the game draws.

``lay_river_bed`` is the entry (``islandground.GroundMixin.material_weights`` calls it); the 2070 material set carries
the bed as a material of its own (``terrain2070.build_material_set``), with ``bed_curve`` as its coverage curve.
"""

from __future__ import annotations

import math

from .islandgrid import AREA_ISLAND, HEIGHT_UNITS, ground_at, read_sparse
from .islandterrain import coarse_weight, page_ground_sampler, smooth_sample
from .rivercourse import is_fall_step, smooth_river

RIVER_BED_GUID = -1  # the bed's key among the 2070 ground texture GUIDs (``material_of``): 2070's are positive
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
    n = corners.shape[0] - 1
    c = np.arange(2 * n + 1) * n / (2 * n)
    sample = page_ground_sampler(smooth_sample(corners, c, c), coarse_weight(area) if area is not None else None)
    xs = (np.arange(n * per_tile) + 0.5) / per_tile
    out = np.empty((len(xs), len(xs)))
    for z0 in range(0, len(xs), 256):  # in bands: the cubic's arrays over a whole fine grid are large
        out[z0 : z0 + 256] = sample(xs, xs[z0 : z0 + 256]) / HEIGHT_UNITS
    return out


def island_area(builder):
    """The tile grid of the island area (``AreaIDs``) of a builder that has written it, else None."""
    import numpy as np

    gm = getattr(builder, "gm", None)
    ids = gm.path("AreaIDs") if gm is not None else None
    if ids is None:
        return None
    size = builder.size
    return np.asarray(read_sparse(ids), np.int64)[:size, :size] == AREA_ISLAND


def drawn_at(grid, per_tile: int, xs, zs):
    """A ``drawn_ground`` grid at the points (xs, zs) in tiles, bilinear."""
    return ground_at(grid, xs * per_tile - 0.5, zs * per_tile - 0.5)


def bed_curve(alpha):
    """The share of the bed texture's texels that pass 2070's test at each lift times vertex alpha from 0 to
    ``CURVE_TOP`` (``CURVE_STEPS`` values): ``alpha`` the texture's alphas (0..1). 2070 discards per pixel, the
    texture's alpha making the edge ragged; 117 blends per texel, so the share is the bed's weight there."""
    import numpy as np

    a = np.sort(np.asarray(alpha, np.float64).ravel())
    q = np.linspace(0.0, CURVE_TOP, CURVE_STEPS)
    need = np.where(q > 0, BED_REF / np.maximum(q, 1e-9), np.inf)
    return (1.0 - np.searchsorted(a, need, side="left") / max(len(a), 1)).astype(np.float32)


def cover(curve, lift):
    """The bed's weight at a lift (world units, lift x vertex alpha) from its ``bed_curve``."""
    import numpy as np

    q = np.clip(np.asarray(lift, np.float64), 0.0, CURVE_TOP) / CURVE_TOP * (len(curve) - 1)
    return np.interp(q, np.arange(len(curve)), curve)


def _ends(pts, samples) -> tuple[float, float, float]:
    """A river's length along its course samples and the lengths of its first and last stretch between its points,
    over which 2070's vertex alpha rises from 0 at a piece's end to 1 at its next point (117 fades the ribbon there,
    ``riverlook.RENDER_BLEND_BOTH``)."""
    lens = [math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(samples, samples[1:], strict=False)]
    first = math.hypot(pts[1][0] - pts[0][0], pts[1][2] - pts[0][2])
    last = math.hypot(pts[-1][0] - pts[-2][0], pts[-1][2] - pts[-2][2])
    return sum(lens), max(first, 1e-6), max(last, 1e-6)


def river_cover(river, ground, per_tile: int, curve, falls=None):
    """The bed's weight per texel (``ground``: the drawn ground, world units, ``per_tile`` texels a tile) under one
    river ((points (x, water, z), widths)): inside its ribbon, by 2070's test against the ribbon lowered by
    ``1 - alpha`` (0 at the river's ends, 1 past its first and last stretch) and lifted by ``BED_LIFT``."""
    import numpy as np

    pts, widths = river
    out = np.zeros(ground.shape, np.float32)
    best = np.full(ground.shape, np.inf)  # the distance to the stretch a texel took its weight from
    samples = smooth_river(pts, widths, SAMPLE_STEP, falls)
    if len(samples) < 2:
        return out
    total, first, last = _ends(pts, samples)
    n = ground.shape[0]
    run = 0.0
    for a, b in zip(samples, samples[1:], strict=False):
        (x0, w0, z0, r0), (x1, w1, z1, r1) = a, b
        seg = math.hypot(x1 - x0, z1 - z0)
        reach = max(r0, r1) / 2 + 1
        lo_x, hi_x = max(0, int((min(x0, x1) - reach) * per_tile)), min(n, int((max(x0, x1) + reach) * per_tile) + 1)
        lo_z, hi_z = max(0, int((min(z0, z1) - reach) * per_tile)), min(n, int((max(z0, z1) + reach) * per_tile) + 1)
        if lo_x >= hi_x or lo_z >= hi_z or seg <= 0:
            run += seg
            continue
        xs = (np.arange(lo_x, hi_x) + 0.5) / per_tile
        zs = (np.arange(lo_z, hi_z) + 0.5) / per_tile
        X, Z = np.meshgrid(xs, zs)
        along = ((X - x0) * (x1 - x0) + (Z - z0) * (z1 - z0)) / (seg * seg)
        t = np.clip(along, 0, 1)
        d = np.hypot(X - x0 - t * (x1 - x0), Z - z0 - t * (z1 - z0))
        inside = (d <= (r0 + (r1 - r0) * t) / 2) & (along >= 0 if run == 0 else True)
        inside &= along <= 1 if run + seg >= total - 1e-6 else True
        water = np.where(t > 0, w1, w0) if is_fall_step(a, b, falls) else w0 + (w1 - w0) * t
        s = run + t * seg
        alpha = np.clip(np.minimum(s / first, (total - s) / last), 0.0, 1.0)
        lift = (water - (1.0 - alpha) + BED_LIFT - ground[lo_z:hi_z, lo_x:hi_x]) * alpha
        # each texel takes the stretch nearest to it, as 2070's quads between cross-sections do: a later stretch's
        # round end behind it would carry its larger alpha back towards the river's start
        near, win = best[lo_z:hi_z, lo_x:hi_x], out[lo_z:hi_z, lo_x:hi_x]
        take = inside & (d < near)
        near[take] = d[take]
        win[take] = cover(curve, lift)[take]
        run += seg
    return out


def lay_river_bed(builder, mats: list[int], weights, per_tile: int, material_of: dict):
    """The bed laid over the material ``weights`` ([materials, H, W], ``mats`` their IDs) under every river of the
    builder, where the 2070 set carries the bed (``material_of[RIVER_BED_GUID]``) and its coverage curve
    (``builder.paint_curves[RIVER_BED_GUID]``); returns the weights. The heights must be final."""
    import numpy as np

    mid = material_of.get(RIVER_BED_GUID)
    curve = (getattr(builder, "paint_curves", None) or {}).get(RIVER_BED_GUID)
    rivers = getattr(builder, "rivers", None) or []
    if mid is None or curve is None or mid not in mats or not rivers:
        return weights
    ground = drawn_ground(np.asarray(builder.heights, np.float64), per_tile, island_area(builder))
    ground = ground[: weights.shape[1], : weights.shape[2]]
    bed = np.zeros(ground.shape, np.float32)
    for river in rivers:
        np.maximum(bed, river_cover(river, ground, per_tile, curve, getattr(builder, "falls", None)), out=bed)
    k = mats.index(mid)
    weights *= 1.0 - bed
    weights[k] += bed
    if per_tile == FINEST:
        builder.report["river bed texels"] = int((bed > 0.5).sum())
    return weights
