"""The installed mods as a tree (Qt): a check box switches a mod on or off in the open profile, the mods nested inside
a mod hang below it, and a mod the loader would not take shows why in grey. ``CModTree`` is the entry; ``docs/mod-manager.md`` "The window".
"""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPalette
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

from ..infra.i18n import N_, tr
from ..modloader.loadlog import SLoadLog
from ..modloader.profilefile import CProfileText
from . import catalog
from .catalog import SModCard
from .checks import PROBLEM, SPlan

COLUMNS = (
    N_("mods.mod"),
    N_("mods.modid"),
    N_("common.version"),
    N_("mods.category"),
    N_("mods.found_in"),
    N_("mods.in_the_profile"),
    N_("mods.problems"),
)
CARD = Qt.ItemDataRole.UserRole
PROBLEM_COLOR = QColor("#c0392b")


class CModTree(QTreeWidget):
    toggled = Signal(str, bool)  # ModID, on
    selected = Signal(object)  # the SModCard (None when nothing is selected)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setColumnCount(len(COLUMNS))
        self.setHeaderLabels([tr(column) for column in COLUMNS])
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.header().setStretchLastSection(True)
        self.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self.itemChanged.connect(self._changed)
        self.currentItemChanged.connect(lambda item, _old: self.selected.emit(item.data(0, CARD) if item else None))
        self._m_filling = False

    def set_data(
        self, cat: catalog.SCatalog, text: CProfileText | None, plan: SPlan | None, log: SLoadLog | None, editable: bool
    ) -> None:
        """Show ``cat`` with the state ``text`` gives each mod; ``editable`` False (no profile open) shows the boxes
        without letting them change."""
        keep = self.current_card()
        self._m_filling = True
        blocker = QSignalBlocker(self)
        try:
            self.clear()
            for card in cat.cards:
                self._add(None, card, text, plan, log, editable)
            self.expandAll()
        finally:
            blocker.unblock()
            self._m_filling = False
        if keep is not None:
            self.select_folder(keep.folder)

    def current_card(self) -> SModCard | None:
        item = self.currentItem()
        return item.data(0, CARD) if item else None

    def select_folder(self, folder) -> None:
        for item in self._items():
            card = item.data(0, CARD)
            if card is not None and card.folder == folder:
                self.setCurrentItem(item)
                return

    def _items(self):
        stack = [self.topLevelItem(index) for index in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop(0)
            yield item
            stack += [item.child(index) for index in range(item.childCount())]

    def _add(self, parent, card: SModCard, text, plan, log, editable: bool) -> None:
        nested = parent is not None
        state = catalog.state_of(card, text)
        item = QTreeWidgetItem(parent if nested else self)
        item.setData(0, CARD, card)
        is_on = state[0] == catalog.ON
        reason = _reason(card, state, nested, parent)
        found = [
            card.name,
            card.mod_id,
            card.version,
            card.category,
            catalog.root_label(card.root),
            reason,
            self._problems(card, plan, log),
        ]
        for column, value in enumerate(found):
            item.setText(column, value)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if card.usable and not nested:
            flags |= Qt.ItemFlag.ItemIsUserCheckable if editable else Qt.ItemFlag(0)
            item.setCheckState(0, Qt.CheckState.Checked if is_on else Qt.CheckState.Unchecked)
        item.setFlags(flags)
        if not card.usable or nested:
            self._dim(item, nested and card.usable)
        if item.text(6):
            item.setForeground(6, QBrush(PROBLEM_COLOR))
        for child in card.children:
            self._add(item, child, text, plan, log, editable)

    def _dim(self, item: QTreeWidgetItem, italic_only: bool) -> None:
        font = QFont(item.font(0))
        font.setItalic(True)
        for column in range(len(COLUMNS)):
            item.setFont(column, font)
            if not italic_only:
                item.setForeground(
                    column, QBrush(self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))
                )

    @staticmethod
    def _problems(card: SModCard, plan: SPlan | None, log: SLoadLog | None) -> str:
        count = len([i for i in (plan.issues_of(card.mod_id) if plan and card.mod_id else []) if i.level == PROBLEM])
        run = log.run_of(card.mod_id) if log and card.mod_id else None
        said = len(run.problems) if run else 0
        parts = ([tr("mods.n_to_fix", n=count)] if count else []) + ([tr("mods.n_in_the_log", n=said)] if said else [])
        return ", ".join(parts)

    def _changed(self, item: QTreeWidgetItem, column: int) -> None:
        card = item.data(0, CARD)
        if self._m_filling or column != 0 or card is None or not card.usable or item.parent() is not None:
            return
        self.toggled.emit(card.mod_id, item.checkState(0) == Qt.CheckState.Checked)


def _reason(card: SModCard, state: tuple[str, str], nested: bool, parent) -> str:
    if card.problem:
        return card.problem
    if card.parked:
        return tr("mods.parked")
    if card.duplicate_of:
        return tr("mods.second_copy_left_out")
    if nested:
        return tr("mods.with_id", id=parent.data(0, CARD).mod_id)
    is_on, how = state
    word = tr("mods.on") if is_on == catalog.ON else tr("mods.off")
    return {catalog.LISTED: word, catalog.NEW: tr("mods.state_enablenewmods", state=word)}.get(
        how, tr("mods.state_not_listed", state=word)
    )
