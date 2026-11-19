"""The slot assets and objects of the island editor's Slots tab (``slotkinds``) and the slot links of the 117 <-> 1800 port
(``slotlinks``, docs/island-regions.md "Slots between the games"): the assets read from asset text, the rules' suggestions
both ways, the saved links, and the port's object table written by them."""

import struct
from collections import Counter
from types import SimpleNamespace

import pytest

from annotoolbox.editor import gametransfer, slotkinds, slotlinks
from annotoolbox.editor.assetmodels import AssetModels
from annotoolbox.formats.filedb import Attrib, Tag
from fakes import asset_xml


def slot(template, guid, name, slot_type="", product=0, becomes=()):
    """A slot asset or slot building: its ``SlotType``, the product its building makes, the slots a random slot becomes."""
    body = f"<Slot><SlotType>{slot_type}</SlotType></Slot>" if slot_type else ""
    if product:
        body += f"<FactoryBase><FactoryOutputs><Item><Product>{product}</Product></Item></FactoryOutputs></FactoryBase>"
    if becomes:
        items = "".join(f"<Item><NewAsset>{g}</NewAsset></Item>" for g in becomes)
        body += f"<RandomMapObject><Replacements>{items}</Replacements></RandomMapObject>"
    return asset_xml(template, guid, name, body)


def models(*assets: str) -> AssetModels:
    return AssetModels(("<Assets>" + "".join(assets) + "</Assets>").encode("utf-8"))


G117 = models(
    slot("Slot", 2882, "Slot Roman Mountain", "Mountain"),
    slot("Slot", 5281, "Slot Celtic Mountain", "Mountain"),
    slot("Slot", 2969, "Slot Roman River", "River"),
    slot("Slot_Marsh", 5282, "Slot Celtic Marsh"),
    slot("WorkAreaSlot", 26668, "Workareaslot Mountain", "WorkArea"),
    slot("WorkAreaSlot", 27877, "Workareaslot Roman River", "WorkArea"),
    slot("SlotFactoryBuilding7", 2918, "Production Mountain Roman Iron Ore", "Mountain", 2080),
    slot("SlotFactoryBuilding7", 144810, "Production Mountain Roman Coal", "Mountain", 2085),
    slot("SlotFactoryBuilding7", 2954, "Production Mountain Roman Marble Blocks", "Mountain", 2084),
    slot("SlotFactoryBuilding7", 2970, "Production River Roman Clay", "River", 2086),
    slot("SlotFactoryBuilding7", 3000, "Production River Roman Olive Oil", "River", 2087),
    asset_xml("Product", 2080, "Good Iron Ore"),
    asset_xml("Product", 2085, "Good Charcoal"),
    asset_xml("Product", 2084, "Good Marble Blocks"),
    asset_xml("Product", 2086, "Good Clay"),
    asset_xml("Product", 2087, "Good Olive Oil"),
    asset_xml("Building", 7, "A House"),
)
G1800 = models(
    slot("Slot", 1010500, "mining_01_slot (Coal Mine Slot)", "Coal"),
    slot("Slot", 1010501, "mining_02_slot (Iron Mine Slot)", "Iron"),
    slot("Slot", 101061, "mining_03_slot (Clay Pit Slot)", "Clay"),
    slot("Slot", 102498, "heavy_01_01_slot (Oil Pump Slot)", "Oil"),
    slot("Slot", 1000029, "random slot mining", "Random", becomes=(1010500, 1010501)),
    slot("Slot", 100417, "random slot clay", "Random", becomes=(101061,)),
    slot("Slot", 100849, "random slot oil pump", "Random", becomes=(102498,)),
    slot("Slot", 117741, "Riverslot", "River"),
    slot("WorkAreaSlot", 1000253, "slot mining workarea mountain", "WorkAreaMountain"),
    slot("WorkAreaRiverSlot", 117742, "Riverslot_workarea", "WorkAreaMountain"),
    slot("ExpeditionSlot", 800384, "Arctic_Expedition01"),
    slot("SlotFactoryBuilding7", 1010304, "mining_01 (Coal Mine)", "Coal", 1010226),
    slot("SlotFactoryBuilding7", 1010305, "mining_02 (Iron Mine)", "Iron", 1010227),
    slot("SlotFactoryBuilding7", 100416, "factory_11 (Clay Pit)", "Clay", 1010201),
    asset_xml("Product", 1010226, "coal"),
    asset_xml("Product", 1010227, "iron"),
    asset_xml("Product", 1010201, "clay"),
)


def test_the_slot_assets_are_read_by_template_with_their_slot_type():
    found = slotkinds.read_assets(G117)
    assert sorted(found) == [2882, 2969, 5281, 5282, 26668, 27877]  # no slot building, no house
    assert found[2882] == slotkinds.SlotAsset(2882, "Slot Roman Mountain", "Slot", "Mountain")
    assert found[5282].kind == "Slot Celtic Marsh"  # no slot type: the name
    assert found[26668].family == "workarea" and found[2882].family == "slot"
    assert 800384 not in slotkinds.read_assets(G1800)  # an expedition slot is no island slot
    assert slotkinds.words_of("WorkAreaRiverSlot", "Riverslot_workarea") >= {"work", "area", "river", "slot"}


def entry(guid, x, kind="object", alive=True):
    return SimpleNamespace(kind=kind, base={"guid": guid}, position=[x, 0.0, 2.0], alive=alive, visible=True)


def test_the_tab_lists_the_standing_slot_objects_and_filters_them():
    assets = slotkinds.read_assets(G117)
    entries = [entry(2882, 1), entry(7, 2), entry(2969, 3), entry(2882, 4, alive=False), entry(2882, 5, kind="prop")]
    listed = slotkinds.rows(entries, assets)
    assert [r.position[0] for r in listed] == [1.0, 3.0]
    assert [r.asset.kind for r in listed if slotkinds.matches(r, kind="River")] == ["River"]
    assert len([r for r in listed if slotkinds.matches(r, "roman")]) == 2
    assert [r for r in listed if slotkinds.matches(r, "2969")] == [listed[1]]
    assert len([r for r in listed if slotkinds.matches(r, "no such")]) == 0


def sides():
    a117, a1800 = slotkinds.read_assets(G117), slotkinds.read_assets(G1800)
    return a117, slotlinks.Resources.read(G117), a1800, slotlinks.Resources.read(G1800)


def test_1800_slots_link_to_117_by_the_product_their_buildings_make_never_by_a_word():
    a117, r117, a1800, r1800 = sides()
    assert r1800.products == {"Coal": {"coal"}, "Iron": {"iron"}, "Clay": {"clay"}}  # the product assets' own names
    assert r117.products["Mountain"] == {"iron ore", "charcoal", "marble blocks"}  # "Good " taken off
    links = slotlinks.suggest(a1800, a117, r1800, r117, "roman")
    assert links[101061] == 2969 and links[100417] == 2969  # clay and the random clay slot: 117's clay is made on River
    assert links[1010500] is None  # "coal" is not "charcoal": 117 burns it in a kiln, no slot makes it
    assert links[1010501] == 2882  # "iron" is "iron ore" by the table of equivalent goods, made on Mountain
    assert {row[0] for row in slotlinks.EQUIVALENT_GOODS} == {"iron", "copper"}  # only materials both games have
    assert slotlinks.PARTNERS["iron ore"] == {"iron"} and "coal" not in slotlinks.PARTNERS
    assert links[117741] == 2969  # the same slot type
    assert links[1000029] == 2882  # a random slot of several resources: 117's generic slot, the Mountain
    assert links[1000253] == 26668 and links[117742] == 27877  # a work area slot by its mountain or river
    assert slotlinks.suggest(a1800, a117, r1800, r117, "celtic")[1000029] == 5281  # the region's own wins among equals


def test_crude_oil_and_mud_are_left_out_by_the_owners_rule_whatever_the_names_hold():
    a117, r117, a1800, r1800 = sides()
    to117 = slotlinks.suggest(a1800, a117, r1800, r117)
    assert to117[102498] is None and to117[100849] is None  # the oil slot and the random slot that only becomes oil
    assert slotlinks.suggest(a117, a1800, r117, r1800)[5282] is None  # 117's marsh (mud) slot


def test_117_slots_link_to_1800_generic_slots_and_a_marsh_has_none():
    a117, r117, a1800, r1800 = sides()
    links = slotlinks.suggest(a117, a1800, r117, r1800)
    assert links[2882] == 1000029  # a mountain slot takes any mining building: 1800's random mining slot
    assert links[2969] == 117741  # River to River
    assert links[5282] is None and links[26668] == 1000253 and links[27877] == 117742


def test_the_persons_links_win_and_only_their_differences_are_kept(tmp_path):
    suggested = {1: 10, 2: None, 3: 30}
    saved = {1: 11, 2: 22}
    assert slotlinks.resolve(suggested, saved) == {1: 11, 2: 22, 3: 30}
    assert slotlinks.resolve(suggested, {3: None})[3] is None  # left out on purpose
    chosen = {1: 10, 2: None, 3: None}  # back to the suggestion; the suggestion; a new choice
    assert slotlinks.merge_saved(saved, chosen, suggested) == {3: None}
    assert slotlinks.merge_saved({9: 90}, {1: 12}, suggested) == {9: 90, 1: 12}  # other slots stay
    path = slotlinks.path_for(tmp_path, "anno1800", "anno117")
    slotlinks.save(path, {5: None, 1: 7})
    assert slotlinks.load(path) == {1: 7, 5: None}
    assert slotlinks.load(tmp_path / "missing.json") == {}
    path.write_text("not json", encoding="utf-8")
    assert slotlinks.load(path) == {}


def test_the_links_go_into_the_object_table_and_slots_without_one_are_listed():
    a117, _, a1800, _ = sides()
    table = {101061: 12345, 77: None}  # a slot the name rule mapped, an object that is no slot
    used = Counter({101061: 3, 102498: 2, 7: 9})
    links = {101061: 2969, 102498: None}
    assert slotlinks.apply(table, links, used, a1800) == 3
    assert table == {101061: 2969, 77: None, 102498: None}  # the link over the name rule; 7 is no slot
    assert slotlinks.unlinked(used, links, a1800) == [
        {"guid": 102498, "name": "heavy_01_01_slot (Oil Pump Slot)", "count": 2}
    ]


def test_a_port_writes_the_links_into_its_tables_and_reports_what_it_left_out(monkeypatch):
    a117, r117, a1800, r1800 = sides()
    objects = [Tag("o", [Attrib("guid", struct.pack("<I", g))]) for g in (101061, 101061, 102498, 9)]
    monkeypatch.setattr(gametransfer.a7mread, "object_tags", lambda gm, groups: (("GameObject", o) for o in objects))
    src = SimpleNamespace(slots=lambda: (a1800, r1800))
    dst = SimpleNamespace(slots=lambda: (a117, r117))
    tables = SimpleNamespace(objects={})
    target = SimpleNamespace(island_region="Roman")
    report = gametransfer.link_slots(src, dst, SimpleNamespace(gm=None), tables, target, {})
    assert tables.objects == {101061: 2969, 102498: None}
    assert report == {
        "slots linked": 2,
        "slots unlinked": [{"guid": 102498, "name": "heavy_01_01_slot (Oil Pump Slot)", "count": 1}],
    }
    saved = {102498: 2969}  # the person links the oil slot, and leaves the coal slot out
    tables = SimpleNamespace(objects={})
    report = gametransfer.link_slots(src, dst, SimpleNamespace(gm=None), tables, target, saved | {101061: None})
    assert tables.objects == {101061: None, 102498: 2969}
    assert [s["guid"] for s in report["slots unlinked"]] == [101061]
    plain = SimpleNamespace(slots=lambda: ({}, r1800))  # an island of a game with no slots: nothing to link
    assert gametransfer.link_slots(plain, dst, SimpleNamespace(gm=None), SimpleNamespace(objects={}), target, {}) == {}


def test_the_slots_tab_lists_filters_and_selects_like_the_objects_list(monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    from annotoolbox.editor.desktop.desktopslotstab import SlotsPanel

    app = widgets.QApplication.instance() or widgets.QApplication([])
    picked = []
    view = SimpleNamespace(
        tool="select", select=lambda e: picked.append(("select", e)), focus_on=lambda es: picked.append(("focus", es))
    )
    entries = [entry(2882, 1.0), entry(2969, 3.0), entry(7, 2.0)]
    doc = SimpleNamespace(
        game_key=lambda: "anno117",
        session=SimpleNamespace(entries=entries),
        editor=SimpleNamespace(slot_assets=lambda: slotkinds.read_assets(G117)),
        view=view,
    )
    panel = SlotsPanel(doc)
    panel.fill()
    assert panel.tree.topLevelItemCount() == 2 and panel.kinds.count() == 3  # all kinds, Mountain, River
    assert panel.tree.topLevelItem(0).text(2) == "2882"
    panel.search.setText("river")
    assert panel.tree.topLevelItemCount() == 1
    panel._click(panel.tree.topLevelItem(0))
    assert picked == [("select", entries[1]), ("focus", [entries[1]])]
    doc.game_key = lambda: "anno2070"
    panel.fill()
    assert panel.tree.topLevelItemCount() == 0 and panel.note.text() and not panel.links.isVisibleTo(panel)
    del app
