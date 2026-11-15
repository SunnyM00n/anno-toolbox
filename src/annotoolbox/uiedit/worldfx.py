"""The effects that read the 3D scene or the island's distance picture, drawn as the game's UI shader draws them:
the background blur and the world darken (the widget's picture says where the blurred, darkened world shows through)
and the island shading (hatching by the distance to the coast, a border, a contrast).

``shade(item, picture, scene, frame)`` gives the item and the picture to draw in place of a widget whose effects
include one of ``effects.WORLD``. docs/ui/117/world-effects.md is the rule; what is inferred or a stand-in is said there.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from ..models import mipchain
from . import effects
from .layout import DrawItem, Matrix
from .pictures import Picture
from .worldscene import Scene

MAX_SIDE = 2048  # the most a world picture is made at, per side
KEPT = 48  # pictures kept
DEFAULT_INK, DEFAULT_PAPER = (0.14, 0.12, 0.10), (0.84, 0.78, 0.66)  # island colours the game's bindings would give
HATCH_LINES = (24, 32, 44, 60, 80, 110)  # a stand-in for the two hatch pictures: lines across a unit, per layer
HATCH_WIDTH = (0.20, 0.24, 0.30, 0.36, 0.44, 0.54)  # and how much of each period the line covers
SCENE_ONLY = (
    "blur",
    "whiten",
    "glow",
    "drop_shadow",
    "distance_field",
    "scroll",
)  # the picture's own branch of the shader
_made: dict[tuple, Picture] = {}


def shade(
    item: DrawItem, picture: Picture | None, scene: Scene, frame: tuple[float, float]
) -> tuple[DrawItem, Picture | None]:
    """The item and picture standing in for a widget with a scene effect (the picture alone when it has none)."""
    fx = item.effects
    if "background_blur" in fx or "world_darken" in fx:
        kept = {k: v for k, v in fx.items() if k not in effects.WORLD and k not in SCENE_ONLY}
        flat = replace(item, color=(255, 255, 255), alpha=1.0, nine=None, uv=(0.0, 0.0, 1.0, 1.0), effects=kept)
        return flat, world_picture(item, picture, scene, frame)
    if "island_shading" in fx and picture is not None:
        kept = {k: v for k, v in fx.items() if k not in effects.WORLD and k not in SCENE_ONLY}
        flat = replace(item, color=(255, 255, 255), alpha=1.0, effects=kept)
        return flat, island_picture(item, picture)
    return item, picture


def _remember(key: tuple, make) -> Picture:
    if key not in _made:
        if len(_made) > KEPT:
            _made.clear()
        made = make()
        made.key = f"{made.key}|{abs(hash(key)):x}"  # the key tells two pictures of one size apart
        _made[key] = made
    return _made[key]


def _grid(matrix: Matrix, size: tuple[float, float]) -> tuple[int, int]:
    """The pixels a widget covers in the reference space, per side, at most ``MAX_SIDE``."""
    a, b, c, d = matrix[:4]
    nx = math.ceil(size[0] * math.hypot(a, b))
    ny = math.ceil(size[1] * math.hypot(c, d))
    return max(1, min(MAX_SIDE, nx)), max(1, min(MAX_SIDE, ny))


def world_picture(item: DrawItem, picture: Picture | None, scene: Scene, frame: tuple[float, float]) -> Picture:
    """The widget's area as the scene shows through it: the scene (blurred by the background blur) darkened by the
    world darken, opaque wherever the widget's own picture has any alpha, clear where it has none."""
    fx = item.effects
    dark = fx.get("world_darken")
    key = ("world", scene.uid, picture.key if picture else None, tuple(round(m, 3) for m in item.matrix), item.size,
           item.uv, frame, "background_blur" in fx, repr(sorted(dark.items())) if dark else "")  # fmt: skip
    return _remember(key, lambda: _world(item, picture, scene, frame))


def _world(item: DrawItem, picture: Picture | None, scene: Scene, frame: tuple[float, float]) -> Picture:
    nx, ny = _grid(item.matrix, item.size)
    a, b, c, d, tx, ty = item.matrix
    s = (np.arange(nx, dtype=np.float32) + 0.5) / nx
    t = (np.arange(ny, dtype=np.float32) + 0.5) / ny
    lx, ly = np.meshgrid(s * item.size[0], t * item.size[1])
    colour = scene.sample(a * lx + c * ly + tx, b * lx + d * ly + ty, frame, "background_blur" in item.effects)
    dark = item.effects.get("world_darken")
    if dark:
        colour = darken(colour, dark)
    mask = np.ones((ny, nx), bool)
    if picture is not None:  # the shader drops the pixels whose picture alpha is zero and keeps the rest whole
        ox, oy, us, vs = item.uv
        px = np.clip(((ox + s * us) * picture.width).astype(np.int32), 0, picture.width - 1)
        py = np.clip(((oy + t * vs) * picture.height).astype(np.int32), 0, picture.height - 1)
        mask = picture.rgba[py[:, None], px[None, :], 3] > 0
    rgba = np.zeros((ny, nx, 4), np.uint8)
    rgba[..., :3] = mipchain.linear_to_srgb8(colour)
    rgba[..., 3] = np.where(mask, 255, 0)
    return Picture(f"world|{picture.key if picture else ''}|{nx}x{ny}", rgba)


def darken(colour: np.ndarray, v: dict) -> np.ndarray:
    """The world darken: the linear colour multiplied by the effect's colour (taken from display to linear light),
    by the share ``Intensity`` says."""
    tint = effects.to_rgb(v.get("Color")) or (0.0, 0.0, 0.0)
    amount = v.get("Intensity")
    amount = 1.0 if amount is None else float(amount)
    tinted = colour * _linear(np.array(tint))
    return colour + (tinted - colour) * amount


def island_picture(item: DrawItem, picture: Picture) -> Picture:
    """The island picture drawn as the strategy map draws it: its blue channel (the distance to the coast) becomes
    hatching, a border and a contrast in the effect's colours."""
    fx = item.effects["island_shading"]
    key = ("island", picture.key, tuple(round(m, 3) for m in item.matrix[:4]), item.size, item.uv,
           repr(sorted(fx.items())))  # fmt: skip
    return _remember(key, lambda: _island(item, picture, fx))


def texel_rates(matrix: Matrix, size: tuple[float, float], uv: tuple[float, float, float, float],
                texels: tuple[int, int]) -> tuple[float, float, float]:  # fmt: skip
    """What the shader's screen derivatives of the texture coordinates give for an affine widget: the texels a screen
    pixel covers along x and along y (``fwidth`` of the coordinates times the picture size), and the angle of the
    picture's x axis on the screen."""
    a, b, c, d = matrix[:4]
    det = a * d - b * c or 1e-9
    kx, ky = uv[2] / max(size[0], 1e-9), uv[3] / max(size[1], 1e-9)  # the coordinates per local unit
    rate_x = texels[0] * kx * (abs(d) + abs(c)) / abs(det)
    rate_y = texels[1] * ky * (abs(b) + abs(a)) / abs(det)
    return rate_x, rate_y, math.atan2(-c / det, d / det)


def hatch_layers(u: np.ndarray, v: np.ndarray, angle: float) -> list[np.ndarray]:
    """The six ink layers the shader reads from its two hatch pictures at the turned coordinates (``u``, ``v`` of
    the picture, turned by ``angle``): lines at 45 degrees that wrap once over the picture, denser with the layer.
    The game's own two pictures are not in its files by name, so these are a stand-in."""
    x1 = u * math.cos(angle) - v * math.sin(angle)
    y1 = u * math.sin(angle) + v * math.cos(angle)
    out = []
    for lines, width in zip(HATCH_LINES, HATCH_WIDTH, strict=True):
        phase = (x1 + y1) * lines
        away = np.abs((phase + 0.5) % 1.0 - 0.5)  # the distance to the nearest line centre, in periods
        out.append(np.clip((width / 2 - away) * 12.0 + 0.5, 0.0, 1.0))
    return out


def hatch_weights(intensity: np.ndarray) -> list[np.ndarray]:
    """The six layer weights of the shader from the stroke intensity: each layer fades in as the intensity reaches
    its sixth and out as the next one takes over, the last stays."""
    ramp = [np.clip(intensity * 6.0 - k, 0.0, 1.0) for k in range(6)]
    return [ramp[k] - ramp[k + 1] for k in range(5)] + [ramp[5]]


def _island(item: DrawItem, picture: Picture, fx: dict) -> Picture:
    h, w = picture.rgba.shape[:2]
    distance = mipchain.srgb_to_linear(picture.rgba[..., 2]).astype(np.float32)  # the shader takes the blue to linear
    ink = np.array(effects.to_rgb(fx.get("ForegroundColor")) or DEFAULT_INK, np.float32)
    paper = np.array(effects.to_rgb(fx.get("BackgroundColor")) or DEFAULT_PAPER, np.float32)
    fg, bg = _linear(ink), _linear(paper)
    factor = float(fx.get("HighlightFactor") or 0.0)
    contrast = float(fx.get("HighlightContrast") or 0.0)
    rate_x, rate_y, align = texel_rates(item.matrix, item.size, item.uv, (w, h))
    u, v = np.meshgrid((np.arange(w) + 0.5) / w, (np.arange(h) + 0.5) / h)
    scaled = np.clip(3.0 * np.sqrt(distance), 0.0, 1.0) * (1.0 + factor)
    layers = hatch_layers(u.astype(np.float32), v.astype(np.float32), align + float(fx.get("Angle") or 0.0))
    inked = sum(layer * weight for layer, weight in zip(layers, hatch_weights(1.0 - scaled), strict=True))
    colour = bg + (fg - bg) * np.clip(inked, 0.0, 1.0)[..., None]  # lerp(fg, bg, 1 - ink)
    border = 10.0 * (rate_x + rate_y)  # ten pixels of a 1920 wide screen, in texels
    edge = _smoothstep(min(max(1.0 - border, 0.0), 1.0), 1.0, distance * border)[..., None]
    colour = fg + (colour - fg) * edge
    colour = np.clip(fg + (colour - fg) * (1.0 + contrast * factor), 0.0, 1.0)
    rgba = np.empty_like(picture.rgba)
    rgba[..., :3] = mipchain.linear_to_srgb8(colour)
    rgba[..., 3] = picture.rgba[..., 3]
    return Picture(f"island|{picture.key}|{w}x{h}", rgba)


def _linear(colour: np.ndarray) -> np.ndarray:
    """A display colour (floats 0..1) in linear light."""
    return mipchain.srgb_to_linear(np.round(colour * 255).astype(np.uint8)).astype(np.float32)


def _smoothstep(low: float, high: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - low) / max(high - low, 1e-9), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)
