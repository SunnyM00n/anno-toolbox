"""The Flash UI editor's picture export in the window (Qt, the ``desktop`` extra): the File > Export images dialog and
the "Export this picture" action. The work is ``uiexport``; this is the choices, the folder, the progress.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from ..formats import feuread
from ..infra.i18n import N_, tr
from . import uiexport


class _CCancelled(Exception):
    """The person cancelled the export (raised out of the progress step, the exporter lets it pass)."""


def export_one(window, image: feuread.SExternalImage) -> Path | None:
    """Ask for a file and write one picture to it."""
    exporter = uiexport.CExporter(window.m_ui)
    item = exporter.item_for(image)
    name, _ = QFileDialog.getSaveFileName(
        window,
        tr("ui.export_picture"),
        item.name.rsplit("/", 1)[-1],
        "PNG (*.png)",  # i18n: ok (a file filter)
    )
    if not name:
        return None
    try:
        Path(name).write_bytes(exporter.render(item))
    except (uiexport.CExportError, OSError) as e:
        QMessageBox.warning(window, tr("common.export"), str(e))
        return None
    window.statusBar().showMessage(tr("ui.exported_name", name=name))
    return Path(name)


class CExportDialog(QDialog):
    """The choices: what to export, and the folder (a sub-folder per movie or atlas, an index file)."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.m_window_ = window
        self.setWindowTitle(tr("ui.export_images"))
        lay = QVBoxLayout(self)
        has_pic = window.doc is not None and window.doc.picture_ref(window.m_node) is not None
        self.m_boxes = {}
        for key, label, enabled in (
            ("selected", N_("ui.selected_elements_picture"), has_pic),
            ("movie", N_("ui.every_picture_open_movie_uses"), window.doc is not None),
            ("all", N_("ui.every_picture_all_movies_sub"), True),
            ("shared", N_("ui.shared_pictures_movies_point"), True),
            ("atlases", N_("ui.atlases_cut_icons"), True),
        ):
            box = QCheckBox(tr(label))
            box.setEnabled(enabled)
            self.m_boxes[key] = box
            lay.addWidget(box)
        self.m_boxes["selected"].setChecked(has_pic)
        self.m_boxes["movie"].setChecked(not has_pic and window.doc is not None)
        row = QHBoxLayout()
        self.m_folder = QLineEdit(str(window.settings.value("export", "")))
        self.m_folder.setPlaceholderText(tr("ui.folder_to_write"))
        browse = QPushButton(tr("common.browse"))
        browse.clicked.connect(self._browse)
        row.addWidget(self.m_folder)
        row.addWidget(browse)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("common.export_into"), self.m_folder.text())
        if folder:
            self.m_folder.setText(folder)

    def items(self, exporter: uiexport.CExporter) -> list[uiexport.SItem]:
        window, checked_kinds = self.m_window_, {k: box.isChecked() for k, box in self.m_boxes.items()}
        out: list[uiexport.SItem] = []
        if checked_kinds["selected"] and window.doc is not None:
            image = window.doc.picture_ref(window.m_node)
            if image is not None:
                item = exporter.picture_item(image)
                item.name = "selected/" + item.name.rsplit("/", 1)[-1]
                out.append(item)
        if checked_kinds["movie"] and window.doc is not None:
            out += exporter.movie_items(window.doc.m_path, uiexport.movie_stem(window.doc.m_path) + "/")
        if checked_kinds["all"]:
            out += exporter.all_movie_items()
        if checked_kinds["shared"]:
            out += exporter.shared_items()
        if checked_kinds["atlases"]:
            out += exporter.atlas_items()
        return out


def export_images(window) -> None:
    """File > Export images...: the dialog, then the export with a progress bar."""
    if window.m_ui is None:
        window.statusBar().showMessage(tr("ui.open_game_folder_first"))
        return
    dialog = CExportDialog(window)
    if dialog.exec() != QDialog.Accepted:
        return
    folder = dialog.m_folder.text().strip()
    if not folder:
        QMessageBox.warning(window, tr("ui.export_images"), tr("ui.choose_folder"))
        return
    window.settings.setValue("export", folder)
    exporter = uiexport.CExporter(window.m_ui)
    items = dialog.items(exporter)
    if not items:
        QMessageBox.information(window, tr("ui.export_images"), tr("ui.nothing_chosen"))
        return
    progress = QProgressDialog(tr("ui.exporting_pictures"), tr("common.cancel"), 0, len(items), window)
    progress.setWindowTitle(tr("ui.export_images"))
    progress.setMinimumDuration(0)

    def step(number: int, total: int, name: str) -> None:
        progress.setValue(number)
        progress.setLabelText(name)
        QApplication.processEvents()
        if progress.wasCanceled():
            raise _CCancelled

    try:
        done = exporter.export(items, Path(folder), step)
    except _CCancelled:
        window.statusBar().showMessage(tr("ui.export_cancelled_no_index_written"))
        return
    except OSError as e:
        QMessageBox.warning(window, tr("ui.export_images"), str(e))
        return
    finally:
        progress.close()
    window.statusBar().showMessage(tr("ui.summary_in_folder", summary=uiexport.summary(done), folder=folder))
