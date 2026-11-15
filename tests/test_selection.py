"""Several objects at once: the selection's group operations as one change, and copy and paste."""

import json
import math

import numpy as np
import pytest

from annotoolbox.editor import selection
from annotoolbox.editor.editsession import EditSession
from fakes import edit_island


def rocks(s: EditSession):
    return [e for e in s.entries if e.cfg == "rock.cfg"]


def test_a_box_selects_what_lies_in_it_and_same_model_the_rest_of_its_kind():
    s = EditSession(edit_island())
    sel = selection.Selection(s)
    assert sel.box(5, 6, 6, 7) == 1 and sel.primary.position == [5.5, 0.7, 6.5]
    assert sel.box(0, 0, 16, 16, layers=["props"]) == 2
    assert sel.same_model(rocks(s)[0]) == 2
    sel.toggle(rocks(s)[0])
    assert len(sel) == 1


def test_a_group_move_is_one_step_of_the_undo_stack():
    s = EditSession(edit_island())
    sel = selection.Selection(s)
    sel.set(rocks(s))
    assert sel.move([1, 0, 2])
    assert [e.position for e in rocks(s)] == [[6.5, 0.7, 8.5], [10.0, 0.7, 11.0]]
    assert s.undo.labels()[-1] == "move 2"
    s.undo.undo()
    assert [e.position for e in rocks(s)] == [[5.5, 0.7, 6.5], [9.0, 0.7, 9.0]]


def test_a_group_turn_turns_the_places_round_the_centre_and_each_object_with_them():
    s = EditSession(edit_island())
    sel = selection.Selection(s)
    sel.set(rocks(s))
    centre = sel.centre()
    sel.turn(math.pi)
    a, b = rocks(s)
    assert np.allclose(np.add(a.position, b.position) / 2, centre)
    assert np.allclose(a.position, [9.0, 0.7, 9.0]) and np.allclose(b.position, [5.5, 0.7, 6.5])
    assert np.allclose(np.abs(a.rotation), [0, 1, 0, 0], atol=1e-9)


def test_a_group_delete_and_duplicate_are_one_step_each():
    s = EditSession(edit_island())
    sel = selection.Selection(s)
    sel.set(rocks(s))
    copies = sel.duplicate()
    assert len(copies) == 2 and sel.entries == copies and s.undo.labels()[-1] == "duplicate 2"
    assert sel.delete() == 2 and s.undo.labels()[-1] == "delete 2"
    assert not s.edits["added_objects"]
    s.undo.undo()
    assert len(s.edits["added_objects"]) == 2
    s.undo.undo()
    assert not s.edits["added_objects"]


def test_copy_and_paste_keep_the_layout_through_json_and_onto_another_island():
    s = EditSession(edit_island())
    herd = next(e for e in s.entries if e.marker == "Herd")
    fall = next(e for e in s.entries if e.cfg == "fall.cfg")
    clip = json.loads(json.dumps(selection.copy([*rocks(s), herd, fall])))
    other = EditSession(edit_island())
    new = selection.paste(other, clip, at=[8.0, 0.7, 8.0], on_ground=True)
    assert len(new) == 4 and other.undo.labels() == ["paste 4"]
    assert {e.kind for e in new} == {"object", "prop"}
    assert next(e for e in new if e.marker == "Herd").added["guid"] == 99
    offsets = np.array([e.position for e in new]) - [8.0, 0.0, 8.0]
    assert np.allclose(offsets[:, [0, 2]], np.array([i["offset"] for i in clip["items"]])[:, [0, 2]])
    other.undo.undo()
    assert not other.edits["added_objects"] and not other.edits["added_props"]


def test_one_change_merges_what_happens_inside_it():
    s = EditSession(edit_island())
    with selection.one_change(s, "both"):
        s.transform(rocks(s)[0], position=[1.0, 0.7, 1.0])
        s.transform(rocks(s)[1], position=[2.0, 0.7, 2.0])
    assert s.undo.labels() == ["both"]
    s.undo.undo()
    assert rocks(s)[0].position == [5.5, 0.7, 6.5] and rocks(s)[1].position == [9.0, 0.7, 9.0]
    s.undo.redo()
    assert rocks(s)[1].position == [2.0, 0.7, 2.0]


@pytest.mark.parametrize("readonly", [True])
def test_a_readonly_island_takes_no_group_change(readonly):
    s = EditSession(edit_island(), readonly=readonly)
    sel = selection.Selection(s)
    sel.set(rocks(s))
    assert not sel.move([1, 0, 0]) and not selection.paste(s, selection.copy(rocks(s)), [0, 0, 0])
