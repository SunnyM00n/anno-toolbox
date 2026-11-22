"""The island's ``.a7minfo`` edits: the start coast, the active rectangle and the active map's cells, applied by the
117 and 1800 builds and written beside the island."""

import json
import math
import struct

import pytest
from tests.islands.islandfakes import CELLS, GRID, island_files
from tests.islands.islandfakes import ISLAND117_SOURCE as SOURCE

from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.formats import filedb
from annotoolbox.islands import islandedits, islandinfo
from annotoolbox.islands.islandgrid import read_bits


def test_the_numbers_are_read_from_the_file():
    isl = a7m_edit117.CIslandFile(island_files().read(SOURCE), island_files().read(SOURCE[:-4] + ".a7minfo"))
    info = islandinfo.read(isl.a7minfo)
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
        files, SOURCE, {**islandedits.empty(), "active": {"0,3": 1, "2,0": 0, "9,9": 1, "oops": 1}}, template=None
    )
    assert done.report["active cells"] == 2  # a cell off the grid and a malformed key are left out
    isl = a7m_edit117.CIslandFile(done.a7m, done.info)
    want = [row[:] for row in CELLS]
    want[3][0], want[0][2] = 1, 0
    assert (
        read_bits(isl.rd3d.path("Terrain/ActiveMap")) == want and read_bits(isl.a7minfo.find("ActiveMapGrid")) == want
    )
    assert islandinfo.read(isl.a7minfo)["active_rect"] == [0, 0, 24, 32]  # the drawn cells' box, in tiles


def test_a_rectangle_set_by_hand_wins_over_the_one_cut_round_the_cells():
    edits = {**islandedits.empty(), "active": {"0,3": 1}, "meta": {"active_rect": [8, 8, 24, 24]}}
    done = a7m_edit117.apply(island_files(), SOURCE, edits, template=None)
    assert islandinfo.read(filedb.read(done.info))["active_rect"] == [8, 8, 24, 24]


def test_an_island_without_meta_edits_writes_no_info_and_keeps_its_bytes():
    files = island_files()
    done = a7m_edit117.apply(files, SOURCE, islandedits.empty(), template=None)
    assert done.info is None
    isl = a7m_edit117.CIslandFile(files.read(SOURCE), files.read(SOURCE[:-4] + ".a7minfo"))
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
