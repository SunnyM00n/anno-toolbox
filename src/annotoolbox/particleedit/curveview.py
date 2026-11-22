"""The selected curve as an editable graph (x along the life or the duration, y in the curve's own range) or, for a
colour curve or a colour list, a band of colour stops.

``CCurveView.set_curve(model, read_only)`` shows a ``curves`` model; on the graph a point is dragged, a double-click
adds one, a right-click removes one, and ``points_edited(points)`` gives the new set when the mouse is let go; on the
band a stop is dragged the same way and a double-click on it opens the colour picker (``stops_edited(stops)``).
docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..infra.i18n import tr
from ..suite import widgets
from .curves import COLORS

MARGIN = 28  # pixels around the plot
HIT = 8  # pixels: how near a press must be to a point
SAMPLES = 160


def _mix(first: tuple, second: tuple, fraction: float) -> tuple:
    return tuple(x + (y - x) * fraction for x, y in zip(first, second, strict=True))


class CCurveView(QWidget):
    points_edited = Signal(list)
    stops_edited = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_model = None
        self.m_read_only = False
        self.m_points: list[tuple[float, float]] = []
        self.m_stops: list[tuple[float, tuple]] = []
        self._m_drag: int | None = None
        self._m_moved = False  # the dragged point left where it was pressed: a plain click edits nothing
        self.setMinimumHeight(160)
        self.setMouseTracking(False)

    def set_curve(self, model, read_only: bool = False) -> None:
        self.m_model, self.m_read_only, self._m_drag = model, read_only, None
        self.m_points = list(model.points()) if model is not None and model.kind != COLORS else []
        self.m_stops = list(model.stops()) if model is not None and model.kind == COLORS else []
        self.update()

    # geometry
    def _plot(self) -> QRectF:
        return QRectF(MARGIN, MARGIN / 2, max(1, self.width() - 1.5 * MARGIN), max(1, self.height() - 1.5 * MARGIN))

    def _range(self) -> tuple[float, float]:
        """The values the plot spans: the curve's range and its points, never empty."""
        low, high = sorted(self.m_model.range()) if self.m_model is not None else (0.0, 1.0)
        values = [y for _x, y in self.m_points]
        low, high = min([low, *values]), max([high, *values])
        return (low, high) if high > low else (low - 0.5, low + 0.5)

    def _to_screen(self, x: float, y: float) -> QPointF:
        rect, (low, high) = self._plot(), self._range()
        return QPointF(
            rect.left() + x * rect.width(), rect.bottom() - (y - low) / ((high - low) or 1.0) * rect.height()
        )

    def _from_screen(self, point: QPointF) -> tuple[float, float]:
        r, (lo, hi) = self._plot(), self._range()
        x = min(max((point.x() - r.left()) / r.width(), 0.0), 1.0)
        y = lo + (r.bottom() - point.y()) / r.height() * (hi - lo)
        low, high = sorted(self.m_model.range())
        return x, min(max(y, low), high)

    def _hit(self, point: QPointF) -> int | None:
        if self.m_model is not None and self.m_model.kind == COLORS:
            rect = self._plot()
            found = [
                k
                for k, (position, _colour) in enumerate(self.m_stops)
                if abs(rect.left() + position * rect.width() - point.x()) <= HIT
            ]
        else:
            found = [k for k, (x, y) in enumerate(self.m_points)
                     if (self._to_screen(x, y) - point).manhattanLength() <= 2 * HIT]  # fmt: skip
        return found[0] if found else None

    # drawing
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot()
        painter.fillRect(self.rect(), self.palette().base())
        painter.setPen(QPen(self.palette().mid().color(), 1))
        painter.drawRect(rect)
        if self.m_model is None:
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("particles.pick_curve_graph_colour_list"))
            return
        if self.m_model.kind == COLORS:
            self._paint_band(painter, rect)
        else:
            self._paint_graph(painter, rect)

    def _paint_graph(self, painter: QPainter, rect: QRectF) -> None:
        low, high = self._range()
        painter.setPen(self.palette().text().color())
        painter.drawText(QRectF(0, rect.top() - 4, MARGIN - 2, 14), Qt.AlignmentFlag.AlignRight, f"{high:g}")
        painter.drawText(QRectF(0, rect.bottom() - 10, MARGIN - 2, 14), Qt.AlignmentFlag.AlignRight, f"{low:g}")
        line = QPolygonF([self._to_screen(k / SAMPLES, self._value(k / SAMPLES)) for k in range(SAMPLES + 1)])
        painter.setPen(QPen(QColor("#f28c28"), 2))
        painter.drawPolyline(line)
        painter.setPen(QPen(self.palette().text().color(), 1))
        painter.setBrush(QColor("#5aa9e6"))
        for x, y in self.m_points:
            painter.drawEllipse(self._to_screen(x, y), 4, 4)

    def _value(self, x: float) -> float:
        """The curve at ``x`` as it is drawn: the model's own reading while nothing is dragged, else straight lines."""
        if self._m_drag is None:
            return self.m_model.at(x)
        pts = sorted(self.m_points)
        if x <= pts[0][0]:
            return pts[0][1]
        for (xa, ya), (xb, yb) in zip(pts, pts[1:], strict=False):
            if x <= xb:
                return ya if xb - xa < 1e-9 else ya + (yb - ya) * (x - xa) / (xb - xa)
        return pts[-1][1]

    def _paint_band(self, painter: QPainter, rect: QRectF) -> None:
        band = QRectF(rect.left(), rect.top() + rect.height() * 0.25, rect.width(), rect.height() * 0.5)
        gradient = QLinearGradient(band.topLeft(), band.topRight())
        for position, colour in self.m_stops:
            gradient.setColorAt(
                min(max(position, 0.0), 1.0), QColor.fromRgbF(*(min(max(channel, 0.0), 1.0) for channel in colour[:3]))
            )
        painter.fillRect(band, gradient if self.m_stops else QColor("white"))
        painter.setPen(QPen(self.palette().text().color(), 1))
        for position, colour in self.m_stops:
            x = rect.left() + position * rect.width()
            painter.setBrush(QColor.fromRgbF(*(min(max(channel, 0.0), 1.0) for channel in colour[:3])))
            painter.drawPolygon(QPolygonF([QPointF(x, band.bottom()), QPointF(x - 6, band.bottom() + 10),
                                     QPointF(x + 6, band.bottom() + 10)]))  # fmt: skip

    # the mouse
    def mousePressEvent(self, event) -> None:
        if self.m_model is None or self.m_read_only:
            return
        k = self._hit(event.position())
        if event.button() == Qt.MouseButton.RightButton and k is not None:
            self._remove(k)
        elif event.button() == Qt.MouseButton.LeftButton:
            self._m_drag, self._m_moved = k, False

    def mouseMoveEvent(self, event) -> None:
        if self._m_drag is None:
            return
        x, y = self._from_screen(event.position())
        if self.m_model.kind == COLORS:
            self._m_moved |= self.m_stops[self._m_drag][0] != x
            self.m_stops[self._m_drag] = (x, self.m_stops[self._m_drag][1])
        else:
            self._m_moved |= self.m_points[self._m_drag] != (x, y)
            self.m_points[self._m_drag] = (x, y)
        self.update()

    def mouseReleaseEvent(self, _event) -> None:
        if self._m_drag is not None:
            self._m_drag = None
            if self._m_moved:
                self._emit()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.m_model is None or self.m_read_only:
            return
        k = self._hit(event.position())
        x, y = self._from_screen(event.position())
        if self.m_model.kind != COLORS:
            if k is None:
                self.m_points.append((x, y))
                self._emit()
            return
        if k is not None:
            self._pick(k)
        else:
            self.m_stops.append((x, self._colour_at(x)))
            self._emit()

    def _remove(self, k: int) -> None:
        held = self.m_stops if self.m_model.kind == COLORS else self.m_points
        if len(held) > 1:
            del held[k]
            self._m_drag = None  # a drag under way holds a place in the list that is another point's now
            self._emit()

    def _colour_at(self, position: float) -> tuple:
        stops = sorted(self.m_stops, key=lambda stop: stop[0])
        if not stops:
            return (1.0, 1.0, 1.0, 1.0)
        for (ta, ca), (tb, cb) in zip(stops, stops[1:], strict=False):
            if ta <= position <= tb:
                return _mix(ca, cb, (position - ta) / (tb - ta) if tb > ta else 0.0)
        return stops[0][1] if position < stops[0][0] else stops[-1][1]

    def _pick(self, k: int) -> None:
        position, colour = self.m_stops[k]
        picked = widgets.pick_qcolor(self, QColor.fromRgbF(*(min(max(channel, 0.0), 1.0) for channel in colour[:3])))
        if picked is not None:
            self.m_stops[k] = (
                position,
                (picked.redF(), picked.greenF(), picked.blueF(), colour[3] if len(colour) > 3 else 1.0),
            )
            self._emit()

    def _emit(self) -> None:
        if self.m_model.kind == COLORS:
            self.stops_edited.emit(sorted(self.m_stops, key=lambda stop: stop[0]))
        else:
            self.points_edited.emit(sorted(self.m_points))
        self.update()
