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


class ImageError(ValueError):
    """A picture that cannot be written or read as asked."""


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png_bytes(width: int, height: int, pixels: bytes, channels: int = 4, depth: int = 8, level: int = 6) -> bytes:
    """A PNG file of ``pixels``: ``channels`` 1 to 4, ``depth`` 8 or 16 (16-bit samples little-endian in, big-endian out)."""
    if channels not in PNG_COLOUR or depth not in (8, 16):
        raise ImageError(f"a PNG of {channels} channels at {depth} bits")
    row = width * channels * depth // 8
    if len(pixels) != row * height:
        raise ImageError(f"{len(pixels)} bytes for a {width} x {height} picture of {row * height}")
    if depth == 16:  # PNG stores 16-bit samples big-endian
        swapped = bytearray(pixels)
        swapped[0::2], swapped[1::2] = pixels[1::2], pixels[0::2]
        pixels = bytes(swapped)
    rows = b"".join(b"\0" + pixels[y * row : (y + 1) * row] for y in range(height))
    head = struct.pack(">IIBBBBB", width, height, depth, PNG_COLOUR[channels], 0, 0, 0)
    return PNG_MAGIC + _chunk(b"IHDR", head) + _chunk(b"IDAT", zlib.compress(rows, level)) + _chunk(b"IEND", b"")


def png_size(png: bytes) -> tuple[int, int]:
    """(width, height) of a PNG file."""
    return struct.unpack_from(">II", png, 16)


def rgbe(r: float, g: float, b: float) -> bytes:
    """One colour as Radiance's shared-exponent bytes."""
    top = max(r, g, b)
    if top < 1e-32:
        return bytes(4)
    mantissa, exponent = math.frexp(top)
    scale = mantissa * 256.0 / top
    return bytes((int(r * scale), int(g * scale), int(b * scale), exponent + 128))


def radiance_bytes(width: int, height: int, rgbe_pixels: bytes) -> bytes:
    """A Radiance HDR file (flat, uncompressed rows) of ``width * height`` RGBE pixels (4 bytes each)."""
    if len(rgbe_pixels) != width * height * 4:
        raise ImageError("the RGBE data does not fit the picture")
    return b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n" + f"-Y {height} +X {width}\n".encode() + rgbe_pixels


def radiance_from_floats(width: int, height: int, rgb: bytes) -> bytes:
    """A Radiance HDR file from packed little-endian float32 RGB (3 values a pixel)."""
    values = struct.unpack(f"<{width * height * 3}f", rgb)
    return radiance_bytes(width, height, b"".join(rgbe(*values[i : i + 3]) for i in range(0, len(values), 3)))


EXR_TYPES = {"half": (1, 2), "float": (2, 4)}  # pixel type -> (EXR code, bytes)


def _attribute(name: str, kind: str, body: bytes) -> bytes:
    return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<I", len(body)) + body


def exr_bytes(width: int, height: int, planes: dict[str, bytes], pixel_type: str = "float") -> bytes:
    """An OpenEXR file (version 2, one uncompressed scanline a block) of named planes, each ``width * height`` packed
    little-endian values of ``pixel_type`` ("half" or "float"); channel names as in ``R``, ``G``, ``B``, ``A``, ``Y``."""
    if pixel_type not in EXR_TYPES:
        raise ImageError(f"EXR pixel type {pixel_type!r}")
    code, size = EXR_TYPES[pixel_type]
    names = sorted(planes)
    for n in names:
        if len(planes[n]) != width * height * size:
            raise ImageError(f"EXR channel {n}: {len(planes[n])} bytes for {width * height * size}")
    chlist = b"".join(n.encode() + b"\0" + struct.pack("<IB3xII", code, 0, 1, 1) for n in names) + b"\0"
    box = struct.pack("<4i", 0, 0, width - 1, height - 1)
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
    start = 8 + len(header) + 1 + 8 * height
    line = width * size * len(names)
    blocks = [
        struct.pack("<ii", y, line) + b"".join(planes[n][y * width * size : (y + 1) * width * size] for n in names)
        for y in range(height)
    ]
    offsets, at = [], start
    for block in blocks:
        offsets.append(at)
        at += len(block)
    return b"v/1\x01" + struct.pack("<I", 2) + header + b"\0" + struct.pack(f"<{height}Q", *offsets) + b"".join(blocks)


def read_tga(data: bytes) -> tuple[int, int, bytes]:
    """A TGA file (uncompressed or run-length, grey, colour-mapped or true colour, 8 to 32 bit) as ``(width, height,
    RGBA bytes)``, rows top to bottom."""
    if len(data) < 18:
        raise ImageError("not a TGA file")
    id_len, map_type, kind, map_first, map_len, map_bits, _x, _y, width, height, bits, descriptor = struct.unpack_from(
        "<BBBHHBHHHHBB", data, 0
    )
    if kind not in (1, 2, 3, 9, 10, 11) or not width or not height:
        raise ImageError(f"TGA image type {kind}")
    at = 18 + id_len
    palette: list[tuple[int, int, int, int]] = []
    if map_type:
        step = (map_bits + 7) // 8
        palette = [_tga_pixel(data[at + i * step : at + (i + 1) * step], map_bits) for i in range(map_len)]
        at += map_len * step
    step, count = (bits + 7) // 8, width * height
    if kind >= 9:  # run-length packets
        raw, pos = bytearray(), at
        while len(raw) < count * step and pos < len(data):
            head = data[pos]
            pos += 1
            n = (head & 0x7F) + 1
            if head & 0x80:
                raw += data[pos : pos + step] * n
                pos += step
            else:
                raw += data[pos : pos + n * step]
                pos += n * step
        body = bytes(raw)
    else:
        body = data[at : at + count * step]
    if len(body) < count * step:
        raise ImageError("the TGA data is cut short")
    out = bytearray()
    for i in range(count):
        px = body[i * step : (i + 1) * step]
        if kind in (1, 9):
            index = int.from_bytes(px, "little") - map_first
            out += bytes(palette[index] if 0 <= index < len(palette) else (0, 0, 0, 255))
        else:
            out += bytes(_tga_pixel(px, bits, grey=kind in (3, 11)))
    rows = [bytes(out[y * width * 4 : (y + 1) * width * 4]) for y in range(height)]
    return width, height, b"".join(rows if descriptor & 0x20 else reversed(rows))


def _tga_pixel(px: bytes, bits: int, grey: bool = False) -> tuple[int, int, int, int]:
    if grey:
        return (px[0], px[0], px[0], px[1] if bits == 16 else 255)
    if bits in (15, 16):
        v = px[0] | px[1] << 8
        return (((v >> 10) & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31, (v & 31) * 255 // 31, 255)
    if bits == 24:
        return (px[2], px[1], px[0], 255)
    if bits == 32:
        return (px[2], px[1], px[0], px[3])
    raise ImageError(f"TGA at {bits} bits")
