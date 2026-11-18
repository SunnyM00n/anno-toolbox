"""The map editor's leftovers on synthetic maps: the older games' random-map templates, three difficulties from one
map, mirrored copies, mine slots and per-area fertilities, island complexes moved as one, and their windows
(docs/map-editor.md)."""

from __future__ import annotations

import struct

import pytest
from tests.test_mapedit import ISLAND, STEM, _asset, _game, _h, fixed, start, template_bytes

from annotoolbox.formats import filedb, rda
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.mapedit import mapdoc, mapmod, placing, pool, rules, template, variants, worlds
from annotoolbox.mapedit.template import KIND_FIXED, KIND_SLOT, KIND_START, Element
from annotoolbox.sources import gametree

TWW_2070 = (
    b"<RandomMapTemplate><Name></Name>\r\n<Width>1792</Width>\r\n<Height>1792</Height>\r\n<IslandTemplates><k>1</k>\r\n"
    b"<v><hasValue>1</hasValue>\r\n<Island><Type>2</Type>\r\n<Rect><left>704</left>\r\n<top>1008</top>\r\n"
    b"<right>960</right>\r\n<bottom>1264</bottom>\r\n</Rect>\r\n<Climes>1</Climes>\r\n</Island>\r\n</v>\r\n<k>2</k>\r\n"
    b"<v><hasValue>1</hasValue>\r\n<Island><Type>1</Type>\r\n<Rect><left>1104</left>\r\n<top>368</top>\r\n"
    b"<right>1200</right>\r\n<bottom>464</bottom>\r\n</Rect>\r\n<Climes>2</Climes>\r\n</Island>\r\n</v>\r\n"
    b"</IslandTemplates>\r\n<ResourceProfiles><i></i>\r\n</ResourceProfiles>\r\n<PlayerPositions></PlayerPositions>\r\n"
    b"<PlayerPositionsEasymode></PlayerPositionsEasymode>\r\n</RandomMapTemplate>\r\n"
)
TWW_1701 = (
    b"<RandomMapTemplate><Name>Neue Welt</><Width>896</><Height>896</><IslandTemplates><k>13</><v><hasValue>1</>"
    b"<Island><Rect><left>80</><top>64</><right>144</><bottom>128</></><Climes>2</><Cultures>36</><Fertilities>113</>"
    b"<Resources>14</></></><k>8</><v><hasValue>1</><Island><Rect><left>32</><top>544</><right>128</><bottom>640</></>"
    b"<Climes>3</><Cultures>0</><Fertilities>113</><Resources>14</></></></><Islands></>"
    b"<PlayerPositions><i><x>412</><y>483</></></><HIDObjectConfigs></></>"
)


def test_a_random_map_template_reads_edits_and_writes_back():
    w = worlds.read(TWW_2070, "anno2070")
    assert w.random and w.render() == TWW_2070
    assert [(e.kind, e.x, e.z, e.size, e.type, e.clime) for e in w.elements] == [
        (KIND_SLOT, 704, 1008, 5, 2, 1), (KIND_SLOT, 1104, 368, 0, 1, 2)
    ]  # fmt: skip
    assert w.extent(w.elements[0]) == 256 and w.extent(w.elements[1]) == 96
    w.elements[1].x, w.elements[1].size, w.elements[1].clime = 1200, 2, 3  # 144 tiles
    w.elements.append(template.new_element(KIND_SLOT, 300, 300, size=1, type=0))
    w.elements.append(template.new_element(KIND_START, 500, 600))
    back = worlds.read(w.render(), "anno2070")
    assert [(e.x, e.z, back.extent(e), e.clime) for e in back.elements if e.kind == KIND_SLOT] == [
        (704, 1008, 256, 1), (1200, 368, 144, 3), (304, 304, 128, 3)
    ]  # fmt: skip  # the new one on the 8-tile grid, with the climate of the entry it copies
    assert [(e.x, e.z) for e in back.elements if e.kind == KIND_START] == [(496, 600)]
    keys = [c.text for c in back.config.find("IslandTemplates").children if c.name == "k"]
    assert keys == ["1", "2", "3"]


def test_an_1701_random_map_template_keeps_its_short_tags_and_bits():
    w = worlds.read(TWW_1701, "anno1701")
    assert w.render() == TWW_1701
    assert [(e.x, e.z, w.extent(e), e.clime) for e in w.elements if e.kind == KIND_SLOT] == [
        (80, 64, 64, 2),
        (32, 544, 96, 3),
    ]
    w.elements[0].x = 88
    out = w.render()
    assert b"<left>88</><top>64</><right>152</>" in out and b"<Cultures>36</>" in out
    first = w.elements[0]
    assert (first.cultures, first.fertilities, first.resources) == (36, 113, 14)
    first.cultures, first.resources = 4, 15  # the bits are fields of the placeholder
    out = w.render()
    assert b"<Cultures>4</><Fertilities>113</><Resources>15</>" in out and b"<Cultures>0</>" in out


def test_the_maps_app_lists_and_opens_random_map_templates(tmp_path):
    (tmp_path / "data" / "levels").mkdir(parents=True)
    (tmp_path / "data" / "levels" / "r.tww").write_bytes(TWW_2070)
    tree = gametree.loose_tree(tmp_path, "2070")
    assert mapdoc.map_paths(tree, "anno2070") == ["data/levels/r.tww"]
    doc = mapdoc.open_map(tree, "anno2070", "data/levels/r.tww", pool.GameMaps(game="anno2070"))
    assert doc.size_names()[0] == "96 tiles" and doc.extent(doc.elements[0]) == 256
    assert not doc.changed()


# three difficulties
def _medium() -> list[Element]:
    """A medium layout as 117's chain map: Normal slots Small, Medium, Large 6, 4, 2, four XL starters, a pirate."""
    out = [template.new_element(KIND_SLOT, 64 + 512 * (i % 4), 64 + 512 * (i // 4), size=s)
           for i, s in enumerate([0] * 6 + [1] * 4 + [2] * 2)]  # fmt: skip
    out += [template.new_element(KIND_SLOT, 200 + 400 * i, 1800, size=3, type=1) for i in range(4)]
    out.append(template.new_element(KIND_SLOT, 1900, 1900, size=1, type=4))
    return out


CLASSES = {0: 256, 1: 320, 2: 432, 3: 432}


def _sizes(elements, type_id=0) -> list[int]:
    return [sum(1 for e in elements if e.kind == KIND_SLOT and e.type == type_id and e.size == s) for s in (0, 1, 2, 3)]


def test_easy_and_hard_move_the_pool_slots_a_size_as_vanilla_does():
    medium = _medium()
    assert _sizes(variants.derive(medium, "medium", CLASSES)) == [6, 4, 2, 0]
    easy, hard = variants.derive(medium, "easy", CLASSES), variants.derive(medium, "hard", CLASSES)
    assert _sizes(easy) == [4, 4, 4, 0] and _sizes(hard) == [8, 4, 0, 0]
    assert _sizes(easy, 1) == [0, 0, 0, 4] and _sizes(hard, 1) == [0, 0, 4, 0]  # hard: the XL starters Large
    assert _sizes(hard, 4) == [0, 1, 0, 0]  # the pirate as it was
    grown = next(i for i, (a, b) in enumerate(zip(medium, easy, strict=True)) if a.size != b.size)
    a, b = medium[grown], easy[grown]
    centre = (a.x + CLASSES[a.size] / 2, a.z + CLASSES[a.size] / 2)
    assert abs(b.x + CLASSES[b.size] / 2 - centre[0]) <= 4 and abs(b.z + CLASSES[b.size] / 2 - centre[1]) <= 4
    assert [e.size for e in medium] == [e.size for e in _medium()]  # the medium map itself is untouched
    with pytest.raises(ValueError):
        variants.derive(medium, "nightmare", CLASSES)


def test_the_three_difficulties_are_written_as_new_maps_or_over_the_siblings(tmp_path):
    tree = _game(tmp_path / "game")
    maps = pool.GameMaps.read(tree, "anno117")
    doc = mapdoc.open_map(tree, "anno117", STEM + ".a7tinfo", maps)
    guids = {"easy": 2_100_000_001, "medium": 2_100_000_002, "hard": 2_100_000_003}
    files, findings = mapmod.variant_files(doc, mapmod.Choice(True, "isles", 0, "m"), guids)
    for level in ("easy", "medium", "hard"):
        assert f"data/mods/m/maps/isles_{level}/isles_{level}.a7tinfo" in files and level in findings
    ops = files[pool.ASSET_FILES["anno117"]].decode()
    assert "<GUID>2100000001</GUID>" in ops and ops.count("<ModOp ") == 3
    assert "<IslandSize>Large</IslandSize>" in ops or "IslandSize" not in _asset()
    with pytest.raises(ValueError):  # the synthetic map has no vanilla easy and hard beside it
        mapmod.variant_files(doc, mapmod.Choice(), {})
    for level in ("easy", "hard"):
        sib = variants.sibling(STEM.replace("t_01", "t_01_medium") + ".a7tinfo", level)
        assert sib.endswith(f"t_01_{level}/t_01_{level}.a7tinfo")
    doc.grow((64, 0, 0, 0))
    with pytest.raises(ValueError):
        mapmod.variant_files(doc, mapmod.Choice(), {})


def test_a_new_maps_island_size_is_the_levels():
    asset = _asset().replace("<TemplateRegion>", "<IslandSize>Medium</IslandSize><TemplateRegion>")

    class Doc:
        path, grown, maps = STEM + ".a7tinfo", None, pool.GameMaps("anno117", templates=pool.read_templates(asset))

        def is_enlarged(self):
            return False

    op = mapmod.new_asset_op(Doc(), mapmod.Choice(True, "x_hard", 5, "m"), "x.a7t", "Small")
    assert "<IslandSize>Small</IslandSize>" in op and "<GUID>5</GUID>" in op


# mirrored copies and complexes
def test_mirrored_copies_mirror_slots_and_starts_and_turn_islands_twice():
    centre = (1024.0, 1024.0)
    s = template.new_element(KIND_SLOT, 200, 304, size=1)
    i = template.new_element(KIND_FIXED, 400, 400, map_file_path=ISLAND, rotation90=1)
    p = template.new_element(KIND_START, 104, 512)
    edge = {KIND_SLOT: 320, KIND_FIXED: 192, KIND_START: 0}
    x = placing.mirror_copies([s, i, p], "x", centre, lambda e: edge[e.kind])
    assert [(e.x, e.z, e.rotation90) for e in x] == [(1528, 304, 0), (1456, 400, 3), (1944, 512, 0)]
    pt = placing.mirror_copies([s], "point", centre, lambda e: 320)[0]
    assert (pt.x, pt.z) == (1528, 1424)
    assert len(placing.mirror_copies([s, p], "xz", centre, lambda e: edge[e.kind])) == 6


def _complex_doc(tmp_path, second: tuple[int, int] = (904, 880)):
    """A map with two overlapping fixed islands (a complex) and one apart."""
    els = [fixed(800, 800), fixed(*second), fixed(100, 100), start(1500, 1500)]
    path = tmp_path / "data" / "t.a7tinfo"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(template_bytes(elements=els))
    tree = _game(tmp_path)
    return mapdoc.open_map(tree, "anno117", "data/t.a7tinfo", pool.GameMaps.read(tree, "anno117"))


def test_a_complex_is_found_moved_and_turned_as_one_and_its_seams_are_not_flagged(tmp_path):
    doc = _complex_doc(tmp_path)
    assert doc.complexes() == [[0, 1]] and placing.complex_of(0, doc.complexes()) == [0, 1]
    overlap = [f for f in doc.check() if "overlap" in f.text]
    assert not overlap  # their grids and active areas meet by design
    doc.rotate([0, 1])
    assert [e.rotation90 for e in doc.elements[:2]] == [1, 1] and not [f for f in doc.check() if "overlap" in f.text]
    doc.move([0, 1], 64, 64)
    assert not [f for f in doc.check() if "overlap" in f.text]
    doc.move([1], 0, 24)  # one island moved alone: the seam is gone, the overlap is named
    assert [f for f in doc.check() if "overlap" in f.text]
    copies = doc.duplicate([0, 1])
    assert copies == [4, 5] and doc.elements[4].rotation90 == 1


def test_mirrored_in_place_and_the_undo(tmp_path):
    doc = _complex_doc(tmp_path)
    before = [(e.x, e.z, e.rotation90) for e in doc.elements]
    assert doc.mirror([2], "z", copies=False) == [2]
    assert doc.elements[2].z != 100 and doc.elements[2].rotation90 == 2
    doc.undo()
    assert [(e.x, e.z, e.rotation90) for e in doc.elements] == before


# mine slots and per-area fertilities
def _with_maps(name: str, value: str) -> Tag:
    el = fixed(800, 800)
    e = el.find("Element")
    e.children = [c for c in e.children if c.name != "MineSlotActivation"]
    pairs = [Attrib("#32768", struct.pack("<Q", 0x8001000000000005)), Attrib("#32768", struct.pack(value, 1))]
    e.children.append(Tag(name, pairs))
    if name == template.MINE_SLOT_ACTIVATION:
        e.children.append(Tag(template.AREA_SETS, [Attrib("#32768", _h(1)), Attrib("#32768", struct.pack("<I", 3656))]))
        e.children.append(
            Tag(template.AREA_FERTILITIES, [Attrib("#32768", _h(1)), Attrib("#32768", struct.pack("<2I", 7, 8))])
        )
    return el


@pytest.mark.parametrize("name, value", [(template.MINE_SLOT_ACTIVATION, "<B"), (template.MINE_SLOT_MAPPING, "<I")])
def test_mine_slots_and_areas_read_edit_and_write_back(name, value):
    raw = template_bytes(elements=[_with_maps(name, value), start(10, 10)])
    t = template.read(raw)
    e = t.elements[0]
    assert e.mine_slot_activation == ((0x8001000000000005, 1),) and t.render() == raw
    if name == template.MINE_SLOT_ACTIVATION:
        assert e.fertility_set_guids == ((1, 3656),) and e.fertilities_per_area_index == ((1, (7, 8)),)
        e.fertility_set_guids, e.fertilities_per_area_index = ((1, 14198), (4, 1)), ((4, (9,)),)
    e.mine_slot_activation = ((0x8001000000000005, 0), (0x8001000000000006, 1))
    back = template.read(t.render()).elements[0]
    assert back.mine_slot_activation == e.mine_slot_activation
    if name == template.MINE_SLOT_ACTIVATION:
        assert back.fertility_set_guids == ((1, 14198), (4, 1)) and back.fertilities_per_area_index == ((4, (9,)),)
    else:
        assert back.tag.path("Element/FertilitySetGUIDs") is None  # 1800 gets no 117 list


def test_a_new_island_made_from_a_model_and_a_swapped_file_drop_the_old_mine_slots(tmp_path):
    raw = template_bytes(elements=[_with_maps(template.MINE_SLOT_ACTIVATION, "<B"), start(10, 10)])
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "m.a7tinfo").write_bytes(raw)
    tree = _game(tmp_path)
    doc = mapdoc.open_map(tree, "anno117", "data/m.a7tinfo", pool.GameMaps.read(tree, "anno117"))
    doc.add(template.new_element(KIND_FIXED, 1200, 1200, map_file_path=ISLAND))
    back = template.read(doc.render())
    assert back.elements[2].mine_slot_activation == () and back.elements[0].mine_slot_activation
    doc.set_values(0, {"map_file_path": "data/other.a7m"})
    assert doc.elements[0].mine_slot_activation == () and doc.elements[0].fertility_set_guids == ()
    doc.undo()
    assert doc.elements[0].mine_slot_activation


def _island_archive() -> bytes:
    objects = [Tag("#1", [Attrib("ID", struct.pack("<Q", 0x80010000000000AA)), Attrib("guid", struct.pack("<I", 1000029)),
                          Attrib("Position", struct.pack("<3f", 1, 2, 3))]),
               Tag("#1", [Attrib("ID", struct.pack("<Q", 0x80010000000000AB)), Attrib("guid", struct.pack("<I", 42)),
                          Attrib("Position", struct.pack("<3f", 1, 2, 3))])]  # fmt: skip
    area = filedb.write(Tag("", [Tag("AreaObjectManager", [Tag("GameObject", [Tag("objects", objects)])])]))
    amd = Tag("AreaManagerData", [Tag("#1", [Attrib("Data", area)])])
    return rda.write_v22({"gamedata.data": filedb.write(Tag("", [Tag("GameSessionManager", [amd])]))})


def test_an_islands_slot_objects_are_read_from_its_archive(tmp_path):
    (tmp_path / "i.a7m").write_bytes(_island_archive())
    tree = gametree.loose_tree(tmp_path, "1800")
    slots = pool.island_slots(tree, "i.a7m", {1000029: "random slot mining"})
    assert slots == [pool.SlotObject(0x80010000000000AA, 1000029, "random slot mining")]
    assert pool.island_slots(tree, "missing.a7m", {}) == []
    assert pool.named_assets(
        "<Asset><Template>Slot</Template><Values><Standard><GUID>9</GUID><Name>s</Name></Standard></Values></Asset>",
        "Slot",
    ) == {9: "s"}


def test_the_checks_name_a_mirrored_complex_that_no_longer_meets(tmp_path):
    doc = _complex_doc(tmp_path)
    doc.mirror([0, 1], "x")  # islands cannot be mirrored: the copies' seam is not the designed one
    found = rules.check(doc.facts())
    assert any("overlap" in f.text and set(f.elements) == {4, 5} for f in found)


def test_a_complex_taken_from_another_map_keeps_its_seams(tmp_path):
    doc = _complex_doc(tmp_path)
    other = _complex_doc(tmp_path / "other", second=(904, 920))  # another pair: a seam this map does not know
    copies = [other.elements[i].copy() for i in other.complexes()[0]]
    for c in copies:
        c.x, c.z = c.x + 600, c.z - 640
    doc.add_all(copies)
    assert [f for f in doc.check() if "overlap" in f.text]
    doc.learn_seams(copies)
    assert not [f for f in doc.check() if "overlap" in f.text]
