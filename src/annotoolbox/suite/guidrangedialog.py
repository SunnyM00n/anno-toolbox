"""The window that chooses a GUID range, for a project (Project window) and for a change set (the asset editor's
Edit > GUID range): the user's own range, a publicly declared free range, or one typed in (docs/tool-suite.md,
"GUID ranges"; the rules and data are ``suite.guidranges``).

``ask(parent, game, settings, others, selection, vanilla)`` shows it and returns the new ``SSelection`` or None.
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
class SSelection:
    choice: str
    source: str
    span: tuple[int, int]


class CGuidRangeDialog(QDialog):
    def __init__(self, parent, game: str, settings, others: dict, selection: SSelection, vanilla=None) -> None:
        super().__init__(parent)
        self.m_game, self.m_settings, self.m_others, self.m_vanilla = game, settings, others, vanilla
        self.setWindowTitle(tr("common.guid_range"))
        self.resize(640, 460)
        self.m_radios = {key: QRadioButton(tr(text)) for key, text in (
            (gr.OWN, N_("common.my_own_range")),
            (gr.PUBLIC, N_("common.publicly_declared_free_range")),
            (gr.CUSTOM, N_("common.range_i_type")),
        )}  # fmt: skip
        self.m_own_text = QLabel(wordWrap=True)
        self.m_public = QComboBox()
        self.m_choices = gr.public_choices(game)
        for choice in self.m_choices:
            self.m_public.addItem(choice.label, choice.key)
        self.m_public_text = QLabel(wordWrap=True, openExternalLinks=True)
        self.m_first, self.m_last = QLineEdit(), QLineEdit()
        self.m_status = QLabel(wordWrap=True)
        self.m_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._m_problems: list = []
        self._layout()
        self._show(selection)
        for radio in self.m_radios.values():
            radio.toggled.connect(lambda _on=False: self.refresh())
        self.m_public.currentIndexChanged.connect(lambda _i: self.refresh())
        for edit in (self.m_first, self.m_last):
            edit.textChanged.connect(lambda _t: self.refresh())
        self.m_buttons.accepted.connect(self.accept_checked)
        self.m_buttons.rejected.connect(self.reject)
        self.refresh()

    def _layout(self) -> None:
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("common.numbers_new_assets_islands_take")))
        lay.addWidget(self.m_radios[gr.OWN])
        lay.addWidget(self.m_own_text)
        lay.addWidget(self.m_radios[gr.PUBLIC])
        lay.addWidget(self.m_public)
        lay.addWidget(self.m_public_text)
        lay.addWidget(self.m_radios[gr.CUSTOM])
        row = QHBoxLayout()
        for widget in (self.m_first, QLabel(tr("common.to")), self.m_last):
            row.addWidget(widget)
        lay.addLayout(row)
        lay.addWidget(self.m_status)
        lay.addStretch(1)
        lay.addWidget(self.m_buttons)

    def _show(self, selection: SSelection) -> None:
        self.m_radios[selection.choice].setChecked(True)
        i = self.m_public.findData(selection.source)
        if selection.choice == gr.PUBLIC and i < 0:  # a declared range the data no longer lists: shown as typed
            self.m_radios[gr.CUSTOM].setChecked(True)
        self.m_public.setCurrentIndex(max(0, i))
        self.m_first.setText(str(selection.span[0]))
        self.m_last.setText(str(selection.span[1]))

    @property
    def choice(self) -> str:
        return next(k for k, radio in self.m_radios.items() if radio.isChecked())

    def selection(self) -> SSelection:
        """What the window says now (``ValueError`` when a typed range is not two numbers)."""
        choice = self.choice
        source = self.m_public.currentData() or "" if choice == gr.PUBLIC else ""
        try:
            typed = (int(self.m_first.text().replace(",", "")), int(self.m_last.text().replace(",", "")))
        except ValueError:
            if choice == gr.CUSTOM:
                raise ValueError(tr("common.range_two_whole_numbers_first")) from None
            typed = (0, 0)
        return SSelection(
            choice, source, tuple(gr.resolve(choice, source, typed, self.m_settings, self.m_game, self.m_others))
        )

    def refresh(self) -> None:
        for widget, key in ((self.m_public, gr.PUBLIC), (self.m_public_text, gr.PUBLIC)):
            widget.setEnabled(self.choice == key)
        for edit in (self.m_first, self.m_last):
            edit.setEnabled(self.choice == gr.CUSTOM)
        found = self.m_choices[self.m_public.currentIndex()] if self.m_choices else None
        if found:
            claim = f' <a href="{found.claim_url}">{tr("common.claim_range")}</a>' if found.claim_url else ""
            self.m_public_text.setText(
                f"{found.note}<br>{tr('common.source')} {found.source}{claim}"
            )  # i18n: ok (markup around marked text)
        else:
            self.m_public_text.setText(tr("common.no_declared_range_game"))
        try:
            low, high = gr.own_range(self.m_settings, self.m_others)
            self.m_own_text.setText(
                tr(
                    "common.block_your_own_reserve_kept",
                    span=f"{number(low)} - {number(high)}",
                )
            )
        except ValueError as exc:
            self.m_own_text.setText(str(exc))
        try:
            chosen = self.selection()
        except ValueError as exc:
            self.m_status.setText(str(exc))
            self.m_buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            return
        problems = gr.check(self.m_game, chosen.span, self.m_others, self.m_vanilla)
        levels = {gr.ERROR: N_("common.error"), gr.WARNING: N_("common.warning")}
        lines = [f"{tr(levels.get(problem.level, problem.level.upper()))}: {problem.text}" for problem in problems] or [
            tr("common.no_clash_found", span=f"{number(chosen.span[0])} - {number(chosen.span[1])}")
        ]
        self.m_status.setStyleSheet("color: #c0392b" if problems else "")
        self.m_status.setText("\n".join(lines))
        self.m_buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not gr.blocking(problems))
        self._m_problems = problems

    def accept_checked(self) -> None:
        if (
            self._m_problems
            and QMessageBox.question(self, tr("common.guid_range"), tr("common.range_warnings_shown_use_anyway"))
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.accept()


def ask(parent, game: str, settings, others: dict, selection: SSelection, vanilla=None) -> SSelection | None:
    dialog = CGuidRangeDialog(parent, game, settings, others, selection, vanilla)
    return dialog.selection() if dialog.exec() == QDialog.DialogCode.Accepted else None
