"""The viewer's RDM tools: the Inspect panel (an ``.rdm``'s elements, and a comparison with another file) and the
dialog that imports a glTF back into ``.rdm`` files with the import's report.

``CInspectPanel`` is a dock: ``show_rdm(name, data)`` lists a model's meshes, submeshes, materials, skeleton and
animations; **Values** adds a checksum per vertex element, index list and track; **Compare with...** diffs it against
another ``.rdm`` or glTF (the glTF as the ``.rdm`` its import would write). ``CImportDialog`` is File > Import a model.
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
from ..rdm.gltfdoc import SOptions
from .projectpart import EDITS


def _mono(view: QPlainTextEdit) -> None:
    view.setReadOnly(True)
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))


class CInspectPanel(QWidget):
    """The element listing of the open ``.rdm`` and its comparison with another file."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_name, self.m_data = "", b""
        self.m_lines: list[str] = []
        self.m_values = QCheckBox(tr("viewer.values"))
        self.m_values.setToolTip(tr("viewer.tip.add_checksum_every_vertex_element"))
        self.m_compare = QPushButton(tr("viewer.compare_with_dots"))
        self.m_compare.setToolTip(tr("viewer.tip.another_rdm_gltf_import_write"))
        self.m_view = QPlainTextEdit()
        _mono(self.m_view)
        self.m_note = QLabel(tr("viewer.open_rdm_list_elements"), wordWrap=True)
        row = QHBoxLayout()
        row.addWidget(self.m_values)
        row.addWidget(self.m_compare)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(row)
        layout.addWidget(self.m_note)
        layout.addWidget(self.m_view, 1)
        self.m_values.toggled.connect(lambda _on: self.refresh())
        self.m_compare.clicked.connect(lambda _c=False: self.compare_with())

    def show_rdm(self, name: str, data: bytes) -> None:
        self.m_name, self.m_data = name, data
        self.refresh()

    def clear(self, why: str = "") -> None:
        self.m_name, self.m_data, self.m_lines = "", b"", []
        self.m_view.clear()
        self.m_note.setText(why or tr("viewer.open_rdm_list_elements"))

    def refresh(self) -> None:
        if not self.m_name:
            return
        try:
            self.m_lines = actions.describe(self.m_name, self.m_data, self.m_values.isChecked())
        except ValueError as exc:
            self.m_lines = []
            self.m_view.setPlainText(tr("viewer.not_readable_error", error=exc))
            return
        self.m_note.setText(tr("viewer.name_count_lines", name=self.m_name, count=len(self.m_lines)))
        self.m_view.setPlainText("\n".join(self.m_lines))

    def compare_with(self, other: Path | None = None) -> None:
        """Diff the open file against ``other`` (asked for when None)."""
        if not self.m_name:
            return
        if other is None:
            found, _ = QFileDialog.getOpenFileName(
                self, tr("viewer.compare_with"), "", tr("viewer.models_rdm_glb_gltf")
            )
            if not found:
                return
            other = Path(found)
        try:
            theirs = actions.describe_as_rdm(other, self.m_values.isChecked(), Path(self.m_name).name)
            found_lines = listing.diff(self.m_lines, theirs, (self.m_name, other.name))
        except (OSError, ValueError) as exc:
            self.m_view.setPlainText(tr("viewer.cannot_compare_name_error", name=other.name, error=exc))
            return
        same = tr("viewer.same_every_element_value") if self.m_values.isChecked() else tr("viewer.same_every_element")
        self.m_note.setText(
            tr(
                "viewer.name_against_other_count_differences",
                name=self.m_name,
                other=other.name,
                count=len(found_lines),
            )
        )
        self.m_view.setPlainText("\n".join(found_lines) if found_lines else same)


class CImportDialog(QDialog):
    """Import a glTF into ``.rdm`` files: pick the file and the options, import, read the report."""

    def __init__(self, parent=None, state: dict | None = None, start: str = "", project=None, game: str = "") -> None:
        super().__init__(parent)
        self.m_project, self.m_game = project, game
        self.setWindowTitle(tr("viewer.title.import_model_gltf_rdm"))
        self.resize(640, 520)
        self.m_state = state if state is not None else {}
        self.m_source = QLineEdit(start)
        self.m_target = QLineEdit(self.m_state.get("import_folder", ""))
        self.m_layout_name = QComboBox(editable=True)
        self.m_layout_name.addItems(["", *rdmlayout.KNOWN])
        self.m_like = QLineEdit()
        self.m_like.setPlaceholderText(tr("viewer.rdm_whose_layouts_names_order"))
        self.m_axes = QComboBox()
        self.m_axes.addItem(tr("viewer.files_own"), "")
        self.m_axes.addItem("gltf", "gltf")  # i18n: ok (an axes convention)
        self.m_axes.addItem("anno", "anno")  # i18n: ok (an axes convention)
        self.m_bake = QCheckBox(tr("viewer.bake_node_transforms_static_meshes"), checked=True)
        self.m_joints = QComboBox()
        self.m_joints.addItem(tr("common.name_lower"), "name")  # i18n: ok (the datum)
        self.m_joints.addItem(tr("viewer.index"), "index")  # i18n: ok (the datum)
        self.m_order = QLineEdit()
        self.m_order.setPlaceholderText(tr("viewer.material_names_model_configs_order"))
        self.m_fps = QSpinBox(minimum=0, maximum=240, specialValueText=tr("viewer.from_the_keys"))
        self.m_force = QCheckBox(tr("viewer.overwrite_files_exist"))
        self.m_keep = QCheckBox(tr("viewer.also_add_model_projects_mod"))
        self.m_game_folder = QLineEdit(self.m_state.get("import_game_folder", ""))
        self.m_game_folder.setPlaceholderText(tr("viewer.folder_file_replaces_e_g"))
        form = QFormLayout()
        form.addRow(tr("viewer.gltf_file"), self._with_browse(self.m_source, self._pick_source))
        form.addRow(tr("viewer.into_the_folder"), self._with_browse(self.m_target, self._pick_folder))
        form.addRow(tr("viewer.vertex_layout"), self.m_layout_name)
        form.addRow(tr("viewer.follow_file"), self._with_browse(self.m_like, self._pick_like))
        form.addRow(tr("viewer.axes"), self.m_axes)
        form.addRow(self.m_bake)
        form.addRow(tr("viewer.joint_names"), self.m_joints)
        form.addRow(tr("viewer.material_order"), self.m_order)
        form.addRow(tr("viewer.animation_frame_rate"), self.m_fps)
        form.addRow(self.m_force)
        if project is not None:
            form.addRow(self.m_keep)
            form.addRow(self.m_game_folder)
        self.m_report = QPlainTextEdit()
        _mono(self.m_report)
        self.m_report.setPlaceholderText(tr("viewer.imports_report_files_written_what"))
        buttons = QDialogButtonBox()
        buttons.addButton(tr("common.close"), QDialogButtonBox.ButtonRole.RejectRole)
        run_button = buttons.addButton(tr("viewer.import"), QDialogButtonBox.ButtonRole.ActionRole)
        run_button.clicked.connect(self.run)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.m_report, 1)
        layout.addWidget(buttons)

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
            self, tr("viewer.a_gltf_file"), self.m_source.text(), "glTF (*.glb *.gltf)"
        )  # i18n: ok (a file filter)
        if found:
            self.m_source.setText(found)
            if not self.m_target.text():
                self.m_target.setText(str(Path(found).parent))

    def _pick_folder(self) -> None:
        found = QFileDialog.getExistingDirectory(self, tr("viewer.write_rdm_files"), self.m_target.text())
        if found:
            self.m_target.setText(found)

    def _pick_like(self) -> None:
        found, _ = QFileDialog.getOpenFileName(
            self, tr("viewer.follow_rdm"), self.m_like.text(), "RDM (*.rdm)"
        )  # i18n: ok (a file filter)
        if found:
            self.m_like.setText(found)

    def options(self) -> SOptions:
        from ..formats.rdmread import read_rdm

        like = read_rdm(Path(self.m_like.text()).read_bytes()) if self.m_like.text().strip() else None
        axes = self.m_axes.currentData()
        order = tuple(name.strip() for name in self.m_order.text().split(",") if name.strip())
        return SOptions(
            axes if axes in ("gltf", "anno") else None,
            self.m_layout_name.currentText().strip() or None,
            None,
            self.m_bake.isChecked(),
            self.m_joints.currentData(),
            order,
            self.m_fps.value() or None,
            like,
        )

    def run(self) -> None:
        """Import and write; the report (or the reason it was refused) fills the text box."""
        source, folder = Path(self.m_source.text().strip()), Path(self.m_target.text().strip())
        if not source.is_file() or not self.m_target.text().strip():
            self.m_report.setPlainText(tr("viewer.pick_gltf_file_folder_write"))
            return
        try:
            report = actions.import_model(source, None, self.options())
            written, skipped = actions.write_files(report, folder, self.m_force.isChecked())
        except (OSError, ValueError) as exc:
            self.m_report.setPlainText(tr("viewer.refused_error", error=exc))
            return
        self.m_state["import_folder"] = str(folder)
        lines = [tr("common.wrote_path", path=path) for path in written]
        lines += [tr("viewer.left_alone_exists", name=n) for n in skipped]
        lines += self._keep_in_project(report)
        lines += ["", tr("viewer.what_import"), *[f"  - {n}" for n in report.notes]] if report.notes else []
        self.m_report.setPlainText("\n".join(lines) or tr("viewer.nothing_written"))

    def _keep_in_project(self, report: actions.SImportReport) -> list[str]:
        """Add the imported files to the project's mod (when asked): the lines the report shows."""
        if self.m_project is None or not self.m_keep.isChecked():
            return []
        folder = self.m_game_folder.text().strip().strip("/")
        if not folder:
            return [tr("viewer.not_added_project_name_game")]
        self.m_state["import_game_folder"] = folder
        saved = EDITS.save_files(
            self.m_project, self.m_game, {f"{folder}/{name}": data for name, data in report.files.items()}
        )
        return [tr("viewer.added_projects_mod", path=path) for path in saved]
