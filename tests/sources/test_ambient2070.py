"""The ambient-map, light and tool-default readers, on small files written for the test (no game files)."""

from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest
from tests.sources.sourcesfakes import AMBIENT

from annotoolbox.sources import ambient2070, propdefaults2070
from annotoolbox.sources.overlay import CSourceTree


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


def test_a_missing_vector_or_block_is_named_not_an_attribute_error():
    for tag, hint in (("Wind", "Wind"), ("Atmosphere", "Atmosphere"), ("WaveFrequency", "WaveFrequency")):
        broken = re.sub(rf"<{tag}>.*?</{tag}>", "", AMBIENT, flags=re.S)
        with pytest.raises(KeyError, match=hint):
            ambient2070.parse(broken)
    with pytest.raises(KeyError, match="Nowhere"):
        ambient2070.parse(AMBIENT).area("Nowhere")


def test_read_from_a_tree(tmp_path: Path):
    for rel, data in {
        "Data0/data/config/engine/ambientmap.xml": AMBIENT.encode(),
        "Data0/data/config/engine/light.xml": LIGHT,
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    tree = CSourceTree(tmp_path)
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
    tree = CSourceTree(tmp_path)
    assert propdefaults2070.datasets(tree) == {"ItemSocketQuality": ("All", "A")}  # the first of a repeated name
    values = propdefaults2070.default_values(tree, propdefaults2070.AI_PROPERTIES)
    assert propdefaults2070.default(values, "Budget/StartBudget") == "4000"
    assert propdefaults2070.default(values, "Budget/BudgetEvents") is None  # declared empty: no default
    assert propdefaults2070.default(values, "Budget/Missing") is None
    text = tree.read_text(propdefaults2070.AI_PROPERTIES, "latin-1")
    assert propdefaults2070.default_values_in(text) == values  # the same over a text already read
    assert propdefaults2070.default_values_in("") == {} and propdefaults2070.default_values_in("<not xml") == {}
    assert values["Feedback/HarbourUnits/Item/Behaviour"] == ["A", "B"]
    ranges = propdefaults2070.value_range
    path = propdefaults2070.AI_PROPERTIES
    assert ranges(tree, path, "MinMorale", "ResidentBuildingUpgrade") == propdefaults2070.SRange(1, 5)
    assert ranges(tree, path, "MinMorale").maximum == 5  # the first definition
    assert ranges(tree, path, "Free", "ResidentBuildingUpgrade") == propdefaults2070.SRange(None, None)
    assert ranges(tree, path, "Nope") is None
