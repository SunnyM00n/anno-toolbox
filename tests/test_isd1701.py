import math
import struct
from pathlib import Path

import pytest

from annotoolbox.editor.formats.isc import edit as isc_edit
from annotoolbox.formats import isd2070, rda
from annotoolbox.othergames import isd1701


def t(name: str, inner: bytes | str = b"", close: bytes = b"</>") -> bytes:
    """A node as 1701 writes it: no line break after the close."""
    inner = inner.encode() if isinstance(inner, str) else inner
    return b"<%s>" % name.encode() + inner + close


def cdata(data: bytes) -> bytes:
    return b"CDATA[" + struct.pack("<I", len(data)) + data + b"]"


W = H = 2  # a 2 x 2 tile island
HEIGHTS = [-100.0, 0.0, -100.0, 0.0, 20.0, 0.0, -100.0, 0.0, -100.0]
TILES = [82, 82, 78, 82]  # rows = z


def isd_bytes() -> bytes:
    verts = b"".join(
        isd1701.VERTEX.pack(0, h, 0, 0, 1, 0, 0xFFFF, 1, 0xFFFFFF if k else 0x102030) for k, h in enumerate(HEIGHTS)
    )
    tiles = b"".join(isd1701.TILE_RECORD.pack(20.0, 0, tex, 0, 0, 1, 0, 0, 1, 0) for tex in TILES)
    settings = t("Settings", t("Dim_X", "4") + t("Dim_Z", "4") + t("UsedSizeX", "2") + t("UsedSizeZ", "2"))
    terrain = t("Terrain", settings + t("HeightMap", cdata(verts)) + t("TileMap", cdata(tiles)))
    hid = t(
        "003C002F",
        t("m_GUID", "4010")
        + t("m_Variation", "2")
        + t("m_Position", t("data", cdata(struct.pack("<3i", 4096, 20 * 128, 2048))))
        + t("m_Direction", str(round(math.pi / 2 * 4096)))
        + t("m_DynGridPosition.x", "1"),
    )
    old_hid = t("003C0030", t("m_GUID", "21") + t("m_Position", t("m_x", "8192") + t("m_y", "0") + t("m_z", "0")))
    pid = t(
        "00000001",
        t("m_GUID", "270")
        + t("m_Position.x", "32.000000")
        + t("m_Position.y", "5.000000")
        + t("m_Position.z", "16.000000")
        + t("m_Direction", "3.141593")
        + b"".join(t(f"m_Size.{k}", "1.500000") for k in "xyz"),
    )
    gop = t(
        "m_GOPManager",
        t("m_GRIDManager", t("m_WaterLevel", "0"))
        + t("m_PIDManager", t("m_LastPID", "1") + pid)
        + t("m_HIDManager", t("m_DummyOccupationMap", t("mapsize", "0")) + t("m_LastHID", "48") + hid + old_hid),
    )
    return terrain + gop


def point(x: float, y: float, z: float, width: float, binary: bool) -> bytes:
    pos = t("data", cdata(struct.pack("<3f", x, y, z))) if binary else t("x", str(x)) + t("y", str(y)) + t("z", str(z))
    return t("position", pos) + t("width", f"{width:.6f}") + t("distance", "0.000000")


def isc_bytes() -> bytes:
    # a coast in the newer list generation (repeated i), a river in the older (arraysize + i0000)
    coast = t(
        "i",
        t("hasValue", "1")
        + t("m_ID", "1")
        + t("m_GUID", "0")
        + t("m_Variation", "1")
        + t("m_Polygon", t("i", point(0, 0, 32, 96, True)) + t("i", point(64, 0, 32, 64, True))),
    )
    river = t(
        "i0000",
        t("m_ID", "7")
        + t("m_GUID", "236")
        + t("m_Polygon", t("arraysize", "1") + t("i0000", point(16, 20, 16, 16, False))),
    )
    return (
        t("Width", "2", b"</Width>\r\n")
        + t("Height", "2")
        + t("Clime", "1")
        + t("Coastlines", coast)
        + t("Rivers", t("arraysize", "1") + river)
    )


def test_the_1701_dialect_round_trips():
    """Names with spaces and dots, no line break after a close, indentation, an unclosed root."""
    for data in (
        isd_bytes(),
        isc_bytes(),
        b"<GameData>\r\n  <EnableUndiscovered>1</EnableUndiscovered>\r\n  <Config 0000><Label>x</></>\r\n</GameData>",
        b"<WorldConfig><m_Width>64</><m_Fertility><arraysize>0</></>",
    ):
        root = isd2070.parse(data)
        assert isd2070.serialize(root) == data
    root = isd2070.parse(b"<a><Config 0000>1</><m_Position.x>2</></a>")
    assert root.int("a/Config 0000") == 1 and root.float("a/m_Position.x") == 2.0


def test_a_tree_built_in_code_is_written_as_2070_writes_it():
    node = isd2070.Node("a", children=[isd2070.Node("b", "1")])
    assert isd2070.serialize(isd2070.Node("", children=[node])) == b"<a><b>1</b>\r\n</a>\r\n"


def test_terrain():
    isl = isd1701.Island1701(isd_bytes(), isc_bytes())
    assert (isl.width, isl.height, isl.clime) == (W, H, 1)
    assert isl.heightmap() == [HEIGHTS[0:3], HEIGHTS[3:6], HEIGHTS[6:9]]
    assert isl.colours()[0][0] == 0x102030 and isl.colours()[1][1] == 0xFFFFFF
    assert isl.tiles() == [[82, 82], [78, 82]]
    assert isl.texture_ids() == [82, 78]  # most tiles first
    layers = isl.texture_layers()
    assert list(layers) == [82, 78]
    assert layers[82][0][0] == 255 and layers[78][2][0] == 255  # corners: one tile each
    assert layers[78][1][1] == round(255 / 4) and layers[82][1][1] == round(255 * 3 / 4)  # the centre: four
    ((cx, cz, per, chunk),) = isd1701.Island1701(isd_bytes()).texture_chunks()
    assert (cx, cz, per) == (0, 0, 16) and [i for i, _ in chunk] == [82, 78]
    assert chunk[1][1][2][0] == 255 and chunk[1][1][3][0] == 0  # past the island's edge: nothing


def test_objects():
    hid, old, pid = isd1701.Island1701(isd_bytes()).objects()
    assert (hid.kind, hid.guid, hid.variation) == ("HID", 4010, 2)
    assert (hid.x, hid.y, hid.z) == (1.0, 20.0, 0.5)  # tiles, world units, tiles
    assert hid.direction == pytest.approx(90 * 4096, abs=1)  # degrees x 4096, as 2070's
    assert (old.guid, old.x, old.variation) == (21, 2.0, None)
    assert (pid.kind, pid.guid, pid.x, pid.y, pid.z, pid.scale) == ("PID", 270, 1.0, 5.0, 0.5, 1.5)
    assert pid.direction == pytest.approx(180 * 4096, abs=1)


def test_coasts_and_rivers_in_both_list_generations():
    isl = isd1701.Island1701(isd_bytes(), isc_bytes())
    (coast,) = isl.coasts()
    assert (coast.id, coast.guid, coast.variation) == (1, 0, 1)
    assert coast.points == [(0.0, 0.0, 1.0), (2.0, 0.0, 1.0)] and coast.widths == [3.0, 2.0]
    (river,) = isl.rivers()
    assert (river.id, river.guid, river.points, river.widths) == (7, 236, [(0.5, 20.0, 0.5)], [0.5])
    assert isd1701.Island1701(isd_bytes()).coasts() == []  # without a header


def test_the_grid_must_match_the_used_size():
    bad = isd_bytes().replace(b"<UsedSizeX>2</>", b"<UsedSizeX>3</>")
    with pytest.raises(ValueError):
        isd1701.Island1701(bad)


def test_palette():
    prj = (
        'PROJECTFILE V1.7\n; a comment\n[TEXTURE]\n{\n\tTEX_NAME "gras01"\n\tTEX_GUID 82\n\tTEX_MAP_TO_GUID 0\n'
        '\tTEX_MAPPING_GUID 65535\n\tTEX_FILENAME "data\\graphics\\gras01.tga"\n\tTEX_MAP_COLOR 255\n}\n'
        '[TEXTURE]\n{\n\tTEX_NAME "gras01b"\n\tTEX_GUID 83\n\tTEX_MAP_TO_GUID 1\n\tTEX_MAPPING_GUID 82\n}\n'
        '[OBJECT]\n{\n\tOBJ_NAME "tree"\n\tOBJ_GUID 4010\n}\n'
    )
    pal = isd1701.palette(prj)
    assert set(pal) == {82, 83}
    assert (pal[82].name, pal[82].filename, pal[82].color, pal[82].base) == (
        "gras01",
        "data\\graphics\\gras01.tga",
        0xFF0000,  # 0x00BBGGRR in the file
        None,
    )
    assert pal[83].base == 82


def write(path: Path, files: dict[str, bytes], stamp: int) -> None:
    path.write_bytes(rda.write_v11(files, timestamp=stamp))


def test_archives_and_the_newest_file_wins(tmp_path: Path):
    isle = "data/levels/islands/north/n_k01"
    write(tmp_path / "data0.rda", {f"{isle}.isd": isd_bytes(), "data/a.txt": b"new"}, 200)
    write(tmp_path / "data1.rda", {"data/a.txt": b"old", f"{isle}.isc": isc_bytes()}, 100)
    write(tmp_path / "data2_ger.rda", {"data/b.txt": b"ger"}, 100)
    write(tmp_path / "data2_eng.rda", {"data/b.txt": b"eng"}, 100)
    write(tmp_path / "data4.rda", {"data/c.txt": b"after a gap"}, 100)  # no data3: not mounted
    assert [p.name for p in isd1701.archives(tmp_path)] == ["data0.rda", "data1.rda", "data2_eng.rda"]
    assert [p.name for p in isd1701.archives(tmp_path, "ger")][-1] == "data2_ger.rda"
    tree = isd1701.source_tree(tmp_path, cache_dir=tmp_path / "cache")
    assert tree.read_bytes("data/a.txt") == b"new"  # the later archive is older
    assert tree.read_bytes("data/b.txt") == b"eng" and not tree.exists("data/c.txt")
    assert isd1701.islands(tree) == [f"{isle}.isd"]
    isl = isd1701.load(tree, f"{isle}.isd")
    assert isl.clime == 1 and len(isl.coasts()) == 1


def test_the_history_editions_archive_is_read_too(tmp_path: Path):
    write(tmp_path / "data0.rda", {"data/a.txt": b"base"}, 100)
    (tmp_path / "ahe").mkdir()
    (tmp_path / "ahe" / "a3he0.rda").write_bytes(rda.write_v22({"data/texts.xml": b"<Texts/>", "data/a.txt": b"he"}))
    tree = isd1701.source_tree(tmp_path, cache_dir=tmp_path / "cache")
    assert tree.read_bytes("data/texts.xml") == b"<Texts/>"
    assert tree.read_bytes("data/a.txt") == b"base"  # stamped 0: it never replaces a file of the game's
    assert [p.name for p in isd1701.archives(tmp_path)] == ["data0.rda"]  # what the island build mounts after


def test_same_timestamp_keeps_the_earlier_archive(tmp_path: Path):
    write(tmp_path / "data0.rda", {"data/a.txt": b"first"}, 100)
    write(tmp_path / "data1.rda", {"data/a.txt": b"second"}, 100)
    assert isd1701.source_tree(tmp_path, cache_dir=tmp_path / "cache").read_bytes("data/a.txt") == b"first"


def test_an_unedited_island_is_written_back_byte_for_byte():
    data = isd_bytes()
    isl = isd1701.Island1701(data, isc_bytes())
    report = isc_edit.apply(isl, {})
    assert isl.write() == data and report["objects changed"] == 0 and report["unmatched"] == []


def test_object_edits_move_turn_scale_and_delete():
    isl = isd1701.Island1701(isd_bytes())
    quarter = [0, -math.sin(math.pi / 4), 0, math.cos(math.pi / 4)]  # a quarter turn, as the viewer builds it
    edits = {
        "objects": {
            "anno1701:0": {"position": [2.0, 1.0, 1.5], "rotation": quarter},
            "anno1701:1": {"position": [3.0, 0.0, 0.25]},
            "anno1701:2": {"position": [4.0, 0.5, 1.0], "rotation": quarter, "scale": 2.0},
            "anno1701:9": {"deleted": True},
        }
    }
    report = isc_edit.apply(isl, edits)
    assert report["objects changed"] == 3 and report["unmatched"] == ["anno1701:9"]
    hid, old, pid = isd1701.Island1701(isl.write()).objects()
    assert (hid.x, hid.y, hid.z) == (2.0, 32.0, 1.5) and hid.direction == pytest.approx(90 * 4096, abs=1)
    assert (old.x, old.z) == (3.0, 0.25)
    assert (pid.x, pid.y, pid.z, pid.scale) == (4.0, 16.0, 1.0, 2.0)
    assert pid.direction == pytest.approx(90 * 4096, abs=1)
    gone = isd1701.Island1701(isd_bytes())
    isc_edit.apply(gone, {"objects": {"anno1701:1": {"deleted": True}}})
    assert [o.guid for o in isd1701.Island1701(gone.write()).objects()] == [4010, 270]


def test_height_edits_redraw_the_normals_the_way_the_files_do():
    isl = isd1701.Island1701(isd_bytes())
    assert isc_edit.apply(isl, {"heights": {"1,1": 1.0}})["heights"] == 1  # the centre vertex, 32 world units up
    out = isd1701.Island1701(isl.write())
    heights = out.heightmap()
    assert heights[1][1] == 20.0 + 32 and heights[0][0] == -100.0
    import numpy as np

    n1, n2, vn = isc_edit._normals(np.array(heights))
    assert [v[3:6] for v in out.vertices][4] == pytest.approx(tuple(vn[1, 1]))
    assert out.tile_records[0][4:7] == pytest.approx(tuple(n1[0, 0])) and out.tile_records[3][7:10] == pytest.approx(
        tuple(n2[1, 1])
    )
    assert out.vertices[0][6:] == isd1701.Island1701(isd_bytes()).vertices[0][6:]  # the rest of a vertex stays


def test_the_logic_grid_height_follows():
    grid = struct.pack("<4h", 10, 10, 10, 10)
    data = isd_bytes().replace(t("m_WaterLevel", "0"), t("m_WaterLevel", "0") + t("m_HeightMap", cdata(grid)))
    isl = isd1701.Island1701(data)
    isc_edit.apply(isl, {"heights": {"0,0": 1.0}})  # +32 at one corner of tile (0, 0): the tile's mean rises by 8
    node = isd1701.Island1701(isl.write()).root.path("m_GOPManager/m_GRIDManager/m_HeightMap")
    assert struct.unpack("<4h", node.data) == (18, 10, 10, 10)


def test_painted_strokes_set_the_ground_texture_of_the_tiles_they_cover():
    isl = isd1701.Island1701(isd_bytes())
    stroke = {"material": 99, "radius": 0.8, "strength": 1.0, "points": [[0.5, 0.5]]}
    assert isc_edit.apply(isl, {"paint": [stroke]})["paint"] == 1
    assert isd1701.Island1701(isl.write()).tiles() == [[99, 82], [78, 82]]
