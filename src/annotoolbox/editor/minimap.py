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


def _box(mask: np.ndarray, size: int = KERNEL) -> np.ndarray:
    """The mean of ``mask`` over a ``size`` x ``size`` box; the border pixels keep the plain mask."""
    pad = size // 2
    m = mask.astype(np.float32)
    h, w = m.shape
    if h <= 2 * pad or w <= 2 * pad:
        return m
    total = np.zeros((h - 2 * pad, w - 2 * pad), np.float32)
    for dz in range(size):
        for dx in range(size):
            total += m[dz : dz + h - 2 * pad, dx : dx + w - 2 * pad]
    out = m.copy()
    out[pad : h - pad, pad : w - pad] = total / (size * size)
    return out


def _grow(seed: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    """Everything in ``allowed`` that is 8-connected to ``seed`` (itself or a neighbour of it)."""

    def around(a: np.ndarray) -> np.ndarray:
        p = np.pad(a, 1)
        out = np.zeros_like(a)
        for dz in range(3):
            for dx in range(3):
                out |= p[dz : dz + a.shape[0], dx : dx + a.shape[1]]
        return out

    reached = around(seed) & allowed
    while True:
        more = reached | (around(reached) & allowed)
        if more.sum() == reached.sum():
            return reached
        reached = more


def sample(heights: np.ndarray, width: int) -> np.ndarray:
    """``heights`` (rows z, columns x) resampled to ``width`` x ``width`` pixels, the nearest sample of each."""
    n = heights.shape[0]
    idx = np.minimum(n - 1, (np.arange(width) * n) // width)
    return heights[np.ix_(idx, idx)]


def paint(heights: np.ndarray, pixels: int, colors: dict | None = None, underwater: bool = True) -> np.ndarray:
    """The minimap of an island whose height samples are ``heights`` (rows z, columns x, in world units above sea
    level), ``pixels`` wide and high, as a uint8 array (``pixels``, ``pixels``, 4) of RGBA.

    Land is every sample above sea level; its coast is the outline colour fading in over a box of the land mask, the
    inside is the normal colour turning to the mountain colour with height up to ``MAX_MOUNTAIN``; sea is clear.
    With ``underwater``, clear ground between ``UNDERWATER_LEVEL`` and sea level that has deep sea all round (a
    patch that touches painted land is a shelf or a lake and stays sea) is teal, edged in the light border.
    ``colors``: ``LAND_COLORS`` with any of its keys replaced."""
    c = {**LAND_COLORS, **(colors or {})}
    h = sample(np.asarray(heights, np.float32), pixels)
    land = np.round(h * RAW_PER_UNIT) > 0
    cover = _box(land)
    normal, outline, mountain = (np.array(c[k], np.float32) for k in ("normal", "outline", "mountain"))
    t = np.clip(h / MAX_MOUNTAIN, 0.0, 1.0)[..., None]
    inner = normal + (mountain - normal) * t
    cv = cover[..., None]
    rgb = np.where(cv > 0.75, inner, outline + (inner - outline) * ((cv - 0.5) / 0.25).clip(0, 1))
    alpha = np.where(cover <= 0.25, 0.0, np.where(cover <= 0.5, (cover - 0.25) / 0.25, 1.0))
    out = np.zeros((pixels, pixels, 4), np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = (alpha * 255).astype(np.uint8)
    out[cover <= 0.25, :3] = 0
    if underwater:
        _underwater(out, h)
    return out


def _underwater(out: np.ndarray, h: np.ndarray) -> None:
    """Teal over the clear pixels of ``out`` that are an underwater island's ground, with the light border."""
    clear = out[..., 3] == 0
    candidate = clear & (h > UNDERWATER_LEVEL) & (h <= 0.0)
    kept = candidate & ~_grow(~clear, candidate)
    if not kept.any():
        return
    p = np.pad(kept, 1)
    inside = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    border = kept & ~inside
    lift = np.clip((h - UNDERWATER_LEVEL - 1.0) / -(UNDERWATER_LEVEL + 1.0), 0.0, 1.0)[..., None] * 0.35
    teal = np.array(UNDERWATER_COLOR, np.float32)
    edge = np.array(UNDERWATER_BORDER, np.float32)
    shaded = teal + (edge - teal) * lift
    out[kept, :3] = shaded[kept].astype(np.uint8)
    out[border, :3] = UNDERWATER_BORDER
    out[kept, 3] = 255
