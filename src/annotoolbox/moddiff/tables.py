"""The Conflicts tab's table, and the Dropped tab's list. ``ConflictTable.set_conflicts`` fills the table (a model that
reads a row's text only when the view shows it, so no conflict is left out), a double click on a row says which target it
is about (``opened``). ``docs/mod-diff.md`` "The views".
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QPlainTextEdit, QTableView

from ..infra.i18n import N_, tr
from .model import ASSET, BLOCK, CONFLICT, DUPLICATE, FILE, GONE, OVERLAP, VALUE, Conflict

KINDS = {
    VALUE: N_("same value"), BLOCK: N_("block"), GONE: N_("removed and used"), DUPLICATE: N_("added twice"),
    FILE: N_("same file"), ASSET: N_("same asset"),
}  # fmt: skip
NO_PARENT = QModelIndex()  # the default parent of a model's questions: the top level
LEVELS = {CONFLICT: N_("conflict"), OVERLAP: N_("overlap")}
HEADERS = (
    N_("Level"),
    N_("Kind"),
    N_("Target"),
    N_("Path"),
    N_("Mods and values in load order"),
    N_("Winner"),
    N_("Decided by"),
)


def values_text(conflict: Conflict) -> str:
    """Each mod's value in load order: ``mod: value`` joined by a semicolon (removed: ``-``)."""
    return "; ".join(f"{m}: {v if v is not None else '-'}" for m, v in zip(conflict.mods, conflict.values, strict=True))


def cells(c: Conflict) -> list[str]:
    """The seven columns of one conflict."""
    target = c.file if not c.key else f"{c.key} {c.name}".strip()
    return [tr(LEVELS.get(c.level, c.level)), tr(KINDS[c.kind]), target, c.path, values_text(c), c.winner, c.decided]


class ConflictModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.found: list[Conflict] = []

    def set_conflicts(self, conflicts: list[Conflict]) -> None:
        self.beginResetModel()
        self.found = conflicts
        self.endResetModel()

    def rowCount(self, parent=NO_PARENT) -> int:
        return 0 if parent.isValid() else len(self.found)

    def columnCount(self, parent=NO_PARENT) -> int:
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(HEADERS[section])
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        return cells(self.found[index.row()])[index.column()]


class ConflictTable(QTableView):
    opened = Signal(str, str)  # the file and the key of the row's target

    def __init__(self) -> None:
        super().__init__()
        self.model_ = ConflictModel()
        self.setModel(self.model_)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setDefaultSectionSize(22)
        self.setColumnWidth(2, 260)
        self.setColumnWidth(3, 240)
        self.setColumnWidth(4, 360)
        self.doubleClicked.connect(self._open)

    @property
    def found(self) -> list[Conflict]:
        return self.model_.found

    def set_conflicts(self, conflicts: list[Conflict]) -> None:
        self.model_.set_conflicts(conflicts)

    def _open(self, index: QModelIndex) -> None:
        if 0 <= index.row() < len(self.found):
            self.opened.emit(self.found[index.row()].file, self.found[index.row()].key)


class DroppedList(QPlainTextEdit):
    """What the loader would log or skip, one line each."""

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def set_lines(self, dropped: list[str], notes: list[str]) -> None:
        self.setPlainText("\n".join([*dropped, *notes]) or tr("Nothing the loader would drop."))
