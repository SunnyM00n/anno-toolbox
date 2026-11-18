"""The Conflicts tab's table, and the Dropped tab's list. ``ConflictTable.set_conflicts`` fills the table (a model that
reads a row's text only when the view shows it, so no conflict is left out), a double click on a row says which target it
is about (``opened``). ``docs/mod-diff.md`` "The views".
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QPlainTextEdit, QTableView

from ..infra.i18n import N_, tr
from .model import (
    ADDED,
    ASSET,
    BLOCK,
    BYTES,
    CONFLICT,
    DUPLICATE,
    FILE,
    GONE,
    LATE_PHASE,
    NAME_ORDER,
    NOTED,
    ORDER,
    OVERLAP,
    REMOVED,
    SET_VALUE,
    VALUE,
    VALUES,
    Conflict,
)

KINDS = {
    VALUE: N_("moddiff.same_value"), BLOCK: N_("ui.block"), GONE: N_("moddiff.removed_used"), DUPLICATE: N_("moddiff.added_twice"),
    FILE: N_("moddiff.same_file"), ASSET: N_("moddiff.same_asset"),
}  # fmt: skip
NO_PARENT = QModelIndex()  # the default parent of a model's questions: the top level
LEVELS = {CONFLICT: N_("moddiff.conflict"), OVERLAP: N_("moddiff.overlap")}
# ``LoadAfter`` is the loader's own word and stays
DECIDED = {
    LATE_PHASE: N_("moddiff.late_phase"),
    NAME_ORDER: N_("moddiff.name_order"),
    ORDER: N_("moddiff.load_order"),
}
NOTE_WORDS = {ADDED: N_("moddiff.added"), REMOVED: N_("moddiff.removed"), SET_VALUE: N_("moddiff.set_value")}
NOTE_COUNTS = {VALUES: N_("moddiff.n_values"), BYTES: N_("viewer.n_bytes")}
HEADERS = (
    N_("common.level"),
    N_("ui.kind"),
    N_("common.target"),
    N_("islands.path"),
    N_("moddiff.mods_values_load_order"),
    N_("moddiff.winner"),
    N_("moddiff.decided_by"),
)


def shown_value(kind: str, value: str | None) -> str:
    """One mod's value of a conflict of ``kind``: the game's data as it is (none: ``-``), a note of ours (``added``,
    ``3 value(s)``, ``12 bytes``) in the interface's language."""
    if value is None:
        return "-"
    if kind not in NOTED:
        return value
    if value in NOTE_WORDS:
        return tr(NOTE_WORDS[value])
    count, _, unit = value.partition(" ")
    return tr(NOTE_COUNTS[unit], n=count) if unit in NOTE_COUNTS and count.isdigit() else value


def values_text(conflict: Conflict) -> str:
    """Each mod's value in load order: ``mod: value`` joined by a semicolon (removed: ``-``)."""
    shown = (shown_value(conflict.kind, v) for v in conflict.values)
    return "; ".join(f"{m}: {v}" for m, v in zip(conflict.mods, shown, strict=True))


def cells(c: Conflict) -> list[str]:
    """The seven columns of one conflict."""
    target = c.file if not c.key else f"{c.key} {c.name}".strip()
    return [
        tr(LEVELS.get(c.level, c.level)),
        tr(KINDS[c.kind]),
        target,
        c.path,
        values_text(c),
        c.winner,
        tr(DECIDED[c.decided]) if c.decided in DECIDED else c.decided,
    ]


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
        self.setPlainText("\n".join([*dropped, *notes]) or tr("moddiff.nothing_loader_drop"))
