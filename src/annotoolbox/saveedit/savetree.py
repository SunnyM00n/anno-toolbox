"""The Tree tab's model (Qt): a save's ``nodes.Item`` tree, a level made when it is opened, off the window's thread
(a 117 session's document takes seconds to read). Read only.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QPersistentModelIndex, Qt, Signal

from ..infra.i18n import N_, tr
from . import nodes

HEADERS = (N_("Name"), N_("Bytes"), N_("Value"))


class SaveTreeModel(QAbstractItemModel):
    loading = Signal(str)  # the path of an item whose children are being read ("" when done)

    def __init__(self, background, sync: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.background, self.sync = background, sync
        self.top: list[nodes.Item] = []
        self._pending: set[int] = set()
        self._shown: dict[int, list[nodes.Item]] = {}  # an item's children once the view was told of them

    def set_roots(self, items: list[nodes.Item]) -> None:
        self.beginResetModel()
        self.top, self._pending, self._shown = items, set(), {}
        self.endResetModel()

    def item(self, index: QModelIndex) -> nodes.Item | None:
        return index.internalPointer() if index.isValid() else None

    def _rows(self, parent: nodes.Item | None) -> list[nodes.Item]:
        return self.top if parent is None else self._shown.get(id(parent), [])

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:  # noqa: B008
        rows = self._rows(self.item(parent))
        return self.createIndex(row, column, rows[row]) if 0 <= row < len(rows) else QModelIndex()

    def parent(self, index: QModelIndex = QModelIndex()):  # noqa: B008
        item = self.item(index)
        up = item.parent if item is not None else None
        if up is None:
            return QModelIndex()
        siblings = self._rows(up.parent)
        return self.createIndex(next(i for i, s in enumerate(siblings) if s is up), 0, up)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.column() > 0 else len(self._rows(self.item(parent)))

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return len(HEADERS)

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:  # noqa: B008
        item = self.item(parent)
        return True if item is None else item.has_children()

    def canFetchMore(self, parent: QModelIndex) -> bool:
        item = self.item(parent)
        return (
            item is not None and item.has_children() and id(item) not in self._shown and id(item) not in self._pending
        )

    def fetchMore(self, parent: QModelIndex) -> None:
        item = self.item(parent)
        if item is None or id(item) in self._pending:
            return
        self._pending.add(id(item))
        keep = QPersistentModelIndex(parent)
        self.loading.emit(item.path())
        self.background.run(item.children, lambda kids: self._fetched(keep, item, kids), wait=self.sync)

    def _fetched(self, keep: QPersistentModelIndex, item: nodes.Item, kids) -> None:
        self._pending.discard(id(item))
        self.loading.emit("")
        if not keep.isValid():
            return  # the tree was replaced while it read
        if isinstance(kids, Exception) or not kids:
            if isinstance(kids, Exception):
                item.error = str(kids)
            self._shown[id(item)] = []
            self.dataChanged.emit(QModelIndex(keep), QModelIndex(keep))
            return
        self.beginInsertRows(QModelIndex(keep), 0, len(kids) - 1)
        self._shown[id(item)] = kids
        self.endInsertRows()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        item = self.item(index)
        if item is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return item.name
            if index.column() == 1:
                return "" if item.size is None else f"{item.size:,}"
            return item.error or item.preview
        if role == Qt.ItemDataRole.ToolTipRole and item.error:
            return item.error
        return None

    def headerData(self, section: int, orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(HEADERS[section])
        return None

    def index_of(self, item: nodes.Item) -> QModelIndex:
        """The index of an item already made (the search's hits)."""
        siblings = self._rows(item.parent)
        row = next((i for i, s in enumerate(siblings) if s is item), -1)
        return self.createIndex(row, 0, item) if row >= 0 else QModelIndex()
