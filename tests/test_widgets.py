"""The suite's shared Qt pieces (suite/widgets.py): the search list with its first row current and its value line,
and the Edit menu with Undo and Redo."""

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qt():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def rows(query):
    return [(f"label {n}", n) for n in ("a1", "b2", "a3") if query in n]


def test_the_first_row_is_current_and_a_search_refills(qt):
    from annotoolbox.suite.widgets import ListPicker

    dialog = ListPicker(None, "t", "hint", rows)
    assert dialog.list.currentRow() == 0
    dialog.accept()
    assert dialog.chosen == "a1"  # Enter takes the first match
    dialog = ListPicker(None, "t", "", rows)
    dialog.search.setText("3")
    assert dialog.list.count() == 1 and dialog.list.currentItem().data(256) == "a3"
    dialog.search.setText("zz")
    dialog.accept()
    assert dialog.chosen is None


def test_the_value_line_keeps_its_text_until_a_row_is_clicked(qt):
    from annotoolbox.suite.widgets import ListPicker

    dialog = ListPicker(None, "t", "hint", rows, value="typed.cfg")
    assert dialog.list.currentRow() == 0 and dialog.value.text() == "typed.cfg"  # filling does not overwrite it
    dialog.search.setText("")
    assert dialog.value.text() == "typed.cfg"
    dialog.list.setCurrentRow(1)
    assert dialog.value.text() == "b2"
    dialog.value.setText("  edited  ")
    dialog.accept()
    assert dialog.chosen == "edited"


def test_edit_menu_has_undo_and_redo_with_their_shortcuts(qt):
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QMainWindow

    from annotoolbox.suite.widgets import edit_menu

    window, seen = QMainWindow(), []
    menu, undo, redo = edit_menu(window, lambda: seen.append("u"), lambda: seen.append("r"))
    assert menu.title() == "&Edit" and menu in window.menuBar().findChildren(type(menu))
    assert undo.shortcut() == QKeySequence(QKeySequence.StandardKey.Undo)
    undo.trigger()
    redo.trigger()
    assert seen == ["u", "r"]
