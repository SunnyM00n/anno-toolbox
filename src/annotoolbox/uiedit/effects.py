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
    for fx in phoenixedit.objects(node, "Effects"):
        tpl = fields.get(fx, "EffectTemplate")
        root = load(tpl) if tpl else None
        if root is None:
            continue
        uid = int(fields.get(fx, "UniqueId", 0) or 0)
        values = {}
        for p in bindings.custom_properties(root):
            info = bindings.property_info(p)
            default = info["default"][1] if isinstance(info["default"], tuple) else None
            given = overrides.get((uid, info["uid"]))
            values[info["name"]] = default if given is None else given
        kind = kind_of(set(values))
        if kind is not None:
            out[kind] = values
    return out


def drawn(effects: dict[str, dict]) -> dict[str, dict]:
    """The effects that change a picture (a soft mask acts on the widgets it covers instead)."""
    return {k: v for k, v in effects.items() if k in DRAWN and _active(k, v)}


def moving(effects: dict[str, dict]) -> bool:
    """Whether an effect among ``effects`` (``drawn``'s result) changes with time."""
    return any(k in effects for k in MOVING)


def _active(kind: str, v: dict) -> bool:
    if kind in ("alpha_test",):
        return bool(v.get("Enabled", True))
    if kind == "whiten":
        return bool(v.get("Enable"))
    if kind == "blur":
        return float(v.get("Strength") or 0.0) > 0.0
    if kind == "scroll":
        return float(v.get("Speed") or 0.0) > 0.0
    if kind == "world_darken":
        return float(v.get("Intensity", 1.0) if v.get("Intensity") is not None else 1.0) > 0.0
    return True


def _neighbourhood(alpha: np.ndarray, shift: tuple[int, int] = (0, 0)) -> list[np.ndarray]:
    """The alpha of the 5 x 5 texels round each texel (clamped at the edges), each moved by ``shift``."""
    pad = SAMPLE_RADIUS + max(abs(shift[0]), abs(shift[1]))
    padded = np.pad(alpha, pad, mode="edge")
    h, w = alpha.shape
    out = []
    for dy in range(-SAMPLE_RADIUS, SAMPLE_RADIUS + 1):
        for dx in range(-SAMPLE_RADIUS, SAMPLE_RADIUS + 1):
            y0, x0 = pad + dy + shift[1], pad + dx + shift[0]
            out.append(padded[y0 : y0 + h, x0 : x0 + w])
    return out


def _over(bottom: np.ndarray, top: np.ndarray) -> np.ndarray:
    """Premultiplied ``top`` over ``bottom`` (both rgba floats)."""
    out = np.empty_like(top)
    out[..., :3] = top[..., :3] + bottom[..., :3] * (1 - top[..., 3:4])
    out[..., 3] = top[..., 3] * (1 - bottom[..., 3]) + bottom[..., 3]
    return out


def _unpremultiply(c: np.ndarray) -> np.ndarray:
    c[..., 3] = np.clip(c[..., 3], 0.0, 1.0)
    a = c[..., 3:4]
    c[..., :3] = np.where(a > 0, c[..., :3] / np.maximum(a, 1e-6), c[..., :3])
    return c


def glow(c: np.ndarray, v: dict) -> np.ndarray:
    """Inner and outer glow from the alpha round each texel, blended as the effect's mode says (0, 1 or 2)."""
    a = c[..., 3]
    outer = sum(s * (1 - a) for s in _neighbourhood(a)) * float(v.get("OuterGlowStrength") or 0) / 25
    inner = sum(a * (1 - s) for s in _neighbourhood(a)) * float(v.get("InnerGlowStrength") or 0) / 25
    oc = np.array(to_rgb(v.get("OuterGlowColor")) or (0, 0, 0), np.float32)
    ic = np.array(to_rgb(v.get("InnerGlowColor")) or (0, 0, 0), np.float32)
    mode = int(v.get("BlendMode") or 0)
    if mode == 1:
        c[..., :3] += oc * outer[..., None] + ic * inner[..., None]
        c[..., 3] = c[..., 3] + (3.0 - c[..., 3]) * outer
        c[..., 3] = c[..., 3] + (3.0 - c[..., 3]) * inner
        return c
    if mode == 0:
        for col, f in ((oc, outer), (ic, inner)):
            target = np.concatenate([np.broadcast_to(col, c[..., :3].shape), np.full(a.shape + (1,), 3.0)], -1)
            c = c + (target - c) * f[..., None]
        return c
    c[..., :3] *= c[..., 3:4]
    og = np.concatenate([oc * outer[..., None], outer[..., None]], -1)
    ig = np.concatenate([ic * inner[..., None], inner[..., None]], -1)
    return _unpremultiply(_over(_over(og, c), ig))


def drop_shadow(c: np.ndarray, v: dict) -> np.ndarray:
    """The picture's alpha, moved by the shadow's offset and spread, in black under the picture."""
    if v.get("IsOmnidirectional"):
        shift = (0, 0)
    else:  # Strength texels in Direction degrees (inferred: the effect's GPU data takes an offset and a strength)
        r = np.radians(float(v.get("Direction") or 0.0))
        dist = float(v.get("Strength") or 0.0)
        shift = (round(-np.cos(r) * dist), round(-np.sin(r) * dist))
    a = c[..., 3]
    shadow = sum(s * (1 - a) for s in _neighbourhood(a, shift)) / 25
    c[..., :3] *= c[..., 3:4]
    below = np.zeros_like(c)
    below[..., 3] = shadow
    return _unpremultiply(_over(below, c))


def colorize(c: np.ndarray, v: dict) -> np.ndarray:
    """Contrast, brightness, grey scale, then a multiply by a mix of two colours, as the shader's colorize."""
    rgb = np.clip(0.5 + (c[..., :3] - 0.5) * (1.0 + float(v.get("Contrast") or 0.0)), 0, 1)
    rgb = np.clip(rgb * float(v.get("Brightness", 1.0) if v.get("Brightness") is not None else 1.0), 0, 1)
    grey = float(v.get("GrayScale") or 0.0)
    if grey > 0:
        rgb = rgb + ((rgb @ LUMA)[..., None] - rgb) * min(grey, 1.0)
    c1 = np.array(to_rgb(v.get("RGB1")) or (1, 1, 1), np.float32)
    c2 = np.array(to_rgb(v.get("RGB2")) or (1, 1, 1), np.float32)
    blend = c1 + (c2 - c1) * float(v.get("MixFactor") or 0.0)
    c[..., :3] = rgb + (rgb * blend - rgb) * float(v.get("Opacity") or 0.0)
    gamma = float(v.get("AlphaGamma") or 1.0)
    c[..., 3] = np.power(np.clip(c[..., 3], 0, 1), gamma)
    return c


def simple(c: np.ndarray, kind: str, v: dict) -> np.ndarray:
    """The described templates mapped onto the colorize step (inferred from their names, ui-117.md section 4)."""
    if kind == "brightness":
        return colorize(c, {"Brightness": 1.0 + float(v.get("BrightnessLevel") or 0.0)})
    if kind == "saturation":  # a negative level takes colour away (inferred from the name)
        return colorize(c, {"GrayScale": max(0.0, -float(v.get("SaturationLevel") or 0.0))})
    colour = to_rgb(v.get("Color")) or (1, 1, 1)
    return colorize(c, {"RGB1": colour, "RGB2": colour, "Opacity": float(v.get("Intensity") or 0.0)})


def _sample(a: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """``a`` read at the texel (x + dx, y + dy), clamped at the edges."""
    h, w = a.shape[:2]
    pad = max(abs(dx), abs(dy))
    padded = np.pad(a, [(pad, pad), (pad, pad)] + [(0, 0)] * (a.ndim - 2), mode="edge")
    return padded[pad + dy : pad + dy + h, pad + dx : pad + dx + w]


GAUSS = ((0, 0, 0.195346), (1, 0, 0.123317), (-1, 0, 0.123317), (0, 1, 0.123317), (0, -1, 0.123317),
         (1, 1, 0.077847), (1, -1, 0.077847), (-1, 1, 0.077847), (-1, -1, 0.077847))  # fmt: skip


def blur(c: np.ndarray, v: dict) -> np.ndarray:
    """The shader's nine taps with its weights, ``step`` texels apart: the picture's mip count x Strength x 1/4."""
    h, w = c.shape[:2]
    step = max(1, round((int(np.log2(max(h, w, 1))) + 1) * float(v.get("Strength") or 0.0) * 0.25))
    premultiplied = c.copy()
    premultiplied[..., :3] *= premultiplied[..., 3:4]
    out = sum(wt * _sample(premultiplied, dx * step, dy * step) for dx, dy, wt in GAUSS)
    return _unpremultiply(out)


def _smoothstep(lo, hi, x: np.ndarray) -> np.ndarray:
    """GLSL's smoothstep (lo may exceed hi: then the step falls)."""
    t = np.clip((x - lo) / (hi - lo if hi != lo else 1e-5), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def distance_field(c: np.ndarray, v: dict) -> np.ndarray:
    """A picture whose alpha is a distance (1 - alpha), drawn as the shader does: soft edge round ``Thickness``, the
    inner colour toward the inside, the outer one as a glow, each read at its own offset (a fraction of the picture)."""
    eps = 1e-5
    thickness, gradient = float(v.get("Thickness", 0.5) or 0.0), float(v.get("Gradient", 0.1) or 0.0)
    h, w = c.shape[:2]
    dist = 1.0 - c[..., 3]
    at = lambda off: 1.0 - _sample(c[..., 3], round(float(off[0]) * w), round(float(off[1]) * h))  # noqa: E731
    lo, hi = max(thickness - gradient / 2 - eps, 0.0), min(thickness + gradient / 2 + eps, 1.0)
    rgb, alpha = c[..., :3].copy(), np.ones_like(dist)
    inner = float(v.get("InnerSize") or 0.0)
    if inner > 0:
        imin = max(thickness - thickness * inner - eps, 0.0)
        imax = min(imin + (hi - imin) * float(v.get("InnerGradient") or 0.0) + eps, 1.0)
        where = at(_pair(v.get("InnerOffset")))
        f = np.where(where >= imin, _smoothstep(imin, imax, where), 0.0)[..., None]
        rgb = rgb + (np.array(to_rgb(v.get("InnerColor")) or (1, 1, 1), np.float32) - rgb) * f
    alpha = alpha * _smoothstep(hi, lo, dist)
    outer = float(v.get("OuterSize") or 0.0)
    if outer > 0:
        omax = max(thickness + (1.0 - thickness) * outer - eps, 0.0)
        omin = min(omax - float(v.get("OuterGradient") or 0.0) + eps, 1.0)
        where = at(_pair(v.get("OuterOffset")))
        glow_a = np.where(where <= omax, np.where(where > omin, _smoothstep(omax, omin, where), 1.0), 0.0)
        colour = np.array(to_rgb(v.get("OuterColor")) or (1, 1, 1), np.float32)
        total = alpha + glow_a * (1 - alpha)
        rgb = np.where(total[..., None] > 0, (rgb * alpha[..., None] + colour * (glow_a * (1 - alpha))[..., None])
                       / np.maximum(total[..., None], 1e-6), rgb)  # fmt: skip
        alpha = total
    out = np.empty_like(c)
    out[..., :3], out[..., 3] = rgb, alpha
    return out


def _pair(value) -> tuple[float, float]:
    return (float(value[0]), float(value[1])) if isinstance(value, (tuple, list)) and len(value) >= 2 else (0.0, 0.0)


def blink(c: np.ndarray, v: dict, seconds: float = 0.0) -> np.ndarray:
    """The warm white the shader fades to, 0.5 Hz between 20 and 100 percent, times ``FadeIntensity``."""
    wave = np.sin(seconds * 0.5 * 2 * np.pi) * 0.5 + 0.5
    amount = (0.2 + 0.8 * wave) * float(v.get("FadeIntensity", 1.0) if v.get("FadeIntensity") is not None else 1.0)
    full = np.array((1.2, 1.08, 0.96), np.float32) * np.power(np.abs(c[..., :3]), 0.25)
    c[..., :3] = c[..., :3] + (full - c[..., :3]) * amount
    return c


def scroll(c: np.ndarray, v: dict, seconds: float = 0.0) -> np.ndarray:
    """The picture's x moved by time x ``Speed`` (an endless strip: it wraps)."""
    shift = (float(v.get("Speed") or 0.0) * seconds) % 1.0
    return np.roll(c, round(shift * c.shape[1]), axis=1)


def apply(rgba: np.ndarray, effects: dict[str, dict], seconds: float = 0.0) -> np.ndarray:
    """A picture (uint8 rgba) with its effects in the shader's order: scroll, the colour source (distance field, blur,
    whiten), glow, drop shadow, alpha test, colorize, blink; ``seconds`` is the time scroll and blink read."""
    c = rgba.astype(np.float32) / 255.0
    if "scroll" in effects:
        c = scroll(c, effects["scroll"], seconds)
    if "distance_field" in effects:
        c = distance_field(c, effects["distance_field"])
    elif "blur" in effects:
        c = blur(c, effects["blur"])
    if "whiten" in effects:
        c[..., :3] = 1.0
    if "glow" in effects:
        c = glow(c, effects["glow"])
    if "drop_shadow" in effects:
        c = drop_shadow(c, effects["drop_shadow"])
    if "alpha_test" in effects:
        c[..., 3] = np.where(c[..., 3] < float(effects["alpha_test"].get("Threshold") or 0.0), 0.0, c[..., 3])
    if "colorize" in effects:
        c = colorize(c, effects["colorize"])
    for kind in ("tint", "brightness", "saturation"):
        if kind in effects:
            c = simple(c, kind, effects[kind])
    if "blink" in effects:
        c = blink(c, effects["blink"], seconds)
    return (np.clip(c, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def soft_step(alpha: np.ndarray, v: dict) -> np.ndarray:
    """A soft mask's coverage from its picture's alpha: a smooth step from its threshold over its gradient (the
    shader's two thresholds; the pairing of the effect's values with them is inferred)."""
    lo = float(v.get("AlphaThreshold") or 0.0)
    hi = lo + max(float(v.get("AlphaGradient") or 0.0), 1e-4)
    if v.get("InverseThreshold"):
        lo, hi = hi, lo
    t = np.clip((alpha - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)
