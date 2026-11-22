"""The Texts window's lower half: the picked text's source (read only) and target (edited), what is wrong with the
target as it is typed, the glossary's hints, the text's twin lines and voice, and the buttons that apply or take back an
edit.

``CDetailPanel(actions)``; ``show_row(SRowView)`` fills it, ``checked`` is the live check the window also asks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import tr
from . import checks
from .glossary import SGlossary

MARKS = {checks.ERROR: "✗", checks.WARNING: "!", checks.HINT: "·"}


@dataclass
class SRowView:
    """What the panel shows of one text."""

    key: str = ""
    title: str = ""
    source: str = ""
    target: str = ""
    one_line: bool = False
    glossary: SGlossary | None = None
    glyphs: Callable[[str, str], list[checks.SProblem]] | None = (
        None  # (text, picked style): the characters the fonts lack
    )
    twins: list[str] = field(default_factory=list)  # lines shown in the twin list: id, what it is for, its text
    voice: str = ""  # who speaks the line, empty when it is not spoken
    playable: tuple[str, ...] = ()  # "source" and "target": the sides whose language has the voice
    lips: str = ""  # the lip file of the voice (1404, 1701), one line
    drawn_in: str = ""  # the text styles of the labels that show the text, empty where no UI file names it
    face: Callable[[str], tuple[str | None, str]] | None = None  # picked style -> (the game's font family, why not)


@dataclass
class SActions:
    """What the panel's buttons call back."""

    apply: Callable[[str], None]
    revert: Callable[[], None]
    fill_twins: Callable[[], None]
    play: Callable[[str], None]  # the language whose voice to play


class CDetailPanel(QWidget):
    def __init__(self, actions: SActions, parent=None) -> None:
        super().__init__(parent)
        self.m_view = SRowView()
        self.actions = actions
        self.m_title = QLabel()
        self.m_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.m_source_box = QPlainTextEdit(readOnly=True)
        self.m_target_box = QPlainTextEdit()
        for box in (self.m_source_box, self.m_target_box):
            box.setTabChangesFocus(True)  # Tab goes on to the next control; a text holds no tab
        self.m_counter = QLabel()
        self.m_problems = QListWidget()
        self.m_problems.setMaximumHeight(90)
        self.m_twins = QListWidget()
        self.m_twins.setMaximumHeight(70)
        self.m_twins_label = QLabel(tr("texts.twin_lines_other_portraits_gamepads"))
        self.m_fill_button = QPushButton(tr("texts.fill_twins_text"))
        self.m_voice_label = QLabel()
        self.m_lips_label = QLabel()
        self.m_style_box = QComboBox()
        self.m_style_box.addItem(tr("texts.style_texts_own"), "")
        self.m_style_box.addItem(tr("texts.style_every"), "*")
        self.m_drawn_label = QLabel()
        self.m_plain_font = self.m_target_box.font()  # what the boxes use where the game's font is not read
        self.m_play_buttons: dict[str, QPushButton] = {}
        apply_button = QPushButton(tr("texts.apply_ctrl_plus_enter"))
        revert_button = QPushButton(tr("texts.revert_games_text"))
        copy_button = QPushButton(tr("texts.copy_source"))
        apply_button.clicked.connect(lambda: self.m_view.key and actions.apply(self.m_target_box.toPlainText()))
        revert_button.clicked.connect(lambda: self.m_view.key and actions.revert())
        copy_button.clicked.connect(lambda: self.m_target_box.setPlainText(self.m_source_box.toPlainText()))
        self.m_fill_button.clicked.connect(lambda: self.m_view.key and actions.fill_twins())
        for combo in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(combo), self.m_target_box, activated=apply_button.click)
        buttons = QHBoxLayout()
        for button in (apply_button, revert_button, copy_button):
            buttons.addWidget(button)
        buttons.addWidget(self.m_counter, 1, Qt.AlignmentFlag.AlignRight)
        style = QHBoxLayout()
        style.addWidget(self.m_style_box)
        style.addWidget(self.m_drawn_label, 1)
        voice = QHBoxLayout()
        voice.addWidget(self.m_voice_label, 1)
        for which, text in (("source", tr("texts.play_source_voice")), ("target", tr("texts.play_target_voice"))):
            button = self.m_play_buttons[which] = QPushButton(text)
            button.clicked.connect(lambda _checked=False, chosen=which: self.m_view.key and actions.play(chosen))
            voice.addWidget(button)
        lay = QVBoxLayout(self)
        for widget in (
            self.m_title,
            QLabel(tr("texts.source")),
            self.m_source_box,
            QLabel(tr("common.target")),
            self.m_target_box,
        ):
            lay.addWidget(widget, 3 if isinstance(widget, QPlainTextEdit) else 0)
        lay.addLayout(buttons)
        lay.addLayout(style)
        lay.addLayout(voice)
        lay.addWidget(self.m_lips_label)
        for widget in (self.m_twins_label, self.m_twins, self.m_fill_button, self.m_problems):
            lay.addWidget(widget)
        self.m_target_box.textChanged.connect(self._live)
        self.m_style_box.currentIndexChanged.connect(lambda _index: self._live())
        self.m_style_box.currentIndexChanged.connect(lambda _index: self._show_face())
        self.setEnabled(False)

    def show_row(self, view: SRowView) -> None:
        self.m_view = view
        self.m_title.setText(view.title)
        self.m_source_box.setPlainText(view.source)
        self.m_target_box.blockSignals(True)  # one check below, not one per box filled
        self.m_target_box.setPlainText(view.target)
        self.m_target_box.blockSignals(False)
        self.m_twins.clear()
        self.m_twins.addItems(view.twins)
        for widget in (self.m_twins_label, self.m_twins, self.m_fill_button):
            widget.setVisible(bool(view.twins))
        self.m_voice_label.setText(tr("texts.spoken_by_speaker", speaker=view.voice) if view.voice else "")
        self.m_lips_label.setText(view.lips)
        self.m_lips_label.setVisible(bool(view.lips))
        self.m_drawn_label.setText(tr("texts.drawn_in_styles", styles=view.drawn_in) if view.drawn_in else "")
        for which, button in self.m_play_buttons.items():
            button.setVisible(bool(view.voice))
            button.setEnabled(which in view.playable)
        self.setEnabled(bool(view.key))
        self._show_face()
        self._live()

    def _show_face(self) -> None:
        """Both boxes in the game's font of the picked style; where it cannot be read, the plain font and the reason."""
        family, reason = self.m_view.face(self.m_style_box.currentData() or "") if self.m_view.face else (None, "")
        font = QFont(self.m_plain_font)
        if family:
            font = QFont(family)
            font.setPointSizeF(self.m_plain_font.pointSizeF() * 1.15)
        for box in (self.m_source_box, self.m_target_box):
            box.setFont(font)
            box.setToolTip(reason)

    def checked(self, text: str) -> list[str]:
        """The problems of ``text`` as lines (a mark, then the sentence), errors first, then the glyphs and the glossary's
        hints."""
        view = self.m_view
        found = checks.check(view.source, text, view.one_line) + (
            view.glyphs(text, self.m_style_box.currentData() or "") if view.glyphs else []
        )
        found.sort(key=lambda problem: (checks.ERROR, checks.WARNING, checks.HINT).index(problem.severity))
        hints = view.glossary.hints(view.source, text) if view.glossary is not None else []
        return [f"{MARKS[problem.severity]} {problem.message}" for problem in found] + [
            f"{MARKS[checks.HINT]} {hint}" for hint in hints
        ]

    def _live(self) -> None:
        text = self.m_target_box.toPlainText()
        self.m_problems.clear()
        if self.m_view.key:
            self.m_problems.addItems(self.checked(text))
        self.m_counter.setText(
            tr("texts.target_of_source_characters", target=len(text), source=len(self.m_view.source))
        )

    def set_styles(self, choices: list[tuple[str, str]]) -> None:
        """The picker's styles after its two fixed entries (the target language's localized fonts), the pick kept."""
        keep = self.m_style_box.currentData()
        self.m_style_box.blockSignals(True)
        while self.m_style_box.count() > 2:
            self.m_style_box.removeItem(2)
        for key, name in choices:
            self.m_style_box.addItem(name, key)  # i18n: ok (a font's name)
        self.m_style_box.setCurrentIndex(max(self.m_style_box.findData(keep), 0))
        self.m_style_box.blockSignals(False)
        self.m_style_box.setVisible(bool(choices))
        self._show_face()
