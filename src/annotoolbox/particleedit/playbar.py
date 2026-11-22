"""The preview's controls: play and pause, a slider to scrub through one pass of the effect, loop on or off, the pace.

``CPlayBar(view)`` drives a viewer 3D view (``viewer.modelview``: its ``playing``, ``speed``, ``set_time`` and
``state.time``); ``set_length(seconds)`` is one pass; ``follow()`` moves the slider with the view's clock and, with loop
off, stops at the end. docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget

from ..infra.i18n import number, tr

STEPS = 1000  # slider positions over one pass


class CPlayBar(QWidget):
    def __init__(self, view, parent=None) -> None:
        super().__init__(parent)
        self.m_view = view
        self.m_length_s = 1.0
        self.m_play = QPushButton(tr("common.play"), checkable=True)
        self.m_slider = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=STEPS)
        self.m_loop = QCheckBox(tr("objects.loop"), checked=True)
        self.m_pace = QDoubleSpinBox(minimum=0.05, maximum=8.0, singleStep=0.25, value=1.0, decimals=2)
        self.m_pace.setPrefix(tr("particles.pace"))
        self.m_clock = QLabel()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        for widget in (self.m_play, self.m_slider, self.m_clock, self.m_loop, self.m_pace):
            lay.addWidget(widget, 1 if widget is self.m_slider else 0)
        self.m_play.toggled.connect(self.set_playing)
        self.m_slider.sliderMoved.connect(lambda value: self.seek(value / STEPS * self.m_length_s))
        self.m_pace.valueChanged.connect(lambda value: setattr(self.m_view, "m_speed", value))
        self._show(0.0)

    def set_length(self, seconds: float) -> None:
        self.m_length_s = max(seconds, 0.05)
        self._show(self.m_view.m_state.time)

    def set_playing(self, is_on: bool) -> None:
        if is_on and not self.m_loop.isChecked() and self.m_view.m_state.time >= self.m_length_s:
            self.m_view.set_time(0.0)
        self.m_view.m_playing = is_on
        self.m_play.blockSignals(True)
        self.m_play.setChecked(is_on)
        self.m_play.setText(tr("ui.pause") if is_on else tr("common.play"))
        self.m_play.blockSignals(False)

    def seek(self, seconds: float) -> None:
        self.m_view.set_time(seconds)
        self._show(seconds)

    def follow(self) -> None:
        """After a frame: the slider at the view's time; with loop off the pass ends and holds."""
        seconds = self.m_view.m_state.time
        if not self.m_loop.isChecked() and self.m_view.m_playing and seconds >= self.m_length_s:
            self.set_playing(False)
            self.m_view.set_time(self.m_length_s)
            seconds = self.m_length_s
        self._show(seconds)

    def _show(self, seconds: float) -> None:
        shown = seconds % self.m_length_s if self.m_loop.isChecked() else min(seconds, self.m_length_s)
        self.m_slider.blockSignals(True)
        self.m_slider.setValue(round(shown / self.m_length_s * STEPS))
        self.m_slider.blockSignals(False)
        self.m_clock.setText(tr("particles.now_of_length_s", now=number(shown, 2), length=number(self.m_length_s, 2)))
