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
from .layout import Matrix, SDrawItem
from .pictures import SPicture
from .worldscene import CScene

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
_g_made: dict[tuple, SPicture] = {}


def shade(
    item: SDrawItem, picture: SPicture | None, scene: CScene, frame: tuple[float, float]
) -> tuple[SDrawItem, SPicture | None]:
    """The item and picture standing in for a widget with a scene effect (the picture alone when it has none)."""
    item_effects = item.effects
    if "background_blur" in item_effects or "world_darken" in item_effects:
        kept = {k: value for k, value in item_effects.items() if k not in effects.WORLD and k not in SCENE_ONLY}
        flat = replace(
            item, color=(255, 255, 255), alpha=1.0, nine=None, uv_offset_scale=(0.0, 0.0, 1.0, 1.0), effects=kept
        )
        return flat, world_picture(item, picture, scene, frame)
    if "island_shading" in item_effects and picture is not None:
        kept = {k: value for k, value in item_effects.items() if k not in effects.WORLD and k not in SCENE_ONLY}
        flat = replace(item, color=(255, 255, 255), alpha=1.0, effects=kept)
        return flat, island_picture(item, picture)
    return item, picture


def _remember(key: tuple, make) -> SPicture:
    if key not in _g_made:
        if len(_g_made) > KEPT:
            _g_made.clear()
        made = make()
        made.key = f"{made.key}|{abs(hash(key)):x}"  # the key tells two pictures of one size apart
        _g_made[key] = made
    return _g_made[key]


def _grid(matrix: Matrix, size_px: tuple[float, float]) -> tuple[int, int]:
    """The pixels a widget covers in the reference space, per side, at most ``MAX_SIDE``."""
    matrix_a, matrix_b, matrix_c, matrix_d = matrix[:4]
    count_x = math.ceil(size_px[0] * math.hypot(matrix_a, matrix_b))
    count_y = math.ceil(size_px[1] * math.hypot(matrix_c, matrix_d))
    return max(1, min(MAX_SIDE, count_x)), max(1, min(MAX_SIDE, count_y))


def world_picture(item: SDrawItem, picture: SPicture | None, scene: CScene, frame: tuple[float, float]) -> SPicture:
    """The widget's area as the scene shows through it: the scene (blurred by the background blur) darkened by the
    world darken, opaque wherever the widget's own picture has any alpha, clear where it has none."""
    effects = item.effects
    dark = effects.get("world_darken")
    key = ("world", scene.m_uid, picture.key if picture else None, tuple(round(component, 3) for component in item.matrix), item.size,
           item.uv_offset_scale, frame, "background_blur" in effects, repr(sorted(dark.items())) if dark else "")  # fmt: skip
    return _remember(key, lambda: _world(item, picture, scene, frame))


def _world(item: SDrawItem, picture: SPicture | None, scene: CScene, frame: tuple[float, float]) -> SPicture:
    count_x, count_y = _grid(item.matrix, item.size)
    matrix_a, matrix_b, matrix_c, matrix_d, offset_x, offset_y = item.matrix
    u_fraction = (np.arange(count_x, dtype=np.float32) + 0.5) / count_x
    v_fraction = (np.arange(count_y, dtype=np.float32) + 0.5) / count_y
    local_x, local_y = np.meshgrid(u_fraction * item.size[0], v_fraction * item.size[1])
    colour = scene.sample(
        matrix_a * local_x + matrix_c * local_y + offset_x,
        matrix_b * local_x + matrix_d * local_y + offset_y,
        frame,
        "background_blur" in item.effects,
    )
    dark = item.effects.get("world_darken")
    if dark:
        colour = darken(colour, dark)
    mask = np.ones((count_y, count_x), bool)
    if picture is not None:  # the shader drops the pixels whose picture alpha is zero and keeps the rest whole
        u_offset, v_offset, u_scale, v_scale = item.uv_offset_scale
        pixel_x = np.clip(((u_offset + u_fraction * u_scale) * picture.width).astype(np.int32), 0, picture.width - 1)
        pixel_y = np.clip(((v_offset + v_fraction * v_scale) * picture.height).astype(np.int32), 0, picture.height - 1)
        mask = picture.rgba[pixel_y[:, None], pixel_x[None, :], 3] > 0
    rgba = np.zeros((count_y, count_x, 4), np.uint8)
    rgba[..., :3] = mipchain.linear_to_srgb8(colour)
    rgba[..., 3] = np.where(mask, 255, 0)
    return SPicture(f"world|{picture.key if picture else ''}|{count_x}x{count_y}", rgba)


def darken(colour: np.ndarray, settings: dict) -> np.ndarray:
    """The world darken: the linear colour multiplied by the effect's colour (taken from display to linear light),
    by the share ``Intensity`` says."""
    tint = effects.to_rgb(settings.get("Color")) or (0.0, 0.0, 0.0)
    amount = settings.get("Intensity")
    amount = 1.0 if amount is None else float(amount)
    tinted = colour * _linear(np.array(tint))
    return colour + (tinted - colour) * amount


def island_picture(item: SDrawItem, picture: SPicture) -> SPicture:
    """The island picture drawn as the strategy map draws it: its blue channel (the distance to the coast) becomes
    hatching, a border and a contrast in the effect's colours."""
    effects = item.effects["island_shading"]
    key = ("island", picture.key, tuple(round(component, 3) for component in item.matrix[:4]), item.size, item.uv_offset_scale,
           repr(sorted(effects.items())))  # fmt: skip
    return _remember(key, lambda: _island(item, picture, effects))


def texel_rates(matrix: Matrix, size_px: tuple[float, float], uv: tuple[float, float, float, float],
                texels: tuple[int, int]) -> tuple[float, float, float]:  # fmt: skip
    """What the shader's screen derivatives of the texture coordinates give for an affine widget: the texels a screen
    pixel covers along x and along y (``fwidth`` of the coordinates times the picture size), and the angle of the
    picture's x axis on the screen."""
    matrix_a, matrix_b, matrix_c, matrix_d = matrix[:4]
    det = matrix_a * matrix_d - matrix_b * matrix_c or 1e-9
    u_per_unit, v_per_unit = (
        uv[2] / max(size_px[0], 1e-9),
        uv[3] / max(size_px[1], 1e-9),
    )  # the coordinates per local unit
    rate_x = texels[0] * u_per_unit * (abs(matrix_d) + abs(matrix_c)) / abs(det)
    rate_y = texels[1] * v_per_unit * (abs(matrix_b) + abs(matrix_a)) / abs(det)
    return rate_x, rate_y, math.atan2(-matrix_c / det, matrix_d / det)


def hatch_layers(u_values: np.ndarray, v_values: np.ndarray, angle: float) -> list[np.ndarray]:
    """The six ink layers the shader reads from its two hatch pictures at the turned coordinates (``u``, ``v`` of
    the picture, turned by ``angle``): lines at 45 degrees that wrap once over the picture, denser with the layer.
    The game's own two pictures are not in its files by name, so these are a stand-in."""
    rotated_u = u_values * math.cos(angle) - v_values * math.sin(angle)
    rotated_v = u_values * math.sin(angle) + v_values * math.cos(angle)
    out = []
    for lines, width in zip(HATCH_LINES, HATCH_WIDTH, strict=True):
        phase = (rotated_u + rotated_v) * lines
        away = np.abs((phase + 0.5) % 1.0 - 0.5)  # the distance to the nearest line centre, in periods
        out.append(np.clip((width / 2 - away) * 12.0 + 0.5, 0.0, 1.0))
    return out


def hatch_weights(intensity: np.ndarray) -> list[np.ndarray]:
    """The six layer weights of the shader from the stroke intensity: each layer fades in as the intensity reaches
    its sixth and out as the next one takes over, the last stays."""
    ramp = [np.clip(intensity * 6.0 - k, 0.0, 1.0) for k in range(6)]
    return [ramp[k] - ramp[k + 1] for k in range(5)] + [ramp[5]]


def _island(item: SDrawItem, picture: SPicture, shading: dict) -> SPicture:
    height, width = picture.rgba.shape[:2]
    distance = mipchain.srgb_to_linear(picture.rgba[..., 2]).astype(np.float32)  # the shader takes the blue to linear
    ink = np.array(effects.to_rgb(shading.get("ForegroundColor")) or DEFAULT_INK, np.float32)
    paper = np.array(effects.to_rgb(shading.get("BackgroundColor")) or DEFAULT_PAPER, np.float32)
    ink_linear, paper_linear = _linear(ink), _linear(paper)
    factor = float(shading.get("HighlightFactor") or 0.0)
    contrast = float(shading.get("HighlightContrast") or 0.0)
    rate_x, rate_y, align = texel_rates(item.matrix, item.size, item.uv_offset_scale, (width, height))
    u_values, v_values = np.meshgrid((np.arange(width) + 0.5) / width, (np.arange(height) + 0.5) / height)
    scaled = np.clip(3.0 * np.sqrt(distance), 0.0, 1.0) * (1.0 + factor)
    layers = hatch_layers(
        u_values.astype(np.float32), v_values.astype(np.float32), align + float(shading.get("Angle") or 0.0)
    )
    inked = sum(layer * weight for layer, weight in zip(layers, hatch_weights(1.0 - scaled), strict=True))
    colour = paper_linear + (ink_linear - paper_linear) * np.clip(inked, 0.0, 1.0)[..., None]  # lerp(fg, bg, 1 - ink)
    border = 10.0 * (rate_x + rate_y)  # ten pixels of a 1920 wide screen, in texels
    edge = _smoothstep(min(max(1.0 - border, 0.0), 1.0), 1.0, distance * border)[..., None]
    colour = ink_linear + (colour - ink_linear) * edge
    colour = np.clip(ink_linear + (colour - ink_linear) * (1.0 + contrast * factor), 0.0, 1.0)
    rgba = np.empty_like(picture.rgba)
    rgba[..., :3] = mipchain.linear_to_srgb8(colour)
    rgba[..., 3] = picture.rgba[..., 3]
    return SPicture(f"island|{picture.key}|{width}x{height}", rgba)


def _linear(colour: np.ndarray) -> np.ndarray:
    """A display colour (floats 0..1) in linear light."""
    return mipchain.srgb_to_linear(np.round(colour * 255).astype(np.uint8)).astype(np.float32)


def _smoothstep(low: float, high: float, x: np.ndarray) -> np.ndarray:
    fraction = np.clip((x - low) / max(high - low, 1e-9), 0.0, 1.0)
    return fraction * fraction * (3.0 - 2.0 * fraction)
