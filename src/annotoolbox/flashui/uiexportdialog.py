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


def export_one(window, img: feuread.ExternalImage) -> Path | None:
    """Ask for a file and write one picture to it."""
    exporter = uiexport.Exporter(window.ui)
    item = exporter.item_for(img)
    name, _ = QFileDialog.getSaveFileName(
        window,
        tr("Export this picture"),
        item.name.rsplit("/", 1)[-1],
        "PNG (*.png)",  # i18n: ok (a file filter)
    )
    if not name:
        return None
    try:
        Path(name).write_bytes(exporter.render(item))
    except (uiexport.ExportError, OSError) as e:
        QMessageBox.warning(window, tr("Export"), str(e))
        return None
    window.statusBar().showMessage(tr("exported {name}", name=name))
    return Path(name)


class ExportDialog(QDialog):
    """The choices: what to export, and the folder (a sub-folder per movie or atlas, an index file)."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window_ = window
        self.setWindowTitle(tr("Export images"))
        lay = QVBoxLayout(self)
        has_pic = window.doc is not None and window.doc.picture_ref(window.node) is not None
        self.boxes = {}
        for key, label, enabled in (
            ("selected", N_("The selected element's picture"), has_pic),
            ("movie", N_("Every picture the open movie uses"), window.doc is not None),
            ("all", N_("Every picture of all movies (a sub-folder per movie)"), True),
            ("shared", N_("The shared pictures the movies point at (shared/)"), True),
            ("atlases", N_("The atlases cut into their icons (atlases/<atlas>/)"), True),
        ):
            box = QCheckBox(tr(label))
            box.setEnabled(enabled)
            self.boxes[key] = box
            lay.addWidget(box)
        self.boxes["selected"].setChecked(has_pic)
        self.boxes["movie"].setChecked(not has_pic and window.doc is not None)
        row = QHBoxLayout()
        self.folder = QLineEdit(str(window.settings.value("export", "")))
        self.folder.setPlaceholderText(tr("folder to write into"))
        browse = QPushButton(tr("Browse..."))
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder)
        row.addWidget(browse)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Export into"), self.folder.text())
        if folder:
            self.folder.setText(folder)

    def items(self, exporter: uiexport.Exporter) -> list[uiexport.Item]:
        w, on = self.window_, {k: b.isChecked() for k, b in self.boxes.items()}
        out: list[uiexport.Item] = []
        if on["selected"] and w.doc is not None:
            img = w.doc.picture_ref(w.node)
            if img is not None:
                item = exporter.picture_item(img)
                item.name = "selected/" + item.name.rsplit("/", 1)[-1]
                out.append(item)
        if on["movie"] and w.doc is not None:
            out += exporter.movie_items(w.doc.path, uiexport.movie_stem(w.doc.path) + "/")
        if on["all"]:
            out += exporter.all_movie_items()
        if on["shared"]:
            out += exporter.shared_items()
        if on["atlases"]:
            out += exporter.atlas_items()
        return out


def export_images(window) -> None:
    """File > Export images...: the dialog, then the export with a progress bar."""
    if window.ui is None:
        window.statusBar().showMessage(tr("open a game folder first"))
        return
    dialog = ExportDialog(window)
    if dialog.exec() != QDialog.Accepted:
        return
    folder = dialog.folder.text().strip()
    if not folder:
        QMessageBox.warning(window, tr("Export images"), tr("choose a folder"))
        return
    window.settings.setValue("export", folder)
    exporter = uiexport.Exporter(window.ui)
    items = dialog.items(exporter)
    if not items:
        QMessageBox.information(window, tr("Export images"), tr("nothing chosen"))
        return
    progress = QProgressDialog(tr("Exporting pictures..."), tr("Cancel"), 0, len(items), window)
    progress.setWindowTitle(tr("Export images"))
    progress.setMinimumDuration(0)

    def step(n: int, total: int, name: str) -> None:
        progress.setValue(n)
        progress.setLabelText(name)
        QApplication.processEvents()
        if progress.wasCanceled():
            raise KeyboardInterrupt

    try:
        done = exporter.export(items, Path(folder), step)
    except KeyboardInterrupt:
        window.statusBar().showMessage(tr("export cancelled (no index written)"))
        return
    except OSError as e:
        QMessageBox.warning(window, tr("Export images"), str(e))
        return
    finally:
        progress.close()
    window.statusBar().showMessage(tr("{summary} in {folder}", summary=uiexport.summary(done), folder=folder))
