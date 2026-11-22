"""Rivers, lakes, coast lines and path blockers of a 2070-format island: edited, written and read back."""

import struct

import numpy as np
import pytest
from tests.formats.isdfakes import ISLAND2070_SIZE as SIZE
from tests.formats.isdfakes import ISLAND2070_SOURCE as SOURCE
from tests.formats.isdfakes import install
from tests.formats.isdfakes import isd2070_bytes as isd_bytes

from annotoolbox.editor.editsession import CEditSession
from annotoolbox.editor.formats.isd import edit as isd_edit
from annotoolbox.editor.formats.isd import water as isd_water
from annotoolbox.formats import isd2070
from annotoolbox.formats.isd2070 import FIXED, SNode

CELLS = 2 * SIZE


def _fixed(x: float, z: float) -> bytes:
    return struct.pack("<qq", round(x * FIXED), round(z * FIXED))


def _river(points, widths, dialect=("p", "w")) -> SNode:
    pos, width = dialect
    items = [
        SNode(
            "i",
            children=[
                SNode(pos, data=struct.pack("<3f", *p)),
                SNode(width, f"{w:.6f}"),
                *([SNode("b", "1.000000"), SNode("d", "0.500000")] if pos == "p" else []),
            ],
        )
        for p, w in zip(points, widths, strict=True)
    ]
    outline, cells = isd_water.river_blocking(points, widths)
    return SNode(
        "i",
        children=[
            SNode("m_Setting", "N_TEST"),
            SNode("m_Points", children=items),
            SNode(
                "m_BuildBlocking", children=[SNode("Polygon", children=[SNode("i", data=_fixed(*v)) for v in outline])]
            ),
            SNode("m_PathBlockings", children=[SNode("i", data=_fixed(*c)) for c in cells]),
        ],
    )


def _bits(n: int, set_cells=()) -> SNode:
    grid = np.zeros((n, n), bool)
    for x, z in set_cells:
        grid[z, x] = True
    words = (n + 31) // 32
    padded = np.zeros((n, words * 32), np.uint8)
    padded[:, :n] = grid
    return SNode(
        "BitGrid",
        children=[
            SNode("m_XSize", str(n)),
            SNode("m_YSize", str(n)),
            SNode("m_IntXSize", str(words)),
            SNode("m_BitGrid", data=np.packbits(padded, bitorder="little").tobytes()),
        ],
    )


RIVER_A = ([(8.0, 0.5, 6.0), (9.0, 0.5, 10.0), (11.0, 0.4, 14.0), (12.0, 0.4, 18.0)], [2.0, 2.5, 3.0, 2.0])
RIVER_B = ([(20.0, 0.5, 4.0), (22.0, 0.5, 9.0), (25.0, 0.5, 12.0)], [1.5, 1.5, 1.5])
COAST = [(3.0, 3.0), (3.0, 9.0), (6.0, 14.0), (10.0, 20.0)]


SHAPE_A = [(5.0, 5.0), (9.0, 5.0), (9.0, 9.0), (5.0, 9.0)]
SHAPE_B = [(20.0, 20.0), (24.0, 20.0), (22.0, 24.0)]


def _shape(points) -> SNode:
    return SNode("i", children=[SNode("Polygon", children=[SNode("i", data=_fixed(x, z)) for x, z in points])])


def water_island(dialect=("p", "w")) -> bytes:
    """The test island with two rivers, a lake, a coast line (three segments) and both blocker layers."""
    root = isd2070.parse(isd_bytes())
    lake = SNode(
        "i",
        children=[
            SNode("m_Setting", "N_L17"),
            SNode("m_WaterHeight", str(round(1.5 * FIXED))),
            SNode(
                "m_Points",
                children=[SNode("i", data=_fixed(x, z)) for x, z in ((14, 14), (18, 14), (18, 18), (14, 18))],
            ),
            SNode("m_DamHeight", "0"),
        ],
    )
    coast = SNode(
        "i",
        children=[
            SNode("Points", children=[SNode("i", data=_fixed(x, z)) for x, z in COAST]),
            SNode("Directions", children=[SNode("i", str(d * 4096)) for d in (-90, 180, 0)]),
        ],
    )
    top = [
        SNode("Rivers", children=[_river(*RIVER_A, dialect), _river(*RIVER_B, dialect)]),
        SNode("Lakes", children=[lake]),
        SNode("CoastBuildingLines", children=[coast]),
        SNode("BuildBlockerShapes", children=[_shape([(1, 1), (2, 1)]), _shape(SHAPE_A), _shape(SHAPE_B)]),
        SNode("CamBlockerShapes", children=[_shape(SHAPE_A)]),
    ]
    root.children[4:4] = top
    manager = root.path("m_GOPManager/m_GRIDManager")
    manager.children.append(SNode("m_PathBlockerLayer", children=[_bits(CELLS, [(2, 2), (3, 2), (10, 10), (11, 10)])]))
    manager.children.append(SNode("m_HeightPathBlockerLayer", children=[_bits(CELLS, [(10, 10), (11, 10)])]))
    return isd2070.serialize(root)


def test_unedited_water_keeps_every_byte():
    data = water_island()
    isl = isd_edit.CIsland2070(data)
    isl.apply({}, {})
    assert isl.write() == data
    same = {"rivers": {"0": {"points": [list(p) for p in RIVER_A[0]], "widths": RIVER_A[1]}}, "lakes": {"0": 1.5}}
    isl.apply({**same, "coast": {"0": {"points": [list(p) for p in COAST]}}}, {})
    assert isl.write() == data  # an edit that changes nothing writes nothing


def test_network_reads_rivers_lakes_and_coast_lines():
    net = isd_water.network(isd2070.parse(water_island()))
    assert [len(r["points"]) for r in net["rivers"]] == [4, 3]
    assert net["rivers"][0]["widths"] == [2.0, 2.5, 3.0, 2.0]
    assert net["lakes"][0]["level"] == 1.5 and len(net["lakes"][0]["points"]) == 4
    assert net["coasts"][0]["points"] == [list(c) for c in COAST]


def test_river_edit_moves_points_and_redraws_the_blocking():
    isl = isd_edit.CIsland2070(water_island())
    points = [list(p) for p in RIVER_A[0]]
    points[1] = [10.5, 0.5, 10.0]
    widths = [2.0, 4.0, 3.0, 2.0]
    report = isl.apply({"rivers": {"0": {"points": points, "widths": widths}}}, {})
    assert report["rivers"] == 1 and report["unmatched"] == []
    out = isd2070.parse(isl.write())
    river = isd_water.river_items(out)[0]
    got, got_widths = isd_water.river_points(river)
    assert got[1] == pytest.approx(points[1]) and got_widths == pytest.approx(widths)
    outline, cells = isd_water.river_blocking(got, got_widths)
    drawn = [struct.unpack("<qq", n.data) for n in river.path("m_BuildBlocking").children[0].children]
    np.testing.assert_allclose(np.array(drawn) / FIXED, np.array(outline), atol=1 / FIXED)
    paths = [struct.unpack("<qq", n.data) for n in river.find("m_PathBlockings").children]
    np.testing.assert_allclose(np.array(paths) / FIXED, np.array(cells), atol=1 / FIXED)
    # the other river is untouched, byte for byte
    assert isd2070.serialize(SNode("", children=[isd_water.river_items(out)[1]])) == isd2070.serialize(
        SNode("", children=[isd_water.river_items(isd2070.parse(water_island()))[1]])
    )


def test_river_blocking_follows_the_river():
    outline, cells = isd_water.river_blocking([(10.0, 0.5, 4.0), (10.0, 0.5, 12.0)], [2.0, 2.0])
    assert sorted(outline) == [(9.0, 4.0), (9.0, 12.0), (11.0, 4.0), (11.0, 12.0)]
    xs = {x for x, _ in cells}
    assert 9.25 in xs and 10.75 in xs and 8.25 not in xs and 11.75 not in xs  # within 0.25 of the banks
    assert all(3.75 <= z <= 12.25 for _, z in cells)


def test_river_with_points_added_or_taken_out():
    isl = isd_edit.CIsland2070(water_island())
    points = [list(p) for p in RIVER_A[0]]
    longer = points[:2] + [[9.5, 0.5, 12.0]] + points[2:]
    isl.apply({"rivers": {"0": {"points": longer, "widths": [2.0, 2.5, 2.7, 3.0, 2.0]}}}, {})
    river = isd_water.river_items(isd2070.parse(isl.write()))[0]
    assert len(isd_water.river_points(river)[0]) == 5
    assert len(river.path("m_BuildBlocking").children[0].children) == 10
    fresh = isd_edit.CIsland2070(water_island())
    fresh.apply({"rivers": {"0": {"points": points[1:], "widths": RIVER_A[1][1:]}}}, {})
    river = isd_water.river_items(isd2070.parse(fresh.write()))[0]
    assert len(isd_water.river_points(river)[0]) == 3


def test_1404_rivers_use_position_and_width():
    isl = isd_edit.CIsland2070(water_island(("position", "width")))
    assert isd_water.network(isl.m_root)["rivers"][0]["widths"] == [2.0, 2.5, 3.0, 2.0]
    points = [list(p) for p in RIVER_A[0]]
    points[0] = [8.5, 0.5, 6.0]
    isl.apply({"rivers": {"0": {"points": points, "widths": RIVER_A[1]}}}, {})
    river = isd_water.river_items(isd2070.parse(isl.write()))[0]
    assert isd_water.river_points(river)[0][0] == pytest.approx(points[0])


def test_lake_level_and_coast_points():
    isl = isd_edit.CIsland2070(water_island())
    coast = [list(c) for c in COAST]
    coast[1] = [3.5, 9.25]
    report = isl.apply({"lakes": {"0": 2.25}, "coast": {"0": {"points": coast}}}, {})
    assert report["lakes"] == 1 and report["coast lines"] == 1
    out = isd2070.parse(isl.write())
    net = isd_water.network(out)
    assert net["lakes"][0]["level"] == 2.25
    np.testing.assert_allclose(net["coasts"][0]["points"], coast, atol=1 / FIXED)
    assert [int(n.text) for n in isd_water.coast_items(out)[0].find("Directions").children] == [
        d * 4096 for d in (-90, 180, 0)
    ]


def test_a_coast_line_with_more_points_gets_a_direction_per_segment():
    isl = isd_edit.CIsland2070(water_island())
    coast = [list(c) for c in COAST]
    coast.insert(2, [4.5, 11.5])
    isl.apply({"coast": {"0": {"points": coast}}}, {})
    node = isd_water.coast_items(isd2070.parse(isl.write()))[0]
    dirs = [int(n.text) // 4096 for n in node.find("Directions").children]
    assert len(dirs) == 4 and dirs[:2] == [-90, 180] and dirs[-1] == 0


def test_blocker_cells_are_painted_into_the_height_layer_only():
    isl = isd_edit.CIsland2070(water_island())
    report = isl.apply({"path_blockers": {"5,5": 1, "6,5": 1, "10,10": 0, "999,1": 1}}, {})
    assert report["path cells"] == 3 and report["unmatched"] == ["path cell 999,1"]
    out = isd2070.parse(isl.write())
    height, general = isd_water.blocker_layers(out)
    cells = isd_water._bits(height)[:CELLS, :CELLS].astype(bool)
    assert cells[5, 5] and cells[5, 6] and not cells[10, 10] and cells[10, 11]  # painted, cleared, kept
    assert isd_water._bits(general)[10, 10]  # the general layer is the island's own
    assert isd_water.blocker_cells(out)[5, 5]


def test_edit_that_matches_nothing_is_reported():
    isl = isd_edit.CIsland2070(water_island())
    report = isl.apply(
        {
            "rivers": {"7": {"points": [[1, 0, 1], [2, 0, 2]], "widths": [1, 1]}},
            "lakes": {"3": 1.0},
            "coast": {"2": {"points": [[1, 1], [2, 2]]}},
        },
        {},
    )
    assert report["unmatched"] == ["river 7", "lake 3", "coast line 2"]


@pytest.mark.parametrize(
    "change",
    [
        {"points": [[1, 0, 1]], "widths": [1]},  # one point is no line
        {"points": [[1, 0, 1], [2, 0, 2]]},  # no widths
        {"points": [[1, 0, 1], [2, 0, 2]], "widths": [1]},  # one width short
    ],
)
def test_a_river_edit_that_cannot_be_a_line_is_listed_and_leaves_the_island_as_it_was(change):
    data = water_island()
    isl = isd_edit.CIsland2070(data)
    assert isl.apply({"rivers": {"0": change}}, {})["unmatched"] == ["river 0"]
    assert isl.write() == data


def test_the_1404_logic_grid_follows_height_edits():
    root = isd2070.parse(isd_bytes())
    manager = root.path("m_GOPManager/m_GRIDManager")
    manager.find("m_HeightMap_v2").name = "m_HeightMap"  # 1404 names it so
    isl = isd_edit.CIsland2070(isd2070.serialize(root))
    isl.apply({"heights": {"4,4": 1.0}}, {})
    grid = np.frombuffer(isl.m_root.path("m_GOPManager/m_GRIDManager/m_HeightMap").data, "<i2").reshape(SIZE, SIZE)
    assert grid[3, 3] == 1024 + 256 and grid[0, 0] == 1024


def test_the_editor_shows_water_and_blockers_and_the_session_edits_them(tmp_path):
    ed = isd_edit.CIsland2070Editor(
        install({"data/levels/islands/normal/isle.isd": water_island()}),
        SOURCE,
        tmp_path / "c",
    )
    island = ed.island()
    assert len(island["water"]["rivers"]) == 2 and len(island["water"]["coasts"]) == 1
    assert island["blockers"]["n"] == CELLS
    session = CEditSession(island)
    session.m_path.move_coast_point(0, 1, [4.0, 0.7, 9.5])
    assert session.m_edits["coast"]["0"]["points"][1] == [4.0, 9.5]
    before = session.water_snapshot()
    session.set_lake_level(0, 2.0)
    session.end_water(before, "lake level")
    session.m_undo.undo()
    assert "0" not in session.m_edits["lakes"] and session.m_edits["coast"]["0"]["points"][1] == [4.0, 9.5]
    session.m_path.begin(True, 1.0, 5.0, 5.0)
    session.m_path.end()
    blocked = session.m_path.m_blocked
    assert blocked[10, 10] and blocked[9, 9] and len(session.m_edits["path_blockers"]) > 0
    session.m_undo.undo()
    assert not session.m_edits["path_blockers"] and not session.m_path.m_blocked[9, 9]
    session.m_path.begin(False, 1.0, 5.5, 5.5)  # (10, 10) and (11, 10) are blocked in the height layer
    session.m_path.end()
    assert not session.m_path.m_blocked[10, 10]


def test_the_blocker_shapes_are_numbered_among_those_with_three_points_and_written_back():
    isl = isd_edit.CIsland2070(water_island())
    net = isd_water.network(isd2070.parse(isl.write()))
    assert [len(c["points"]) for c in net["blockers"]["build"]] == [4, 3] and len(net["blockers"]["camera"]) == 1
    moved = [list(p) for p in SHAPE_B]
    moved[2] = [23.0, 25.5]
    edits = {
        "build": {"1": {"points": moved}, "5": {"points": moved}, "0": {"points": [[1, 1]]}},
        "camera": {"0": {"points": moved}},
    }
    report = isl.apply({"blocker_shapes": edits}, {})
    assert report["blocker shapes"] == 2
    assert report["unmatched"] == ["build blocker shape 0", "build blocker shape 5"]
    net = isd_water.network(isd2070.parse(isl.write()))
    np.testing.assert_allclose(net["blockers"]["build"][1]["points"], moved, atol=1 / FIXED)
    np.testing.assert_allclose(net["blockers"]["camera"][0]["points"], moved, atol=1 / FIXED)
    assert net["blockers"]["build"][0]["points"] == [list(p) for p in SHAPE_A]  # the other shape keeps its bytes
    assert isd_edit.CIsland2070(isl.write()).write() == isl.write()


def test_the_session_moves_a_blocker_shape_point_and_undoes_it(tmp_path):
    ed = isd_edit.CIsland2070Editor(
        install({"data/levels/islands/normal/isle.isd": water_island()}), SOURCE, tmp_path / "c"
    )
    session = CEditSession(ed.island())
    session.m_path.move_shape_point("build", 1, 2, [23.0, 0.7, 25.5])
    assert session.m_edits["blocker_shapes"]["build"]["1"]["points"][2] == [23.0, 25.5]
    assert session.m_path.shapes("build")[1]["points"][2] == [23.0, 25.5] and session.m_path.shapes("build")[0][
        "points"
    ][0] == [5.0, 5.0]
    before = session.water_snapshot()
    session.m_path.move_shape_point("camera", 0, 0, [6.0, 0.7, 6.0])
    session.end_water(before, "shape")
    session.m_undo.undo()
    assert "camera" not in session.m_edits["blocker_shapes"] or not session.m_edits["blocker_shapes"]["camera"]
    assert session.m_edits["blocker_shapes"]["build"]["1"]["points"][2] == [23.0, 25.5]
