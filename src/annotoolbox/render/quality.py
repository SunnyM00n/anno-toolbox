"""Rendering quality for the two newest games (117 and 1800), so the island editor and the viewer run on modest GPUs:
presets Low, Medium, High and Ultra (and Custom), each a ``SQuality`` record the renderers read; the older games draw
at their best (they draw easily). No Qt, no GPU.

``resolve(game, stored, settings_text)`` is the entry: the quality the settings keep for a game (``Settings.quality``,
default Medium) with the game's own values of its quality level filled in from its ``enginesettings`` file where the
file has them (``game_levels``); which values are the game's and which ours: docs/rendering/quality.md.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, replace

from ..infra.i18n import N_
from .cull import SLodRules

GAMES = ("anno117", "anno1800")  # the games with a quality setting; every other game draws at ``ULTRA``
PRESETS = {
    "low": N_("common.low"),
    "medium": N_("islands.medium"),
    "high": N_("common.high"),
    "ultra": N_("common.ultra"),
}
CUSTOM = "custom"
DEFAULT = "medium"
SETTINGS_FILE = {"anno117": "data/base/config/engine/enginesettings/default.xml",
                 "anno1800": "data/config/engine/enginesettings/default.xml"}  # fmt: skip
INFINITE = 1e9


@dataclass(frozen=True)
class SQuality:
    """What a view draws at one quality. ``level``: the game's quality level (``QualityLevel0`` .. ``4`` of its
    engine settings) whose values fill ``lod_bias``, ``min_pixels``, ``view_distance``; the rest are ours."""

    preset: str = DEFAULT
    level: int = 1
    render_scale: float = 1.0  # of the view's pixels the scene is drawn at
    shadow_size: int = 4096  # the shadow map's side (the game frame's atlas of four cascades; the view's own map)
    shadows: bool = True
    shadow_cascades: int = 4  # the sun's cascades drawn and read (``cUsedShadowPartitions``): fewer, nearer shadows
    lod_bias: float = 0.5  # the game's ``LODBias`` (117; 1800 has none: 1)
    min_pixels: float = 15.0  # the game's ``MinimumObjectSizeInPixels``: smaller objects are not drawn
    view_distance: float = INFINITE  # objects past this many units from the eye are not drawn
    max_texture: int = 0  # the largest side a model's map is uploaded at (0: as stored); coarser mips cut memory
    tessellation: bool = True  # the near terrain tessellated from the height pages (117's game look)
    bake_size: int = 256  # the terrain bake's texels per node (``terraindraw.SLICE_SIZE``, the engine's 512)
    clouds: bool = True
    haze: bool = True
    ssao: bool = True
    ssr: bool = True
    post: bool = True
    eye_adaptation: bool = True
    particles: int = 40_000  # the particle quads a frame draws at most (the nearest first)
    cloth: int = 64  # the cloths simulated at once

    def with_values(self, **values) -> SQuality:
        """This quality with ``values`` changed: a Custom one."""
        return replace(self, preset=CUSTOM, **values)


# [ours]: what each preset sets beyond the game's level (rendering/quality.md "The presets")
OURS = {
    "low": {
        "level": 0,
        "render_scale": 0.6,
        "shadow_size": 2048,
        "shadow_cascades": 2,
        "view_distance": 900.0,
        "max_texture": 512,
        "tessellation": False,
        "bake_size": 128,
        "clouds": False,
        "haze": False,
        "ssao": False,
        "ssr": False,
        "eye_adaptation": False,
        "particles": 8_000,
        "cloth": 8,
    },
    "medium": {
        "level": 1,
        "render_scale": 0.8,
        "shadow_size": 2048,
        "shadow_cascades": 3,
        "view_distance": 1600.0,
        "max_texture": 1024,
        "tessellation": True,
        "bake_size": 256,
        "ssr": False,
        "particles": 20_000,
        "cloth": 24,
    },
    "high": {
        "level": 2,
        "render_scale": 1.0,
        "shadow_size": 4096,
        "shadow_cascades": 4,
        "view_distance": 3000.0,
        "max_texture": 2048,
        "particles": 40_000,
        "cloth": 48,
    },
    "ultra": {
        "level": 4,
        "render_scale": 1.0,
        "shadow_size": 4096,
        "shadow_cascades": 4,
        "view_distance": INFINITE,
        "max_texture": 0,
        "particles": 40_000,
        "cloth": 64,
    },
}
# the game's values per quality level where its file is not at hand: vanilla 117's (enginesettings/default.xml)
VANILLA = {
    "anno117": [(0.18, 16.0), (0.5, 15.0), (1.0, 14.0), (1.5, 13.0), (2.5, 12.0)],
    "anno1800": [(1.0, 12.0), (1.0, 12.0), (1.0, 10.0), (1.0, 8.0), (1.0, 6.0)],
}  # (LODBias, MinimumObjectSizeInPixels)


def game_levels(text: str) -> list[dict[str, float]]:
    """Per quality level of an ``enginesettings`` file its plain values (``LODBias``, ``MinimumObjectSizeInPixels``,
    ``ViewDistance``, ``ObjectViewDistance`` ..., the ``LODDistances`` table left out); [] without levels."""
    out = []
    for k in range(8):
        match = re.search(rf"<QualityLevel{k}>(.*?)</QualityLevel{k}>", text, re.S)
        if match is None:
            break
        body = re.sub(r"<LODDistances>.*?</LODDistances>", "", match.group(1), flags=re.S)
        out.append(dict(_numbers(re.findall(r"<(\w+)>\s*([-\d.]+)\s*</\1>", body))))
    return out


def _numbers(found: list[tuple[str, str]]):
    """(name, number) of each ``found`` pair whose text reads as a number (``-`` and ``1.2.3`` do not)."""
    for name, text in found:
        try:
            yield name, float(text)
        except ValueError:
            continue


def preset(game: str, name: str, levels: list[dict] | None = None) -> SQuality:
    """A preset for ``game``: our values (``OURS``) with the game's of its level (``levels``: ``game_levels`` of the
    game's file; vanilla's when None). Another game than ``GAMES``: Ultra, whatever the name."""
    name = name if name in OURS and game in GAMES else "ultra"
    ours = OURS[name]
    level = ours["level"]
    bias, pixels = VANILLA.get(game, VANILLA["anno117"])[level]
    if levels and level < len(levels):
        bias = levels[level].get("LODBias", bias if game == "anno117" else 1.0)
        pixels = levels[level].get("MinimumObjectSizeInPixels", pixels)
    return SQuality(preset=name, lod_bias=float(bias), min_pixels=float(pixels), **ours)


def resolve(game: str, stored, settings_text: str = "") -> SQuality:
    """The quality a game draws at: ``stored`` as ``Settings.quality`` keeps it for the game (a preset's name, or a
    Custom record of ``store``; missing: Medium), the game's own values read from ``settings_text`` (its engine
    settings) where given."""
    levels = game_levels(settings_text) if settings_text else None
    if isinstance(stored, dict) and stored.get("preset") == CUSTOM:
        known = {field.name for field in fields(SQuality)}
        base = preset(game, str(stored.get("base", DEFAULT)), levels)
        values = {}
        for k, value in stored.items():
            if k in known and k != "preset":
                try:
                    values[k] = type(getattr(base, k))(value)
                except (TypeError, ValueError):  # a hand-edited settings file: that value keeps the preset's
                    continue
        return replace(base, **values, preset=CUSTOM) if game in GAMES else base
    return preset(game, str(stored or DEFAULT), levels)


def store(quality: SQuality, base: str = DEFAULT) -> object:
    """What ``Settings.quality`` keeps for a game: a preset's name, or a Custom quality's values (with the preset
    it started from)."""
    if quality.preset != CUSTOM:
        return quality.preset
    return {**asdict(quality), "base": base}


def lod_rules(quality: SQuality | None, base: SLodRules | None = None) -> SLodRules:
    """The level-of-detail rules at quality ``q``: its bias, smallest size and draw distance over ``base`` (the
    game's areas and distances; vanilla's when None); the best (vanilla's highest level) without a quality."""
    base = base or SLodRules()
    return (
        base
        if quality is None
        else replace(base, bias=quality.lod_bias, min_pixels=quality.min_pixels, far=quality.view_distance)
    )


MADE_WITH = ("shadow_size", "tessellation", "max_texture", "bake_size")  # what a game frame is built for


def remakes(made: SQuality | None, wanted: SQuality | None) -> bool:
    """Whether a frame built for ``made`` must be built again for ``wanted`` (its maps, shadow map or terrain
    programs differ; the rest applies from the next frame)."""
    if made is None or wanted is None:
        return (made is None) != (wanted is None)
    return any(getattr(made, k) != getattr(wanted, k) for k in MADE_WITH)
