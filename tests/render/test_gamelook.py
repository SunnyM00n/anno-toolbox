"""The game's colour grade read from its EXR strips (``render.gamelook``)."""

import struct

import numpy as np
import pytest

from annotoolbox.render import gamelook


def _exr(width: int, height: int, pixel) -> bytes:
    """A minimal uncompressed scanline EXR with half-float A, B, G, R channels (as the game's grade)."""

    def attr(name, typ, value):
        return name.encode() + b"\0" + typ.encode() + b"\0" + struct.pack("<i", len(value)) + value

    chans = (
        b"".join(c.encode() + b"\0" + struct.pack("<i", 1) + bytes(4) + struct.pack("<ii", 1, 1) for c in "ABGR")
        + b"\0"
    )
    header = (
        b"\x76\x2f\x31\x01" + struct.pack("<i", 2)
        + attr("channels", "chlist", chans)
        + attr("compression", "compression", b"\0")
        + attr("dataWindow", "box2i", struct.pack("<4i", 0, 0, width - 1, height - 1))
        + b"\0"
    )  # fmt: skip
    lines = []
    for y in range(height):
        values = np.array([[pixel(x, y)[c] for x in range(width)] for c in (3, 2, 1, 0)], np.float16)  # A B G R
        lines.append(struct.pack("<ii", y, values.nbytes) + values.tobytes())
    start = len(header) + 8 * height
    offsets, at = [], start
    for line in lines:
        offsets.append(at)
        at += len(line)
    return header + struct.pack(f"<{height}Q", *offsets) + b"".join(lines)


def test_lut_strip_becomes_a_cube():
    """Texel (r, g, b) of the cube sits at row g, column b*32 + r of the strip."""

    def pixel(x, y):  # encode the coordinates themselves: r = x % 32, b = x // 32, g = y
        return (x % 32, y, x // 32, 1.0)

    lut = gamelook.read_lut(_exr(1024 + 8, 40, pixel))
    assert lut.shape == (32, 32, 32, 3)  # [b][g][r]
    assert tuple(lut[5, 7, 9]) == (9.0, 7.0, 5.0)


def test_a_cut_or_damaged_grade_raises_the_readers_own_error():
    def flat(x, y):
        return (0.0, 0.0, 0.0, 1.0)

    good = _exr(1024, 32, flat)
    bad_files = (b"", b"\x76\x2f\x31\x01", good[:40], good[:200], good[:-10], b"notanexr" * 8, _exr(512, 32, flat),
                 _exr(1024, 8, flat))  # fmt: skip
    for bad in bad_files:
        with pytest.raises(gamelook.CExrError):
            gamelook.read_lut(bad)
    assert issubclass(gamelook.CExrError, ValueError)  # callers that caught ValueError still do
