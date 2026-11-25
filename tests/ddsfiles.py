"""DDS texture files for the tests: DXT1, BC7 and a DX10 header."""

from __future__ import annotations

import struct


def make_dxt1_dds(width: int = 4, height: int = 4) -> bytes:
    """A legacy DXT1 file (one red block repeated), the kind 2070 ships."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height, width, 8, 0, 1) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DXT1", 0, 0, 0, 0, 0) + struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    return head + struct.pack("<HHI", 0xF800, 0, 0) * (-(-width // 4) * -(-height // 4))


def make_bc7_dds(width: int, height: int, mip_count: int = 1, is_srgb: bool = False) -> bytes:
    """A DX10 BC7 header (UNORM, or UNORM_SRGB) with a stand-in payload: the layout of 117's UI textures."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mip_count > 1 else 0)
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height, width, 0, 0, mip_count) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    head += struct.pack("<5I", 0x1000 | (0x400008 if mip_count > 1 else 0), 0, 0, 0, 0)
    head += struct.pack("<5I", 99 if is_srgb else 98, 3, 0, 1, 0)
    return head + bytes(16)


def header(
    width: int, height: int, mips: int, *, fourcc: bytes = b"DX10", cube: bool = False, rgb_bits: int = 0
) -> bytes:
    """A DDS header (and the DX10 one is the caller's); ``rgb_bits``: an uncompressed BGRA layout of that many bits."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000
    pf = struct.pack("<II4s5I", 32, 0x40 | 0x1 if rgb_bits else 0x4, b"" if rgb_bits else fourcc, rgb_bits,
                     0xFF0000, 0xFF00, 0xFF, 0xFF000000)  # fmt: skip
    caps2 = 0xFE00 if cube else 0
    body = struct.pack("<7I", flags, height, width, 0, 0, mips, 0) + b"\0" * 40 + pf
    return b"DDS " + struct.pack("<I", 124) + body + struct.pack("<4I", 0x1000, caps2, 0, 0) + b"\0" * 4


def dx10(code: int, array: int = 1, cube: bool = False) -> bytes:
    return struct.pack("<5I", code, 3, 0x4 if cube else 0, array, 0)


def dxt5_flat_file() -> bytes:
    """A 4x4 DXT5 file of one flat colour whose flags (0x000a1007) hold the byte a text-mode copy turns into two."""
    head = b"DDS " + struct.pack("<7I", 124, 0x000A1007, 4, 4, 16, 0, 0) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DXT5", 0, 0, 0, 0, 0) + struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    return head + bytes([255, 255]) + bytes(6) + struct.pack("<HH", 0x8400, 0x7BE0) + b"\xaa" * 4
