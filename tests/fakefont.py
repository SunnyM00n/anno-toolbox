"""A tiny TrueType font a test makes itself (no font of a game or of the system is needed): one square glyph for "A"."""

from __future__ import annotations

import struct


def _pad(data: bytes) -> bytes:
    return data + b"\x00" * (-len(data) % 4)


def _name(family: str) -> bytes:
    records = [(1, family), (2, "Regular"), (4, family), (6, family.replace(" ", ""))]
    head, strings = b"", b""
    for ident, text in records:
        encoded = text.encode("utf-16-be")
        head += struct.pack(">6H", 3, 1, 0x409, ident, len(encoded), len(strings))
        strings += encoded
    return struct.pack(">3H", 0, len(records), 6 + len(head)) + head + strings


def _cmap() -> bytes:
    """Format 4: the character A is glyph 1, the closing segment glyph 0."""
    sub = struct.pack(
        ">7H", 4, 32, 0, 4, 4, 1, 0
    )  # format, length, language, segCountX2, search range, selector, shift
    sub += struct.pack(">2H", 0x41, 0xFFFF) + struct.pack(">H", 0) + struct.pack(">2H", 0x41, 0xFFFF)
    sub += struct.pack(">2h", -0x40, 1) + struct.pack(">2H", 0, 0)
    return struct.pack(">2H", 0, 1) + struct.pack(">HHI", 3, 1, 12) + sub


def make_ttf(family: str = "Fake Test Face") -> bytes:
    """The bytes of a font named ``family``: an empty .notdef and a 500-unit square for "A"."""
    square = struct.pack(">5h", 1, 0, 0, 500, 500) + struct.pack(">HH", 3, 0) + bytes([1, 1, 1, 1])
    square += struct.pack(">4h", 0, 500, 0, -500) + struct.pack(">4h", 0, 0, 500, 0)
    glyf = _pad(square)
    tables = {
        "cmap": _cmap(),
        "glyf": glyf,
        "head": struct.pack(
            ">4I2H2q4h2H3h", 0x10000, 0x10000, 0, 0x5F0F3CF5, 0, 1000, 0, 0, 0, 0, 500, 500, 0, 8, 2, 0, 0
        ),
        "hhea": struct.pack(">I3hH11hH", 0x10000, 800, -200, 0, 600, 0, 0, 500, 1, 0, 0, 0, 0, 0, 0, 0, 2),
        "hmtx": struct.pack(">HhHh", 600, 0, 600, 0),
        "loca": struct.pack(">3H", 0, 0, len(glyf) // 2),
        "maxp": struct.pack(">I14H", 0x10000, 2, 4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0),
        "name": _name(family),
        "post": struct.pack(">IIhhIIIII", 0x30000, 0, 0, 0, 0, 0, 0, 0, 0),
    }
    offset = 12 + 16 * len(tables)
    directory, blobs = b"", b""
    for tag in sorted(tables):  # the directory is sorted by tag
        directory += tag.encode().ljust(4) + struct.pack(">3I", 0, offset + len(blobs), len(tables[tag]))
        blobs += _pad(tables[tag])
    return struct.pack(">I4H", 0x10000, len(tables), 128, 3, len(tables) * 16 - 128) + directory + blobs
