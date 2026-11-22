"""The 3D view's group tools without a window: Shift+click, a gizmo move carrying the rest, Del, copy and paste,
snapping, layers hidden in the view."""

import pytest

pytest.importorskip("PySide6")

from tests.editor.editorfakes import FakeSignal, edit_island

from annotoolbox.editor.desktop.desktopgroup import CGroupTools, edit_menu, start_falloff, start_snap
from annotoolbox.editor.editlists import CHidden
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.infra.i18n import tr
from annotoolbox.suite.settings import CSettings


class FakeGroupView:
    """What ``CGroupTools`` asks of ``CIslandView3D``."""

    def __init__(self, session):
        self.m_session, self.m_selected, self.m_cursor_hit = session, None, None
        self.m_hidden, self.status = CHidden(), FakeSignal()
        self.m_group = CGroupTools(self)
        self.m_group.attach(session)

    def select(self, e):
        self.m_selected = e
        self.m_group.sync(e)

    def apply_hidden(self):
        self.m_hidden.apply(self.m_session.m_entries)

    def from_view(self, v):
        return [self.m_session.m_size_tiles - v[0], v[1], v[2]]

    def update(self):
        pass


def rocks(s):
    return [e for e in s.m_entries if e.cfg == "rock.cfg"]


def test_shift_click_builds_a_selection_that_a_plain_click_replaces():
    s = CEditSession(edit_island())
    v = FakeGroupView(s)
    a, b = rocks(s)
    v.m_group.click(a, False)
    v.m_group.click(b, True)
    assert v.m_selected is a and v.m_group.others() == [b]
    v.m_group.click(b, False)
    assert v.m_group.m_selection.entries == [b]


def test_a_gizmo_move_carries_the_others_and_del_takes_all_as_one_change():
    s = CEditSession(edit_island())
    v = FakeGroupView(s)
    a, b = rocks(s)
    v.m_group.click(a, False)
    v.m_group.click(b, True)
    start = s.begin_transform(v.m_group.moving(a))
    s.set_transform(a, position=[6.5, 0.7, 6.5])
    v.m_group.follow(start, a.position)
    assert s.end_transform(start, "move")
    assert b.position == [10.0, 0.7, 9.0]
    assert v.m_group.delete() and s.m_undo.labels()[-1] == "delete 2"


def test_paste_lands_at_the_cursor_snapped_and_on_the_ground():
    s = CEditSession(edit_island())
    v = FakeGroupView(s)
    v.m_group.click(rocks(s)[0], False)
    assert v.m_group.copy() == 1
    v.m_group.m_snap.mode = "tile"
    v.m_cursor_hit = [16 - 8.2, 3.0, 8.9]
    assert v.m_group.paste() == 1
    assert v.m_selected.position == pytest.approx([8.5, 0.7, 8.5])


def test_a_hidden_layer_hides_in_the_view_and_a_locked_one_takes_no_click():
    s = CEditSession(edit_island())
    v = FakeGroupView(s)
    a, b = rocks(s)
    v.m_group.m_layers.assign([a], "cliffs")
    v.m_group.m_layers.set_flag("cliffs", "hidden", True)
    v.m_group.m_layers.set_flag("cliffs", "locked", True)
    v.m_group.apply_layers()
    assert a.hidden and not b.hidden
    v.m_group.click(a, False)
    assert v.m_selected is None and "locked" in v.status.sent[-1]


def test_a_new_island_starts_from_the_users_settings_and_an_opened_one_keeps_its_state():
    settings = CSettings(brush_falloff="linear", snap_places="half", snap_turns="quarter", snap_onto_ground=True)
    first = FakeGroupView(CEditSession(edit_island()))
    first.m_group.m_settings = settings
    first.m_session = CEditSession(edit_island())  # an island the view had not seen
    first.m_group.attach(first.m_session)
    assert (
        first.m_session.m_falloff,
        first.m_group.m_snap.mode,
        first.m_group.m_snap.turn,
        first.m_group.m_snap.ground,
    ) == (
        "linear",
        "half",
        "quarter",
        True,
    )
    first.m_group.m_snap.mode = "tile"  # the user's change on this island
    first.m_session.m_falloff = "sharp"
    second = CEditSession(edit_island())
    first.m_group.attach(second)
    assert (second.m_falloff, first.m_group.m_snap.mode) == ("linear", "half")  # another island: the settings again
    first.m_group.attach(first.m_session)  # back to the first: its own state, not the settings
    assert (first.m_session.m_falloff, first.m_group.m_snap.mode) == ("sharp", "tile")


def test_a_setting_that_is_no_curve_mode_or_turn_reads_as_the_default():
    bad = CSettings(brush_falloff="wavy", snap_places="x", snap_turns="y")
    assert start_falloff(bad) == "smooth" and start_snap(bad).mode == "off" and start_snap(bad).turn == "off"


@pytest.fixture(scope="module")
def qt():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_edit_menu_shows_the_open_islands_snap_and_falloff(qt):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMenu

    session = CEditSession(edit_island())
    view = FakeGroupView(session)
    main = SimpleNamespace(m_doc=SimpleNamespace(m_view=view, m_session=session), say=lambda _text: None)
    menu = QMenu()
    edit_menu(main, menu)
    session.m_snap.mode, session.m_falloff = "half", "sharp"  # the island's own state, set after the menu was built

    def checked(title):
        sub = next(a.menu() for a in menu.actions() if a.menu() and a.text().replace("&", "") == title)
        sub.aboutToShow.emit()
        return [a.text().replace("&", "") for a in sub.actions() if a.isChecked()]

    assert checked(tr("islands.menu.snap_places")) == [tr("islands.half_tiles")]
    assert checked(tr("islands.menu.brush_falloff")) == [tr("islands.sharp")]


def test_the_settings_page_has_a_row_for_each_start_value_and_returns_it(qt, tmp_path):
    from annotoolbox.editor.desktop.desktopgroup import new_island_choices
    from annotoolbox.suite.settingsdialog import CSettingsDialog

    settings = CSettings(workspace=str(tmp_path), snap_places="cell")
    dialog = CSettingsDialog(settings, choices=new_island_choices())
    assert dialog.values()["snap_places"] == "cell" and dialog.values()["brush_falloff"] == "smooth"
    dialog.m_choices["snap_onto_ground"].setCurrentIndex(dialog.m_choices["snap_onto_ground"].findData(True))
    assert dialog.values()["snap_onto_ground"] is True
    assert "snap_places" not in CSettingsDialog(settings).values()  # a page without the rows is as before
