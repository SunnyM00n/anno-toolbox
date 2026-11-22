"""The export dialog: which forms of which kinds of file, the options, the folder; and the run with its progress.

``CExportDialog(paths, state)`` asks, ``choice()`` returns what was chosen (the form keys, the ``SOptions`` and the
folder) and ``state`` (a dict the caller keeps in its settings) remembers it for next time. ``kinds_summary`` is
the line that says what is being exported. ``CExportRun`` runs ``exporting.run.export_many`` on a thread with a progress
dialog and says at the end how it went. The forms and what each makes: docs/exporting.md.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..exporting import kinds, run
from ..infra.i18n import N_, number, tr
from ..rdm import grannyrdm

KIND_NAMES = {
    "model": N_("viewer.models"),
    "texture": N_("viewer.textures"),
    "picture": N_("viewer.pictures"),
    "movie": N_("viewer.movies"),
    "sound": N_("viewer.sounds"),
    "bank": N_("viewer.sound_banks"),
    "data": N_("viewer.data"),
    "ui": N_("viewer.ui_files"),
    "effect": N_("common.effects"),
    "island": N_("common.islands"),
    "text": N_("viewer.language_texts"),
    "font": N_("common.fonts"),
    "other": N_("viewer.other_files"),
}

KIND_COUNTS = {
    "model": N_("viewer.models_lower"),
    "texture": N_("viewer.textures_lower"),
    "picture": N_("viewer.pictures_lower"),
    "movie": N_("viewer.movies_lower"),
    "sound": N_("viewer.sounds_lower"),
    "bank": N_("viewer.sound_banks_lower"),
    "data": N_("viewer.data_lower"),
    "ui": N_("viewer.ui_files_lower"),
    "effect": N_("common.effects_lower"),
    "island": N_("common.islands_lower"),
    "text": N_("viewer.language_texts_lower"),
    "font": N_("viewer.fonts"),
    "other": N_("viewer.other_files_lower"),
}  # the plural names a count is followed by


def kinds_summary(paths: list[str]) -> str:
    """ "12 files: 7 textures, 3 models, 2 data" for the paths to export."""
    counts = Counter(kinds.kind_of(path) for path in paths)
    parts = ", ".join(
        f"{kind_count} {tr(KIND_COUNTS[k]) if k in KIND_COUNTS else k}" for k, kind_count in counts.most_common()
    )
    if not paths:
        return tr("viewer.no_files")
    count = number(len(paths))
    if len(paths) == 1:
        return tr("viewer.count_file_parts", count=count, parts=parts)
    return tr("viewer.count_files_parts", count=count, parts=parts)


class CExportDialog(QDialog):
    """Pick the forms (always "as stored" and any converted ones the kinds present have), options and the folder."""

    def __init__(self, paths: list[str], state: dict, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or tr("common.export"))
        self.m_state = state
        self.m_paths = paths
        self.m_boxes: dict[str, QCheckBox] = {}
        remembered = set(state.get("formats", ["raw"]))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(kinds_summary(paths), wordWrap=True))
        present = [k for k in KIND_NAMES if any(kinds.kind_of(path) == k for path in paths)]
        for kind in present:
            offered = kinds.formats_for(kind)
            box = QGroupBox(tr(KIND_NAMES[kind]))
            row = QHBoxLayout(box)
            for export_format in offered:
                if export_format.key not in self.m_boxes:
                    check = QCheckBox(
                        tr(export_format.label), checked=export_format.default or export_format.key in remembered
                    )
                    check.toggled.connect(lambda _on: self._tools())
                    self.m_boxes[export_format.key] = check
                else:
                    check = QCheckBox(tr(export_format.label))
                    check.setChecked(self.m_boxes[export_format.key].isChecked())
                    check.toggled.connect(self.m_boxes[export_format.key].setChecked)
                    self.m_boxes[export_format.key].toggled.connect(check.setChecked)
                row.addWidget(check)
            row.addStretch(1)
            layout.addWidget(box)
        layout.addWidget(self._options(present))
        self.m_tools = QLabel(wordWrap=True)
        self.m_tools.setStyleSheet("color: #c80")
        layout.addWidget(self.m_tools)
        self.m_folder = QLineEdit(state.get("folder", ""))
        pick = QPushButton(tr("common.browse"))
        pick.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("viewer.into_the_folder_colon")))
        row.addWidget(self.m_folder, 1)
        row.addWidget(pick)
        layout.addLayout(row)
        buttons = QDialogButtonBox()
        buttons.addButton(tr("common.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.m_go = buttons.addButton(tr("common.export"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._tools()

    def _options(self, present: list[str]) -> QWidget:
        box = QGroupBox(tr("common.options"))
        form = QFormLayout(box)
        saved = self.m_state.get("options", {})
        self.m_depth16 = QCheckBox(tr("viewer.16_bit_png_textures"), checked=saved.get("png_depth") == 16)
        self.m_all_mips = QCheckBox(tr("viewer.every_mip_level_texture"), checked=bool(saved.get("all_mips")))
        self.m_hdr = QComboBox()
        self.m_hdr.addItems(["exr", "hdr"])  # i18n: ok (format names)
        self.m_hdr.setCurrentText(saved.get("hdr_form", "exr"))
        self.m_exposure = QDoubleSpinBox(minimum=-8.0, maximum=8.0, singleStep=0.5, value=saved.get("exposure", 0.0))
        self.m_axes = QComboBox()
        self.m_axes.addItems(["gltf", "anno"])  # i18n: ok (axes conventions)
        self.m_axes.setCurrentText(saved.get("axes", "gltf"))
        self.m_axes.setToolTip(tr("viewer.tip.gltf_mirrored_x_gltfs_right"))
        self.m_strict = QCheckBox(tr("viewer.strict_gltf_unit_normals_weights"), checked=bool(saved.get("strict")))
        self.m_lods = QCheckBox(tr("viewer.include_other_lod_files"), checked=bool(saved.get("lods")))
        self.m_textures = QCheckBox(tr("viewer.png_copies_models_textures"), checked=saved.get("with_textures", True))
        self.m_anims = QLineEdit(saved.get("anims_folder", ""))
        self.m_anims.setPlaceholderText(tr("viewer.game_folder_say_data_graphics"))
        self.m_anims.setToolTip(tr("viewer.tip.animation_files_folder_move_model"))
        self.m_rdm_target = QComboBox()
        for key, target in grannyrdm.TARGETS.items():
            self.m_rdm_target.addItem(target.name, key)  # i18n: ok (game names)
        self.m_rdm_target.setCurrentIndex(max(0, self.m_rdm_target.findData(saved.get("rdm_target", "anno117"))))
        self.m_rdm_target.setToolTip(tr("viewer.tip.rdm_game_granny_files"))
        self.m_force = QCheckBox(tr("viewer.overwrite_files_exist"), checked=bool(saved.get("force")))
        if "texture" in present:
            form.addRow(self.m_depth16)
            form.addRow(self.m_all_mips)
            form.addRow(tr("viewer.hdr_textures"), self.m_hdr)
            form.addRow(tr("viewer.exposure_stops_hdr_textures_png"), self.m_exposure)
        if "model" in present:
            form.addRow(tr("viewer.model_axes"), self.m_axes)
            form.addRow(self.m_strict)
            form.addRow(self.m_lods)
            form.addRow(self.m_textures)
            form.addRow(tr("viewer.animations_folder"), self.m_anims)
            form.addRow(tr("viewer.rdm_game_granny_models"), self.m_rdm_target)
        form.addRow(self.m_force)
        return box

    def formats(self) -> set[str]:
        """The forms ticked; "as stored" when none is."""
        return {key for key, box in self.m_boxes.items() if box.isChecked()} or {"raw"}

    def options(self) -> run.SOptions:
        return run.SOptions(
            png_depth=16 if self.m_depth16.isChecked() else 8,
            all_mips=self.m_all_mips.isChecked(),
            hdr_form=self.m_hdr.currentText(),
            exposure=self.m_exposure.value(),
            axes=self.m_axes.currentText(),
            strict=self.m_strict.isChecked(),
            lods=self.m_lods.isChecked(),
            with_textures=self.m_textures.isChecked(),
            anims_folder=self.m_anims.text().strip(),
            rdm_target=self.m_rdm_target.currentData(),
            force=self.m_force.isChecked(),
        )

    def choice(self) -> tuple[set[str], run.SOptions, Path]:
        return self.formats(), self.options(), Path(self.m_folder.text().strip())

    def _tools(self) -> None:
        self.m_tools.setText(run.missing_tools(self.formats()))

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("common.export_into"), self.m_folder.text())
        if folder:
            self.m_folder.setText(folder)

    def _accept(self) -> None:
        if not self.m_folder.text().strip():
            QMessageBox.information(self, self.windowTitle(), tr("viewer.pick_folder_export"))
            return
        formats, options, folder = self.choice()
        self.m_state.update(formats=sorted(formats), folder=str(folder), options=asdict(options))
        self.accept()


class _CProgress(QObject):
    step = Signal(int, int, str)
    finished = Signal(object)


class CExportRun(QObject):
    """One export on a thread: a progress dialog with Cancel, then a summary (the folder and the index); ``done`` is
    emitted with the run when it ended and the run is about to be deleted."""

    done = Signal(object)

    def __init__(self, source, paths: list[str], formats: set[str], options: run.SOptions, out: Path, parent) -> None:
        super().__init__(parent)
        self.m_source, self.m_paths, self.m_formats, self.m_options, self.m_out = source, paths, formats, options, out
        self.m_parent_widget = parent
        self.m_entries: list[run.SEntry] = []
        self.m_stop = threading.Event()
        self.m_signals = _CProgress()
        self.m_dialog = QProgressDialog(tr("viewer.exporting"), tr("common.cancel"), 0, max(len(paths), 1), parent)
        self.m_dialog.setWindowTitle(tr("common.export"))
        self.m_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.m_dialog.setMinimumDuration(400)
        self.m_dialog.canceled.connect(self.m_stop.set)
        self.destroyed.connect(self.m_stop.set)  # the window closed: nobody to export for, the thread ends
        self.m_signals.step.connect(self._step)
        self.m_signals.finished.connect(self._finished)

    def start(self) -> None:
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        try:
            entries = run.export_many(
                self.m_source,
                self.m_paths,
                self.m_formats,
                self.m_out,
                self.m_options,
                progress=lambda done, total, path: self._tell(self.m_signals.step, done, total, path),
                cancelled=self.m_stop.is_set,
            )
        except Exception as exc:  # the dialog says why
            entries = exc
        self._tell(self.m_signals.finished, entries)

    def _tell(self, signal, *values) -> None:
        """Emit from the worker; a window that closed meanwhile took the receiver with it."""
        try:
            signal.emit(*values)
        except RuntimeError:
            self.m_stop.set()

    def _step(self, done: int, total: int, path: str) -> None:
        if self.m_stop.is_set():  # a step still under way after Cancel: setting a value would show the dialog again
            return
        self.m_dialog.setMaximum(total)
        self.m_dialog.setValue(done)
        self.m_dialog.setLabelText(path)

    def _finished(self, entries) -> None:
        try:
            self._report(entries)
        finally:  # the run is over: its dialog and it go, so a window that exports often keeps none of them
            self.done.emit(self)
            self.m_dialog.deleteLater()
            self.deleteLater()

    def _report(self, entries) -> None:
        self.m_dialog.reset()
        if isinstance(entries, Exception):
            QMessageBox.warning(
                self.m_parent_widget,
                tr("common.export"),
                tr("viewer.export_stopped_kind_error", kind=type(entries).__name__, error=entries),
            )
            return
        self.m_entries = entries
        QMessageBox.information(
            self.m_parent_widget, tr("common.export"), summary_text(entries, self.m_out, self.m_stop.is_set())
        )


def summary_text(entries: list[run.SEntry], out: Path, cancelled: bool = False) -> str:
    """What an export did: counts by status, the first failures, where the index is."""
    counts = Counter(entry.status for entry in entries)
    written = number(counts.get("written", 0))
    lines = [
        tr("viewer.count_exported_cancelled", count=written)
        if cancelled
        else tr("viewer.count_exported", count=written)
    ]
    for status, label in (
        ("skipped", N_("viewer.left_alone_they_exist")),
        ("failed", N_("common.failed")),
        ("tool-missing", N_("viewer.need_tool")),
    ):
        if counts.get(status):
            lines.append(tr("viewer.count_what", count=number(counts[status]), what=tr(label)))
    shown = [entry for entry in entries if entry.status in ("failed", "tool-missing")][:5]
    lines += [f"  {entry.source}: {entry.notes[0] if entry.notes else entry.status}" for entry in shown]
    lines.append(tr("viewer.index_every_file_json_csv", folder=out))
    return "\n".join(lines)
