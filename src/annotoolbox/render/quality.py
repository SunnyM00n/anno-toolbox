"""Rendering quality for the two newest games (117 and 1800) as 117's own graphics options set it: a quality preset
(``Low`` .. ``UltraHigh``, or ``Custom``) and a level per area (``Water``, ``Lighting``, ``Shadow`` ...: the keys of the
game's ``engine.ini``), turned into the ``SQuality`` record the renderers read. The older games draw at their best.
No Qt, no GPU.

``resolve(game, stored, settings_text)`` is the entry: the quality ``Settings.quality`` keeps for a game, or without
one the player's own ``engine.ini`` (``player_choice``, read only), with the game's values of its quality level from
its ``enginesettings`` file (``game_levels``). Which values are the game's and which ours: docs/rendering/quality.md.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from ..infra.i18n import N_
from ..sources import gametree
from .cull import SLodRules

GAMES = ("anno117", "anno1800")  # the games with a quality setting; every other game draws at its best
# the game's graphics areas (engine.ini ``RenderEngine.Custom``, in the options menu's order) and each one's top level
AREAS = {
    "Water": 3,
    "Lighting": 3,
    "Shadow": 4,
    "Terrain": 2,
    "Texture": 2,
    "AA": 3,
    "Object": 4,
    "ViewDistance": 4,
    "Raytracing": 3,
    "FeedbackQualityLevel": 4,
}
AREA_LABELS = {
    "Water": N_("common.quality_water"),
    "Lighting": N_("common.quality_lighting"),
    "Shadow": N_("common.quality_shadow"),
    "Terrain": N_("common.quality_terrain"),
    "Texture": N_("common.quality_texture"),
    "AA": N_("common.quality_anti_aliasing"),
    "Object": N_("common.quality_object"),
    "ViewDistance": N_("common.quality_view_distance"),
    "Raytracing": N_("common.quality_raytracing"),
    "FeedbackQualityLevel": N_("common.quality_crowds"),
}
DRAWN = ("Lighting", "Shadow", "Terrain", "Texture", "Object", "ViewDistance")  # the areas the views reproduce
PRESETS = {  # the game's ``QualitySetting`` names, in its order
    "Low": N_("common.low"),
    "Medium": N_("islands.medium"),
    "High": N_("common.high"),
    "VeryHigh": N_("common.quality_very_high"),
    "UltraHigh": N_("common.quality_ultra_high"),
}
LEVEL_LABELS = tuple(PRESETS.values())  # a level's name in an area's list: Low .. Ultra High
CUSTOM = "Custom"
DEFAULT = "Medium"
RELEASED = {"low": "Low", "medium": "Medium", "high": "High", "ultra": "UltraHigh"}  # Toolbox 0.3's preset names
SETTINGS_FILE = {"anno117": "data/base/config/engine/enginesettings/default.xml",
                 "anno1800": "data/config/engine/enginesettings/default.xml"}  # fmt: skip
PLAYER_FILE = {"anno117": "config/engine.ini"}  # under the game's Documents folder; 1800's is not read
PLAYER_FILE_ENV = "ANNO_ENGINE_INI"  # the player's engine.ini in its place (a test's file; empty: none)
INFINITE = 1e9

# [ours] what an area's level sets in the views, where the view has the feature (quality.md "The plan")
SHADOW = ((2, 2048), (3, 2048), (4, 2048), (4, 4096), (4, 4096))  # per Shadow level: (cascades, the atlas's side)
TEXTURE = (512, 1024, 0)  # per Texture level: the largest side a map is uploaded at (0: as stored)
TERRAIN_BAKE = (128, 256, 256)  # per Terrain level: the bake's texels a node; tessellated from level 1
VIEW_DISTANCE = (900.0, 1600.0, 3000.0, 6000.0, INFINITE)  # per ViewDistance level: objects farther are not drawn
PARTICLES = (8_000, 14_000, 20_000, 30_000, 40_000)  # per Object level: the particle quads a frame draws
CLOTH = (8, 16, 24, 48, 64)  # per Object level: the cloths simulated at once
# the game's values per quality level where its file is not at hand: vanilla's (enginesettings/default.xml)
VANILLA = {
    "anno117": [(0.18, 16.0), (0.5, 15.0), (1.0, 14.0), (1.5, 13.0), (2.5, 12.0)],
    "anno1800": [(1.0, 12.0), (1.0, 12.0), (1.0, 10.0), (1.0, 8.0), (1.0, 6.0)],
}  # (LODBias, MinimumObjectSizeInPixels)


@dataclass(frozen=True)
class SQuality:
    """What a view draws at one quality: the game's preset and area levels (``levels``, ``AREAS`` order) and the
    values the renderers read, derived from them (``derive``). ``level``: the ``QualityLevel`` record of the game's
    engine settings (the ``ViewDistance`` level) whose distances apply; ``lod_bias`` and ``min_pixels`` are the
    record's of the ``Object`` level. ``is_player_setting``: taken from the player's own ``engine.ini``."""

    preset: str = "UltraHigh"
    levels: tuple[int, ...] = tuple(AREAS.values())
    is_player_setting: bool = False
    level: int = 4
    render_scale: float = 1.0  # of the view's pixels the scene is drawn at [ours]: the game keeps its resolution
    shadow_size: int = 4096  # the shadow map's side (the game frame's atlas of four cascades; the view's own map)
    shadows: bool = True
    shadow_cascades: int = 4  # the sun's cascades drawn and read (``cUsedShadowPartitions``): fewer, nearer shadows
    lod_bias: float = 2.5  # the game's ``LODBias`` (117; 1800 has none: 1)
    min_pixels: float = 12.0  # the game's ``MinimumObjectSizeInPixels``: smaller objects are not drawn
    view_distance: float = INFINITE  # objects past this many metres from the eye are not drawn
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

    def area(self, name: str) -> int:
        """The level of the game's area ``name`` (a key of ``AREAS``)."""
        return self.levels[list(AREAS).index(name)]


def preset_levels(name: str) -> dict[str, int]:
    """The area levels of the game's preset ``name``: Ultra High has every area at its top [game]; a lower preset
    takes its step in the list (Low 0 .. Very High 3) up to the area's top [I] (quality.md "The presets")."""
    step = list(PRESETS).index(name) if name in PRESETS else list(PRESETS).index(DEFAULT)
    return {area: min(step, top) for area, top in AREAS.items()}


def clamp_levels(levels: dict) -> dict[str, int]:
    """``levels`` (area -> level, as a file or a settings record holds them) as whole levels within each area's
    range; an area that is missing or not a number takes the default preset's."""
    out = preset_levels(DEFAULT)
    for area, top in AREAS.items():
        value = levels.get(area)
        # JSON reads ``Infinity`` and ``NaN`` as floats, which ``int`` refuses
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out[area] = max(0, min(top, int(value)))
    return out


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


def _record_values(game: str, level: int, records: list[dict] | None) -> tuple[float, float]:
    """(``LODBias``, ``MinimumObjectSizeInPixels``) of the game's quality level ``level``: its file's (``records``)
    where it has them, vanilla's otherwise (1800 has no bias: 1)."""
    bias, pixels = VANILLA.get(game, VANILLA["anno117"])[level]
    if records and level < len(records):
        bias = records[level].get("LODBias", bias if game == "anno117" else 1.0)
        pixels = records[level].get("MinimumObjectSizeInPixels", pixels)
    return float(bias), float(pixels)


def derive(game: str, preset: str, levels: dict[str, int], records: list[dict] | None = None) -> SQuality:
    """The quality the views draw at for the game's ``preset`` with area ``levels`` (``clamp_levels``'s): the
    engine settings' values of the levels (``records``: ``game_levels`` of the game's file; vanilla's when None)
    and ours per level where the game's are not known (``SHADOW`` .. ``CLOTH``)."""
    lighting, terrain, objects = levels["Lighting"], levels["Terrain"], levels["Object"]
    cascades, shadow_size = SHADOW[levels["Shadow"]]
    bias, pixels = _record_values(game, objects, records)
    return SQuality(
        preset=preset,
        levels=tuple(levels[area] for area in AREAS),
        level=levels["ViewDistance"],
        shadow_size=shadow_size,
        shadow_cascades=cascades,
        lod_bias=bias,
        min_pixels=pixels,
        view_distance=VIEW_DISTANCE[levels["ViewDistance"]],
        max_texture=TEXTURE[levels["Texture"]],
        tessellation=terrain >= 1,
        bake_size=TERRAIN_BAKE[terrain],
        clouds=lighting >= 1,
        haze=lighting >= 1,
        ssao=lighting >= 1,  # the shipped graphics sets: no SSAO in the lowest (quality.md "What each area changes")
        ssr=lighting >= 2,
        eye_adaptation=lighting >= 1,
        particles=PARTICLES[objects],
        cloth=CLOTH[objects],
    )


def preset(game: str, name: str, records: list[dict] | None = None) -> SQuality:
    """The game's preset ``name`` (a key of ``PRESETS``; another name: the default) for ``game``; another game than
    ``GAMES``: Ultra High, whatever the name."""
    if game not in GAMES:
        name = "UltraHigh"
    elif name not in PRESETS:
        name = DEFAULT
    return derive(game, name, preset_levels(name), records)


def map_released(stored):
    """``stored`` as this version keeps it: a Toolbox 0.3 settings file's lower-case preset name, or its Custom
    record (``base`` and plain values), mapped once to the game's names (quality.md "Settings")."""
    if isinstance(stored, str) and stored in RELEASED:
        return RELEASED[stored]
    if isinstance(stored, dict) and stored.get("preset") == "custom":
        levels = preset_levels(RELEASED.get(str(stored.get("base")), DEFAULT))
        extras = {key: stored[key] for key in ("render_scale", "shadows") if key in stored}
        return {"preset": CUSTOM, "levels": levels, **extras}
    return stored


def player_file(game: str) -> Path | None:
    """Where the player's own graphics settings of ``game`` are (117's ``engine.ini``); None for a game without."""
    if game not in PLAYER_FILE:
        return None
    if PLAYER_FILE_ENV in os.environ:
        return Path(os.environ[PLAYER_FILE_ENV]) if os.environ[PLAYER_FILE_ENV] else None
    return gametree.user_dir(game) / PLAYER_FILE[game]


def read_player_text(game: str) -> str:
    """The player's ``engine.ini`` of ``game`` as text, read only ("" when there is none or it does not read)."""
    path = player_file(game)
    try:
        return path.read_text(encoding="utf-8-sig") if path is not None and path.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return ""


def player_choice(text: str):
    """What the player's ``engine.ini`` (its text) sets: ``RenderEngine.QualitySetting`` with the levels of its
    ``Custom`` block (the levels the game last applied) as a settings record; None when it holds no setting."""
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return None
    engine = data.get("RenderEngine") if isinstance(data, dict) else None
    if not isinstance(engine, dict):
        return None
    name, block = engine.get("QualitySetting"), engine.get("Custom")
    if name not in (*PRESETS, CUSTOM):
        return None
    if not isinstance(block, dict):
        return name if name in PRESETS else None
    return {"preset": name, "levels": clamp_levels(block)}


def resolve(game: str, stored, settings_text: str = "", player_text: str | None = None) -> SQuality:
    """The quality a game draws at: ``stored`` as ``Settings.quality`` keeps it for the game (a preset's name or a
    record of ``store``); without one the player's ``engine.ini`` (``player_text``, read from the player's file when
    None), then the default. The game's own values come from ``settings_text`` (its engine settings) where given."""
    records = game_levels(settings_text) if settings_text else None
    if game not in GAMES:
        return preset(game, "UltraHigh")
    stored, is_player = map_released(stored), False
    if not stored:
        stored = player_choice(read_player_text(game) if player_text is None else player_text)
        is_player = stored is not None
    if isinstance(stored, dict):
        name = stored.get("preset")
        name = name if isinstance(name, str) and name in PRESETS else CUSTOM  # a list (hand-edited) cannot be looked up
        levels = stored.get("levels")
        got = derive(game, name, clamp_levels(levels if isinstance(levels, dict) else {}), records)
        return replace(got, is_player_setting=is_player, **_extras(stored))
    got = preset(game, stored if isinstance(stored, str) else DEFAULT, records)
    return replace(got, is_player_setting=is_player)


def _extras(stored: dict) -> dict:
    """The values of ours a Custom record may add to its levels (the render scale, the shadows switch)."""
    out = {}
    scale = stored.get("render_scale")
    if isinstance(scale, (int, float)) and not isinstance(scale, bool) and 0.25 <= scale <= 1.0:
        out["render_scale"] = float(scale)
    if isinstance(stored.get("shadows"), bool):
        out["shadows"] = stored["shadows"]
    return out


def store(quality: SQuality):
    """What ``Settings.quality`` keeps for a game: None while it follows the player's ``engine.ini``, a preset's
    name, or a Custom record (the area levels and our render scale and shadow switch)."""
    if quality.is_player_setting:
        return None
    if quality.preset in PRESETS and quality.render_scale == 1.0 and quality.shadows:
        if quality.levels == tuple(preset_levels(quality.preset).values()):
            return quality.preset
    levels = dict(zip(AREAS, quality.levels, strict=True))
    return {"preset": CUSTOM, "levels": levels, "render_scale": quality.render_scale, "shadows": quality.shadows}


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
