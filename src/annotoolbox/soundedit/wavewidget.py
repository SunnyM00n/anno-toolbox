"""The waveform view (Qt): ``WaveformView.show_shape`` draws a ``waveform.Shape`` as a column of peaks with the clip's
length under it and the ``marks`` (a music segment's cues: seconds, name) as lines; ``show_message`` says why there is
none. docs/sound-editor.md "The waveform".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..infra.i18n import tr
from .waveform import Shape

HEIGHT = 110
MAX_SAMPLE = 32768.0


class WaveformView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(HEIGHT)
        self.shape: Shape | None = None
        self.marks: list[tuple[float, str]] = []
        self.message = tr("no clip picked")

    def show_shape(self, shape: Shape, marks: list[tuple[float, str]] | None = None) -> None:
        self.shape, self.marks, self.message = shape, list(marks or []), ""
        self.update()

    def show_message(self, text: str) -> None:
        self.shape, self.marks, self.message = None, [], text
        self.update()

    def caption(self) -> str:
        """The line under the picture: length, rate, channels."""
        s = self.shape
        if s is None:
            return self.message
        return tr(
            "{seconds} s, {rate} Hz, {channels} channels", seconds=f"{s.seconds:.2f}", rate=s.rate, channels=s.channels
        )

    def paintEvent(self, _event) -> None:  # Qt's name
        p = QPainter(self)
        palette = self.palette()
        p.fillRect(self.rect(), palette.base())
        text = palette.text().color()
        body = self.rect().adjusted(0, 0, 0, -16)
        mid = body.center().y()
        if self.shape is None:
            p.setPen(text)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return
        scale = body.height() / 2 / MAX_SAMPLE
        peaks = self.shape.peaks
        p.setPen(QPen(QColor(70, 140, 220)))
        for x in range(body.width()):
            lo, hi = peaks[min(len(peaks) - 1, x * len(peaks) // max(body.width(), 1))]
            p.drawLine(x, int(mid - hi * scale), x, int(mid - lo * scale) + 1)
        p.setPen(QPen(QColor(220, 90, 70)))
        for seconds, label in self.marks:
            x = int(body.width() * seconds / max(self.shape.seconds, 1e-9))
            p.drawLine(x, 0, x, body.height())
            p.drawText(x + 3, 11, label)
        p.setPen(text)
        p.drawText(2, self.height() - 3, self.caption())
