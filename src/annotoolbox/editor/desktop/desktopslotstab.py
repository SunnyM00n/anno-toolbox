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
from .desktopview import fly_to

if TYPE_CHECKING:
    from .desktopeditor import CIslandDoc

SLOT_GAMES = ("anno117", "anno1800")  # the games whose islands the tab lists (their slot assets: ``slotkinds``)


class CSlotsPanel(QWidget):
    """The slot objects of the open island; ``fill`` reads them again (the objects list calls it when it refills)."""

    def __init__(self, doc: CIslandDoc) -> None:
        super().__init__()
        self.m_doc = doc
        self.m_rows: list[slotkinds.SSlotRow] = []
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.m_search = QLineEdit(placeholderText=tr("islands.search_name_guid_template"), clearButtonEnabled=True)
        self.m_kinds = QComboBox()
        self.m_links = QPushButton(tr("islands.slot_links"))
        bar.addWidget(self.m_search, 1)
        bar.addWidget(self.m_kinds)
        layout.addLayout(bar)
        self.m_note = QLabel(wordWrap=True)
        layout.addWidget(self.m_note)
        self.m_tree = QTreeWidget()
        self.m_tree.setRootIsDecorated(False)
        self.m_tree.setHeaderLabels(
            [tr("islands.slot_kind"), tr("common.name"), tr("islands.slot_guid"), tr("islands.slot_position")]
        )
        layout.addWidget(self.m_tree, 1)
        layout.addWidget(self.m_links)
        self.m_search.textChanged.connect(self.show_rows)
        self.m_kinds.currentIndexChanged.connect(self.show_rows)
        self.m_tree.itemClicked.connect(self._click)
        self.m_links.clicked.connect(lambda _c=False: desktopslotlinks.show(self.m_doc))

    def fill(self) -> None:
        """The slots again; a game whose islands list none says so."""
        game = self.m_doc.game_key()
        self.m_links.setVisible(game in desktopslotlinks.OTHER_GAME)
        self.m_rows = []
        note = tr("islands.no_slots_game")
        if game in SLOT_GAMES:
            try:
                self.m_rows = slotkinds.rows(self.m_doc.m_session.m_entries, self.m_doc.m_editor.slot_assets())
                note = "" if self.m_rows else tr("islands.no_slots_island")
            except Exception as exc:  # a game without its assets: no slots to list, not a crash
                note = tr("islands.no_game_assets_buildings_draw", error=exc)
        self.m_note.setText(note)
        kept = self.m_kinds.currentData()
        self.m_kinds.blockSignals(True)
        self.m_kinds.clear()
        self.m_kinds.addItem(tr("islands.all_kinds"), "")
        for kind in sorted({row.asset.kind for row in self.m_rows}):
            self.m_kinds.addItem(kind, kind)
        self.m_kinds.setCurrentIndex(max(0, self.m_kinds.findData(kept)))
        self.m_kinds.blockSignals(False)
        self.show_rows()

    def show_rows(self) -> None:
        text, kind = self.m_search.text(), self.m_kinds.currentData() or ""
        self.m_tree.clear()
        for row in self.m_rows:
            if not slotkinds.matches(row, text, kind):
                continue
            x, _y, z = row.position
            item = QTreeWidgetItem(
                [row.asset.kind, row.asset.name, str(row.asset.guid), f"x {x:.1f}  z {z:.1f}"]  # i18n: ok (axis names)
            )
            item.setData(0, Qt.ItemDataRole.UserRole, row.entry)
            self.m_tree.addTopLevelItem(item)
        for column in range(self.m_tree.columnCount()):
            self.m_tree.resizeColumnToContents(column)

    def _click(self, item) -> None:
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if entry is None or not entry.alive:
            return
        fly_to(self.m_doc.m_view, [entry])
