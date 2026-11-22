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
    height_px, width_px = grey.shape
    padded_height_px, padded_width_px = -(-height_px // 4) * 4, -(-width_px // 4) * 4
    if (padded_height_px, padded_width_px) != (height_px, width_px):
        grey = np.pad(grey, ((0, padded_height_px - height_px), (0, padded_width_px - width_px)), mode="edge")
    return grey.reshape(padded_height_px // 4, 4, padded_width_px // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16)


def encode_level(grey: np.ndarray) -> bytes:
    """One level as BC4 blocks, 8 bytes each, row by row."""
    texels = _blocks(np.asarray(grey, np.uint8)).astype(np.int32)
    endpoint0, endpoint1 = texels.max(axis=1), texels.min(axis=1)
    # the decoder's palette for a0 > a1: a0, a1, then (7 - i) a0 + i a1 over 7 (models.ddsdecode)
    palette = np.empty((len(texels), 8), np.int32)
    palette[:, 0], palette[:, 1] = endpoint0, endpoint1
    for i in range(1, 7):
        palette[:, i + 1] = ((7 - i) * endpoint0 + i * endpoint1) // 7
    index = np.abs(texels[:, :, None] - palette[:, None, :]).argmin(axis=2).astype(np.uint64)
    index[endpoint0 == endpoint1] = 0  # a flat block: both ends equal, every texel the first
    bits = (index << (3 * np.arange(16, dtype=np.uint64))[None, :]).sum(axis=1, dtype=np.uint64)
    out = np.zeros((len(texels), 8), np.uint8)
    out[:, 0], out[:, 1] = endpoint0, endpoint1
    out[:, 2:] = bits.astype("<u8").view(np.uint8).reshape(-1, 8)[:, :6]
    return out.tobytes()


DXGI_BC4_UNORM = 80


def dds_header(width_px: int, height_px: int, mips: int, dx10: bool = False) -> bytes:
    """A DDS header for BC4: the legacy ``BC4U`` four-character code, as vanilla's height maps carry, or with ``dx10``
    the DX10 header and BC4_UNORM that texconv writes."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mips > 1 else 0)
    linear_size_bytes = -(-width_px // 4) * -(-height_px // 4) * 8
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height_px, width_px, linear_size_bytes, 1, mips) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DX10" if dx10 else b"BC4U", 0, 0, 0, 0, 0)
    head += struct.pack("<5I", 0x1000 | (0x400008 if mips > 1 else 0), 0, 0, 0, 0)
    return head + (struct.pack("<5I", DXGI_BC4_UNORM, 3, 0, 1, 0) if dx10 else b"")


def encode_dds(levels: list[np.ndarray], dx10: bool = False) -> bytes:
    """A BC4 DDS of the mip levels (level 0 first)."""
    height_px, width_px = levels[0].shape[:2]
    return dds_header(width_px, height_px, len(levels), dx10) + b"".join(encode_level(level) for level in levels)
