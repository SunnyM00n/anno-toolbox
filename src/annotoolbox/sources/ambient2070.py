"""2070's light and atmosphere sets, read from ``data/config/engine/ambientmap.xml`` and ``light.xml``.

``read`` returns every named set (``North``, ``Underwater_Void``, ``Night`` ...) with its three variants and the
world's areas; a ``SSetting`` holds the file's numbers as they are and the few products the shaders form from them (a
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
from .overlay import CSourceTree

AMBIENT_MAP = "data/config/engine/ambientmap.xml"
LIGHT = "data/config/engine/light.xml"
Vec = tuple[float, ...]


def _child(element: ET.Element, tag: str) -> ET.Element:
    found = element.find(tag)
    if found is None:
        raise KeyError(f"{tag} (in {element.tag})")
    return found


def _vec(element: ET.Element, tag: str, names: str) -> Vec:
    """The components ``names`` (``xyz``, ``rgba``) of the child ``tag``."""
    return tuple(_num(_child(element, tag), name) for name in names)


def _num(element: ET.Element, tag: str) -> float:
    text = element.findtext(tag)
    if text is None:
        raise KeyError(f"{tag} (in {element.tag})")
    return float(text)


def _scaled(rgb: Vec, power: float) -> Vec:
    return tuple(component * power for component in rgb[:3])


@dataclass(frozen=True)
class SWater:
    """The ``SWater`` block: waves, and the light and view depths above and under the surface."""

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
class SAtmosphere:
    """The ``SAtmosphere`` block: colours as stored (before their ``...Power`` in the Setting) and the fall-offs."""

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
class SSetting:
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
    water: SWater
    atmosphere: SAtmosphere
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
class SArea:
    name: str
    position: tuple[float, float]  # on the world map, 0..1
    setting: str  # the named set it shows


@dataclass
class SAmbientMap:
    areas: list[SArea] = field(default_factory=list)
    sets: dict[str, tuple[SSetting, ...]] = field(default_factory=dict)

    def area(self, name: str) -> SArea:
        found = next((area for area in self.areas if area.name == name), None)
        if found is None:
            raise KeyError(f"no area {name!r} in the ambient map")
        return found

    def setting(self, name: str, variant: int = 0) -> SSetting:
        return self.sets[name][variant]


def _water(element: ET.Element) -> SWater:
    return SWater(
        _vec(element, "WaveFrequency", "xy"),
        _vec(element, "WaveAmplitude", "xy"),
        (_vec(element, "WaveVelocity0", "xy"), _vec(element, "WaveVelocity1", "xy")),
        _num(element, "Choppiness"),
        _vec(element, "AboveWaterLightAbsorptionExponents", "xyz"),
        _num(element, "AboveWaterLightDepth"),
        _num(element, "AboveWaterViewDepth"),
        _num(element, "AboveWaterViewDepthExponent"),
        _vec(element, "AboveWaterScattering", "xyz"),
        _vec(element, "UnderWaterLightAbsorptionExponents", "xyz"),
        _num(element, "UnderWaterLightDepth"),
        _num(element, "UnderWaterViewDepth"),
        _num(element, "UnderWaterViewDepthExponent"),
        _vec(element, "UnderWaterScattering", "xyz"),
        _num(element, "UnderWaterDOFFocusDistance"),
    )


def _atmosphere(element: ET.Element) -> SAtmosphere:
    return SAtmosphere(
        _num(element, "ScatterStart"),
        _num(element, "SunInnerAngle"),
        _num(element, "SunOuterAngle"),
        _num(element, "SunFallOff"),
        _num(element, "HorizonFallOff"),
        _num(element, "DistanceFallOff"),
        _num(element, "CloudDensity"),
        _vec(element, "SunColor", "rgba"),
        _vec(element, "HorizonColor", "rgba"),
        _vec(element, "ZenithColor", "rgba"),
        _vec(element, "CloudColor", "rgba"),
    )


def _setting(element: ET.Element) -> SSetting:
    return SSetting(
        _vec(element, "DiffuseColor", "rgba"),
        _num(element, "DiffuseColorPower"),
        _vec(element, "AmbientColor", "rgba"),
        _num(element, "AmbientColorPower"),
        _vec(element, "MoodColor", "rgba"),
        _num(element, "MoodColorPower"),
        _num(element, "InvCloudShadowFac"),
        _num(element, "SunColorPower"),
        _num(element, "HorizonColorPower"),
        _num(element, "ZenithColorPower"),
        _num(element, "CloudColorPower"),
        _water(_child(element, "Water")),
        _atmosphere(_child(element, "Atmosphere")),
        _vec(element, "Wind", "xyzw"),
        _vec(element, "Current", "xyzw"),
        _num(element, "BlurStrength"),
        _vec(element, "LightDirection", "xyz"),
        _num(element, "CamYRotOffset"),
    )


def parse(text: str) -> SAmbientMap:
    """The text of ``ambientmap.xml`` as sets and areas."""
    root = ET.fromstring(text)
    out = SAmbientMap()
    for item in root.findall("GlobalAreas/i"):
        position_element = item.find("Position")
        out.areas.append(
            SArea(
                item.findtext("Name"),
                (float(position_element.findtext("x")), float(position_element.findtext("y"))),
                item.findtext("Setting"),
            )
        )
    for named in root.find("Settings"):
        out.sets[named.tag] = tuple(_setting(child) for child in named)
    return out


def read(tree: CSourceTree) -> SAmbientMap:
    return parse(tree.read_text(AMBIENT_MAP, "latin-1"))


@dataclass(frozen=True)
class SLight:
    """``light.xml``: the light used when no set applies."""

    diffuse: Vec
    ambient: Vec
    specular: Vec
    direction: Vec
    cam_y_rot_offset: float


def read_light(tree: CSourceTree) -> SLight:
    root = isd2070.parse(tree.read_bytes(LIGHT)).find("m_LightInfo")
    if root is None:
        raise KeyError("m_LightInfo (in light.xml)")

    def colour(name: str) -> Vec:
        data = root.find(name).data  # four float32: the file stores its colours as binary values
        return struct.unpack("<4f", data)

    direction_element = root.find("m_Direction")
    direction = tuple(float(direction_element.value(axis)) for axis in "xyz")
    return SLight(
        colour("m_Diffuse"),
        colour("m_Ambient"),
        colour("m_Specular"),
        direction,  # type: ignore[arg-type]
        float(root.value("m_CameraYRotOffset")),
    )
