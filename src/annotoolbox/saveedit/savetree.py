"""The Tree tab's model (Qt): a save's ``nodes.CItem`` tree, a level made when it is opened, off the window's thread
(a 117 session's document takes seconds to read). Read only.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt, Signal

from ..infra.i18n import N_, tr
from . import nodes

HEADERS = (N_("common.name"), N_("saves.bytes"), N_("common.value"))


class CSaveTreeModel(QAbstractItemModel):
    loading = Signal(str)  # the path of an item whose children are being read ("" when done)

    def __init__(self, background, is_synchronous: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.m_background, self.m_is_synchronous = background, is_synchronous
        self.m_top: list[nodes.CItem] = []
        self._m_pending: set[int] = set()
        self._m_shown: dict[int, list[nodes.CItem]] = {}  # an item's children once the view was told of them

    def set_roots(self, items: list[nodes.CItem]) -> None:
        self.beginResetModel()
        self.m_top, self._m_pending, self._m_shown = items, set(), {}
        self.endResetModel()

    def item(self, index: QModelIndex) -> nodes.CItem | None:
        return index.internalPointer() if index.isValid() else None

    def _rows(self, parent: nodes.CItem | None) -> list[nodes.CItem]:
        return self.m_top if parent is None else self._m_shown.get(id(parent), [])

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:  # noqa: B008
        rows = self._rows(self.item(parent))
        return self.createIndex(row, column, rows[row]) if 0 <= row < len(rows) else QModelIndex()

    def parent(self, index: QModelIndex = QModelIndex()):  # noqa: B008
        item = self.item(index)
        parent = item.m_parent if item is not None else None
        if parent is None:
            return QModelIndex()
        siblings = self._rows(parent.m_parent)
        return self.createIndex(next(i for i, sibling in enumerate(siblings) if sibling is parent), 0, parent)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.column() > 0 else len(self._rows(self.item(parent)))

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return len(HEADERS)

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:  # noqa: B008
        item = self.item(parent)
        return True if item is None else item.has_children()

    def canFetchMore(self, parent: QModelIndex) -> bool:
        item = self.item(parent)
        if item is None or not item.has_children():
            return False
        key = id(item)
        return key not in self._m_shown and key not in self._m_pending

    def fetchMore(self, parent: QModelIndex) -> None:
        item = self.item(parent)
        if item is None or id(item) in self._m_pending:
            return
        self._m_pending.add(id(item))
        keep = QPersistentModelIndex(parent)
        self.loading.emit(item.path())
        self.m_background.run(
            item.children, lambda kids: self._fetched(keep, item, kids), is_waiting=self.m_is_synchronous
        )

    def _fetched(self, keep: QPersistentModelIndex, item: nodes.CItem, kids) -> None:
        self._m_pending.discard(id(item))
        self.loading.emit("")
        if not keep.isValid():
            return  # the tree was replaced while it read
        if isinstance(kids, Exception) or not kids:
            if isinstance(kids, Exception):
                item.m_error = str(kids)
            self._m_shown[id(item)] = []
            self.dataChanged.emit(QModelIndex(keep), QModelIndex(keep))
            return
        self.beginInsertRows(QModelIndex(keep), 0, len(kids) - 1)
        self._m_shown[id(item)] = kids
        self.endInsertRows()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        item = self.item(index)
        if item is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return item.m_name
            if index.column() == 1:
                return "" if item.m_size is None else f"{item.m_size:,}"
            return item.m_error or item.m_preview
        if role == Qt.ItemDataRole.ToolTipRole and item.m_error:
            return item.m_error
        return None

    def headerData(self, section: int, orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(HEADERS[section])
        return None

    def index_of(self, item: nodes.CItem) -> QModelIndex:
        """The index of an item already made (the search's hits)."""
        siblings = self._rows(item.m_parent)
        row = next((i for i, sibling in enumerate(siblings) if sibling is item), -1)
        return self.createIndex(row, 0, item) if row >= 0 else QModelIndex()
