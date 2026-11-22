"""117's enum reference: datasets.xml items with their Lua indexes, Lua-only stubs, engine enums (synthetic files)."""

from annotoolbox.sources import enums117

XML = """<DataSets>
  <Group><Name>Global</Name>
    <DataSet><Name>Region</Name><Id>279</Id><Items>
      <Item><Name>Meta</Name><Id>0</Id></Item>
      <Item><Name>Roman</Name><Id>7</Id><Description>the Roman one</Description></Item>
      <Item><Name>Egyptian</Name><Id>12</Id><SkipXmlExportInVersion>main;dlc01</SkipXmlExportInVersion></Item>
    </Items></DataSet>
  </Group>
  <Group><Name>Audio</Name>
    <DataSet><Name>Mood</Name><Id>3</Id><Description>a | b</Description><Items>
      <Item><Name>Calm</Name><Id>1</Id></Item>
    </Items></DataSet>
  </Group>
</DataSets>"""

REGION_LUA = """---@enum datasets.Region
datasets.Region = {
\tInvalid = -1,
    Meta = 0,
    Roman = 1,
    Egyptian = 3,
\tCount = 4
}
"""
EXTRA_LUA = "datasets.Hull = {\n    Bow = 0,\n    Stern = 1,\n}\n"
HALO_LUA = "---@enum halo.State\nhalo.State = {\n\tOn = 0x54A9BBE6,\n\tOff = 0x56306B4C,\n\n}\n"


def test_datasets_keep_groups_ids_descriptions_and_hidden_versions():
    region, mood = enums117.read_datasets(XML)
    assert (region.group, region.name, region.id) == ("Global", "Region", 279)
    assert [(i.name, i.id) for i in region.items] == [("Meta", 0), ("Roman", 7), ("Egyptian", 12)]
    assert region.items[1].description == "the Roman one"
    assert region.items[2].hidden_in == "main;dlc01"
    assert (mood.group, mood.description) == ("Audio", "a | b")


def test_lua_stub_values_including_hashes():
    region = enums117.read_lua_enum(REGION_LUA)
    assert (region.space, region.name) == ("datasets", "Region")
    assert dict(region.values) == {"Invalid": -1, "Meta": 0, "Roman": 1, "Egyptian": 3, "Count": 4}
    halo = enums117.read_lua_enum(HALO_LUA)
    assert (halo.space, dict(halo.values)["On"]) == ("halo", 0x54A9BBE6)
    assert enums117.read_lua_enum("-- no table here") is None


def test_lua_index_differs_from_data_id_and_lua_only_stubs_are_kept():
    datasets = enums117.read_datasets(XML)
    rest = enums117.attach_lua(datasets, [enums117.read_lua_enum(REGION_LUA), enums117.read_lua_enum(EXTRA_LUA)])
    roman = datasets[0].items[1]
    assert (roman.id, roman.lua) == (7, 1)
    assert datasets[1].items[0].lua is None  # no stub for Mood
    assert [s.name for s in rest] == ["Hull"]


def test_reference_marks_fixed_enums_and_escapes_cells():
    datasets = enums117.read_datasets(XML)
    rest = enums117.attach_lua(datasets, [enums117.read_lua_enum(REGION_LUA), enums117.read_lua_enum(EXTRA_LUA)])
    engine = [enums117.read_lua_enum(HALO_LUA)]
    md = enums117.render_markdown(datasets, rest, engine, "test build")
    assert "### Region" in md and "cannot grow by data" in md
    assert "| `Roman` | 7 | 1 | the Roman one |" in md
    assert "not exported in main;dlc01" in md
    assert "a \\| b" in md
    assert "### datasets.Hull" in md and "| `On` | 0x54A9BBE6 |" in md
    data = enums117.to_json(datasets, rest, engine, "test build")
    assert data["datasets"][0]["fixed"] and not data["datasets"][1]["fixed"]
    assert data["engine"][0]["values"]["Off"] == 0x56306B4C
