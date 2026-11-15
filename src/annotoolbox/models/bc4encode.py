"""An in-process BC4 encoder in numpy and the DDS around it: the format of 117's height maps (vanilla stores them as
``BC4U``, docs/texture-pipeline.md "Derived material maps").

``encode_dds(levels)`` writes the mip levels (``(h, w)`` uint8, level 0 first). Per 4 x 4 block the two ends are the
block's lowest and highest value (the eight-value mode), each texel takes its nearest palette entry; a flat block is
exact. ``models.ddsdecode`` reads the result back.
"""

from __future__ import annotations

import struct

import numpy as np


def _blocks(grey: np.ndarray) -> np.ndarray:
    """``(h, w)`` uint8 as ``(n, 16)`` texels in block order; sides that are no multiple of 4 repeat the edge."""
    h, w = grey.shape
    ph, pw = -(-h // 4) * 4, -(-w // 4) * 4
    if (ph, pw) != (h, w):
        grey = np.pad(grey, ((0, ph - h), (0, pw - w)), mode="edge")
    return grey.reshape(ph // 4, 4, pw // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16)


def encode_level(grey: np.ndarray) -> bytes:
    """One level as BC4 blocks, 8 bytes each, row by row."""
    texels = _blocks(np.asarray(grey, np.uint8)).astype(np.int32)
    hi, lo = texels.max(axis=1), texels.min(axis=1)
    # the decoder's palette for a0 > a1: a0, a1, then (7 - i) a0 + i a1 over 7 (models.ddsdecode)
    palette = np.empty((len(texels), 8), np.int32)
    palette[:, 0], palette[:, 1] = hi, lo
    for i in range(1, 7):
        palette[:, i + 1] = ((7 - i) * hi + i * lo) // 7
    index = np.abs(texels[:, :, None] - palette[:, None, :]).argmin(axis=2).astype(np.uint64)
    index[hi == lo] = 0  # a flat block: both ends equal, every texel the first
    bits = (index << (3 * np.arange(16, dtype=np.uint64))[None, :]).sum(axis=1, dtype=np.uint64)
    out = np.zeros((len(texels), 8), np.uint8)
    out[:, 0], out[:, 1] = hi, lo
    out[:, 2:] = bits.astype("<u8").view(np.uint8).reshape(-1, 8)[:, :6]
    return out.tobytes()


def dds_header(width: int, height: int, mips: int) -> bytes:
    """A legacy DDS header with the ``BC4U`` four-character code, as vanilla's height maps carry."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mips > 1 else 0)
    size = -(-width // 4) * -(-height // 4) * 8
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height, width, size, 1, mips) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"BC4U", 0, 0, 0, 0, 0)
    return head + struct.pack("<5I", 0x1000 | (0x400008 if mips > 1 else 0), 0, 0, 0, 0)


def encode_dds(levels: list[np.ndarray]) -> bytes:
    """A BC4 DDS of the mip levels (level 0 first)."""
    h, w = levels[0].shape[:2]
    return dds_header(w, h, len(levels)) + b"".join(encode_level(level) for level in levels)
