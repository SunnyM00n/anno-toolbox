"""The viewer's file browser: a game's merged tree of folders and files (folders filled when opened), a search over
every path (all words, any order), a filter by kind, a "Not referenced" box (the files no other file names, once ``set_unreferenced`` has the list), and for
the file under the cursor every archive that carries it (the one it is read from first); the paths 117's archive map
takes away are a kind of their own.

``CFileBrowser(parent)`` is the entry: ``set_tree(tree)`` (a ``sources.gametree.CGameTree``), the signals ``picked``
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
    "All files": (),
    "Models": suffixes("model"),
    "Textures": suffixes("texture", "picture"),
    "Effects": suffixes("effect"),
    "Sounds": (
        *suffixes("sound", "bank"),
        ".sp",
        ".snd",
    ),  # and the older games' speech envelopes and timelines
    "Data": suffixes("data", "island", "text"),
}
KIND_TITLES = {
    "All files": N_("viewer.all_files"),
    "Models": N_("viewer.models"),
    "Textures": N_("viewer.textures"),
    "Effects": N_("common.effects"),
    "Sounds": N_("viewer.sounds"),
    "Data": N_("viewer.data"),
}
DELETED = N_("viewer.deleted_game")  # the kind that lists the paths the archive map takes away (117)
NOT_REFERENCED_TIP = N_("viewer.files_no_other_file_names")
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
        return tr("viewer.deleted_archive_map_still", archives=", ".join(found) or "-")
    if not found:
        return tr("viewer.read_from_lower")
    also = "\n" + tr("viewer.also_overridden_archives", archives=", ".join(found[1:])) if len(found) > 1 else ""
    return tr("viewer.read_from_archive", archive=found[0]) + also


def matches(paths: list[str], text: str, suffixes: tuple[str, ...]) -> list[str]:
    """The paths holding every word of ``text`` (case blind) and ending in one of ``suffixes`` (any when empty)."""
    words = text.lower().split()
    return [path for path in paths if (not suffixes or path.endswith(suffixes)) and all(word in path for word in words)]


class CFileBrowser(QWidget):
    picked = Signal(str)
    opened = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_tree = None
        self.m_index: dict = {}
        self.m_search = QLineEdit(placeholderText=tr("viewer.search_paths_all_words"), clearButtonEnabled=True)
        self.m_kind = QComboBox()
        for name in KINDS:
            self.m_kind.addItem(tr(KIND_TITLES[name]), name)
        self.m_unreferenced_toggle = QCheckBox(tr("viewer.not_referenced"), toolTip=tr(NOT_REFERENCED_TIP))
        self.m_unreferenced_toggle.hide()  # shown once the reference graph is there (``set_unreferenced``)
        self.m_unreferenced: list[str] = []
        self.m_model = QStandardItemModel(self)
        self.m_view = QTreeView(headerHidden=True, uniformRowHeights=True)
        self.m_view.setModel(self.m_model)
        self.m_found = QListWidget(uniformItemSizes=True)
        self.m_pages = QStackedWidget()
        self.m_pages.addWidget(self.m_view)
        self.m_pages.addWidget(self.m_found)
        self.m_origin = QLabel(wordWrap=True)
        top_row = QHBoxLayout()
        top_row.addWidget(self.m_search, 1)
        top_row.addWidget(self.m_kind)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top_row)
        layout.addWidget(self.m_unreferenced_toggle)
        layout.addWidget(self.m_pages, 1)
        layout.addWidget(self.m_origin)
        self._m_timer = QTimer(self, singleShot=True, interval=250)
        self._m_timer.timeout.connect(self._run_search)
        self.m_search.textChanged.connect(lambda _t: self._m_timer.start())
        self.m_kind.currentIndexChanged.connect(lambda _i: self._refresh())
        self.m_unreferenced_toggle.toggled.connect(lambda _on: self._refresh())
        self.m_view.expanded.connect(self._fill)
        self.m_view.selectionModel().currentChanged.connect(lambda cur, _prev: self._pick(cur.data(PATH_ROLE)))
        self.m_view.activated.connect(lambda index: self._open(index.data(PATH_ROLE)))
        self.m_found.currentTextChanged.connect(self._pick)
        self.m_found.itemActivated.connect(lambda item: self._open(item.text()))

    def set_tree(self, tree) -> None:
        """Browse ``tree`` (a ``CGameTree``), from its top folder."""
        self.m_tree = tree
        self.m_unreferenced = []
        self.m_unreferenced_toggle.blockSignals(True)
        self.m_unreferenced_toggle.setChecked(False)
        self.m_unreferenced_toggle.blockSignals(False)
        self.m_unreferenced_toggle.hide()
        self.m_index = folder_index(tree.m_paths)
        self.m_kind.blockSignals(True)
        if self.m_kind.findData(DELETED) < 0 and tree.m_deleted:
            self.m_kind.addItem(tr(DELETED), DELETED)
        elif self.m_kind.findData(DELETED) >= 0 and not tree.m_deleted:
            self.m_kind.removeItem(self.m_kind.findData(DELETED))
        self.m_kind.blockSignals(False)
        self._refresh()

    def set_unreferenced(self, paths: list[str] | None) -> None:
        """The files no reference was found for (``refgraph``): the "Not referenced" box filters to them; ``None``
        hides the box (no graph yet, or another game)."""
        self.m_unreferenced = sorted(paths or [])
        self.m_unreferenced_toggle.blockSignals(True)
        self.m_unreferenced_toggle.setChecked(False)
        self.m_unreferenced_toggle.blockSignals(False)
        self.m_unreferenced_toggle.setVisible(paths is not None)
        self.m_unreferenced_toggle.setText(tr("viewer.not_referenced_count", count=number(len(self.m_unreferenced))))
        if self.m_tree is not None:
            self._refresh()

    def pool(self) -> list[str]:
        """The paths the filters start from: the deleted ones, the unreferenced ones (box checked) or every file."""
        if self.m_tree is None:
            return []
        if self.m_kind.currentData() == DELETED:
            return self.m_tree.m_deleted
        return self.m_unreferenced if self.m_unreferenced_toggle.isChecked() else self.m_tree.m_paths

    def _refresh(self) -> None:
        if (
            self.m_search.text().strip()
            or self.m_kind.currentData() == DELETED
            or self.m_unreferenced_toggle.isChecked()
        ):
            self._run_search()
            return
        self.m_pages.setCurrentWidget(self.m_view)
        self.m_model.clear()
        if self.m_tree is not None:
            self._add_children(self.m_model.invisibleRootItem(), "")
        self.m_origin.setText(
            tr("viewer.name_count_files", name=self.m_tree.m_name, count=number(len(self.m_tree.m_paths)))
            if self.m_tree
            else ""
        )

    def _suffixes(self) -> tuple[str, ...]:
        return KINDS.get(self.m_kind.currentData(), ())

    def _add_children(self, parent: QStandardItem, folder: str) -> None:
        subfolders, files = self.m_index.get(folder, (set(), []))
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
        item = self.m_model.itemFromIndex(index)
        if item is None or not item.data(FOLDER_ROLE):
            return
        item.setData(False, FOLDER_ROLE)
        item.removeRows(0, item.rowCount())
        self._add_children(item, item.data(PATH_ROLE))

    def _run_search(self) -> None:
        text = self.m_search.text().strip()
        if self.m_tree is None:
            return
        if not text and self.m_kind.currentData() != DELETED and not self.m_unreferenced_toggle.isChecked():
            self._refresh()
            return
        found = matches(self.pool(), text, self._suffixes())
        self.m_found.clear()
        self.m_found.addItems(found[:SEARCH_SHOWN])
        self.m_pages.setCurrentWidget(self.m_found)
        more = tr("viewer.first_n_shown", n=number(SEARCH_SHOWN)) if len(found) > SEARCH_SHOWN else ""
        self.m_origin.setText(tr("viewer.count_files_more", count=number(len(found)), more=more))

    def _pick(self, path) -> None:
        if not path or self.m_tree is None or path.endswith("/"):
            return
        self.m_origin.setText(f"{path}\n{origin_text(self.m_tree, path)}")
        if self.m_tree.exists(path):
            self.picked.emit(path)

    def _open(self, path) -> None:
        if path and not path.endswith("/"):
            self.opened.emit(path)

    def reveal(self, path: str) -> None:
        """Show ``path`` in the tree: its folders opened, its row selected."""
        if self.m_tree is None or not self.m_tree.exists(path):
            return
        self.m_search.clear()
        self._m_timer.stop()  # the cleared search must not rebuild the tree under the row
        self._refresh()
        parent, folder = self.m_model.invisibleRootItem(), ""
        for name in path.split("/")[:-1]:
            folder += name + "/"
            item = next(
                (
                    parent.child(row_index)
                    for row_index in range(parent.rowCount())
                    if parent.child(row_index).data(PATH_ROLE) == folder
                ),
                None,
            )
            if item is None:
                return
            self.m_view.expand(item.index())
            parent = item
        row = next(
            (
                parent.child(row_index)
                for row_index in range(parent.rowCount())
                if parent.child(row_index).data(PATH_ROLE) == path
            ),
            None,
        )
        if row is not None:
            self.m_view.setCurrentIndex(row.index())
            self.m_view.scrollTo(row.index())
