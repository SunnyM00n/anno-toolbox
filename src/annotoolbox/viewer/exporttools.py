"""The viewer's export, import and inspect tools: the File menu entries, the Inspect dock, and which files each
export takes (the open file, the browser's selection or folder, the filter).

``CExportTools(window)`` is made before the window's menus; ``add_actions(menu)`` puts the entries into File,
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
from .exportdialog import CExportDialog, CExportRun
from .rdmpanel import CImportDialog, CInspectPanel


def project_of(settings):
    """The suite's open project (where an imported model can be kept), None when the settings name no folder."""
    from ..suite import project

    try:
        return project.current(settings) if project.folder_of(settings) != Path() else None
    except ValueError:
        return None


STATE = "viewer"  # the window's key in the suite's settings (viewerwindow.APP)
BIG_EXPORT = 2000  # an export of more files than this asks first


class CExportTools(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.m_window = window
        self.m_inspect = CInspectPanel()
        dock = QDockWidget(tr("viewer.inspect"), window, objectName="viewerInspect")
        dock.setWidget(self.m_inspect)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        window.m_docks["Inspect"] = dock
        if "Checks" in window.m_docks:
            window.tabifyDockWidget(window.m_docks["Checks"], dock)
        self.m_runs: list[CExportRun] = []

    def add_actions(self, file_menu) -> None:
        file_menu.addSeparator()
        file_menu.addAction(tr("viewer.export"), self.export_open, QKeySequence("Ctrl+E"))
        file_menu.addAction(tr("viewer.export_selection_folder"), self.export_selection)
        file_menu.addAction(tr("viewer.export_files_filter_shows_dots"), self.export_filter)
        file_menu.addAction(tr("viewer.import_model_gltf_rdm"), self.import_model)

    # which files
    def open_file(self) -> list[str]:
        return [self.m_window.m_current] if self.m_window.m_current else []

    def selection(self) -> list[str]:
        """The browser's selected file, or the files under its selected folder (the found list: its selected rows)."""
        browser, tree = self.m_window.m_browser, self.m_window.m_tree
        if tree is None:
            return []
        if browser.m_pages.currentWidget() is browser.m_found:
            return [item.text() for item in browser.m_found.selectedItems()]
        index = browser.m_view.currentIndex()  # a folder's own path ends in "/", which tells it from a file
        path = index.data(PATH_ROLE) if index.isValid() else None
        if not path:
            return []
        return tree.under(path) if path.endswith("/") else [path]

    def filtered(self) -> list[str]:
        """Every path the search text and the kind filter match (all files with neither)."""
        browser, tree = self.m_window.m_browser, self.m_window.m_tree
        if tree is None:
            return []
        return matches(browser.pool(), browser.m_search.text().strip(), KINDS.get(browser.m_kind.currentData(), ()))

    # the actions
    def export_open(self) -> None:
        self.export_paths(self.open_file(), tr("viewer.export_open_file"))

    def export_selection(self) -> None:
        self.export_paths(self.selection(), tr("viewer.export_selection"))

    def export_filter(self) -> None:
        self.export_paths(self.filtered(), tr("viewer.export_files_filter_shows"))

    def export_paths(self, paths: list[str], title: str) -> bool:
        """Ask for the forms and the folder, then export ``paths`` of the open game; False when nothing was started."""
        if self.m_window.m_tree is None or not paths:
            self.m_window.statusBar().showMessage(tr("viewer.open_game_pick_file_folder"), 8000)
            return False
        if len(paths) > BIG_EXPORT and not self._confirm_many(len(paths)):
            return False
        state = self.m_window.m_settings.apps.setdefault(STATE, {}).setdefault("export", {})
        dialog = CExportDialog(paths, state, title, self.m_window)
        if not dialog.exec():
            return False
        formats, options, folder = dialog.choice()
        job = CExportRun(self.m_window.m_tree, paths, formats, options, folder, self.m_window)
        self.m_runs.append(job)
        job.done.connect(self.m_runs.remove)
        job.start()
        return True

    def _confirm_many(self, count: int) -> bool:
        answer = QMessageBox.question(
            self.m_window,
            tr("common.export"),
            tr("viewer.files_match_export_all", count=number(count)),
            defaultButton=QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def import_model(self) -> None:
        state = self.m_window.m_settings.apps.setdefault(STATE, {})
        tree = self.m_window.m_tree
        project = project_of(self.m_window.m_settings) if tree is not None and tree.m_game else None
        CImportDialog(self.m_window, state, project=project, game=tree.m_game if tree else "").exec()

    # following the open file
    def file_shown(self, path: str) -> None:
        """The Inspect panel lists an ``.rdm`` that was opened."""
        tree = self.m_window.m_tree
        if path.lower().endswith(".rdm") and tree is not None:
            self.m_inspect.show_rdm(path, tree.read(path))
        else:
            self.m_inspect.clear(tr("viewer.inspect_panel_lists_elements_rdm"))
