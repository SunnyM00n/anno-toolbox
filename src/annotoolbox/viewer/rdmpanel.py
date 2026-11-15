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
        self.values = QCheckBox(tr("Values"))
        self.values.setToolTip(tr("add a checksum of every vertex element, index list and animation track"))
        self.compare = QPushButton(tr("Compare with..."))
        self.compare.setToolTip(tr("another .rdm, or a glTF as the .rdm its import would write"))
        self.view = QPlainTextEdit()
        _mono(self.view)
        self.note = QLabel(tr("Open a .rdm to list its elements."), wordWrap=True)
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
        self.note.setText(why or tr("Open a .rdm to list its elements."))

    def refresh(self) -> None:
        if not self.name:
            return
        try:
            self.lines = actions.describe(self.name, self.data, self.values.isChecked())
        except ValueError as exc:
            self.lines = []
            self.view.setPlainText(tr("not readable: {error}", error=exc))
            return
        self.note.setText(tr("{name}: {count} lines", name=self.name, count=len(self.lines)))
        self.view.setPlainText("\n".join(self.lines))

    def compare_with(self, other: Path | None = None) -> None:
        """Diff the open file against ``other`` (asked for when None)."""
        if not self.name:
            return
        if other is None:
            found, _ = QFileDialog.getOpenFileName(self, tr("Compare with"), "", tr("Models (*.rdm *.glb *.gltf)"))
            if not found:
                return
            other = Path(found)
        try:
            theirs = actions.describe_as_rdm(other, self.values.isChecked(), Path(self.name).name)
            found_lines = listing.diff(self.lines, theirs, (self.name, other.name))
        except (OSError, ValueError) as exc:
            self.view.setPlainText(tr("cannot compare with {name}: {error}", name=other.name, error=exc))
            return
        same = tr("the same in every element and value") if self.values.isChecked() else tr("the same in every element")
        self.note.setText(
            tr(
                "{name} against {other}: {count} differences",
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
        self.setWindowTitle(tr("Import a model (glTF to .rdm)"))
        self.resize(640, 520)
        self.state = state if state is not None else {}
        self.source = QLineEdit(start)
        self.target = QLineEdit(self.state.get("import_folder", ""))
        self.layout_name = QComboBox(editable=True)
        self.layout_name.addItems(["", *rdmlayout.KNOWN])
        self.like = QLineEdit()
        self.like.setPlaceholderText(tr("an .rdm whose layouts, names and order to follow (optional)"))
        self.axes = QComboBox()
        self.axes.addItem(tr("the file's own"), "")
        self.axes.addItem("gltf", "gltf")  # i18n: ok (an axes convention)
        self.axes.addItem("anno", "anno")  # i18n: ok (an axes convention)
        self.bake = QCheckBox(tr("bake the node transforms into static meshes"), checked=True)
        self.joints = QComboBox()
        self.joints.addItem(tr("name"), "name")  # i18n: ok (the datum)
        self.joints.addItem(tr("index"), "index")  # i18n: ok (the datum)
        self.order = QLineEdit()
        self.order.setPlaceholderText(tr("material names in the model config's order, comma separated (optional)"))
        self.fps = QSpinBox(minimum=0, maximum=240, specialValueText=tr("from the keys"))
        self.force = QCheckBox(tr("overwrite files that exist"))
        self.keep = QCheckBox(tr("also add the model to the project's mod, at the game folder:"))
        self.game_folder = QLineEdit(self.state.get("import_game_folder", ""))
        self.game_folder.setPlaceholderText(tr("the folder of the file it replaces, e.g. data/base/graphics/.../rdm"))
        form = QFormLayout()
        form.addRow(tr("glTF file"), self._with_browse(self.source, self._pick_source))
        form.addRow(tr("Into the folder"), self._with_browse(self.target, self._pick_folder))
        form.addRow(tr("Vertex layout"), self.layout_name)
        form.addRow(tr("Follow the file"), self._with_browse(self.like, self._pick_like))
        form.addRow(tr("Axes"), self.axes)
        form.addRow(self.bake)
        form.addRow(tr("Joint names from"), self.joints)
        form.addRow(tr("Material order"), self.order)
        form.addRow(tr("Animation frame rate"), self.fps)
        form.addRow(self.force)
        if project is not None:
            form.addRow(self.keep)
            form.addRow(self.game_folder)
        self.report = QPlainTextEdit()
        _mono(self.report)
        self.report.setPlaceholderText(
            tr("The import's report: the files written, what was dropped, rebuilt or refused.")
        )
        buttons = QDialogButtonBox()
        buttons.addButton(tr("Close"), QDialogButtonBox.ButtonRole.RejectRole)
        run_button = buttons.addButton(tr("Import"), QDialogButtonBox.ButtonRole.ActionRole)
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
        button = QPushButton("...")
        button.clicked.connect(lambda _c=False: pick())
        row.addWidget(edit, 1)
        row.addWidget(button)
        return box

    def _pick_source(self) -> None:
        found, _ = QFileDialog.getOpenFileName(
            self, tr("A glTF file"), self.source.text(), "glTF (*.glb *.gltf)"
        )  # i18n: ok (a file filter)
        if found:
            self.source.setText(found)
            if not self.target.text():
                self.target.setText(str(Path(found).parent))

    def _pick_folder(self) -> None:
        found = QFileDialog.getExistingDirectory(self, tr("Write the .rdm files into"), self.target.text())
        if found:
            self.target.setText(found)

    def _pick_like(self) -> None:
        found, _ = QFileDialog.getOpenFileName(
            self, tr("Follow this .rdm"), self.like.text(), "RDM (*.rdm)"
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
            self.report.setPlainText(tr("Pick a glTF file and a folder to write into."))
            return
        try:
            report = actions.import_model(source, None, self.options())
            written, skipped = actions.write_files(report, folder, self.force.isChecked())
        except (OSError, ValueError) as exc:
            self.report.setPlainText(tr("REFUSED: {error}", error=exc))
            return
        self.state["import_folder"] = str(folder)
        lines = [tr("wrote {path}", path=p) for p in written]
        lines += [tr("left alone (exists): {name}", name=n) for n in skipped]
        lines += self._keep_in_project(report)
        lines += ["", tr("What the import did:"), *[f"  - {n}" for n in report.notes]] if report.notes else []
        self.report.setPlainText("\n".join(lines) or tr("nothing written"))

    def _keep_in_project(self, report: actions.ImportReport) -> list[str]:
        """Add the imported files to the project's mod (when asked): the lines the report shows."""
        if self.project is None or not self.keep.isChecked():
            return []
        folder = self.game_folder.text().strip().strip("/")
        if not folder:
            return [tr("not added to the project: name the game folder the file goes into")]
        self.state["import_game_folder"] = folder
        saved = EDITS.save_files(self.project, self.game, {f"{folder}/{n}": d for n, d in report.files.items()})
        return [tr("added to the project's mod: {path}", path=p) for p in saved]
