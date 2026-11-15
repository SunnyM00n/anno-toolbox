"""The object brush: a stroke sprays a model over the ground as one undoable change of added objects."""

import math

import pytest

from annotoolbox.editor.editsession import EditSession
from annotoolbox.editor.objectbrush import ObjectBrush
from annotoolbox.islands import islandedits
from fakes import edit_island as island

PICK = {"cfg": "data/base/tree_pine.cfg"}


def spray(s: EditSession, points, **kw) -> ObjectBrush:
    kw = {"radius": 4.0, "strength": 1.0, "spacing": 1.5, "seed": 5, **kw}
    brush = ObjectBrush(s, kw.pop("pick", PICK), **kw)
    brush.begin(*points[0])
    for p in points[1:]:
        brush.to(*p)
    brush.end()
    return brush


def test_a_stroke_places_models_on_land_within_the_brush_and_apart():
    s = EditSession(island())
    brush = spray(s, [(8.0, 8.0)])
    rows = s.edits["added_objects"]
    assert len(rows) == len(brush.placed) > 3
    assert all(r["cfg"] == PICK["cfg"] for r in rows)
    pts = [(r["position"][0], r["position"][2]) for r in rows]
    assert all(math.hypot(x - 8, z - 8) <= 4.0 + 1e-6 for x, z in pts)
    assert all(r["position"][1] == pytest.approx(0.7) for r in rows)  # on the ground
    assert min(math.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(pts) for b in pts[i + 1 :]) >= 1.5


def test_the_turn_and_size_are_random_and_a_seed_repeats_them():
    a, b = EditSession(island()), EditSession(island())
    spray(a, [(8.0, 8.0)])
    spray(b, [(8.0, 8.0)])
    assert a.edits["added_objects"] == b.edits["added_objects"]
    turns = {tuple(r["rotation"]) for r in a.edits["added_objects"]}
    sizes = {r["scale"] for r in a.edits["added_objects"]}
    assert len(turns) > 2 and len(sizes) > 2 and all(0.84 <= v <= 1.16 for v in sizes)
    fixed = EditSession(island())
    spray(fixed, [(8.0, 8.0)], random_turn=False, random_scale=False)
    assert {tuple(r["rotation"]) for r in fixed.edits["added_objects"]} == {(0.0, -0.0, 0.0, 1.0)}
    assert {r["scale"] for r in fixed.edits["added_objects"]} == {1}


def test_a_drag_keeps_the_spacing_over_the_whole_stroke_and_no_object_lands_in_the_sea():
    s = EditSession(island())
    spray(s, [(2.0, 8.0), (8.0, 8.0), (14.0, 8.0)], radius=2.5)
    pts = [(r["position"][0], r["position"][2]) for r in s.edits["added_objects"]]
    assert len(pts) > 8
    assert min(math.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(pts) for b in pts[i + 1 :]) >= 1.5
    assert all(2.7 <= x <= 13.3 for x, _ in pts)  # the land runs from tile 3 to 13: the sea beyond stays bare


def test_the_whole_stroke_is_one_step_of_the_undo_stack_that_redoes_too():
    s = EditSession(island())
    brush = spray(s, [(6.0, 8.0), (10.0, 8.0)])
    assert len(s.undo.commands) == 1 and s.undo.commands[0].label == "spray"
    assert s.undo.undo() == "spray" and not s.edits["added_objects"]
    assert not any(e.shown for e in brush.placed)
    s.undo.redo()
    assert len(s.edits["added_objects"]) == len(brush.placed) and all(e.shown for e in brush.placed)


def test_the_edit_file_carries_the_sprayed_objects_back(tmp_path):
    s = EditSession(island())
    spray(s, [(8.0, 8.0)])
    islandedits.save(tmp_path / "e.json", s.edits)
    again = EditSession(island(), islandedits.load(tmp_path / "e.json"))
    assert [e.position for e in again.entries if e.cfg == PICK["cfg"]] == [
        r["position"] for r in s.edits["added_objects"]
    ]


def test_an_editor_only_asset_sprays_as_a_marker_and_a_tree_or_a_read_only_island_sprays_nothing():
    s = EditSession(island())
    herd = {"guid": 1000, "template": "Herd", "name": "Herd Sheep"}
    spray(s, [(8.0, 8.0)], pick=herd, radius=3.0, spacing=2.5)
    assert s.edits["added_objects"] and all(r["guid"] == 1000 and "scale" not in r for r in s.edits["added_objects"])
    fresh = EditSession(island())
    assert not ObjectBrush(fresh, {"tree": 0}, 3.0).usable and not ObjectBrush(fresh, {}, 3.0).usable
    assert ObjectBrush(fresh, PICK, 3.0).begin(8.0, 8.0) > 0
    locked = EditSession(island(), readonly=True)
    assert ObjectBrush(locked, PICK, 3.0).begin(8.0, 8.0) == 0 and not locked.edits["added_objects"]
