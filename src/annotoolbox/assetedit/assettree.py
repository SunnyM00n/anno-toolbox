"""The asset tree: the game's assets by their file groups or by template, the change set's new assets on top, a
search by GUID, name or template with "find next", a template filter and a value filter. A group's assets are
listed when it is first opened (ToolOne's lazy groups), so the half-gigabyte games open at once.

``AssetTree.set_doc(doc)`` fills it, ``picked`` says which asset was chosen, ``select(guid)`` shows one.
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
from ..suite.background import Background
from . import filters
from .assetdoc import AssetDocument
from .icons import IconSource

ICON_ROWS = 300  # rows a fill or an opened group gives an icon (each is a file read and decoded once, off-thread)
SEARCH_DELAY = 250  # ms the search waits after a key before it fills the tree
SHOWN = 2000  # rows a search or a filter lists at once
FILTERED = 20000  # assets a value filter looks at (it resolves each one's layers)
GROUPS, TEMPLATES = N_("Groups"), N_("Templates")
GUID_ROLE = Qt.ItemDataRole.UserRole
MEMBERS_ROLE = Qt.ItemDataRole.UserRole + 1  # every asset of a group row, its subgroups' too
OWN_ROLE = Qt.ItemDataRole.UserRole + 2  # the assets a group row holds itself
KEY_ROLE = Qt.ItemDataRole.UserRole + 3  # the group's path (a tuple of names)


class AssetTree(QWidget):
    picked = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.doc: AssetDocument | None = None
        self._groups: dict[tuple, dict] = {}  # per group path: its own assets, all its assets, its subgroups' names
        self.icons: IconSource | None = None  # set by the window: rows get the icon their asset names
        self._icon_budget = ICON_ROWS
        self._wanted: list[tuple[QTreeWidgetItem, str]] = []  # rows that wait for the icon of their key
        self._generation = 0  # raised when the tree is cleared: icons that come late are dropped
        self._background = Background(self)
        self._typing = QTimer(self, singleShot=True, interval=SEARCH_DELAY)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(placeholderText=tr("search: GUID, name or template (Enter: find next)"))
        row = QHBoxLayout()
        self.mode = QComboBox()
        for key in (GROUPS, TEMPLATES):
            self.mode.addItem(tr(key), key)
        self.template = QComboBox(editable=True)
        self.template.setToolTip(tr("only assets of this template"))
        row.addWidget(QLabel(tr("by")))
        row.addWidget(self.mode)
        row.addWidget(self.template, 1)
        self.value_filter = QLineEdit(placeholderText=tr("value filter: Building/Upkeep >= 5  (= >= <= ~ ^ in)"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Asset"), tr("GUID")])
        self.tree.setColumnWidth(0, 260)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)  # several assets to cut or copy
        self.count = QLabel()
        for w in (self.search, row, self.value_filter, self.tree, self.count):
            (layout.addLayout if isinstance(w, QHBoxLayout) else layout.addWidget)(w)
        self.search.returnPressed.connect(self.find_next)
        self._typing.timeout.connect(self.fill)
        self.search.textChanged.connect(lambda _t: self._typing.start())
        self.mode.currentIndexChanged.connect(lambda _i: self.fill())
        self.template.currentTextChanged.connect(lambda _t: self.fill())
        self.value_filter.returnPressed.connect(self.fill)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.currentItemChanged.connect(self._current)

    def set_doc(self, doc: AssetDocument | None) -> None:
        self.doc = doc
        self.template.blockSignals(True)
        self.template.clear()
        self.template.addItem("")
        if doc is not None:
            self.template.addItems(sorted({e.template for e in doc.index.entries if e.template}))
            named = any(g for g in doc.index.groups)
            self.mode.setCurrentIndex(
                self.mode.findData(GROUPS if named else TEMPLATES)
            )  # 117's export names no groups
        self.template.blockSignals(False)
        self.fill()

    def _row(self, guid: int, icon: bool = True) -> QTreeWidgetItem:
        c = self.doc.changes
        mark = " *" if guid in c.new or (guid in c.assets and not c.assets[guid].empty) else ""
        item = QTreeWidgetItem([f"{self.doc.name(guid) or tr('(no name)')}{mark}", str(guid)])
        item.setData(0, GUID_ROLE, guid)
        item.setToolTip(0, self.doc.template(guid))
        if icon and self.icons is not None and self._icon_budget > 0:
            key = self.icons.key_of(self.doc, guid)
            if key:
                self._wanted.append((item, key))
            self._icon_budget -= 1
        return item

    def _load_icons(self) -> None:
        """Decode the wanted rows' icons off the window's thread; each row gets its icon when its picture is in."""
        wanted, self._wanted, generation, source = self._wanted, [], self._generation, self.icons
        if not wanted or source is None:
            return

        def work():
            return [source.image(key) for _item, key in wanted]

        def done(images) -> None:
            if isinstance(images, Exception) or generation != self._generation:
                return
            for (item, _key), image in zip(wanted, images, strict=True):
                icon = source.icon(image)
                try:
                    if icon is not None:
                        item.setIcon(0, icon)
                except RuntimeError:  # the row was removed meanwhile
                    return

        self._background.run(work, done)

    def _entries(self) -> list[int]:
        """The assets the filters let through, in the index's order (the new ones first)."""
        doc, template = self.doc, self.template.currentText().strip()
        removed = set(doc.changes.removed)
        guids = list(doc.changes.new) + [e.guid for e in doc.index.entries if e.guid not in removed]
        if template:
            guids = [g for g in guids if doc.template(g) == template]
        vf = filters.parse(self.value_filter.text())
        if vf is not None:
            guids = [g for g in guids[:FILTERED] if vf.matches(doc, g)]
        return guids

    def fill(self) -> None:
        self._typing.stop()
        self.tree.clear()
        self._generation += 1
        self._wanted = []
        self._icon_budget = ICON_ROWS
        self._fill_rows()
        self._load_icons()

    def _fill_rows(self) -> None:
        if self.doc is None:
            self.count.setText(tr("no game open"))
            return
        query = self.search.text().strip()
        if query or filters.parse(self.value_filter.text()) is not None:
            return self._flat(query)
        guids = self._entries()
        if self.doc.changes.new:
            top = QTreeWidgetItem([tr("New in this change set ({n})", n=len(self.doc.changes.new)), ""])
            top.addChildren([self._row(g) for g in self.doc.changes.new if g in guids])
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        by_template = self.mode.currentData() == TEMPLATES
        self._groups = self._bucket([g for g in guids if g not in self.doc.changes.new], by_template)
        for key in sorted(k for k in self._groups if len(k) == 1):
            self.tree.addTopLevelItem(self._group_item(key))
        self.count.setText(
            tr("{n} assets in {count} templates", n=len(guids), count=len(self._groups))
            if by_template
            else tr("{n} assets in {count} groups", n=len(guids), count=len(self._groups))
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

        for g in guids:
            e = self.doc.index.by_guid[g]
            path = (
                (e.template or tr("(no template)"),)
                if by_template
                else self.doc.index.groups[e.group] or (tr("(no group)"),)
            )
            node(path)["own"].append(g)
            for k in range(1, len(path) + 1):
                node(path[:k])["all"].append(g)
        return out

    def _group_item(self, key: tuple) -> QTreeWidgetItem:
        info = self._groups[key]
        item = QTreeWidgetItem([f"{key[-1]} ({len(info['all'])})", ""])
        item.setData(0, KEY_ROLE, list(key))
        item.setData(0, MEMBERS_ROLE, info["all"])
        item.setData(0, OWN_ROLE, info["own"])
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return item

    def _flat(self, query: str) -> None:
        doc = self.doc
        if query:
            hits = [e.guid for e in doc.index.search(query, limit=SHOWN)]
            q = query.lower()
            hits = [g for g in doc.changes.new if q in doc.name(g).lower() or q == str(g)] + hits
            allowed = set(self._entries())
            hits = [g for g in hits if g in allowed]
        else:
            hits = self._entries()
        self.tree.addTopLevelItems([self._row(g) for g in hits[:SHOWN]])
        more = " " + tr("(the first {n} shown)", n=SHOWN) if len(hits) > SHOWN else ""
        self.count.setText(tr("{n} found", n=len(hits)) + more)

    def mark(self, guids: set) -> None:
        """The rows of these assets say again whether the change set edits them (`` *``) and show their icon as it is
        now (an icon edit changes no row's place, so the tree is not filled again)."""
        stack = [self.tree.topLevelItem(k) for k in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            stack += [item.child(i) for i in range(item.childCount())]
            if item.data(0, GUID_ROLE) in guids:
                item.setText(0, self._row(item.data(0, GUID_ROLE), icon=False).text(0))
                if self.icons is not None:
                    self.icons.forget(item.data(0, GUID_ROLE))
                key = self.icons.key_of(self.doc, item.data(0, GUID_ROLE)) if self.icons is not None else ""
                if key:
                    self._wanted.append((item, key))
                else:
                    item.setIcon(0, QIcon())
        self._load_icons()

    def _expand(self, item: QTreeWidgetItem) -> None:
        key = tuple(item.data(0, KEY_ROLE) or ())
        if key and not item.childCount():
            self._icon_budget = ICON_ROWS
            subs = sorted(self._groups[key]["sub"])
            item.addChildren([self._group_item((*key, name)) for name in subs])
            item.addChildren([self._row(g) for g in item.data(0, OWN_ROLE)])
            self._load_icons()

    def _current(self, item: QTreeWidgetItem | None, _old) -> None:
        guid = item.data(0, GUID_ROLE) if item is not None else None
        if guid is not None:
            self.picked.emit(guid)

    def find_next(self) -> None:
        """The next row below the current one (the search lists its hits flat)."""
        n = self.tree.topLevelItemCount()
        if not n:
            return
        current = self.tree.indexOfTopLevelItem(self.tree.currentItem()) if self.tree.currentItem() else -1
        self.tree.setCurrentItem(self.tree.topLevelItem((current + 1) % n))

    def select(self, guid: int) -> None:
        """Show ``guid`` in the tree (its groups opened), or as the only search hit when it is not listed."""
        for k in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(k)
            if top.data(0, GUID_ROLE) == guid:
                return self.tree.setCurrentItem(top)
            if guid in self._members(top):
                found = self._reveal(top, guid)
                if found is not None:
                    return self.tree.setCurrentItem(found)
        self.search.setText(str(guid))
        self.fill()  # not after the typing delay: the caller wants the row now
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

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

    def selected_guids(self) -> list[int]:
        """The assets selected: those of the rows, and every asset of a selected group row."""
        out: list[int] = []
        for item in self.tree.selectedItems():
            for guid in [item.data(0, GUID_ROLE)] if item.data(0, GUID_ROLE) is not None else self._members(item):
                if guid is not None and guid not in out:
                    out.append(guid)
        return out

    def selected_group(self) -> list[int]:
        """The assets of the group the current row is in, its subgroups' too (the balancing view's rows)."""
        item = self.tree.currentItem()
        if item is None:
            return []
        group = item if item.data(0, GUID_ROLE) is None else item.parent() or item
        members = self._members(group)
        return list(members) or ([group.data(0, GUID_ROLE)] if group.data(0, GUID_ROLE) is not None else [])
