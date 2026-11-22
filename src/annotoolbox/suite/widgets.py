"""Qt pieces every app's windows share: OK / Cancel, a search over a list in a dialog, the colour dialog, the Edit menu.

``CListPicker`` / ``pick_from_list`` ask for one entry of a list the caller builds for each search text;
``read_only_table`` / ``add_row`` the plain tables of the viewers; ``pick_qcolor`` is the colour dialog;
``edit_menu`` adds the Edit menu with Undo and Redo to a window; ``run_alone`` shows an app's window on its own.
``ask_keep`` is the Save / Discard / Cancel question every app asks before unkept edits go; ``may_close`` is what a
page's close handler asks first (the shell's two-step close, docs/app-api.md "Closing"); ``enable_unless`` greys a
control and says why; ``stop_button``: a long job's Stop; ``access_keys`` / ``add_docks`` give titles access keys.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable

from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..infra.i18n import tr
from . import theme
from .settings import CSettings, settings_file

ROLE = 256  # Qt.UserRole: the entry's value


def ok_cancel(dialog: QDialog) -> QDialogButtonBox:
    """The OK and Cancel buttons of a dialog, wired to its accept and reject."""
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


def stop_button(window, stop: Callable[[], None]) -> QPushButton:
    """A Stop button at the right of ``window``'s status bar, hidden until a long job shows it; a click calls
    ``stop``."""
    button = QPushButton(tr("common.stop"))
    button.clicked.connect(lambda _c=False: stop())
    button.hide()
    window.statusBar().addPermanentWidget(button)
    return button


def read_only_table(columns: tuple[str, ...]) -> QTableWidget:
    """A table of whole-row selection that cannot be typed into, headed by the (translated) ``columns``."""
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels([tr(column_key) for column_key in columns])
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    return table


def add_row(table: QTableWidget, cells: list[str], data: object = None) -> int:
    """A row of ``cells`` at the end of ``table`` (``data`` kept on its first cell under ``ROLE``); its index."""
    row = table.rowCount()
    table.insertRow(row)
    for column, text in enumerate(cells):
        table.setItem(row, column, QTableWidgetItem(text))
    if data is not None:
        table.item(row, 0).setData(ROLE, data)
    return row


class CListPicker(QDialog):
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
        size_px: tuple[int, int] = (520, 560),
        placeholder: str = "",
    ) -> None:
        super().__init__(parent)
        self.m_rows, self.m_chosen = rows, None
        self.setWindowTitle(title)
        self.m_search = QLineEdit(placeholderText=placeholder or tr("common.search"))
        self.m_list = QListWidget()
        self.m_value = QLineEdit(value) if value is not None else None
        layout = QVBoxLayout(self)
        parts = [QLabel(hint)] if hint else []
        parts += [self.m_search, self.m_list]
        if self.m_value is not None:
            parts += [QLabel(tr("common.value_lower")), self.m_value]
        for widget in (*parts, ok_cancel(self)):
            layout.addWidget(widget)
        self.m_search.textChanged.connect(lambda _t: self.fill())
        self.m_list.itemDoubleClicked.connect(lambda _i: self.accept())
        if self.m_value is not None:
            self.m_list.currentItemChanged.connect(self._take)
        self.fill()
        self.resize(*size_px)

    def _take(self, current: QListWidgetItem | None, _previous) -> None:
        if current is not None:
            self.m_value.setText(str(current.data(ROLE)))

    def fill(self) -> None:
        # signals blocked: the first row becoming current must not overwrite the value line
        self.m_list.blockSignals(True)
        self.m_list.clear()
        for label, value in self.m_rows(self.m_search.text()):
            item = QListWidgetItem(label)
            item.setData(ROLE, value)
            self.m_list.addItem(item)
        if self.m_list.count():
            self.m_list.setCurrentRow(0)
        self.m_list.blockSignals(False)

    def accept(self) -> None:
        item = self.m_list.currentItem()
        if self.m_value is not None:
            self.m_chosen = self.m_value.text().strip()
        else:
            self.m_chosen = item.data(ROLE) if item is not None else None
        super().accept()


def pick_from_list(
    parent, title: str, hint: str, rows: Callable[[str], list[tuple[str, object]]], **options
) -> object | None:
    """The value of the entry chosen in a ``CListPicker`` (``options`` as its keywords), None when cancelled."""
    dialog = CListPicker(parent, title, hint, rows, **options)
    return dialog.m_chosen if dialog.exec() and dialog.m_chosen is not None else None


def pick_qcolor(parent, start: QColor, alpha: bool = False) -> QColor | None:
    """The colour chosen in the system's dialog (with an alpha slider when ``alpha``), None when cancelled."""
    options = QColorDialog.ColorDialogOption.ShowAlphaChannel if alpha else QColorDialog.ColorDialogOption(0)
    chosen = QColorDialog.getColor(start, parent, tr("assets.pick_colour"), options)
    return chosen if chosen.isValid() else None


def edit_menu(window, undo: Callable, redo: Callable) -> tuple[QMenu, QAction, QAction]:
    """The window's Edit menu with Undo and Redo (their standard shortcuts); the caller adds the rest and the enabling."""
    menu = window.menuBar().addMenu(tr("common.menu.edit"))
    undo_action = menu.addAction(tr("common.undo"), undo, QKeySequence.StandardKey.Undo)
    redo_action = menu.addAction(tr("common.redo"), redo, QKeySequence.StandardKey.Redo)
    return menu, undo_action, redo_action


def ask_keep(parent, title: str, text: str, save: Callable[[], object]) -> bool:
    """Ask Save / Discard / Cancel before unkept edits go. True when the caller may go on (the edits were kept by
    ``save``, which returns whether it did, or dropped); False on Cancel or a save that did not keep them."""
    buttons = QMessageBox.StandardButton
    answer = QMessageBox.question(parent, title, text, buttons.Save | buttons.Discard | buttons.Cancel, buttons.Save)
    if answer == buttons.Save:
        return bool(save())
    return answer != buttons.Cancel


CLOSE_AGREED = "close_agreed"  # the dynamic property the shell sets on a page that said yes to ``can_close``


def may_close(window) -> bool:
    """A page's ``closeEvent`` asks this first: True when the shell already got the page's yes (its ``can_close``
    asked the questions), else the page asks them now. The yes is used once."""
    agreed = bool(window.property(CLOSE_AGREED))
    window.setProperty(CLOSE_AGREED, False)
    return agreed or window.can_close()


def ask_choice(parent, title: str, text: str, choices: Iterable[tuple], default: str, escape: str) -> str:
    """A question with its own buttons. ``choices`` are (key, label or a standard button, role); Enter takes
    ``default``, Esc and closing the box ``escape``; the key of the button clicked."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
    made = {}
    for key, label, role in choices:
        made[key] = box.addButton(label, role) if isinstance(label, str) else box.addButton(label)
    box.setDefaultButton(made[default])
    box.setEscapeButton(made[escape])
    box.exec()
    clicked = box.clickedButton()
    return next((key for key, button in made.items() if button is clicked), escape)


def kept_switch(window, menu: QMenu, text: str, changed: Callable[[], object], key: tuple[str, str],
                checked_by_default: bool = True) -> QAction:  # fmt: skip
    """A checkable menu entry kept in the settings: ``key`` is (app, name) in ``window.settings.apps``,
    ``checked_by_default`` its state before anyone set it. The setting follows every change; ``changed`` runs when the
    person picks the entry."""
    app, name = key
    action = menu.addAction(text, changed)
    action.setCheckable(True)
    action.setChecked(bool(window.settings.apps.get(app, {}).get(name, checked_by_default)))
    action.toggled.connect(lambda state: window.settings.apps.setdefault(app, {}).__setitem__(name, state))
    return action


def find_key(window, box: QLineEdit) -> QShortcut:
    """Ctrl+F in ``window`` goes to the search box ``box`` (its dock is shown first when it is hidden)."""

    def go() -> None:
        dock = box.parentWidget()
        while dock is not None and not isinstance(dock, QDockWidget):
            dock = dock.parentWidget()
        if dock is not None:
            dock.show()
            dock.raise_()
        box.setFocus()
        box.selectAll()

    shortcut = QShortcut(QKeySequence.StandardKey.Find, window)
    shortcut.activated.connect(go)
    return shortcut


def enable_unless(item, why: str = "", tip: str = "") -> None:
    """Enable a widget or action when ``why`` is empty; else grey it and make ``why`` its tool tip (else ``tip``)."""
    item.setEnabled(not why)
    item.setToolTip(why or tip)
    if isinstance(item, QAction):
        item.setStatusTip(why or tip)


def access_letters(texts: Iterable[str]) -> str:
    """The access keys (``&x``) the texts carry, in lower case."""
    return "".join(
        text[i + 1].lower() for text in texts for i in range(len(text) - 1) if text[i] == "&" and text[i + 1] != "&"
    )


def access_keys(titles: Iterable[str], avoid: str = "") -> list[str]:
    """The titles with an access key each, no key given twice: a title that has one (``&x``) keeps it, the others take
    the first letter or digit not taken yet (the first of a word first), none of the letters ``avoid``."""
    texts = list(titles)
    taken = set(access_letters(texts)) | set(avoid.lower())
    out = []
    for text in texts:
        if "&" in text.replace("&&", ""):
            out.append(text)
            continue
        starts = [
            i for i, character in enumerate(text) if character.isalnum() and (i == 0 or not text[i - 1].isalnum())
        ]
        order = starts + [i for i, character in enumerate(text) if character.isalnum() and i not in starts]
        key_index = next((i for i in order if text[i].lower() not in taken), None)
        if key_index is None:
            out.append(text)
            continue
        taken.add(text[key_index].lower())
        out.append(f"{text[:key_index]}&{text[key_index:]}")
    return out


def assign_access_keys(actions: Iterable[QAction], shown_together: Iterable[list[QAction]], avoid: str = "") -> None:
    """Give the actions without an access key one that no other action of a list they are shown in carries (a page of
    buttons, a menu), and none of the letters ``avoid``; an action that has one keeps it."""
    groups = [list(group) for group in shown_together]
    for action in actions:
        if "&" in action.text().replace("&&", ""):
            continue
        away = avoid + "".join(
            access_letters(other.text() for other in group if other is not action)
            for group in groups
            if action in group
        )
        action.setText(access_keys([action.text()], away)[0])


def menu_groups(menu) -> list[list[QAction]]:
    """The entries of a menu or menu bar and, below them, of each submenu: the lists whose keys must differ."""
    entries = [action for action in menu.actions() if not action.isSeparator()]
    out = [entries]
    for action in entries:
        if action.menu() is not None:
            out += menu_groups(action.menu())
    return out


def add_docks(menu: QMenu, docks: Iterable) -> None:
    """The docks' show/hide actions in ``menu``, each title with an access key of its own."""
    actions = [dock.toggleViewAction() for dock in docks]
    for action, text in zip(actions, access_keys(dock_action.text() for dock_action in actions), strict=True):
        action.setText(text)
        menu.addAction(action)


def run_alone(window_class, app: str, size_px: tuple[int, int], game: str | None = None, *opened) -> int:
    """An app's window alone, on the suite's settings: ``window_class(settings, path)`` shown at ``size_px``, the game
    asked for (else the one the app had open last, ``settings.apps[app]``) opened with ``open_game(game, *opened)``;
    the settings are saved when the window closes. Returns Qt's exit code."""
    application = QApplication.instance() or QApplication([sys.argv[0]])
    application.setStyle("Fusion")
    settings_path = settings_file()
    settings = CSettings.load(settings_path)  # the window keeps this one: read here, so any window class fits
    window = window_class(settings, settings_path)
    theme.apply(window, settings.theme)
    window.resize(*size_px)
    window.show()
    game = game or settings.apps.get(app, {}).get("game")
    if game:
        window.open_game(game, *opened)
    code = application.exec()
    settings.save(settings_path)
    return code
