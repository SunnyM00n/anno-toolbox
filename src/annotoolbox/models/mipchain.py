"""The mip chain of an 8-bit RGBA image, built in numpy (docs/texture-pipeline.md).

A level is the area average of the one above (a plain 2x2 box where both sizes are even). The colour is filtered
on its own, not weighted by alpha, and the alpha channel on its own: a gloss or cutout alpha must not darken the
colour (known-issues-buildings "Textures"). Textures that store sRGB are filtered in linear light.
"""

from __future__ import annotations

import numpy as np

_SRGB_TO_LINEAR = np.array(
    [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (np.arange(256) / 255.0)], np.float64
)


def srgb_to_linear(stored: np.ndarray) -> np.ndarray:
    """8-bit stored values to linear light, float64 0..1."""
    return _SRGB_TO_LINEAR[stored]


def linear_to_srgb8(linear: np.ndarray) -> np.ndarray:
    """Linear light 0..1 to the nearest stored 8-bit value."""
    linear = np.clip(linear, 0.0, 1.0)
    encoded = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055)
    return np.round(encoded * 255.0).astype(np.uint8)


def exposed_srgb8(values: np.ndarray, stops: float = 0.0) -> np.ndarray:
    """Linear float texels ``(h, w, 4)`` as 8-bit sRGB at an exposure of ``stops`` (alpha clipped, not curved)."""
    rgb = linear_to_srgb8(values[..., :3] * 2.0**stops)
    alpha = np.round(np.clip(values[..., 3:4], 0.0, 1.0) * 255.0).astype(np.uint8)
    return np.concatenate([rgb, alpha], axis=2)


FILTER_VERSION = 1  # part of the cache keys: bumped when the filter changes what comes out


def level_count(width_px: int, height_px: int) -> int:
    return max(width_px, height_px).bit_length()


def _weights(old: int, new: int) -> np.ndarray:
    """``(new, old)`` area-coverage weights of the old texels in each new texel."""
    edges = np.arange(new + 1) * (old / new)
    low, high = np.arange(old), np.arange(1, old + 1)
    cover = np.minimum(edges[1:, None], high[None, :]) - np.maximum(edges[:-1, None], low[None, :])
    return np.clip(cover, 0, None) / (old / new)


def _halve(level: np.ndarray) -> np.ndarray:
    """``(h, w, c)`` float64 to the next level (each side halved, at least 1)."""
    height_px, width_px = level.shape[:2]
    halved_height_px, halved_width_px = max(1, height_px // 2), max(1, width_px // 2)
    if height_px == 2 * halved_height_px and width_px == 2 * halved_width_px:
        return level.reshape(halved_height_px, 2, halved_width_px, 2, -1).mean(axis=(1, 3))
    return np.einsum(
        "yh,hwc->ywc",
        _weights(height_px, halved_height_px),
        np.einsum("xw,hwc->hxc", _weights(width_px, halved_width_px), level),
    )


def mip_chain(rgba: np.ndarray, count: int = 0, gamma: bool = False, to_srgb: bool = False) -> list[np.ndarray]:
    """The levels of ``rgba`` (``(h, w, 4)`` uint8), ``count`` of them (0: down to 1x1), level 0 being the image.

    ``gamma``: the colour holds sRGB-stored values and is filtered in linear light. ``to_srgb``: the colour holds
    linear values that are stored sRGB-encoded (every level is encoded, level 0 too).
    """
    height_px, width_px = rgba.shape[:2]
    total = level_count(width_px, height_px)
    count = min(count, total) if count else total
    if gamma:
        colour = srgb_to_linear(rgba[..., :3])
    else:
        colour = rgba[..., :3].astype(np.float64) / 255.0
    alpha = rgba[..., 3:].astype(np.float64)

    def store(level_colour: np.ndarray, level_alpha: np.ndarray) -> np.ndarray:
        rgb = (
            linear_to_srgb8(level_colour)
            if (gamma or to_srgb)
            else np.round(np.clip(level_colour, 0, 1) * 255.0).astype(np.uint8)
        )
        return np.concatenate([rgb, np.round(level_alpha).astype(np.uint8)], axis=-1)

    levels = [store(colour, alpha) if to_srgb else np.ascontiguousarray(rgba)]
    for _ in range(count - 1):
        colour, alpha = _halve(colour), _halve(alpha)
        levels.append(store(colour, alpha))
    return levels
