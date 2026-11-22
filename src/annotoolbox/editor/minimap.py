"""The island as a strategy minimap: land from the heights above sea level, a coast outline, a mountain colour by
height, and underwater plateaus in teal with a light border. ``paint(heights, ...)`` returns an RGBA picture; no Qt.
How the picture is made: ``docs/island-editor.md`` ("Minimap").
"""

from __future__ import annotations

import numpy as np

# The three colours of the minimap's land (normal, outline, mountain) as RGB; the game's own come from its minimap
# scene asset, these are neutral stand-ins.
LAND_COLORS = {"normal": (205, 190, 140), "outline": (110, 95, 60), "mountain": (140, 135, 125)}
MAX_MOUNTAIN = 40.0  # the height at which the land has taken the mountain colour
UNDERWATER_COLOR = (49, 110, 133)
UNDERWATER_BORDER = (158, 224, 255)
UNDERWATER_LEVEL = -28.0  # an underwater island's ground lies above this, at most at sea level
RAW_PER_UNIT = 128.0  # the heights are compared as the game stores them: 16-bit integers, 128 to a unit
KERNEL = 5  # the coast outline is a box of this many pixels over the land mask


def _box(mask: np.ndarray, size_px: int = KERNEL) -> np.ndarray:
    """The mean of ``mask`` over a ``size_px`` x ``size_px`` box; the border pixels keep the plain mask."""
    pad = size_px // 2
    float_mask = mask.astype(np.float32)
    height_px, width_px = float_mask.shape
    if height_px <= 2 * pad or width_px <= 2 * pad:
        return float_mask
    total = np.zeros((height_px - 2 * pad, width_px - 2 * pad), np.float32)
    for offset_z in range(size_px):
        for offset_x in range(size_px):
            total += float_mask[offset_z : offset_z + height_px - 2 * pad, offset_x : offset_x + width_px - 2 * pad]
    out = float_mask.copy()
    out[pad : height_px - pad, pad : width_px - pad] = total / (size_px * size_px)
    return out


def _around(mask: np.ndarray) -> np.ndarray:
    """``a`` dilated by one pixel in all eight directions."""
    padded = np.pad(mask, 1)
    out = np.zeros_like(mask)
    for offset_z in range(3):
        for offset_x in range(3):
            out |= padded[offset_z : offset_z + mask.shape[0], offset_x : offset_x + mask.shape[1]]
    return out


def _grow(seed: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    """Everything in ``allowed`` that is 8-connected to ``seed`` (itself or a neighbour of it).

    One labelling pass when scipy is there (linear in the pixels); without it the region grows a ring at a time."""
    reached = _around(seed) & allowed
    try:
        from scipy import ndimage
    except ImportError:
        while True:
            more = reached | (_around(reached) & allowed)
            if more.sum() == reached.sum():
                return reached
            reached = more
    labels, _count = ndimage.label(allowed, structure=np.ones((3, 3), bool))
    return np.isin(labels, labels[reached]) & allowed


def sample(heights: np.ndarray, width_px: int) -> np.ndarray:
    """``heights`` (rows z, columns x) resampled to ``width_px`` x ``width_px`` pixels, the nearest sample of each."""
    side = heights.shape[0]
    indices = np.minimum(side - 1, (np.arange(width_px) * side) // width_px)
    return heights[np.ix_(indices, indices)]


def paint(heights: np.ndarray, pixels: int, colors: dict | None = None, underwater: bool = True) -> np.ndarray:
    """The minimap of an island whose height samples are ``heights`` (rows z, columns x, in world units above sea
    level), ``pixels`` wide and high, as a uint8 array (``pixels``, ``pixels``, 4) of RGBA.

    Land is every sample above sea level; its coast is the outline colour fading in over a box of the land mask, the
    inside is the normal colour turning to the mountain colour with height up to ``MAX_MOUNTAIN``; sea is clear.
    With ``underwater``, clear ground between ``UNDERWATER_LEVEL`` and sea level that has deep sea all round (a
    patch that touches painted land is a shelf or a lake and stays sea) is teal, edged in the light border.
    ``colors``: ``LAND_COLORS`` with any of its keys replaced."""
    land_colors = {**LAND_COLORS, **(colors or {})}
    sampled = sample(np.asarray(heights, np.float32), pixels)
    land = np.round(sampled * RAW_PER_UNIT) > 0
    cover = _box(land)
    normal, outline, mountain = (np.array(land_colors[k], np.float32) for k in ("normal", "outline", "mountain"))
    mountain_blend = np.clip(sampled / MAX_MOUNTAIN, 0.0, 1.0)[..., None]
    inner = normal + (mountain - normal) * mountain_blend
    cover_column = cover[..., None]
    rgb = np.where(cover_column > 0.75, inner, outline + (inner - outline) * ((cover_column - 0.5) / 0.25).clip(0, 1))
    alpha = np.where(cover <= 0.25, 0.0, np.where(cover <= 0.5, (cover - 0.25) / 0.25, 1.0))
    out = np.zeros((pixels, pixels, 4), np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = (alpha * 255).astype(np.uint8)
    out[cover <= 0.25, :3] = 0
    if underwater:
        _underwater(out, sampled)
    return out


def _underwater(out: np.ndarray, sampled: np.ndarray) -> None:
    """Teal over the clear pixels of ``out`` that are an underwater island's ground, with the light border."""
    clear = out[..., 3] == 0
    candidate = clear & (sampled > UNDERWATER_LEVEL) & (sampled <= 0.0)
    kept = candidate & ~_grow(~clear, candidate)
    if not kept.any():
        return
    padded = np.pad(kept, 1)
    inside = padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    border = kept & ~inside
    lift = np.clip((sampled - UNDERWATER_LEVEL - 1.0) / -(UNDERWATER_LEVEL + 1.0), 0.0, 1.0)[..., None] * 0.35
    teal = np.array(UNDERWATER_COLOR, np.float32)
    edge = np.array(UNDERWATER_BORDER, np.float32)
    shaded = teal + (edge - teal) * lift
    out[kept, :3] = shaded[kept].astype(np.uint8)
    out[border, :3] = UNDERWATER_BORDER
    out[kept, 3] = 255
