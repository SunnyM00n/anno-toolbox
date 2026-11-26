"""The Saves app's Ships tab (Qt): the ships of an Anno 117 save in a table, and the delete of the chosen one after a
question that shows what is removed, left and refused. ``CShipsPanel(window)`` is the tab; it reads a save's ships
the first time it is shown for it. The reading and the change are in ``ships117`` and ``shipdelete117``
(``docs/save-editor.md``, "Ships (117)").
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import tr
from ..suite import widgets
from . import shipdelete117, ships117

GAME = "anno117"


class CShipsPanel(QWidget):
    """The Ships tab of ``window`` (the ``CSavesWindow``: its names, jobs, busy state, status line and writes)."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.m_window = window
        self.m_save = None
        self.m_read_save = None  # the save whose ships were asked for (the table shows them once they are read)
        self.m_ships: list[ships117.SShip] = []
        self.m_routes: dict[int, str] = {}
        self.m_is_planning = False
        self.m_note = QLabel(wordWrap=True)
        self.m_table = QTableWidget(0, len(ships117.HEADERS))
        self.m_table.setHorizontalHeaderLabels([tr(header) for header in ships117.HEADERS])
        self.m_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.m_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.m_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.m_table.itemSelectionChanged.connect(self.update_button)
        self.m_delete_button = QPushButton(tr("saves.delete_ship"))
        self.m_delete_button.clicked.connect(lambda _checked=False: self.delete_chosen())
        lay = QVBoxLayout(self)
        lay.addWidget(self.m_note)
        lay.addWidget(self.m_table)
        lay.addWidget(self.m_delete_button)
        self.set_save(None)

    def set_save(self, save) -> None:
        """The open save; its ships are read when the tab is shown (``read_ships``)."""
        self.m_save, self.m_read_save, self.m_ships, self.m_routes = save, None, [], {}
        self.m_table.setRowCount(0)
        self.m_note.setText(tr("saves.ships_only_117") if save is None or save.game != GAME else "")
        self.update_button()
        if self.isVisible():
            self.read_ships()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.read_ships()

    def read_ships(self) -> None:
        """Read the open save's ships off the window's thread, once per save (a 117 save only)."""
        save = self.m_save
        if save is None or save.game != GAME or self.m_read_save is save:
            return
        self.m_read_save = save
        self.m_note.setText(tr("saves.reading_ships"))
        say = self.m_window.say
        self.m_window.m_background.run(
            lambda: (ships117.read(save, say), ships117.route_names(save)),
            lambda result: self._read(save, result),
            self.m_window.m_is_synchronous,
        )

    def _read(self, save, result) -> None:
        if save is not self.m_save:
            return
        if isinstance(result, Exception):
            self.m_note.setText(tr("saves.ships_not_read", error=str(result)))
            return
        self.m_ships, self.m_routes = result
        self.m_note.setText(tr("saves.ships_found", n=len(self.m_ships)))
        self.show_names()

    def show_names(self) -> None:
        """Fill the table, with the game's names once the window has them."""
        self.m_table.setRowCount(len(self.m_ships))
        for row, cells in enumerate(ships117.rows(self.m_ships, self.m_routes, self.m_window.m_names)):
            for column, text in enumerate(cells):
                self.m_table.setItem(row, column, QTableWidgetItem(text))
        self.m_table.resizeColumnsToContents()
        self.update_button()

    def chosen(self) -> ships117.SShip | None:
        row = self.m_table.currentRow()
        return self.m_ships[row] if 0 <= row < len(self.m_ships) and self.m_table.selectedItems() else None

    def update_button(self) -> None:
        """Delete is on for a chosen ship while nothing is planned or written, and says why it is not otherwise."""
        if self.m_is_planning or self.m_window.m_busy:
            why = tr("saves.tip.write_busy")
        elif self.chosen() is None:
            why = tr("saves.tip.pick_ship_first")
        else:
            why = ""
        widgets.enable_unless(self.m_delete_button, why, tr("saves.tip.delete_ship"))

    def delete_chosen(self) -> None:
        """Plan the delete of the chosen ship off the window's thread, then ask before it is written."""
        ship, save = self.chosen(), self.m_save
        if ship is None or save is None or self.m_is_planning or self.m_window.m_busy:
            return
        self.m_is_planning = True
        self.update_button()
        self.m_window.statusBar().showMessage(tr("saves.planning_delete", name=self._name(ship)))
        self.m_window.m_background.run(
            lambda: shipdelete117.plan(save, ship),
            lambda result: self._planned(save, ship, result),
            self.m_window.m_is_synchronous,
        )

    def _name(self, ship: ships117.SShip) -> str:
        return ships117.ship_name(ship, self.m_window.m_names)

    def _planned(self, save, ship: ships117.SShip, result) -> None:
        self.m_is_planning = False
        self.update_button()
        if save is not self.m_save:
            return
        if isinstance(result, Exception):
            self.m_window.statusBar().showMessage(str(result))
            QMessageBox.warning(self, tr("saves.delete_ship"), str(result))
            return
        if result.refused:
            said = tr("saves.ship_cannot_delete", name=self._name(ship))
            self.m_window.statusBar().showMessage(said)
            box = QMessageBox(QMessageBox.Icon.Warning, tr("saves.delete_ship"), said, parent=self)
            box.setInformativeText(shipdelete117.describe(result))
            box.exec()
            return
        if self.confirm(ship, result):
            self.m_window.run_write(lambda: shipdelete117.write_beside(save, result))

    def confirm(self, ship: ships117.SShip, planned: shipdelete117.SPlan) -> bool:
        """Ask before the delete, with every place it changes and leaves."""
        question = tr("saves.delete_ship_question", name=self._name(ship), path=self.m_save.path.name)
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        box = QMessageBox(QMessageBox.Icon.Question, tr("saves.delete_ship"), question, buttons, self)
        box.setInformativeText(shipdelete117.describe(planned))
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes
