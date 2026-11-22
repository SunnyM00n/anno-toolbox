"""The rows of the Texts window as a table model: one row per text id, the columns the person reads (the id or the editor's
name, the status, the source, the target). Rows are asked of the ``CTextDoc`` as they are drawn, so a language of
forty thousand texts is as light as one of forty.

``CRowsModel(doc)``; ``show(ids)`` replaces the rows, ``refresh(key)`` redraws one after an edit, ``key_at(row)``.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..infra.i18n import N_, tr
from .textdoc import CHANGED, NEW, OUTDATED, STATUS_NAMES, UNTRANSLATED, CTextDoc

COLUMNS = (N_("texts.id"), N_("texts.status"), N_("texts.source"), N_("common.target"))
TINTS = {UNTRANSLATED: QColor(230, 150, 40, 60), OUTDATED: QColor(220, 200, 40, 70), CHANGED: QColor(70, 170, 90, 55),
         NEW: QColor(70, 130, 220, 55)}  # fmt: skip
SHOWN = 120  # characters of a text a cell shows


class CRowsModel(QAbstractTableModel):
    def __init__(self, doc: CTextDoc | None = None, parent=None) -> None:
        super().__init__(parent)
        self.m_doc, self.m_ids = doc, []

    def show(self, ids: list[str]) -> None:
        self.beginResetModel()
        self.m_ids = ids
        self.endResetModel()

    def key_at(self, row: int) -> str:
        return self.m_ids[row] if 0 <= row < len(self.m_ids) else ""

    def row_of(self, key: str) -> int:
        try:
            return self.m_ids.index(key)
        except ValueError:
            return -1

    def refresh(self, key: str) -> None:
        row = self.row_of(key)
        if row >= 0:
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: B008  (Qt's signature)
        return 0 if parent.isValid() else len(self.m_ids)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return tr(COLUMNS[section])
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.m_doc is None:
            return None
        key, column = self.m_ids[index.row()], index.column()
        if role == Qt.ItemDataRole.BackgroundRole:
            return TINTS.get(self.m_doc.status(key))
        if role == Qt.ItemDataRole.ToolTipRole and column >= 2:
            return (self.m_doc.source_text(key) if column == 2 else self.m_doc.target_text(key))[:1500]
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == 0:
            entry = self.m_doc.entry(key)
            return f"{key}  {entry.name}" if entry is not None and entry.name else key
        if column == 1:
            return tr(STATUS_NAMES[self.m_doc.status(key)])
        text = self.m_doc.source_text(key) if column == 2 else self.m_doc.target_text(key)
        return text.replace("\n", " ↵ ")[:SHOWN]
