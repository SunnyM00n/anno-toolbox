"""The light of a frame: the sun, ambient, sky, fog and finish of the chosen look - the editor's (its sky and fog
follow the Light / Dark theme, ``viewcolors``), the game's (``render.gamelook``) or 2070's. ``light(opts, size)``
is the entry; ``desktoprender`` hands the answer to its shaders. No Qt.
"""

from __future__ import annotations

import math

import numpy as np

from . import viewcolors

SUN_EDITOR = np.array([120.0, 200.0, -80.0]) / np.linalg.norm([120.0, 200.0, -80.0])
LOOK2070 = {
    "sun": (1.507, 1.235, 1.021),
    "ambient": (0.612, 0.772, 0.788),
    "elevation": 47.9,
    "zenith": (0.246, 0.464, 0.594),
    "horizon": (0.672, 1.301, 1.483),
    "fall_off": 9.792,
    "mood": (0.877, 0.867, 0.819, 0.2),
}
# The look seen under water, all in gamma-space colours and world units. Light falls off per channel with
# (1 - depth)^exponent, depth = max(-y / 36, distance / 72); the haze takes over with (distance / 57.2)^2.52 and
# has the colour of the light from above (``light`` x a view-dependent factor, see ``shaders.LIGHT``).
UNDERWATER = {"light": (3.5, 3.5, 3.5)}


def srgb_hex(packed: int) -> tuple[float, float, float]:
    """A colour as written (#rrggbb) in linear light."""
    return tuple((((packed >> k) & 255) / 255.0) ** 2.2 for k in (16, 8, 0))


def lin(colour) -> tuple:
    return tuple(float(component) ** 2.2 for component in colour)


def light(options, size_tiles: float) -> dict:
    """The sun, ambient, sky, fog and frame finish of the look ``opts`` (``SViewOptions``) chooses, for an island
    ``size_tiles`` tiles wide."""
    flat = np.array([SUN_EDITOR[0], 0, SUN_EDITOR[2]])
    flat /= np.linalg.norm(flat)
    if options.look2070 or options.underwater:
        elevation = math.radians(LOOK2070["elevation"])
        amb, sun = lin(LOOK2070["ambient"]), lin(LOOK2070["sun"])
        under = {"uwLight": UNDERWATER["light"], "uwCaustics": float(options.caustics)} if options.underwater else {}
        return {
            **under,
            "sunDir": (flat[0] * math.cos(elevation), math.sin(elevation), flat[2] * math.cos(elevation)),
            "sunColor": tuple(
                sun_channel - ambient_channel for sun_channel, ambient_channel in zip(sun, amb, strict=True)
            ),
            "ambientMode": 2,
            "flatAmbient": amb,
            "sky": (2, lin(LOOK2070["zenith"]), lin(LOOK2070["horizon"]), LOOK2070["fall_off"]),
            "fogColor": lin(LOOK2070["horizon"]),
            "finish": 2,
            "exposure": 1.0,
        }
    if options.game_light:
        from . import gamelook

        look = gamelook.look()
        elevation = math.radians(look["sun_elevation"])
        harmonics = [
            tuple(-value for value in row) if k in (3, 4, 7) else tuple(row) for k, row in enumerate(look["sky_sh"])
        ]
        return {
            "sunDir": (flat[0] * math.cos(elevation), math.sin(elevation), flat[2] * math.cos(elevation)),
            "sunColor": tuple(look["sun_diffuse"]),
            "ambientMode": 1,
            "sh": harmonics,
            "shFactor": look["ambient_factor"],
            "sky": (1, tuple(look["sky_zenith"]), tuple(look["sky_horizon"]), 3.0),
            "fogColor": tuple(look["sky_horizon"]),
            "finish": 1,
            "exposure": look["exposure"],
        }
    colors = viewcolors.palette(options.scheme)
    return {
        "sunDir": tuple(SUN_EDITOR),
        "sunColor": tuple(channel * 2.6 / math.pi for channel in srgb_hex(0xFFF0D8)),
        "ambientMode": 0,
        "hemiSky": srgb_hex(0xDFEFFF),
        "hemiGround": srgb_hex(0x4A4036),
        "envLight": 0.4,  # a room-like environment light: models take it whole, the ground a third
        "envGround": 0.35,
        "sky": (0, lin(colors["sky_top"]), lin(colors["sky_horizon"]), 1.0),
        "skyBelow": lin(colors["sky_below"]),
        "fogColor": lin(colors["fog"]),
        "finish": 0,
        "exposure": 1.05,
        "fog": (size_tiles * 1.6, size_tiles * 4),
    }
