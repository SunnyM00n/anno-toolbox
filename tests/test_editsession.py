"""The PC editor's edit document: tools, undo and redo, and the edit file it writes (islandedits format)."""

import json
import math

import numpy as np
import pytest

from annotoolbox.editor import editlists, editsession, selection
from annotoolbox.editor.editsession import EditSession
from annotoolbox.islands import islandedits
from annotoolbox.suite.undo import Command, UndoStack
from fakes import EDIT_SIZE as SIZE
from fakes import edit_island as island
from fakes import edit_object as _obj


class Editor:
    """The editor's save, as ``IslandEditor.save_edits`` does it."""

    def __init__(self, path, meta=None):
        self.edits_path, self.edit_meta = path, meta or {}

    def save_edits(self, edits):
        islandedits.save(self.edits_path, {**edits, **self.edit_meta})


def entry(s: EditSession, cfg: str, x: float):
    return next(e for e in s.entries if e.cfg == cfg and e.position[0] == x)


def test_a_move_is_one_change_that_undoes_and_redoes():
    s = EditSession(island())
    rock = entry(s, "rock.cfg", 5.5)
    start = s.begin_transform([rock])
    for x in (6.0, 7.0, 8.25):  # a drag's steps
        s.set_transform(rock, position=[x, 0.7, 6.5])
    assert s.end_transform(start, "move")
    assert len(s.undo.commands) == 1 and s.undo.dirty
    rec = s.edits["objects"][rock.key]
    assert rec["position"] == [8.25, 0.7, 6.5] and rec["rotation"] == [0.0, 0.0, 0.0, 1.0] and rec["scale"] == 1.0
    assert s.undo.undo() == "move"
    assert rock.key not in s.edits["objects"] and rock.position == [5.5, 0.7, 6.5]
    assert s.undo.redo() == "move"
    assert s.edits["objects"][rock.key]["position"] == [8.25, 0.7, 6.5]
    assert not s.end_transform(s.begin_transform([rock]))  # nothing moved: no change recorded


def test_a_turn_keeps_its_tilt_and_a_prop_scales_per_axis():
    s = EditSession(island())
    rock = entry(s, "rock.cfg", 9.0)
    tilt = [math.sin(0.2), 0.0, 0.0, math.cos(0.2)]
    s.transform(rock, "rotation", rotation=tilt)
    s.transform(rock, "move", position=[9.5, 0.7, 9.0])  # a move keeps the whole quaternion
    assert s.edits["objects"][rock.key]["rotation"] == pytest.approx(tilt)
    fall = entry(s, "fall.cfg", 10.0)
    s.transform(fall, "scale", scale=[1.0, 1.75, 1.0])
    assert s.edits["props"][fall.key]["scale"] == [1.0, 1.75, 1.0]


def test_an_object_scaled_per_axis_keeps_its_three_scales_through_a_move_and_a_copy():
    """An 1800 object of the Prop group (a fence) stores a scale per axis; a move must not squash it to one."""
    fence = {**_obj("fence.cfg", [3.0, 1.0, 3.0]), "scale": [1.0, 2.0, 4.0], "clone": True, "template": "Prop"}
    s = EditSession(island(objects=[fence, _obj("rock.cfg", [5.5, 0.7, 6.5])]))
    e = entry(s, "fence.cfg", 3.0)
    s.transform(e, "move", position=[3.5, 1.0, 3.0])
    assert s.edits["objects"][e.key]["scale"] == [1.0, 2.0, 4.0]
    s.duplicate(e)
    assert s.edits["added_objects"][0]["scale"] == [1.0, 2.0, 4.0]
    rock = entry(s, "rock.cfg", 5.5)  # a plain object keeps its one number
    s.transform(rock, "move", position=[6.0, 0.7, 6.5])
    assert s.edits["objects"][rock.key]["scale"] == 1.0


def test_a_height_edit_off_the_island_is_left_out_as_the_build_leaves_it():
    s = EditSession(island(), {"heights": {"-1,3": 2.0, "3,99": 1.0, "2,2": 0.5}})
    assert not s.D[-1].any() and s.D[2, 2] == 0.5  # "-1,3" would have wrapped to the last row


def test_delete_marks_the_islands_own_and_removes_an_added_one():
    s = EditSession(island())
    rock = entry(s, "rock.cfg", 5.5)
    s.delete(rock)
    assert s.edits["objects"][rock.key] == {"deleted": True} and not rock.visible
    s.undo.undo()
    assert rock.key not in s.edits["objects"] and rock.visible
    added = s.place({"cfg": "crate.cfg", "source": "data/graphics/crate.cfg"}, [3.0, 0.7, 3.0], math.pi / 2)
    assert s.edits["added_objects"] == [
        {"cfg": "crate.cfg", "position": [3.0, 0.7, 3.0], "rotation": pytest.approx(editsession.yaw_quat(math.pi / 2)),
         "scale": 1, "source": "data/graphics/crate.cfg"}
    ]  # fmt: skip
    s.delete(added)
    assert s.edits["added_objects"] == [] and not added.alive
    s.undo.undo()
    assert len(s.edits["added_objects"]) == 1 and added.alive
    s.undo.undo()  # the placing itself
    assert s.edits["added_objects"] == [] and not added.alive


def test_editor_only_assets_and_duplicates_are_recorded_as_the_page_did():
    s = EditSession(island())
    herd = s.place({"guid": 1003227, "template": "Herd", "name": "Herd Sheep"}, [8.0, 0.7, 8.0])
    assert herd.marker == "Herd" and herd.layer == "markers"
    assert set(s.edits["added_objects"][0]) == {"guid", "template", "name", "position", "rotation"}
    rock = entry(s, "rock.cfg", 9.0)
    copy = s.duplicate(rock)
    assert s.edits["added_objects"][1] == {"cfg": "rock.cfg", "position": [11.0, 0.7, 11.0],
                                           "rotation": [0.0, 0.0, 0.0, 1.0], "scale": 1.0}  # fmt: skip
    assert copy.position == [11.0, 0.7, 11.0]


def _placement(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in ("position", "scale")}


@pytest.mark.parametrize(
    "pick",
    [
        {"cfg": "tree.cfg", "source": "data/t.xml", "asset": 123, "variation": 2},  # a 2070 library pick
        {"guid": 2955, "template": "Fish", "name": "Fish", "cfg": "f.cfg", "clone": True, "plain": True},
        {"guid": 7, "template": "Hut", "name": "Hut", "clone": True, "variation": 1},  # an island clone
        {"guid": 1003227, "template": "Herd", "name": "Herd Sheep"},  # an editor-only marker
    ],
)
def test_a_duplicate_and_a_paste_keep_everything_the_original_was_placed_with(pick):
    s = EditSession(island())
    e = s.place(pick, [8.0, 0.7, 8.0])
    record = s.edits["added_objects"][0]
    for copy_of in (s.duplicate(e), selection.paste(s, selection.copy([e]), [8.0, 0.7, 8.0])[0]):
        made = s.edits["added_objects"][-1]
        assert _placement(made) == _placement(record)
        s.delete(copy_of)


def test_the_height_brush_writes_corner_differences_and_undoes_whole():
    s = EditSession(island())
    before = s.heights.copy()
    s.begin_height("raise", 2, 1.0, 8.0, 8.0)
    s.height_to(8.5, 8.0)
    s.end_height()
    h = s.edits["heights"]
    assert h["8,8"] > 0 and all(isinstance(k, str) and k.count(",") == 1 for k in h)
    assert all(round(v, 3) == v for v in h.values())  # to 1 mm, as the page rounded
    assert s.heights[16, 16] == pytest.approx(0.7 + h["8,8"], abs=1e-3)  # corner (8, 8) is sample (16, 16)
    s.undo.undo()
    assert s.edits["heights"] == {} and np.allclose(s.heights, before)
    s.undo.redo()
    assert s.edits["heights"] == h
    s.begin_height("flatten", 3, 1.0, 8.0, 8.0)  # towards the height where the stroke began
    for _ in range(30):
        s.height_to(9.5, 8.0)
    s.end_height()
    assert abs(s.corner_heights()[8, 10] - s.corner_heights()[8, 8]) < 0.05


def test_paint_strokes_are_recorded_and_previewed_in_the_weights():
    s = EditSession(island())
    weights = np.zeros((2, 64, 64), np.uint8)
    weights[0] = 255  # material 3 everywhere
    s.attach_ground([3, 7], weights, [{"id": 3, "repetition": 4}, {"id": 7, "repetition": 4}])
    s.begin_paint(7, 2, 0.5, 8.0, 8.0)
    s.paint_to(8.2, 8.0)  # closer than 0.3 radius: no point
    s.paint_to(10.0, 8.0)
    s.end_paint()
    (stroke,) = s.edits["paint"]
    assert stroke == {"material": 7, "radius": 2, "strength": 0.5, "points": [[8, 8], [10, 8]]}
    assert s.weights[1, 32, 32] > 100 and int(s.weights[0, 32, 32]) + int(s.weights[1, 32, 32]) == 255
    s.undo.undo()
    assert s.edits["paint"] == [] and s.weights[1].max() == 0
    again = EditSession(island(), {"paint": [stroke]})  # the saved stroke is laid over the weights again
    again.attach_ground([3, 7], weights.copy(), [{"id": 3}, {"id": 7}])
    assert again.weights[1, 32, 32] > 100


def test_a_saved_stroke_of_a_material_the_palette_has_not_registered_yet_is_still_previewed():
    """The ground and the palette load side by side: a stroke of a 117 material added in an earlier session must
    not depend on which arrives first."""
    stroke = {"material": 30, "radius": 2, "strength": 1.0, "points": [[8, 8]]}
    s = EditSession(island(), {"paint": [stroke], "materials": {"30": {"file": "g.cfg", "set": "s.xml"}}})
    weights = np.zeros((1, 64, 64), np.uint8)
    weights[0] = 255
    s.attach_ground([3], weights, [{"id": 3, "repetition": 4}])  # material 30 not registered: the ground came first
    assert s.ground_ids == [3, 30] and s.weights[1, 32, 32] > 200
    seen = []
    s.listeners.append(seen.append)
    s.set_own_materials([{"id": 3, "name": "grass", "order": 0}])  # the palette arrives: the view is told
    assert seen and seen[-1].materials


def test_a_117_material_takes_a_free_id_of_the_islands_set():
    s = EditSession(island())
    m = {"file": "data/base/terrain/materials/roman_grass.cfg", "set": "roman_01", "diffuse": "g.psd"}
    mid = s.register_vanilla_material(m, set_ids=range(30))
    assert mid == 30 and s.edits["materials"] == {"30": {"file": m["file"], "set": "roman_01"}}
    assert s.register_vanilla_material(m, set_ids=range(30)) == 30  # once only
    s.undo.undo()
    assert s.edits["materials"] == {}


def test_a_117_material_is_drawn_where_its_family_stands_in_the_islands_set():
    from annotoolbox.islands import materialorder

    s = EditSession(island())
    own = [{"id": 0, "name": "a2070_n_water", "order": 0}, {"id": 1, "name": "a2070_n_mountain_00", "order": 1},
           {"id": 2, "name": "a2070_n_gras_01", "order": 2}]  # fmt: skip
    s.set_own_materials(own)
    grass = {"file": "data/base/m/grass_dry.cfg", "set": "data/base/s.xml", "name": "grass_dry", "rank": 4}
    mid = s.register_vanilla_material(grass, set_ids=[0, 1, 2])
    order = sorted(s.paint_materials, key=lambda k: s.paint_materials[k]["order"])
    assert order == [0, mid, 1, 2]  # under the island's rock, over its water: its family's place
    assert materialorder.family("grass_dry") == "grass" and materialorder.family("u_lava_01") == "rock"


def test_the_forest_brush_plants_and_clears_tiles_against_the_islands_own():
    s = EditSession(island())
    assert sum(e.visible for e in s.trees.values()) == 3
    s.begin_forest(True, 1.5, 10.5, 10.5)
    s.end_forest()
    planted = {k for k, v in s.edits["forest"].items() if v == 1}
    assert planted and all(s.trees[k].visible for k in planted)
    assert s.trees["10,10"].cfg in ("tree_a.cfg", "tree_b.cfg")
    s.begin_forest(False, 1.0, 6.5, 5.5)  # clearing the island's own trees writes 0
    s.end_forest()
    assert s.edits["forest"]["6,5"] == 0 and not s.trees["6,5"].visible
    s.undo.undo()
    assert "6,5" not in s.edits["forest"] and s.trees["6,5"].visible
    s.begin_forest(True, 1.0, 1.5, 1.5)  # the sea: no tree
    s.end_forest()
    assert "1,1" not in s.edits["forest"]
    assert s.place({"tree": 0}, [1.5, -2.0, 1.5]) is None and "1,1" not in s.edits["forest"]  # a click neither
    assert s.place({"tree": 0}, [12.5, 0.7, 12.5]) is not None and s.edits["forest"]["12,12"] == 1


def test_clearing_forest_on_a_2070_island_deletes_its_tree_objects():
    tree = {**_obj("palm.cfg", [5.5, 0.7, 5.5]), "key": "2070:12", "template": "Tree"}
    s = EditSession(island(mode="2070", objects=[tree], forest={"tiles": [], "models": ["palm.cfg"]}))
    palm = s.entries[0]
    s.begin_forest(False, 2.0, 5.5, 5.5)
    s.end_forest()
    assert s.edits["objects"]["2070:12"] == {"deleted": True} and not palm.visible
    s.undo.undo()
    assert "2070:12" not in s.edits["objects"] and palm.visible


def test_rivers_and_lakes_edit_whole_rivers_and_levels():
    s = EditSession(island())
    before = s.water_snapshot()
    s.move_river_point(0, 1, [8.5, 0.45, 8.5])
    s.set_river_width(0, 1, 2.5)
    s.end_water(before, "water")
    assert s.edits["rivers"]["0"] == {
        "points": [[4.0, 0.5, 4.0], [8.5, 0.45, 8.5], [12.0, 0.3, 8.0]],
        "widths": [2.0, 2.5, 3.0],
    }
    before = s.water_snapshot()
    s.set_lake_level(0, 1.2345)
    s.end_water(before, "lake level")
    assert s.edits["lakes"] == {"0": 1.23}
    assert s.insert_river_point(0, 0) == 1 and len(s.river(0)["points"]) == 4
    assert s.delete_river_point(0, 1) and len(s.river(0)["points"]) == 3
    s.undo.undo()
    s.undo.undo()
    s.undo.undo()
    assert s.edits["lakes"] == {}
    assert islandedits.water(s.edits)["rivers"]["0"]["widths"] == [2.0, 2.5, 3.0]


def test_the_edit_file_round_trips_through_a_save(tmp_path):
    s = EditSession(island())
    rock = entry(s, "rock.cfg", 5.5)
    s.transform(rock, "move", position=[6.0, 0.7, 6.5])
    s.delete(entry(s, "grass.cfg", 11.0))
    s.place({"cfg": "crate.cfg"}, [3.0, 0.7, 3.0])
    s.begin_height("raise", 2, 0.5, 8.0, 8.0)
    s.end_height()
    s.begin_forest(True, 1.0, 10.5, 10.5)
    s.end_forest()
    s.set_target("new")
    path = tmp_path / "island-edits" / "117_isle.json"
    s.save(Editor(path, {"source": "data/x/isle.a7m"}))
    assert not s.undo.dirty
    saved = islandedits.load(path)
    assert saved["source"] == "data/x/isle.a7m" and saved["target"] == "new" and saved["version"] == 1
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(json.dumps(saved, sort_keys=True))
    again = EditSession(island(), saved)  # opened again: the edits are where they were left
    assert entry(again, "rock.cfg", 6.0).position == [6.0, 0.7, 6.5]
    assert not entry(again, "grass.cfg", 11.0).visible
    assert any(e.cfg == "crate.cfg" and e.added is not None for e in again.entries)
    assert np.allclose(again.heights, s.heights, atol=1e-3)  # saved to 1 mm
    assert again.forest_tiles() == s.forest_tiles()


def test_the_undo_stack_drops_the_oldest_and_knows_the_saved_point():
    stack, log = UndoStack(limit=3), []
    for k in range(5):
        stack.push(Command(str(k), lambda k=k: log.append(f"-{k}"), lambda k=k: log.append(f"+{k}")))
    assert stack.labels() == ["2", "3", "4"]
    stack.mark_clean()
    assert not stack.dirty
    assert stack.undo() == "4" and stack.dirty
    stack.redo()
    assert not stack.dirty
    while stack.undo():
        pass
    assert log == ["-4", "+4", "-4", "-3", "-2"] and stack.undo() is None
    stack.push(Command("new", lambda: None, lambda: None))  # the redo branch is gone
    assert stack.labels() == ["new"] and stack.redo() is None and stack.dirty


def test_a_readonly_island_changes_nothing():
    s = EditSession(island(), readonly=True)
    rock = entry(s, "rock.cfg", 5.5)
    s.transform(rock, position=[1, 1, 1])
    s.delete(rock)
    s.begin_height("raise", 2, 1, 8, 8)
    s.end_height()
    assert s.edits == islandedits.empty() and not s.undo.commands


def test_hiding_groups_and_the_libraries_listing():
    s = EditSession(island())
    hidden = editlists.Hidden()
    hidden.groups.add("rock.cfg")
    changed = hidden.apply(s.entries)
    assert {e.cfg for e in changed} == {"rock.cfg"} and all(e.hidden for e in changed)
    hidden.foliage = True
    assert any(e.kind == "tree" for e in hidden.apply(s.entries))
    groups = editlists.object_groups(s.entries)
    assert sum(len(es) for models in groups.values() for es in models.values()) == len(s.entries)
    lib = {"117": [{"name": "crate", "cfg": "data/base/graphics/library/props/crates/crate.cfg", "group": "props"}]}
    out = editlists.library_items(lib, "117", "", ["tree_a.cfg"])
    assert list(out) == ["forest of this island", "props"] and out["forest of this island"][0]["tree"] == 0
    assert list(editlists.library_items(lib, "117", "crate")) == ["props"]


def test_a_lake_outline_point_moves_over_the_ground_and_the_move_is_one_undo_step():
    s = EditSession(island())
    assert s.lake_outline(0) == [[9.0, 9.0], [11.0, 9.0], [11.0, 11.0]]
    before = s.water_snapshot()
    s.move_lake_outline_point(0, 1, [12.5, 1.0, 9.5])
    s.move_lake_outline_point(0, 1, [12.0, 1.0, 9.0])  # a drag: each step replaces the last
    s.end_water(before, "water")
    assert s.edits["lake_outlines"] == {"0": {"points": [[9.0, 9.0], [12.0, 9.0], [11.0, 11.0]]}}
    assert len(s.undo.commands) == 1
    s.undo.undo()
    assert s.edits["lake_outlines"] == {} and s.lake_outline(0)[1] == [11.0, 9.0]
    s.undo.redo()
    assert s.lake_outline(0)[1] == [12.0, 9.0] and s.water_snapshot()["lake_outlines"]["0"]["points"][1] == [12.0, 9.0]


def test_the_water_tool_offers_the_outline_points_of_a_new_engine_islands_lakes_only():
    from annotoolbox.editor.desktop.desktopoverlays import OverlayDraw

    def view(session):
        return type("V", (OverlayDraw,), {"session": session, "to_view": lambda self, p: np.asarray(p, float)})()

    s = EditSession(island())
    handles = [h for h, _p in view(s).water_handles()]
    assert {"outline": 0, "point": 2} in handles and {"lake": 0} in handles
    s.anno2070_editing = True  # 2070's own lake file keeps levels only
    assert not [h for h, _p in view(s).water_handles() if "outline" in h]


def test_dragging_an_outline_handle_moves_that_point():
    from annotoolbox.editor.desktop.desktopgizmo import GizmoControls

    s, sent = EditSession(island()), []
    stub = type(
        "G",
        (),
        {"session": s, "water_sel": {"outline": 0, "point": 0}, "waterChanged": type("S", (), {"emit": sent.append})()},
    )()
    GizmoControls._apply_water_drag(stub, [9.5, 1.0, 8.5])
    assert s.lake_outline(0)[0] == [9.5, 8.5] and sent == [{"outline": 0, "point": 0}]


def test_the_slot_rows_count_the_slots_standing_after_the_edits_against_those_the_island_lists():
    s = EditSession(island(slot_list={"99": 3, "7": 2}))
    rows = {r["guid"]: r for r in s.slot_rows()}
    assert (rows[99]["listed"], rows[99]["now"], rows[99]["name"]) == (3, 1, "Herd Sheep")
    assert (rows[7]["listed"], rows[7]["now"]) == (2, 0)  # an asset none of the island's objects uses
    s.delete(entry(s, "", 7.0))
    assert {r["guid"]: r["now"] for r in s.slot_rows()} == {99: 0, 7: 0}
    s.undo.undo()
    assert {r["guid"]: r["now"] for r in s.slot_rows()}[99] == 1


def test_a_transform_that_changes_nothing_leaves_no_record_in_the_edit_file():
    s = EditSession(island())
    rock = entry(s, "rock.cfg", 5.5)
    s.transform(rock, "move", position=list(rock.position))
    start = s.begin_transform([rock])
    s.set_transform(rock, position=[6.0, 0.7, 6.5])
    s.set_transform(rock, position=[5.5, 0.7, 6.5])  # dragged away and back
    assert not s.end_transform(start, "move")
    assert not s.undo.commands and rock.key not in s.edits["objects"] and not s.undo.dirty
