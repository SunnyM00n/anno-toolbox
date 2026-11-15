"""The ambient-map, light and tool-default readers, on small files written for the test (no game files)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from annotoolbox.sources import ambient2070, propdefaults2070
from annotoolbox.sources.overlay import SourceTree


def _vec(tag: str, **v: float) -> str:
    return f"<{tag}>" + "".join(f"<{k}>{x}</{k}>" for k, x in v.items()) + f"</{tag}>"


def _setting(diffuse_power: float = 2.0, wind_z: float = 0.17) -> str:
    return (
        _vec("DiffuseColor", r=0.5, g=0.25, b=1, a=0)
        + f"<DiffuseColorPower>{diffuse_power}</DiffuseColorPower>"
        + _vec("AmbientColor", r=0.5, g=0.5, b=0.5, a=0)
        + "<AmbientColorPower>1.5</AmbientColorPower>"
        + _vec("MoodColor", r=0.2, g=0.2, b=0.2, a=0.2)
        + "<MoodColorPower>2</MoodColorPower><InvCloudShadowFac>0.6</InvCloudShadowFac><SunColorPower>0.7</SunColorPower>"
        + "<HorizonColorPower>2</HorizonColorPower><ZenithColorPower>1</ZenithColorPower><CloudColorPower>1</CloudColorPower>"
        + "<Water>"
        + _vec("WaveFrequency", x=1, y=2)
        + _vec("WaveAmplitude", x=0.3, y=0.4)
        + _vec("WaveVelocity0", x=0.01, y=0.02)
        + _vec("WaveVelocity1", x=0.03, y=0.04)
        + "<Choppiness>3.5</Choppiness>"
        + _vec("AboveWaterLightAbsorptionExponents", x=1, y=2, z=3)
        + "<AboveWaterLightDepth>30</AboveWaterLightDepth><AboveWaterViewDepth>10</AboveWaterViewDepth>"
        + "<AboveWaterViewDepthExponent>0.35</AboveWaterViewDepthExponent>"
        + _vec("AboveWaterScattering", x=0.1, y=0.2, z=0.3)
        + _vec("UnderWaterLightAbsorptionExponents", x=4, y=5, z=6)
        + "<UnderWaterLightDepth>36</UnderWaterLightDepth><UnderWaterViewDepth>58.6</UnderWaterViewDepth>"
        + "<UnderWaterViewDepthExponent>1.84</UnderWaterViewDepthExponent>"
        + _vec("UnderWaterScattering", x=0.03, y=0.05, z=0.1)
        + "<UnderWaterDOFFocusDistance>26.8</UnderWaterDOFFocusDistance></Water>"
        + "<Atmosphere><ScatterStart>0.095</ScatterStart><SunInnerAngle>0</SunInnerAngle><SunOuterAngle>0.84</SunOuterAngle>"
        + "<SunFallOff>8.5</SunFallOff><HorizonFallOff>9.8</HorizonFallOff><DistanceFallOff>1.5</DistanceFallOff>"
        + "<CloudDensity>0.08</CloudDensity>"
        + _vec("SunColor", r=0.1, g=0.2, b=0.3, a=1)
        + _vec("HorizonColor", r=0.2, g=0.4, b=0.5, a=1)
        + _vec("ZenithColor", r=0.3, g=0.5, b=0.6, a=1)
        + _vec("CloudColor", r=0.9, g=0.9, b=0.9, a=1)
        + "</Atmosphere>"
        + _vec("Wind", x=0.2, y=0.98, z=wind_z, w=0)
        + _vec("Current", x=1, y=0, z=0.1, w=0.2)
        + "<BlurStrength>0.3</BlurStrength>"
        + _vec("LightDirection", x=-1, y=-1, z=0)
        + "<CamYRotOffset>2.6</CamYRotOffset>"
    )


AMBIENT = (
    "<AmbientMap><GlobalAreas><i><Name>North</Name><Position><x>0.25</x><y>0.75</y></Position>"
    "<Setting>North</Setting></i></GlobalAreas><Settings>"
    f"<North><Setting0>{_setting()}</Setting0><Setting1>{_setting(1.0, 0.5)}</Setting1></North>"
    f"<Night><Setting0>{_setting(0.5)}</Setting0></Night></Settings></AmbientMap>"
)


def _floats(*v: float) -> bytes:
    return b"CDATA[" + struct.pack("<I", 4 * len(v)) + struct.pack(f"<{len(v)}f", *v) + b"]"


LIGHT = (
    b"<m_LightInfo><m_Type>1</m_Type>\r\n<m_Diffuse>"
    + _floats(1.5, 1.5, 1.5, 1.5)
    + b"</m_Diffuse>\r\n<m_Ambient>"
    + _floats(0.75, 0.75, 0.75, 1.0)
    + b"</m_Ambient>\r\n<m_Specular>"
    + _floats(0, 0, 0, 1)
    + b"</m_Specular>\r\n<m_Direction><x>-0.1</x>\r\n<y>-0.5</y>\r\n<z>1.3</z>\r\n</m_Direction>\r\n"
    b"<m_CameraYRotOffset>2.6</m_CameraYRotOffset>\r\n</m_LightInfo>\r\n"
)


def test_ambient_sets_and_products():
    m = ambient2070.parse(AMBIENT)
    assert list(m.sets) == ["North", "Night"] and len(m.sets["North"]) == 2
    assert m.area("North").position == (0.25, 0.75) and m.area("North").setting == "North"
    s = m.setting("North")
    assert s.sun == (1.0, 0.5, 2.0) and s.ambient == (0.75, 0.75, 0.75) and s.mood == (0.4, 0.4, 0.4)
    assert (
        s.zenith == (0.3, 0.5, 0.6)
        and s.horizon == pytest.approx((0.4, 0.8, 1.0))
        and s.sky_sun[2] == pytest.approx(0.21)
    )
    assert s.wind == (0.2, 0.98, 0.17, 0.0) and m.setting("North", 1).wind[2] == 0.5
    assert s.sun_elevation == pytest.approx(45.0)  # a light travelling along (-1, -1, 0)
    assert s.water.under_absorption == (4, 5, 6) and s.water.under_light_depth == 36
    assert s.water.wave_velocity == ((0.01, 0.02), (0.03, 0.04))


def test_missing_tag_is_named():
    broken = AMBIENT.replace("<Choppiness>3.5</Choppiness>", "")
    with pytest.raises(KeyError, match="Choppiness"):
        ambient2070.parse(broken)


def test_read_from_a_tree(tmp_path: Path):
    for rel, data in {
        "Data0/data/config/engine/ambientmap.xml": AMBIENT.encode(),
        "Data0/data/config/engine/light.xml": LIGHT,
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    tree = SourceTree(tmp_path)
    assert ambient2070.read(tree).setting("Night").sun[0] == 0.25
    light = ambient2070.read_light(tree)
    assert (
        light.diffuse == (1.5, 1.5, 1.5, 1.5) and light.direction == (-0.1, -0.5, 1.3) and light.cam_y_rot_offset == 2.6
    )


PROPERTIES = """<Properties>
<Property><Name>ResidentBuildingUpgrade</Name>
 <ValueDefinition><DataType>Integer</DataType><Name>MinMorale</Name><Min>1</Min><Max>5</Max></ValueDefinition>
 <ValueDefinition><DataType>Integer</DataType><Name>Free</Name></ValueDefinition>
</Property>
<Property><Name>Other</Name>
 <ValueDefinition><DataType>Integer</DataType><Name>MinMorale</Name><Min>0</Min><Max>9</Max></ValueDefinition>
</Property>
<DefaultValues><Budget><StartBudget>4000</StartBudget><BudgetEvents /></Budget>
 <Feedback><HarbourUnits><Item><Behaviour>A</Behaviour></Item><Item><Behaviour>B</Behaviour></Item></HarbourUnits></Feedback>
</DefaultValues></Properties>"""
DATASETS = """<Datasets><DataSet><Name>ItemSocketQuality</Name><Items><Item><Name>All</Name></Item>
<Item><Name>A</Name></Item></Items></DataSet><DataSet><Name>ItemSocketQuality</Name><Items/></DataSet></Datasets>"""


def test_property_defaults(tmp_path: Path):
    for rel, text in {
        "Data0/data/config/ai/properties.xml": PROPERTIES,
        "Data0/data/config/game/datasets.xml": DATASETS,
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    tree = SourceTree(tmp_path)
    assert propdefaults2070.datasets(tree) == {"ItemSocketQuality": ("All", "A")}  # the first of a repeated name
    values = propdefaults2070.default_values(tree, propdefaults2070.AI_PROPERTIES)
    assert propdefaults2070.default(values, "Budget/StartBudget") == "4000"
    assert propdefaults2070.default(values, "Budget/BudgetEvents") is None  # declared empty: no default
    assert propdefaults2070.default(values, "Budget/Missing") is None
    assert values["Feedback/HarbourUnits/Item/Behaviour"] == ["A", "B"]
    ranges = propdefaults2070.value_range
    path = propdefaults2070.AI_PROPERTIES
    assert ranges(tree, path, "MinMorale", "ResidentBuildingUpgrade") == propdefaults2070.Range(1, 5)
    assert ranges(tree, path, "MinMorale").maximum == 5  # the first definition
    assert ranges(tree, path, "Free", "ResidentBuildingUpgrade") == propdefaults2070.Range(None, None)
    assert ranges(tree, path, "Nope") is None
