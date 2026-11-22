"""Image files written (and the one old format read) with the standard library only: PNG (8 and 16 bit, grey, grey
and alpha, RGB, RGBA), Radiance HDR (RGBE), OpenEXR (uncompressed scanlines, half or float), TGA (read).

Pixels go in as packed bytes, rows top to bottom: 8-bit samples as they are, 16-bit samples as little-endian
``uint16`` (the PNG writer turns them big-endian), EXR planes as little-endian half or float values. A caller with
numpy packs them with ``tobytes``. Used by ``exporting`` and by the editors' picture exports.
"""

from __future__ import annotations

import math
import struct
import zlib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PNG_COLOUR = {1: 0, 2: 4, 3: 2, 4: 6}  # channels -> PNG colour type (grey, grey+alpha, RGB, RGBA)


class CImageError(ValueError):
    """A picture that cannot be written or read as asked."""


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_bytes(width_px: int, height_px: int, pixels: bytes, channels: int = 4, depth: int = 8, level: int = 6) -> bytes:
    """A PNG file of ``pixels``: ``channels`` 1 to 4, ``depth`` 8 or 16 (16-bit samples little-endian in, big-endian out)."""
    if channels not in PNG_COLOUR or depth not in (8, 16):
        raise CImageError(f"a PNG of {channels} channels at {depth} bits")
    row_bytes = width_px * channels * depth // 8
    if len(pixels) != row_bytes * height_px:
        raise CImageError(f"{len(pixels)} bytes for a {width_px} x {height_px} picture of {row_bytes * height_px}")
    if depth == 16:  # PNG stores 16-bit samples big-endian
        swapped = bytearray(pixels)
        swapped[0::2], swapped[1::2] = pixels[1::2], pixels[0::2]
        pixels = bytes(swapped)
    scanlines = b"".join(b"\0" + pixels[y * row_bytes : (y + 1) * row_bytes] for y in range(height_px))
    head = struct.pack(">IIBBBBB", width_px, height_px, depth, PNG_COLOUR[channels], 0, 0, 0)
    return PNG_MAGIC + _chunk(b"IHDR", head) + _chunk(b"IDAT", zlib.compress(scanlines, level)) + _chunk(b"IEND", b"")


def png_size(png: bytes) -> tuple[int, int]:
    """(width, height) of a PNG file."""
    return struct.unpack_from(">II", png, 16)


def rgbe(red: float, green: float, blue: float) -> bytes:
    """One colour as Radiance's shared-exponent bytes."""
    top = max(red, green, blue)
    if top < 1e-32:
        return bytes(4)
    mantissa, exponent = math.frexp(top)
    scale = mantissa * 256.0 / top
    return bytes((int(red * scale), int(green * scale), int(blue * scale), exponent + 128))


def radiance_bytes(width_px: int, height_px: int, rgbe_pixels: bytes) -> bytes:
    """A Radiance HDR file (flat, uncompressed rows) of ``width_px * height_px`` RGBE pixels (4 bytes each)."""
    if len(rgbe_pixels) != width_px * height_px * 4:
        raise CImageError("the RGBE data does not fit the picture")
    return b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n" + f"-Y {height_px} +X {width_px}\n".encode() + rgbe_pixels


EXR_TYPES = {"half": (1, 2), "float": (2, 4)}  # pixel type -> (EXR code, bytes)


def _attribute(name: str, kind: str, body: bytes) -> bytes:
    return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<I", len(body)) + body


def exr_bytes(width_px: int, height_px: int, planes: dict[str, bytes], pixel_type: str = "float") -> bytes:
    """An OpenEXR file (version 2, one uncompressed scanline a block) of named planes, each ``width_px * height_px`` packed
    little-endian values of ``pixel_type`` ("half" or "float"); channel names as in ``R``, ``G``, ``B``, ``A``, ``Y``."""
    if pixel_type not in EXR_TYPES:
        raise CImageError(f"EXR pixel type {pixel_type!r}")
    code, size = EXR_TYPES[pixel_type]
    names = sorted(planes)
    for channel_name in names:
        if len(planes[channel_name]) != width_px * height_px * size:
            raise CImageError(
                f"EXR channel {channel_name}: {len(planes[channel_name])} bytes for {width_px * height_px * size}"
            )
    chlist = (
        b"".join(channel_name.encode() + b"\0" + struct.pack("<IB3xII", code, 0, 1, 1) for channel_name in names)
        + b"\0"
    )
    box = struct.pack("<4i", 0, 0, width_px - 1, height_px - 1)
    header = b"".join(
        (
            _attribute("channels", "chlist", chlist),
            _attribute("compression", "compression", b"\0"),
            _attribute("dataWindow", "box2i", box),
            _attribute("displayWindow", "box2i", box),
            _attribute("lineOrder", "lineOrder", b"\0"),
            _attribute("pixelAspectRatio", "float", struct.pack("<f", 1.0)),
            _attribute("screenWindowCenter", "v2f", struct.pack("<2f", 0.0, 0.0)),
            _attribute("screenWindowWidth", "float", struct.pack("<f", 1.0)),
        )
    )
    start = 8 + len(header) + 1 + 8 * height_px
    line = width_px * size * len(names)
    blocks = [
        struct.pack("<ii", y, line)
        + b"".join(planes[channel_name][y * width_px * size : (y + 1) * width_px * size] for channel_name in names)
        for y in range(height_px)
    ]
    offsets, offset = [], start
    for block in blocks:
        offsets.append(offset)
        offset += len(block)
    return (
        b"v/1\x01" + struct.pack("<I", 2) + header + b"\0" + struct.pack(f"<{height_px}Q", *offsets) + b"".join(blocks)
    )


def read_tga(data: bytes) -> tuple[int, int, bytes]:
    """A TGA file (uncompressed or run-length, grey, colour-mapped or true colour, 8 to 32 bit) as ``(width, height,
    RGBA bytes)``, rows top to bottom."""
    if len(data) < 18:
        raise CImageError("not a TGA file")
    id_len, map_type, kind, map_first, map_len, map_bits, _origin_x, _origin_y, width, height, bits, descriptor = (
        struct.unpack_from("<BBBHHBHHHHBB", data, 0)
    )
    if kind not in (1, 2, 3, 9, 10, 11) or not width or not height:
        raise CImageError(f"TGA image type {kind}")
    offset = 18 + id_len
    palette: list[tuple[int, int, int, int]] = []
    if map_type:
        step = (map_bits + 7) // 8
        palette = [_tga_pixel(data[offset + i * step : offset + (i + 1) * step], map_bits) for i in range(map_len)]
        offset += map_len * step
    step, count = (bits + 7) // 8, width * height
    if kind >= 9:  # run-length packets
        raw, position = bytearray(), offset
        while len(raw) < count * step and position < len(data):
            head = data[position]
            position += 1
            run_length = (head & 0x7F) + 1
            if head & 0x80:
                raw += data[position : position + step] * run_length
                position += step
            else:
                raw += data[position : position + run_length * step]
                position += run_length * step
        body = bytes(raw)
    else:
        body = data[offset : offset + count * step]
    if len(body) < count * step:
        raise CImageError("the TGA data is cut short")
    out = bytearray()
    for i in range(count):
        pixel_bytes = body[i * step : (i + 1) * step]
        if kind in (1, 9):
            index = int.from_bytes(pixel_bytes, "little") - map_first
            out += bytes(palette[index] if 0 <= index < len(palette) else (0, 0, 0, 255))
        else:
            out += bytes(_tga_pixel(pixel_bytes, bits, grey=kind in (3, 11)))
    scanlines = [bytes(out[y * width * 4 : (y + 1) * width * 4]) for y in range(height)]
    return width, height, b"".join(scanlines if descriptor & 0x20 else reversed(scanlines))


def _tga_pixel(pixel_bytes: bytes, bits: int, grey: bool = False) -> tuple[int, int, int, int]:
    if grey:
        return (pixel_bytes[0], pixel_bytes[0], pixel_bytes[0], pixel_bytes[1] if bits == 16 else 255)
    if bits in (15, 16):
        packed = pixel_bytes[0] | pixel_bytes[1] << 8
        return (((packed >> 10) & 31) * 255 // 31, ((packed >> 5) & 31) * 255 // 31, (packed & 31) * 255 // 31, 255)
    if bits == 24:
        return (pixel_bytes[2], pixel_bytes[1], pixel_bytes[0], 255)
    if bits == 32:
        return (pixel_bytes[2], pixel_bytes[1], pixel_bytes[0], pixel_bytes[3])
    raise CImageError(f"TGA at {bits} bits")
