"""Qt pieces every app's windows share: OK / Cancel, a search over a list in a dialog, the colour dialog, the Edit menu.

``ListPicker`` / ``pick_from_list`` ask for one entry of a list the caller builds for each search text;
``read_only_table`` / ``add_row`` the plain tables of the viewers; ``pick_qcolor`` is the colour dialog;
``edit_menu`` adds the Edit menu with Undo and Redo to a window; ``run_alone`` shows an app's window on its own.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..infra.i18n import tr
from . import theme
from .settings import SETTINGS_FILE, Settings, appdata

ROLE = 256  # Qt.UserRole: the entry's value


def ok_cancel(dialog: QDialog) -> QDialogButtonBox:
    """The OK and Cancel buttons of a dialog, wired to its accept and reject."""
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


def read_only_table(columns: tuple[str, ...]) -> QTableWidget:
    """A table of whole-row selection that cannot be typed into, headed by the (translated) ``columns``."""
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels([tr(c) for c in columns])
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    return table


def add_row(table: QTableWidget, cells: list[str], data: object = None) -> int:
    """A row of ``cells`` at the end of ``table`` (``data`` kept on its first cell under ``ROLE``); its index."""
    row = table.rowCount()
    table.insertRow(row)
    for col, text in enumerate(cells):
        table.setItem(row, col, QTableWidgetItem(text))
    if data is not None:
        table.item(row, 0).setData(ROLE, data)
    return row


class ListPicker(QDialog):
    """A search over ``rows(query)`` (pairs of the text shown and the value); the first row is current.

    ``chosen`` is the current row's value after a double click or OK. With ``value`` the dialog has an editable value
    line (starting with that text, taking the text of a row clicked) and ``chosen`` is the line's text instead.
    """

    def __init__(
        self,
        parent,
        title: str,
        hint: str,
        rows: Callable[[str], list[tuple[str, object]]],
        *,
        value: str | None = None,
        size: tuple[int, int] = (520, 560),
        placeholder: str = "",
    ) -> None:
        super().__init__(parent)
        self.rows, self.chosen = rows, None
        self.setWindowTitle(title)
        self.search = QLineEdit(placeholderText=placeholder or tr("search"))
        self.list = QListWidget()
        self.value = QLineEdit(value) if value is not None else None
        layout = QVBoxLayout(self)
        parts = [QLabel(hint)] if hint else []
        parts += [self.search, self.list]
        if self.value is not None:
            parts += [QLabel(tr("value")), self.value]
        for widget in (*parts, ok_cancel(self)):
            layout.addWidget(widget)
        self.search.textChanged.connect(lambda _t: self.fill())
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        if self.value is not None:
            self.list.currentItemChanged.connect(self._take)
        self.fill()
        self.resize(*size)

    def _take(self, current: QListWidgetItem | None, _previous) -> None:
        if current is not None:
            self.value.setText(str(current.data(ROLE)))

    def fill(self) -> None:
        # signals blocked: the first row becoming current must not overwrite the value line
        self.list.blockSignals(True)
        self.list.clear()
        for label, value in self.rows(self.search.text()):
            item = QListWidgetItem(label)
            item.setData(ROLE, value)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)

    def accept(self) -> None:
        item = self.list.currentItem()
        if self.value is not None:
            self.chosen = self.value.text().strip()
        else:
            self.chosen = item.data(ROLE) if item is not None else None
        super().accept()


def pick_from_list(
    parent, title: str, hint: str, rows: Callable[[str], list[tuple[str, object]]], **options
) -> object | None:
    """The value of the entry chosen in a ``ListPicker`` (``options`` as its keywords), None when cancelled."""
    dialog = ListPicker(parent, title, hint, rows, **options)
    return dialog.chosen if dialog.exec() and dialog.chosen is not None else None


def pick_qcolor(parent, start: QColor, alpha: bool = False) -> QColor | None:
    """The colour chosen in the system's dialog (with an alpha slider when ``alpha``), None when cancelled."""
    options = QColorDialog.ColorDialogOption.ShowAlphaChannel if alpha else QColorDialog.ColorDialogOption(0)
    chosen = QColorDialog.getColor(start, parent, tr("Pick a colour"), options)
    return chosen if chosen.isValid() else None


def edit_menu(window, undo: Callable, redo: Callable) -> tuple[QMenu, QAction, QAction]:
    """The window's Edit menu with Undo and Redo (their standard shortcuts); the caller adds the rest and the enabling."""
    menu = window.menuBar().addMenu(tr("&Edit"))
    undo_action = menu.addAction(tr("&Undo"), undo, QKeySequence.StandardKey.Undo)
    redo_action = menu.addAction(tr("&Redo"), redo, QKeySequence.StandardKey.Redo)
    return menu, undo_action, redo_action


def run_alone(window_class, app: str, size: tuple[int, int], game: str | None = None, *opened) -> int:
    """An app's window alone, on the suite's settings: ``window_class(settings, path)`` shown at ``size``, the game
    asked for (else the one the app had open last, ``settings.apps[app]``) opened with ``open_game(game, *opened)``;
    the settings are saved when the window closes. Returns Qt's exit code."""
    qt = QApplication.instance() or QApplication([sys.argv[0]])
    qt.setStyle("Fusion")
    settings_path = appdata() / SETTINGS_FILE
    window = window_class(Settings.load(settings_path), settings_path)
    theme.apply(window, window.settings.theme)
    window.resize(*size)
    window.show()
    game = game or window.settings.apps.get(app, {}).get("game")
    if game:
        window.open_game(game, *opened)
    code = qt.exec()
    window.settings.save(settings_path)
    return code
