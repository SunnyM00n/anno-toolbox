"""The viewer's export, import and inspect tools: the File menu entries, the Inspect dock, and which files each
export takes (the open file, the browser's selection or folder, the filter).

``ExportTools(window)`` is made before the window's menus; ``add_actions(menu)`` puts the entries into File,
``file_shown(path)`` follows the open file. The dialogs are ``exportdialog`` and ``rdmpanel``; the work is
``exporting`` and ``rdm.actions`` (docs/exporting.md, docs/rdm-gltf.md).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDockWidget, QMessageBox

from ..infra.i18n import number, tr
from .browser import KINDS, PATH_ROLE, matches
from .exportdialog import ExportDialog, ExportRun
from .rdmpanel import ImportDialog, InspectPanel


def project_of(settings):
    """The suite's open project (where an imported model can be kept), None when the settings name no folder."""
    from ..suite import project

    try:
        return project.current(settings) if project.folder_of(settings) != Path() else None
    except ValueError:
        return None


STATE = "viewer"  # the window's key in the suite's settings (viewerwindow.APP)
BIG_EXPORT = 2000  # an export of more files than this asks first


class ExportTools(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.inspect = InspectPanel()
        dock = QDockWidget(tr("Inspect"), window, objectName="viewerInspect")
        dock.setWidget(self.inspect)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        window.docks["Inspect"] = dock
        if "Checks" in window.docks:
            window.tabifyDockWidget(window.docks["Checks"], dock)
        self.runs: list[ExportRun] = []

    def add_actions(self, file_menu) -> None:
        file_menu.addSeparator()
        file_menu.addAction(tr("&Export..."), self.export_open, QKeySequence("Ctrl+E"))
        file_menu.addAction(tr("Export the &selection or folder..."), self.export_selection)
        file_menu.addAction(tr("Export the files the &filter shows..."), self.export_filter)
        file_menu.addAction(tr("&Import a model (glTF to .rdm)..."), self.import_model)

    # which files
    def open_file(self) -> list[str]:
        return [self.window.current] if self.window.current else []

    def selection(self) -> list[str]:
        """The browser's selected file, or the files under its selected folder (the found list: its selected rows)."""
        browser, tree = self.window.browser, self.window.tree
        if tree is None:
            return []
        if browser.pages.currentWidget() is browser.found:
            return [item.text() for item in browser.found.selectedItems()]
        index = browser.view.currentIndex()  # a folder's own path ends in "/", which tells it from a file
        path = index.data(PATH_ROLE) if index.isValid() else None
        if not path:
            return []
        return tree.under(path) if path.endswith("/") else [path]

    def filtered(self) -> list[str]:
        """Every path the search text and the kind filter match (all files with neither)."""
        browser, tree = self.window.browser, self.window.tree
        if tree is None:
            return []
        return matches(browser.pool(), browser.search.text().strip(), KINDS.get(browser.kind.currentData(), ()))

    # the actions
    def export_open(self) -> None:
        self.export_paths(self.open_file(), tr("Export the open file"))

    def export_selection(self) -> None:
        self.export_paths(self.selection(), tr("Export the selection"))

    def export_filter(self) -> None:
        self.export_paths(self.filtered(), tr("Export the files the filter shows"))

    def export_paths(self, paths: list[str], title: str) -> bool:
        """Ask for the forms and the folder, then export ``paths`` of the open game; False when nothing was started."""
        if self.window.tree is None or not paths:
            self.window.statusBar().showMessage(tr("Open a game and pick a file, a folder or a filter first"), 8000)
            return False
        if len(paths) > BIG_EXPORT and not self._confirm_many(len(paths)):
            return False
        state = self.window.settings.apps.setdefault(STATE, {}).setdefault("export", {})
        dialog = ExportDialog(paths, state, title, self.window)
        if not dialog.exec():
            return False
        formats, options, folder = dialog.choice()
        job = ExportRun(self.window.tree, paths, formats, options, folder, self.window)
        self.runs = [r for r in self.runs if r.dialog.isVisible()] + [job]
        job.start()
        return True

    def _confirm_many(self, count: int) -> bool:
        answer = QMessageBox.question(
            self.window,
            tr("Export"),
            tr("{count} files match. Export them all?", count=number(count)),
            defaultButton=QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def import_model(self) -> None:
        state = self.window.settings.apps.setdefault(STATE, {})
        tree = self.window.tree
        project = project_of(self.window.settings) if tree is not None and tree.game else None
        ImportDialog(self.window, state, project=project, game=tree.game if tree else "").exec()

    # following the open file
    def file_shown(self, path: str) -> None:
        """The Inspect panel lists an ``.rdm`` that was opened."""
        tree = self.window.tree
        if path.lower().endswith(".rdm") and tree is not None:
            self.inspect.show_rdm(path, tree.read(path))
        else:
            self.inspect.clear(tr("The Inspect panel lists the elements of an .rdm (a mesh or an animation file)."))
