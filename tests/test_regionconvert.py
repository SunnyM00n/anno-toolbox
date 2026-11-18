"""Island ports within an engine generation (docs/island-regions.md): the regions, the tables made from
the games' own files, an island ported between two regions of 117 and read back, the region to itself unchanged, the
mod and its pool asset; on the installed games (ANNO117_DIR, ANNO1800_DIR) vanilla islands converted and read back."""

import json
import struct
from collections import Counter

import numpy as np
import pytest

from annotoolbox.editor import gametransfer, islandcommands, islandconvert, regionconvert
from annotoolbox.editor.assetmodels import ASSETS_117, AssetModels
from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.formats import a7mread, filedb
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.formats.rda import write_v22
from annotoolbox.islands import regions, regiontables, sessionsettings, slotlist
from fakes import FakeFiles, asset_xml, installed

SOURCE = "data/base/provinces/roman/islands/pool/isle/isle.a7m"
OTHER = "data/base/provinces/celtic/islands/pool/moor/moor.a7m"
ROMAN_SET = "data/base/graphics/library/environment/material_sets/roman_materialset_02.xml"
CELTIC_SET = "data/base/graphics/library/environment/material_sets/celtic_materialset_02.xml"
SIZE = 4  # tiles; the material maps: 8 texels a tile in _gamedata, 2 in rd3d, ID blocks of 2 x 2 texels
ASSETS = (
    asset_xml("Slot", 2882, "Slot Roman Mountain")
    + asset_xml("Slot", 2969, "Slot Roman River")
    + asset_xml("Slot", 5281, "Slot Celtic Mountain")
    + asset_xml("Prop", 42525, "roman_rock_02")
    + asset_xml("Prop", 44712, "celtic_rock_02")
    + asset_xml("VisualObject", 900, "harbour crane")
)


def _set(*entries: tuple[int, str, str, str]) -> str:
    body = "".join(
        f"<Material><ID>{i}</>\n<ImportLabel>{label}</>\n<FileName>{file}</>\n<GroundType>{ground}</>\n</>\n"
        for i, label, ground, file in entries
    )
    return f"<MaterialSet><ShaderType>0</>\n{body}</>\n"


ROMAN = _set(
    (7, "sand_base", "None", "data/m/sand/beach_04/beach_04.xml"),
    (1, "grass", "Grass", "data/m/grass/grass_01_e/grass_01_e.xml"),
    (14, "rock_large", "Rock", "data/m/rock/cliff_limestone_01/cliff_limestone_01.xml"),
)
CELTIC = _set(
    (21, "", "None", "data/m/sand/beach_01/beach_01.xml"),
    (3, "Grass", "Grass", "data/m/grass/grass_01/grass_01.xml"),
    (9, "Rock3", "", "data/m/rock/celtic_cliffs_06/celtic_cliffs_06.xml"),
    (4, "Grass_variance", "GrassDry", "data/m/grass/grass_01_b_light/grass_01_b_light.xml"),
)


def ids_word(*mids: int) -> int:
    return sum(m << (5 * s) for s, m in enumerate(mids))


def _dds(words: np.ndarray) -> bytes:
    header = bytearray(128)
    header[:4] = b"DDS "
    header[12:16] = struct.pack("<I", words.shape[0])
    header[16:20] = struct.pack("<I", words.shape[1])
    return bytes(header) + words.astype("<u4").tobytes()


def _object(guid: int, serial: int, pos) -> Tag:
    return Tag(
        "#1",
        [
            Attrib("guid", struct.pack("<I", guid)),
            Attrib("ID", struct.pack("<I", serial) + b"\x01\x00\x00\x00"),
            Attrib("Position", struct.pack("<3f", *pos)),
        ],
    )


def _island(set_path: str, prop: str, guids: tuple[int, ...], ambient: str, vegetation: str) -> dict[str, bytes]:
    """A synthetic 117 island and its files: objects of ``guids``, one prop file, every ground map of one material
    ID in three of the six slots, a mesh node's used-material mask, a slot list."""
    path = SOURCE if "roman" in set_path else OTHER
    objs = [_object(g, k + 1, (k + 0.5, 0, 1.5)) for k, g in enumerate(guids)]
    area = Tag("doc", [Tag("AreaObjectManager", [Tag("GameObject", [Tag("objects", objs)])])])
    session = Tag(
        "SessionSettings",
        [
            Attrib("GlobalAmbientName", ambient.encode("utf-16-le")),
            Attrib("PlayableArea", struct.pack("<4i", 0, 0, SIZE, SIZE)),
            Attrib("VegetationPropSetName", vegetation.encode()),
        ],
    )
    water = Tag(
        "Water",
        [Attrib("x", struct.pack("<I", SIZE)), Attrib("y", struct.pack("<I", SIZE)), Attrib("bits", b"\xff" * 16)],
    )
    gm = Tag(
        "GameSessionManager",
        [
            session,
            Tag("WorldManager", [water]),
            Tag("AreaManagerData", [Tag("#1", [Attrib("Data", filedb.write(area))])]),
        ],
    )
    first = 7 if "roman" in set_path else 21
    word = ids_word(first, 1 if first == 7 else 3, 14 if first == 7 else 9)
    alpha = sum(31 << (5 * s) for s in range(1)) | (10 << 5)
    terrain = Tag(
        "Terrain",
        [
            Attrib("MaterialSetFileName", set_path.encode("utf-16-le")),
            Attrib("MaterialIDs2", np.full((SIZE, SIZE), word, "<u4").tobytes()),
            Attrib("MaterialAlphas2", np.full((2 * SIZE, 2 * SIZE), alpha, "<u4").tobytes()),
            Attrib("UsedMaterialBits", struct.pack("<2I", (1 << first) | (1 << 14), 0)),
        ],
    )
    props = Tag(
        "PropGrid",
        [
            Tag("FileNames", [Attrib("#32768", prop.encode("utf-16-le"))]),
            Tag(
                "Instances",
                [Tag("#1", [Attrib("Index", struct.pack("<I", 0)), Attrib("Position", struct.pack("<3f", 1, 0, 1))])],
            ),
        ],
    )
    a7m = write_v22(
        {"gamedata.data": filedb.write(Tag("", [gm])), "rd3d.data": filedb.write(Tag("", [terrain, props]))}
    )
    info = Tag("", [Tag("ObjectMetaInfo", [Tag("RandomSlotObjects", [])])])
    slotlist.write(
        info,
        {
            g: [(struct.pack("<I", k + 1) + b"\x01\x00\x00\x00", (k + 0.5, 0, 1.5))]
            for k, g in enumerate(guids)
            if g < 9000
        },
    )
    mesh = Tag(
        "", [Tag("Meshes", [Tag("map", [Tag("#1", [Attrib("UsedMaterialBits", struct.pack("<I", 1 << first))])])])]
    )
    folder = path.rsplit("/", 1)[0] + "/_gamedata/" + path.rsplit("/", 1)[1][:-4] + "/"
    return {
        path: a7m,
        path[:-4] + ".a7minfo": filedb.write(info),
        path[:-4] + ".a7me": b"<a7me/>",
        folder + "materialmaps/0/matid.dds": _dds(np.full((2 * SIZE, 2 * SIZE), word, np.uint32)),
        folder + "materialmaps/0/matalpha.dds": _dds(np.full((8 * SIZE, 8 * SIZE), alpha, np.uint32)),
        folder + "meshes/0/0x0.tmc": filedb.write(mesh),
        folder + "mapimage.png": b"png",
    }


@pytest.fixture
def game():
    files = {
        **_island(ROMAN_SET, "data/base/graphics/roman/props/vegetation/trees/tree_laurus_01_a.cfg", (2882, 2969, 42525, 900), "amb_roman_01", "ls_heartlands_01"),
        **_island(CELTIC_SET, "data/base/graphics/celtic/props/vegetation/trees/tree_beech_02_a.cfg", (5281, 44712, 900), "amb_celtic_01", "ls_wetlands_01"),
        ROMAN_SET: ROMAN.encode(),
        CELTIC_SET: CELTIC.encode(),
        ASSETS_117.assets: (
            "<Assets>" + ASSETS
            + asset_xml("RandomIsland", 31655, "isle", f"<RandomIsland><FilePath>{SOURCE}</FilePath><IslandRegion>Roman</IslandRegion></RandomIsland>")
            + "</Assets>"
        ).encode(),
    }  # fmt: skip
    side = islandconvert.GameSide("anno117", FakeFiles(files))
    side._models = AssetModels(files[ASSETS_117.assets], "", ASSETS_117)
    return side


def test_the_regions_are_told_by_the_islands_path():
    assert regions.region_of("anno117", SOURCE).key == "roman"
    assert (
        regions.region_of("anno1800", "data/dlc01/sessions/islands/pool/moderate_dst_02/moderate_dst_02.a7m").key
        == "cape"
    )
    assert (
        regions.region_of("anno1800", "data/sessions/islands/pool/moderate/moderate_l_02/moderate_l_02.a7m").key
        == "moderate"
    )
    assert (
        regions.region_of("anno1800", "data/dlc03/sessions/islands/pool/colony03_d_01/colony03_d_01.a7m").key
        == "colony03"
    )
    assert regions.region_of("anno1800", "data/dlc10/scenario02/sessions/islands/pool/x/x.a7m") is None
    assert regions.plain_name("Slot Roman Mountain", "anno117") == regions.plain_name("Slot Celtic Mountain", "anno117")
    assert regions.region("anno1800:colony02").island_region == "Africa"


def test_the_material_table_goes_by_family_then_ground_type_then_colour():
    table = regiontables.material_table(ROMAN, CELTIC)
    assert table == {7: 21, 1: 3, 14: 9}
    shades = {
        "data/m/grass/grass_01_e/grass_01_e.xml": (200, 200, 100),
        "data/m/grass/grass_01/grass_01.xml": (0, 90, 0),
    }
    shades["data/m/grass/grass_01_b_light/grass_01_b_light.xml"] = (190, 190, 110)
    drier = _set((1, "grass", "GrassDry", "data/m/grass/grass_01_e/grass_01_e.xml"))
    assert regiontables.material_table(drier, CELTIC, lambda _side, f: shades.get(f))[1] == 4  # same ground type
    no_kind = _set((1, "grass", "Flowers", "data/m/grass/grass_01_e/grass_01_e.xml"))
    assert regiontables.material_table(no_kind, CELTIC, lambda _side, f: shades.get(f))[1] == 4  # nearest colour
    snow = _set((5, "", "Grass", "data/t/p_snow_01.xml"))
    assert regiontables.material_table(snow, CELTIC)[5] == 3  # the target has no snow: grass


def test_props_go_kind_by_kind_in_order_of_use_and_neutral_ones_stay():
    src = regiontables.Inventory(
        props=Counter({"r/trees/laurus_a": 9, "r/trees/laurus_b": 5, "r/rocks/rock_1": 2, "lib/grass/tuft": 7})
    )
    dst = regiontables.Inventory(props=Counter({"c/trees/beech": 20, "c/rocks/rock_7": 1, "lib/grass/tuft": 3}))
    table = regiontables.prop_table(src, dst)
    assert table == {
        "r/trees/laurus_a": "c/trees/beech",
        "r/trees/laurus_b": "c/trees/beech",
        "r/rocks/rock_1": "c/rocks/rock_7",
    }
    lone = regiontables.Inventory(props=Counter({"r/water/lily": 1}))
    assert regiontables.prop_table(lone, dst) == {} and regiontables.prop_table(lone, dst, drop_unmapped=True) == {
        "r/water/lily": None
    }


def test_objects_go_by_name_without_region_words_and_have_no_counterpart_removed(game):
    src = regiontables.Inventory(objects=Counter({2882: 3, 2969: 2, 42525: 1, 900: 4}))
    dst = regiontables.Inventory(objects=Counter({5281: 5, 44712: 1, 900: 2}))
    table = regiontables.object_table(src, dst, (game.asset_names, game.asset_names), ("anno117", "anno117"))
    assert table == {2882: 5281, 2969: None, 42525: 44712}  # the crane is no region's: kept


def test_an_island_converted_to_another_region_reads_back_with_the_targets_things(game):
    done = islandconvert.convert(game, game, islandconvert.Request("anno117", SOURCE, "anno117:celtic"), colours=False)
    assert done.target == "data/base/provinces/celtic/islands/pool/isle_celtic/isle_celtic.a7m"
    isl = a7m_edit117.IslandFile(done.a7m, done.info)
    t = isl.rd.path("Terrain")
    assert t.find("MaterialSetFileName").data.decode("utf-16-le") == CELTIC_SET
    assert int(np.frombuffer(t.find("MaterialIDs2").data, "<u4")[0]) == ids_word(21, 3, 9)
    assert (
        t.find("MaterialAlphas2").data
        == game.files.files[SOURCE.replace("isle.a7m", "") + "_gamedata/isle/materialmaps/0/matalpha.dds"][128:][
            : len(t.find("MaterialAlphas2").data)
        ]
    )
    assert struct.unpack("<2I", t.find("UsedMaterialBits").data) == ((1 << 21) | (1 << 9), 0)
    ids = np.frombuffer(done.gamedata["materialmaps/0/matid.dds"][128:], "<u4")
    assert int(ids[0]) == ids_word(21, 3, 9)
    mesh = filedb.read(done.gamedata["meshes/0/0x0.tmc"]).path("Meshes/map")
    assert struct.unpack("<I", mesh.children[0].find("UsedMaterialBits").data)[0] == 1 << 21
    assert sessionsettings.read(isl.gm) == {
        "ambient": "amb_celtic_01",
        "vegetation": "ls_wetlands_01",
        "playable_area": [0, 0, SIZE, SIZE],
    }
    assert regiontables.prop_files(isl.rd) == ["data/base/graphics/celtic/props/vegetation/trees/tree_beech_02_a.cfg"]
    guids = [struct.unpack_from("<I", o.find("guid").data)[0] for _g, o in a7mread.object_tags(isl.gm)]
    assert guids == [5281, 44712, 900]  # the river slot has no counterpart: removed
    assert sorted(slotlist.read(isl.info)) == [900, 5281]  # the river slot's entry went with it
    assert done.report["objects removed"] == 1 and done.side == {done.target[:-4] + ".a7me": b"<a7me/>"}
    assert "<IslandRegion>Celtic</IslandRegion>" in done.asset and f"<FilePath>{done.target}</FilePath>" in done.asset
    assert 'GUID="31655"' in done.asset


def test_a_region_to_itself_gives_the_island_back_byte_for_byte(game):
    done = islandconvert.convert(game, game, islandconvert.Request("anno117", SOURCE, "anno117:roman", name="same"))
    assert done.a7m == game.read(SOURCE) and done.info == game.read(SOURCE[:-4] + ".a7minfo")
    folder = "data/base/provinces/roman/islands/pool/isle/_gamedata/isle/"
    assert done.gamedata == {k[len(folder) :]: v for k, v in game.files.files.items() if k.startswith(folder)}
    isl = a7m_edit117.IslandFile(game.read(SOURCE), game.read(SOURCE[:-4] + ".a7minfo"))
    out, report = regionconvert.apply(isl, None, regiontables.Tables())
    assert out == {} and isl.write() == game.read(SOURCE)  # nothing to change: the members are written as read


def test_the_mod_holds_the_island_its_side_files_and_the_pool_asset(game, tmp_path):
    done = islandconvert.convert(game, game, islandconvert.Request("anno117", SOURCE, "anno117:celtic"), colours=False)
    out = tmp_path / "conv"
    written = islandconvert.write_mod(out, "conv", "Ported", "anno117", [done])
    assert written == [done.target]
    assert (out / done.target).read_bytes() == done.a7m and (out / (done.target[:-4] + ".a7me")).is_file()
    gamedata = out / done.target.rsplit("/", 1)[0] / "_gamedata" / "isle_celtic" / "materialmaps/0/matid.dds"
    assert gamedata.read_bytes() == done.gamedata["materialmaps/0/matid.dds"]
    assert "<IslandRegion>Celtic" in (out / "data/base/config/export/assets.xml").read_text(encoding="utf-8")
    assert json.loads((out / "modinfo.json").read_text(encoding="utf-8"))["ModID"] == "conv"


def test_the_targets_are_the_generations_regions_and_an_unknown_one_is_refused(game):
    offered = [t for t, _label in islandconvert.targets("anno117", SOURCE)]
    assert "anno117:roman" not in offered and "anno117:celtic" in offered and "anno1800:colony03" in offered
    assert islandconvert.targets("anno2070", "data/levels/islands/x.isd") == []  # another generation: none here
    with pytest.raises(ValueError, match="unknown region"):
        islandconvert.convert(game, game, islandconvert.Request("anno117", SOURCE, "anno2070:north"))


def test_a_node_grafts_value_by_value_and_keeps_what_only_the_target_has():
    src = Tag("T", [Attrib("a", b"1"), Tag("L", [Tag("#1", [Attrib("v", b"s")])]), Attrib("only_src", b"x")])
    dst = Tag(
        "T", [Attrib("a", b"0"), Attrib("only_dst", b"d"), Tag("L", [Tag("#1", [Attrib("v", b"d")]), Tag("#1", [])])]
    )
    assert gametransfer.graft(src, dst) == 2
    assert dst.find("a").data == b"1" and dst.find("only_dst").data == b"d" and dst.find("only_src") is None
    assert [c.find("v").data for c in dst.find("L").children] == [b"s"]  # a list of entries: the source's whole


def test_the_ground_is_carried_into_another_resolution():
    ids = np.full((4, 4), ids_word(7, 1, 0), np.uint32)  # 8 x 8 texels: 2 a tile on 4 tiles
    alphas = np.full((8, 8), 31 | (16 << 5), np.uint32)
    ground = gametransfer.Ground(ids, alphas, 4, {7: 21, 1: 3}, {7: 0, 1: 1}, {21: 0, 3: 1})
    new_ids, new_alphas, used = gametransfer.encode_map(ground, 2, 4)  # 1 texel a tile, blocks of 2
    assert new_ids.shape == (2, 2) and new_alphas.shape == (4, 4) and used == (1 << 21) | (1 << 3)
    assert {int(new_ids[0, 0]) & 31, (int(new_ids[0, 0]) >> 5) & 31} == {21, 3}


def test_the_command_line_lists_the_regions(capsys):
    import argparse

    parser = argparse.ArgumentParser()
    islandcommands.register(parser.add_subparsers(dest="cmd", required=True))
    args = parser.parse_args(["regions", "anno1800"])
    assert args.func(args) == 0
    assert "anno1800:colony03\tThe Arctic\tArctic" in capsys.readouterr().out
    with pytest.raises(ValueError, match="unknown region"):
        bad = parser.parse_args(["port", "anno117", SOURCE, "--to", "anno117:atlantis", "--out", "x"])
        bad.func(bad)


INSTALLED_117, INSTALLED_1800 = installed("anno117"), installed("anno1800")


def placed_props(rd) -> list[str]:
    """The prop file of every placed prop (a file no prop uses any more is left out)."""
    names = [regiontables.norm(n) for n in regiontables.prop_files(rd)]
    return [names[struct.unpack_from("<I", p.find("Index").data)[0]] for p in rd.path("PropGrid/Instances").children]


ROMAN_MEDIUM = "data/base/provinces/roman/islands/pool/roman_island_medium_03/roman_island_medium_03.a7m"


def _side(key):
    from annotoolbox.editor.formats.a7m.reader import GameFiles
    from annotoolbox.islands.islandview import Files

    return islandconvert.GameSide(key, Files(INSTALLED_117) if key == "anno117" else GameFiles(INSTALLED_1800))


@pytest.mark.skipif(INSTALLED_117 is None, reason="needs ANNO117_DIR")
def test_installed_117_island_to_albion_and_back_to_latium_unchanged():
    side = _side("anno117")
    done = islandconvert.convert(
        side, side, islandconvert.Request("anno117", ROMAN_MEDIUM, "anno117:celtic"), colours=False
    )
    isl = a7m_edit117.IslandFile(done.a7m, done.info)
    assert sessionsettings.read(isl.gm)["ambient"] == "amb_celtic_01"
    trees = [p for p in placed_props(isl.rd) if "/trees/" in p]
    assert trees and not [p for p in trees if "/roman/" in p]  # every tree is Albion's (a shared grass tuft stays)
    assert done.report["ground"]["maps"] >= 4 and done.asset
    same = islandconvert.convert(side, side, islandconvert.Request("anno117", ROMAN_MEDIUM, "anno117:roman"))
    assert same.a7m == side.read(ROMAN_MEDIUM)


@pytest.mark.skipif(INSTALLED_1800 is None, reason="needs ANNO1800_DIR")
def test_installed_1800_island_to_the_arctic_reads_back():
    from annotoolbox.editor.formats.a7m.reader import IslandFile1800

    side = _side("anno1800")
    path = "data/sessions/islands/pool/moderate/moderate_l_02/moderate_l_02.a7m"
    done = islandconvert.convert(
        side, side, islandconvert.Request("anno1800", path, "anno1800:colony03"), colours=False
    )
    isl = IslandFile1800(done.a7m, done.info)
    assert isl.rd.path("Terrain").find("MaterialSetFileName").data.decode("utf-16-le").endswith("arctic_01.xml")
    assert isl.write() == done.a7m or filedb.read(a7mread.members(isl.write(), "rd3d.data")[0]) is not None
    assert "<IslandRegion>Arctic</IslandRegion>" in done.asset


@pytest.mark.skipif(INSTALLED_117 is None or INSTALLED_1800 is None, reason="needs ANNO117_DIR and ANNO1800_DIR")
def test_installed_117_island_carried_into_1800_reads_back():
    from annotoolbox.editor.formats.a7m.reader import IslandFile1800

    src, dst = _side("anno117"), _side("anno1800")
    path = "data/base/provinces/celtic/islands/pool/celtic_island_medium_01/celtic_island_medium_01.a7m"
    done = islandconvert.convert(src, dst, islandconvert.Request("anno117", path, "anno1800:moderate"), colours=False)
    isl = IslandFile1800(done.a7m, done.info)
    back = a7m_edit117.IslandFile(src.read(path), src.read(path[:-4] + ".a7minfo"))
    # the Old World's 256-tile islands are not in its pool (no RandomIsland asset): a larger donor carries it
    assert isl.size > back.size and done.report["embedded"]["place"][2] == back.size
    assert placed_props(isl.rd) and not [p for p in placed_props(isl.rd) if p.startswith("data/base/")]
    assert done.report["objects kept"] > 0 and "<IslandRegion>Moderate</IslandRegion>" in done.asset


def test_1800s_slot_list_is_rebuilt_from_the_slot_objects_and_keeps_its_header():
    area = Tag("doc", [Tag("AreaObjectManager", [Tag("GameObject", [Tag("objects", [_object(614, 9, (5, 0, 6))])])])])
    gm = Tag("GameSessionManager", [Tag("AreaManagerData", [Tag("#1", [Attrib("Data", filedb.write(area))])])])
    item = Tag(
        "#1",
        [Attrib("ObjectId", bytes(8)), Attrib("ObjectGuid", struct.pack("<I", 614)), Attrib("Position", bytes(12))],
    )
    header = Tag("#1", [Tag("value", [Attrib("id", b"\x03\x00")])])
    info = Tag("", [Tag("ObjectMetaInfo", [Tag("SlotObjects", [header, Tag("#1", [item, item])])])])

    class Donor:
        pass

    donor = Donor()
    donor.gm, donor.info = gm, info
    report = gametransfer.refresh_slots(donor)
    groups = info.path("ObjectMetaInfo/SlotObjects").children
    assert groups[0].find("value") is not None and report["slot items"] == 1
    (only,) = groups[1].children
    assert only.find("ObjectId").data == struct.pack("<I", 9) + b"\x01\x00\x00\x00"
    assert struct.unpack("<3f", only.find("Position").data) == (5, 0, 6)


def test_a_guid_both_games_place_is_not_taken_for_one_asset():
    """117's GUID 5 and 1800's GUID 5 are two assets: the table must map the first, not leave it as the second."""
    src = regiontables.Inventory(objects=Counter({5: 2}))
    dst = regiontables.Inventory(objects=Counter({5: 3, 7: 1}))
    src_names = {5: ("Slot", "Slot Roman Mountain")}.get
    dst_names = {5: ("Building", "Harbour"), 7: ("Slot", "Slot Colony Mountain")}.get
    table = regiontables.object_table(src, dst, (src_names, dst_names), ("anno117", "anno1800"))
    assert table == {5: 7}
    same = regiontables.object_table(src, dst, (src_names, src_names), ("anno117", "anno117"))
    assert same == {}  # within a game a GUID both regions place is one asset: kept


def test_an_asset_registered_in_a_region_without_subregions_drops_the_anchors_subregion():
    block = asset_xml(
        "RandomIsland", 77, "moderate_dst_02",
        "<RandomIsland><FilePath>cape.a7m</FilePath><IslandRegion>Moderate</IslandRegion>"
        "<IslandSubRegion>SecondaryRegion</IslandSubRegion></RandomIsland>",
    )  # fmt: skip
    side = islandconvert.GameSide("anno1800", FakeFiles({}))
    side._pool = [block]
    request = islandconvert.Request("anno1800", "cape.a7m", "anno1800:colony01")
    op = islandconvert.registration(side, request, regions.region("anno1800:colony01"), "x/new.a7m", "cape.a7m")
    assert "IslandSubRegion" not in op and "<IslandRegion>Colony01</IslandRegion>" in op
    back = islandconvert.registration(side, request, regions.region("anno1800:cape"), "x/new.a7m", "cape.a7m")
    assert "<IslandSubRegion>SecondaryRegion</IslandSubRegion>" in back


def test_a_broken_or_old_inventory_cache_is_made_again_not_a_crash(tmp_path, game):
    game.cache = tmp_path
    game.read = lambda path: b"not an island"  # nothing reads: the inventory is empty, but it is made
    region = regions.region("anno117:roman")
    first = game.inventory(region)
    file = tmp_path / "region-inventory-anno117-roman.json"
    assert file.is_file() and first.islands == 0
    file.write_text('{"stamp": "x", "inven', encoding="utf-8")  # half a file, as a stopped run leaves
    game._inventories.clear()
    assert game.inventory(region).islands == 0
    assert json.loads(file.read_text(encoding="utf-8"))["format"] == regiontables.INVENTORY_FORMAT


def test_the_inventory_cache_is_made_again_when_the_games_files_change(tmp_path, game, monkeypatch):
    game.cache = tmp_path
    game.read = lambda path: b"not an island"
    region = regions.region("anno117:roman")
    monkeypatch.setattr(game, "files_stamp", lambda: "update 1")
    game.inventory(region)
    file = tmp_path / "region-inventory-anno117-roman.json"
    first = json.loads(file.read_text(encoding="utf-8"))["stamp"]
    game._inventories.clear()
    monkeypatch.setattr(game, "files_stamp", lambda: "update 2")  # a game update with the same island paths
    game.inventory(region)
    assert json.loads(file.read_text(encoding="utf-8"))["stamp"] != first


def test_a_port_tells_its_stages(game):
    heard = []
    islandconvert.convert(
        game, game, islandconvert.Request("anno117", SOURCE, "anno117:celtic"), colours=False, log=heard.append
    )
    assert len(heard) >= 3 and "isle" in heard[0]


@pytest.mark.skipif(INSTALLED_117 is None or INSTALLED_1800 is None, reason="needs ANNO117_DIR and ANNO1800_DIR")
def test_installed_64_tile_1800_island_is_carried_onto_a_larger_117_donor():
    from annotoolbox.editor import groundedit

    src, dst = _side("anno1800"), _side("anno117")
    path = "data/sessions/islands/pool/moderate/moderate_d_01/moderate_d_01.a7m"
    done = islandconvert.convert(src, dst, islandconvert.Request("anno1800", path, "anno117:roman"), colours=False)
    isl, own = a7m_edit117.IslandFile(done.a7m, done.info), src.open(path)
    assert isl.size == 192 and done.report["embedded"]["place"] == [64, 64, 64, 192]
    inside = groundedit.coarse_heights(isl)[128:257, 128:257]  # corners 64..128 at two samples a tile
    assert np.abs(inside[::2, ::2] - groundedit.coarse_heights(own)[::2, ::2]).max() < 0.02
    assert struct.unpack("<4i", isl.info.find("ActiveMapRect").data)[0] >= 64  # the window moved with the island
    assert placed_props(isl.rd) and not [p for p in placed_props(isl.rd) if "terrain_props" in p]
