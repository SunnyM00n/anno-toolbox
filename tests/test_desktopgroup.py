"""The 3D view's group tools without a window: Shift+click, a gizmo move carrying the rest, Del, copy and paste,
snapping, layers hidden in the view."""

import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop.desktopgroup import GroupTools
from annotoolbox.editor.editlists import Hidden
from annotoolbox.editor.editsession import EditSession
from fakes import edit_island


class Signal:
    def __init__(self):
        self.sent = []

    def emit(self, v):
        self.sent.append(v)


class FakeView:
    """What ``GroupTools`` asks of ``IslandView3D``."""

    def __init__(self, session):
        self.session, self.selected, self.cursor_hit = session, None, None
        self.hidden, self.status = Hidden(), Signal()
        self.group = GroupTools(self)
        self.group.attach(session)

    def select(self, e):
        self.selected = e
        self.group.sync(e)

    def apply_hidden(self):
        self.hidden.apply(self.session.entries)

    def from_view(self, v):
        return [self.session.size - v[0], v[1], v[2]]

    def update(self):
        pass


def rocks(s):
    return [e for e in s.entries if e.cfg == "rock.cfg"]


def test_shift_click_builds_a_selection_that_a_plain_click_replaces():
    s = EditSession(edit_island())
    v = FakeView(s)
    a, b = rocks(s)
    v.group.click(a, False)
    v.group.click(b, True)
    assert v.selected is a and v.group.others() == [b]
    v.group.click(b, False)
    assert v.group.selection.entries == [b]


def test_a_gizmo_move_carries_the_others_and_del_takes_all_as_one_change():
    s = EditSession(edit_island())
    v = FakeView(s)
    a, b = rocks(s)
    v.group.click(a, False)
    v.group.click(b, True)
    start = s.begin_transform(v.group.moving(a))
    s.set_transform(a, position=[6.5, 0.7, 6.5])
    v.group.follow(start, a.position)
    assert s.end_transform(start, "move")
    assert b.position == [10.0, 0.7, 9.0]
    assert v.group.delete() and s.undo.labels()[-1] == "delete 2"


def test_paste_lands_at_the_cursor_snapped_and_on_the_ground():
    s = EditSession(edit_island())
    v = FakeView(s)
    v.group.click(rocks(s)[0], False)
    assert v.group.copy() == 1
    v.group.snap.mode = "tile"
    v.cursor_hit = [16 - 8.2, 3.0, 8.9]
    assert v.group.paste() == 1
    assert v.selected.position == pytest.approx([8.5, 0.7, 8.5])


def test_a_hidden_layer_hides_in_the_view_and_a_locked_one_takes_no_click():
    s = EditSession(edit_island())
    v = FakeView(s)
    a, b = rocks(s)
    v.group.layers.assign([a], "cliffs")
    v.group.layers.set_flag("cliffs", "hidden", True)
    v.group.layers.set_flag("cliffs", "locked", True)
    v.group.apply_layers()
    assert a.hidden and not b.hidden
    v.group.click(a, False)
    assert v.selected is None and "locked" in v.status.sent[-1]
