"""The viewer's reference tools: the "Not referenced" filter of the file browser, the References dock (what names the
open file and what it names) and the File entries that find the unreferenced files and export the list as CSV.

``CReferenceTools(window)`` is made before the window's menus; ``add_actions(menu)`` puts the entries into File,
``tree_changed()`` takes a kept graph for a newly opened game, ``file_shown(path)`` follows the open file. The graph and
its rules are ``refgraph`` (docs/references.md).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
)

from ..infra.i18n import N_, number, tr
from ..refgraph import graph as refgraph
from ..refgraph import scan
from .browser import PATH_ROLE  # the game path an item opens


def _judged(tree, graph):
    """(graph, every file's class, the unreferenced paths sorted) for the worker thread, so the window thread only
    takes them; None when there is no graph."""
    if graph is None:
        return None
    verdicts = graph.verdicts(tree.m_paths)
    return graph, verdicts, refgraph.unused(verdicts)


class CReferencePanel(QTreeWidget):
    """What names the open file, and what it names; a double-click opens the file (``open_file``)."""

    open_file = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels([tr("common.file_lower"), tr("viewer.asset")])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.itemDoubleClicked.connect(lambda item, _col: self._open(item))
        self.note(tr("viewer.file_gt_find_unreferenced_files"))

    def note(self, text: str) -> None:
        self.clear()
        self.addTopLevelItem(QTreeWidgetItem([text]))

    def show_references(self, path: str, named_by: list[str], names: list[str]) -> None:
        self.clear()
        for title, rows in ((N_("viewer.named_by"), named_by), (N_("viewer.names"), names)):
            root_item = QTreeWidgetItem([tr("common.title_count", title=tr(title), count=number(len(rows)))])
            self.addTopLevelItem(root_item)
            for source in rows[:1000]:
                file, guid = scan.split_source(source)
                item = QTreeWidgetItem([file, guid])
                item.setData(0, PATH_ROLE, file)
                root_item.addChild(item)
            root_item.setExpanded(len(rows) <= 50)
        if not named_by:
            self.note(tr("viewer.no_file_names", name=path.rsplit("/", 1)[-1]))

    def _open(self, item) -> None:
        path = item.data(0, PATH_ROLE)
        if path:
            self.open_file.emit(path)


class CReferenceTools(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.m_window = window
        self.m_graph: refgraph.CRefGraph | None = None
        self.m_verdicts: dict[str, str] = {}
        self.m_unreferenced: list[str] = []
        self.m_building = None  # the tree whose graph is being read
        self.m_panel = CReferencePanel()
        dock = QDockWidget(tr("viewer.references"), window, objectName="viewerReferences")
        dock.setWidget(self.m_panel)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        window.m_docks["References"] = dock
        if "Used by" in window.m_docks:
            window.tabifyDockWidget(window.m_docks["Used by"], dock)
        self.m_panel.open_file.connect(window.open_path)

    def add_actions(self, file_menu) -> None:
        file_menu.addAction(tr("viewer.find_unreferenced_files"), self.find)
        file_menu.addAction(tr("viewer.export_unreferenced_files_csv"), self.export_csv)

    def find_users(self) -> None:
        """File > Who uses the file: the same graph as ``find``, shown in the Used by panel."""
        self.find()
        self.m_window.m_docks["Used by"].raise_()

    def _cache(self) -> Path | None:
        tree = self.m_window.m_tree
        if tree is None or not tree.m_game:
            return None
        return refgraph.cache_file(self.m_window.m_settings.index_dir(tree.m_game), tree.m_game)

    def tree_changed(self) -> None:
        """A game was opened: take its kept graph when it fits (no scan), else wait for ``find``."""
        self.m_graph, self.m_verdicts, self.m_unreferenced = None, {}, []
        self.m_window.m_browser.m_unreferenced = []
        tree = self.m_window.m_tree
        cache = self._cache()
        if tree is None or cache is None or not cache.exists():
            self.m_panel.note(tr("viewer.file_gt_find_unreferenced_files"))
            return
        self.m_window.m_background.run(
            lambda: _judged(tree, refgraph.cached(tree, cache)), lambda found: self._found(tree, found)
        )

    def find(self) -> None:
        """Build the graph in the background (kept beside the game), then fill the filter and the panel."""
        tree = self.m_window.m_tree
        if tree is None:
            self.m_window.statusBar().showMessage(tr("viewer.open_game_first"), 8000)
            return
        if self.m_graph is not None:
            self._show()
            return
        if self.m_building is tree:  # a second click while the first read goes on would read every file twice
            return
        self.m_building = tree
        cache = self._cache()
        reading = tr("viewer.reading_every_file_name_another")
        self.m_panel.note(reading)
        self.m_window.m_users.note(reading)
        self.m_window.m_docks["References"].raise_()
        self.m_window.m_background.run(
            lambda: _judged(tree, refgraph.build(tree, cache)), lambda found: self._found(tree, found)
        )

    def _found(self, tree, found) -> None:
        if self.m_building is tree:
            self.m_building = None
        if tree is not self.m_window.m_tree:
            return
        if isinstance(found, Exception):
            self.m_panel.note(tr("viewer.could_not_build_references", kind=type(found).__name__, error=found))
            return
        if found is None:
            return
        self.m_graph, self.m_verdicts, self.m_unreferenced = found
        self._show()

    def _show(self) -> None:
        self.m_window.m_browser.set_unreferenced(self.m_unreferenced)
        self.m_window.statusBar().showMessage(
            tr(
                "viewer.files_named_no_file_use",
                count=number(len(self.m_window.m_browser.m_unreferenced)),
            ),
            10000,
        )
        self.file_shown(self.m_window.m_current)

    def file_shown(self, path: str) -> None:
        if self.m_graph is not None and path:
            self.m_panel.show_references(path, self.m_graph.referenced_by(path), self.m_graph.references(path))
            self.m_window.m_users.show_users(path, *self.m_graph.users(path))

    def export_csv(self) -> None:
        tree = self.m_window.m_tree
        if tree is None or self.m_graph is None:
            self.m_window.statusBar().showMessage(tr("viewer.find_unreferenced_files_first"), 8000)
            return
        proposal = f"{tree.m_game or 'game'}-unreferenced.csv"
        name, _ = QFileDialog.getSaveFileName(  # i18n: ok
            self.m_window, tr("viewer.export_unreferenced_files"), proposal, "CSV (*.csv)"
        )
        if not name:
            return
        try:
            Path(name).write_text(
                refgraph.unreferenced_csv(tree, self.m_graph, self.m_verdicts), encoding="utf-8", newline="\n"
            )
        except OSError as exc:  # open in a spreadsheet, a folder that takes no files
            self.m_window.statusBar().showMessage(tr("viewer.could_not_write_name", name=name, error=exc), 12000)
            return
        self.m_window.statusBar().showMessage(tr("viewer.wrote_name", name=name), 8000)
