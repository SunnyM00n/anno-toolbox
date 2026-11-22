"""The waveform view (Qt): ``CWaveformView.show_shape`` draws a ``waveform.SShape`` as a column of peaks with the clip's
length under it and the ``marks`` (a music segment's cues: seconds, name) as lines; ``show_message`` says why there is
none. docs/sound-editor.md "The waveform".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..infra.i18n import tr
from .waveform import SShape

HEIGHT = 110
MAX_SAMPLE = 32768.0


class CWaveformView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(HEIGHT)
        self.m_shape: SShape | None = None
        self.m_marks: list[tuple[float, str]] = []
        self.m_message = tr("common.no_clip_picked")

    def show_shape(self, shape: SShape, marks: list[tuple[float, str]] | None = None) -> None:
        self.m_shape, self.m_marks, self.m_message = shape, list(marks or []), ""
        self.update()

    def show_message(self, text: str) -> None:
        self.m_shape, self.m_marks, self.m_message = None, [], text
        self.update()

    def caption(self) -> str:
        """The line under the picture: length, rate, channels."""
        shape = self.m_shape
        if shape is None:
            return self.m_message
        return tr("common.s_hz_channels", seconds=f"{shape.seconds:.2f}", rate=shape.rate, channels=shape.channels)

    def paintEvent(self, _event) -> None:  # Qt's name
        painter = QPainter(self)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        text = palette.text().color()
        body = self.rect().adjusted(0, 0, 0, -16)
        mid = body.center().y()
        if self.m_shape is None:
            painter.setPen(text)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.m_message)
            return
        scale = body.height() / 2 / MAX_SAMPLE
        peaks = self.m_shape.peaks
        painter.setPen(QPen(QColor(70, 140, 220)))
        for x in range(body.width()):
            lo, hi = peaks[min(len(peaks) - 1, x * len(peaks) // max(body.width(), 1))]
            painter.drawLine(x, int(mid - hi * scale), x, int(mid - lo * scale) + 1)
        painter.setPen(QPen(QColor(220, 90, 70)))
        for seconds, label in self.m_marks:
            x = int(body.width() * seconds / max(self.m_shape.seconds, 1e-9))
            painter.drawLine(x, 0, x, body.height())
            painter.drawText(x + 3, 11, label)
        painter.setPen(text)
        painter.drawText(2, self.height() - 3, self.caption())
