"""The asset tree: the game's assets by their file groups or by template, the change set's new assets on top, a
search by GUID, name or template with "find next", a template filter and a value filter. A group's assets are
listed when it is first opened (ToolOne's lazy groups), so the half-gigabyte games open at once.

``CAssetTree.set_doc(doc)`` fills it, ``picked`` says which asset was chosen, ``select(guid)`` shows one.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..suite.background import CBackground
from . import filters
from .assetdoc import CAssetDocument
from .icons import CIconSource
from .origins import MOD_CHANGED, MOD_NEW

ICON_ROWS = 300  # rows a fill or an opened group gives an icon (each is a file read and decoded once, off-thread)
SEARCH_DELAY = 250  # ms the search waits after a key before it fills the tree
SHOWN = 2000  # rows a search or a filter lists at once
FILTERED = 20000  # assets a value filter looks at (it resolves each one's layers)
GROUPS, TEMPLATES = N_("assets.groups"), N_("assets.templates")
ORIGIN_MARKS = {MOD_NEW: N_("assets.mark_mod_new"), MOD_CHANGED: N_("assets.mark_mod_changed")}  # a row's suffix
ORIGIN_NOTES = {  # a row's tooltip line: where the asset comes from (the game's own and the person's say nothing)
    MOD_NEW: N_("assets.origin_mod_new"),
    MOD_CHANGED: N_("assets.origin_mod_changed"),
}
GUID_ROLE = Qt.ItemDataRole.UserRole
MEMBERS_ROLE = Qt.ItemDataRole.UserRole + 1  # every asset of a group row, its subgroups' too
OWN_ROLE = Qt.ItemDataRole.UserRole + 2  # the assets a group row holds itself
KEY_ROLE = Qt.ItemDataRole.UserRole + 3  # the group's path (a tuple of names)


class CAssetTree(QWidget):
    picked = Signal(object)  # a GUID (may exceed a Qt int)

    def __init__(self) -> None:
        super().__init__()
        self.m_doc: CAssetDocument | None = None
        self._m_groups: dict[tuple, dict] = {}  # per group path: its own assets, all its assets, its subgroups' names
        self.m_icons: CIconSource | None = None  # set by the window: rows get the icon their asset names
        self._m_icon_budget = ICON_ROWS
        self._m_wanted: list[tuple[QTreeWidgetItem, str]] = []  # rows that wait for the icon of their key
        self._m_generation = 0  # raised when the tree is cleared: icons that come late are dropped
        self._m_background = CBackground(self)
        self._m_typing = QTimer(self, singleShot=True, interval=SEARCH_DELAY)
        layout = QVBoxLayout(self)
        self.m_search = QLineEdit(placeholderText=tr("assets.search_guid_name_template_enter"))
        row = QHBoxLayout()
        self.m_mode = QComboBox()
        for key in (GROUPS, TEMPLATES):
            self.m_mode.addItem(tr(key), key)
        self.m_template = QComboBox(editable=True)
        self.m_template.setToolTip(tr("assets.tip.only_assets_template"))
        row.addWidget(QLabel(tr("assets.by")))
        row.addWidget(self.m_mode)
        row.addWidget(self.m_template, 1)
        self.m_value_filter = QLineEdit(placeholderText=tr("assets.value_filter_building_upkeep_gt"))
        self.m_tree = QTreeWidget()
        self.m_tree.setHeaderLabels([tr("common.asset"), tr("assets.guid")])
        self.m_tree.setColumnWidth(0, 260)
        self.m_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)  # several assets to cut or copy
        self.m_count = QLabel()
        for widget in (self.m_search, row, self.m_value_filter, self.m_tree, self.m_count):
            (layout.addLayout if isinstance(widget, QHBoxLayout) else layout.addWidget)(widget)
        self.m_search.returnPressed.connect(self.find_next)
        self._m_typing.timeout.connect(self.fill)
        self.m_search.textChanged.connect(lambda _t: self._m_typing.start())
        self.m_mode.currentIndexChanged.connect(lambda _i: self.fill())
        self.m_template.currentTextChanged.connect(lambda _t: self.fill())
        self.m_value_filter.returnPressed.connect(self.fill)
        self.m_tree.itemExpanded.connect(self._expand)
        self.m_tree.currentItemChanged.connect(self._current)

    def set_doc(self, doc: CAssetDocument | None) -> None:
        self.m_doc = doc
        self.m_template.blockSignals(True)
        self.m_template.clear()
        self.m_template.addItem("")
        if doc is not None:
            self.m_template.addItems(sorted({entry.template for entry in doc.m_index.m_entries if entry.template}))
            named = any(group for group in doc.m_index.m_groups)
            self.m_mode.setCurrentIndex(
                self.m_mode.findData(GROUPS if named else TEMPLATES)
            )  # 117's export names no groups
        self.m_template.blockSignals(False)
        self.fill()

    def _row(self, guid: int, icon: bool = True) -> QTreeWidgetItem:
        changes = self.m_doc.m_changes
        mark = " *" if guid in changes.m_new or (guid in changes.m_assets and not changes.m_assets[guid].empty) else ""
        origin = self.m_doc.origin(guid)
        mark += tr(ORIGIN_MARKS[origin]) if origin in ORIGIN_MARKS else ""
        item = QTreeWidgetItem([f"{self.m_doc.name(guid) or tr('assets.no_name')}{mark}", str(guid)])
        item.setData(0, GUID_ROLE, guid)
        note = f"{chr(10)}{tr(ORIGIN_NOTES[origin])}" if origin in ORIGIN_NOTES else ""
        item.setToolTip(0, self.m_doc.template(guid) + note)
        if icon and self.m_icons is not None and self._m_icon_budget > 0:
            key = self.m_icons.key_of(self.m_doc, guid)
            if key:
                self._m_wanted.append((item, key))
            self._m_icon_budget -= 1
        return item

    def _load_icons(self) -> None:
        """Decode the wanted rows' icons off the window's thread; each row gets its icon when its picture is in."""
        wanted, self._m_wanted, generation, source = self._m_wanted, [], self._m_generation, self.m_icons
        if not wanted or source is None:
            return

        def work():
            return [source.image(key) for _item, key in wanted]

        def done(images) -> None:
            if isinstance(images, Exception) or generation != self._m_generation:
                return
            for (item, _key), image in zip(wanted, images, strict=True):
                icon = source.icon(image)
                try:
                    if icon is not None:
                        item.setIcon(0, icon)
                except RuntimeError:  # the row was removed meanwhile
                    return

        self._m_background.run(work, done)

    def _entries(self) -> list[int]:
        """The assets the filters let through, in the index's order (the new ones first)."""
        doc, template = self.m_doc, self.m_template.currentText().strip()
        removed = set(doc.m_changes.m_removed)
        guids = list(doc.m_changes.m_new) + [entry.guid for entry in doc.m_index.m_entries if entry.guid not in removed]
        if template:
            guids = [guid for guid in guids if doc.template(guid) == template]
        value_filter = filters.parse(self.m_value_filter.text())
        if value_filter is not None:
            guids = [guid for guid in guids[:FILTERED] if value_filter.matches(doc, guid)]
        return guids

    def fill(self) -> None:
        self._m_typing.stop()
        self.m_tree.clear()
        self._m_generation += 1
        self._m_wanted = []
        self._m_icon_budget = ICON_ROWS
        self._fill_rows()
        self._load_icons()

    def _fill_rows(self) -> None:
        if self.m_doc is None:
            self.m_count.setText(tr("assets.no_game_open"))
            return
        query = self.m_search.text().strip()
        if query or filters.parse(self.m_value_filter.text()) is not None:
            return self._flat(query)
        guids = self._entries()
        if self.m_doc.m_changes.m_new:
            top = QTreeWidgetItem([tr("assets.new_change_set", n=len(self.m_doc.m_changes.m_new)), ""])
            top.addChildren([self._row(guid) for guid in self.m_doc.m_changes.m_new if guid in guids])
            self.m_tree.addTopLevelItem(top)
            top.setExpanded(True)
        by_template = self.m_mode.currentData() == TEMPLATES
        self._m_groups = self._bucket([guid for guid in guids if guid not in self.m_doc.m_changes.m_new], by_template)
        for key in sorted(k for k in self._m_groups if len(k) == 1):
            self.m_tree.addTopLevelItem(self._group_item(key))
        self.m_count.setText(
            tr("assets.n_assets_count_templates", n=len(guids), count=len(self._m_groups))
            if by_template
            else tr("assets.n_assets_count_groups", n=len(guids), count=len(self._m_groups))
        )

    def _bucket(self, guids: list[int], by_template: bool) -> dict[tuple, dict]:
        """The groups of these assets: by template one flat level, else the file groups' tree (a group's path is the
        names from the root, the files its groups name hanging below them)."""
        out: dict[tuple, dict] = {}

        def node(key: tuple) -> dict:
            if key not in out:
                out[key] = {"own": [], "all": [], "sub": set()}
                if len(key) > 1:
                    node(key[:-1])["sub"].add(key[-1])
            return out[key]

        for guid in guids:
            entry = self.m_doc.m_index.m_by_guid[guid]
            path = (
                (entry.template or tr("assets.no_template"),)
                if by_template
                else self.m_doc.m_index.m_groups[entry.group] or (tr("assets.no_group"),)
            )
            node(path)["own"].append(guid)
            for k in range(1, len(path) + 1):
                node(path[:k])["all"].append(guid)
        return out

    def _group_item(self, key: tuple) -> QTreeWidgetItem:
        group = self._m_groups[key]
        item = QTreeWidgetItem([f"{key[-1]} ({len(group['all'])})", ""])
        item.setData(0, KEY_ROLE, list(key))
        item.setData(0, MEMBERS_ROLE, group["all"])
        item.setData(0, OWN_ROLE, group["own"])
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return item

    def _flat(self, query: str) -> None:
        doc = self.m_doc
        if query:
            hits = [entry.guid for entry in doc.m_index.search(query, limit=SHOWN)]
            lowered_query = query.lower()
            hits = [
                guid
                for guid in doc.m_changes.m_new
                if lowered_query in doc.name(guid).lower() or lowered_query == str(guid)
            ] + hits
            allowed = set(self._entries())
            hits = [guid for guid in hits if guid in allowed]
        else:
            hits = self._entries()
        self.m_tree.addTopLevelItems([self._row(guid) for guid in hits[:SHOWN]])
        more = " " + tr("assets.first_n_shown", n=SHOWN) if len(hits) > SHOWN else ""
        self.m_count.setText(tr("assets.n_found", n=len(hits)) + more)

    def mark(self, guids: set) -> None:
        """The rows of these assets say again whether the change set edits them (`` *``) and show their icon as it is
        now (an icon edit changes no row's place, so the tree is not filled again)."""
        stack = [self.m_tree.topLevelItem(k) for k in range(self.m_tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            stack += [item.child(i) for i in range(item.childCount())]
            if item.data(0, GUID_ROLE) in guids:
                item.setText(0, self._row(item.data(0, GUID_ROLE), icon=False).text(0))
                if self.m_icons is not None:
                    self.m_icons.forget(item.data(0, GUID_ROLE))
                key = self.m_icons.key_of(self.m_doc, item.data(0, GUID_ROLE)) if self.m_icons is not None else ""
                if key:
                    self._m_wanted.append((item, key))
                else:
                    item.setIcon(0, QIcon())
        self._load_icons()

    def _expand(self, item: QTreeWidgetItem) -> None:
        key = tuple(item.data(0, KEY_ROLE) or ())
        if key and not item.childCount():
            self._m_icon_budget = ICON_ROWS
            subs = sorted(self._m_groups[key]["sub"])
            item.addChildren([self._group_item((*key, name)) for name in subs])
            item.addChildren([self._row(guid) for guid in item.data(0, OWN_ROLE)])
            self._load_icons()

    def _current(self, item: QTreeWidgetItem | None, _old) -> None:
        guid = item.data(0, GUID_ROLE) if item is not None else None
        if guid is not None:
            self.picked.emit(guid)

    def find_next(self) -> None:
        """The next row below the current one (the search lists its hits flat)."""
        count = self.m_tree.topLevelItemCount()
        if not count:
            return
        current = self.m_tree.indexOfTopLevelItem(self.m_tree.currentItem()) if self.m_tree.currentItem() else -1
        self.m_tree.setCurrentItem(self.m_tree.topLevelItem((current + 1) % count))

    def select(self, guid: int) -> None:
        """Show ``guid`` in the tree (its groups opened), or as the only search hit when it is not listed."""
        for k in range(self.m_tree.topLevelItemCount()):
            top = self.m_tree.topLevelItem(k)
            if top.data(0, GUID_ROLE) == guid:
                return self.m_tree.setCurrentItem(top)
            if guid in self._members(top):
                found = self._reveal(top, guid)
                if found is not None:
                    return self.m_tree.setCurrentItem(found)
        self.m_search.setText(str(guid))
        self.fill()  # not after the typing delay: the caller wants the row now
        if self.m_tree.topLevelItemCount():
            self.m_tree.setCurrentItem(self.m_tree.topLevelItem(0))

    @staticmethod
    def _members(item: QTreeWidgetItem) -> list[int]:
        """Every asset below a row: a group row's (its subgroups' too), else the rows it has."""
        return item.data(0, MEMBERS_ROLE) or [
            item.child(i).data(0, GUID_ROLE) for i in range(item.childCount()) if item.child(i).data(0, GUID_ROLE)
        ]

    def _reveal(self, group: QTreeWidgetItem, guid: int) -> QTreeWidgetItem | None:
        """The row of ``guid`` below ``group``, the groups on the way opened."""
        group.setExpanded(True)
        children = [group.child(i) for i in range(group.childCount())]
        for child in children:
            if child.data(0, GUID_ROLE) == guid:
                return child
        for child in children:
            if child.data(0, GUID_ROLE) is None and guid in self._members(child):
                return self._reveal(child, guid)
        return None

    def selected_assets(self) -> list[int]:
        """The assets selected: those of the rows, and every asset of a selected group row."""
        out: list[int] = []
        for item in self.m_tree.selectedItems():
            for guid in [item.data(0, GUID_ROLE)] if item.data(0, GUID_ROLE) is not None else self._members(item):
                if guid is not None and guid not in out:
                    out.append(guid)
        return out

    def selected_group(self) -> list[int]:
        """The assets of the group the current row is in, its subgroups' too (the balancing view's rows)."""
        item = self.m_tree.currentItem()
        if item is None:
            return []
        group = item if item.data(0, GUID_ROLE) is None else item.parent() or item
        members = self._members(group)
        return list(members) or ([group.data(0, GUID_ROLE)] if group.data(0, GUID_ROLE) is not None else [])
