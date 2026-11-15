"""The form of the selected emitter (or material, curve, config block): its fields in file order, each edited the way
its kind needs - a switch ticks, a closed list is a drop-down, a number or a name is typed, a value made with the rest
of the file is shown greyed - with what it does as its tool tip.

``FieldForm.show_rows(rows, read_only)`` fills it with ``outline.Row`` objects; ``edited(row, text)`` is a change the
user made, ``open_file(name)`` a file name double-clicked. docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QHeaderView, QTreeWidget, QTreeWidgetItem

from ..infra.i18n import tr
from .fieldinfo import CHOICE, FILE, FIXED, SWITCH

ROW = Qt.ItemDataRole.UserRole + 1
LINK = "#4f9fd9"


class FieldForm(QTreeWidget):
    edited = Signal(object, str)
    open_file = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.read_only = False
        self.setColumnCount(2)
        self.setHeaderLabels([tr("field"), tr("value")])
        self.setRootIsDecorated(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.header().setStretchLastSection(True)
        self.setEditTriggers(QTreeWidget.EditTrigger.DoubleClicked | QTreeWidget.EditTrigger.EditKeyPressed)
        self.itemChanged.connect(self._changed)
        self.itemDoubleClicked.connect(self._double)

    def show_rows(self, rows: list, read_only: bool = False) -> None:
        self.read_only = read_only
        self.blockSignals(True)
        self.clear()
        for row in rows:
            item = self._item(row)
            self.addTopLevelItem(item)
            if row.info.kind == CHOICE and row.info.choices:
                self.setItemWidget(item, 1, self._combo(row))
        self.blockSignals(False)

    def _item(self, row) -> QTreeWidgetItem:
        kind = row.info.kind
        item = QTreeWidgetItem([row.name, "" if kind == SWITCH else row.value])
        item.setData(0, ROW, row)
        flags = item.flags()
        if kind == SWITCH and row.value in ("0", "1"):
            flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setCheckState(1, Qt.CheckState.Checked if row.value == "1" else Qt.CheckState.Unchecked)
        elif kind == SWITCH:
            item.setText(1, row.value)
            flags |= Qt.ItemFlag.ItemIsEditable
        elif kind not in (FIXED, CHOICE):
            flags |= Qt.ItemFlag.ItemIsEditable
        if self.read_only or kind == FIXED:
            flags &= ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
        if kind == FIXED:
            item.setForeground(1, QColor("#888888"))
        if kind == FILE:
            item.setForeground(1, QColor(LINK))
        item.setFlags(flags)
        tip = tr(row.info.meaning) if row.info.meaning else tr("not described")
        item.setToolTip(0, tip)
        item.setToolTip(1, tip if kind != FILE else tr("{meaning}; double-click the name: open it in the viewer",
                                                       meaning=tip))  # fmt: skip
        return item

    def _combo(self, row) -> QComboBox:
        combo = QComboBox()
        for value, label in row.info.choices:
            combo.addItem(f"{value}  {tr(label)}", value)
        at = combo.findData(row.value.strip())
        if at < 0:
            combo.addItem(tr("{value} (not in the list)", value=row.value), row.value)
            at = combo.count() - 1
        combo.setCurrentIndex(at)
        combo.setEnabled(not self.read_only)
        combo.activated.connect(lambda k, c=combo, r=row: self._chosen(r, c.itemData(k)))
        return combo

    def _chosen(self, row, value: str) -> None:
        if value != row.value:
            self.edited.emit(row, value)

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        row = item.data(0, ROW)
        if row is None or column != 1:
            return
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            value = "1" if item.checkState(1) == Qt.CheckState.Checked else "0"
        else:
            value = item.text(1).strip()
        if value != row.value:
            self.edited.emit(row, value)

    def _double(self, item: QTreeWidgetItem, column: int) -> None:
        row = item.data(0, ROW)
        if row is not None and column == 0 and row.info.kind == FILE and row.value.strip():
            self.open_file.emit(row.value.strip())
