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
    match = re.search(r"<LUTTexturePath>([^<]+)</(?:LUTTexturePath)?>", text)  # the file closes tags with </>
    if not match:
        raise ValueError(f"{ambient}: no LUTTexturePath")
    return match.group(1).strip().replace("\\", "/")


class CExrError(ValueError):
    """A grade EXR that cannot be read: not an EXR, compressed, not half floats, too small or cut short."""


EXR_MAGIC = b"\x76\x2f\x31\x01"
EXR_HALF = 1  # a channel's pixel type: half float


def read_lut(exr: bytes) -> np.ndarray:
    """The colour cube from the game's grade EXR (uncompressed scanlines, half floats): the
    top-left 1024x32 strip, texel (r, g, b) at row g, column b*32 + r. Returns [b][g][r][rgb]
    float32, the order a three.js Data3DTexture takes (x fastest). Raises ``CExrError`` on a file it cannot read."""
    try:
        return _read_strip(exr)
    except CExrError:
        raise
    except (IndexError, KeyError, ValueError, struct.error) as exc:  # a cut or damaged header, table or line
        raise CExrError(f"the EXR is cut short or damaged ({type(exc).__name__}: {exc})") from exc


def _read_strip(exr: bytes) -> np.ndarray:
    if exr[:4] != EXR_MAGIC:
        raise CExrError("not an EXR")
    i, attributes = 8, {}
    while exr[i] != 0:
        name_end = exr.index(b"\0", i)
        name = exr[i:name_end].decode()
        type_end = exr.index(b"\0", name_end + 1)
        size = struct.unpack_from("<i", exr, type_end + 1)[0]
        attributes[name] = exr[type_end + 5 : type_end + 5 + size]
        i = type_end + 5 + size
    i += 1
    if attributes.get("compression", b"\0")[0] != 0:
        raise CExrError("compressed EXR")
    min_x, min_y, max_x, max_y = struct.unpack("<4i", attributes["dataWindow"])
    width, height = max_x - min_x + 1, max_y - min_y + 1
    if width < LUT_SIZE * LUT_SIZE or height < LUT_SIZE or height * 8 > len(exr):
        raise CExrError(f"a {width} x {height} EXR holds no {LUT_SIZE * LUT_SIZE} x {LUT_SIZE} strip")
    chlist, channels, j = attributes["channels"], [], 0
    while chlist[j] != 0:
        name_end = chlist.index(b"\0", j)
        if struct.unpack_from("<i", chlist, name_end + 1)[0] != EXR_HALF:
            raise CExrError("the EXR's channels are not half floats")
        channels.append(chlist[j:name_end].decode())
        j = name_end + 1 + 16
    offsets = struct.unpack_from(f"<{height}Q", exr, i)
    rows = []
    for y in range(LUT_SIZE):
        offset = offsets[y]
        line = np.frombuffer(exr, np.float16, width * len(channels), offset + 8).reshape(len(channels), width)
        rows.append(np.stack([line[channels.index(channel_name), : LUT_SIZE * LUT_SIZE] for channel_name in "RGB"], -1))
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
