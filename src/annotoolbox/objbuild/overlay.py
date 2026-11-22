"""The lines drawn over the 3D view: the info file's boxes and ground areas and the feedback file's dummies
(``objbuild.shapes``), projected with the view's camera; the selected part's shape in the selection colour.

``COverlay(view)`` sits on the view and follows its size; ``set_shapes`` and ``selected`` say what it draws, ``extras``
(``painter, camera, size``) are drawn after the shapes (the handles), ``picture(image, camera)`` draws the same onto a
picture of the view (the offscreen pictures).
docs/tool-suite.md section 23, "The window".
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..render import picking
from .shapes import SShape

COLORS = {"box": "#46c8e0", "area": "#e05050", "dummy": "#9ad35a"}
SELECTED = "#ffd040"


def project(points: list[tuple[float, float, float]], camera, size_px: tuple[int, int]) -> list[QPointF | None]:
    """Model-space points as widget points (None behind the camera); x mirrored as the model renderer draws."""
    view_proj = camera.proj(size_px[0] / max(1, size_px[1])) @ camera.view()
    mirrored = [(-x, y, z) for x, y, z in points]
    return [
        None if point is None else QPointF(point[0], point[1])
        for point in picking.to_screen(view_proj, size_px, mirrored)
    ]


def draw(painter: QPainter, shapes: list[SShape], camera, size_px: tuple[int, int], selected: str | None) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for shape in shapes:
        is_selected = selected is not None and shape.path == selected
        painter.setPen(
            QPen(QColor(SELECTED if is_selected else COLORS.get(shape.kind, "#ccc")), 2.0 if is_selected else 1.2)
        )
        pts = project(shape.points, camera, size_px)
        for start, end in shape.segments:
            if pts[start] is not None and pts[end] is not None:
                painter.drawLine(pts[start], pts[end])
        if shape.kind == "dummy" and pts and pts[0] is not None and (is_selected or len(shapes) < 40):
            painter.drawText(pts[0] + QPointF(4, -4), shape.name)


class COverlay(QWidget):
    def __init__(self, view: QWidget) -> None:
        super().__init__(view)
        self.m_view = view
        self.m_shapes: list[SShape] = []
        self.m_extras: list[Callable] = []
        self.m_selected: str | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._m_follow = _CResize(self)
        view.installEventFilter(self._m_follow)
        self.resize(view.size())

    def set_shapes(self, shapes: list[SShape], selected: str | None = None) -> None:
        self.m_shapes, self.m_selected = shapes, selected
        self.update()

    def paintEvent(self, _event) -> None:
        camera = getattr(self.m_view, "m_camera", None)
        if camera is None:
            return
        self._paint(self, camera, (self.width(), self.height()))

    def picture(self, image, camera) -> None:
        """Draw the shapes onto ``image`` (a ``QImage`` of the view) with ``camera``."""
        self._paint(image, camera, (image.width(), image.height()))

    def _paint(self, device, camera, size_px: tuple[int, int]) -> None:
        painter = QPainter(device)
        draw(painter, self.m_shapes, camera, size_px, self.m_selected)
        for extra in self.m_extras:
            extra(painter, camera, size_px)
        painter.end()


class _CResize(QObject):
    def __init__(self, overlay: COverlay) -> None:
        super().__init__(overlay)
        self.m_overlay = overlay

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.m_overlay.resize(watched.size())
            self.m_overlay.raise_()
        return False
