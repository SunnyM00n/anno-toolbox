"""The 3D view's own colours in the Light and the Dark theme: the editor look's sky and fog, the empty view, and
the overlays drawn over the island (brush ring, selection, the gizmo's active handle). No Qt, so the renderer
and ``scripts/editor_shots.py`` use it without a window; the window picks the scheme (``suite.theme.scheme``).

The game-look modes (the game's light, 2070's look) keep the game's colours whatever the theme; so do the
colours that say what a thing is - the gizmo's axes, the markers' kinds, the water tool's lines.
(docs/island-editor-view.md "The 3D view")
"""

from __future__ import annotations

MARKER_COLORS = {
    "PositionMarker": 0xFF4FA3,
    "AudioSpots": 0x4FD1FF,
    "Herd": 0xC58B3A,
    "Flock": 0x3A7DC5,
    "DummySequenceAsset": 0xC54FFF,
    "StaticBlockingObject": 0xFF5A36,
    "FeedbackGridModifier": 0xFFD23F,
    "MapExitBlocker": 0x9A9A9A,
}  # the markers' kinds, as #rrggbb

# sRGB 0-1. "light" is the view's pale sky as it always was, "dark" a slate one that sits in the dark window; the
# light selection is the deeper accent, which the pale sky needs.
PALETTES = {
    "light": {
        "sky_top": (0.239, 0.463, 0.722),
        "sky_horizon": (0.725, 0.827, 0.890),
        "sky_below": (0.812, 0.867, 0.902),
        "fog": (0.725, 0.827, 0.890),
        "clear": (0.55, 0.70, 0.85),
        "select": (0.86, 0.42, 0.04),  # the accent, deep enough to read on the pale sky
        "brush": (0.86, 0.42, 0.04),
    },
    "dark": {
        "sky_top": (0.075, 0.095, 0.130),
        "sky_horizon": (0.215, 0.255, 0.315),
        "sky_below": (0.170, 0.200, 0.250),
        "fog": (0.215, 0.255, 0.315),
        "clear": (0.11, 0.13, 0.17),
        "select": (1.0, 0.85, 0.25),
        "brush": (1.0, 0.82, 0.25),
    },
}


def palette(scheme: str) -> dict[str, tuple[float, float, float]]:
    """The colours of ``light`` or ``dark`` (any other name is the light ones)."""
    return PALETTES.get(scheme, PALETTES["light"])


def missing_texture(ctx, kept: dict):
    """The magenta and black checker a view draws where a colour map is missing (``fileresolve.MISSING_RGBA``),
    made once on ``ctx`` and kept in ``kept`` (the view's textures) under ``"missing"``."""
    if "missing" not in kept:
        from ..models.fileresolve import MISSING_RGBA  # numpy is an optional extra: the palettes load without it

        texture = ctx.texture((MISSING_RGBA.shape[1], MISSING_RGBA.shape[0]), 4, MISSING_RGBA.tobytes())
        texture.filter = (ctx.NEAREST, ctx.NEAREST)
        texture.repeat_x = texture.repeat_y = True
        kept["missing"] = texture
    return kept["missing"]
