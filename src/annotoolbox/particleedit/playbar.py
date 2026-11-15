"""The preview's controls: play and pause, a slider to scrub through one pass of the effect, loop on or off, the pace.

``PlayBar(view)`` drives a viewer 3D view (``viewer.modelview``: its ``playing``, ``speed``, ``set_time`` and
``state.time``); ``set_length(seconds)`` is one pass; ``follow()`` moves the slider with the view's clock and, with loop
off, stops at the end. docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget

from ..infra.i18n import number, tr

STEPS = 1000  # slider positions over one pass


class PlayBar(QWidget):
    def __init__(self, view, parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self.length = 1.0
        self.play = QPushButton(tr("Play"), checkable=True)
        self.slider = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=STEPS)
        self.loop = QCheckBox(tr("Loop"), checked=True)
        self.pace = QDoubleSpinBox(minimum=0.05, maximum=8.0, singleStep=0.25, value=1.0, decimals=2)
        self.pace.setPrefix(tr("pace "))
        self.clock = QLabel()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        for w in (self.play, self.slider, self.clock, self.loop, self.pace):
            lay.addWidget(w, 1 if w is self.slider else 0)
        self.play.toggled.connect(self.set_playing)
        self.slider.sliderMoved.connect(lambda v: self.seek(v / STEPS * self.length))
        self.pace.valueChanged.connect(lambda v: setattr(self.view, "speed", v))
        self._show(0.0)

    def set_length(self, seconds: float) -> None:
        self.length = max(seconds, 0.05)
        self._show(self.view.state.time)

    def set_playing(self, on: bool) -> None:
        if on and not self.loop.isChecked() and self.view.state.time >= self.length:
            self.view.set_time(0.0)
        self.view.playing = on
        self.play.blockSignals(True)
        self.play.setChecked(on)
        self.play.setText(tr("Pause") if on else tr("Play"))
        self.play.blockSignals(False)

    def seek(self, seconds: float) -> None:
        self.view.set_time(seconds)
        self._show(seconds)

    def follow(self) -> None:
        """After a frame: the slider at the view's time; with loop off the pass ends and holds."""
        t = self.view.state.time
        if not self.loop.isChecked() and self.view.playing and t >= self.length:
            self.set_playing(False)
            self.view.set_time(self.length)
            t = self.length
        self._show(t)

    def _show(self, t: float) -> None:
        shown = t % self.length if self.loop.isChecked() else min(t, self.length)
        self.slider.blockSignals(True)
        self.slider.setValue(round(shown / self.length * STEPS))
        self.slider.blockSignals(False)
        self.clock.setText(tr("{now} of {length} s", now=number(shown, 2), length=number(self.length, 2)))
