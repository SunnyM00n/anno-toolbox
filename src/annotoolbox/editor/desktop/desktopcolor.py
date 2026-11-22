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
    red, green, blue, alpha = (min(1.0, max(0.0, float(component))) for component in rgba)
    return QColor.fromRgbF(red, green, blue, alpha)


class CColorButton(QPushButton):
    picked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.m_rgba: list[float] | None = None
        self.clicked.connect(self._pick)
        self.set_color(None)

    def set_color(self, rgba) -> None:
        self.m_rgba = [float(component) for component in rgba] if rgba is not None else None
        if self.m_rgba is None:
            self.setText(tr("common.none"))
            self.setStyleSheet("")
            return
        qcolor = to_qcolor(self.m_rgba)
        text = "#ffffff" if qcolor.lightness() < 128 else "#000000"
        self.setText(tr("islands.color_alpha", color=qcolor.name(), alpha=f"{self.m_rgba[3]:.2f}"))
        self.setStyleSheet(f"background-color: {qcolor.name()}; color: {text};")

    def _pick(self) -> None:
        start = to_qcolor(self.m_rgba) if self.m_rgba is not None else QColor("white")
        chosen = QColorDialog.getColor(start, self, tr("islands.tint"), QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if chosen.isValid():
            self.picked.emit([chosen.redF(), chosen.greenF(), chosen.blueF(), chosen.alphaF()])
