import json
import struct

import pytest
from tests.fakes import FakeFiles, FakeTree
from tests.formats.isdfakes import CHAIR_PATH as PROP
from tests.formats.isdfakes import ISLAND1800_PATH as PATH
from tests.formats.isdfakes import island1800_files as _island

from annotoolbox.editor import a7mviewer
from annotoolbox.editor.assetmodels import ASSETS_1800, CAssetModels, CReason
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.editor.formats.a7m import edit1800 as a7m_edit1800
from annotoolbox.editor.formats.a7m import reader as a7m_reader
from annotoolbox.formats import filedb
from annotoolbox.islands import islandedits
from annotoolbox.othergames import installs

ASSETS = (
    "<Assets><Asset><Template>Tree</Template><Values><Standard><GUID>100849</GUID><Name>Oak</Name></Standard>"
    "<Object><Variations><Item><Filename>data/graphics/tree_01.cfg</Filename></Item></Variations></Object>"
    "</Values></Asset></Assets>"
)
PROP_TEXT = (
    "<Prop><Type>SimplePBR</>\r\n<MeshFileName>data/graphics/props/rdm/chair_01_lod0.rdm</>\r\n"
    "<Materials><Material><cModelDiffTex>data/graphics/props/atlas_diff.psd</>\r\n"
    "<cModelNormalTex>data/graphics/props/atlas_norm.psd</>\r\n<cDiffuseColor.r>0.5</>\r\n</>\r\n</>\r\n</>"
)


def _tree() -> FakeTree:
    files = _island(3, material_set=True)
    files["data/config/export/main/asset/assets.xml"] = ASSETS
    files[PROP] = PROP_TEXT
    return FakeTree(files)


def _viewer(tmp_path, edits=None) -> a7mviewer.CA7mViewer:
    return a7mviewer.CA7mViewer(installs.GAMES["anno1800"], _tree(), PATH, tmp_path / "cache", edits)


def test_an_1800_island_is_drawn_with_its_rivers_props_and_objects(tmp_path):
    viewer = _viewer(tmp_path, tmp_path / "1800_isle.json")
    island = viewer.island()
    (river,) = island["splines"]
    assert river["kind"] == "River" and river["points"] == [[0.0, 0.0, 1.0], [3.0, 0.0, 1.0]]
    assert river["widths"] == [2.0, 2.0]
    (prop,) = island["props"]
    assert prop["key"] == islandedits.key(PROP, [2.0, 0.5, 2.0]) and prop["cfg"] == PROP
    tree = next(o for o in island["objects"] if o["guid"] == 100849)
    assert tree["key"] == islandedits.key("guid:100849", [1.5, 2.0, 2.5])  # the key islandedits finds it by
    assert tree["cfg"] == "data/graphics/tree_01.cfg" and tree["template"] == "Tree" and tree["clone"]
    assert tree["marker"] is None and island["game"] == "anno1800" and not island["readonly"]
    (part,) = viewer.model(PROP)["parts"]  # a .prp is read as the model config the view draws
    assert part["mesh"].endswith("chair_01_lod0.rdm") and part["diffuse"].endswith("atlas_diff.psd")
    assert part["color"] == [0.5, 1.0, 1.0]
    assert [r["guid"] for r in viewer.library()["anno1800"]] == [100849]
    assert "footprints" in island and "unresolved" not in tree  # a pin says why it is one (``CReason``)
    pins = [o for o in island["objects"] if not o["cfg"]]
    assert all(o["unresolved"] in (CReason.NO_MODEL, CReason.NO_ASSET) for o in pins)


def test_the_island_data_made_is_handed_on_and_read_back_from_its_cache_the_same(tmp_path):
    made = _viewer(tmp_path).island_data()  # made: handed on, not written and read again
    assert made == _viewer(tmp_path).island_data() == json.loads(_viewer(tmp_path).island_json())  # the cache's


def test_asset_details_name_the_model_and_the_template():
    models = CAssetModels(ASSETS.encode(), game=ASSETS_1800)
    info = models.asset(100849)
    assert (models.first_model(100849), info.template, info.name) == ("data/graphics/tree_01.cfg", "Tree", "Oak")
    assert models.asset(7) is None


def test_1800_assets_find_a_prop_model_outside_the_variations_and_inherit_from_a_base_and_a_template():
    xml = (
        b"<Asset><Template>Prop</Template><Values><Standard><GUID>1</GUID></Standard>"
        b"<PropData><Filename>data/graphics/props/lamp.prp</Filename></PropData></Values></Asset>"
        b"<Asset><BaseAssetGUID>1</BaseAssetGUID><Values><Standard><GUID>2</GUID></Standard></Values></Asset>"
        b"<Asset><Template>Crate</Template><Values><Standard><GUID>3</GUID></Standard></Values></Asset>"
    )
    templates = "<Template><Name>Crate</Name><Properties><Object><Variations><Item><Filename>data/c.cfg</Filename></Item></Variations></Object></Properties></Template>"
    models = CAssetModels(xml, templates, ASSETS_1800)
    assert [models.first_model(g) for g in (1, 2, 3)] == ["data/graphics/props/lamp.prp"] * 2 + ["data/c.cfg"]
    assert models.asset(2).template == "Prop"  # a base's template


def test_asset_details_skip_a_guid_listed_in_another_asset_first():
    pool = (
        b"<Asset><Template>ItemEffectTargetPool</Template><Values><Standard><GUID>5</GUID></Standard>"
        b"<ItemEffectTargetPool><EffectTargetGUIDs><Item><GUID>100849</GUID></Item></EffectTargetGUIDs>"
        b"</ItemEffectTargetPool></Values></Asset>"
    )
    models = CAssetModels(pool + ASSETS.encode(), game=ASSETS_1800)
    assert (models.first_model(100849), models.asset(100849).template) == ("data/graphics/tree_01.cfg", "Tree")


def test_edits_of_an_1800_island_are_built_back_into_it(tmp_path):
    viewer = _viewer(tmp_path, tmp_path / "1800_isle.json")
    session = CEditSession(json.loads(viewer.island_json()), viewer.edits())
    tree = next(e for e in session.m_entries if e.kind == "object" and e.base.get("guid") == 100849)
    chair = next(e for e in session.m_entries if e.kind == "prop")
    session.transform(tree, position=[2.5, 2.0, 2.5])
    copy = session.duplicate(tree)
    assert copy.base["clone"] and copy.cfg == tree.cfg and copy.added["guid"] == 100849
    session.delete(chair)
    viewer.save_edits(session.m_edits)
    saved = json.loads((tmp_path / "1800_isle.json").read_text(encoding="utf-8"))
    assert saved["source"] == PATH and saved["game"] == "anno1800"

    done = a7m_edit1800.apply(a7m_reader.CGameFiles(_tree()), PATH, saved)
    a7m, report = done.a7m, done.report
    assert report["objects changed"] == 1 and report["objects added"] == 1 and report["props deleted"] == 1
    assert report["unmatched"] == []
    out = a7m_reader.CIslandFile1800(a7m, _tree().read_bytes(PATH[:-4] + ".a7minfo"))
    objs = [o for o in out.main_objects("GameObject").children if hasattr(o, "find")]
    assert len(objs) == 2 and {struct.unpack("<3f", o.find("Position").data)[0] for o in objs} == {2.5, 4.5}
    ids = [o.find("ID").data for o in objs]
    assert len(set(ids)) == 2 and struct.unpack("<2I", ids[1]) == (8, 0x1234)  # the copy has an ID of its own
    assert not out.rd3d.path("PropGrid/Instances").children


def test_an_unmatched_edit_and_an_unknown_copy_are_reported_not_lost():
    edits = {**islandedits.empty(), "objects": {"guid:1|0.00|0.00|0.00": {"deleted": True}}}
    edits["added_objects"] = [
        {"guid": 99, "cfg": "x.cfg", "template": "Tree", "position": [0, 0, 0], "rotation": [0, 0, 0, 1]},
        {"cfg": "y.cfg", "position": [0, 0, 0], "rotation": [0, 0, 0, 1]},
    ]
    report = a7m_edit1800.apply(a7m_reader.CGameFiles(_tree()), PATH, edits).report
    assert report["unmatched"] == ["guid:1|0.00|0.00|0.00", "guid:99", "cfg:y.cfg"]


def test_the_build_writes_a_mod_that_replaces_the_island_and_refuses_other_folders(tmp_path):
    edits_dir = tmp_path / "edits"
    edits_dir.mkdir()
    islandedits.save(
        edits_dir / a7m_edit1800.edit_name(PATH), {**islandedits.empty(), "source": PATH, "game": "anno1800"}
    )
    (edits_dir / "1800_other.json").write_text("{}", encoding="utf-8")  # no source: skipped, said so
    log: list[str] = []
    out = tmp_path / "out" / "anno1800-islands"
    binned: list = []

    def build(files, where=out):
        return a7m_edit1800.build(
            files, edits_dir, where, "anno1800-islands", "Islands", log.append, trash=binned.append
        )

    written = build(a7m_reader.CGameFiles(_tree()))
    assert written == [PATH] and (out / PATH).is_file() and any("no source island" in line for line in log)
    info = json.loads((out / "modinfo.json").read_text(encoding="utf-8"))
    assert info["ModID"] == "anno1800-islands" and info["ModName"]["English"] == "Islands"
    for bad in (tmp_path / "other", out.parent):
        with pytest.raises(ValueError, match="refusing"):
            build(FakeFiles(), bad)
    # a build that fails (the island is gone from the game) leaves the last build as it was: it was removed first
    with pytest.raises((KeyError, FileNotFoundError)):
        build(FakeFiles())
    assert (out / PATH).is_file() and (out / "modinfo.json").is_file() and not binned
    assert sorted(p.name for p in out.parent.iterdir()) == [out.name]  # no staging folder left beside it
    assert build(a7m_reader.CGameFiles(_tree())) == [PATH] and len(binned) == 1  # the old copy goes to the bin
    assert a7m_edit1800.edit_name(PATH) == "1800_isle.json"


DECORATION = (
    "<Asset><Template>VisualObject</Template><Values><Standard><GUID>200</GUID><Name>Crates</Name></Standard>"
    "<Object><Variations><Item><Filename>data/graphics/props/crates.cfg</Filename></Item></Variations></Object>"
    "</Values></Asset><Asset><Template>Prop</Template><Values><Standard><GUID>300</GUID><Name>Fence</Name></Standard>"
    "<Object><Variations><Item><Filename>data/graphics/props/fence.prp</Filename></Item></Variations></Object>"
    "</Values></Asset>"
)


def _tree_with_prop_object() -> FakeTree:
    """The island with an object of the Prop group (as 1800's fences and lamps: its transform in a Prop block) and
    the game's decoration in its assets."""
    from annotoolbox.formats.filedb import SAttrib, STag

    tree = _tree()
    isl = a7m_reader.CIslandFile1800(tree.read_bytes(PATH), tree.read_bytes(PATH[:-4] + ".a7minfo"))
    turn = struct.pack("<4f", 0.7, 0.0, -0.7, 0.1)
    prop = STag("Prop", [SAttrib("Position", struct.pack("<3f", 3, 1, 3)), SAttrib("Rotation", turn),
                        SAttrib("Scale", struct.pack("<3f", 1, 2, 4)), SAttrib("Color", struct.pack("<4f", 1, 1, 1, 1))])  # fmt: skip
    obj = STag("#1", [SAttrib("guid", struct.pack("<I", 300)), SAttrib("ID", struct.pack("<2I", 1, 0x77)),
                     SAttrib("Position", struct.pack("<3f", 3, 1, 3)), SAttrib("Direction", struct.pack("<f", 1.0)), prop])  # fmt: skip
    isl._areas_data()[0][1].find("AreaObjectManager").children.append(STag("Prop", [STag("objects", [obj])]))
    files = dict(tree.files)
    files[PATH] = isl.write()
    files["data/config/export/main/asset/assets.xml"] = ASSETS.replace("</Assets>", DECORATION + "</Assets>")
    return FakeTree(files)


def test_an_object_of_the_prop_group_is_edited_in_its_prop_block(tmp_path):
    tree = _tree_with_prop_object()
    viewer = a7mviewer.CA7mViewer(
        installs.GAMES["anno1800"], tree, PATH, tmp_path / "cache", tmp_path / "1800_isle.json"
    )
    fence = next(o for o in viewer.island()["objects"] if o["guid"] == 300)
    assert fence["clone"] and fence["scale"] == [1, 2, 4] and fence["rotation"] == pytest.approx([0.7, 0, -0.7, 0.1])
    edits = islandedits.empty()
    edits["objects"][fence["key"]] = {"position": [3.5, 1, 3], "rotation": [0, 0, 0, 1], "scale": [2, 2, 2]}
    done = a7m_edit1800.apply(a7m_reader.CGameFiles(tree), PATH, edits)
    assert done.report["objects changed"] == 1 and done.report["unmatched"] == []
    out = a7m_reader.CIslandFile1800(done.a7m, tree.read_bytes(PATH[:-4] + ".a7minfo"))
    (o,) = out._areas_data()[0][1].path("AreaObjectManager/Prop/objects").children
    assert (
        struct.unpack("<3f", o.path("Prop/Position").data)
        == (3.5, 1, 3)
        == struct.unpack("<3f", o.find("Position").data)
    )
    assert struct.unpack("<4f", o.path("Prop/Rotation").data) == (0, 0, 0, 1)
    assert struct.unpack("<3f", o.path("Prop/Scale").data) == (2, 2, 2) and o.find("Mesh") is None


def test_the_library_offers_the_games_decoration_placed_as_copies(tmp_path):
    tree = _tree_with_prop_object()
    viewer = a7mviewer.CA7mViewer(
        installs.GAMES["anno1800"], tree, PATH, tmp_path / "cache", tmp_path / "1800_isle.json"
    )
    rows = {r["guid"]: r for r in viewer.library()["anno1800"]}
    assert set(rows) == {100849, 200, 300} and rows[200]["cfg"] == "data/graphics/props/crates.cfg"
    assert rows[200]["template"] == "VisualObject" and viewer.vanilla_materials() == []
    session = CEditSession(json.loads(viewer.island_json()), viewer.edits())
    session.place(rows[200], [2.0, 0.5, 1.0])
    session.place(rows[300], [1.0, 0.5, 1.0], yaw_rad=1.0)
    assets = tree.read_bytes("data/config/export/main/asset/assets.xml")
    done = a7m_edit1800.apply(a7m_reader.CGameFiles(tree), PATH, session.m_edits, assets)
    assert done.report["objects added"] == 2 and done.report["unmatched"] == []
    out = a7m_reader.CIslandFile1800(done.a7m, tree.read_bytes(PATH[:-4] + ".a7minfo"))
    crates = [o for o in out.main_objects("GameObject").children if hasattr(o, "find") and o.find("guid").u32() == 200]
    assert len(crates) == 1 and crates[0].find("Mesh") is not None  # a copy of the island's plain object
    (fence,) = [
        o
        for o in out._areas_data()[0][1].path("AreaObjectManager/Prop/objects").children
        if o.find("ID").data[:4] != struct.pack("<I", 1)
    ]
    assert fence.find("guid").u32() == 300 and struct.unpack("<3f", fence.path("Prop/Position").data) == (1, 0.5, 1)


def test_a_blank_island_is_its_vanilla_island_without_objects_and_props(tmp_path):
    tree = _tree_with_prop_object()
    edits_path = tmp_path / "1800_isle.json"
    islandedits.save(edits_path, {**islandedits.empty(), "source": PATH, "game": "anno1800", "clear": True})
    viewer = a7mviewer.CA7mViewer(installs.GAMES["anno1800"], tree, PATH, tmp_path / "cache", edits_path)
    island = viewer.island()
    assert island["props"] == [] and {o["group"] for o in island["objects"]} == {"EditorObject"}
    assert island["water"]["rivers"][0]["points"] == [[0.0, 0.0, 1.0], [3.0, 0.0, 1.0]]
    assert {r["guid"] for r in viewer.library()["anno1800"]} >= {100849, 200}  # what it carried can still be placed
    session = CEditSession(json.loads(viewer.island_json()), viewer.edits())
    session.place(next(r for r in viewer.library()["anno1800"] if r["guid"] == 100849), [1.0, 0.5, 1.0])
    viewer.save_edits(session.m_edits)
    saved = islandedits.load(edits_path)
    assert saved["clear"] is True and saved["source"] == PATH
    done = a7m_edit1800.apply(a7m_reader.CGameFiles(tree), PATH, saved)
    assert done.report["cleared"] == {"GameObject": 1, "Prop": 1, "props": 1} and done.report["objects added"] == 1
    out = a7m_reader.CIslandFile1800(done.a7m, tree.read_bytes(PATH[:-4] + ".a7minfo"))
    (tree_copy,) = [o for o in out.main_objects("GameObject").children if hasattr(o, "find")]
    assert struct.unpack("<3f", tree_copy.find("Position").data) == (1, 0.5, 1)
    assert not out.rd3d.path("PropGrid/Instances").children and out.main_objects("EditorObject").children


def test_heights_and_paint_reach_an_1800_islands_own_maps(tmp_path):
    import numpy as np

    from annotoolbox.editor import groundedit

    tree = _tree()
    edits = islandedits.empty()
    edits["heights"] = {"2,2": 1.0}
    edits["paint"] = [{"material": 3, "radius": 2, "strength": 1.0, "points": [[3, 2]]}]
    done = a7m_edit1800.apply(a7m_reader.CGameFiles(tree), PATH, edits)
    out = a7m_reader.CIslandFile1800(done.a7m, tree.read_bytes(PATH[:-4] + ".a7minfo"))
    before = a7m_reader.CIslandFile1800(tree.read_bytes(PATH), tree.read_bytes(PATH[:-4] + ".a7minfo"))
    hm = [np.frombuffer(i.rd3d.path("Terrain/CoarseHeightMap/map").data, "<i2").reshape(9, 9) for i in (before, out)]
    assert (hm[1] - hm[0])[4, 4] == 256 and (hm[1] - hm[0])[0, 0] == 0  # one world unit at corner (2, 2)
    ids = np.frombuffer(out.rd3d.path("Terrain/MaterialIDs2").data, "<u4").reshape(4, 4)
    alphas = np.frombuffer(out.rd3d.path("Terrain/MaterialAlphas2").data, "<u4").reshape(8, 8)
    mats, w = groundedit.layered_weights(ids, alphas, {3: 0, 7: 1})
    assert w[mats.index(3), 4, 6] > 0.9 and w[mats.index(7), 4, 6] < 0.1  # material 3 over the east half's 7
    assert done.report["paint"]["blocks"] > 0


def test_an_1800_islands_info_edits_are_written_in_its_own_layout(tmp_path):
    edits = {**islandedits.empty(), "source": PATH, "game": "anno1800", "meta": {"active_rect": [0, 0, 2, 2]}}
    done = a7m_edit1800.apply(a7m_reader.CGameFiles(_tree()), PATH, edits)
    assert done.report["meta"] == 1 and done.info is not None
    original = _tree().read_bytes(PATH[:-4] + ".a7minfo")
    assert filedb.version(done.info) == filedb.version(original)
    assert struct.unpack("<4i", filedb.read(done.info).find("ActiveMapRect").data) == (0, 0, 2, 2)
    a7m_edit1800.write_info(tmp_path, PATH, done)
    assert (tmp_path / PATH.replace(".a7m", ".a7minfo")).read_bytes() == done.info
    untouched = a7m_edit1800.apply(a7m_reader.CGameFiles(_tree()), PATH, {**islandedits.empty(), "source": PATH})
    assert untouched.info is None


def test_the_games_assets_are_read_again_for_another_tree_at_the_freed_ones_address():
    """The assets were kept by the tree's ``id()``: a new tree (another install folder, the other game's) that took
    the freed tree's address was read with the old one's assets."""

    def viewer(guid: int) -> a7mviewer.CA7mViewer:
        v = a7mviewer.CA7mViewer.__new__(a7mviewer.CA7mViewer)
        v.m_tree = FakeTree({"data/config/export/main/asset/assets.xml": ASSETS.replace("100849", str(guid))})
        return v

    first = viewer(7)
    assert first.game_assets().asset(7) is not None and first.game_assets() is first.game_assets()  # read once
    address = id(first.m_tree)
    del first
    for _ in range(100):  # the next tree usually takes the freed one's address at once
        other = viewer(9)
        if id(other.m_tree) == address:
            break
    assert other.game_assets().asset(9) is not None and other.game_assets().asset(7) is None
