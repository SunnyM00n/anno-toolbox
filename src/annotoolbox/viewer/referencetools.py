"""The viewer's reference tools: the "Not referenced" filter of the file browser, the References dock (what names the
open file and what it names) and the File entries that find the unreferenced files and export the list as CSV.

``ReferenceTools(window)`` is made before the window's menus; ``add_actions(menu)`` puts the entries into File,
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

PATH_ROLE = 0x0100 + 1  # Qt.UserRole + 1: the game path an item opens


def _judged(tree, graph):
    """(graph, every file's class, the unreferenced paths sorted) for the worker thread, so the window thread only
    takes them; None when there is no graph."""
    if graph is None:
        return None
    verdicts = graph.verdicts(tree.paths)
    return graph, verdicts, refgraph.unused(verdicts)


class ReferencePanel(QTreeWidget):
    """What names the open file, and what it names; a double-click opens the file (``open_file``)."""

    open_file = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabels([tr("file"), tr("asset")])
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.itemDoubleClicked.connect(lambda item, _col: self._open(item))
        self.note(
            tr("File > Find the unreferenced files reads every file once; then this panel follows the open file.")
        )

    def note(self, text: str) -> None:
        self.clear()
        self.addTopLevelItem(QTreeWidgetItem([text]))

    def show_references(self, path: str, named_by: list[str], names: list[str]) -> None:
        self.clear()
        for title, rows in ((N_("Named by"), named_by), (N_("Names"), names)):
            top = QTreeWidgetItem([tr("{title} ({count})", title=tr(title), count=number(len(rows)))])
            self.addTopLevelItem(top)
            for source in rows[:1000]:
                file, guid = scan.split_source(source)
                item = QTreeWidgetItem([file, guid])
                item.setData(0, PATH_ROLE, file)
                top.addChild(item)
            top.setExpanded(len(rows) <= 50)
        if not named_by:
            self.note(tr("no file names {name}", name=path.rsplit("/", 1)[-1]))

    def _open(self, item) -> None:
        path = item.data(0, PATH_ROLE)
        if path:
            self.open_file.emit(path)


class ReferenceTools(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.graph: refgraph.RefGraph | None = None
        self.verdicts: dict[str, str] = {}
        self.unreferenced: list[str] = []
        self.building = None  # the tree whose graph is being read
        self.panel = ReferencePanel()
        dock = QDockWidget(tr("References"), window, objectName="viewerReferences")
        dock.setWidget(self.panel)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        window.docks["References"] = dock
        if "Used by" in window.docks:
            window.tabifyDockWidget(window.docks["Used by"], dock)
        self.panel.open_file.connect(window.open_path)

    def add_actions(self, file_menu) -> None:
        file_menu.addAction(tr("Find the &unreferenced files"), self.find)
        file_menu.addAction(tr("Export the unreferenced files (&CSV)..."), self.export_csv)

    def find_users(self) -> None:
        """File > Who uses the file: the same graph as ``find``, shown in the Used by panel."""
        self.find()
        self.window.docks["Used by"].raise_()

    def _cache(self) -> Path | None:
        tree = self.window.tree
        if tree is None or not tree.game:
            return None
        return refgraph.cache_file(self.window.settings.index_dir(tree.game), tree.game)

    def tree_changed(self) -> None:
        """A game was opened: take its kept graph when it fits (no scan), else wait for ``find``."""
        self.graph, self.verdicts, self.unreferenced = None, {}, []
        self.window.browser.unreferenced = []
        tree = self.window.tree
        cache = self._cache()
        if tree is None or cache is None or not cache.exists():
            self.panel.note(
                tr("File > Find the unreferenced files reads every file once; then this panel follows the open file.")
            )
            return
        self.window.background.run(lambda: _judged(tree, refgraph.cached(tree, cache)), lambda f: self._found(tree, f))

    def find(self) -> None:
        """Build the graph in the background (kept beside the game), then fill the filter and the panel."""
        tree = self.window.tree
        if tree is None:
            self.window.statusBar().showMessage(tr("Open a game first"), 8000)
            return
        if self.graph is not None:
            self._show()
            return
        if self.building is tree:  # a second click while the first read goes on would read every file twice
            return
        self.building = tree
        cache = self._cache()
        reading = tr("reading every file that can name another (once, kept beside the game)...")
        self.panel.note(reading)
        self.window.users.note(reading)
        self.window.docks["References"].raise_()
        self.window.background.run(lambda: _judged(tree, refgraph.build(tree, cache)), lambda f: self._found(tree, f))

    def _found(self, tree, found) -> None:
        if self.building is tree:
            self.building = None
        if tree is not self.window.tree:
            return
        if isinstance(found, Exception):
            self.panel.note(
                tr("could not build the references: {kind}: {error}", kind=type(found).__name__, error=found)
            )
            return
        if found is None:
            return
        self.graph, self.verdicts, self.unreferenced = found
        self._show()

    def _show(self) -> None:
        self.window.browser.set_unreferenced(self.unreferenced)
        self.window.statusBar().showMessage(
            tr(
                "{count} files are named by no file that is in use (the game may still load one)",
                count=number(len(self.window.browser.unreferenced)),
            ),
            10000,
        )
        self.file_shown(self.window.current)

    def file_shown(self, path: str) -> None:
        if self.graph is not None and path:
            self.panel.show_references(path, self.graph.referenced_by(path), self.graph.references(path))
            self.window.users.show_users(path, *self.graph.users(path))

    def export_csv(self) -> None:
        tree = self.window.tree
        if tree is None or self.graph is None:
            self.window.statusBar().showMessage(tr("Find the unreferenced files first"), 8000)
            return
        proposal = f"{tree.game or 'game'}-unreferenced.csv"
        name, _ = QFileDialog.getSaveFileName(  # i18n: ok
            self.window, tr("Export the unreferenced files"), proposal, "CSV (*.csv)"
        )
        if name:
            Path(name).write_text(
                refgraph.unreferenced_csv(tree, self.graph, self.verdicts), encoding="utf-8", newline="\n"
            )
            self.window.statusBar().showMessage(tr("Wrote {name}", name=name), 8000)
