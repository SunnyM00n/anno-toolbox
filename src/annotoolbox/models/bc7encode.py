"""The in-process BC7 encoder and the DDS around it (docs/texture-pipeline.md).

The encoder is ``etcpak`` (MIT, Python binding of Rich Geldreich's bc7e, an ISPC encoder; PyPI, maintained): a
native call per image, single-threaded, no GPU, no process start. The same input and settings give the same bytes
on every run. ``identity()`` names the encoder, its binary and its settings for the cache keys.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from ..infra import buildcache

DXGI_BC7, DXGI_BC7_SRGB = 98, 99
UBER_LEVEL = 1  # bc7e effort 0..4; measured in docs/texture-pipeline.md ("Quality and speed")
SETTINGS_VERSION = 1  # bumped when the settings, the padding or the DDS writer change what comes out


def _module():
    try:
        import etcpak
    except ImportError:
        return None
    return etcpak if hasattr(etcpak, "compress_bc7") and hasattr(etcpak, "BC7CompressBlockParams") else None


def available() -> bool:
    return _module() is not None


def identity() -> str:
    """The encoder, the hash of its native binary and the settings: what decides the bytes it writes."""
    etcpak = _module()
    if etcpak is None:
        return "none"
    binaries = sorted(Path(etcpak.__file__).parent.glob("*.pyd")) + sorted(Path(etcpak.__file__).parent.glob("*.so"))
    binary = buildcache.file_digest(binaries[0])[:16] if binaries else "?"
    return f"etcpak-{getattr(etcpak, '__version__', '?')}-{binary}-u{UBER_LEVEL}-s{SETTINGS_VERSION}"


def _params(perceptual: bool):
    params = _module().BC7CompressBlockParams()
    params.m_uber_level = UBER_LEVEL
    params.init_perceptual_weights() if perceptual else params.init_linear_weights()
    return params


def encode_level(rgba: np.ndarray, perceptual: bool) -> bytes:
    """One mip level (``(h, w, 4)`` uint8) as BC7 blocks; sizes that are no multiple of 4 repeat the edge texels."""
    height_px, width_px = rgba.shape[:2]
    padded_height_px, padded_width_px = -(-height_px // 4) * 4, -(-width_px // 4) * 4
    if (padded_height_px, padded_width_px) != (height_px, width_px):
        rgba = np.pad(rgba, ((0, padded_height_px - height_px), (0, padded_width_px - width_px), (0, 0)), mode="edge")
    return bytes(
        _module().compress_bc7(
            np.ascontiguousarray(rgba).tobytes(), padded_width_px, padded_height_px, _params(perceptual)
        )
    )


def dds_header(width_px: int, height_px: int, mip_count: int, is_srgb: bool) -> bytes:
    """The header texconv writes for a BC7 file: a DX10 header, the top level's byte size as the linear size."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mip_count > 1 else 0)
    size = -(-width_px // 4) * -(-height_px // 4) * 16
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height_px, width_px, size, 1, mip_count) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    head += struct.pack("<5I", 0x1000 | (0x400008 if mip_count > 1 else 0), 0, 0, 0, 0)
    return head + struct.pack("<5I", DXGI_BC7_SRGB if is_srgb else DXGI_BC7, 3, 0, 1, 0)


def encode_dds(levels: list[np.ndarray], is_srgb: bool, perceptual: bool) -> bytes:
    """A BC7 DDS of the mip levels (level 0 first)."""
    height_px, width_px = levels[0].shape[:2]
    return dds_header(width_px, height_px, len(levels), is_srgb) + b"".join(
        encode_level(level, perceptual) for level in levels
    )


def solid_block(rgba: tuple[int, int, int, int]) -> bytes | None:
    """The BC7 block (mode 5: 7-bit colour, 8-bit alpha) that decodes to exactly ``rgba`` everywhere, or None when a
    colour channel has no 7-bit form. The encoder's own mode 6 shares one parity bit over all four channels and
    reads back opaque black as alpha 254."""
    seven = [channel >> 1 for channel in rgba[:3]]
    if any((seven_bit << 1 | seven_bit >> 6) != channel for seven_bit, channel in zip(seven, rgba[:3], strict=True)):
        return None
    bits = 1 << 5  # mode 5, no rotation
    for i, seven_bit in enumerate(seven):
        bits |= (seven_bit | seven_bit << 7) << (8 + 14 * i)  # both endpoints of the channel
    bits |= (rgba[3] | rgba[3] << 8) << 50  # both alpha endpoints; every index 0
    return bits.to_bytes(16, "little")


def solid_dds(rgba: tuple[int, int, int, int], size_px: int = 4) -> bytes | None:
    """A one-colour BC7 texture of ``size_px`` squared with its mip chain, or None (see ``solid_block``)."""
    block = solid_block(rgba)
    if block is None:
        return None
    mip_count = size_px.bit_length()
    return dds_header(size_px, size_px, mip_count, False) + block * sum(
        max(1, -(-(size_px >> mip) // 4)) ** 2 for mip in range(mip_count)
    )
