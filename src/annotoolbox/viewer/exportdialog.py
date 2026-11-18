"""The export dialog: which forms of which kinds of file, the options, the folder; and the run with its progress.

``ExportDialog(paths, state)`` asks, ``choice()`` returns what was chosen (the form keys, the ``Options`` and the
folder) and ``state`` (a dict the caller keeps in its settings) remembers it for next time. ``kinds_summary`` is
the line that says what is being exported. ``ExportRun`` runs ``exporting.run.export_many`` on a thread with a progress
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
    counts = Counter(kinds.kind_of(p) for p in paths)
    parts = ", ".join(f"{n} {tr(KIND_COUNTS[k]) if k in KIND_COUNTS else k}" for k, n in counts.most_common())
    if not paths:
        return tr("viewer.no_files")
    count = number(len(paths))
    if len(paths) == 1:
        return tr("viewer.count_file_parts", count=count, parts=parts)
    return tr("viewer.count_files_parts", count=count, parts=parts)


class ExportDialog(QDialog):
    """Pick the forms (always "as stored" and any converted ones the kinds present have), options and the folder."""

    def __init__(self, paths: list[str], state: dict, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or tr("common.export"))
        self.state = state
        self.paths = paths
        self.boxes: dict[str, QCheckBox] = {}
        remembered = set(state.get("formats", ["raw"]))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(kinds_summary(paths), wordWrap=True))
        present = [k for k in KIND_NAMES if any(kinds.kind_of(p) == k for p in paths)]
        for kind in present:
            offered = kinds.formats_for(kind)
            box = QGroupBox(tr(KIND_NAMES[kind]))
            row = QHBoxLayout(box)
            for fmt in offered:
                if fmt.key not in self.boxes:
                    check = QCheckBox(tr(fmt.label), checked=fmt.default or fmt.key in remembered)
                    check.toggled.connect(lambda _on: self._tools())
                    self.boxes[fmt.key] = check
                else:
                    check = QCheckBox(tr(fmt.label))
                    check.setChecked(self.boxes[fmt.key].isChecked())
                    check.toggled.connect(self.boxes[fmt.key].setChecked)
                    self.boxes[fmt.key].toggled.connect(check.setChecked)
                row.addWidget(check)
            row.addStretch(1)
            lay.addWidget(box)
        lay.addWidget(self._options(present))
        self.tools = QLabel(wordWrap=True)
        self.tools.setStyleSheet("color: #c80")
        lay.addWidget(self.tools)
        self.folder = QLineEdit(state.get("folder", ""))
        pick = QPushButton(tr("common.browse"))
        pick.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("viewer.into_the_folder_colon")))
        row.addWidget(self.folder, 1)
        row.addWidget(pick)
        lay.addLayout(row)
        buttons = QDialogButtonBox()
        buttons.addButton(tr("common.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.go = buttons.addButton(tr("common.export"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._tools()

    def _options(self, present: list[str]) -> QWidget:
        box = QGroupBox(tr("common.options"))
        form = QFormLayout(box)
        saved = self.state.get("options", {})
        self.depth16 = QCheckBox(tr("viewer.16_bit_png_textures"), checked=saved.get("png_depth") == 16)
        self.all_mips = QCheckBox(tr("viewer.every_mip_level_texture"), checked=bool(saved.get("all_mips")))
        self.hdr = QComboBox()
        self.hdr.addItems(["exr", "hdr"])  # i18n: ok (format names)
        self.hdr.setCurrentText(saved.get("hdr_form", "exr"))
        self.exposure = QDoubleSpinBox(minimum=-8.0, maximum=8.0, singleStep=0.5, value=saved.get("exposure", 0.0))
        self.axes = QComboBox()
        self.axes.addItems(["gltf", "anno"])  # i18n: ok (axes conventions)
        self.axes.setCurrentText(saved.get("axes", "gltf"))
        self.axes.setToolTip(tr("viewer.tip.gltf_mirrored_x_gltfs_right"))
        self.strict = QCheckBox(tr("viewer.strict_gltf_unit_normals_weights"), checked=bool(saved.get("strict")))
        self.lods = QCheckBox(tr("viewer.include_other_lod_files"), checked=bool(saved.get("lods")))
        self.textures = QCheckBox(tr("viewer.png_copies_models_textures"), checked=saved.get("with_textures", True))
        self.anims = QLineEdit(saved.get("anims_folder", ""))
        self.anims.setPlaceholderText(tr("viewer.game_folder_say_data_graphics"))
        self.anims.setToolTip(tr("viewer.tip.animation_files_folder_move_model"))
        self.rdm_target = QComboBox()
        for key, target in grannyrdm.TARGETS.items():
            self.rdm_target.addItem(target.name, key)  # i18n: ok (game names)
        self.rdm_target.setCurrentIndex(max(0, self.rdm_target.findData(saved.get("rdm_target", "anno117"))))
        self.rdm_target.setToolTip(tr("viewer.tip.rdm_game_granny_files"))
        self.force = QCheckBox(tr("viewer.overwrite_files_exist"), checked=bool(saved.get("force")))
        if "texture" in present:
            form.addRow(self.depth16)
            form.addRow(self.all_mips)
            form.addRow(tr("viewer.hdr_textures"), self.hdr)
            form.addRow(tr("viewer.exposure_stops_hdr_textures_png"), self.exposure)
        if "model" in present:
            form.addRow(tr("viewer.model_axes"), self.axes)
            form.addRow(self.strict)
            form.addRow(self.lods)
            form.addRow(self.textures)
            form.addRow(tr("viewer.animations_folder"), self.anims)
            form.addRow(tr("viewer.rdm_game_granny_models"), self.rdm_target)
        form.addRow(self.force)
        return box

    def formats(self) -> set[str]:
        """The forms ticked; "as stored" when none is."""
        return {key for key, box in self.boxes.items() if box.isChecked()} or {"raw"}

    def options(self) -> run.Options:
        return run.Options(
            png_depth=16 if self.depth16.isChecked() else 8,
            all_mips=self.all_mips.isChecked(),
            hdr_form=self.hdr.currentText(),
            exposure=self.exposure.value(),
            axes=self.axes.currentText(),
            strict=self.strict.isChecked(),
            lods=self.lods.isChecked(),
            with_textures=self.textures.isChecked(),
            anims_folder=self.anims.text().strip(),
            rdm_target=self.rdm_target.currentData(),
            force=self.force.isChecked(),
        )

    def choice(self) -> tuple[set[str], run.Options, Path]:
        return self.formats(), self.options(), Path(self.folder.text().strip())

    def _tools(self) -> None:
        self.tools.setText(run.missing_tools(self.formats()))

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("common.export_into"), self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _accept(self) -> None:
        if not self.folder.text().strip():
            QMessageBox.information(self, self.windowTitle(), tr("viewer.pick_folder_export"))
            return
        formats, options, folder = self.choice()
        self.state.update(formats=sorted(formats), folder=str(folder), options=asdict(options))
        self.accept()


class _Progress(QObject):
    step = Signal(int, int, str)
    finished = Signal(object)


class ExportRun(QObject):
    """One export on a thread: a progress dialog with Cancel, then a summary (the folder and the index)."""

    def __init__(self, source, paths: list[str], formats: set[str], options: run.Options, out: Path, parent) -> None:
        super().__init__(parent)
        self.source, self.paths, self.formats, self.options, self.out = source, paths, formats, options, out
        self.parent_widget = parent
        self.entries: list[run.Entry] = []
        self.stop = threading.Event()
        self.signals = _Progress()
        self.dialog = QProgressDialog(tr("viewer.exporting"), tr("common.cancel"), 0, max(len(paths), 1), parent)
        self.dialog.setWindowTitle(tr("common.export"))
        self.dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.dialog.setMinimumDuration(400)
        self.dialog.canceled.connect(self.stop.set)
        self.destroyed.connect(self.stop.set)  # the window closed: nobody to export for, the thread ends
        self.signals.step.connect(self._step)
        self.signals.finished.connect(self._finished)

    def start(self) -> None:
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        try:
            entries = run.export_many(
                self.source,
                self.paths,
                self.formats,
                self.out,
                self.options,
                progress=lambda n, total, path: self._tell(self.signals.step, n, total, path),
                cancelled=self.stop.is_set,
            )
        except Exception as exc:  # the dialog says why
            entries = exc
        self._tell(self.signals.finished, entries)

    def _tell(self, signal, *values) -> None:
        """Emit from the worker; a window that closed meanwhile took the receiver with it."""
        try:
            signal.emit(*values)
        except RuntimeError:
            self.stop.set()

    def _step(self, done: int, total: int, path: str) -> None:
        if self.stop.is_set():  # a step still under way after Cancel: setting a value would show the dialog again
            return
        self.dialog.setMaximum(total)
        self.dialog.setValue(done)
        self.dialog.setLabelText(path)

    def _finished(self, entries) -> None:
        self.dialog.reset()
        if isinstance(entries, Exception):
            QMessageBox.warning(
                self.parent_widget,
                tr("common.export"),
                tr("viewer.export_stopped_kind_error", kind=type(entries).__name__, error=entries),
            )
            return
        self.entries = entries
        QMessageBox.information(
            self.parent_widget, tr("common.export"), summary_text(entries, self.out, self.stop.is_set())
        )


def summary_text(entries: list[run.Entry], out: Path, cancelled: bool = False) -> str:
    """What an export did: counts by status, the first failures, where the index is."""
    counts = Counter(e.status for e in entries)
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
    shown = [e for e in entries if e.status in ("failed", "tool-missing")][:5]
    lines += [f"  {e.source}: {e.notes[0] if e.notes else e.status}" for e in shown]
    lines.append(tr("viewer.index_every_file_json_csv", folder=out))
    return "\n".join(lines)
