"""The viewer's file browser: a game's merged tree of folders and files (folders filled when opened), a search over
every path (all words, any order), a filter by kind, a "Not referenced" box (the files no other file names, once ``set_unreferenced`` has the list), and for
the file under the cursor every archive that carries it (the one it is read from first); the paths 117's archive map
takes away are a kind of their own.

``FileBrowser(parent)`` is the entry: ``set_tree(tree)`` (a ``sources.gametree.GameTree``), the signals ``picked``
(a file selected) and ``opened`` (a file activated). ``KINDS`` names which suffixes each kind holds.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..exporting.kinds import suffixes
from ..infra.i18n import N_, number, tr

KINDS = {
    N_("All files"): (),
    N_("Models"): suffixes("model"),
    N_("Textures"): suffixes("texture", "picture"),
    N_("Effects"): suffixes("effect"),
    N_("Sounds"): (*suffixes("sound", "bank"), ".sp", ".snd"),  # and the older games' speech envelopes and timelines
    N_("Data"): suffixes("data", "island", "text"),
}
DELETED = N_("Deleted by the game")  # the kind that lists the paths the archive map takes away (117)
NOT_REFERENCED_TIP = N_("Files no other file names. The game may still load one (by a name its program builds).")
SEARCH_SHOWN = 3000  # matches listed at most; the status says how many there were
PATH_ROLE = 0x0100 + 1  # Qt.UserRole + 1: the full game path of a row
FOLDER_ROLE = 0x0100 + 2  # whether a row is a folder still to fill


def folder_index(paths: list[str]) -> dict[str, tuple[set[str], list[str]]]:
    """Folder (``a/b/``, "" for the top) -> (its subfolders' names, its files' names), from sorted paths."""
    index: dict[str, tuple[set[str], list[str]]] = {"": (set(), [])}
    for path in paths:
        parts = path.split("/")
        folder = ""
        for name in parts[:-1]:
            index.setdefault(folder, (set(), []))[0].add(name)
            folder += name + "/"
        index.setdefault(folder, (set(), []))[1].append(parts[-1])
    return index


def origin_text(tree, path: str) -> str:
    """Where a path is read from and which archives it overrides (or, deleted, which still carry it)."""
    found = tree.origins(path)
    if not tree.exists(path):
        return tr("deleted by the archive map; still in: {archives}", archives=", ".join(found) or "-")
    if not found:
        return tr("read from: -")
    also = "\n" + tr("also in (overridden): {archives}", archives=", ".join(found[1:])) if len(found) > 1 else ""
    return tr("read from: {archive}", archive=found[0]) + also


def matches(paths: list[str], text: str, suffixes: tuple[str, ...]) -> list[str]:
    """The paths holding every word of ``text`` (case blind) and ending in one of ``suffixes`` (any when empty)."""
    words = text.lower().split()
    return [p for p in paths if (not suffixes or p.endswith(suffixes)) and all(w in p for w in words)]


class FileBrowser(QWidget):
    picked = Signal(str)
    opened = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tree = None
        self.index: dict = {}
        self.search = QLineEdit(placeholderText=tr("Search the paths (all words)"), clearButtonEnabled=True)
        self.kind = QComboBox()
        for name in KINDS:
            self.kind.addItem(tr(name), name)
        self.unref = QCheckBox(tr("Not referenced"), toolTip=tr(NOT_REFERENCED_TIP))
        self.unref.hide()  # shown once the reference graph is there (``set_unreferenced``)
        self.unreferenced: list[str] = []
        self.model = QStandardItemModel(self)
        self.view = QTreeView(headerHidden=True, uniformRowHeights=True)
        self.view.setModel(self.model)
        self.found = QListWidget(uniformItemSizes=True)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.view)
        self.pages.addWidget(self.found)
        self.origin = QLabel(wordWrap=True)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.kind)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(top)
        lay.addWidget(self.unref)
        lay.addWidget(self.pages, 1)
        lay.addWidget(self.origin)
        self._timer = QTimer(self, singleShot=True, interval=250)
        self._timer.timeout.connect(self._run_search)
        self.search.textChanged.connect(lambda _t: self._timer.start())
        self.kind.currentIndexChanged.connect(lambda _i: self._refresh())
        self.unref.toggled.connect(lambda _on: self._refresh())
        self.view.expanded.connect(self._fill)
        self.view.selectionModel().currentChanged.connect(lambda cur, _prev: self._pick(cur.data(PATH_ROLE)))
        self.view.activated.connect(lambda index: self._open(index.data(PATH_ROLE)))
        self.found.currentTextChanged.connect(self._pick)
        self.found.itemActivated.connect(lambda item: self._open(item.text()))

    def set_tree(self, tree) -> None:
        """Browse ``tree`` (a ``GameTree``), from its top folder."""
        self.tree = tree
        self.unreferenced = []
        self.unref.blockSignals(True)
        self.unref.setChecked(False)
        self.unref.blockSignals(False)
        self.unref.hide()
        self.index = folder_index(tree.paths)
        self.kind.blockSignals(True)
        if self.kind.findData(DELETED) < 0 and tree.deleted:
            self.kind.addItem(tr(DELETED), DELETED)
        elif self.kind.findData(DELETED) >= 0 and not tree.deleted:
            self.kind.removeItem(self.kind.findData(DELETED))
        self.kind.blockSignals(False)
        self._refresh()

    def set_unreferenced(self, paths: list[str] | None) -> None:
        """The files no reference was found for (``refgraph``): the "Not referenced" box filters to them; ``None``
        hides the box (no graph yet, or another game)."""
        self.unreferenced = sorted(paths or [])
        self.unref.blockSignals(True)
        self.unref.setChecked(False)
        self.unref.blockSignals(False)
        self.unref.setVisible(paths is not None)
        self.unref.setText(tr("Not referenced ({count})", count=number(len(self.unreferenced))))
        if self.tree is not None:
            self._refresh()

    def pool(self) -> list[str]:
        """The paths the filters start from: the deleted ones, the unreferenced ones (box checked) or every file."""
        if self.tree is None:
            return []
        if self.kind.currentData() == DELETED:
            return self.tree.deleted
        return self.unreferenced if self.unref.isChecked() else self.tree.paths

    def _refresh(self) -> None:
        if self.search.text().strip() or self.kind.currentData() == DELETED or self.unref.isChecked():
            self._run_search()
            return
        self.pages.setCurrentWidget(self.view)
        self.model.clear()
        if self.tree is not None:
            self._add_children(self.model.invisibleRootItem(), "")
        self.origin.setText(
            tr("{name}: {count} files", name=self.tree.name, count=number(len(self.tree.paths))) if self.tree else ""
        )

    def _suffixes(self) -> tuple[str, ...]:
        return KINDS.get(self.kind.currentData(), ())

    def _add_children(self, parent: QStandardItem, folder: str) -> None:
        subfolders, files = self.index.get(folder, (set(), []))
        suffixes = self._suffixes()
        for name in sorted(subfolders):
            item = QStandardItem(name + "/")
            item.setEditable(False)
            item.setData(folder + name + "/", PATH_ROLE)
            item.setData(True, FOLDER_ROLE)
            item.appendRow(QStandardItem(""))  # a placeholder, so the row can be opened
            parent.appendRow(item)
        for name in files:
            if suffixes and not name.endswith(suffixes):
                continue
            item = QStandardItem(name)
            item.setEditable(False)
            item.setData(folder + name, PATH_ROLE)
            parent.appendRow(item)

    def _fill(self, index) -> None:
        item = self.model.itemFromIndex(index)
        if item is None or not item.data(FOLDER_ROLE):
            return
        item.setData(False, FOLDER_ROLE)
        item.removeRows(0, item.rowCount())
        self._add_children(item, item.data(PATH_ROLE))

    def _run_search(self) -> None:
        text = self.search.text().strip()
        if self.tree is None:
            return
        if not text and self.kind.currentData() != DELETED and not self.unref.isChecked():
            self._refresh()
            return
        found = matches(self.pool(), text, self._suffixes())
        self.found.clear()
        self.found.addItems(found[:SEARCH_SHOWN])
        self.pages.setCurrentWidget(self.found)
        more = tr(" (the first {n} shown)", n=number(SEARCH_SHOWN)) if len(found) > SEARCH_SHOWN else ""
        self.origin.setText(tr("{count} files{more}", count=number(len(found)), more=more))

    def _pick(self, path) -> None:
        if not path or self.tree is None or path.endswith("/"):
            return
        self.origin.setText(f"{path}\n{origin_text(self.tree, path)}")
        if self.tree.exists(path):
            self.picked.emit(path)

    def _open(self, path) -> None:
        if path and not path.endswith("/"):
            self.opened.emit(path)

    def reveal(self, path: str) -> None:
        """Show ``path`` in the tree: its folders opened, its row selected."""
        if self.tree is None or not self.tree.exists(path):
            return
        self.search.clear()
        self._refresh()
        parent, folder = self.model.invisibleRootItem(), ""
        for name in path.split("/")[:-1]:
            folder += name + "/"
            item = next(
                (parent.child(r) for r in range(parent.rowCount()) if parent.child(r).data(PATH_ROLE) == folder), None
            )
            if item is None:
                return
            self.view.expand(item.index())
            parent = item
        row = next((parent.child(r) for r in range(parent.rowCount()) if parent.child(r).data(PATH_ROLE) == path), None)
        if row is not None:
            self.view.setCurrentIndex(row.index())
            self.view.scrollTo(row.index())
