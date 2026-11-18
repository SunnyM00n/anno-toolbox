import re
import struct
import zlib

import numpy as np

from annotoolbox.editor import groundedit
from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.formats.rda import write_v22
from annotoolbox.islands import gameterrain, islandedits
from annotoolbox.islands.islandgrid import ground_at, read_bits
from annotoolbox.islands.islandterrain import TMC_VERTEX
from fakes import FakeFiles
from test_gameterrain import _page

SOURCE = "data/base/provinces/roman/islands/pool/isle/isle.a7m"
GAMEDATA = "data/base/provinces/roman/islands/pool/isle/_gamedata/isle/"
SET = "data/base/graphics/material_sets/test.xml"
SIZE = 32  # tiles: 2 x 2 height pages of level 0
SEA = -1.0  # world units: the whole island starts under the sea
TEXELS = 4  # material texels per tile; ids per 2 x 2 block


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _grid(name: str, values: np.ndarray) -> Tag:
    return Tag(name, [Attrib("width", _u32(values.shape[1])), Attrib("height", _u32(values.shape[0])),
                      Attrib("map", values.tobytes())])  # fmt: skip


def _bits(name: str, n: int, value: int) -> Tag:
    return Tag(
        name, [Attrib("x", _u32(n)), Attrib("y", _u32(n)), Attrib("bits", bytes([value]) * (n * (n + 31) // 32 * 4))]
    )


def _river() -> Tag:
    points = [
        Tag("#1", [Attrib("p", struct.pack("<3f", x, 0.5, 20.0)), Attrib("i", bytes(12)), Attrib("o", bytes(12)),
                   Attrib("w", struct.pack("<f", 2.0)), Attrib("u0", struct.pack("<3f", 1, 3 + x, 1))])
        for x in (4.0, 8.0, 12.0)
    ]  # fmt: skip
    path = Tag("Path", [Attrib("Minimum", bytes(12)), Attrib("Maximum", bytes(12)), Tag("BezierCurve", points)])
    return Tag("#1", [Attrib("guid", _u32(5)), Attrib("ID", _u32(3) + _u32(9)), Attrib("Position", bytes(12)),
                      Tag("BezierPath", [path]), Tag("River", [])])  # fmt: skip


def _coast() -> Tag:
    """A coast line of three points running east at z 6, facing south (d = (0, 1)... as the vanilla ones: the normal)."""
    pts = [
        Tag("#1", [Attrib("p", struct.pack("<3f", x, 0.4, 6.0)), Attrib("i", bytes(12)), Attrib("o", bytes(12)),
                   Attrib("d", struct.pack("<2f", 0.0, 1.0))])
        for x in (4.0, 8.0, 12.0)
    ]  # fmt: skip
    path = Tag("Path", [Attrib("Minimum", bytes(12)), Attrib("Maximum", bytes(12)), Tag("BezierCurve", pts)])
    return Tag("#1", [Attrib("guid", _u32(7)), Attrib("ID", _u32(5) + _u32(9)),
                      Attrib("Position", struct.pack("<3f", 4, 0.4, 6)), Tag("BezierPath", [path]), Tag("CoastLine", [])])  # fmt: skip


def _lake() -> Tag:
    pts = [Tag("#1", [Attrib("p", struct.pack("<3f", x, 0.2, z))]) for x, z in ((20, 20), (24, 20), (22, 24))]
    path = Tag("Path", [Attrib("Minimum", bytes(12)), Attrib("Maximum", bytes(12)), Tag("BezierCurve", pts)])
    return Tag("#1", [Attrib("guid", _u32(6)), Attrib("ID", _u32(4) + _u32(9)),
                      Attrib("Position", struct.pack("<3f", 20, 0.2, 20)), Tag("BezierPath", [path]), Tag("Lake", [])])  # fmt: skip


def _mesh() -> bytes:
    """The level-2 meshes of the island: one node (0, 0) of four tiles, its four corners."""
    rec = np.zeros(4, TMC_VERTEX)
    rec["x"], rec["z"] = [0, 128, 0, 128], [0, 0, 128, 128]  # 1/32 tile
    rec["y"] = 32768 + int(SEA * 256)
    rec["n"] = [128, 255, 128, 255, 128, 128]
    nodes = [Tag("#1", [Attrib("Indices", struct.pack("<6H", 0, 2, 1, 1, 2, 3)), Attrib("Vertices", rec.tobytes()),
                        Attrib("UsedMaterialBits", _u32(1 << 3))])]  # fmt: skip
    nodes += [Tag("#1", [Attrib("Indices", b""), Attrib("Vertices", b""), Attrib("UsedMaterialBits", _u32(0))])] * 3
    return filedb.write(
        Tag("", [Tag("Meshes", [Attrib("width", _u32(32)), Attrib("height", _u32(32)), Tag("map", nodes)])])
    )


def _dds(arr: np.ndarray) -> bytes:
    header = bytearray(128)
    header[:4], header[12:16], header[16:20] = b"DDS ", _u32(arr.shape[0]), _u32(arr.shape[1])
    return bytes(header) + arr.astype("<u4").tobytes()


def _material_maps(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Material 3 everywhere, material 7 laid over it on the east half (the set draws 3 first)."""
    ids = np.full((n // 2, n // 2), 3 | 7 << 5, np.uint32)
    alphas = np.full((n, n), 31, np.uint32)
    alphas[:, n // 2 :] |= 31 << 5
    return ids, alphas


def island() -> FakeFiles:
    """A 32 x 32 island under the sea, as 117 stores one: heightmaps, node bounds, pages, a stored mesh, material
    maps in its folder and in rd3d, a river and a lake."""
    w = 2 * SIZE + 1
    hm = np.full((w, w), int(SEA * 256), "<i2")
    levels = [
        Tag("#1", [_grid("MinYArray", np.full((n, n), -1, "<i2")), _grid("MaxYArray", np.full((n, n), -1, "<i2"))])
        for n in (SIZE, SIZE // 2)
    ]
    ids, alphas = _material_maps(SIZE)  # rd3d's copy: one texel per tile
    terrain = Tag("Terrain", [
        Attrib("MaterialMapWidth", _u32(SIZE * TEXELS)), Attrib("MaterialIDBlockSize", _u32(2)),
        Attrib("MaterialSetFileName", SET.encode("utf-16-le")), _grid("CoarseHeightMap", hm), Tag("Levels", levels),
        Attrib("MaterialIDs2", ids.tobytes()), Attrib("MaterialAlphas2", alphas.tobytes()),
        Attrib("UsedMaterialBits", _u32(1 << 3 | 1 << 7) * 4),
    ])  # fmt: skip
    rd3d = Tag("", [terrain, _grid("WaterInfos", np.full((8, 8, 4), 9, np.uint8))])
    area = filedb.write(
        Tag("", [Tag("AreaObjectManager", [Tag("EditorObject", [Tag("objects", [_river(), _lake(), _coast()])])])])
    )
    gm = Tag("GameSessionManager", [
        Tag("TerrainManager", [Tag("HeightMap", [Attrib("Width", _u32(w)), Attrib("Height", _u32(w)),
                                                 Attrib("HeightMap", (hm // 2).astype("<i2").tobytes())])]),
        Tag("WorldManager", [_bits("Water", SIZE, 0), _bits("RiverGrid", SIZE, 0x0F), _bits("FordGrid", SIZE, 0),
                             Tag("EnvironmentGrid", [Tag("EnvironmentGRid", [Attrib("x", _u32(8)), Attrib("y", _u32(8)),
                                                                              Attrib("val", bytes([20]) * 64)])])]),
        Tag("AreaManagerData", [Tag("#1", [Attrib("ByteCount", _u32(len(area))), Attrib("Data", area)])]),
    ])  # fmt: skip
    files = {
        SOURCE: write_v22({"gamedata.data": filedb.write(Tag("", [gm])), "rd3d.data": filedb.write(rd3d)}),
        SOURCE[:-4] + ".a7minfo": filedb.write(Tag("", [])),
        SET: b"<MaterialSet><Material><ID>3</>\n<FileName>a.xml</>\n</>\n<Material><ID>7</>\n<FileName>b.xml</>\n</>\n",
        GAMEDATA + "meshes/2/0x0.tmc": _mesh(),
    }
    for nx in (0, 1):
        for nz in (0, 1):
            files[f"{GAMEDATA}normalmaps/0/{nx}x{nz}.ctt"] = _page(lambda p: np.full((len(p), len(p)), SEA * 4), 16.0)
    ids, alphas = _material_maps(SIZE * TEXELS)
    files[GAMEDATA + "materialmaps/0/matid.dds"], files[GAMEDATA + "materialmaps/0/matalpha.dds"] = (
        _dds(ids),
        _dds(alphas),
    )
    return FakeFiles(files)


def _hill(edits: dict, x: int, z: int, r: int = 3, top: float = 3.0) -> dict:
    for zz in range(z - r, z + r + 1):
        for xx in range(x - r, x + r + 1):
            d = np.hypot(xx - x, zz - z)
            if d <= r:
                edits["heights"][f"{zz},{xx}"] = round(top * 0.5 * (1 + np.cos(np.pi * d / r)), 3)
    return edits


def _reopen(files: FakeFiles, done) -> tuple[a7m_edit117.IslandFile, dict]:
    back = a7m_edit117.IslandFile(done.a7m, files.read(SOURCE[:-4] + ".a7minfo"))
    return back, {**{p[len(GAMEDATA) :]: files.read(p) for p in files.iter_paths(GAMEDATA)}, **done.gamedata}


def test_a_raised_hill_reaches_the_heightmaps_the_pages_the_meshes_and_the_land():
    files = island()
    done = a7m_edit117.apply(files, SOURCE, _hill(islandedits.empty(), 6, 6), template=None)
    back, gd = _reopen(files, done)
    hm = np.frombuffer(back.rd.path("Terrain/CoarseHeightMap/map").data, "<i2").reshape(65, 65) / 256
    assert hm[12, 12] == SEA + 3.0 and hm[40, 40] == SEA  # two samples per tile: corner (6, 6) is sample 12
    game = np.frombuffer(back.gm.path("TerrainManager/HeightMap/HeightMap").data, "<i2").reshape(65, 65) / 128
    assert game[12, 12] == SEA + 3.0
    # the page the hill stands on is rebuilt by the game on the raised ground; the others keep every byte
    assert sorted(k for k in done.gamedata if k.startswith("normalmaps/")) == ["normalmaps/0/0x0.ctt"]
    page = gameterrain.rebuild_page(gd["normalmaps/0/0x0.ctt"])
    deltas = groundedit.corner_deltas(_hill(islandedits.empty(), 6, 6), SIZE)
    centre = (np.arange(528) - 8 + 0.5) * 16 / 512
    assert np.abs(page - SEA - ground_at(deltas, *np.meshgrid(centre, centre))).max() < 2 / 256
    # the stored mesh's corner on the hill's flank rises with it; the bounds of the nodes widen to the top
    node = filedb.read(gd["meshes/2/0x0.tmc"]).path("Meshes/map").children[0]
    rec = np.frombuffer(node.find("Vertices").data, TMC_VERTEX)
    assert rec["y"][3] == 32768 + int(SEA * 256) + round(256 * deltas[4, 4])
    assert rec["n"][3, 1] < 255  # lit by the new slope
    top = np.frombuffer(back.rd.path("Terrain/Levels").children[0].find("MaxYArray").find("map").data, "<i2")
    assert top.reshape(SIZE, SIZE)[6, 6] == 2  # ceil(2.0 world units)
    land = np.asarray(read_bits(back.gm.path("WorldManager/Water")))
    assert land[6, 6] == 1 and land[20, 20] == 0 and 0 < done.report["heights"]["land tiles changed"] < 40
    info = np.frombuffer(back.rd.path("WaterInfos/map").data, np.uint8).reshape(8, 8, 4)
    assert info[1, 1, 0] == 0 and info[7, 7, 0] == 9  # dry over the hill, the rest as it was


def test_paint_lays_the_islands_own_material_into_every_material_map():
    files = island()
    edits = islandedits.empty()
    edits["paint"] = [
        {"material": 7, "radius": 2, "strength": 1.0, "points": [[4, 4], [6, 4]]},
        {"material": 99, "radius": 2, "strength": 1.0, "points": [[4, 4]]},  # not in the island's set
        {"material": 7, "radius": 2, "strength": 1.0, "points": []},  # a hand-edited file: nothing to lay,
        {"material": 7, "radius": 2, "strength": 1.0},  # ... and it stopped the build ("min() arg is empty")
    ]
    assert groundedit.painted_strokes(edits["paint"], {3: 0, 7: 1}) == edits["paint"][:1]
    word = {
        "material": "sand",
        "radius": 2,
        "strength": 1.0,
        "points": [[4, 4]],
    }  # a material that is no number stopped it too
    assert (
        groundedit.painted_strokes([word], {3: 0, 7: 1}) == []
        and groundedit.used_mask([word, edits["paint"][0]]) == 1 << 7
    )
    assert groundedit.other_materials([word], []) == 1  # reported with the strokes of a material outside the set
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    back, gd = _reopen(files, done)
    assert done.report["paint"] == {"strokes": 1, "other materials": 1, "blocks": done.report["paint"]["blocks"]}
    assert any("outside the island's set" in u for u in done.report["unmatched"])
    order = {3: 0, 7: 1}
    dds = gd["materialmaps/0/matid.dds"], gd["materialmaps/0/matalpha.dds"]
    ids, alphas = (np.frombuffer(d[128:], "<u4").reshape(-1, int.from_bytes(d[16:20], "little")) for d in dds)
    mats, w = groundedit.layered_weights(ids, alphas, order)
    assert w[mats.index(7), 4 * TEXELS, 5 * TEXELS] > 0.95  # under the stroke
    assert w[mats.index(3), 4 * TEXELS, 12 * TEXELS] == 1.0  # far from it, as it was
    rd_ids = np.frombuffer(back.rd.path("Terrain/MaterialIDs2").data, "<u4").reshape(16, 16)
    rd_al = np.frombuffer(back.rd.path("Terrain/MaterialAlphas2").data, "<u4").reshape(32, 32)
    mats, w = groundedit.layered_weights(rd_ids, rd_al, order)
    assert w[mats.index(7), 4, 5] > 0.8  # one texel a tile: its centre half a tile off the stroke
    node = filedb.read(gd["meshes/2/0x0.tmc"]).path("Meshes/map").children[0]
    assert struct.unpack("<I", node.find("UsedMaterialBits").data)[0] == 1 << 3 | 1 << 7


def test_the_islands_own_rivers_and_lakes_are_what_the_water_tool_edits():
    files = island()
    view = a7m_edit117.IslandFile(files.read(SOURCE), files.read(SOURCE[:-4] + ".a7minfo"))
    rivers, lakes = groundedit._water_objects(view)
    net = groundedit.water_network(
        [("River", [struct.unpack("<3f", p.find("p").data) for p in rivers[0].path("BezierPath/Path/BezierCurve").children], [2.0] * 3),
         ("Lake", [(20, 0.2, 20), (24, 0.2, 20)], [])]
    )  # fmt: skip
    assert net["rivers"][0]["points"][1] == [8.0, 0.5, 20.0] and net["lakes"][0]["level"] == 0.2
    assert groundedit.water_network([("CoastLine", [(0, 0, 0)], [])])["coasts"] == [{"points": [[0, 0]]}]
    assert groundedit.water_network([("Lake", [], [])]) is None
    edits = islandedits.empty()
    edits["rivers"] = {
        "0": {"points": [[4, 0.5, 20], [9, 0.4, 21], [12, 0.3, 20], [14, 0.2, 20]], "widths": [2, 3, 3, 2]}
    }
    edits["lakes"] = {"0": 0.6, "3": 1.0}
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    back, _ = _reopen(files, done)
    river, lake = groundedit._water_objects(back)[0][0], groundedit._water_objects(back)[1][0]
    pts = [p for p in river.path("BezierPath/Path/BezierCurve").children if isinstance(p, Tag)]
    assert [struct.unpack("<3f", p.find("p").data)[0] for p in pts] == [4, 9, 12, 14]
    assert struct.unpack("<f", pts[1].find("w").data)[0] == 3
    assert struct.unpack("<3f", pts[3].find("u0").data) == (1, 15, 1)  # flows as the old point nearest to it
    assert river.find("ID").data == _u32(3) + _u32(9)  # the same object, not a new one
    assert {struct.unpack("<3f", p.find("p").data)[1] for p in lake.path("BezierPath/Path/BezierCurve").children} == {
        np.float32(0.6)
    }
    assert (
        done.report["water"] == {"rivers": 1, "lakes": 1, "unmatched": ["lake 3"]}
        and "lake 3" in done.report["unmatched"]
    )


def test_the_build_writes_the_changed_render_data_beside_the_island(tmp_path):
    files = island()
    edits_dir = tmp_path / "edits"
    islandedits.save(edits_dir / "117_isle.json", {**_hill(islandedits.empty(), 6, 6), "source": SOURCE})
    out = tmp_path / "anno117-islands"
    files.files["data/base/config/export/assets.xml"] = b"<Assets />"
    a7m_edit117.build(files, edits_dir, out, "anno117-islands", "Islands", log=lambda _m: None)
    assert (out / SOURCE).is_file() and (out / GAMEDATA / "normalmaps/0/0x0.ctt").is_file()
    assert not (out / GAMEDATA / "normalmaps/0/1x1.ctt").exists()  # untouched: the game's own is read
    page = zlib.decompress((out / GAMEDATA / "normalmaps/0/0x0.ctt").read_bytes()[8:])
    assert filedb.read(page).path("HeightMap/Downsampled") is not None


def test_a_coast_line_is_moved_with_its_heights_and_its_normal_turned_with_it():
    files = island()
    view = a7m_edit117.IslandFile(files.read(SOURCE), files.read(SOURCE[:-4] + ".a7minfo"))
    assert [len(c.path("BezierPath/Path/BezierCurve").children) for c in groundedit._coast_objects(view)] == [3]
    edits = islandedits.empty()
    edits["coast"] = {"0": {"points": [[4, 6], [8, 9], [12, 6], [16, 6]]}, "5": {"points": [[0, 0], [1, 1]]}}
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert done.report["water"]["coasts"] == 1 and "coast line 5" in done.report["unmatched"]
    back, _ = _reopen(files, done)
    coast = groundedit._coast_objects(back)[0]
    pts = [p for p in coast.path("BezierPath/Path/BezierCurve").children if isinstance(p, Tag)]
    assert [struct.unpack("<3f", p.find("p").data)[::2] for p in pts] == [(4, 6), (8, 9), (12, 6), (16, 6)]
    assert {round(struct.unpack("<3f", p.find("p").data)[1], 2) for p in pts} == {0.4}
    normals = [struct.unpack("<2f", p.find("d").data) for p in pts]
    assert normals[0][0] < 0 < normals[0][1]  # the line now runs south-east: its normal turned, still facing south
    assert all(n[1] > 0 for n in normals[:3])
    assert coast.find("ID").data == _u32(5) + _u32(9)
    assert groundedit.water_network([("CoastLine", [(4.0, 0.4, 6.0), (8.0, 0.4, 9.0)], [])])["coasts"][0]["points"][
        1
    ] == [8.0, 9.0]


def test_a_ford_is_laid_on_a_river_tile_and_nowhere_else():
    from annotoolbox.islands.islandgrid import read_bits

    files = island()
    view = a7m_edit117.IslandFile(files.read(SOURCE), files.read(SOURCE[:-4] + ".a7minfo"))
    river = np.asarray(read_bits(view.gm.path("WorldManager/RiverGrid")), bool)
    (zr, xr), (zd, xd) = np.argwhere(river)[0], np.argwhere(~river)[0]
    edits = islandedits.empty()
    edits["ford"] = {f"{xr},{zr}": 1, f"{xd},{zd}": 1, "99,0": 1, "x": 1}
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert done.report["ford"]["cells"] == 1 and len(done.report["ford"]["unmatched"]) == 3
    back, _ = _reopen(files, done)
    ford = np.asarray(read_bits(back.gm.path("WorldManager/FordGrid")), bool)
    assert ford.sum() == 1 and ford[zr, xr]
    files.files[SOURCE] = done.a7m  # the island with its ford: the ford taken off again
    edits["ford"] = {f"{xr},{zr}": 0}
    again = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert again.report["ford"]["cells"] == 1 and again.report["ford"]["unmatched"] == []


def test_environment_zones_are_set_per_cell_and_a_cell_off_the_island_or_a_bad_zone_is_listed():
    files = island()
    edits = islandedits.empty()
    edits["environment"] = {"1,2": 8, "7,7": 4, "8,0": 4, "0,0": 300, "q": 1}
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert done.report["environment"]["cells"] == 2 and len(done.report["environment"]["unmatched"]) == 3
    assert "environment 8,0" in done.report["unmatched"]
    back, _ = _reopen(files, done)
    zones = np.frombuffer(back.gm.path("WorldManager/EnvironmentGrid/EnvironmentGRid/val").data, np.uint8).reshape(8, 8)
    assert zones[2, 1] == 8 and zones[7, 7] == 4 and zones.sum() == 20 * 62 + 8 + 4
    files.files[SOURCE] = done.a7m
    edits["environment"] = {"1,2": 20}  # the island's own zone again
    again = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert again.report["environment"]["cells"] == 1


def test_a_lake_outline_is_redrawn_at_the_lakes_level_and_a_degenerate_one_is_listed():
    files = island()
    edits = islandedits.empty()
    edits["lake_outlines"] = {
        "0": {"points": [[20, 20], [26, 20], [26, 26], [20, 26]]},
        "1": {"points": [[0, 0]] * 3},
        "0x": {},
    }
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert done.report["water"]["lake outlines"] == 1 and "lake outline 1" in done.report["unmatched"]
    lake = groundedit._water_objects(_reopen(files, done)[0])[1][0]
    pts = [p for p in lake.path("BezierPath/Path/BezierCurve").children if isinstance(p, Tag)]
    assert [struct.unpack("<3f", p.find("p").data) for p in pts] == [
        (20, np.float32(0.2), 20),
        (26, np.float32(0.2), 20),
        (26, np.float32(0.2), 26),
        (20, np.float32(0.2), 26),
    ]
    assert lake.find("ID").data == _u32(4) + _u32(9)
    edits["lakes"] = {"0": 0.9}  # the level edit and the outline together
    pts = groundedit._water_objects(_reopen(files, a7m_edit117.apply(files, SOURCE, edits, template=None))[0])[1][0]
    assert {
        round(struct.unpack("<3f", p.find("p").data)[1], 2) for p in pts.path("BezierPath/Path/BezierCurve").children
    } == {0.9}


def test_a_height_edit_draws_the_islands_map_pictures_again(monkeypatch):
    import io

    from PIL import Image

    from annotoolbox.islands import mappictures

    monkeypatch.setattr(mappictures, "to_dds", lambda pictures: {})  # texconv is not needed to see the pictures
    files = island()
    for name in ("gamemapimage.png", "provincemapimage.png"):
        buf = io.BytesIO()
        Image.new("RGBA", (2 * SIZE, 2 * SIZE), (90, 120, 60, 0)).save(buf, "PNG")  # the island is all sea
        files.files[GAMEDATA + name] = buf.getvalue()
    done = a7m_edit117.apply(files, SOURCE, _hill(islandedits.empty(), 6, 6), template=None)
    assert done.report["pictures"] == 2
    assert sorted(k for k in done.gamedata if k.endswith(".png")) == ["gamemapimage.png", "provincemapimage.png"]
    province = np.asarray(Image.open(io.BytesIO(done.gamedata["provincemapimage.png"])))
    # row 0 is the far edge: the hill at tile (6, 6) stands in pixel row 2 * (32 - 6) = 52
    assert province[52, 12, 3] == 255 and province[52, 12, 1] > 0 and province[10, 50, 3] == 0


def test_a_painted_stroke_is_laid_into_the_map_pictures_in_its_materials_colour(monkeypatch):
    import io

    from PIL import Image

    from annotoolbox.islands import mappictures

    monkeypatch.setattr(mappictures, "to_dds", lambda pictures: {})
    files = island()
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 20, 20)).save(buf, "PNG")
    files.files["tex_0.dds"] = buf.getvalue()  # the texture decoder reads a PNG as well
    files.files["b.xml"] = b"<Material><DiffuseFileName>tex.psd</></>"
    buf = io.BytesIO()
    Image.new("RGBA", (2 * SIZE, 2 * SIZE), (90, 120, 60, 0)).save(buf, "PNG")
    files.files[GAMEDATA + "gamemapimage.png"] = buf.getvalue()
    edits = _hill(islandedits.empty(), 6, 6)
    plain = a7m_edit117.apply(files, SOURCE, edits, template=None)
    edits["paint"] = [{"material": 7, "radius": 2, "strength": 1.0, "points": [[6, 6]]}]
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert done.report["pictures"] == 1 and done.report["paint"]["strokes"] == 1
    before, after = (
        np.asarray(Image.open(io.BytesIO(d.gamedata["gamemapimage.png"]))).astype(int) for d in (plain, done)
    )
    assert after[52, 12, 0] > 3 * after[52, 12, 1]
    assert after[52, 12, 3] == before[52, 12, 3] == 255  # the land is the same, only its colour changed
    assert before[52, 12, 0] < after[52, 12, 0] - 20  # the stroke's red over the plain ground
    assert (after[10, 50] == before[10, 50]).all()  # far from the stroke nothing changed
    assert a7m_edit117.paint_colours(files, files.read(SET).decode(), edits) == {7: (200, 20, 20)}


def test_a_material_the_palette_added_is_painted_from_a_set_of_the_islands_own_beside_the_island():
    files = island()
    other = "data/base/graphics/material_sets/other.xml"
    files.files[other] = b"<MaterialSet><Material><ID>2</>\n<FileName>data/m/c.xml</>\n</>\n"
    edits = islandedits.empty()
    edits["materials"] = {"9": {"file": "data/m/c.xml", "set": other}}
    edits["paint"] = [{"material": 9, "radius": 2, "strength": 1.0, "points": [[4, 4]]}]
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    path = a7m_edit117.own_set_path(SOURCE)
    assert path == SOURCE[:-4] + "_materialset.cfg" and sorted(done.files) == [path]
    assert done.report["material set"] == path and done.report["paint"]["strokes"] == 1
    text = done.files[path].decode()
    assert [int(i) for i in re.findall(r"<ID>(\d+)</>", text)] == [
        3,
        7,
        9,
    ]  # the added one by its family, here the last
    assert "data/m/c.xml" in text and "data/m/c.xml" not in files.files[SET].decode()  # the shared set stays as it was
    back, _gd = _reopen(files, done)
    assert back.rd.path("Terrain/MaterialSetFileName").data.decode("utf-16-le") == path
    plain = a7m_edit117.apply(files, SOURCE, _hill(islandedits.empty(), 6, 6), template=None)
    assert plain.files == {}  # no added material: no set of its own


def test_a_material_of_1800_in_the_palette_is_brought_in_with_its_textures_and_a_set_of_its_one_entry():
    import io

    from PIL import Image

    files = island()
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (40, 160, 40)).save(buf, "PNG")  # the texture decoder reads a PNG as well
    other = FakeFiles(
        {
            "data/config/engine/material_sets/south_america_01.xml": (
                b"<MaterialSet><Material><ID>4</>\n<FileName>data/graphics/landscape/terrain/d_grass_06.xml</>\n</>\n"
            ),
            "data/graphics/landscape/terrain/d_grass_06.xml": (
                b"<Material><DiffuseFileName>data/graphics/landscape/terrain/d_grass_06_diff.psd</>\n</>"
            ),
            "data/graphics/landscape/terrain/d_grass_06_diff_0.dds": buf.getvalue(),
            "data/graphics/landscape/terrain/d_grass_06_diff_1.dds": b"dds1",
        }
    )
    edits = islandedits.empty()
    entry = {
        "file": "data/graphics/landscape/terrain/d_grass_06.xml",
        "set": "data/config/engine/material_sets/south_america_01.xml",
    }
    edits["materials"] = {"9": {**entry, "game": "anno1800"}}
    edits["paint"] = [{"material": 9, "radius": 2, "strength": 1.0, "points": [[4, 4]]}]
    done = a7m_edit117.apply(files, SOURCE, edits, template=None, others={"anno1800": other})
    material = "data/imported/1800/graphics/landscape/terrain/d_grass_06.xml"
    assert done.files[material].decode() == (
        "<Material><DiffuseFileName>data/imported/1800/graphics/landscape/terrain/d_grass_06_diff.psd</DiffuseFileName>\n</>"
    )
    assert done.files["data/imported/1800/graphics/landscape/terrain/d_grass_06_diff_1.dds"] == b"dds1"
    assert material in done.files[a7m_edit117.own_set_path(SOURCE)].decode()
    alone = a7m_edit117.apply(files, SOURCE, edits, template=None)  # no 1800 install at hand: said, not painted
    assert "material data/graphics/landscape/terrain/d_grass_06.xml of anno1800" in alone.report["unmatched"]
