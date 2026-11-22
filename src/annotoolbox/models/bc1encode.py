"""An in-process BC1 (DXT1) encoder in numpy: the colour blocks of an RGB image, no tool and no process start.

``encode_blocks`` is a principal-axis range fit per 4 x 4 block (the two colours at the ends of the block's main
axis, quantised to 565, the four-colour mode, each texel to its nearest palette colour); good enough for data
textures such as normal maps, not tuned for photographs. ``decode_blocks`` is its inverse for the texels' own check;
``models.ddsdecode`` reads whole files. Blocks run row by row, 8 bytes each, as in a DDS file.
"""

from __future__ import annotations

import numpy as np

from .ddsdecode import expand565


def to_blocks(rgb: np.ndarray) -> np.ndarray:
    """A ``(h, w, 3)`` image (sides multiples of 4) as ``(n, 16, 3)`` texels in block order."""
    height_px, width_px = rgb.shape[:2]
    if height_px % 4 or width_px % 4:
        raise ValueError(f"a BC1 image has sides that are multiples of 4, not {width_px} x {height_px}")
    return rgb.reshape(height_px // 4, 4, width_px // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3)


def from_blocks(blocks: np.ndarray, height_px: int, width_px: int) -> np.ndarray:
    """``to_blocks`` backwards."""
    return (
        blocks.reshape(height_px // 4, width_px // 4, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(height_px, width_px, 3)
    )


def _quantise(rgb: np.ndarray) -> np.ndarray:
    """8-bit RGB (``(n, 3)``) to the nearest RGB565 word."""
    red = np.rint(rgb[:, 0] * 31 / 255).astype(np.int32)
    green = np.rint(rgb[:, 1] * 63 / 255).astype(np.int32)
    blue = np.rint(rgb[:, 2] * 31 / 255).astype(np.int32)
    return (red << 11) | (green << 5) | blue


def _axis(texels: np.ndarray) -> np.ndarray:
    """Each block's main colour axis (``(n, 3)``): power iteration on its covariance."""
    centred = texels - texels.mean(axis=1, keepdims=True)
    covariance = np.einsum("nti,ntj->nij", centred, centred)
    axis = texels.max(axis=1) - texels.min(axis=1) + 1e-3  # the bounding diagonal: a start inside the main lobe
    for _ in range(4):
        axis = np.einsum("nij,nj->ni", covariance, axis)
        axis /= np.linalg.norm(axis, axis=1, keepdims=True) + 1e-9
    return axis


def encode_blocks(blocks: np.ndarray) -> np.ndarray:
    """``(n, 16, 3)`` texels (uint8) to ``(n, 8)`` BC1 bytes."""
    texels = blocks.astype(np.float64)
    along = np.einsum("nti,ni->nt", texels - texels.mean(axis=1, keepdims=True), _axis(texels))
    pick = np.arange(len(texels))
    word_high = _quantise(texels[pick, along.argmax(axis=1)])
    word_low = _quantise(texels[pick, along.argmin(axis=1)])
    color0, color1 = (
        np.maximum(word_high, word_low),
        np.minimum(word_high, word_low),
    )  # c0 > c1 selects the four-colour mode
    expanded0, expanded1 = expand565(color0), expand565(color1)
    palette = np.stack(
        [expanded0, expanded1, (2 * expanded0 + expanded1) // 3, (expanded0 + 2 * expanded1) // 3], axis=1
    )  # (n, 4, 3)
    dist = ((texels[:, :, None, :] - palette[:, None, :, :]) ** 2).sum(axis=3)  # (n, 16, 4)
    index = dist.argmin(axis=2).astype(np.uint32)
    index[color0 == color1] = 0  # one colour: the three-colour mode's first entry
    bits = (index << (2 * np.arange(16, dtype=np.uint32))[None, :]).sum(axis=1, dtype=np.uint32)
    out = np.zeros((len(texels), 8), np.uint8)
    out[:, 0], out[:, 1] = color0 & 255, color0 >> 8
    out[:, 2], out[:, 3] = color1 & 255, color1 >> 8
    out[:, 4:] = bits.astype("<u4").view(np.uint8).reshape(-1, 4)
    return out


def decode_blocks(data: np.ndarray) -> np.ndarray:
    """``(n, 8)`` BC1 bytes to ``(n, 16, 3)`` texels (the opaque modes; a transparent block's fourth colour is black)."""
    color0 = data[:, 0].astype(np.int32) | (data[:, 1].astype(np.int32) << 8)
    color1 = data[:, 2].astype(np.int32) | (data[:, 3].astype(np.int32) << 8)
    expanded0, expanded1 = expand565(color0), expand565(color1)
    four = (color0 > color1)[:, None]
    palette = np.stack(
        [
            expanded0,
            expanded1,
            np.where(four, (2 * expanded0 + expanded1) // 3, (expanded0 + expanded1) // 2),
            np.where(four, (expanded0 + 2 * expanded1) // 3, 0),
        ],
        axis=1,
    )
    bits = np.ascontiguousarray(data[:, 4:]).view("<u4")[:, 0]
    index = ((bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None, :]) & 3).astype(np.intp)
    return np.take_along_axis(palette, index[:, :, None], axis=1).astype(np.uint8)
