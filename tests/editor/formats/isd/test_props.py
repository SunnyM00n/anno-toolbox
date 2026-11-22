"""The properties at the top of a 2070-format island: read, checked, written, and carried by the edit file."""

from tests.formats.isdfakes import island_node as island

from annotoolbox.editor.formats.isd import props as isd_props
from annotoolbox.editor.formats.isd.edit import CIsland2070
from annotoolbox.formats import isd2070
from annotoolbox.formats.isd2070 import SNode


def names(root: SNode) -> list[str]:
    return [c.name for c in root.children]


def test_the_properties_are_read_as_numbers():
    assert isd_props.read(island("-28")) == {
        "width": 64, "height": 64, "clime": 0, "difficulty": 0, "sea_level": -28,
    }  # fmt: skip
    assert isd_props.read(island(None))["sea_level"] == 0  # absent: 0, as the game reads it


def test_a_value_no_island_of_the_game_has_is_named_and_not_written():
    assert not isd_props.check({"clime": 1, "difficulty": 1, "sea_level": -33})
    problems = isd_props.check({"clime": 2, "sea_level": -90, "colour": 1, "difficulty": 0.5})
    assert len(problems) == 4 and any("Clime" in p for p in problems) and any("colour" in p for p in problems)
    root = island()
    assert isd_props.apply(root, {"clime": 7, "colour": 1}) == 0
    assert isd_props.read(root)["clime"] == 0


def test_the_properties_are_set_on_the_parsed_island_and_survive_the_file():
    root = island("0")
    assert isd_props.apply(root, {"clime": 1, "sea_level": -28}) == 2
    back = isd2070.parse(isd2070.serialize(root))
    assert isd_props.read(back) == {"width": 64, "height": 64, "clime": 1, "difficulty": 0, "sea_level": -28}
    assert names(back) == names(root)  # nothing moved


def test_a_missing_sea_level_is_added_where_the_islands_that_have_one_keep_it():
    root = island(None)
    assert isd_props.apply(root, {"sea_level": -3}) == 1
    assert names(root)[names(root).index("RiverGrid") + 1] == "SeaLevel"
    back = isd2070.parse(isd2070.serialize(root))
    assert back.int("SeaLevel") == -3 and isd2070.CIsland.from_tree(back).m_sea_level == -3


def test_the_edit_file_key_goes_through_the_islands_edit_apply():
    isl = CIsland2070(isd2070.serialize(island("0")))
    report = isl.apply({"properties": {"difficulty": 1}}, {})
    assert report["properties"] == 1 and isl.m_root.int("Difficulty") == 1
    untouched = CIsland2070(isd2070.serialize(island("0")))
    assert untouched.apply({}, {})["properties"] == 0
    assert untouched.write() == isd2070.serialize(island("0"))  # an island without the key is its own bytes


def test_an_island_of_1404s_layout_has_nothing_invented_for_it():
    root = SNode("", children=[SNode("Width", "64"), SNode("Height", "64"), SNode("Rivers", children=[])])
    assert isd_props.editable(root) == []
    assert isd_props.apply(root, {"clime": 1, "sea_level": -3, "difficulty": 1}) == 0
    assert [c.name for c in root.children] == ["Width", "Height", "Rivers"]
    assert isd_props.editable(island(None)) == ["clime", "difficulty", "sea_level"]
