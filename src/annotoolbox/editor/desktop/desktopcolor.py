"""A button that shows a colour (red, green, blue, alpha, each 0 to 1) and opens the colour dialog on a click: the
Properties panel's tint of a prop. ``picked`` carries the new colour as four floats; colours above 1 (a prop may be
brighter than white) show clamped and stay as they are until the dialog changes them.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QPushButton

from ...infra.i18n import tr


def to_qcolor(rgba) -> QColor:
    r, g, b, a = (min(1.0, max(0.0, float(v))) for v in rgba)
    return QColor.fromRgbF(r, g, b, a)


class ColorButton(QPushButton):
    picked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.rgba: list[float] | None = None
        self.clicked.connect(self._pick)
        self.set_color(None)

    def set_color(self, rgba) -> None:
        self.rgba = [float(v) for v in rgba] if rgba is not None else None
        if self.rgba is None:
            self.setText(tr("none"))
            self.setStyleSheet("")
            return
        q = to_qcolor(self.rgba)
        text = "#ffffff" if q.lightness() < 128 else "#000000"
        self.setText(tr("{color}  alpha {alpha}", color=q.name(), alpha=f"{self.rgba[3]:.2f}"))
        self.setStyleSheet(f"background-color: {q.name()}; color: {text};")

    def _pick(self) -> None:
        start = to_qcolor(self.rgba) if self.rgba is not None else QColor("white")
        chosen = QColorDialog.getColor(start, self, tr("Tint"), QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if chosen.isValid():
            self.picked.emit([chosen.redF(), chosen.greenF(), chosen.blueF(), chosen.alphaF()])
