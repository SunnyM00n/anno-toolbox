"""BC1, BC3 and BC5 DDS files in numpy, from the encoders that are there: ``bc1encode`` for the colour blocks,
``bc4encode`` for a single channel (docs/texture-pipeline.md "The built-in encoders"). BC4 is ``bc4encode`` itself and
BC7 ``bc7encode`` / ``bc7simple``.

``encode_dds(levels, kind, srgb)`` writes a DX10 DDS of mip levels (``(h, w, 4)`` uint8, level 0 first; BC5 reads
red and green): BC1 is opaque colour (alpha is dropped), BC3 is BC1 colour plus a BC4 alpha block, BC5 is two BC4 blocks.
Sides that are no multiple of 4 repeat the edge texels.
"""

from __future__ import annotations

import struct

import numpy as np

from . import bc1encode, bc4encode

DXGI = {"BC1": (71, 72), "BC3": (77, 78), "BC5": (83, 83)}  # kind -> (UNORM, sRGB)
BLOCK_BYTES = {"BC1": 8, "BC3": 16, "BC5": 16}


def _padded(rgba: np.ndarray) -> np.ndarray:
    h, w = rgba.shape[:2]
    ph, pw = -(-h // 4) * 4, -(-w // 4) * 4
    return rgba if (ph, pw) == (h, w) else np.pad(rgba, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")


def encode_bc1(rgba: np.ndarray) -> bytes:
    """One level as BC1 blocks (the colour; alpha is dropped)."""
    rgb = _padded(np.asarray(rgba, np.uint8))[..., :3]
    return bc1encode.encode_blocks(bc1encode.to_blocks(rgb)).tobytes()


def encode_bc3(rgba: np.ndarray) -> bytes:
    """One level as BC3 blocks: the alpha's BC4 block, then the colour's BC1 block."""
    px = _padded(np.asarray(rgba, np.uint8))
    alpha = np.frombuffer(bc4encode.encode_level(px[..., 3]), np.uint8).reshape(-1, 8)
    colour = bc1encode.encode_blocks(bc1encode.to_blocks(px[..., :3]))
    return np.concatenate([alpha, colour], axis=1).tobytes()


def encode_bc5(rgba: np.ndarray) -> bytes:
    """One level as BC5 blocks: red's BC4 block, then green's."""
    px = _padded(np.asarray(rgba, np.uint8))
    red = np.frombuffer(bc4encode.encode_level(px[..., 0]), np.uint8).reshape(-1, 8)
    green = np.frombuffer(bc4encode.encode_level(px[..., 1]), np.uint8).reshape(-1, 8)
    return np.concatenate([red, green], axis=1).tobytes()


ENCODERS = {"BC1": encode_bc1, "BC3": encode_bc3, "BC5": encode_bc5}


def dds_header(kind: str, width: int, height: int, mips: int, srgb: bool = False) -> bytes:
    """The DX10 header of a BC1, BC3 or BC5 file (BC5 has no sRGB form)."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mips > 1 else 0)
    size = -(-width // 4) * -(-height // 4) * BLOCK_BYTES[kind]
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height, width, size, 1, mips) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    head += struct.pack("<5I", 0x1000 | (0x400008 if mips > 1 else 0), 0, 0, 0, 0)
    return head + struct.pack("<5I", DXGI[kind][1 if srgb else 0], 3, 0, 1, 0)


def encode_dds(levels: list[np.ndarray], kind: str, srgb: bool = False) -> bytes:
    """A ``kind`` ("BC1", "BC3" or "BC5") DDS of the mip levels."""
    if kind not in ENCODERS:
        raise ValueError(f"no built-in encoder for {kind}: one of {', '.join(ENCODERS)}")
    h, w = levels[0].shape[:2]
    return dds_header(kind, w, h, len(levels), srgb) + b"".join(ENCODERS[kind](level) for level in levels)
