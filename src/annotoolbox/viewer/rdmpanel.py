"""The viewer's RDM tools: the Inspect panel (an ``.rdm``'s elements, and a comparison with another file) and the
dialog that imports a glTF back into ``.rdm`` files with the import's report.

``InspectPanel`` is a dock: ``show_rdm(name, data)`` lists a model's meshes, submeshes, materials, skeleton and
animations; **Values** adds a checksum per vertex element, index list and track; **Compare with...** diffs it against
another ``.rdm`` or glTF (the glTF as the ``.rdm`` its import would write). ``ImportDialog`` is File > Import a model.
What the import keeps, drops and refuses: docs/rdm-gltf.md.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..formats import rdmlayout
from ..infra.i18n import tr
from ..rdm import actions, listing
from ..rdm.gltfdoc import Options
from .projectpart import EDITS


def _mono(view: QPlainTextEdit) -> None:
    view.setReadOnly(True)
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))


class InspectPanel(QWidget):
    """The element listing of the open ``.rdm`` and its comparison with another file."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.name, self.data = "", b""
        self.lines: list[str] = []
        self.values = QCheckBox(tr("viewer.values"))
        self.values.setToolTip(tr("viewer.tip.add_checksum_every_vertex_element"))
        self.compare = QPushButton(tr("viewer.compare_with_dots"))
        self.compare.setToolTip(tr("viewer.tip.another_rdm_gltf_import_write"))
        self.view = QPlainTextEdit()
        _mono(self.view)
        self.note = QLabel(tr("viewer.open_rdm_list_elements"), wordWrap=True)
        row = QHBoxLayout()
        row.addWidget(self.values)
        row.addWidget(self.compare)
        row.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addLayout(row)
        lay.addWidget(self.note)
        lay.addWidget(self.view, 1)
        self.values.toggled.connect(lambda _on: self.refresh())
        self.compare.clicked.connect(lambda _c=False: self.compare_with())

    def show_rdm(self, name: str, data: bytes) -> None:
        self.name, self.data = name, data
        self.refresh()

    def clear(self, why: str = "") -> None:
        self.name, self.data, self.lines = "", b"", []
        self.view.clear()
        self.note.setText(why or tr("viewer.open_rdm_list_elements"))

    def refresh(self) -> None:
        if not self.name:
            return
        try:
            self.lines = actions.describe(self.name, self.data, self.values.isChecked())
        except ValueError as exc:
            self.lines = []
            self.view.setPlainText(tr("viewer.not_readable_error", error=exc))
            return
        self.note.setText(tr("viewer.name_count_lines", name=self.name, count=len(self.lines)))
        self.view.setPlainText("\n".join(self.lines))

    def compare_with(self, other: Path | None = None) -> None:
        """Diff the open file against ``other`` (asked for when None)."""
        if not self.name:
            return
        if other is None:
            found, _ = QFileDialog.getOpenFileName(
                self, tr("viewer.compare_with"), "", tr("viewer.models_rdm_glb_gltf")
            )
            if not found:
                return
            other = Path(found)
        try:
            theirs = actions.describe_as_rdm(other, self.values.isChecked(), Path(self.name).name)
            found_lines = listing.diff(self.lines, theirs, (self.name, other.name))
        except (OSError, ValueError) as exc:
            self.view.setPlainText(tr("viewer.cannot_compare_name_error", name=other.name, error=exc))
            return
        same = tr("viewer.same_every_element_value") if self.values.isChecked() else tr("viewer.same_every_element")
        self.note.setText(
            tr(
                "viewer.name_against_other_count_differences",
                name=self.name,
                other=other.name,
                count=len(found_lines),
            )
        )
        self.view.setPlainText("\n".join(found_lines) if found_lines else same)


class ImportDialog(QDialog):
    """Import a glTF into ``.rdm`` files: pick the file and the options, import, read the report."""

    def __init__(self, parent=None, state: dict | None = None, start: str = "", project=None, game: str = "") -> None:
        super().__init__(parent)
        self.project, self.game = project, game
        self.setWindowTitle(tr("viewer.title.import_model_gltf_rdm"))
        self.resize(640, 520)
        self.state = state if state is not None else {}
        self.source = QLineEdit(start)
        self.target = QLineEdit(self.state.get("import_folder", ""))
        self.layout_name = QComboBox(editable=True)
        self.layout_name.addItems(["", *rdmlayout.KNOWN])
        self.like = QLineEdit()
        self.like.setPlaceholderText(tr("viewer.rdm_whose_layouts_names_order"))
        self.axes = QComboBox()
        self.axes.addItem(tr("viewer.files_own"), "")
        self.axes.addItem("gltf", "gltf")  # i18n: ok (an axes convention)
        self.axes.addItem("anno", "anno")  # i18n: ok (an axes convention)
        self.bake = QCheckBox(tr("viewer.bake_node_transforms_static_meshes"), checked=True)
        self.joints = QComboBox()
        self.joints.addItem(tr("common.name_lower"), "name")  # i18n: ok (the datum)
        self.joints.addItem(tr("viewer.index"), "index")  # i18n: ok (the datum)
        self.order = QLineEdit()
        self.order.setPlaceholderText(tr("viewer.material_names_model_configs_order"))
        self.fps = QSpinBox(minimum=0, maximum=240, specialValueText=tr("viewer.from_the_keys"))
        self.force = QCheckBox(tr("viewer.overwrite_files_exist"))
        self.keep = QCheckBox(tr("viewer.also_add_model_projects_mod"))
        self.game_folder = QLineEdit(self.state.get("import_game_folder", ""))
        self.game_folder.setPlaceholderText(tr("viewer.folder_file_replaces_e_g"))
        form = QFormLayout()
        form.addRow(tr("viewer.gltf_file"), self._with_browse(self.source, self._pick_source))
        form.addRow(tr("viewer.into_the_folder"), self._with_browse(self.target, self._pick_folder))
        form.addRow(tr("viewer.vertex_layout"), self.layout_name)
        form.addRow(tr("viewer.follow_file"), self._with_browse(self.like, self._pick_like))
        form.addRow(tr("viewer.axes"), self.axes)
        form.addRow(self.bake)
        form.addRow(tr("viewer.joint_names"), self.joints)
        form.addRow(tr("viewer.material_order"), self.order)
        form.addRow(tr("viewer.animation_frame_rate"), self.fps)
        form.addRow(self.force)
        if project is not None:
            form.addRow(self.keep)
            form.addRow(self.game_folder)
        self.report = QPlainTextEdit()
        _mono(self.report)
        self.report.setPlaceholderText(tr("viewer.imports_report_files_written_what"))
        buttons = QDialogButtonBox()
        buttons.addButton(tr("common.close"), QDialogButtonBox.ButtonRole.RejectRole)
        run_button = buttons.addButton(tr("viewer.import"), QDialogButtonBox.ButtonRole.ActionRole)
        run_button.clicked.connect(self.run)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.report, 1)
        lay.addWidget(buttons)

    @staticmethod
    def _with_browse(edit: QLineEdit, pick) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("…")
        button.clicked.connect(lambda _c=False: pick())
        row.addWidget(edit, 1)
        row.addWidget(button)
        return box

    def _pick_source(self) -> None:
        found, _ = QFileDialog.getOpenFileName(
            self, tr("viewer.a_gltf_file"), self.source.text(), "glTF (*.glb *.gltf)"
        )  # i18n: ok (a file filter)
        if found:
            self.source.setText(found)
            if not self.target.text():
                self.target.setText(str(Path(found).parent))

    def _pick_folder(self) -> None:
        found = QFileDialog.getExistingDirectory(self, tr("viewer.write_rdm_files"), self.target.text())
        if found:
            self.target.setText(found)

    def _pick_like(self) -> None:
        found, _ = QFileDialog.getOpenFileName(
            self, tr("viewer.follow_rdm"), self.like.text(), "RDM (*.rdm)"
        )  # i18n: ok (a file filter)
        if found:
            self.like.setText(found)

    def options(self) -> Options:
        from ..formats.rdmread import read_rdm

        like = read_rdm(Path(self.like.text()).read_bytes()) if self.like.text().strip() else None
        axes = self.axes.currentData()
        order = tuple(n.strip() for n in self.order.text().split(",") if n.strip())
        return Options(
            axes if axes in ("gltf", "anno") else None,
            self.layout_name.currentText().strip() or None,
            None,
            self.bake.isChecked(),
            self.joints.currentData(),
            order,
            self.fps.value() or None,
            like,
        )

    def run(self) -> None:
        """Import and write; the report (or the reason it was refused) fills the text box."""
        source, folder = Path(self.source.text().strip()), Path(self.target.text().strip())
        if not source.is_file() or not self.target.text().strip():
            self.report.setPlainText(tr("viewer.pick_gltf_file_folder_write"))
            return
        try:
            report = actions.import_model(source, None, self.options())
            written, skipped = actions.write_files(report, folder, self.force.isChecked())
        except (OSError, ValueError) as exc:
            self.report.setPlainText(tr("viewer.refused_error", error=exc))
            return
        self.state["import_folder"] = str(folder)
        lines = [tr("common.wrote_path", path=p) for p in written]
        lines += [tr("viewer.left_alone_exists", name=n) for n in skipped]
        lines += self._keep_in_project(report)
        lines += ["", tr("viewer.what_import"), *[f"  - {n}" for n in report.notes]] if report.notes else []
        self.report.setPlainText("\n".join(lines) or tr("viewer.nothing_written"))

    def _keep_in_project(self, report: actions.ImportReport) -> list[str]:
        """Add the imported files to the project's mod (when asked): the lines the report shows."""
        if self.project is None or not self.keep.isChecked():
            return []
        folder = self.game_folder.text().strip().strip("/")
        if not folder:
            return [tr("viewer.not_added_project_name_game")]
        self.state["import_game_folder"] = folder
        saved = EDITS.save_files(self.project, self.game, {f"{folder}/{n}": d for n, d in report.files.items()})
        return [tr("viewer.added_projects_mod", path=p) for p in saved]
