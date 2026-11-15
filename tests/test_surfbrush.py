"""The surf brush: the coast-wave byte of the sea map in the editing session, the edit file's ``surf`` key and its build."""

import numpy as np

from annotoolbox.editor import cellbrush
from annotoolbox.editor.desktop import desktopgrids
from annotoolbox.editor.desktop.desktopbrush import BrushTools
from annotoolbox.editor.editsession import EditSession
from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.editor.islandeditor import IslandEditor
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands import islandedits, surfmap
from fakes import edit_island
from test_islandinfo import SOURCE, island_files

BASE = np.full((8, 8), 255, np.uint8)  # the 16-tile island's sea map: a cell is 2 x 2 tiles
BASE[0, :2] = (0, 120)


def session(edits=None, readonly=False) -> EditSession:
    return EditSession(edit_island(surf_map=cellbrush.pack_values(BASE)), edits, readonly)


def stroke(s: EditSession, value: int, x: float, z: float, radius: float = 1.0) -> None:
    s.surf.begin(value, radius, x, z)
    s.surf.end()


def sea_map(cells: np.ndarray) -> Tag:
    n = len(cells)
    info = np.zeros((n, n, 4), np.uint8)
    info[..., 0] = 200  # the depth byte, which the brush leaves alone
    info[..., 1] = cells
    return Tag(
        "WaterInfos",
        [
            Attrib("width", n.to_bytes(4, "little")),
            Attrib("height", n.to_bytes(4, "little")),
            Attrib("map", info.tobytes()),
        ],
    )


def test_the_island_json_round_trips_the_cells_and_a_cell_is_two_tiles():
    s = session()
    assert np.array_equal(s.surf.cells, BASE) and s.surf.per == 2.0 and s.surf.n == 8
    assert EditSession(edit_island()).surf.cells is None  # no sea map: no brush
    assert cellbrush.pack_values(BASE)["n"] == 8


def test_a_stroke_sets_the_cells_under_the_brush_and_keeps_only_the_difference():
    s = session()
    stroke(s, 0, 8.0, 8.0, radius=1.5)  # the centres of the four cells round (8, 8) lie 1.41 tiles off
    assert s.edits["surf"] == {"3,3": 0, "4,3": 0, "3,4": 0, "4,4": 0}
    assert all(v == 0 for v in s.edits["surf"].values())
    stroke(s, 255, 8.0, 8.0, radius=3.0)  # back to the island's own value: the keys go
    assert s.edits["surf"] == {}
    stroke(s, 120, 1.0, 1.0, radius=0.5)  # the cell of (0, 0) is 0 on the island; this is a change
    assert s.edits["surf"] == {"0,0": 120}
    stroke(s, 0, 1.0, 1.0, radius=0.5)  # the island's own again
    assert s.edits["surf"] == {}


def test_a_stroke_is_one_undoable_step_and_the_edit_file_carries_it(tmp_path):
    s = session()
    s.surf.begin(40, 1.5, 8.0, 8.0)
    s.surf.to(8.0, 12.0)
    s.surf.end()
    assert len(s.undo.commands) == 1 and s.surf.cells[4, 4] == 40 and s.surf.cells[6, 4] == 40
    islandedits.save(tmp_path / "e.json", s.edits)
    again = session(islandedits.load(tmp_path / "e.json"))
    assert np.array_equal(again.surf.cells, s.surf.cells)
    assert s.undo.undo() == "surf" and np.array_equal(s.surf.cells, BASE) and not s.edits["surf"]
    s.undo.redo()
    assert s.surf.cells[4, 4] == 40


def test_nothing_is_set_on_a_read_only_island_or_one_without_a_sea_map_and_values_are_kept_in_range():
    locked = session(readonly=True)
    stroke(locked, 0, 8.0, 8.0)
    assert np.array_equal(locked.surf.cells, BASE) and not locked.undo.commands
    plain = EditSession(edit_island())
    stroke(plain, 0, 8.0, 8.0)
    assert not plain.undo.commands
    s = session()
    stroke(s, 900, 8.0, 8.0)  # past the byte: held at full, which is the island's own there
    assert s.edits["surf"] == {}


def test_the_build_writes_the_cells_into_the_sea_maps_wave_byte_only(tmp_path):
    files = island_files()
    isl = a7m_edit117.IslandFile(files.read(SOURCE), files.read(SOURCE[:-4] + ".a7minfo"))
    isl.rd.children.append(sea_map(BASE))
    assert np.array_equal(surfmap.read(isl), BASE)
    assert surfmap.apply(isl, {"3,3": 0, "7,7": 50, "9,9": 1, "1,1": 300}) == {
        "surf cells": 2
    }  # off the map, off the byte
    cells = surfmap.read(isl)
    assert cells[3, 3] == 0 and cells[7, 7] == 50 and cells[1, 1] == BASE[1, 1]
    wi = isl.rd.path("WaterInfos")
    assert (np.frombuffer(wi.find("map").data, np.uint8).reshape(8, 8, 4)[..., 0] == 200).all()
    assert (
        surfmap.apply(isl, None) == {}
        and surfmap.apply(a7m_edit117.IslandFile(files.read(SOURCE), files.read(SOURCE[:-4] + ".a7minfo")), {"0,0": 1})
        == {}
    )


def test_the_islands_build_takes_the_key(tmp_path):
    files = island_files()
    a7m = files.read(SOURCE)
    isl = a7m_edit117.IslandFile(a7m, files.read(SOURCE[:-4] + ".a7minfo"))
    isl.rd.children.append(sea_map(BASE))
    files.files[SOURCE] = isl.write()
    done = a7m_edit117.apply(files, SOURCE, {**islandedits.empty(), "surf": {"2,2": 7}}, template=None)
    assert done.report["surf cells"] == 1
    back = a7m_edit117.IslandFile(done.a7m, files.read(SOURCE[:-4] + ".a7minfo"))
    assert surfmap.read(back)[2, 2] == 7


def test_the_window_brush_paints_waves_or_calm_by_the_tool_and_the_overlay_shows_the_weak_and_changed_cells():
    s = session()
    sent = []
    view = type(
        "V",
        (),
        {
            "session": s,
            "tool": "surf",
            "pick": None,
            "paint_material": None,
            "status": type("S", (), {"emit": sent.append})(),
            "brush": {"mode": "raise", "radius": 2, "strength": 0.5, "plant": False},
            "from_view": lambda self, v: [s.size - v[0], v[1], v[2]],
            "update": lambda self: None,
        },
    )()
    tools = BrushTools(view)
    assert tools.start((8.0, 0.0, 8.0))
    tools.end()
    assert s.edits["surf"] and all(v == 0 for v in s.edits["surf"].values())  # Calm
    view.brush["plant"] = True
    assert tools.start((8.0, 0.0, 8.0))
    tools.end()
    assert s.surf.cells[4, 4] == 128  # Waves at half strength
    (points,) = desktopgrids.surf_overlay(view)
    assert points[0] == "points" and points[1].shape[1] == 7 and len(points[1]) >= 3  # two island cells and the changes
    s.surf.cells = None
    assert desktopgrids.surf_overlay(view) == []


def test_an_island_editor_gives_its_sea_map_for_the_json():
    editor = IslandEditor.__new__(IslandEditor)
    editor.view = type("View", (), {"rd": Tag("rd", [sea_map(BASE)])})()
    assert editor.surf_map() == {"surf_map": cellbrush.pack_values(BASE)}
    editor.view = type("View", (), {"rd": Tag("rd", [])})()
    assert editor.surf_map() == {}
