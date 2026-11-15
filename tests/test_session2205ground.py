import os
import struct
from pathlib import Path

import numpy as np
import pytest

from annotoolbox.editor.formats.a7m import edit2205 as a7m_edit2205
from annotoolbox.editor.formats.a7m import session as a7m_session
from annotoolbox.editor.formats.a7m import sessionground as a7m_sessionground
from annotoolbox.editor.formats.a7m.reader import archive_members
from annotoolbox.formats import filedb, rda
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands.islandterrain import TMC_VERTEX
from fakes import INSTALL_VARS, FakeFiles
from test_session2205 import LOOSE_RD3D, PATH, SIZE, _fixed, _session

CODES = a7m_sessionground.CODES


def _nodes() -> Tag:
    """Two mesh levels of one node each: vertices at tile (2, 2), (0, 0) and (3, 1), heights 1.0, 0.0 and 0.5."""
    rec = np.zeros(3, TMC_VERTEX)
    rec["x"], rec["z"] = [64, 0, 96], [64, 0, 32]
    rec["y"] = [32768 + CODES, 32768, 32768 + CODES // 2]
    rec["n"] = [128, 255, 128, 0, 127, 127]

    def level(size: int) -> Tag:
        node = Tag(
            "#1",
            [
                Attrib("BoxMin", struct.pack("<3f", 0, 0, 0)),
                Attrib("BoxMax", struct.pack("<3f", 4, 1, 4)),
                Attrib("Vertices", rec.tobytes()),
                Attrib("UsedMaterialBits", struct.pack("<I", 0b10)),
            ],
        )
        return Tag("#1", [Attrib("LevelSize", struct.pack("<I", size)), Tag("Nodes", [node])])

    return Tag("Levels", [level(1), level(1)])


def _lake() -> Tag:
    curve = [Tag("#1", [Attrib("p", struct.pack("<3f", x, 0.5, z))]) for x, z in ((0, 0), (2, 0), (2, 2))]
    bounds = [Attrib("Minimum", struct.pack("<3f", 0, 0.5, 0)), Attrib("Maximum", struct.pack("<3f", 2, 0.5, 2))]
    return Tag(
        "#1",
        [
            Attrib("guid", struct.pack("<I", 1609)),
            Attrib("ID", struct.pack("<II", 5, 0x80000001)),
            Tag("Position", [Attrib("x", _fixed(1.0)), Attrib("y", _fixed(0.5)), Attrib("z", _fixed(1.0))]),
            Tag("BezierPath", [Tag("Path", [*bounds, Tag("BezierCurve", curve)])]),
            Tag("Lake", []),
        ],
    )


def _files() -> FakeFiles:
    """The synthetic session with what a ground edit reaches: tile heights, sea depth, mesh levels, a lake, and a
    position and bounds on its river."""
    files = _session()
    loose = archive_members(files[LOOSE_RD3D])[1]["rd3d.data"]
    rd = filedb.read(loose)
    coarse = np.frombuffer(rd.path("Terrain/CoarseHeightMap/map").data, "<i2").reshape(2 * SIZE + 1, -1)
    centres = (coarse[1::2, 1::2] / CODES).astype("<f4")
    rd.children += [
        Tag(
            "TerrainHeightMap",
            [
                Attrib("width", struct.pack("<I", SIZE)),
                Attrib("height", struct.pack("<I", SIZE)),
                Attrib("map", centres.tobytes()),
            ],
        ),
        Tag(
            "WaterInfos",
            [
                Attrib("width", struct.pack("<I", 2)),
                Attrib("height", struct.pack("<I", 2)),
                Attrib("map", bytes([9, 1, 2, 3]) * 4),
            ],
        ),
    ]
    rd.path("Terrain").children.append(_nodes())
    files[LOOSE_RD3D] = rda.write_v22({"rd3d.data": filedb.write(rd, *filedb.name_ids(loose), version=1)})
    members = archive_members(files[PATH])[1]
    raw = members["gamedata.data"]
    gd = filedb.read(raw)
    objects = gd.path("GameSessionManager/GameObjectManager/EditorObject/objects")
    river = objects.children[1]
    river.children.insert(2, Tag("Position", [Attrib("x", _fixed(0.0)), Attrib("z", _fixed(1.0))]))
    river.path("BezierPath/Path").children.insert(0, Attrib("Minimum", struct.pack("<3f", 0, 0.5, 1)))
    river.path("BezierPath/Path").children.insert(1, Attrib("Maximum", struct.pack("<3f", 3, 0.5, 1)))
    objects.children.append(_lake())
    members["gamedata.data"] = filedb.write(gd, *filedb.name_ids(raw), version=1)
    files[PATH] = rda.write_v22(members)
    return FakeFiles(files)


def _paths(a: Tag, b: Tag, at: str = "") -> list[str]:
    """The attributes at which two trees of the same shape differ (the shape itself must match)."""
    assert [c.name for c in a.children] == [c.name for c in b.children], at
    out = []
    for x, y in zip(a.children, b.children, strict=True):
        if isinstance(x, Tag):
            out += _paths(x, y, f"{at}/{x.name}")
        elif x.data != y.data:
            out.append(f"{at}/{x.name}")
    return out


def _written(isl: a7m_session.Session2205) -> tuple[Tag, Tag]:
    return (
        filedb.read(a7m_edit2205.member_bytes(isl, "gamedata.data")),
        filedb.read(a7m_edit2205.member_bytes(isl, "rd3d.data")),
    )


def _edit(**kinds) -> tuple[a7m_session.Session2205, dict]:
    isl = a7m_session.Session2205(_files(), PATH)
    return isl, a7m_edit2205.apply(isl, {"source": PATH, **kinds})


def test_an_unedited_session_with_ground_data_is_written_back_unchanged():
    isl = a7m_session.Session2205(_files(), PATH)
    assert a7m_edit2205.apply(isl, {})["members"] == []
    gd, rd = _written(isl)
    assert _paths(gd, filedb.read(a7m_edit2205.member_bytes(isl, "gamedata.data"))) == []
    assert rd.path("Terrain/Levels") is not None


def test_a_height_edit_changes_the_heights_the_derived_maps_the_land_the_sea_and_the_meshes_and_nothing_else():
    before = a7m_session.Session2205(_files(), PATH)
    gd0, rd0 = _written(before)
    isl, report = _edit(heights={"2,2": 1.0, "1,1": -5.0})
    gd, rd = _written(isl)
    assert report["members"] == ["gamedata.data", "rd3d.data"]
    assert report["heights"]["corners"] == 2 and report["heights"]["land tiles changed"] > 0
    assert set(_paths(rd0, rd)) == {
        "/Terrain/CoarseHeightMap/map",
        "/Terrain/Levels/#1/Nodes/#1/BoxMin",
        "/Terrain/Levels/#1/Nodes/#1/BoxMax",
        "/Terrain/Levels/#1/Nodes/#1/Vertices",
        "/TerrainHeightMap/map",
        "/WaterInfos/map",
    }
    assert set(_paths(gd0, gd)) == {
        "/GameSessionManager/TerrainManager/HeightMap/HeightMap",
        "/GameSessionManager/WorldManager/Water/bits",
    }
    # the corner (2, 2) is sample (4, 4): one unit up, the corner (1, 1) five down
    w = 2 * SIZE + 1
    was = np.frombuffer(rd0.path("Terrain/CoarseHeightMap/map").data, "<i2").reshape(w, w).astype(int)
    now = np.frombuffer(rd.path("Terrain/CoarseHeightMap/map").data, "<i2").reshape(w, w).astype(int)
    assert now[4, 4] - was[4, 4] == CODES and now[2, 2] - was[2, 2] == -5 * CODES and now[0, 8] == was[0, 8]
    game = np.frombuffer(gd.path("GameSessionManager/TerrainManager/HeightMap/HeightMap").data, "<i2").reshape(w, w)
    game0 = np.frombuffer(gd0.path("GameSessionManager/TerrainManager/HeightMap/HeightMap").data, "<i2").reshape(w, w)
    assert game[4, 4] - game0[4, 4] == a7m_sessionground.GAME_CODES
    centres = np.frombuffer(rd.path("TerrainHeightMap/map").data, "<f4").reshape(SIZE, SIZE)
    assert centres[1, 1] == pytest.approx(now[3, 3] / CODES)  # the centre of tile (1, 1) is sample (3, 3)
    sea = np.frombuffer(rd.path("WaterInfos/map").data, np.uint8).reshape(2, 2, 4)
    assert sea[0, 0, 0] == 255 and sea[0, 0, 1:].tolist() == [1, 2, 3] and sea[1, 1].tolist() == [9, 1, 2, 3]
    nodes = rd.path("Terrain/Levels").findall("#1")
    for level in nodes:
        node = level.find("Nodes").children[0]
        v = np.frombuffer(node.find("Vertices").data, TMC_VERTEX)
        assert v["y"].tolist() == [32768 + 2 * CODES, 32768, 32768 + CODES // 2]  # only the vertex at (2, 2) rose
        assert v["n"][0, 3:].tolist() == [0, 127, 127]
        box_min, box_max = (struct.unpack("<3f", node.find(n).data)[1] for n in ("BoxMin", "BoxMax"))
        assert (box_min, box_max) == pytest.approx((now.min() / CODES, now.max() / CODES))


def test_a_lifted_sea_floor_leaves_the_games_flat_floor_value_at_zero_until_it_is_cleared():
    isl = a7m_session.Session2205(_files(), PATH)
    tag = isl.gm.path("TerrainManager/HeightMap")
    w = tag.find("Width").u32()
    old = np.full((w, w), a7m_sessionground.FLOOR)
    new = old.copy()
    new[3, 3] = a7m_sessionground.FLOOR + 2.0
    tag.find("HeightMap").data = bytes(2 * w * w)  # a map that is all flat floor: 0 in the game's own heights
    a7m_sessionground._game_heights(isl, old, new)
    g = np.frombuffer(tag.find("HeightMap").data, "<i2").reshape(w, w)
    assert g[3, 3] == round(-6 * a7m_sessionground.GAME_CODES) and g[0, 0] == 0


def test_paint_lays_a_material_in_the_session_maps_and_marks_it_used_under_the_stroke():
    isl, report = _edit(paint=[{"material": 3, "points": [[3.0, 1.0]], "radius": 1.0}])
    assert report["paint"]["strokes"] == 1 and report["paint"]["blocks"] >= 1
    assert report["members"] == ["rd3d.data"]
    mids, wts = a7m_session.Session2205(_files(), PATH).weights()
    new_mids, new_wts = isl.weights()
    z, x = 2, 6  # a texel at the stroke's centre (tile (3, 1): two texels a tile)
    slot = [s for s in range(6) if new_mids[s, z, x] == 3]
    assert slot and new_wts[slot[0], z, x] > 0.95 and wts[mids == 3].sum() < new_wts[new_mids == 3].sum()
    for level in isl.rd.path("Terrain/Levels").findall("#1"):
        assert struct.unpack("<I", level.find("Nodes").children[0].find("UsedMaterialBits").data)[0] == 0b1010


def test_river_and_lake_edits_write_into_the_session_s_editor_objects_in_fixed_point():
    isl, report = _edit(
        rivers={"0": {"points": [[0, 0.5, 1], [2, 0.5, 2], [3, 0.5, 3]], "widths": [2, 2, 2]}}, lakes={"0": 2.0}
    )
    assert report["water"]["rivers"] == 1 and report["water"]["lakes"] == 1 and report["water"]["unmatched"] == []
    assert report["members"] == ["gamedata.data"]
    splines = {kind: (pts, widths) for kind, pts, widths in isl.splines()}
    assert len(splines["River"][0]) == 3 and splines["River"][0][1] == (2.0, 0.5, 2.0)
    assert {p[1] for p in splines["Lake"][0]} == {2.0}
    lake = [o for _g, o in isl.object_tags(("EditorObject",)) if o.find("Lake") is not None][0]
    assert a7m_session.object_position(lake) == [1.0, 2.0, 1.0]  # the level only; x and z stay
    assert struct.unpack("<3f", lake.path("BezierPath/Path/Maximum").data)[1] == 2.0
    assert report["water"] and a7m_edit2205.apply(isl, {"rivers": {"9": {"points": [], "widths": []}}})["water"][
        "unmatched"
    ] == ["river 9"]


def test_what_a_ground_edit_cannot_place_is_listed_in_the_reports_unmatched():
    isl, report = _edit(
        rivers={"9": {"points": [], "widths": []}, "0": {"points": [[0, 0.5, 1], [2, 0.5, 2]]}},
        paint=[{"material": 31, "points": [[3.0, 1.0]], "radius": 1.0}],
    )
    assert report["unmatched"][:2] == ["river 0", "river 9"]
    assert report["unmatched"][2:] == ["1 painted strokes of a material outside the island's set"]
    assert report["water"]["rivers"] == 0


def test_the_ground_of_an_installed_session_writes_back_with_nothing_but_the_edit_changed():
    folder = os.environ.get(INSTALL_VARS["anno2205"])
    if not folder or not (Path(folder) / "maindata").is_dir():
        pytest.skip("needs the installed Anno 2205")
    from annotoolbox.sources.gametree import open_game

    tree = open_game("anno2205", Path(folder), None)
    path = "data/sessions/meta/strategic_view/strategic_view_01.a6m"
    if not tree.exists(path):
        pytest.skip("no strategic view map")
    isl = a7m_session.Session2205(tree, path)
    gd0, rd0 = _written(isl)
    n = isl.size
    isl2 = a7m_session.Session2205(tree, path)
    report = a7m_edit2205.apply(isl2, {"heights": {f"{n // 2},{n // 2}": 2.0}})
    assert report["heights"]["corners"] == 1
    _, rd = _written(isl2)
    changed = set(_paths(rd0, rd))
    assert "/Terrain/CoarseHeightMap/map" in changed
    assert all(p.startswith(("/Terrain/", "/TerrainHeightMap", "/WaterInfos")) for p in changed)
