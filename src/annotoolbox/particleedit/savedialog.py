"""The particle editor's save dialog: the three ways (``saving.ways``) with their reasons, a path for a new file, and
what the chosen way writes; ``result_plan`` is the ``saving.Plan`` chosen. docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
)

from ..infra.i18n import tr
from .effectdoc import EffectDoc
from .saving import NEW, TITLES, plan, ways


class SaveDialog(QDialog):
    def __init__(self, effect: EffectDoc, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Save into the project"))
        self.effect, self.result_plan = effect, None
        self.ways = ways(effect)
        self.group = QButtonGroup(self)
        stem, ext = effect.path.rsplit(".", 1)
        self.path = QLineEdit(f"{stem}_mod.{ext}")
        self.preview = QPlainTextEdit(readOnly=True)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("<b>{title}</b>", title=effect.title)))
        for k, w in enumerate(self.ways):
            button = QRadioButton(tr(TITLES[w.way]), enabled=w.allowed)
            self.group.addButton(button, k)
            lay.addWidget(button)
            why = QLabel(w.why if w.allowed else tr("Not open: {why}", why=w.why), wordWrap=True)
            why.setIndent(24)
            lay.addWidget(why)
            if w.way == NEW:
                lay.addWidget(self.path)
        lay.addWidget(self.preview)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton(tr("Save"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.group.idToggled.connect(lambda _id, on: on and self._show())
        self.path.textChanged.connect(lambda _t: self._show())
        self.group.button(0).setChecked(True)
        self.resize(680, 480)

    def chosen(self) -> str:
        return self.ways[max(0, self.group.checkedId())].way

    def _show(self) -> None:
        found = plan(self.effect, self.chosen(), self.path.text())
        lines = [tr("writes {path}", path=p) for p in found.files] + found.notes
        lines += [tr("cannot: {problem}", problem=p) for p in found.problems]
        patch = next((d for p, d in found.files.items() if p.endswith(".xml")), b"")
        self.preview.setPlainText("\n".join(lines) + ("\n\n" + patch.decode("utf-8") if patch else ""))
        self.save_button.setEnabled(not found.problems)

    def _save(self) -> None:
        self.result_plan = plan(self.effect, self.chosen(), self.path.text())
        self.accept()
