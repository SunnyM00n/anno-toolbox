"""The Slots tab of the objects list: the island's slot objects with kind, name, GUID and position, a search and a kind
filter; a click selects the slot and flies there, as a click in the objects list does. Beside it the button of the slot
links of the port between Anno 117 and Anno 1800 (``desktopslotlinks``). Plan and use: ``docs/island-editor.md``
"The Slots tab".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...infra.i18n import tr
from .. import slotkinds
from . import desktopslotlinks

if TYPE_CHECKING:
    from .desktopeditor import IslandDoc

SLOT_GAMES = ("anno117", "anno1800")  # the games whose islands the tab lists (their slot assets: ``slotkinds``)


class SlotsPanel(QWidget):
    """The slot objects of the open island; ``fill`` reads them again (the objects list calls it when it refills)."""

    def __init__(self, doc: IslandDoc) -> None:
        super().__init__()
        self.doc = doc
        self.rows: list[slotkinds.SlotRow] = []
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit(placeholderText=tr("islands.search_name_guid_template"), clearButtonEnabled=True)
        self.kinds = QComboBox()
        self.links = QPushButton(tr("islands.slot_links"))
        bar.addWidget(self.search, 1)
        bar.addWidget(self.kinds)
        layout.addLayout(bar)
        self.note = QLabel(wordWrap=True)
        layout.addWidget(self.note)
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels(
            [tr("islands.slot_kind"), tr("common.name"), tr("islands.slot_guid"), tr("islands.slot_position")]
        )
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.links)
        self.search.textChanged.connect(self.show_rows)
        self.kinds.currentIndexChanged.connect(self.show_rows)
        self.tree.itemClicked.connect(self._click)
        self.links.clicked.connect(lambda _c=False: desktopslotlinks.show(self.doc))

    def fill(self) -> None:
        """The slots again; a game whose islands list none says so."""
        game = self.doc.game_key()
        self.links.setVisible(game in desktopslotlinks.OTHER_GAME)
        self.rows = []
        note = tr("islands.no_slots_game")
        if game in SLOT_GAMES:
            try:
                self.rows = slotkinds.rows(self.doc.session.entries, self.doc.editor.slot_assets())
                note = "" if self.rows else tr("islands.no_slots_island")
            except Exception as exc:  # a game without its assets: no slots to list, not a crash
                note = tr("islands.no_game_assets_buildings_draw", error=exc)
        self.note.setText(note)
        kept = self.kinds.currentData()
        self.kinds.blockSignals(True)
        self.kinds.clear()
        self.kinds.addItem(tr("islands.all_kinds"), "")
        for kind in sorted({r.asset.kind for r in self.rows}):
            self.kinds.addItem(kind, kind)
        self.kinds.setCurrentIndex(max(0, self.kinds.findData(kept)))
        self.kinds.blockSignals(False)
        self.show_rows()

    def show_rows(self) -> None:
        text, kind = self.search.text(), self.kinds.currentData() or ""
        self.tree.clear()
        for row in self.rows:
            if not slotkinds.matches(row, text, kind):
                continue
            x, _y, z = row.position
            item = QTreeWidgetItem(
                [row.asset.kind, row.asset.name, str(row.asset.guid), f"x {x:.1f}  z {z:.1f}"]  # i18n: ok (axis names)
            )
            item.setData(0, Qt.ItemDataRole.UserRole, row.entry)
            self.tree.addTopLevelItem(item)
        for column in range(self.tree.columnCount()):
            self.tree.resizeColumnToContents(column)

    def _click(self, item) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        view = self.doc.view
        if entry is None or not entry.alive:
            return
        if view.tool != "select":
            view.set_tool("select")
        view.select(entry)
        view.focus_on([entry])
