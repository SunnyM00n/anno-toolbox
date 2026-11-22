"""The light of Anno 2205's and 1800's frame as their ambient settings give it: a local setting (``AmbientArea``) names a
global one (``GlobalAmbientInfo``) whose graphs over the time of day the local setting's variances scale, and the
engine's per-frame constants are filled from them the way the engine fills them (no Qt, no GPU).

``read_light(files, local)`` is the entry, a ``SLight``; ``light_values(light, toward_sun, night)`` the members of
``cbPerFrame`` and ``cbShadow`` by name, ``scattering_values(light, eye_height, near, far)`` the haze of
``cbScattering``. What is read from the engine and what is chosen: docs/rendering/shader-compile.md section 11, "Light".
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from ..mod.modxml import close_tags
from .camera import MIRROR

AMBIENT_DIR = "data/config/engine/ambientsettings/"
DEFAULT_LOCAL = "moderate_session_01"  # the temperate session's setting: the look of most of the game's buildings
LOCALS = {"anno2205": DEFAULT_LOCAL, "anno1800": "moderate_01"}  # per game: its temperate setting (1800: the Old World)
BRDF_1800 = "data/graphics/effects/brdf/prefiltered_environment_brdf.dds"  # 1800's table of the specular light
DAY = 0.5  # the scene's time of day the engine starts at; the graphs are read there
NIGHT = 0.15  # the viewer's night: the light scaled as the other looks do
GAMMA = 2.2  # the engine uploads its colours as pow(rgb, 2.2)
VARIANCE_HOURS = 12.0  # a timed setting's variance fades out over two hours of 24 (1 - 12 |time - t|)
LIGHT_CAP = -0.2356  # radians: the sun's direction is kept at least this far below the horizon
# the sun disc of the sky's scattering: the engine's built-in angles (inner, outer radians) and fall-off
SUN_INNER, SUN_OUTER, SUN_FALLOFF = 0.013, 0.085, 13.0
# 1800's "Tone Mapping Lite": the combine's member -> (the setting's tag, its value when the tag is missing)
LITE = {"cExposure": ("Exposure", 1.0), "cToneMappingEnabled": ("ToneMappingEnabled", 0.0),
        "cToneMappingMaxLinear": ("ToneMappingMaxLinear", 1.0), "cToneMappingWhite": ("ToneMappingWhite", 1.0)}  # fmt: skip
Color = tuple[float, float, float]


@dataclass(frozen=True)
class SLight:
    """One frame's light as the engine uploads it: the colours in linear light, the fill lights' and the sun support
    light's (yaw from the sun, elevation) in radians, the glow brightness, the screen-space ambient occlusion's
    (minimum, fade start, fade end), the sky's scattering values, the cloud shadows, and the cube maps the setting
    names (1800's prefiltered sky, the sky box)."""

    ambient: Color = (0.3, 0.3, 0.3)
    diffuse: Color = (1.0, 1.0, 1.0)
    second: Color = (0.0, 0.0, 0.0)
    third: Color = (0.0, 0.0, 0.0)
    second_offset: tuple[float, float] = (0.0, 0.0)
    third_offset: tuple[float, float] = (0.0, 0.0)
    support: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 0.0)
    support_offset: tuple[float, float] = (0.0, 0.0)
    glow: float = 1.0
    scao: Color = (1.0, 0.1, 1.0)
    time: float = DAY
    zenith: Color = (0.3, 0.5, 0.8)
    horizon: Color = (0.7, 0.8, 0.9)
    sun_color: Color = (1.0, 1.0, 1.0)
    horizon_falloff: float = 1.0
    scatter_start: float = 0.5
    scatter_intensity: float = 1.0
    scatter_falloff: float = 1.0
    sun_enabled: bool = True
    clouds: tuple[float, float, float, float] = (1.0, 0.5, 1.0, 0.0)  # CloudShadowParams x, y, z, w
    invert_clouds: bool = False
    skybox: str = ""
    skybox_night: str = ""
    cloud_dense: str = ""  # 1800's dense cloud box (the sky's sparse one is ``skybox``)
    sky_cloud_density: float = 1.0  # 1800's cloud boxes' visibility (``SkyCloudDensity``)
    specular_cubes: tuple[str, ...] = field(default_factory=tuple)
    environment_rotation: float = 0.0
    post: dict = field(default_factory=dict)  # the post chain's members of cbPostEffect by name (``post_values``)
    lite: dict = field(default_factory=dict)  # 1800's tone map without the filmic curve (``lite_values``)
    lut: str = ""  # 1800's colour grade: a graded picture whose top-left strip is the look-up table


def _parse(raw: bytes) -> ET.Element:
    return ET.fromstring(close_tags(raw.decode("utf-8", "replace").lstrip(chr(0xFEFF))))


def _floats(element: ET.Element | None, keys: str, default: float = 1.0) -> tuple[float, ...]:
    if element is None:
        return tuple(default for _ in keys)
    return tuple(float(element.findtext(k) or default) for k in keys)


def graph_at(graph: ET.Element | None, day_time: float) -> tuple[float, float, float, float]:
    """A graph's (r, g, b, a) at time ``day_time`` (0..1 over the day) as the engine reads it: each key's colour times its
    alpha first, then linear between the keys (the alpha kept beside); white without a graph."""
    keys = []
    for key_node in graph.iter("i") if graph is not None else ():
        red, green, blue, alpha = (float(key_node.findtext(f"c.{channel}") or 1) for channel in "rgba")
        keys.append((float(key_node.findtext("t") or 0), (red * alpha, green * alpha, blue * alpha, alpha)))
    if not keys:
        return (1.0, 1.0, 1.0, 1.0)
    keys.sort(key=lambda k: k[0])
    if day_time <= keys[0][0]:
        return keys[0][1]
    for (start_time, start_colour), (end_time, end_colour) in zip(keys, keys[1:], strict=False):
        if day_time <= end_time:
            blend = (day_time - start_time) / max(1e-9, end_time - start_time)
            return tuple(start + (end - start) * blend for start, end in zip(start_colour, end_colour, strict=True))
    return keys[-1][1]


def _global(files, name: str) -> ET.Element | None:
    """The global setting whose ``Name`` is ``name`` (the names carry stray spaces), None without one."""
    for path in sorted(files.under(AMBIENT_DIR + "global/")):
        if path.endswith(".xml"):
            root = _parse(files.read(path))
            if (root.findtext("Name") or "").strip().lower() == name.strip().lower():
                return root
    return None


def _anywhere(setting: ET.Element, name: str) -> str:
    """The text of the first ``name`` inside the setting (the games keep the sky's files in different blocks)."""
    found = next(setting.iter(name), None)
    return (found.text or "").strip() if found is not None else ""


def variance_weight(setting: ET.Element, day_time: float) -> float:
    """How much of the local variances applies at time ``day_time``: all of it without a time, else fading out over two hours
    of its ``Time``."""
    if setting.findtext("TimeEnabled") != "1":
        return 1.0
    return max(0.0, min(1.0, 1.0 - VARIANCE_HOURS * abs(float(setting.findtext("Time") or DAY) - day_time)))


def _scaled(
    global_setting, graph: str, setting: ET.Element, variance: str, day_time: float, weight: float, gamma: float = GAMMA
) -> Color:
    """A colour graph times its variance (weighted), in linear light (``gamma`` 1: as stored)."""
    red, green, blue, _a = graph_at(global_setting.find(graph) if global_setting is not None else None, day_time)
    variances = _floats(setting.find(variance), "rgb")
    return tuple(
        max(0.0, channel * (1 - weight + weight * channel_variance)) ** gamma
        for channel, channel_variance in zip((red, green, blue), variances, strict=True)
    )


def _scalar(
    global_atmosphere, graph: str, local_atmosphere: ET.Element, variance: str, day_time: float, weight: float
) -> float:
    """A scalar graph (its keys' alpha) times its variance."""
    value = graph_at(global_atmosphere.find(graph) if global_atmosphere is not None else None, day_time)[3]
    return value * (1 - weight + weight * float(local_atmosphere.findtext(variance) or 1.0))


def post_values(global_setting, setting: ET.Element, day_time: float, weight: float) -> dict:
    """The members of ``cbPostEffect`` the bloom and the tone map read, as the engine uploads them: the post blur as
    stored, the white point, the bloom bias and the bloom brightness as ``pow(x, 2.2)`` of the local setting's, the
    image brightness and contrast (scalar graphs times their variance) and the colour balance (colour graphs times
    their variance, as stored: the adjustment works on the encoded picture)."""
    out = {"cPostBlur": float(setting.findtext("PostBlur") or 0.0)}
    for name, tag in (("cToneMappingWhite", "ToneMappingWhite"), ("cBloomBias", "BloomBias"),
                      ("cBloomBrightness", "BloomBrightness")):  # fmt: skip
        out[name] = max(0.0, float(setting.findtext(tag) or 1.0)) ** GAMMA
    out["cImageBrightness"] = _scalar(
        global_setting, "ImageBrightnessGraph", setting, "ImageBrightnessVariance", day_time, weight
    )
    out["cImageContrast"] = _scalar(
        global_setting, "ImageContrastGraph", setting, "ImageContrastVariance", day_time, weight
    )
    for part in ("Shadows", "Midtones", "Highlights"):
        balance = _scaled(
            global_setting,
            f"ColorBalance{part}Graph",
            setting,
            f"ColorBalance{part}Variance",
            day_time,
            weight,
            gamma=1.0,
        )
        out[f"cColorBalance{part}"] = list(balance)
    return out


def read_light(files, local: str = DEFAULT_LOCAL, time: float = DAY) -> SLight:
    """The light of the local setting ``local`` (a file name under ``local/``) at the scene time ``time``."""
    root = _parse(files.read(f"{AMBIENT_DIR}local/{local}.xml"))
    setting = root.find("Info")
    if setting is None:
        return SLight()
    day_time, weight = time, variance_weight(setting, time)
    global_setting = _global(files, setting.findtext("GlobalName") or "Default")
    global_atmosphere, local_atmosphere = (
        global_setting.find("Atmosphere") if global_setting is not None else None,
        setting.find("Atmosphere"),
    )
    if local_atmosphere is None:
        local_atmosphere = ET.Element("Atmosphere")
    support = setting.find("SunSupportLightColor")
    support_red, support_green, support_blue, support_alpha = (
        _floats(support, "rgba", 1.0) if support is not None else (1.0, 1.0, 1.0, 0.0)
    )
    path = lambda text: (text or "").replace(chr(92), "/")  # noqa: E731
    sun = graph_at(global_atmosphere.find("SunColorGraph") if global_atmosphere is not None else None, day_time)
    return SLight(
        ambient=_scaled(global_setting, "AmbientGraph", setting, "AmbientVariance", day_time, weight),
        diffuse=_scaled(global_setting, "DiffuseGraph", setting, "DiffuseVariance", day_time, weight),
        second=_scaled(global_setting, "SecondLightColorGraph", setting, "SecondLightColorVariance", day_time, weight),
        third=_scaled(global_setting, "ThirdLightColorGraph", setting, "ThirdLightColorVariance", day_time, weight),
        second_offset=_floats(setting.find("SecondLightOffset"), "xy", 0.0),
        third_offset=_floats(setting.find("ThirdLightOffset"), "xy", 0.0),
        support=(support_red**GAMMA, support_green**GAMMA, support_blue**GAMMA, support_alpha),
        support_offset=_floats(setting.find("SunSupportLightDirection"), "xy", 0.0),
        glow=float(setting.findtext("GlowBrightness") or 1.0) ** GAMMA,
        scao=tuple(
            float(setting.findtext(k) or default)
            for k, default in (("SCAOMinAmbient", 1.0), ("SCAOFadeStart", 0.1), ("SCAOFadeEnd", 1.0))
        ),  # fmt: skip
        time=day_time,
        zenith=_scaled(
            global_atmosphere, "ZenithColorGraph", local_atmosphere, "ZenithColorVariance", day_time, weight
        ),
        horizon=_scaled(
            global_atmosphere, "HorizonColorGraph", local_atmosphere, "HorizonColorVariance", day_time, weight
        ),
        sun_color=tuple(max(0.0, channel) ** GAMMA for channel in sun[:3]),
        horizon_falloff=_scalar(
            global_atmosphere, "HorizonFallOffGraph", local_atmosphere, "HorizonFallOffVariance", day_time, weight
        ),
        scatter_start=_scalar(
            global_atmosphere, "ScatteringStartGraph", local_atmosphere, "ScatteringStartVariance", day_time, weight
        ),
        scatter_intensity=_scalar(
            global_atmosphere,
            "ScatteringIntensityGraph",
            local_atmosphere,
            "ScatteringIntensityVariance",
            day_time,
            weight,
        ),
        scatter_falloff=_scalar(
            global_atmosphere, "ScatteringFallOffGraph", local_atmosphere, "ScatteringFallOffVariance", day_time, weight
        ),
        sun_enabled=(local_atmosphere.findtext("SunEnabled") or "1") != "0",
        clouds=_floats(local_atmosphere.find("CloudShadowParams"), "xyzw", 0.0),
        invert_clouds=(local_atmosphere.findtext("InvertCloudShadows") or "0") not in ("0", ""),
        skybox=path(_anywhere(setting, "SkyBoxPath")),
        skybox_night=path(_anywhere(setting, "SkyBoxPathNight")),
        cloud_dense=path(_anywhere(setting, "CloudBoxDensePath")),
        sky_cloud_density=float(_anywhere(setting, "SkyCloudDensity") or 1.0),
        specular_cubes=tuple(
            path(_anywhere(setting, f"SpecularCubePath{k}"))
            for k in range(3)
            if _anywhere(setting, f"SpecularCubePath{k}")
        ),  # fmt: skip
        environment_rotation=float(_anywhere(setting, "EnvironmentRotation") or 0.0),
        post=post_values(
            global_setting if global_setting is not None else ET.Element("GlobalAmbientInfo"), setting, day_time, weight
        ),
        lite=lite_values(setting),
        lut=path(_anywhere(setting, "LUTTexturePath")),
    )


def lite_values(setting: ET.Element) -> dict:
    """1800's members of the "Tone Mapping Lite" path (exposure, the switch, the linear part's end and the white),
    as stored: the combine applies them to the encoded picture (shader-compile.md 11 "1800's post chain"); none for
    a setting without them (2205's)."""
    if setting.findtext("ToneMappingMaxLinear") is None:
        return {}
    return {member: float(setting.findtext(tag) or default) for member, (tag, default) in LITE.items()}


def environment_turn(light: SLight) -> np.ndarray:
    """The setting's ``EnvironmentRotation`` (a share of a full turn about the up axis: our reading) as a 4x4 of ours,
    the turn of 1800's prefiltered cubes and of both games' sky boxes."""
    turn = light.environment_rotation * 2 * math.pi
    cosine, sine = math.cos(turn), math.sin(turn)
    return np.array([[cosine, 0, -sine, 0], [0, 1, 0, 0], [sine, 0, cosine, 0], [0, 0, 0, 1]])


def cube_turn(light: SLight) -> np.ndarray:
    """``environment_turn`` with the view's mirrored axes turned back into the game's: what a lookup into one of the
    game's cubes is turned by (1800's prefiltered cubes, both games' sky boxes), so the models' reflections and the
    visible sky read the same side of the setting's sky (shader-compile.md 11 "Sky cubes")."""
    return MIRROR @ environment_turn(light)


def offset_direction(down: Color, offset_rad: tuple[float, float]) -> Color:
    """The direction towards a light set off from the sun: its azimuth the sun's plus ``offset[0]``, its elevation
    ``offset[1]`` itself (the engine builds the light's direction that way and uploads its negation). ``down``: the
    direction the sunlight travels."""
    yaw = math.atan2(down[2], down[0]) + offset_rad[0]
    elevation_rad = offset_rad[1]
    return (
        -math.cos(yaw) * math.cos(elevation_rad),
        -math.sin(elevation_rad),
        -math.sin(yaw) * math.cos(elevation_rad),
    )


def capped(down: Color) -> Color:
    """The sun's direction kept at least ``LIGHT_CAP`` below the horizon (the engine's ``cLgtDirection``)."""
    x, y, z = down
    flat = math.hypot(x, z)
    if flat < 1e-9 or math.atan2(y, flat) <= LIGHT_CAP:
        return down
    return (x / flat * math.cos(LIGHT_CAP), math.sin(LIGHT_CAP), z / flat * math.cos(LIGHT_CAP))


def light_values(light: SLight, toward_sun, is_night: bool = False) -> dict:
    """The members of the engine's per-frame and shadow buffers the model shaders read, by name: the sun, the fill
    lights, day or night, the cloud shadows (standing still: the viewer's wind does not blow)."""
    night_factor = NIGHT if is_night else 1.0
    toward = tuple(float(component) for component in toward_sun)
    length = math.sqrt(sum(component * component for component in toward)) or 1.0
    down = tuple(-component / length for component in toward)
    light_direction = capped(down)
    scale = lambda colour: [component * night_factor for component in colour]  # noqa: E731
    support_red, support_green, support_blue, support_alpha = light.support
    cloud_x, cloud_y, cloud_z, cloud_w = light.clouds
    return {
        "cLgtDirection": list(light_direction), "cLgtDirectionInv": [-component for component in light_direction], "cLgtDirectionUncapped": list(down),
        "cLgtDirectionBound": list(down), "cLgtAmbient": scale(light.ambient), "cLgtDiffuse": scale(light.diffuse),
        "cAmbientLightColor1": scale(light.second), "cAmbientLightColor2": scale(light.third),
        "cAmbientLightDirInv1": list(offset_direction(down, light.second_offset)),
        "cAmbientLightDirInv2": list(offset_direction(down, light.third_offset)),
        "cSunSupportLightColor": [support_red, support_green, support_blue, support_alpha],
        "cSunSupportLightDirInv": list(offset_direction(down, light.support_offset)),
        "cGlowBrightness": light.glow, "cSCAO": list(light.scao),
        "cTimeOfDay": [light.time, 0.0 if is_night else 1.0, 0.0 if is_night else 1.0, 1.0 if is_night else 0.0],
        "cDayNight": [0.0, 1.0, 0.0, 0.0] if is_night else [1.0, 0.0, 1.0, 0.0],
        "cCloudShadows": [cloud_x, cloud_y, cloud_z, cloud_w], "cCloudShadowsDeltas": [0.0, 0.0, 0.0, 0.0],
        "cInvertCloudShadows": 0.0 if light.invert_clouds else 1.0,
    }  # fmt: skip


def scattering_values(light: SLight, eye_height_m: float, near: float, far: float, is_night: bool = False) -> dict:
    """``cbScattering`` as the engine fills it for a camera at ``eye_height`` with the planes ``near`` and ``far``:
    the haze starts at the graph's share of the way to the far plane (that share is our reading) and grows over the
    rest; the sky's colours, its horizon fall-off and the sun disc's built-in angles. Every exponent stays above 0
    (the shaders' pow(0, 0) is NaN)."""
    night_factor = NIGHT if is_night else 1.0
    start = max(near, min(far * 0.95, light.scatter_start * far))
    span = far - start - 5.0
    relative_height = eye_height_m / max(1e-6, far)
    horizon_falloff, distance_falloff = max(1e-3, light.horizon_falloff), max(1e-3, light.scatter_falloff)
    cos_in, cos_out = math.cos(SUN_INNER), math.cos(SUN_OUTER)
    return {
        "cScatteringStart": start, "cScatteringFactor": 1.0 / span if span > 0 else 0.0,
        "cScatteringIntensity": light.scatter_intensity, "cScatteringDistanceFalloff": [distance_falloff, 0.9 * distance_falloff, 0.75 * distance_falloff],
        "cScatteringHorizonFalloff": [horizon_falloff, 0.9 * horizon_falloff, 0.75 * horizon_falloff],
        "cScatteringZenithColor": [channel * night_factor for channel in light.zenith],
        "cScatteringHorizonColor": [channel * night_factor for channel in light.horizon],
        "cScatteringSunColor": [channel * night_factor for channel in light.sun_color], "cScatteringSunCosOuterAngle": cos_out,
        "cScatteringSunFactor": 1.0 / (cos_in - cos_out) if light.sun_enabled else 0.0,
        "cScatteringSunFalloff": [SUN_FALLOFF] * 3, "cScatteringRelativeCameraHeight": relative_height,
        "cScatteringCameraHeightFactor": 1.0 / (relative_height + 1.0), "cScatteringOptimizationFactor": eye_height_m * 0.001,
    }  # fmt: skip
