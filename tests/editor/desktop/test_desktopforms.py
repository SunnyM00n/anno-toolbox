"""The island properties dialog offscreen: the fields show the island's values and the changes become edit-file keys."""

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from tests.editor.editorfakes import edit_island
from tests.islands.islandfakes import vanilla_editor

from annotoolbox.editor import islandprops
from annotoolbox.editor.desktop import desktopforms
from annotoolbox.editor.editsession import CEditSession


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def dialog(qt, edits=None):
    s = CEditSession(edit_island(), edits)
    sections = islandprops.sections_for(vanilla_editor(), s)
    return s, desktopforms.CIslandPropertiesDialog(None, sections, s.m_edits)


def test_the_fields_show_the_islands_values_and_a_change_is_written_as_one_step(qt):
    s, dlg = dialog(qt, {"target": "new"})
    info, registration = dlg.m_pages
    assert info.m_editors["start_coast"].get() == pytest.approx(57.3, abs=0.01)
    assert info.m_editors["active_rect"].get() == [8, 0, 24, 24] and info.m_editors["grid"].get() is None
    assert (
        registration.m_editors["IslandRegion"].get() == "Roman"
        and registration.m_editors["IslandType"].get() == "Normal"
    )
    info.m_editors["start_coast"].set(200.0)
    info.m_editors["active_rect"].set([0, 0, 32, 32])
    registration.m_editors["IslandType"].set("Decoration;Starter")
    registration.m_editors["IslandPlaceholderSize"].set("Medium")
    problems = dlg.write(s)
    assert len(problems) == 1 and "slots" in problems[0]  # a Medium slot on a 32-tile grid is named
    assert s.m_edits["meta"] == {"start_coast": 200.0, "active_rect": [0, 0, 32, 32]}
    assert s.m_edits["registration"]["IslandType"] == "Decoration;Starter"
    assert len(s.m_undo.m_commands) == 2


def test_an_int_field_the_island_lacks_opens_and_stays_unset(qt):
    editor = desktopforms.CFieldEditor(islandprops.SField("n", "N", "int", 0, 9), None)
    assert editor.m_parts[0].value() == 0 and editor.get() is None


def test_an_unset_angle_stays_unset_and_a_problem_shows_as_you_type(qt):
    s, dlg = dialog(qt)
    info = dlg.m_pages[0]
    info.m_editors["start_coast"].m_has_value.setChecked(False)
    assert info.m_editors["start_coast"].get() is None
    info.m_editors["active_rect"].m_parts[2].setValue(500)
    assert "grid" in info.m_problems.text()
    info.m_editors["active_rect"].set([8, 0, 24, 24])
    assert info.m_problems.text() == ""


def test_a_registration_that_is_not_editable_is_shown_and_left_alone(qt):
    s, dlg = dialog(qt)
    registration = dlg.m_pages[1]
    assert not registration.m_editors["IslandRegion"].m_widget.isEnabled() and "new one" in registration.m_section.note
    registration.m_editors["IslandRegion"].set("Celtic")
    dlg.write(s)
    assert "registration" not in s.m_edits


def test_the_menu_item_opens_nothing_for_an_island_without_settings(qt):
    said = []
    main = SimpleNamespace(m_doc=None, say=said.append, sync=lambda: None)
    desktopforms.show(main)
    assert not said  # no island open: nothing happens
    s = CEditSession(edit_island())
    main.m_doc = SimpleNamespace(m_editor=object(), m_session=s)
    desktopforms.show(main)
    assert said == ["this island has no settings to change"]


def test_the_tint_button_shows_the_colour_and_none_for_a_prop_without_one(qt):
    from annotoolbox.editor.desktop.desktopcolor import CColorButton

    button = CColorButton()
    got = []
    button.picked.connect(got.append)
    assert button.text() == "none" and button.m_rgba is None
    button.set_color([1.0, 0.0, 0.0, 0.5])
    assert button.text() == "#ff0000  alpha 0.50" and "background-color: #ff0000" in button.styleSheet()
    button.set_color([3.0, 0.0, 0.0, 1.0])  # brighter than white: shown clamped, kept as it is
    assert button.text().startswith("#ff0000") and button.m_rgba[0] == 3.0


def test_the_session_page_lists_the_games_names_keeps_an_unknown_one_and_names_a_bad_area(qt):
    from annotoolbox.islands import sessionsettings

    editor = vanilla_editor()
    editor.m_files.files[f"{sessionsettings.AMBIENT_FOLDER}amb_celtic_01.xml"] = b""
    own = {"ambient": "amb_made_up", "vegetation": "ls_a", "playable_area": [0, 0, 16, 16]}
    s = CEditSession(edit_island(session=own))
    dlg = desktopforms.CIslandPropertiesDialog(None, islandprops.sections_for(editor, s), s.m_edits)
    page = dlg.m_pages[-1]
    assert page.m_editors["ambient"].get() == "amb_made_up"  # not in the list: kept, not lost
    page.m_editors["ambient"].set("amb_celtic_01")
    page.m_editors["playable_area"].set([0, 0, 99, 16])
    assert "must lie inside" in page.m_problems.text()
    problems = dlg.write(s)
    assert len(problems) == 1 and s.m_edits["session"]["ambient"] == "amb_celtic_01"


def test_the_slot_list_dialog_shows_listed_standing_and_what_the_generator_keeps(qt):
    from annotoolbox.editor.desktop import desktopslots

    s = CEditSession(
        edit_island(
            slot_list={"99": 3, "7": 2},
            slot_generator={"99": {"name": "Slot Test", "count": "2", "all": "0"}, "7": {"name": "Fixed", "all": "1"}},
        )
    )
    dlg = desktopslots.CSlotListDialog(None, s.slot_rows())
    cells = [[dlg.m_table.item(r, c).text() for c in range(5)] for r in range(2)]
    assert cells == [
        ["Slot Test", "3", "1", "2 of them", "the build lists 1"],
        ["Fixed", "2", "0", "all of them", "the build lists 0"],
    ]
    said = []
    desktopslots.show(SimpleNamespace(m_doc=SimpleNamespace(m_session=CEditSession(edit_island())), say=said.append))
    assert said == ["this island lists no slots"]


@pytest.mark.parametrize(
    "field, value, touched",
    [
        (islandprops.SField("clime", "Clime", "int", 0, 1), 3, 0),
        (islandprops.SField("start_coast", "Coast", "angle", 0, 360), -90.0, 0.0),
        (islandprops.SField("active_rect", "Rect", "rect"), [-8, 0, 5000, 24], [0, 0, 4096, 25]),
        (
            islandprops.SField("IslandType", "Type", "choices", choices=("Normal", "Starter")),
            "Mod;Normal",
            "Normal;Starter",
        ),
    ],
)
def test_a_value_the_field_cannot_show_stays_the_islands_until_the_field_is_changed(qt, field, value, touched):
    """OK without a change wrote the widget's nearest value (the number clamped into the field's range, the list
    without the entry that is no choice) as an edit of the island."""
    editor = desktopforms.CFieldEditor(field, value)
    assert editor.get() == value
    if field.kind == "rect":
        editor.m_parts[3].setValue(25)
    elif field.kind == "choices":
        editor.m_parts[1].setChecked(True)
    elif field.kind == "angle":
        editor.m_parts[0].setValue(0.0)
        editor.m_has_value.setChecked(False)
        editor.m_has_value.setChecked(True)
        assert editor.get() == value  # back to what it showed: still the island's
        editor.m_parts[0].setValue(12.5)
        touched = 12.5
    else:
        editor.m_parts[0].setValue(0)
    assert editor.get() == touched
