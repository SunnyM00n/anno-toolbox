"""Rivers, lakes, coast lines and path blockers of a 2070-format island: edited, written and read back."""

import struct

import numpy as np
import pytest

from annotoolbox.editor.editsession import EditSession
from annotoolbox.editor.formats.isd import edit as isd_edit
from annotoolbox.editor.formats.isd import water as isd_water
from annotoolbox.formats import isd2070
from annotoolbox.formats.isd2070 import FIXED, Node
from test_island2070edit import SIZE, SOURCE, install, isd_bytes

CELLS = 2 * SIZE


def _fixed(x: float, z: float) -> bytes:
    return struct.pack("<qq", round(x * FIXED), round(z * FIXED))


def _river(points, widths, dialect=("p", "w")) -> Node:
    pos, width = dialect
    items = [
        Node(
            "i",
            children=[
                Node(pos, data=struct.pack("<3f", *p)),
                Node(width, f"{w:.6f}"),
                *([Node("b", "1.000000"), Node("d", "0.500000")] if pos == "p" else []),
            ],
        )
        for p, w in zip(points, widths, strict=True)
    ]
    outline, cells = isd_water.river_blocking(points, widths)
    return Node(
        "i",
        children=[
            Node("m_Setting", "N_TEST"),
            Node("m_Points", children=items),
            Node("m_BuildBlocking", children=[Node("Polygon", children=[Node("i", data=_fixed(*v)) for v in outline])]),
            Node("m_PathBlockings", children=[Node("i", data=_fixed(*c)) for c in cells]),
        ],
    )


def _bits(n: int, set_cells=()) -> Node:
    grid = np.zeros((n, n), bool)
    for x, z in set_cells:
        grid[z, x] = True
    words = (n + 31) // 32
    padded = np.zeros((n, words * 32), np.uint8)
    padded[:, :n] = grid
    return Node(
        "BitGrid",
        children=[
            Node("m_XSize", str(n)),
            Node("m_YSize", str(n)),
            Node("m_IntXSize", str(words)),
            Node("m_BitGrid", data=np.packbits(padded, bitorder="little").tobytes()),
        ],
    )


RIVER_A = ([(8.0, 0.5, 6.0), (9.0, 0.5, 10.0), (11.0, 0.4, 14.0), (12.0, 0.4, 18.0)], [2.0, 2.5, 3.0, 2.0])
RIVER_B = ([(20.0, 0.5, 4.0), (22.0, 0.5, 9.0), (25.0, 0.5, 12.0)], [1.5, 1.5, 1.5])
COAST = [(3.0, 3.0), (3.0, 9.0), (6.0, 14.0), (10.0, 20.0)]


SHAPE_A = [(5.0, 5.0), (9.0, 5.0), (9.0, 9.0), (5.0, 9.0)]
SHAPE_B = [(20.0, 20.0), (24.0, 20.0), (22.0, 24.0)]


def _shape(points) -> Node:
    return Node("i", children=[Node("Polygon", children=[Node("i", data=_fixed(x, z)) for x, z in points])])


def water_island(dialect=("p", "w")) -> bytes:
    """The test island with two rivers, a lake, a coast line (three segments) and both blocker layers."""
    root = isd2070.parse(isd_bytes())
    lake = Node(
        "i",
        children=[
            Node("m_Setting", "N_L17"),
            Node("m_WaterHeight", str(round(1.5 * FIXED))),
            Node(
                "m_Points", children=[Node("i", data=_fixed(x, z)) for x, z in ((14, 14), (18, 14), (18, 18), (14, 18))]
            ),
            Node("m_DamHeight", "0"),
        ],
    )
    coast = Node(
        "i",
        children=[
            Node("Points", children=[Node("i", data=_fixed(x, z)) for x, z in COAST]),
            Node("Directions", children=[Node("i", str(d * 4096)) for d in (-90, 180, 0)]),
        ],
    )
    top = [
        Node("Rivers", children=[_river(*RIVER_A, dialect), _river(*RIVER_B, dialect)]),
        Node("Lakes", children=[lake]),
        Node("CoastBuildingLines", children=[coast]),
        Node("BuildBlockerShapes", children=[_shape([(1, 1), (2, 1)]), _shape(SHAPE_A), _shape(SHAPE_B)]),
        Node("CamBlockerShapes", children=[_shape(SHAPE_A)]),
    ]
    root.children[4:4] = top
    manager = root.path("m_GOPManager/m_GRIDManager")
    manager.children.append(Node("m_PathBlockerLayer", children=[_bits(CELLS, [(2, 2), (3, 2), (10, 10), (11, 10)])]))
    manager.children.append(Node("m_HeightPathBlockerLayer", children=[_bits(CELLS, [(10, 10), (11, 10)])]))
    return isd2070.serialize(root)


def test_unedited_water_keeps_every_byte():
    data = water_island()
    isl = isd_edit.Island2070(data)
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
    isl = isd_edit.Island2070(water_island())
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
    assert isd2070.serialize(Node("", children=[isd_water.river_items(out)[1]])) == isd2070.serialize(
        Node("", children=[isd_water.river_items(isd2070.parse(water_island()))[1]])
    )


def test_river_blocking_follows_the_river():
    outline, cells = isd_water.river_blocking([(10.0, 0.5, 4.0), (10.0, 0.5, 12.0)], [2.0, 2.0])
    assert sorted(outline) == [(9.0, 4.0), (9.0, 12.0), (11.0, 4.0), (11.0, 12.0)]
    xs = {x for x, _ in cells}
    assert 9.25 in xs and 10.75 in xs and 8.25 not in xs and 11.75 not in xs  # within 0.25 of the banks
    assert all(3.75 <= z <= 12.25 for _, z in cells)


def test_river_with_points_added_or_taken_out():
    isl = isd_edit.Island2070(water_island())
    points = [list(p) for p in RIVER_A[0]]
    longer = points[:2] + [[9.5, 0.5, 12.0]] + points[2:]
    isl.apply({"rivers": {"0": {"points": longer, "widths": [2.0, 2.5, 2.7, 3.0, 2.0]}}}, {})
    river = isd_water.river_items(isd2070.parse(isl.write()))[0]
    assert len(isd_water.river_points(river)[0]) == 5
    assert len(river.path("m_BuildBlocking").children[0].children) == 10
    fresh = isd_edit.Island2070(water_island())
    fresh.apply({"rivers": {"0": {"points": points[1:], "widths": RIVER_A[1][1:]}}}, {})
    river = isd_water.river_items(isd2070.parse(fresh.write()))[0]
    assert len(isd_water.river_points(river)[0]) == 3


def test_1404_rivers_use_position_and_width():
    isl = isd_edit.Island2070(water_island(("position", "width")))
    assert isd_water.network(isl.root)["rivers"][0]["widths"] == [2.0, 2.5, 3.0, 2.0]
    points = [list(p) for p in RIVER_A[0]]
    points[0] = [8.5, 0.5, 6.0]
    isl.apply({"rivers": {"0": {"points": points, "widths": RIVER_A[1]}}}, {})
    river = isd_water.river_items(isd2070.parse(isl.write()))[0]
    assert isd_water.river_points(river)[0][0] == pytest.approx(points[0])


def test_lake_level_and_coast_points():
    isl = isd_edit.Island2070(water_island())
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
    isl = isd_edit.Island2070(water_island())
    coast = [list(c) for c in COAST]
    coast.insert(2, [4.5, 11.5])
    isl.apply({"coast": {"0": {"points": coast}}}, {})
    node = isd_water.coast_items(isd2070.parse(isl.write()))[0]
    dirs = [int(n.text) // 4096 for n in node.find("Directions").children]
    assert len(dirs) == 4 and dirs[:2] == [-90, 180] and dirs[-1] == 0


def test_blocker_cells_are_painted_into_the_height_layer_only():
    isl = isd_edit.Island2070(water_island())
    report = isl.apply({"path_blockers": {"5,5": 1, "6,5": 1, "10,10": 0, "999,1": 1}}, {})
    assert report["path cells"] == 3 and report["unmatched"] == ["path cell 999,1"]
    out = isd2070.parse(isl.write())
    height, general = isd_water.blocker_layers(out)
    cells = isd_water._bits(height)[:CELLS, :CELLS].astype(bool)
    assert cells[5, 5] and cells[5, 6] and not cells[10, 10] and cells[10, 11]  # painted, cleared, kept
    assert isd_water._bits(general)[10, 10]  # the general layer is the island's own
    assert isd_water.blocker_cells(out)[5, 5]


def test_edit_that_matches_nothing_is_reported():
    isl = isd_edit.Island2070(water_island())
    report = isl.apply(
        {
            "rivers": {"7": {"points": [[1, 0, 1], [2, 0, 2]], "widths": [1, 1]}},
            "lakes": {"3": 1.0},
            "coast": {"2": {"points": [[1, 1], [2, 2]]}},
        },
        {},
    )
    assert report["unmatched"] == ["river 7", "lake 3", "coast line 2"]


def test_the_1404_logic_grid_follows_height_edits():
    root = isd2070.parse(isd_bytes())
    manager = root.path("m_GOPManager/m_GRIDManager")
    manager.find("m_HeightMap_v2").name = "m_HeightMap"  # 1404 names it so
    isl = isd_edit.Island2070(isd2070.serialize(root))
    isl.apply({"heights": {"4,4": 1.0}}, {})
    grid = np.frombuffer(isl.root.path("m_GOPManager/m_GRIDManager/m_HeightMap").data, "<i2").reshape(SIZE, SIZE)
    assert grid[3, 3] == 1024 + 256 and grid[0, 0] == 1024


def test_the_editor_shows_water_and_blockers_and_the_session_edits_them(tmp_path):
    ed = isd_edit.Island2070Editor(
        install({"data/levels/islands/normal/isle.isd": water_island()}),
        SOURCE,
        tmp_path / "c",
    )
    island = ed.island()
    assert len(island["water"]["rivers"]) == 2 and len(island["water"]["coasts"]) == 1
    assert island["blockers"]["n"] == CELLS
    session = EditSession(island)
    session.path.move_coast_point(0, 1, [4.0, 0.7, 9.5])
    assert session.edits["coast"]["0"]["points"][1] == [4.0, 9.5]
    before = session.water_snapshot()
    session.set_lake_level(0, 2.0)
    session.end_water(before, "lake level")
    session.undo.undo()
    assert "0" not in session.edits["lakes"] and session.edits["coast"]["0"]["points"][1] == [4.0, 9.5]
    session.path.begin(True, 1.0, 5.0, 5.0)
    session.path.end()
    blocked = session.path.blocked
    assert blocked[10, 10] and blocked[9, 9] and len(session.edits["path_blockers"]) > 0
    session.undo.undo()
    assert not session.edits["path_blockers"] and not session.path.blocked[9, 9]
    session.path.begin(False, 1.0, 5.5, 5.5)  # (10, 10) and (11, 10) are blocked in the height layer
    session.path.end()
    assert not session.path.blocked[10, 10]


def test_the_blocker_shapes_are_numbered_among_those_with_three_points_and_written_back():
    isl = isd_edit.Island2070(water_island())
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
    assert isd_edit.Island2070(isl.write()).write() == isl.write()


def test_the_session_moves_a_blocker_shape_point_and_undoes_it(tmp_path):
    ed = isd_edit.Island2070Editor(
        install({"data/levels/islands/normal/isle.isd": water_island()}), SOURCE, tmp_path / "c"
    )
    session = EditSession(ed.island())
    session.path.move_shape_point("build", 1, 2, [23.0, 0.7, 25.5])
    assert session.edits["blocker_shapes"]["build"]["1"]["points"][2] == [23.0, 25.5]
    assert session.path.shapes("build")[1]["points"][2] == [23.0, 25.5] and session.path.shapes("build")[0]["points"][
        0
    ] == [5.0, 5.0]
    before = session.water_snapshot()
    session.path.move_shape_point("camera", 0, 0, [6.0, 0.7, 6.0])
    session.end_water(before, "shape")
    session.undo.undo()
    assert "camera" not in session.edits["blocker_shapes"] or not session.edits["blocker_shapes"]["camera"]
    assert session.edits["blocker_shapes"]["build"]["1"]["points"][2] == [23.0, 25.5]
