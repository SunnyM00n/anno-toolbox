"""The active map in the editing session: the cell brush, the edit file's ``active`` key, the island JSON, the overlay."""

import numpy as np

from annotoolbox.editor import cellbrush
from annotoolbox.editor.desktop import desktopgrids
from annotoolbox.editor.desktop.desktopbrush import BrushTools
from annotoolbox.editor.editsession import EditSession
from annotoolbox.editor.islandeditor import IslandEditor
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands import islandedits
from annotoolbox.islands.islandgrid import write_bits
from fakes import edit_island

BASE = np.array([[0, 1], [1, 1]], bool)  # the 16-tile island's 2 x 2 cells of 8 tiles: the top left is not drawn


def session(edits=None, readonly=False) -> EditSession:
    return EditSession(edit_island(active_map=cellbrush.pack(BASE)), edits, readonly)


def stroke(s: EditSession, on: bool, x: float, z: float, radius: float = 1.0) -> None:
    s.active.begin(on, radius, x, z)
    s.active.end()


def test_the_island_json_round_trips_the_cells_and_a_cell_is_8_tiles():
    s = session()
    assert np.array_equal(s.active.cells, BASE) and s.active.cell == 8 and s.active.tiles() == 3 * 64
    assert EditSession(edit_island()).active.cells is None  # an island with no active map offers none
    assert cellbrush.pack(BASE)["n"] == 2


def test_the_brush_draws_the_cells_it_touches_and_keeps_only_the_difference():
    s = session()
    stroke(s, True, 2.0, 2.0)  # inside the top left cell
    assert s.active.cells[0, 0] and s.edits["active"] == {"0,0": 1}
    stroke(s, False, 12.0, 12.0)
    assert not s.active.cells[1, 1] and s.edits["active"] == {"0,0": 1, "1,1": 0}
    stroke(s, True, 12.0, 12.0)  # back to the island's own: the key goes
    assert s.edits["active"] == {"0,0": 1}


def test_a_radius_reaches_the_cells_next_to_it_by_their_nearest_edge():
    s = session()
    stroke(s, False, 8.0, 8.0, radius=1.5)  # on the corner of all four cells
    assert not s.active.cells.any()
    s = session()
    stroke(s, False, 5.0, 12.0, radius=1.0)  # 3 tiles from the cell to its right: out of reach
    assert s.active.cells[1, 1] and not s.active.cells[1, 0]


def test_a_stroke_is_one_undoable_step_and_the_edit_file_carries_it(tmp_path):
    s = session()
    s.active.begin(False, 1.0, 12.0, 2.0)
    s.active.to(12.0, 12.0)
    s.active.end()
    assert s.edits["active"] == {"1,0": 0, "1,1": 0} and len(s.undo.commands) == 1
    islandedits.save(tmp_path / "e.json", s.edits)
    again = session(islandedits.load(tmp_path / "e.json"))
    assert np.array_equal(again.active.cells, s.active.cells)
    assert s.undo.undo() == "active map" and not s.edits["active"] and np.array_equal(s.active.cells, BASE)
    s.undo.redo()
    assert s.active.cells.tolist() == [[False, False], [True, False]]


def test_nothing_is_edited_on_a_read_only_island_or_without_an_active_map():
    locked = session(readonly=True)
    stroke(locked, True, 2.0, 2.0)
    assert np.array_equal(locked.active.cells, BASE) and not locked.undo.commands
    plain = EditSession(edit_island())
    stroke(plain, True, 2.0, 2.0)
    assert not plain.undo.commands


def test_the_window_brush_and_the_overlay_follow_the_session():
    s = session()
    sent = []
    view = type(
        "V",
        (),
        {
            "session": s,
            "tool": "active",
            "pick": None,
            "paint_material": None,
            "status": type("S", (), {"emit": sent.append})(),
            "brush": {"mode": "raise", "radius": 1, "strength": 0.5, "plant": True},
            "from_view": lambda self, v: [s.size - v[0], v[1], v[2]],
            "update": lambda self: None,
            "_rgba": lambda self, key: (0.1, 0.2, 0.3, 1.0),
        },
    )()
    tools = BrushTools(view)
    assert tools.start((14.0, 0.0, 2.0)) and tools.end() is None  # view x 14 is x = 2
    assert s.edits["active"] == {"0,0": 1}
    lines, crosses = desktopgrids.active_overlay(view)
    assert lines[0] == "lines" and lines[1].shape[1] == 7 and len(lines[1]) % 2 == 0
    assert crosses[1].shape[1] == 7 and len(crosses[1]) >= 4  # the changed cell has its cross
    assert desktopgrids.outline(s, np.zeros((2, 2), bool), 8, (1, 1, 1, 1)).shape == (0, 7)
    s.active.cells = None
    assert desktopgrids.active_overlay(view) == []


def test_an_island_editor_gives_its_render_data_active_cells_for_the_json():
    grid = Tag(
        "ActiveMap",
        [Attrib("x", (2).to_bytes(4, "little")), Attrib("y", (2).to_bytes(4, "little")), Attrib("bits", bytes(4 * 2))],
    )
    write_bits(grid, BASE.astype(int).tolist())
    editor = IslandEditor.__new__(IslandEditor)
    editor.view = type("View", (), {"rd": Tag("rd", [Tag("Terrain", [grid])])})()
    (data,) = editor.active_map().values()
    assert data == cellbrush.pack(BASE)
    editor.view = type("View", (), {"rd": Tag("rd", [])})()
    assert editor.active_map() == {}


RIVER = np.zeros((16, 16), bool)  # the 16-tile island's river tiles: a row of five at z = 4, x = 4..8
RIVER[4, 4:9] = True
FORDS = np.zeros((16, 16), bool)
FORDS[4, 5] = True


def ford_session(edits=None) -> EditSession:
    return EditSession(edit_island(ford_grid=cellbrush.pack(FORDS, RIVER)), edits)


def test_the_ford_brush_lays_fords_on_river_tiles_only_and_keeps_the_difference():
    s = ford_session()
    assert s.ford.cell == 1 and np.array_equal(s.ford.cells, FORDS) and s.active.cells is None
    s.ford.begin(True, 2.0, 6.5, 4.5)  # five tiles round the river's middle, two of them off the river
    s.ford.end()
    assert s.ford.cells[4, 4:9].all() and s.ford.cells.sum() == 5  # the ones off the river were left out
    assert s.edits["ford"] == {"4,4": 1, "6,4": 1, "7,4": 1, "8,4": 1}  # (5, 4) was a ford already
    s.ford.begin(False, 0.4, 5.5, 4.5)
    s.ford.end()
    assert s.edits["ford"].get("5,4") == 0 and not s.ford.cells[4, 5]
    assert s.undo.undo() == "ford" and s.ford.cells[4, 5]
    s.ford.begin(False, 20.0, 8.0, 8.0)  # erasing reaches everything
    s.ford.end()
    assert not s.ford.cells.any() and s.edits["ford"] == {"5,4": 0}


def test_the_cell_tool_edits_the_ford_grid_when_the_brush_says_so_and_the_overlay_follows():
    s = ford_session()
    view = type(
        "V",
        (),
        {
            "session": s,
            "tool": "active",
            "pick": None,
            "paint_material": None,
            "status": type("S", (), {"emit": lambda self, text: None})(),
            "brush": {"mode": "raise", "radius": 0.4, "strength": 0.5, "plant": True, "grid": "ford"},
            "from_view": lambda self, v: [s.size - v[0], v[1], v[2]],
            "update": lambda self: None,
            "_rgba": lambda self, key: (0.1, 0.2, 0.3, 1.0),
        },
    )()
    tools = BrushTools(view)
    assert tools.start((16 - 7.5, 0.0, 4.5)) and tools.end() is None  # the river tile (7, 4)
    assert s.edits["ford"] == {"7,4": 1} and not s.edits.get("active")
    points, crosses = desktopgrids.active_overlay(view)
    assert points[0] == "points" and points[1].shape[1] == 7 and len(points[1]) == 5  # three free, two fords
    assert crosses[0] == "lines" and len(crosses[1]) >= 4
    view.brush["grid"] = None  # an island with no active map offers no overlay
    assert desktopgrids.active_overlay(view) == []


def test_an_island_editor_gives_the_ford_and_river_grids_to_the_json():
    def grid(name: str, cells: np.ndarray) -> Tag:
        tag = Tag(
            name,
            [
                Attrib("x", (16).to_bytes(4, "little")),
                Attrib("y", (16).to_bytes(4, "little")),
                Attrib("bits", bytes(32)),
            ],
        )
        write_bits(tag, cells.astype(int).tolist())
        return tag

    editor = IslandEditor.__new__(IslandEditor)
    editor.view = type(
        "View", (), {"gm": Tag("gm", [Tag("WorldManager", [grid("FordGrid", FORDS), grid("RiverGrid", RIVER)])])}
    )()
    assert editor.ford_grid() == {"ford_grid": cellbrush.pack(FORDS, RIVER)}
    editor.view = type("View", (), {"gm": Tag("gm", [Tag("WorldManager", [grid("RiverGrid", RIVER)])])})()
    assert editor.ford_grid() == {}


def test_the_ribbon_names_the_cell_button_of_the_grid_being_edited():
    from annotoolbox.editor.desktop.desktopribbon import items, tool_action

    ids = {item.id for item in items()}
    assert {"tool_cell_on", "tool_ford_on", "tool_ford_off"} <= ids
    assert tool_action("active", {"plant": True}) == "tool_cell_on"
    assert tool_action("active", {"plant": False, "grid": "ford"}) == "tool_ford_off"


ZONES = np.full((4, 4), 20, np.uint8)  # the 16-tile island's environment grid: a cell of 4 x 4 tiles, all sea


def environment_session(edits=None) -> EditSession:
    return EditSession(edit_island(environment_map=cellbrush.pack_values(ZONES)), edits)


def test_the_environment_brush_paints_a_zone_and_keeps_only_the_difference():
    s = environment_session()
    assert s.environment.per == 4.0 and np.array_equal(s.environment.cells, ZONES)
    s.environment.begin(8, 1.0, 6.0, 6.0)  # the cell (1, 1): its centre is (6, 6)
    s.environment.end()
    assert s.environment.cells[1, 1] == 8 and s.edits["environment"] == {"1,1": 8}
    s.environment.begin(20, 1.0, 6.0, 6.0)  # back to the island's own: the key goes
    s.environment.end()
    assert s.edits["environment"] == {} and len(s.undo.commands) == 2
    s.undo.undo()
    assert s.environment.cells[1, 1] == 8 and s.undo.undo() == "environment"
    assert np.array_equal(environment_session({"environment": {"2,0": 9}}).environment.changed().nonzero()[0], [0])


def test_the_cell_tool_paints_the_zone_of_the_zone_box_and_the_overlay_colours_each_zone():
    s = environment_session()
    view = type(
        "V",
        (),
        {
            "session": s,
            "tool": "active",
            "pick": None,
            "paint_material": None,
            "status": type("S", (), {"emit": lambda self, text: None})(),
            "brush": {"radius": 1.0, "plant": True, "grid": "environment", "zone": 1},  # the second zone: Forest
            "from_view": lambda self, v: [s.size - v[0], v[1], v[2]],
            "update": lambda self: None,
            "_rgba": lambda self, key: (0.1, 0.2, 0.3, 1.0),
        },
    )()
    assert BrushTools(view).start((16 - 2.0, 0.0, 2.0)) and BrushTools(view).end() is None
    assert s.edits["environment"] == {"0,0": 4}
    points, crosses = desktopgrids.active_overlay(view)
    assert points[0] == "points" and len(points[1]) == 16 and crosses[0] == "lines"
    assert len({tuple(row[3:6]) for row in points[1]}) == 2  # two zones, two colours


def test_an_island_editor_gives_the_environment_zones_to_the_json():
    def zones(values: np.ndarray) -> Tag:
        return Tag(
            "EnvironmentGRid",
            [
                Attrib("x", (4).to_bytes(4, "little")),
                Attrib("y", (4).to_bytes(4, "little")),
                Attrib("val", values.tobytes()),
            ],
        )

    editor = IslandEditor.__new__(IslandEditor)
    editor.view = type("View", (), {"gm": Tag("gm", [Tag("WorldManager", [Tag("EnvironmentGrid", [zones(ZONES)])])])})()
    assert editor.environment_grid() == {"environment_map": cellbrush.pack_values(ZONES)}
    editor.view = type("View", (), {"gm": Tag("gm", [Tag("WorldManager", [])])})()
    assert editor.environment_grid() == {}


def test_the_ribbon_offers_the_zone_box_and_names_its_button():
    from annotoolbox.editor.desktop.desktopribbon import items, tool_action
    from annotoolbox.editor.islandgrids import ZONES as NAMES

    box = next(item for item in items() if item.id == "zone")
    assert box.kind == "combo" and len(box.choices) == len(NAMES) and box.choices[0] == "Meadow"
    assert tool_action("active", {"plant": True, "grid": "environment"}) == "tool_zone"


def test_a_hand_edited_edit_file_with_bad_cells_opens_and_the_good_cells_count():
    bad = {"x,y": 4, "9,9": 4, "0,0": 300, "1,0": "no", "1,1": 7}
    s = environment_session({"environment": bad})  # a key that is no cell, one off the grid, a byte that is none
    assert s.environment.cells[1, 1] == 7 and s.environment.changed().sum() == 1
    f = EditSession(edit_island(ford_grid=cellbrush.pack(BASE, BASE)), {"ford": {"a,b": 1, "0,0": 1}})
    assert f.ford.cells[0, 0] and f.ford.n == 2  # the edit file's good cell is on, its bad key is left out


def test_the_cell_points_are_capped_once_and_keep_each_cells_own_colour():
    n = 200  # 40,000 cells: the cap holds for all zones together, not for each zone
    ground = type("G", (), {"size": n * 4, "heights_at": lambda self, xs, zs: np.zeros(len(xs))})()
    zone = np.arange(n * n).reshape(n, n) % 3
    colours = np.stack([zone == 0, zone == 1, zone == 2], axis=-1).astype(float)
    rows = desktopgrids._cell_points(ground, np.ones((n, n), bool), 4, colours, 0.8)
    assert len(rows) == desktopgrids.POINTS and {tuple(r[3:6]) for r in rows} == {(1, 0, 0), (0, 1, 0), (0, 0, 1)}


def test_the_heights_of_many_points_are_those_of_one_point():
    s = session()
    xs, zs = np.array([0.2, 7.7, 15.9, -3.0, 40.0]), np.array([1.0, 8.4, 3.3, 5.0, 2.0])
    assert [round(float(h), 4) for h in s.heights_at(xs, zs)] == [
        round(s.height_at(float(x), float(z)), 4) for x, z in zip(xs, zs, strict=True)
    ]


def _area_gm(size: int = 16):
    """An ``AreaIDs`` grid: the left half the construction area, a column of another area id, the rest none."""
    from annotoolbox.islands.islandgrid import AREA_ISLAND, AREA_NONE, write_sparse

    header = Tag("block", [Attrib("x", (16).to_bytes(2, "little"))])
    tag = Tag("AreaIDs", [Attrib("x", size.to_bytes(4, "little")), Attrib("y", size.to_bytes(4, "little")), header])
    write_sparse(
        tag, [[0x4001 if x == 3 else AREA_ISLAND if x < 8 else AREA_NONE for x in range(size)] for _ in range(size)]
    )
    return Tag("gm", [tag])


def test_the_area_brush_draws_the_construction_area_and_never_a_tile_of_another_area():
    from annotoolbox.islands.islandgrid import read_sparse

    gm = _area_gm()
    ids = np.asarray(read_sparse(gm.path("AreaIDs")))
    area = cellbrush.pack(ids == 0x2001, (ids == 0x2001) | (ids == 1))
    s = EditSession(edit_island(area_grid=area))
    assert s.area.cell == 1 and s.area.cells[0, 5] and not s.area.cells[0, 10] and s.active.cells is None
    s.area.begin(True, 2.0, 9.5, 5.5)
    s.area.end()
    assert s.area.cells[5, 8:11].all() and set(s.edits["area"]) >= {"9,5", "8,5"}
    s.undo.undo()
    assert not s.edits["area"] and not s.area.cells[5, 9]
    s.area.begin(True, 1.5, 3.5, 5.5)  # the column of another area id is out of reach
    s.area.end()
    assert not s.area.cells[5, 3] and "3,5" not in s.edits["area"]


def test_area_edits_are_written_into_the_area_ids_and_a_tile_of_another_area_is_left_out():
    from annotoolbox.islands.islandgrid import AREA_ISLAND, AREA_NONE, read_sparse

    gm = _area_gm()
    report = islandedits.apply_area(gm, {"9,5": 1, "0,0": 0, "3,2": 1, "99,1": 1, "x,y": 0, "-1,2": 1, "8,5": 1})
    ids = np.asarray(read_sparse(gm.path("AreaIDs")))
    assert ids[5, 9] == AREA_ISLAND and ids[0, 0] == AREA_NONE and ids[2, 3] == 0x4001
    assert report["cells"] == 3 and report["unmatched"] == [
        "area tile -1,2",
        "area tile 3,2",
        "area tile 99,1",
        "area tile x,y",
    ]
    assert islandedits.apply_area(Tag("gm", []), {"1,1": 1})["unmatched"] == ["no area grid on this island"]
    assert islandedits.apply_area(Tag("gm", []), {}) == {"cells": 0, "unmatched": []}


def test_the_herd_brush_adds_land_tiles_and_keeps_to_them():
    herd = np.zeros((16, 16), bool)
    free = np.ones((16, 16), bool)
    free[:, 12:] = False  # water or river
    s = EditSession(edit_island(herd_grid=cellbrush.pack(herd, free)))
    assert s.cells("herd") is s.herd and s.herd.cell == 1
    s.herd.begin(True, 2.0, 11.5, 5.5)
    s.herd.end()
    assert s.herd.cells[5, 10:12].all() and not s.herd.cells[5, 12:].any()
    assert s.edits["herd"] and all(int(k.split(",")[0]) < 12 for k in s.edits["herd"])
    s.undo.undo()
    assert not s.edits["herd"] and not s.herd.cells.any()


def test_an_area_edit_can_name_another_area_the_island_has_and_never_makes_one_up():
    from annotoolbox.islands.islandgrid import AREA_ISLAND, read_sparse

    gm = _area_gm()  # column 3 holds 0x4001, the left half the construction area, the rest none
    report = islandedits.apply_area(
        gm, {"3,2": 0x4001, "4,2": 0x4001, "9,2": 0x4001, "5,5": 0x4002, "3,3": 0, "6,6": "x"}
    )
    ids = np.asarray(read_sparse(gm.path("AreaIDs")))
    assert (
        ids[2, 4] == 0x4001 and ids[2, 9] == 0x4001
    )  # a construction tile and a free tile take the island's other area
    assert ids[5, 5] == AREA_ISLAND and ids[3, 3] == 0x4001  # an id the island lacks, and a plain 0 on another area
    assert report["cells"] == 2 and report["unmatched"] == ["area tile 3,3", "area tile 5,5", "area tile 6,6"]
    assert islandedits.apply_area(gm, {"3,2": AREA_ISLAND})["unmatched"] == ["area tile 3,2"]


def test_an_area_edit_for_another_areas_id_does_not_show_as_the_construction_area():
    ids = np.zeros((16, 16), bool)
    s = EditSession(edit_island(area_grid=cellbrush.pack(ids, ids)), {"area": {"2,2": 0x4001, "3,3": 1, "4,4": "x"}})
    assert s.area.cells[3, 3] and not s.area.cells[2, 2] and not s.area.cells[4, 4]


def _kind_island():
    ids = np.array(
        [[0x4001 if x == 3 else 0x6001 if x == 12 else 0 if x == 15 else 0x2001 if x < 8 else 1 for x in range(16)]]
        * 16
    )
    return ids, edit_island(area_grid=cellbrush.pack_area(ids))


def test_the_area_brush_paints_another_areas_id_over_none_construction_and_other_kinds_only():
    ids, island = _kind_island()
    assert island["area_grid"]["kinds"]["ids"] == [0x4001, 0x6001]
    s = EditSession(island)
    assert s.area.kind_ids == [0x4001, 0x6001] and s.area.kind_cells[0, 3] == 1 and s.area.kind_cells[0, 12] == 2
    s.area.begin(0x6001, 1.5, 5.5, 5.5)  # construction tiles at x 4 to 6 take the second kind
    s.area.to(14.5, 5.5)  # none at 13 and 14 as well; 15 is outside every area and stays
    s.area.end()
    assert set(s.edits["area"].values()) == {0x6001} and s.area.kind_cells[5, 5] == 2 and not s.area.cells[5, 5]
    assert s.area.kind_cells[5, 15] == 0 and "15,5" not in s.edits["area"] and s.area.kind_cells[5, 14] == 2
    s.area.begin(0x6001, 1.5, 3.5, 7.5)  # over the first kind
    s.area.end()
    assert s.edits["area"]["3,7"] == 0x6001 and s.area.kind_cells[7, 3] == 2
    s.area.begin(0x4001, 0.4, 3.5, 7.5)  # and back: the island's own id there, so no edit
    s.area.end()
    assert "3,7" not in s.edits["area"] and s.area.kind_cells[7, 3] == 1
    s.area.begin(True, 0.4, 3.5, 2.5)  # construction over a kind of the island: refused
    s.area.end()
    assert "3,2" not in s.edits["area"] and s.area.kind_cells[2, 3] == 1 and not s.area.cells[2, 3]
    s.area.begin(0x8001, 0.4, 6.5, 2.5)  # an id the island lacks paints nothing
    assert s._brush is None and "6,2" not in s.edits["area"]


def test_painting_a_tile_plain_again_takes_the_kind_the_edits_gave_it_back_and_undo_restores_it():
    ids, island = _kind_island()
    s = EditSession(island)
    s.area.begin(0x4001, 0.4, 5.5, 5.5)
    s.area.end()
    assert s.area.kind_cells[5, 5] == 1 and s.edits["area"] == {"5,5": 0x4001}
    s.area.begin(True, 0.4, 5.5, 5.5)  # construction again: the island's own state
    s.area.end()
    assert s.area.kind_cells[5, 5] == 0 and s.area.cells[5, 5] and not s.edits["area"]
    s.undo.undo()
    assert s.area.kind_cells[5, 5] == 1 and not s.area.cells[5, 5] and s.edits["area"] == {"5,5": 0x4001}
    again = EditSession(island, s.edits)  # a saved edit file draws it again
    assert again.area.kind_cells[5, 5] == 1
