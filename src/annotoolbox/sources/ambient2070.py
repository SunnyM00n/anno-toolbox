"""2070's light and atmosphere sets, read from ``data/config/engine/ambientmap.xml`` and ``light.xml``.

``read`` returns every named set (``North``, ``Underwater_Void``, ``Night`` ...) with its three variants and the
world's areas; a ``Setting`` holds the file's numbers as they are and the few products the shaders form from them (a
colour times its power: rendering-2070 "4.1 Where the values come from"). Which variant a session shows (the eco
balance, rendering-2070 4.1) is not in the data; callers pick one. ``read_light`` is the fallback light of
``light.xml``. Nothing here guesses a value: a missing tag raises ``KeyError`` naming it.
"""

from __future__ import annotations

import math
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from ..formats import isd2070
from .overlay import SourceTree

AMBIENT_MAP = "data/config/engine/ambientmap.xml"
LIGHT = "data/config/engine/light.xml"
Vec = tuple[float, ...]


def _vec(el: ET.Element, names: str) -> Vec:
    return tuple(float(el.findtext(n)) for n in names)  # type: ignore[arg-type]


def _num(el: ET.Element, tag: str) -> float:
    text = el.findtext(tag)
    if text is None:
        raise KeyError(f"{tag} (in {el.tag})")
    return float(text)


def _scaled(rgb: Vec, power: float) -> Vec:
    return tuple(c * power for c in rgb[:3])


@dataclass(frozen=True)
class Water:
    """The ``Water`` block: waves, and the light and view depths above and under the surface."""

    wave_frequency: Vec
    wave_amplitude: Vec
    wave_velocity: tuple[Vec, Vec]
    choppiness: float
    above_absorption: Vec  # AboveWaterLightAbsorptionExponents
    above_light_depth: float
    above_view_depth: float
    above_view_exponent: float
    above_scattering: Vec
    under_absorption: Vec
    under_light_depth: float
    under_view_depth: float
    under_view_exponent: float
    under_scattering: Vec
    under_dof_focus: float


@dataclass(frozen=True)
class Atmosphere:
    """The ``Atmosphere`` block: colours as stored (before their ``...Power`` in the Setting) and the fall-offs."""

    scatter_start: float
    sun_inner_angle: float
    sun_outer_angle: float
    sun_fall_off: float
    horizon_fall_off: float
    distance_fall_off: float
    cloud_density: float
    sun_color: Vec
    horizon_color: Vec
    zenith_color: Vec
    cloud_color: Vec


@dataclass(frozen=True)
class Setting:
    """One variant of a named set. Colours are gamma values as stored; ``sun`` and the like multiply a colour by its
    power (rendering-2070 4.1 gives the shader constant each feeds)."""

    diffuse_color: Vec
    diffuse_power: float
    ambient_color: Vec
    ambient_power: float
    mood_color: Vec  # r, g, b, a
    mood_power: float
    inv_cloud_shadow_fac: float
    sun_color_power: float
    horizon_color_power: float
    zenith_color_power: float
    cloud_color_power: float
    water: Water
    atmosphere: Atmosphere
    wind: Vec  # x, y: direction; z: strength; w: storm strength (the shaders' cWind)
    current: Vec
    blur_strength: float
    light_direction: Vec  # the direction the light travels along
    cam_y_rot_offset: float

    @property
    def sun(self) -> Vec:
        return _scaled(self.diffuse_color, self.diffuse_power)

    @property
    def ambient(self) -> Vec:
        return _scaled(self.ambient_color, self.ambient_power)

    @property
    def mood(self) -> Vec:
        return _scaled(self.mood_color, self.mood_power)

    @property
    def zenith(self) -> Vec:
        return _scaled(self.atmosphere.zenith_color, self.zenith_color_power)

    @property
    def horizon(self) -> Vec:
        return _scaled(self.atmosphere.horizon_color, self.horizon_color_power)

    @property
    def sky_sun(self) -> Vec:
        return _scaled(self.atmosphere.sun_color, self.sun_color_power)

    @property
    def sun_elevation(self) -> float:
        """Degrees above the horizon of the light's source."""
        x, y, z = self.light_direction
        return math.degrees(math.asin(-y / math.sqrt(x * x + y * y + z * z)))


@dataclass(frozen=True)
class Area:
    name: str
    position: tuple[float, float]  # on the world map, 0..1
    setting: str  # the named set it shows


@dataclass
class AmbientMap:
    areas: list[Area] = field(default_factory=list)
    sets: dict[str, tuple[Setting, ...]] = field(default_factory=dict)

    def area(self, name: str) -> Area:
        return next(a for a in self.areas if a.name == name)

    def setting(self, name: str, variant: int = 0) -> Setting:
        return self.sets[name][variant]


def _water(el: ET.Element) -> Water:
    return Water(
        _vec(el.find("WaveFrequency"), "xy"),
        _vec(el.find("WaveAmplitude"), "xy"),
        (_vec(el.find("WaveVelocity0"), "xy"), _vec(el.find("WaveVelocity1"), "xy")),
        _num(el, "Choppiness"),
        _vec(el.find("AboveWaterLightAbsorptionExponents"), "xyz"),
        _num(el, "AboveWaterLightDepth"),
        _num(el, "AboveWaterViewDepth"),
        _num(el, "AboveWaterViewDepthExponent"),
        _vec(el.find("AboveWaterScattering"), "xyz"),
        _vec(el.find("UnderWaterLightAbsorptionExponents"), "xyz"),
        _num(el, "UnderWaterLightDepth"),
        _num(el, "UnderWaterViewDepth"),
        _num(el, "UnderWaterViewDepthExponent"),
        _vec(el.find("UnderWaterScattering"), "xyz"),
        _num(el, "UnderWaterDOFFocusDistance"),
    )


def _atmosphere(el: ET.Element) -> Atmosphere:
    return Atmosphere(
        _num(el, "ScatterStart"),
        _num(el, "SunInnerAngle"),
        _num(el, "SunOuterAngle"),
        _num(el, "SunFallOff"),
        _num(el, "HorizonFallOff"),
        _num(el, "DistanceFallOff"),
        _num(el, "CloudDensity"),
        _vec(el.find("SunColor"), "rgba"),
        _vec(el.find("HorizonColor"), "rgba"),
        _vec(el.find("ZenithColor"), "rgba"),
        _vec(el.find("CloudColor"), "rgba"),
    )


def _setting(el: ET.Element) -> Setting:
    return Setting(
        _vec(el.find("DiffuseColor"), "rgba"),
        _num(el, "DiffuseColorPower"),
        _vec(el.find("AmbientColor"), "rgba"),
        _num(el, "AmbientColorPower"),
        _vec(el.find("MoodColor"), "rgba"),
        _num(el, "MoodColorPower"),
        _num(el, "InvCloudShadowFac"),
        _num(el, "SunColorPower"),
        _num(el, "HorizonColorPower"),
        _num(el, "ZenithColorPower"),
        _num(el, "CloudColorPower"),
        _water(el.find("Water")),
        _atmosphere(el.find("Atmosphere")),
        _vec(el.find("Wind"), "xyzw"),
        _vec(el.find("Current"), "xyzw"),
        _num(el, "BlurStrength"),
        _vec(el.find("LightDirection"), "xyz"),
        _num(el, "CamYRotOffset"),
    )


def parse(text: str) -> AmbientMap:
    """The text of ``ambientmap.xml`` as sets and areas."""
    root = ET.fromstring(text)
    out = AmbientMap()
    for item in root.findall("GlobalAreas/i"):
        pos = item.find("Position")
        out.areas.append(
            Area(item.findtext("Name"), (float(pos.findtext("x")), float(pos.findtext("y"))), item.findtext("Setting"))
        )
    for named in root.find("Settings"):
        out.sets[named.tag] = tuple(_setting(v) for v in named)
    return out


def read(tree: SourceTree) -> AmbientMap:
    return parse(tree.read_text(AMBIENT_MAP, "latin-1"))


@dataclass(frozen=True)
class Light:
    """``light.xml``: the light used when no set applies."""

    diffuse: Vec
    ambient: Vec
    specular: Vec
    direction: Vec
    cam_y_rot_offset: float


def read_light(tree: SourceTree) -> Light:
    root = isd2070.parse(tree.read_bytes(LIGHT)).find("m_LightInfo")
    if root is None:
        raise KeyError("m_LightInfo (in light.xml)")

    def colour(name: str) -> Vec:
        data = root.find(name).data  # four float32: the file stores its colours as binary values
        return struct.unpack("<4f", data)

    d = root.find("m_Direction")
    direction = tuple(float(d.value(axis)) for axis in "xyz")
    return Light(
        colour("m_Diffuse"),
        colour("m_Ambient"),
        colour("m_Specular"),
        direction,  # type: ignore[arg-type]
        float(root.value("m_CameraYRotOffset")),
    )
