"""The effects a widget carries, read and applied to its picture as the game's UI shader does: colorize (contrast,
brightness, grey scale, a mix of two colours), the simple colorize, brightness and saturation templates, whiten,
alpha test, glow and drop shadow from the alpha around a texel, and the soft mask's smooth step.

``read(node, overrides, load)`` gives the effects of an instance by kind with their values (the template's defaults,
the instance's values, what bindings write); ``apply(rgba, effects)`` the picture with them. docs/rendering/ui-117.md
sections 2 to 4 are the rules; docs/ui-editor.md "Effects and soft masks" what is inferred.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ..formats import phoenixedit
from . import bindings, fields

# an effect template is known by the names of its properties (the shipped templates are unnamed files)
KINDS = (
    ("soft_mask", {"AlphaThreshold", "AlphaGradient"}),
    ("colorize", {"RGB1", "RGB2", "MixFactor", "GrayScale"}),
    ("glow", {"InnerGlowColor", "OuterGlowColor"}),
    ("drop_shadow", {"Strength", "Direction", "IsOmnidirectional"}),
    ("blur", {"Strength"}),
    ("distance_field", {"Thickness", "Gradient", "InnerSize", "OuterSize"}),
    ("blink", {"FadeIntensity"}),
    ("scroll", {"Speed"}),
    ("tint", {"Color", "Intensity", "Opaque"}),
    ("world_darken", {"Color", "Intensity"}),
    ("island_shading", {"ForegroundColor", "BackgroundColor", "HighlightFactor"}),
    ("brightness", {"BrightnessLevel"}),
    ("saturation", {"SaturationLevel"}),
    ("alpha_test", {"Threshold", "Enabled"}),
    ("whiten", {"Enable"}),
)
DRAWN = {"colorize", "glow", "drop_shadow", "tint", "brightness", "saturation", "alpha_test", "whiten", "blur",
         "distance_field", "blink", "scroll", "background_blur", "world_darken", "island_shading"}  # fmt: skip
WORLD = (
    "background_blur",
    "world_darken",
    "island_shading",
)  # drawn by ``worldfx`` over the 3D scene, not by ``apply``
MOVING = ("scroll", "blink")  # the effects whose picture changes with time (``apply``'s ``seconds``)
STEP_SECONDS = 0.05  # the preview's time step: a moving effect is made again once per step, not per pixel of time
SAMPLE_RADIUS = 2  # glow and drop shadow read the 5 x 5 texels round each texel
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def kind_of(names: set[str]) -> str | None:
    """The kind of an effect template by its property names; the one with none is the background blur."""
    if not names:
        return "background_blur"
    return next((kind for kind, need in KINDS if need <= names), None)


def to_rgb(value) -> tuple[float, float, float] | None:
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        v = [float(x) for x in value[:3]]
        return tuple(x / 255.0 for x in v) if max(v) > 1.0 else tuple(v)
    return None


def read(node: dict, overrides: dict, load: Callable[[str], dict | None]) -> dict[str, dict[str, object]]:
    """The instance's effects by kind, each with its values by property name."""
    out: dict[str, dict[str, object]] = {}
    for effect in phoenixedit.objects(node, "Effects"):
        tpl = fields.get(effect, "EffectTemplate")
        root = load(tpl) if tpl else None
        if root is None:
            continue
        uid = int(fields.get(effect, "UniqueId", 0) or 0)
        values = {}
        for property in bindings.custom_properties(root):
            property_data = bindings.property_info(property)
            default = property_data["default"][1] if isinstance(property_data["default"], tuple) else None
            given = overrides.get((uid, property_data["uid"]))
            values[property_data["name"]] = default if given is None else given
        kind = kind_of(set(values))
        if kind is not None:
            out[kind] = values
    return out


def drawn(effects: dict[str, dict]) -> dict[str, dict]:
    """The effects that change a picture (a soft mask acts on the widgets it covers instead)."""
    return {kind: values for kind, values in effects.items() if kind in DRAWN and _active(kind, values)}


def moving(effects: dict[str, dict]) -> bool:
    """Whether an effect among ``effects`` (``drawn``'s result) changes with time."""
    return any(kind in effects for kind in MOVING)


def _value(values: dict, name: str, default: float) -> float:
    """A value as a number; ``default`` when the template gave none (``read`` leaves a missing default as None)."""
    return default if values.get(name) is None else float(values[name])


def _active(kind: str, values: dict) -> bool:
    if kind == "alpha_test":
        return bool(values.get("Enabled", True))
    if kind == "whiten":
        return bool(values.get("Enable"))
    if kind == "blur":
        return float(values.get("Strength") or 0.0) > 0.0
    if kind == "scroll":
        return float(values.get("Speed") or 0.0) > 0.0
    if kind == "world_darken":
        return _value(values, "Intensity", 1.0) > 0.0
    return True


def _neighbourhood(alpha: np.ndarray, shift: tuple[int, int] = (0, 0)) -> list[np.ndarray]:
    """The alpha of the 5 x 5 texels round each texel (clamped at the edges), each moved by ``shift``."""
    height_px, width_px = alpha.shape
    reach = (width_px + SAMPLE_RADIUS, height_px + SAMPLE_RADIUS)  # a shift past this reads only the edge
    shift = (max(-reach[0], min(reach[0], shift[0])), max(-reach[1], min(reach[1], shift[1])))
    pad = SAMPLE_RADIUS + max(abs(shift[0]), abs(shift[1]))
    padded = np.pad(alpha, pad, mode="edge")
    out = []
    for dy in range(-SAMPLE_RADIUS, SAMPLE_RADIUS + 1):
        for dx in range(-SAMPLE_RADIUS, SAMPLE_RADIUS + 1):
            y0, x0 = pad + dy + shift[1], pad + dx + shift[0]
            out.append(padded[y0 : y0 + height_px, x0 : x0 + width_px])
    return out


def _over(bottom: np.ndarray, top: np.ndarray) -> np.ndarray:
    """Premultiplied ``top`` over ``bottom`` (both rgba floats)."""
    out = np.empty_like(top)
    out[..., :3] = top[..., :3] + bottom[..., :3] * (1 - top[..., 3:4])
    out[..., 3] = top[..., 3] * (1 - bottom[..., 3]) + bottom[..., 3]
    return out


def _unpremultiply(pixels: np.ndarray) -> np.ndarray:
    pixels[..., 3] = np.clip(pixels[..., 3], 0.0, 1.0)
    alpha = pixels[..., 3:4]
    pixels[..., :3] = np.where(alpha > 0, pixels[..., :3] / np.maximum(alpha, 1e-6), pixels[..., :3])
    return pixels


def glow(pixels: np.ndarray, values: dict) -> np.ndarray:
    """Inner and outer glow from the alpha round each texel, blended as the effect's mode says (0, 1 or 2)."""
    alpha = pixels[..., 3]
    outer = (
        sum(neighbour * (1 - alpha) for neighbour in _neighbourhood(alpha))
        * float(values.get("OuterGlowStrength") or 0)
        / 25
    )
    inner = (
        sum(alpha * (1 - neighbour) for neighbour in _neighbourhood(alpha))
        * float(values.get("InnerGlowStrength") or 0)
        / 25
    )
    oc = np.array(to_rgb(values.get("OuterGlowColor")) or (0, 0, 0), np.float32)
    ic = np.array(to_rgb(values.get("InnerGlowColor")) or (0, 0, 0), np.float32)
    mode = int(values.get("BlendMode") or 0)
    if mode == 1:
        pixels[..., :3] += oc * outer[..., None] + ic * inner[..., None]
        pixels[..., 3] = pixels[..., 3] + (3.0 - pixels[..., 3]) * outer
        pixels[..., 3] = pixels[..., 3] + (3.0 - pixels[..., 3]) * inner
        return pixels
    if mode == 0:
        for col, weight in ((oc, outer), (ic, inner)):
            target = np.concatenate([np.broadcast_to(col, pixels[..., :3].shape), np.full(alpha.shape + (1,), 3.0)], -1)
            pixels = pixels + (target - pixels) * weight[..., None]
        return pixels
    pixels[..., :3] *= pixels[..., 3:4]
    og = np.concatenate([oc * outer[..., None], outer[..., None]], -1)
    ig = np.concatenate([ic * inner[..., None], inner[..., None]], -1)
    return _unpremultiply(_over(_over(og, pixels), ig))


def drop_shadow(pixels: np.ndarray, values: dict) -> np.ndarray:
    """The picture's alpha, moved by the shadow's offset and spread, in black under the picture."""
    if values.get("IsOmnidirectional"):
        shift = (0, 0)
    else:  # Strength texels in Direction degrees (inferred: the effect's GPU data takes an offset and a strength)
        angle_rad = np.radians(float(values.get("Direction") or 0.0))
        dist = float(values.get("Strength") or 0.0)
        shift = (round(-np.cos(angle_rad) * dist), round(-np.sin(angle_rad) * dist))
    alpha = pixels[..., 3]
    shadow = sum(neighbour * (1 - alpha) for neighbour in _neighbourhood(alpha, shift)) / 25
    pixels[..., :3] *= pixels[..., 3:4]
    below = np.zeros_like(pixels)
    below[..., 3] = shadow
    return _unpremultiply(_over(below, pixels))


def colorize(pixels: np.ndarray, values: dict) -> np.ndarray:
    """Contrast, brightness, grey scale, then a multiply by a mix of two colours, as the shader's colorize."""
    rgb = np.clip(0.5 + (pixels[..., :3] - 0.5) * (1.0 + float(values.get("Contrast") or 0.0)), 0, 1)
    rgb = np.clip(rgb * _value(values, "Brightness", 1.0), 0, 1)
    grey = float(values.get("GrayScale") or 0.0)
    if grey > 0:
        rgb = rgb + ((rgb @ LUMA)[..., None] - rgb) * min(grey, 1.0)
    c1 = np.array(to_rgb(values.get("RGB1")) or (1, 1, 1), np.float32)
    c2 = np.array(to_rgb(values.get("RGB2")) or (1, 1, 1), np.float32)
    blend = c1 + (c2 - c1) * float(values.get("MixFactor") or 0.0)
    pixels[..., :3] = rgb + (rgb * blend - rgb) * float(values.get("Opacity") or 0.0)
    gamma = float(values.get("AlphaGamma") or 1.0)
    pixels[..., 3] = np.power(np.clip(pixels[..., 3], 0, 1), gamma)
    return pixels


def simple(pixels: np.ndarray, kind: str, values: dict) -> np.ndarray:
    """The described templates mapped onto the colorize step (inferred from their names, ui-117.md section 4)."""
    if kind == "brightness":
        return colorize(pixels, {"Brightness": 1.0 + float(values.get("BrightnessLevel") or 0.0)})
    if kind == "saturation":  # a negative level takes colour away (inferred from the name)
        return colorize(pixels, {"GrayScale": max(0.0, -float(values.get("SaturationLevel") or 0.0))})
    colour = to_rgb(values.get("Color")) or (1, 1, 1)
    return colorize(pixels, {"RGB1": colour, "RGB2": colour, "Opacity": float(values.get("Intensity") or 0.0)})


def _sample(texels: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """``texels`` read at the texel (x + dx, y + dy), clamped at the edges."""
    height_px, width_px = texels.shape[:2]
    dx, dy = (
        max(-width_px, min(width_px, dx)),
        max(-height_px, min(height_px, dy)),
    )  # a shift past the picture reads only its edge
    pad = max(abs(dx), abs(dy))
    padded = np.pad(texels, [(pad, pad), (pad, pad)] + [(0, 0)] * (texels.ndim - 2), mode="edge")
    return padded[pad + dy : pad + dy + height_px, pad + dx : pad + dx + width_px]


GAUSS = ((0, 0, 0.195346), (1, 0, 0.123317), (-1, 0, 0.123317), (0, 1, 0.123317), (0, -1, 0.123317),
         (1, 1, 0.077847), (1, -1, 0.077847), (-1, 1, 0.077847), (-1, -1, 0.077847))  # fmt: skip


def blur(pixels: np.ndarray, values: dict) -> np.ndarray:
    """The shader's nine taps with its weights, ``step`` texels apart: the picture's mip count x Strength x 1/4."""
    height_px, width_px = pixels.shape[:2]
    step = max(1, round((int(np.log2(max(height_px, width_px, 1))) + 1) * float(values.get("Strength") or 0.0) * 0.25))
    premultiplied = pixels.copy()
    premultiplied[..., :3] *= premultiplied[..., 3:4]
    out = sum(wt * _sample(premultiplied, dx * step, dy * step) for dx, dy, wt in GAUSS)
    return _unpremultiply(out)


def _smoothstep(lo, hi, x: np.ndarray) -> np.ndarray:
    """GLSL's smoothstep (lo may exceed hi: then the step falls)."""
    t = np.clip((x - lo) / (hi - lo if hi != lo else 1e-5), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def distance_field(pixels: np.ndarray, values: dict) -> np.ndarray:
    """A picture whose alpha is a distance (1 - alpha), drawn as the shader does: soft edge round ``Thickness``, the
    inner colour toward the inside, the outer one as a glow, each read at its own offset (a fraction of the picture)."""
    eps = 1e-5
    thickness, gradient = _value(values, "Thickness", 0.5), _value(values, "Gradient", 0.1)
    height_px, width_px = pixels.shape[:2]
    dist = 1.0 - pixels[..., 3]
    at = lambda off: 1.0 - _sample(pixels[..., 3], round(float(off[0]) * width_px), round(float(off[1]) * height_px))  # noqa: E731
    lo, hi = max(thickness - gradient / 2 - eps, 0.0), min(thickness + gradient / 2 + eps, 1.0)
    rgb, alpha = pixels[..., :3].copy(), np.ones_like(dist)
    inner = float(values.get("InnerSize") or 0.0)
    if inner > 0:
        imin = max(thickness - thickness * inner - eps, 0.0)
        imax = min(imin + (hi - imin) * float(values.get("InnerGradient") or 0.0) + eps, 1.0)
        where = at(_pair(values.get("InnerOffset")))
        weight = np.where(where >= imin, _smoothstep(imin, imax, where), 0.0)[..., None]
        rgb = rgb + (np.array(to_rgb(values.get("InnerColor")) or (1, 1, 1), np.float32) - rgb) * weight
    alpha = alpha * _smoothstep(hi, lo, dist)
    outer = float(values.get("OuterSize") or 0.0)
    if outer > 0:
        omax = max(thickness + (1.0 - thickness) * outer - eps, 0.0)
        omin = min(omax - float(values.get("OuterGradient") or 0.0) + eps, 1.0)
        where = at(_pair(values.get("OuterOffset")))
        glow_a = np.where(where <= omax, np.where(where > omin, _smoothstep(omax, omin, where), 1.0), 0.0)
        colour = np.array(to_rgb(values.get("OuterColor")) or (1, 1, 1), np.float32)
        total = alpha + glow_a * (1 - alpha)
        rgb = np.where(total[..., None] > 0, (rgb * alpha[..., None] + colour * (glow_a * (1 - alpha))[..., None])
                       / np.maximum(total[..., None], 1e-6), rgb)  # fmt: skip
        alpha = total
    out = np.empty_like(pixels)
    out[..., :3], out[..., 3] = rgb, alpha
    return out


def _pair(value) -> tuple[float, float]:
    return (float(value[0]), float(value[1])) if isinstance(value, (tuple, list)) and len(value) >= 2 else (0.0, 0.0)


def blink(pixels: np.ndarray, values: dict, seconds: float = 0.0) -> np.ndarray:
    """The warm white the shader fades to, 0.5 Hz between 20 and 100 percent, times ``FadeIntensity``."""
    wave = np.sin(seconds * 0.5 * 2 * np.pi) * 0.5 + 0.5
    amount = (0.2 + 0.8 * wave) * _value(values, "FadeIntensity", 1.0)
    full = np.array((1.2, 1.08, 0.96), np.float32) * np.power(np.abs(pixels[..., :3]), 0.25)
    pixels[..., :3] = pixels[..., :3] + (full - pixels[..., :3]) * amount
    return pixels


def scroll(pixels: np.ndarray, values: dict, seconds: float = 0.0) -> np.ndarray:
    """The picture's x moved by time x ``Speed`` (an endless strip: it wraps)."""
    shift = (float(values.get("Speed") or 0.0) * seconds) % 1.0
    return np.roll(pixels, round(shift * pixels.shape[1]), axis=1)


def apply(rgba: np.ndarray, effects: dict[str, dict], seconds: float = 0.0) -> np.ndarray:
    """A picture (uint8 rgba) with its effects in the shader's order: scroll, the colour source (distance field, blur,
    whiten), glow, drop shadow, alpha test, colorize, blink; ``seconds`` is the time scroll and blink read."""
    pixels = rgba.astype(np.float32) / 255.0
    if "scroll" in effects:
        pixels = scroll(pixels, effects["scroll"], seconds)
    if "distance_field" in effects:
        pixels = distance_field(pixels, effects["distance_field"])
    elif "blur" in effects:
        pixels = blur(pixels, effects["blur"])
    if "whiten" in effects:
        pixels[..., :3] = 1.0
    if "glow" in effects:
        pixels = glow(pixels, effects["glow"])
    if "drop_shadow" in effects:
        pixels = drop_shadow(pixels, effects["drop_shadow"])
    if "alpha_test" in effects:
        pixels[..., 3] = np.where(
            pixels[..., 3] < float(effects["alpha_test"].get("Threshold") or 0.0), 0.0, pixels[..., 3]
        )
    if "colorize" in effects:
        pixels = colorize(pixels, effects["colorize"])
    for kind in ("tint", "brightness", "saturation"):
        if kind in effects:
            pixels = simple(pixels, kind, effects[kind])
    if "blink" in effects:
        pixels = blink(pixels, effects["blink"], seconds)
    return (np.clip(pixels, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def soft_step(alpha: np.ndarray, values: dict) -> np.ndarray:
    """A soft mask's coverage from its picture's alpha: a smooth step from its threshold over its gradient (the
    shader's two thresholds; the pairing of the effect's values with them is inferred)."""
    lo = float(values.get("AlphaThreshold") or 0.0)
    hi = lo + max(float(values.get("AlphaGradient") or 0.0), 1e-4)
    if values.get("InverseThreshold"):
        lo, hi = hi, lo
    t = np.clip((alpha - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)
