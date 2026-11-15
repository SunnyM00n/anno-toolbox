"""The island's ``.a7minfo`` edits: the start coast, the active rectangle and the active map's cells, applied by the
117 and 1800 builds and written beside the island."""

import json
import math
import struct

import pytest

from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.formats.rda import write_v22
from annotoolbox.islands import islandedits, islandinfo
from annotoolbox.islands.islandgrid import read_bits
from fakes import FakeFiles

SOURCE = "data/base/provinces/roman/islands/pool/isle/isle.a7m"
GRID = 32  # tiles: 4 x 4 cells of 8


def _grid(name: str, n: int, bits: bytes) -> Tag:
    return Tag(name, [Attrib("x", struct.pack("<I", n)), Attrib("y", struct.pack("<I", n)), Attrib("bits", bits)])


def _rows(cells: list[list[int]]) -> bytes:
    """Cell rows as the file stores them: one row after the other, a row padded to 32 bits."""
    out = bytearray(4 * len(cells))
    for z, row in enumerate(cells):
        for x, v in enumerate(row):
            if v:
                out[z * 4 + x // 8] |= 1 << (x % 8)
    return bytes(out)


CELLS = [[0, 1, 1, 0], [0, 1, 1, 0], [0, 1, 1, 0], [0, 0, 0, 0]]


def island_files(coast: bool = True, version: int = 3):
    gm = Tag(
        "GameSessionManager",
        [Tag("WorldManager", [_grid("Water", GRID, bytes(GRID * GRID // 8))]), Tag("AreaManagerData", [])],
    )
    rd = Tag("root", [Tag("Terrain", [_grid("ActiveMap", 4, _rows(CELLS))]), Tag("PropGrid", [])])
    info = Tag(
        "",
        [
            Attrib("MapSize", struct.pack("<2i", GRID, GRID)),
            _grid("ActiveMapGrid", 4, _rows(CELLS)),
            Attrib("ActiveMapRect", struct.pack("<4i", 8, 0, 24, 24)),
            Tag(
                "ObjectMetaInfo",
                [Tag("StartCoastDirection", [Attrib("value", struct.pack("<f", 1.0))] if coast else [])],
            ),
        ],
    )
    a7m = write_v22(
        {
            "gamedata.data": filedb.write(Tag("root", [gm]), version=version),
            "rd3d.data": filedb.write(rd, version=version),
        }
    )
    return FakeFiles({SOURCE: a7m, SOURCE[:-4] + ".a7minfo": filedb.write(info, version=version)})


def test_the_numbers_are_read_from_the_file():
    isl = a7m_edit117.IslandFile(island_files().read(SOURCE), island_files().read(SOURCE[:-4] + ".a7minfo"))
    info = islandinfo.read(isl.info)
    assert info == {
        "grid": GRID,
        "start_coast": pytest.approx(57.3, abs=0.01),
        "active_rect": [8, 0, 24, 24],
        "cells": 4,
    }
    bare = island_files(coast=False)
    assert islandinfo.read(filedb.read(bare.read(SOURCE[:-4] + ".a7minfo")))["start_coast"] is None


def test_what_the_game_would_not_take_is_named():
    assert not islandinfo.check({"start_coast": 359.9, "active_rect": [0, 0, 32, 32]}, GRID)
    problems = islandinfo.check({"start_coast": 400, "active_rect": [0, 0, 40, 32]}, GRID)
    assert len(problems) == 2 and "360" in problems[0] and "grid" in problems[1]
    assert "cells" in islandinfo.check({"active_rect": [3, 0, 16, 16]}, GRID)[0]
    assert islandinfo.check({"active_rect": [16, 0, 8, 16]}, GRID)  # corners in order


def test_the_build_writes_the_start_coast_and_rectangle_beside_the_island(tmp_path):
    files = island_files()
    edits = {**islandedits.empty(), "meta": {"start_coast": 90, "active_rect": [0, 0, 32, 32]}}
    done = a7m_edit117.apply(files, SOURCE, edits, template=None)
    assert done.report["meta"] == 2 and done.info is not None
    back = filedb.read(done.info)
    assert islandinfo.read(back)["start_coast"] == pytest.approx(90, abs=0.01)
    assert islandinfo.read(back)["active_rect"] == [0, 0, 32, 32]
    assert struct.unpack("<f", back.path("ObjectMetaInfo/StartCoastDirection/value").data)[0] == pytest.approx(
        math.pi / 2
    )
    a7m_edit117.write_info(tmp_path, SOURCE, done)
    assert (tmp_path / SOURCE.replace(".a7m", ".a7minfo")).read_bytes() == done.info


def test_an_island_with_no_start_coast_value_gets_one():
    files = island_files(coast=False)
    done = a7m_edit117.apply(files, SOURCE, {**islandedits.empty(), "meta": {"start_coast": 45}}, template=None)
    assert islandinfo.read(filedb.read(done.info))["start_coast"] == pytest.approx(45, abs=0.01)


def test_active_cells_go_into_both_grids_and_the_rectangle_follows_them():
    files = island_files()
    done = a7m_edit117.apply(
        files, SOURCE, {**islandedits.empty(), "active": {"0,3": 1, "2,0": 0, "9,9": 1}}, template=None
    )
    assert done.report["active cells"] == 2  # the cell off the grid is left out
    isl = a7m_edit117.IslandFile(done.a7m, done.info)
    want = [row[:] for row in CELLS]
    want[3][0], want[0][2] = 1, 0
    assert read_bits(isl.rd.path("Terrain/ActiveMap")) == want and read_bits(isl.info.find("ActiveMapGrid")) == want
    assert islandinfo.read(isl.info)["active_rect"] == [0, 0, 24, 32]  # the drawn cells' box, in tiles


def test_a_rectangle_set_by_hand_wins_over_the_one_cut_round_the_cells():
    edits = {**islandedits.empty(), "active": {"0,3": 1}, "meta": {"active_rect": [8, 8, 24, 24]}}
    done = a7m_edit117.apply(island_files(), SOURCE, edits, template=None)
    assert islandinfo.read(filedb.read(done.info))["active_rect"] == [8, 8, 24, 24]


def test_an_island_without_meta_edits_writes_no_info_and_keeps_its_bytes():
    files = island_files()
    done = a7m_edit117.apply(files, SOURCE, islandedits.empty(), template=None)
    assert done.info is None
    isl = a7m_edit117.IslandFile(files.read(SOURCE), files.read(SOURCE[:-4] + ".a7minfo"))
    assert isl.write_info() == files.read(SOURCE[:-4] + ".a7minfo")  # the same layout, name ids and bytes


def test_a_new_island_gets_the_edited_info_instead_of_the_copied_one(tmp_path):
    files = island_files()
    out = tmp_path / "mod"
    edits_dir = tmp_path / "edits"
    edits_dir.mkdir()
    edits = {**islandedits.empty(), "source": SOURCE, "target": "new", "name": "isle_new", "meta": {"start_coast": 10}}
    (edits_dir / "117_isle.json").write_text(json.dumps(edits), encoding="utf-8")
    files.files["data/base/config/export/assets.xml"] = b"<Assets></Assets>"
    files.files[SOURCE[:-4] + ".a7me"] = b"me"
    files.list = lambda prefix: files.iter_paths(prefix)
    a7m_edit117.build(files, edits_dir, out / "m", "m", "M", log=lambda _t: None)
    written = (out / "m" / a7m_edit117.NEW_DIR / "isle_new" / "isle_new.a7minfo").read_bytes()
    assert islandinfo.read(filedb.read(written))["start_coast"] == pytest.approx(10, abs=0.01)
