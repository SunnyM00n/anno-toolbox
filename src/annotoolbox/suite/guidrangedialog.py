"""The window that chooses a GUID range, for a project (Project window) and for a change set (the asset editor's
Edit > GUID range): the user's own range, a publicly declared free range, or one typed in (docs/tool-suite.md,
"GUID ranges"; the rules and data are ``suite.guidranges``).

``ask(parent, game, settings, others, selection, vanilla)`` shows it and returns the new ``Selection`` or None.
Errors (the game's own GUIDs) stop a range; warnings (a claimed range, another project's) are shown in red and the
person confirms them.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
)

from ..infra.i18n import N_, number, tr
from . import guidranges as gr


@dataclass(frozen=True)
class Selection:
    choice: str
    source: str
    span: tuple[int, int]


class GuidRangeDialog(QDialog):
    def __init__(self, parent, game: str, settings, others: dict, selection: Selection, vanilla=None) -> None:
        super().__init__(parent)
        self.game, self.settings, self.others, self.vanilla = game, settings, others, vanilla
        self.setWindowTitle(tr("common.guid_range"))
        self.resize(640, 460)
        self.radios = {key: QRadioButton(tr(text)) for key, text in (
            (gr.OWN, N_("common.my_own_range")),
            (gr.PUBLIC, N_("common.publicly_declared_free_range")),
            (gr.CUSTOM, N_("common.range_i_type")),
        )}  # fmt: skip
        self.own_text = QLabel(wordWrap=True)
        self.public = QComboBox()
        self.choices = gr.public_choices(game)
        for c in self.choices:
            self.public.addItem(c.label, c.key)
        self.public_text = QLabel(wordWrap=True, openExternalLinks=True)
        self.first, self.last = QLineEdit(), QLineEdit()
        self.status = QLabel(wordWrap=True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._problems: list = []
        self._layout()
        self._show(selection)
        for radio in self.radios.values():
            radio.toggled.connect(lambda _on=False: self.refresh())
        self.public.currentIndexChanged.connect(lambda _i: self.refresh())
        for edit in (self.first, self.last):
            edit.textChanged.connect(lambda _t: self.refresh())
        self.buttons.accepted.connect(self.accept_checked)
        self.buttons.rejected.connect(self.reject)
        self.refresh()

    def _layout(self) -> None:
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("common.numbers_new_assets_islands_take")))
        lay.addWidget(self.radios[gr.OWN])
        lay.addWidget(self.own_text)
        lay.addWidget(self.radios[gr.PUBLIC])
        lay.addWidget(self.public)
        lay.addWidget(self.public_text)
        lay.addWidget(self.radios[gr.CUSTOM])
        row = QHBoxLayout()
        for widget in (self.first, QLabel(tr("common.to")), self.last):
            row.addWidget(widget)
        lay.addLayout(row)
        lay.addWidget(self.status)
        lay.addStretch(1)
        lay.addWidget(self.buttons)

    def _show(self, selection: Selection) -> None:
        self.radios[selection.choice].setChecked(True)
        i = self.public.findData(selection.source)
        if selection.choice == gr.PUBLIC and i < 0:  # a declared range the data no longer lists: shown as typed
            self.radios[gr.CUSTOM].setChecked(True)
        self.public.setCurrentIndex(max(0, i))
        self.first.setText(str(selection.span[0]))
        self.last.setText(str(selection.span[1]))

    @property
    def choice(self) -> str:
        return next(k for k, radio in self.radios.items() if radio.isChecked())

    def selection(self) -> Selection:
        """What the window says now (``ValueError`` when a typed range is not two numbers)."""
        choice = self.choice
        source = self.public.currentData() or "" if choice == gr.PUBLIC else ""
        try:
            typed = (int(self.first.text().replace(",", "")), int(self.last.text().replace(",", "")))
        except ValueError:
            if choice == gr.CUSTOM:
                raise ValueError(tr("common.range_two_whole_numbers_first")) from None
            typed = (0, 0)
        return Selection(
            choice, source, tuple(gr.resolve(choice, source, typed, self.settings, self.game, self.others))
        )

    def refresh(self) -> None:
        for widget, key in ((self.public, gr.PUBLIC), (self.public_text, gr.PUBLIC)):
            widget.setEnabled(self.choice == key)
        for edit in (self.first, self.last):
            edit.setEnabled(self.choice == gr.CUSTOM)
        found = self.choices[self.public.currentIndex()] if self.choices else None
        if found:
            claim = f' <a href="{found.claim_url}">{tr("common.claim_range")}</a>' if found.claim_url else ""
            self.public_text.setText(
                f"{found.note}<br>{tr('common.source')} {found.source}{claim}"
            )  # i18n: ok (markup around marked text)
        else:
            self.public_text.setText(tr("common.no_declared_range_game"))
        try:
            lo, hi = gr.own_range(self.settings, self.others)
            self.own_text.setText(
                tr(
                    "common.block_your_own_reserve_kept",
                    span=f"{number(lo)} - {number(hi)}",
                )
            )
        except ValueError as exc:
            self.own_text.setText(str(exc))
        try:
            sel = self.selection()
        except ValueError as exc:
            self.status.setText(str(exc))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        problems = gr.check(self.game, sel.span, self.others, self.vanilla)
        levels = {gr.ERROR: N_("common.error"), gr.WARNING: N_("common.warning")}
        lines = [f"{tr(levels.get(p.level, p.level.upper()))}: {p.text}" for p in problems] or [
            tr("common.no_clash_found", span=f"{number(sel.span[0])} - {number(sel.span[1])}")
        ]
        self.status.setStyleSheet("color: #c0392b" if problems else "")
        self.status.setText("\n".join(lines))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not gr.blocking(problems))
        self._problems = problems

    def accept_checked(self) -> None:
        if (
            self._problems
            and QMessageBox.question(self, tr("common.guid_range"), tr("common.range_warnings_shown_use_anyway"))
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.accept()


def ask(parent, game: str, settings, others: dict, selection: Selection, vanilla=None) -> Selection | None:
    dialog = GuidRangeDialog(parent, game, settings, others, selection, vanilla)
    return dialog.selection() if dialog.exec() == QDialog.DialogCode.Accepted else None
