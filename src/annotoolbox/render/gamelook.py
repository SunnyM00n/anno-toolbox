"""Anno 117's light and colour grade, for the island editor's game look (rendering/lighting.md).

The grade is a 32x32x32 HDR colour cube the game reads from an EXR named by the island's ambient
(``LUTTexturePath``); it is decoded here from the install at run time and never stored. The sun,
the sky light (nine spherical-harmonic coefficients), the sky colours and the exposure are the
Roman daytime values of the spec, section 6, worked out from the ambient's settings by a model of
the game's atmosphere: inferred, not measured in game.
"""

from __future__ import annotations

import re
import struct

import numpy as np

DEFAULT_AMBIENT = "amb_roman_01"
AMBIENT_DIR = "data/base/config/engine/ambientsettings/"
LUT_SIZE = 32

# Roman daytime at the 58-degree cap (rendering/lighting.md 6): linear radiance, game axes
SUN_ELEVATION = 58.0
SUN_DIFFUSE = (4.45, 4.05, 3.47)  # LgtDiffuse = irradiance / pi
SKY_ZENITH = (0.13, 0.33, 0.77)
SKY_HORIZON = (1.26, 2.31, 2.83)
SKY_SH = (  # c0..c8, rgb, the sun 30 degrees from +z towards +x
    (1.920, 2.573, 3.579),
    (-0.847, -0.274, 0.776),
    (0.050, 0.082, 0.127),
    (0.018, 0.022, 0.041),
    (0.028, 0.042, 0.069),
    (0.047, 0.072, 0.119),
    (0.180, 0.376, 0.517),
    (0.020, 0.024, 0.036),
    (0.252, 0.538, 0.750),
)
AMBIENT_FACTOR = 0.7  # the sky light without ray-traced lighting (NoRTAmbientFactor)
EXPOSURE = 0.55


def lut_path(files, ambient: str = DEFAULT_AMBIENT) -> str:
    """The grade the ambient names (``LUTTexturePath``)."""
    text = files.read(f"{AMBIENT_DIR}{ambient}.xml").decode("utf-8", "replace")
    m = re.search(r"<LUTTexturePath>([^<]+)</(?:LUTTexturePath)?>", text)  # the file closes tags with </>
    if not m:
        raise ValueError(f"{ambient}: no LUTTexturePath")
    return m.group(1).strip().replace("\\", "/")


def read_lut(exr: bytes) -> np.ndarray:
    """The colour cube from the game's grade EXR (uncompressed scanlines, half floats): the
    top-left 1024x32 strip, texel (r, g, b) at row g, column b*32 + r. Returns [b][g][r][rgb]
    float32, the order a three.js Data3DTexture takes (x fastest)."""
    if exr[:4] != b"\x76\x2f\x31\x01":
        raise ValueError("not an EXR")
    i, attrs = 8, {}
    while exr[i] != 0:
        n = exr.index(b"\0", i)
        name = exr[i:n].decode()
        m = exr.index(b"\0", n + 1)
        size = struct.unpack_from("<i", exr, m + 1)[0]
        attrs[name] = exr[m + 5 : m + 5 + size]
        i = m + 5 + size
    i += 1
    if attrs.get("compression", b"\0")[0] != 0:
        raise ValueError("compressed EXR")
    x0, y0, x1, y1 = struct.unpack("<4i", attrs["dataWindow"])
    width, height = x1 - x0 + 1, y1 - y0 + 1
    chlist, channels, j = attrs["channels"], [], 0
    while chlist[j] != 0:
        n = chlist.index(b"\0", j)
        channels.append(chlist[j:n].decode())
        j = n + 1 + 16
    offsets = struct.unpack_from(f"<{height}Q", exr, i)
    rows = []
    for y in range(LUT_SIZE):
        o = offsets[y]
        line = np.frombuffer(exr, np.float16, width * len(channels), o + 8).reshape(len(channels), width)
        rows.append(np.stack([line[channels.index(c), : LUT_SIZE * LUT_SIZE] for c in "RGB"], -1))
    strip = np.stack(rows).astype(np.float32)  # [g][b*32 + r][rgb]
    return np.ascontiguousarray(strip.reshape(LUT_SIZE, LUT_SIZE, LUT_SIZE, 3).transpose(1, 0, 2, 3))


def look() -> dict:
    """The light the page sets up: sun, sky light, sky colours, exposure (game axes)."""
    return {
        "sun_elevation": SUN_ELEVATION,
        "sun_diffuse": SUN_DIFFUSE,
        "sky_zenith": SKY_ZENITH,
        "sky_horizon": SKY_HORIZON,
        "sky_sh": SKY_SH,
        "ambient_factor": AMBIENT_FACTOR,
        "exposure": EXPOSURE,
        "lut_size": LUT_SIZE,
    }
