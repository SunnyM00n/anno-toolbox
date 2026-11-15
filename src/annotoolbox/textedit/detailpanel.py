"""The Texts window's lower half: the picked text's source (read only) and target (edited), what is wrong with the
target as it is typed, the glossary's hints, the text's twin lines and voice, and the buttons that apply or take back an
edit.

``DetailPanel(actions)``; ``show_row(RowView)`` fills it, ``checked`` is the live check the window also asks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
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
from .glossary import Glossary

MARKS = {checks.ERROR: "✗", checks.WARNING: "!", checks.HINT: "·"}


@dataclass
class RowView:
    """What the panel shows of one text."""

    key: str = ""
    title: str = ""
    source: str = ""
    target: str = ""
    one_line: bool = False
    glossary: Glossary | None = None
    glyphs: Callable[[str, str], list[checks.Problem]] | None = (
        None  # (text, picked style): the characters the fonts lack
    )
    twins: list[str] = field(default_factory=list)  # lines shown in the twin list: id, what it is for, its text
    voice: str = ""  # who speaks the line, empty when it is not spoken
    playable: tuple[str, ...] = ()  # "source" and "target": the sides whose language has the voice
    lips: str = ""  # the lip file of the voice (1404, 1701), one line
    drawn_in: str = ""  # the text styles of the labels that show the text, empty where no UI file names it


@dataclass
class Actions:
    """What the panel's buttons call back."""

    apply: Callable[[str], None]
    revert: Callable[[], None]
    fill_twins: Callable[[], None]
    play: Callable[[str], None]  # the language whose voice to play


class DetailPanel(QWidget):
    def __init__(self, actions: Actions, parent=None) -> None:
        super().__init__(parent)
        self.view = RowView()
        self.actions = actions
        self.title = QLabel()
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source_box = QPlainTextEdit(readOnly=True)
        self.target_box = QPlainTextEdit()
        self.counter = QLabel()
        self.problems = QListWidget()
        self.problems.setMaximumHeight(90)
        self.twins = QListWidget()
        self.twins.setMaximumHeight(70)
        self.twins_label = QLabel(tr("Twin lines (the other portrait's and the gamepad's)"))
        self.fill_button = QPushButton(tr("Fill the twins with this text"))
        self.voice_label = QLabel()
        self.lips_label = QLabel()
        self.style_box = QComboBox()
        self.style_box.addItem(tr("Style: the text's own"), "")
        self.style_box.addItem(tr("Style: every style"), "*")
        self.drawn_label = QLabel()
        self.play_buttons: dict[str, QPushButton] = {}
        apply_button = QPushButton(tr("Apply (Ctrl+Enter)"))
        revert_button = QPushButton(tr("Revert to the game's text"))
        copy_button = QPushButton(tr("Copy the source"))
        apply_button.clicked.connect(lambda: self.view.key and actions.apply(self.target_box.toPlainText()))
        revert_button.clicked.connect(lambda: self.view.key and actions.revert())
        copy_button.clicked.connect(lambda: self.target_box.setPlainText(self.source_box.toPlainText()))
        self.fill_button.clicked.connect(lambda: self.view.key and actions.fill_twins())
        for combo in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(combo), self.target_box, activated=apply_button.click)
        buttons = QHBoxLayout()
        for button in (apply_button, revert_button, copy_button):
            buttons.addWidget(button)
        buttons.addWidget(self.counter, 1, Qt.AlignmentFlag.AlignRight)
        style = QHBoxLayout()
        style.addWidget(self.style_box)
        style.addWidget(self.drawn_label, 1)
        voice = QHBoxLayout()
        voice.addWidget(self.voice_label, 1)
        for which, text in (("source", tr("Play the source voice")), ("target", tr("Play the target voice"))):
            button = self.play_buttons[which] = QPushButton(text)
            button.clicked.connect(lambda _c=False, w=which: self.view.key and actions.play(w))
            voice.addWidget(button)
        lay = QVBoxLayout(self)
        for widget in (self.title, QLabel(tr("Source")), self.source_box, QLabel(tr("Target")), self.target_box):
            lay.addWidget(widget, 3 if isinstance(widget, QPlainTextEdit) else 0)
        lay.addLayout(buttons)
        lay.addLayout(style)
        lay.addLayout(voice)
        lay.addWidget(self.lips_label)
        for widget in (self.twins_label, self.twins, self.fill_button, self.problems):
            lay.addWidget(widget)
        self.target_box.textChanged.connect(self._live)
        self.style_box.currentIndexChanged.connect(lambda _i: self._live())
        self.setEnabled(False)

    def show_row(self, view: RowView) -> None:
        self.view = view
        self.title.setText(view.title)
        self.source_box.setPlainText(view.source)
        self.target_box.setPlainText(view.target)  # the box's change runs the check
        self.twins.clear()
        self.twins.addItems(view.twins)
        for widget in (self.twins_label, self.twins, self.fill_button):
            widget.setVisible(bool(view.twins))
        self.voice_label.setText(tr("Spoken by {speaker}", speaker=view.voice) if view.voice else "")
        self.lips_label.setText(view.lips)
        self.lips_label.setVisible(bool(view.lips))
        self.drawn_label.setText(tr("Drawn in {styles}", styles=view.drawn_in) if view.drawn_in else "")
        for which, button in self.play_buttons.items():
            button.setVisible(bool(view.voice))
            button.setEnabled(which in view.playable)
        self.setEnabled(bool(view.key))
        self._live()

    def checked(self, text: str) -> list[str]:
        """The problems of ``text`` as lines (a mark, then the sentence), errors first, then the glyphs and the glossary's
        hints."""
        view = self.view
        found = checks.check(view.source, text, view.one_line) + (
            view.glyphs(text, self.style_box.currentData() or "") if view.glyphs else []
        )
        found.sort(key=lambda p: (checks.ERROR, checks.WARNING, checks.HINT).index(p.severity))
        hints = view.glossary.hints(view.source, text) if view.glossary is not None else []
        return [f"{MARKS[p.severity]} {p.message}" for p in found] + [f"{MARKS[checks.HINT]} {h}" for h in hints]

    def _live(self) -> None:
        text = self.target_box.toPlainText()
        self.problems.clear()
        if self.view.key:
            self.problems.addItems(self.checked(text))
        self.counter.setText(tr("{target} of {source} characters", target=len(text), source=len(self.view.source)))

    def set_styles(self, choices: list[tuple[str, str]]) -> None:
        """The picker's styles after its two fixed entries (the target language's localized fonts), the pick kept."""
        keep = self.style_box.currentData()
        self.style_box.blockSignals(True)
        while self.style_box.count() > 2:
            self.style_box.removeItem(2)
        for key, name in choices:
            self.style_box.addItem(name, key)  # i18n: ok (a font's name)
        self.style_box.setCurrentIndex(max(self.style_box.findData(keep), 0))
        self.style_box.blockSignals(False)
        self.style_box.setVisible(bool(choices))
