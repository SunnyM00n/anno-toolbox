import xml.etree.ElementTree as ET
from pathlib import Path

from annotoolbox.sources.assetdb import as_list
from annotoolbox.sources.overlay import CSourceTree
from annotoolbox.sources.toolone import CRepeatedList, element_to_data, load_database


def test_element_to_data_repeats_become_lists():
    el = ET.fromstring("<V><A>1</A><L><Item>x</Item><Item>y</Item></L><S><Item>only</Item></S></V>")
    data = element_to_data(el)
    assert data["A"] == "1"
    assert isinstance(data["L"]["Item"], CRepeatedList)
    assert data["L"]["Item"] == ["x", "y"]
    assert data["S"]["Item"] == "only"
    assert as_list(data["S"]["Item"]) == ["only"]


def test_load_database_reads_patched_assets_and_addon(source_root: Path):
    db = load_database(CSourceTree(source_root))
    assert db.m_sources == [
        "data/config/game/assets.xml",
        "addondata/config/balancing/addon_01_assets.xml",
    ]
    guids = sorted(a.guid for a in db.m_assets)
    assert guids == [10001, 10002, 10003, 20001]

    bakery = db.by_guid(10001)
    assert bakery is not None
    assert bakery.template == "FactoryBuilding"
    assert bakery.group_path == "Buildings/Production"
    assert bakery.get("WareProduction/Product") == "Bread"
    assert bakery.variation_files() == ["data\\graphics\\buildings\\bakery\\bakery.cfg"]

    two = db.by_guid(10002)
    assert two is not None
    assert len(two.variation_files()) == 2

    addon = db.by_guid(20001)
    assert addon is not None
    assert addon.group_path == "Addon"
    assert addon.source.endswith("addon_01_assets.xml")


def test_base_asset_inheritance(source_root: Path):
    db = load_database(CSourceTree(source_root))
    assert db.m_problems == []
    derived = db.by_guid(10003)
    assert derived is not None
    assert derived.inherits and derived.base_asset == 10001
    # Template comes from the base, overrides win, empty elements keep base values.
    assert derived.template == "FactoryBuilding"
    assert derived.get("WareProduction/Product") == "Cake"
    assert derived.variation_files() == ["data\\graphics\\buildings\\bakery\\bakery.cfg"]
    assert derived.name == "bakery_variant"
    assert derived.own_values is not None and derived.own_values["Object"] == ""
    # The base itself is untouched.
    assert db.by_guid(10001).get("WareProduction/Product") == "Bread"


def test_template_values_are_defaults_under_the_asset(source_root: Path):
    """ToolOne stores only what differs from the template, so the template fills the rest."""
    db = load_database(CSourceTree(source_root))
    bakery, two, derived = (db.by_guid(g) for g in (10001, 10002, 10003))
    assert bakery.get("Hitpoints/MaxHitpoints") == "450"  # the asset's own value wins
    assert bakery.get("Hitpoints/StartFireValue") == "200"  # the rest of the component is the template's
    assert two.get("Hitpoints/MaxHitpoints") == "300"
    assert derived.get("Hitpoints/MaxHitpoints") == "450"  # template < base < derived
    assert db.m_templates["FactoryBuilding"].properties["Hitpoints"]["MaxHitpoints"] == "300"  # not shared


def test_deep_merge_rules():
    from annotoolbox.sources.assetdb import deep_merge

    base = {"A": {"x": "1", "y": "2"}, "B": "keep", "C": {"n": "1"}}
    override = {"A": {"y": "3", "z": "4"}, "B": "", "C": "", "D": "new"}
    assert deep_merge(base, override) == {"A": {"x": "1", "y": "3", "z": "4"}, "B": "keep", "C": {"n": "1"}, "D": "new"}
    assert deep_merge("", {"k": "v"}) == {"k": "v"}
    assert deep_merge({"k": "v"}, "explicit") == "explicit"


def test_templates_and_properties(source_root: Path):
    db = load_database(CSourceTree(source_root))
    assert set(db.m_templates) == {"FactoryBuilding", "OrnamentBuilding"}
    assert db.m_templates["OrnamentBuilding"].group == ("Objects", "Nested")
    assert "WareProduction" in db.m_templates["FactoryBuilding"].properties

    prop = db.m_properties["WareProduction"]
    assert prop.group == ("Building",)
    assert [v.name for v in prop.values] == ["Product"]
    assert prop.values[0].data_type == "Choice"
    assert prop.values[0].extras["DataSet"] == "Product"
    assert prop.extras["HasImplementation"] == "1"
    assert db.template_counts() == {"FactoryBuilding": 3, "OrnamentBuilding": 1}


def test_a_guid_the_addon_redefines_is_one_asset_the_addons(source_root: Path):
    from annotoolbox.sources.assetdb import SAsset
    from annotoolbox.sources.toolone import last_definitions

    def asset(guid, source):
        return SAsset(guid, "n", "T", (), {}, source)

    base, addon, other, nameless = asset(340, "base"), asset(340, "addon"), asset(1, "base"), asset(None, "base")
    kept = last_definitions([base, other, nameless, addon])
    assert [a.source for a in kept] == ["addon", "base", "base"]  # at the base one's place, the add-on's definition
    assert kept[0] is addon
    db = load_database(CSourceTree(source_root))
    assert len({a.guid for a in db.m_assets}) == len([a for a in db.m_assets if a.guid is not None])
