"""Placing buildings from a game's asset list: the list (``placeables``), the footprints of the old engine, the live
rules (``placedrules.check_place``) and, per game, a building placed, moved, written back and read again."""

import base64
import math
import struct

import numpy as np
import pytest

from annotoolbox.editor import assetmodels, footprints, objectclones, placeables, placedrules, templatedonors
from annotoolbox.editor.editchange import yaw_quat
from annotoolbox.editor.editsession import EditSession
from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.editor.formats.a7m import edit2205 as a7m_edit2205
from annotoolbox.editor.formats.isc import edit as isc_edit
from annotoolbox.editor.formats.isd import edit as isd_edit
from annotoolbox.formats import filedb, isd2070
from annotoolbox.formats.isd2070 import Node
from annotoolbox.islands import islandedits, objects2070
from annotoolbox.othergames import isd1701
from fakes import FakeFiles, FakeTree
from test_isd1701 import isd_bytes as isd1701_bytes
from test_island2070edit import ASSETS as ASSETS_2070
from test_island2070edit import isd_bytes as isd2070_bytes
from test_island2205edit import _edits as edits2205
from test_island2205edit import _objects, _session2205
from test_placedobjects import _game_object, _opened, _opened_tags, _synthetic_file

ASSETS_117 = b"""<AssetList><Groups><Group><Assets>
<Asset><Template>Production</Template><Values><Standard><GUID>2955</GUID><Name>Sardines</Name>
<IconFilename>data/ui/icon_sardines.png</IconFilename></Standard>
<Building><TerrainType2>Coast</TerrainType2></Building>
<Object><Variations><Item><Filename>data/b/sardines.cfg</Filename></Item></Variations></Object></Values></Asset>
<Asset><Template>Residence</Template><Values><Standard><GUID>3000</GUID><Name>House</Name></Standard>
<Object><Variations><Item><Filename>data/b/house_a.cfg</Filename></Item><Item><Filename>data/b/house_b.cfg</Filename>
</Item></Variations></Object></Values></Asset>
<Asset><BaseAssetGUID>3000</BaseAssetGUID><Values><Standard><GUID>3001</GUID><Name>House Celtic</Name></Standard>
</Values></Asset>
<Asset><Template>Decoration</Template><Values><Standard><GUID>4000</GUID><Name>Bench</Name></Standard>
<Object><Variations><Item><Filename>data/b/bench.cfg</Filename></Item></Variations></Object></Values></Asset>
<Asset><Template>Marker</Template><Values><Standard><GUID>4100</GUID><Name>Dummy</Name></Standard>
<Object><Variations><Item><Filename>data/dev/graphics/tools/templates/empty.cfg</Filename></Item></Variations></Object>
</Values></Asset>
<Asset><Template>ConstructionCategory</Template><Values><Standard><GUID>9000</GUID><Name>Roman</Name></Standard>
<ConstructionCategory><BuildingList><Item><Building>9001</Building></Item></BuildingList></ConstructionCategory>
</Values></Asset>
<Asset><Template>ConstructionCategory</Template><Values><Standard><GUID>9001</GUID><Name>Production</Name></Standard>
<ConstructionCategory><BuildingList><Item><Building>2955</Building></Item></BuildingList></ConstructionCategory>
</Values></Asset>
</Assets></Group></Groups></AssetList>"""
TEMPLATES_117 = """<Templates><Template><Name>Residence</Name><Properties><Building><TerrainType2>NormalLand</TerrainType2>
</Building></Properties></Template></Templates>"""
ASSETS_GROUPED = b"""<AssetList><Groups><Group><Name>Objects</Name><Groups><Group><Name>Buildings</Name><Assets>
<Asset><Template>Warehouse</Template><Values><Blocking><BlockingRules>OverHabourBuilding;CoastBuilding</BlockingRules>
</Blocking><Building /><Standard><Name>port</Name><GUID>10110</GUID></Standard>
<Object><Variations><Item><Filename>data\\graphics\\port.cfg</Filename></Item></Variations></Object></Values></Asset>
<Asset><Template>FactoryBuilding</Template><Values><Standard><Name>carbon</Name><GUID>10112</GUID></Standard>
<Object><Variations><Item><Filename>data\\graphics\\carbon.cfg</Filename></Item></Variations></Object></Values></Asset>
</Assets></Group></Groups></Group></Groups></AssetList>"""
IFO_FIXED = """<Info><BuildBlocker><Position><x>-8192</x><z>-4096</z></Position><Position><x>-8192</x><z>4096</z>
</Position><Position><x>8192</x><z>4096</z></Position><Position><x>8192</x><z>-4096</z></Position></BuildBlocker></Info>"""
QUARTER = [0.0, -math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4)]


def test_the_list_holds_what_draws_a_model_grouped_by_the_build_menu_else_the_template():
    rows = {r.guid: r for r in placeables.scan([ASSETS_117], TEMPLATES_117)}
    assert sorted(rows) == [2955, 3000, 3001, 4000]  # no categories, no developer stand-in
    fish = rows[2955]
    assert fish.group == ("Build menu", "Roman", "Production") and fish.terrain == ("coast",)
    assert fish.building and fish.icon == "data/ui/icon_sardines.png" and fish.cfg == "data/b/sardines.cfg"
    house = rows[3001]  # a base asset's models, template and its template's ground
    assert (house.template, house.cfg, house.variations, house.terrain) == (
        "Residence",
        "data/b/house_a.cfg",
        2,
        ("land",),
    )
    assert house.group == ("Templates", "Residence") and house.building
    assert not rows[4000].building and rows[4000].terrain == ()
    assert [r.guid for r in placeables.search(list(rows.values()), "house")] == [3000, 3001]
    assert [r.guid for r in placeables.search(list(rows.values()), "4000")] == [4000]
    assert [r.guid for r in placeables.search(list(rows.values()), "", buildings_only=True)] == [2955, 3000, 3001]
    assert list(placeables.grouped(list(rows.values()))) == ["Build menu / Roman / Production", "Templates / Decoration",
                                                             "Templates / Residence"]  # fmt: skip
    assert placeables.from_json(placeables.to_json([fish])) == [fish]
    pick = placeables.pick(fish, "data/b/sardines.cfg")
    assert pick["guid"] == 2955 and pick["plain"] and pick["clone"] and pick["terrain"] == ["coast"]


def test_the_old_engines_list_keeps_its_asset_groups_and_harbour_rules():
    rows = {r.guid: r for r in placeables.scan([ASSETS_GROUPED])}
    assert rows[10110].group == ("Objects", "Buildings") and rows[10110].terrain == ("coast",)
    assert rows[10112].building and rows[10112].terrain == ("land",) and rows[10112].cfg == "data/graphics/carbon.cfg"
    ocf = {"data/graphics/objects/buildings/north/hut/hut.ocf": "<ObjectConfig><GUID>37115</GUID><Name>hut</Name>"
           "<MeshConfig>data\\graphics\\objects\\buildings\\north\\hut\\hut.mcf</MeshConfig></ObjectConfig>"}  # fmt: skip
    (hut,) = placeables.from_object_configs(ocf)
    assert (hut.guid, hut.name, hut.building, hut.group) == (37115, "hut", True, ("buildings", "north"))
    assert placeables.turn_step("anno117") == "eighth" and placeables.turn_step("anno2070") == "quarter"


def test_an_asset_without_a_group_takes_the_group_of_its_base():
    assets = b"""<AssetList><Groups><Group><Name>Objects</Name><Assets>
<Asset><Template>Residence</Template><Values><Standard><GUID>3000</GUID><Name>House</Name></Standard>
<Object><Variations><Item><Filename>data/b/house_a.cfg</Filename></Item></Variations></Object></Values></Asset>
</Assets></Group><Group><Assets>
<Asset><BaseAssetGUID>3000</BaseAssetGUID><Values><Standard><GUID>3001</GUID><Name>House Celtic</Name></Standard>
</Values></Asset></Assets></Group></Groups></AssetList>"""
    rows = {r.guid: r for r in placeables.scan([assets], TEMPLATES_117)}
    assert rows[3001].group == rows[3000].group == ("Objects",)


def test_an_old_engine_footprint_is_read_in_tiles():
    assert footprints.read_footprint(IFO_FIXED) == [[(-2.0, -1.0), (-2.0, 1.0), (2.0, 1.0), (2.0, -1.0)]]


def _session(heights: np.ndarray, footprints_table: dict, game: str | None = None, objects=()) -> EditSession:
    island = {
        "width": heights.shape[0],
        "samples_per_tile": 1,
        "heights": base64.b64encode(heights.astype("<f4").tobytes()).decode(),
        "objects": list(objects),
        "footprints": footprints_table,
        **({"game": game} if game else {}),
    }
    return EditSession(island)


def _coast_island() -> np.ndarray:
    """33 corners: land west of x 16, sea east of it."""
    heights = np.full((33, 33), -2.0)
    heights[:, :17] = 1.0
    return heights


def test_the_live_check_says_where_a_building_may_not_stand():
    poly = [[(-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0)]]
    s = _session(_coast_island(), {"data/b/hut.cfg": poly})
    turn = [0.0, 0.0, 0.0, 1.0]
    assert placedrules.check_place(s, poly, [8.0, 1.0, 8.0], turn, ("land",)) == []
    assert placedrules.check_place(s, poly, [24.0, 0.0, 8.0], turn, ("land",)) == ["needs land"]
    assert placedrules.check_place(s, poly, [8.0, 1.0, 8.0], turn, ("coast",)) == ["needs the coast"]
    assert placedrules.check_place(s, poly, [16.0, 1.0, 8.0], turn, ("coast",)) == []  # half on land, half in the sea
    assert placedrules.check_place(s, poly, [15.0, 1.0, 8.0], turn, ("coast",)) == []  # on land, the sea beside it
    assert placedrules.check_place(s, poly, [24.0, 0.0, 8.0], turn, ("water", "coast")) == []
    assert placedrules.check_place(s, poly, [0.5, 1.0, 8.0], turn, ()) == ["off the island"]
    s.place({"guid": 7, "template": "Hut", "name": "Hut", "cfg": "data/b/hut.cfg", "clone": True}, [8.0, 1, 8.0])
    assert placedrules.check_place(s, poly, [9.0, 1.0, 8.0], turn, ("land",)) == ["over Hut"]
    assert placedrules.check_place(s, poly, [10.0, 1.0, 8.0], turn, ("land",)) == []  # edge to edge


def test_the_live_check_knows_slope_blocked_ground_and_a_rock_with_no_footprint():
    poly = [[(-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0)]]
    turn = [0.0, 0.0, 0.0, 1.0]
    flat = np.full((33, 33), 1.0)
    s = _session(flat, {"data/b/hut.cfg": poly})
    assert placedrules.check_place(s, poly, [8.0, 1.0, 8.0], turn, ("land",)) == []
    hill = flat.copy()
    hill[:, 12:] += np.linspace(0, 20, 21)  # a ramp rising one a tile
    s = _session(hill, {"data/b/hut.cfg": poly})
    assert placedrules.check_place(s, poly, [10.0, 1.0, 8.0], turn, ("land",)) == []  # before the ramp
    assert placedrules.check_place(s, poly, [16.0, 1.0, 8.0], turn, ("land",))[0].startswith("stands on sloping ground")
    rock = {"key": "r", "cfg": "data/n/rock_big_01.cfg", "guid": 5, "position": [20.0, 1.0, 20.0],
            "rotation": turn, "scale": 1.0}  # fmt: skip
    s = _session(flat, {"data/b/hut.cfg": poly}, objects=[rock])
    assert placedrules.check_place(s, poly, [20.0, 1.0, 20.0], turn, ("land",))[0].startswith(
        "rock_big_01 stands in it"
    )
    assert placedrules.check_place(s, poly, [24.0, 1.0, 20.0], turn, ("land",)) == []
    s.path.blocked = np.zeros((64, 64), bool)  # 2070 and 1404: half-tile cells of the height layer
    s.path.blocked[16:18, 16:18] = True  # tile (8, 8)
    assert placedrules.check_place(s, poly, [8.0, 1.0, 8.0], turn, ("land",))[0].startswith("stands on blocked ground")
    assert placedrules.check_place(s, poly, [12.0, 1.0, 8.0], turn, ("land",)) == []


def test_a_selected_building_has_its_footprint_drawn_under_it_red_where_the_rules_say_no():
    from types import SimpleNamespace

    from annotoolbox.editor.desktop.desktopoverlays import OverlayDraw

    poly = [[(-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0)]]
    s = _session(_coast_island(), {"data/b/hut.cfg": poly})
    s.island["terrain"] = {"7": ["land"]}
    pick = {"guid": 7, "template": "Hut", "name": "Hut", "cfg": "data/b/hut.cfg", "clone": True}
    view = SimpleNamespace(session=s, to_view=lambda p: np.asarray(p, "f4"), _rgba=lambda _name: (0.1, 0.2, 0.3, 1.0))
    on_land, in_sea = s.place(pick, [8.0, 1.0, 8.0]), s.place(pick, [24.0, 0.0, 8.0])
    ((mode, rows),) = OverlayDraw._footprint_under(view, on_land)
    assert mode == "lines" and rows.shape == (8, 7)  # four edges, two ends each
    assert np.allclose(rows[:, 3:], (0.1, 0.2, 0.3, 1.0))  # not over itself: the selection's colour
    assert set(rows[:, 0]) == {7.0, 9.0} and set(rows[:, 2]) == {7.0, 9.0}
    ((_, bad),) = OverlayDraw._footprint_under(view, in_sea)
    assert np.allclose(bad[:, 3:], (1.0, 0.25, 0.2, 1.0))  # needs land
    s.island["footprints"] = {}
    assert OverlayDraw._footprint_under(view, on_land) == []


def test_a_quarter_turn_game_flags_an_eighth_and_the_terrain_of_a_placed_building():
    poly = [[(-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0)]]
    s = _session(_coast_island(), {"data/b/hut.cfg": poly}, game="anno1404")
    s.island["terrain"] = {"7": ["land"]}
    e = s.place({"guid": 7, "template": "Hut", "name": "Hut", "cfg": "data/b/hut.cfg", "clone": True}, [8.0, 1, 8.0])
    assert not [r for r in placedrules.check(s) if r[0] == "warning"]
    s.transform(e, rotation=yaw_quat(math.pi / 4))
    assert any(
        "turned 45.0 degrees" in r[1] and "quarters (90)" in r[1] for r in placedrules.check(s)
    )  # as the view shows it
    s.transform(e, rotation=QUARTER, position=[24.0, 0.0, 8.0])
    assert [r[1] for r in placedrules.check(s) if r[0] == "warning"] == ["Hut needs land"]


def test_117_a_building_with_none_of_its_kind_on_the_island_is_written_as_a_plain_copy():
    ed = _opened()
    objects = ed.objects()
    island = {"width": 33, "samples_per_tile": 2, "heights": base64.b64encode(np.zeros((33, 33), "<f4").tobytes()).decode(),
              "objects": objects, "footprints": ed.footprints(objects)}  # fmt: skip
    session = EditSession(island)
    row = placeables.Placeable(2955, "Sardines", "Production", ("Build menu",), "data/b/sardines.cfg", 1, True)
    new = session.place(placeables.pick(row, "data/b/sardines.cfg"), [16.0, 0.5, 16.0], math.pi / 2)
    assert new.added["plain"] and new.added["guid"] == 2955
    session.transform(new, position=[18.0, 0.5, 16.0])
    a7m, info = _synthetic_file(ed)
    files = FakeFiles({"data/i/i.a7m": a7m, "data/i/i.a7minfo": info})
    done = a7m_edit117.apply(files, "data/i/i.a7m", {**islandedits.empty(), **session.edits}, template=None)
    assert done.report["objects added"] == 1 and done.report["unmatched"] == []
    back = a7m_edit117.IslandFile(done.a7m, info)
    rows = {struct.unpack_from("<I", o.find("guid").data)[0]: o for _g, _objs, o, _n in islandedits._objects(back)}
    assert struct.unpack("<3f", rows[2955].find("Position").data) == (18.0, 0.5, 16.0)
    assert struct.unpack_from("<I", rows[2955].find("ID").data)[0] == len(_opened_tags()) + 1  # the next serial


class _IslandFiles(FakeFiles):
    def list(self, prefix: str) -> list[str]:
        return self.iter_paths(prefix)


def test_117_a_plain_placed_building_takes_the_blocks_of_its_asset_from_another_island():
    ed = _opened()
    a7m, info = _synthetic_file(ed)
    donor = _game_object(1, 2955, (5.0, 0.5, 5.0))
    donor.children.append(filedb.Tag("Building", []))
    donor.children.append(filedb.Tag("Maintenance", []))
    donor_a7m, _ = _synthetic_file(ed, [donor])
    files = _IslandFiles(
        {
            "data/i/i.a7m": a7m, "data/i/i.a7minfo": info, "data/base/provinces/x/islands/d/d.a7m": donor_a7m,
            assetmodels.ASSETS_117.assets: ASSETS_117,
        }
    )  # fmt: skip
    entry = {"guid": 2955, "template": "Production", "clone": True, "plain": True, "variation": 0,
             "position": [18.0, 0.5, 16.0], "rotation": [0, 0, 0, 1]}  # fmt: skip
    done = a7m_edit117.apply(files, "data/i/i.a7m", {**islandedits.empty(), "added_objects": [entry]}, template=None)
    assert done.report["objects added"] == 1 and done.report["unmatched"] == []
    back = a7m_edit117.IslandFile(done.a7m, info)
    rows = {struct.unpack_from("<I", o.find("guid").data)[0]: o for _g, _objs, o, _n in islandedits._objects(back)}
    assert [c.name for c in rows[2955].children if isinstance(c, filedb.Tag)] == ["Mesh", "Building", "Maintenance"]


def test_a_template_donor_is_the_same_asset_first_then_the_same_template_and_is_read_once():
    reads = []

    class Counting(_IslandFiles):
        def read(self, path):
            reads.append(path)
            return super().read(path)

    ed = _opened()
    other = _game_object(1, 3001, (5.0, 0.5, 5.0))
    same = _game_object(1, 3000, (5.0, 0.5, 5.0))
    files = Counting({f"data/a/islands/{n}.a7m": _synthetic_file(ed, [o])[0] for n, o in (("a", other), ("b", same))})
    models = assetmodels.AssetModels(ASSETS_117)
    donors = templatedonors.for_game("anno117", files, models)
    assert donors.find(3000, "Residence") is not None and donors.find(3000, "Residence").find("guid").u32() == 3000
    assert donors.find(9999, "Residence") is not None  # the template's own, an island later
    assert donors.find(9999, "") is None and donors.find(9999, "Nothing") is None
    assert len(reads) == 2  # each island read once, however many answers


def test_without_plain_a_copy_still_needs_a_sample():
    entry = {"guid": 77, "clone": True, "position": [1, 0, 1], "rotation": [0, 0, 0, 1]}
    assert objectclones.place_copies({"added_objects": [entry]}, {})[2] == ["guid:77"]
    assert objectclones.place_copies({"added_objects": [{**entry, "plain": True}]}, {})[2] == ["guid:77"]  # nothing


def test_a_copy_with_no_template_is_never_made_from_an_arbitrary_object():
    tags = _opened_tags()
    objs = filedb.Tag("Objects", tags)
    found = {struct.unpack_from("<I", t.find("guid").data)[0]: (objs, t) for t in tags}
    entry = {"guid": 999, "clone": True, "template": "", "position": [1, 0, 1], "rotation": [0, 0, 0, 1]}
    for templates in (None, {3402: "Hut"}):
        rest, added, missing = objectclones.place_copies({"added_objects": [entry]}, found, templates)
        assert added == 0 and missing == ["guid:999"] and len(objs.children) == len(tags)
    _rest, added, missing = objectclones.place_copies({"added_objects": [{**entry, "plain": True}]}, found)
    assert added == 1 and missing == []  # only a plain entry takes the plain (mesh only) sample


def test_dragging_an_asset_that_cannot_be_drawn_says_why_instead_of_raising():
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from PySide6.QtCore import QEvent

    from annotoolbox.editor.desktop import desktopplace

    said = []

    def broken(_guid):
        raise FileNotFoundError("no such model")

    view = SimpleNamespace(session=object(), status=SimpleNamespace(emit=said.append))
    tool = SimpleNamespace(view=view, panel=SimpleNamespace(pick_now=broken))
    data = SimpleNamespace(hasFormat=lambda _m: True, data=lambda _m: b'{"guid": 5}')
    event = SimpleNamespace(type=lambda: QEvent.Type.DragEnter, mimeData=lambda: data)
    assert desktopplace._DropFilter(tool).eventFilter(None, event) is False
    assert "no such model" in said[0]


def test_the_list_cache_changes_with_the_templates_and_with_an_edit_in_the_middle_of_an_asset_file(tmp_path):
    import threading
    from types import SimpleNamespace

    from annotoolbox.editor import islandeditor

    def run(asset: bytes, templates: str) -> None:
        stub = SimpleNamespace(
            placeable_sources=lambda: ([asset], templates),
            _cached=lambda key, suffix: tmp_path / (str(abs(hash(key))) + suffix),
            _lock=lambda _path: threading.Lock(),
        )
        islandeditor.IslandEditor.placeables(stub)

    big = b"<AssetList>" + b"x" * 200_000 + b"</AssetList>"
    middle = big[:100_000] + b"y" + big[100_001:]  # the same length and the same ends
    run(big, TEMPLATES_117)
    run(middle, TEMPLATES_117)
    run(big, TEMPLATES_117 + " ")
    assert len(list(tmp_path.iterdir())) == 3


@pytest.fixture
def types2070():
    assets = ASSETS_2070.replace(
        "</Assets>",
        "<Asset><Template>FactoryBuilding</Template><Values><Standard><GUID>10112</GUID><Name>carbon</Name>"
        "</Standard><Object><Variations><Item><Filename>data/graphics/carbon.cfg</Filename></Item></Variations>"
        "</Object></Values></Asset></Assets>",
    )
    return objects2070.read_types(FakeTree({objects2070.ASSETS_2070: assets.encode()}))


def test_2070_and_1404_a_building_is_written_as_a_handle_object_and_a_second_as_its_copy(types2070):
    isl = isd_edit.Island2070(isd2070_bytes())
    before = max(o.int("m_ID") for _, _, o in isl.objects())
    place = {"guid": 10112, "clone": True, "plain": True, "position": [10.0, 1.0, 12.0], "rotation": QUARTER}
    report = isl.apply({"added_objects": [place]}, types2070)
    assert report["objects added"] == 1 and report["unmatched"] == []
    again = isd_edit.Island2070(isl.write())
    (factory,) = [o for _g, _objs, o in again.objects() if o.int("m_GUID") == 10112]
    assert factory.int("m_ID") == before + 1 and factory.find("PropertyMesh") is not None
    x, z, y = struct.unpack("<qqq", isd2070.position_node(factory).data)
    assert (x / isd2070.FIXED, z / isd2070.FIXED) == (10.0, 12.0) and factory.int("m_Direction") == 90 * 4096
    factory.children.append(isd2070.Node("Blocking"))  # a block of its own: a copy of it carries it
    moved = {"guid": 10112, "clone": True, "variation": 0, "position": [20.0, 1.0, 20.0], "rotation": [0, 0, 0, 1]}
    again.apply({"added_objects": [moved]}, types2070)
    copies = [o for _g, _objs, o in isd_edit.Island2070(again.write()).objects() if o.int("m_GUID") == 10112]
    assert len(copies) == 2 and copies[1].find("Blocking") is not None
    ids = [o.int("m_ID") for _g, _o, o in isd_edit.Island2070(again.write()).objects()]
    assert len(ids) == len(set(ids)) and max(ids) == before + 2


def test_1701_a_placed_object_is_a_copy_under_a_handle_of_its_own():
    isl = isd1701.Island1701(isd1701_bytes())
    entry = {"guid": 37115, "clone": True, "variation": 1, "position": [2.5, 0.0, 3.5], "rotation": QUARTER}
    report = isc_edit.apply(isl, {"added_objects": [entry, {"position": [0, 0, 0]}]})
    assert report["objects added"] == 1 and report["unmatched"] == ["added guid:None"]
    back = isd1701.Island1701(isl.write())
    new = next(o for o in back.objects() if o.guid == 37115)
    assert (new.x, new.z, new.variation) == (2.5, 3.5, 1) and new.direction == pytest.approx(90 * 4096, abs=1)
    manager = back.root.path("m_GOPManager/m_HIDManager")
    names = [c.name for c in manager.children if c.find("m_GUID") is not None]
    assert len(names) == len(set(names)) == 3 and names[-1] == "003C0031" and manager.find("m_LastHID").text == "49"


def test_the_shared_apply_loops_match_by_key_and_report_what_nothing_matched():
    from annotoolbox.islands import objectedits

    items = {"a": [1, 2], "b": [3], "c": [4]}
    moved, removed = [], []
    ops = objectedits.ObjectOps(lambda h, change: moved.append((h, change)), removed.extend, lambda h: h == "c")
    report = {"objects changed": 0, "objects deleted": 0, "unmatched": [], "still referenced": []}
    objectedits.apply_changes(
        iter((k, k) for k in items), {"a": {"position": [1]}, "c": {"deleted": True}, "x": {}, "w": {}}, ops, report
    )
    assert moved == [("a", {"position": [1]})] and removed == ["c"]
    assert report == {"objects changed": 1, "objects deleted": 1, "unmatched": ["w", "x"], "still referenced": ["c"]}
    report = {"objects added": 0, "unmatched": []}
    objectedits.add_each([{"guid": 1}, {"guid": 2}], lambda e: e["guid"] == 1, lambda e: f"no {e['guid']}", report)
    assert report == {"objects added": 1, "unmatched": ["no 2"]}


def test_1701_a_turned_copy_gets_the_grid_direction_of_its_turn():
    isl = isd1701.Island1701(isd1701_bytes())
    sample = isl.root.path("m_GOPManager/m_HIDManager/003C002F")
    sample.children.append(Node("m_GRIDDirection", text="0"))
    entry = {"guid": 4010, "clone": True, "position": [2.5, 0.0, 3.5], "rotation": QUARTER}
    isc_edit.apply(isl, {"added_objects": [entry]})
    new = isl.root.path("m_GOPManager/m_HIDManager").children[-1]
    assert new.find("m_GRIDDirection").text == "16"  # a quarter turn of 64 steps


def test_2205_a_placed_asset_the_session_does_not_carry_is_a_copy_of_a_game_object():
    isl = _session2205()
    entry = {"guid": 5555, "clone": True, "plain": True, "position": [4.0, 0.0, 4.0], "rotation": QUARTER}
    report = a7m_edit2205.apply(isl, edits2205(added_objects=[entry, {**entry, "guid": 6666, "plain": False}]))
    assert report["objects added"] == 1 and report["unmatched"] == ["guid:6666"]
    assert report["placed with another asset's kind"] == ["guid:5555"]  # the sample's ID kind is a guess: said so
    back = isl  # the session was changed in place; its object list now holds the copy
    (new,) = [o for o in _objects(back, "GameObject") if o.find("guid").u32() == 5555]
    ids = [o.find("ID").data for o in _objects(back, "GameObject")]
    assert len(ids) == len(set(ids))
    assert new.find("Position") is not None


def test_1404_a_placed_building_is_built_into_the_islands_archive(tmp_path, types2070):
    from annotoolbox.formats import rda
    from test_oldenginebuild import ISLE, _save, built, edits, entries

    assets = ASSETS_2070.replace("</Assets>", "<Asset><Template>FactoryBuilding</Template><Values><Standard><GUID>10112"
                                 "</GUID><Name>carbon</Name></Standard></Values></Asset></Assets>")  # fmt: skip
    game = tmp_path / "game"
    _save(game / "Anno1404.exe", b"")
    _save(
        game / "maindata/data0.rda", rda.write_v20({ISLE: isd2070_bytes(), objects2070.ASSETS_2070: assets.encode()}, 1)
    )
    place = {"guid": 10112, "clone": True, "plain": True, "position": [6.0, 1.0, 8.0], "rotation": QUARTER}
    edits(tmp_path / "e", "1404_", ISLE, added_objects=[place])
    archives, written, _settings = built("anno1404", game, tmp_path / "e")
    assert written == [ISLE]
    _save(tmp_path / "out" / "data0.rda", archives["maindata/data0.rda"])
    back = isd_edit.Island2070(entries(tmp_path / "out" / "data0.rda")[ISLE])
    (factory,) = [o for _g, _objs, o in back.objects() if o.int("m_GUID") == 10112]
    assert factory.int("m_Direction") == 90 * 4096


class _PlacingEditor:
    """The fake editor of the window tests, with a game's asset list."""

    ROW = placeables.Placeable(10112, "carbon", "FactoryBuilding", ("Objects", "Buildings"), "data/g/carbon.cfg", 1,
                               True, ("land",))  # fmt: skip

    def __init__(self) -> None:
        from test_desktop import _Editor

        self.base = _Editor()

    def __getattr__(self, name):
        return getattr(self.base, name)

    def placeables(self):
        return [
            self.ROW,
            placeables.Placeable(4000, "bench", "Decoration", ("Templates", "Decoration"), "b.cfg", 1, False),
        ]

    def drawable(self, cfg):
        return cfg

    def footprint_of(self, cfg):
        return [[(-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0)]] if "carbon" in cfg else []

    def read_game(self, path):
        raise FileNotFoundError(path)


def test_the_buildings_dock_picks_snaps_checks_and_places(tmp_path, monkeypatch):
    import time

    from test_desktop import _window

    qt, main = _window(tmp_path)
    monkeypatch.setattr(main.app, "open", lambda kind, name: (setattr(main.app, "editor", _PlacingEditor()),
                                                             {"kind": "built", "name": name, "editable": True})[1])  # fmt: skip
    main.open("built", "isle")
    doc, view = main.doc, main.view
    t = time.monotonic()
    while not doc.buildings.rows and time.monotonic() - t < 10:
        qt.processEvents()
        time.sleep(0.01)
    assert main.dock_holders["Buildings"].panel is doc.buildings and doc.buildings.tree.topLevelItemCount() == 1
    doc.buildings.buildings.setChecked(False)
    assert doc.buildings.tree.topLevelItemCount() == 2 and doc.session.island["terrain"] == {"10112": ["land"]}
    view.set_pick(doc.buildings.pick_now(10112))
    assert view.tool == "place" and view.placer.polygons
    view.place_yaw = 0.3
    view.placer.turn(1)  # onto 117's eighths, then one on
    assert view.place_yaw == pytest.approx(math.pi / 4)
    view.cursor_hit = view.to_view([8.3, 0.7, 8.2])
    view.placer.ghost()
    at, _yaw = view.placer.spot
    assert (at[0], at[2]) == (8.0, 8.0) and view.placer.problems == [] and view.placer.overlay()
    view.placer.place(view.cursor_hit)
    added = doc.session.edits["added_objects"][-1]
    assert added["guid"] == 10112 and added["plain"] and added["position"][0] == 8.0
    view.cursor_hit = view.to_view([1.2, 0.0, 1.2])  # in the sea, off the land the asset asks for
    view.placer.ghost()
    assert "needs land" in view.placer.problems
    view.cursor_hit = view.to_view([9.0, 0.7, 8.0])
    view.placer.ghost()
    assert (
        view.placer.problems[-1] == "over carbon"
    )  # and the rock the fake island has at (9, 9), under the turned footprint
    assert view.placer.problems[0].startswith("rock.cfg stands in it") or "rock" in view.placer.problems[0]
    doc.session.undo.mark_clean()  # closing asks about unsaved edits otherwise
    main.close_island()


def test_once_the_list_is_read_decoration_is_not_held_to_the_turn_steps():
    poly = [[(-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0)]]
    s = _session(_coast_island(), {"data/b/fence.cfg": poly}, game="anno1800")
    e = s.place(
        {"guid": 8, "template": "Fence", "name": "Fence", "cfg": "data/b/fence.cfg", "clone": True}, [8.0, 1, 8]
    )
    s.transform(e, rotation=yaw_quat(0.3))
    assert any("quarters" in r[1] for r in placedrules.check(s))
    s.island["buildings"] = ["7"]  # the list read: asset 8 is no building
    assert not any("quarters" in r[1] or "grid" in r[1] for r in placedrules.check(s))


def test_the_asset_editor_hands_an_asset_to_the_islands_place_tool():
    from types import SimpleNamespace

    from annotoolbox.suite import apps

    chosen = []
    buildings = SimpleNamespace(
        choose=lambda guid: chosen.append(guid) or "", guid_of=lambda cfg: 7 if cfg == "a.cfg" else None
    )
    page = SimpleNamespace(doc=SimpleNamespace(info={"kind": "anno2070"}, buildings=buildings))
    assert apps._islands_place_asset(page, "anno2070", 10112) == "" and chosen == [10112]
    assert "anno2070" in apps._islands_place_asset(page, "anno117", 1)
    assert apps._islands_place_asset(SimpleNamespace(doc=None), "anno2070", 1) == "open an island first"
    assert apps._islands_place(page, "anno2070", "a.cfg") == "" and chosen[-1] == 7  # the viewer's model, as its asset


def test_cell_grid_gives_the_cells_under_a_footprint_and_the_ring_round_it():
    square = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]])
    centres, under, ring = footprints.cell_grid([square])
    assert sorted(map(tuple, centres[under])) == [(0.5, 0.5), (0.5, 1.5), (1.5, 0.5), (1.5, 1.5)]
    assert not ring.any()  # the grid stops at the footprint: nothing lies beside it
    centres, under, ring = footprints.cell_grid([square], margin=1)
    assert len(centres) == 16 and under.sum() == 4 and ring.sum() == 8  # the edge cells, not the corners
    assert np.array_equal(footprints.cell_centres([square], 1.0), centres[under])
    wide, under, _ = footprints.cell_grid([square], step=2.0)
    assert wide[under].tolist() == [[1.0, 1.0]]
