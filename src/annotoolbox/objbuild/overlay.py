"""The lines drawn over the 3D view: the info file's boxes and ground areas and the feedback file's dummies
(``objbuild.shapes``), projected with the view's camera; the selected part's shape in the selection colour.

``Overlay(view)`` sits on the view and follows its size; ``set_shapes`` and ``selected`` say what it draws, ``extras``
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
from .shapes import Shape

COLORS = {"box": "#46c8e0", "area": "#e05050", "dummy": "#9ad35a"}
SELECTED = "#ffd040"


def project(points: list[tuple[float, float, float]], camera, size: tuple[int, int]) -> list[QPointF | None]:
    """Model-space points as widget points (None behind the camera); x mirrored as the model renderer draws."""
    view_proj = camera.proj(size[0] / max(1, size[1])) @ camera.view()
    mirrored = [(-x, y, z) for x, y, z in points]
    return [None if p is None else QPointF(p[0], p[1]) for p in picking.to_screen(view_proj, size, mirrored)]


def draw(painter: QPainter, shapes: list[Shape], camera, size: tuple[int, int], selected: str | None) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for shape in shapes:
        on = selected is not None and shape.path == selected
        painter.setPen(QPen(QColor(SELECTED if on else COLORS.get(shape.kind, "#ccc")), 2.0 if on else 1.2))
        pts = project(shape.points, camera, size)
        for a, b in shape.segments:
            if pts[a] is not None and pts[b] is not None:
                painter.drawLine(pts[a], pts[b])
        if shape.kind == "dummy" and pts and pts[0] is not None and (on or len(shapes) < 40):
            painter.drawText(pts[0] + QPointF(4, -4), shape.name)


class Overlay(QWidget):
    def __init__(self, view: QWidget) -> None:
        super().__init__(view)
        self.view = view
        self.shapes: list[Shape] = []
        self.extras: list[Callable] = []
        self.selected: str | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._follow = _Resize(self)
        view.installEventFilter(self._follow)
        self.resize(view.size())

    def set_shapes(self, shapes: list[Shape], selected: str | None = None) -> None:
        self.shapes, self.selected = shapes, selected
        self.update()

    def paintEvent(self, _e) -> None:
        camera = getattr(self.view, "camera", None)
        if camera is None:
            return
        p = QPainter(self)
        size = (self.width(), self.height())
        draw(p, self.shapes, camera, size, self.selected)
        for extra in self.extras:
            extra(p, camera, size)
        p.end()

    def picture(self, image, camera) -> None:
        """Draw the shapes onto ``image`` (a ``QImage`` of the view) with ``camera``."""
        p = QPainter(image)
        size = (image.width(), image.height())
        draw(p, self.shapes, camera, size, self.selected)
        for extra in self.extras:
            extra(p, camera, size)
        p.end()


class _Resize(QObject):
    def __init__(self, overlay: Overlay) -> None:
        super().__init__(overlay)
        self.overlay = overlay

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.overlay.resize(obj.size())
            self.overlay.raise_()
        return False
