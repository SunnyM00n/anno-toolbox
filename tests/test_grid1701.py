"""1701's occupation grid: the footprint rule, the shapes read off an island, and the grid following an edit."""

import math
import struct

import pytest

from annotoolbox.editor import footprints
from annotoolbox.editor.formats.isc import edit as isc_edit
from annotoolbox.othergames import grid1701, isd1701
from annotoolbox.othergames.grid1701 import Shape
from test_isd1701 import cdata, t

SIDE = 12


def _words(held: dict[int, set[tuple[int, int]]]) -> bytes:
    grid = [0x80000000] * (SIDE * SIDE)  # a land tile of the shipped files
    for handle, tiles in held.items():
        for x, z in tiles:
            grid[z * SIDE + x] = 0x40000000 | handle
    return struct.pack(f"<{SIDE * SIDE}I", *grid)


def _object(handle: int, guid: int, x: float, z: float, direction: int = 0, offset: int = 0) -> bytes:
    return t(
        f"{handle:08X}",
        t("m_GUID", str(guid))
        + t("m_GRIDOffsetX", str(offset))
        + t("m_GRIDOffsetZ", str(offset))
        + t("m_Position", t("data", cdata(struct.pack("<3i", round(x * 4096), 0, round(z * 4096)))))
        + t("m_Direction", str(round(direction / 64 * 2 * math.pi * 4096)))
        + t("m_GRIDDirection", str(direction)),
    )


def _island(objects: list[bytes], held: dict[int, set[tuple[int, int]]], grid: bool = True) -> isd1701.Island1701:
    n = SIDE + 1
    verts = b"".join(isd1701.VERTEX.pack(0, 0, 0, 0, 1, 0, 0xFFFF, 1, 0xFFFFFF) for _ in range(n * n))
    tiles = b"".join(isd1701.TILE_RECORD.pack(20.0, 0, 82, 0, 0, 1, 0, 0, 1, 0) for _ in range(SIDE * SIDE))
    size = t("UsedSizeX", str(SIDE)) + t("UsedSizeZ", str(SIDE))
    terrain = t(
        "Terrain",
        t("Settings", t("Dim_X", "16") + t("Dim_Z", "16") + size)
        + t("HeightMap", cdata(verts))
        + t("TileMap", cdata(tiles)),
    )
    gridnode = t("m_GRID", cdata(_words(held))) if grid else b""
    gop = t(
        "m_GOPManager",
        t("m_GRIDManager", t("m_WaterLevel", "0") + gridnode)
        + t("m_HIDManager", t("m_LastHID", "9") + b"".join(objects)),
    )
    return isd1701.Island1701(terrain + gop)


def _square(x0: int, z0: int, w: int, h: int) -> set[tuple[int, int]]:
    return {(x, z) for x in range(x0, x0 + w) for z in range(z0, z0 + h)}


def test_the_rule_matches_what_the_shipped_islands_hold_at_each_quarter_turn():
    seven_by_four = Shape(7, 4)
    assert grid1701.cells(45, 85, seven_by_four, 0) == _square(42, 83, 7, 4)
    assert grid1701.cells(91, 75, seven_by_four, 1) == _square(89, 72, 4, 7)
    assert grid1701.cells(53, 27, seven_by_four, 2) == _square(49, 25, 7, 4)
    assert grid1701.cells(36, 79, seven_by_four, 3) == _square(34, 75, 4, 7)
    assert grid1701.cells(23.5, 18.5, Shape(3, 3, 16, 16), 0) == _square(22, 17, 3, 3)  # a house centred on its tile
    assert grid1701.cells(41.5, 28.5, Shape(3, 3, 16, 16), 2) == _square(40, 27, 3, 3)


def test_the_footprint_polygon_turned_like_the_view_covers_the_same_tiles():
    shape = Shape(7, 4)
    for quarter in range(4):
        angle = quarter * math.pi / 2  # m_Direction, which the viewer turns into the quaternion
        rotation = [0.0, math.sin(-angle / 2), 0.0, math.cos(-angle / 2)]
        poly = footprints.world([grid1701.polygon(shape)], (10.0, 0.0, 10.0), rotation)[0]
        tiles = grid1701.cells(10, 10, shape, quarter)
        assert (poly[:, 0].min(), poly[:, 0].max()) == pytest.approx(
            (min(c[0] for c in tiles), max(c[0] for c in tiles) + 1)
        )
        assert (poly[:, 1].min(), poly[:, 1].max()) == pytest.approx(
            (min(c[1] for c in tiles), max(c[1] for c in tiles) + 1)
        )


def test_shapes_are_read_off_the_islands_own_occupied_tiles():
    isl = _island(
        [_object(0x3C0001, 3300, 5, 4, 16), _object(0x3C0002, 4010, 9, 9), _object(0x3C0003, 270, 2, 2)],
        {0x3C0001: grid1701.cells(5, 4, Shape(7, 4), 1), 0x3C0002: grid1701.cells(9, 9, Shape(4, 4), 0)},
    )
    assert grid1701.island_shapes(isl) == {3300: Shape(7, 4), 4010: Shape(4, 4)}  # a tree holds no tiles
    assert grid1701.island_shapes(_island([_object(0x3C0001, 3300, 5, 4)], {}, grid=False)) == {}


def test_a_moved_a_new_and_a_deleted_object_change_their_tiles_and_nothing_else():
    shape = Shape(4, 4)
    isl = _island(
        [_object(0x3C0001, 4010, 5, 5), _object(0x3C0002, 4010, 9, 9)],
        {0x3C0001: grid1701.cells(5, 5, shape, 0), 0x3C0002: grid1701.cells(9, 9, shape, 0)},
    )
    before = grid1701.read_words(grid1701.grid_of(isl))
    assert isc_edit.apply(isl, {})["unmatched"] == [] and grid1701.read_words(grid1701.grid_of(isl)) == before
    edits = {
        "objects": {"anno1701:0": {"position": [6.0, 0.0, 5.0]}, "anno1701:1": {"deleted": True}},
        "added_objects": [{"guid": 4010, "clone": True, "position": [3.0, 0.0, 9.0], "rotation": [0, 0, 0, 1]}],
    }
    report = isc_edit.apply(isl, edits)
    words = grid1701.read_words(grid1701.grid_of(isl))
    assert report["grid tiles"] == 32 and report["objects added"] == 1
    assert grid1701.holds(words, SIDE, 0x3C0001) == grid1701.cells(6, 5, shape, 0)
    assert grid1701.holds(words, SIDE, 0x3C0002) == set()
    assert grid1701.holds(words, SIDE, 0x3C000A) == grid1701.cells(3, 9, shape, 0)  # the handle after m_LastHID
    assert words[0] == 0x80000000 and words[2 * SIDE + 11] == 0x80000000  # the ground elsewhere is as it was


def test_a_new_object_of_an_asset_the_island_lacks_takes_its_footprint_from_the_table():
    isl = _island([_object(0x3C0001, 4010, 2, 2)], {0x3C0001: grid1701.cells(2, 2, Shape(4, 4), 0)})
    entry = {"guid": 3300, "clone": True, "position": [8.5, 0.0, 8.5], "rotation": [0, 0, 0, 1]}
    asked = []

    def table() -> dict:
        asked.append(1)
        return {3300: Shape(3, 3, 16, 16)}

    isc_edit.apply(isl, {"added_objects": [entry]}, table)
    new = next(n for k, n in isl.object_nodes() if n.int("m_GUID") == 3300)
    assert asked == [1] and (new.int("m_GRIDOffsetX"), new.int("m_GRIDOffsetZ")) == (16, 16)
    assert len(grid1701.holds(grid1701.read_words(grid1701.grid_of(isl)), SIDE, int(new.name, 16))) == 9
    isc_edit.apply(isl, {"added_objects": [{**entry, "guid": 99}]}, table)  # no shape known: holds none
    assert len(asked) == 2


def test_the_shape_table_of_the_shipped_islands_is_kept_atomically_and_dropped_when_the_archives_change(tmp_path):
    from types import SimpleNamespace

    from annotoolbox.editor import gameviewers

    archive = tmp_path / "data0.rda"
    archive.write_bytes(b"one")
    viewer = gameviewers.Isd1701Viewer.__new__(gameviewers.Isd1701Viewer)
    viewer.cache = tmp_path / "cache"
    viewer.tree2070 = SimpleNamespace(archives=[SimpleNamespace(path=archive)])
    table = {7: grid1701.Shape(2, 3, 1, 0)}
    assert viewer._kept_shapes() == {}  # nothing kept yet
    viewer._keep_shapes(table)
    assert viewer._kept_shapes() == table
    assert [p.name for p in viewer.cache.iterdir()] == ["shapes1701.json"]  # no half-written part left behind
    archive.write_bytes(b"other bytes")  # an archive changed (patched game): the table is read again
    assert viewer._kept_shapes() == {}


def test_a_1701_footprint_comes_from_the_models_info_file_and_the_island_scan_is_the_fallback(tmp_path):
    from annotoolbox.editor import gameviewers

    cells = "".join(f"<GridPos><x>{x}</x><z>{z}</z></GridPos>" for x in (1, 2, 3) for z in (1, 2))
    files = {
        "data/a/house.ocf": "<GUID>7</GUID><XOffset>16</XOffset><ZOffset>0</ZOffset><MeshConfig>data/a/house.mcf</MeshConfig>",
        "data/a/house.info": f"<NoBuildings><GridResolution>1</GridResolution>{cells}</NoBuildings>",
        "data/a/rock.ocf": "<GUID>8</GUID><MeshConfig>data/a/rock.mcf</MeshConfig>",  # a landscape piece: no info
    }

    class Tree:
        def exists(self, path):
            return path in files

        def read_text(self, path):
            return files[path]

        def iter_paths(self, prefix, suffix=None):
            return [p for p in files if p.endswith(suffix or "")]

    viewer = gameviewers.Isd1701Viewer.__new__(gameviewers.Isd1701Viewer)
    viewer.tree2070 = Tree()
    viewer._table, viewer._own = {8: Shape(2, 2)}, {}
    viewer._shape_lock = __import__("threading").Lock()
    box = [[(-0.5, 0.0), (2.5, 0.0), (2.5, 2.0), (-0.5, 2.0)]]  # 3 x 2 tiles, the origin half a tile in
    assert viewer.footprint_of("data/a/house.mcf") == box
    out = viewer.footprints([{"cfg": "data/a/house.mcf", "guid": 7}, {"cfg": "data/a/rock.mcf", "guid": 8}])
    assert out["data/a/house.mcf"] == box and out["data/a/rock.mcf"] == [grid1701.polygon(Shape(2, 2))]


def test_the_object_configs_of_another_1701_install_are_read_again():
    """The table was kept by the tree's ``id()``, which the next tree is given again once the first is gone (the
    game's folder changed in the settings): its objects then drew the other install's models."""
    from annotoolbox.editor import gameviewers

    class Tree:
        def __init__(self, files):
            self.files = files

        def read_text(self, path):
            return self.files[path]

        def iter_paths(self, prefix, suffix=None):
            return [p for p in self.files if p.endswith(suffix or "")]

    def viewer(guid: int, name: str):
        v = gameviewers.Isd1701Viewer.__new__(gameviewers.Isd1701Viewer)
        ocf = f"<ObjectConfig><GUID>{guid}</GUID><MeshConfig>data/{name}.mcf</MeshConfig></ObjectConfig>"
        v.tree2070 = Tree({f"data/{name}.ocf": ocf})
        return v

    first = viewer(7, "a")
    assert first._ocf_models() == {7: ["data/a.mcf"]} and first._ocf_models() is first._ocf_models()  # read once
    address = id(first.tree2070)
    del first
    for _ in range(100):  # the next tree usually takes the freed one's address at once
        other = viewer(9, "b")
        if id(other.tree2070) == address:
            break
    assert other._ocf_models() == {9: ["data/b.mcf"]}
