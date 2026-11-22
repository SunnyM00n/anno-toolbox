"""The object brush: a stroke sprays a model over the ground as one undoable change of added objects."""

import math

import pytest
from tests.editor.editorfakes import edit_island as island

from annotoolbox.editor.editsession import CEditSession
from annotoolbox.editor.objectbrush import CObjectBrush
from annotoolbox.islands import islandedits

PICK = {"cfg": "data/base/tree_pine.cfg"}


def spray(s: CEditSession, points, **kw) -> CObjectBrush:
    kw = {"radius_tiles": 4.0, "strength": 1.0, "spacing_tiles": 1.5, "seed": 5, **kw}
    brush = CObjectBrush(s, kw.pop("pick", PICK), **kw)
    brush.begin(*points[0])
    for p in points[1:]:
        brush.to(*p)
    brush.end()
    return brush


def test_a_stroke_places_models_on_land_within_the_brush_and_apart():
    s = CEditSession(island())
    brush = spray(s, [(8.0, 8.0)])
    rows = s.m_edits["added_objects"]
    assert len(rows) == len(brush.m_placed) > 3
    assert all(r["cfg"] == PICK["cfg"] for r in rows)
    pts = [(r["position"][0], r["position"][2]) for r in rows]
    assert all(math.hypot(x - 8, z - 8) <= 4.0 + 1e-6 for x, z in pts)
    assert all(r["position"][1] == pytest.approx(0.7) for r in rows)  # on the ground
    assert min(math.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(pts) for b in pts[i + 1 :]) >= 1.5


def test_the_turn_and_size_are_random_and_a_seed_repeats_them():
    a, b = CEditSession(island()), CEditSession(island())
    spray(a, [(8.0, 8.0)])
    spray(b, [(8.0, 8.0)])
    assert a.m_edits["added_objects"] == b.m_edits["added_objects"]
    turns = {tuple(r["rotation"]) for r in a.m_edits["added_objects"]}
    sizes = {r["scale"] for r in a.m_edits["added_objects"]}
    assert len(turns) > 2 and len(sizes) > 2 and all(0.84 <= v <= 1.16 for v in sizes)
    fixed = CEditSession(island())
    spray(fixed, [(8.0, 8.0)], random_turn=False, random_scale=False)
    assert {tuple(r["rotation"]) for r in fixed.m_edits["added_objects"]} == {(0.0, -0.0, 0.0, 1.0)}
    assert {r["scale"] for r in fixed.m_edits["added_objects"]} == {1}


def test_a_drag_keeps_the_spacing_over_the_whole_stroke_and_no_object_lands_in_the_sea():
    s = CEditSession(island())
    spray(s, [(2.0, 8.0), (8.0, 8.0), (14.0, 8.0)], radius_tiles=2.5)
    pts = [(r["position"][0], r["position"][2]) for r in s.m_edits["added_objects"]]
    assert len(pts) > 8
    assert min(math.hypot(a[0] - b[0], a[1] - b[1]) for i, a in enumerate(pts) for b in pts[i + 1 :]) >= 1.5
    assert all(2.7 <= x <= 13.3 for x, _ in pts)  # the land runs from tile 3 to 13: the sea beyond stays bare


def test_the_whole_stroke_is_one_step_of_the_undo_stack_that_redoes_too():
    s = CEditSession(island())
    brush = spray(s, [(6.0, 8.0), (10.0, 8.0)])
    assert len(s.m_undo.m_commands) == 1 and s.m_undo.m_commands[0].label == "spray"
    assert s.m_undo.undo() == "spray" and not s.m_edits["added_objects"]
    assert not any(e.shown for e in brush.m_placed)
    s.m_undo.redo()
    assert len(s.m_edits["added_objects"]) == len(brush.m_placed) and all(e.shown for e in brush.m_placed)


def test_the_edit_file_carries_the_sprayed_objects_back(tmp_path):
    s = CEditSession(island())
    spray(s, [(8.0, 8.0)])
    islandedits.save(tmp_path / "e.json", s.m_edits)
    again = CEditSession(island(), islandedits.load(tmp_path / "e.json"))
    assert [e.position for e in again.m_entries if e.cfg == PICK["cfg"]] == [
        r["position"] for r in s.m_edits["added_objects"]
    ]


def test_an_editor_only_asset_sprays_as_a_marker_and_a_tree_or_a_read_only_island_sprays_nothing():
    s = CEditSession(island())
    herd = {"guid": 1000, "template": "Herd", "name": "Herd Sheep"}
    spray(s, [(8.0, 8.0)], pick=herd, radius_tiles=3.0, spacing_tiles=2.5)
    assert s.m_edits["added_objects"] and all(
        r["guid"] == 1000 and "scale" not in r for r in s.m_edits["added_objects"]
    )
    fresh = CEditSession(island())
    assert not CObjectBrush(fresh, {"tree": 0}, 3.0).usable and not CObjectBrush(fresh, {}, 3.0).usable
    assert CObjectBrush(fresh, PICK, 3.0).begin(8.0, 8.0) > 0
    locked = CEditSession(island(), is_readonly=True)
    assert CObjectBrush(locked, PICK, 3.0).begin(8.0, 8.0) == 0 and not locked.m_edits["added_objects"]
