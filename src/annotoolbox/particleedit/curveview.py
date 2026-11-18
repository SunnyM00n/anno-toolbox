"""The selected curve as an editable graph (x along the life or the duration, y in the curve's own range) or, for a
colour curve or a colour list, a band of colour stops.

``CurveView.set_curve(model, read_only)`` shows a ``curves`` model; on the graph a point is dragged, a double-click
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


def _mix(a: tuple, b: tuple, f: float) -> tuple:
    return tuple(x + (y - x) * f for x, y in zip(a, b, strict=True))


class CurveView(QWidget):
    points_edited = Signal(list)
    stops_edited = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.model = None
        self.read_only = False
        self.points: list[tuple[float, float]] = []
        self.stops: list[tuple[float, tuple]] = []
        self._drag: int | None = None
        self._moved = False  # the dragged point left where it was pressed: a plain click edits nothing
        self.setMinimumHeight(160)
        self.setMouseTracking(False)

    def set_curve(self, model, read_only: bool = False) -> None:
        self.model, self.read_only, self._drag = model, read_only, None
        self.points = list(model.points()) if model is not None and model.kind != COLORS else []
        self.stops = list(model.stops()) if model is not None and model.kind == COLORS else []
        self.update()

    # geometry
    def _plot(self) -> QRectF:
        return QRectF(MARGIN, MARGIN / 2, max(1, self.width() - 1.5 * MARGIN), max(1, self.height() - 1.5 * MARGIN))

    def _range(self) -> tuple[float, float]:
        """The values the plot spans: the curve's range and its points, never empty."""
        lo, hi = sorted(self.model.range()) if self.model is not None else (0.0, 1.0)
        values = [y for _x, y in self.points]
        lo, hi = min([lo, *values]), max([hi, *values])
        return (lo, hi) if hi > lo else (lo - 0.5, lo + 0.5)

    def _to_screen(self, x: float, y: float) -> QPointF:
        r, (lo, hi) = self._plot(), self._range()
        return QPointF(r.left() + x * r.width(), r.bottom() - (y - lo) / ((hi - lo) or 1.0) * r.height())

    def _from_screen(self, p: QPointF) -> tuple[float, float]:
        r, (lo, hi) = self._plot(), self._range()
        x = min(max((p.x() - r.left()) / r.width(), 0.0), 1.0)
        y = lo + (r.bottom() - p.y()) / r.height() * (hi - lo)
        low, high = sorted(self.model.range())
        return x, min(max(y, low), high)

    def _hit(self, p: QPointF) -> int | None:
        if self.model is not None and self.model.kind == COLORS:
            r = self._plot()
            found = [k for k, (t, _c) in enumerate(self.stops) if abs(r.left() + t * r.width() - p.x()) <= HIT]
        else:
            found = [k for k, (x, y) in enumerate(self.points)
                     if (self._to_screen(x, y) - p).manhattanLength() <= 2 * HIT]  # fmt: skip
        return found[0] if found else None

    # drawing
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._plot()
        p.fillRect(self.rect(), self.palette().base())
        p.setPen(QPen(self.palette().mid().color(), 1))
        p.drawRect(r)
        if self.model is None:
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, tr("particles.pick_curve_graph_colour_list"))
            return
        if self.model.kind == COLORS:
            self._paint_band(p, r)
        else:
            self._paint_graph(p, r)

    def _paint_graph(self, p: QPainter, r: QRectF) -> None:
        lo, hi = self._range()
        p.setPen(self.palette().text().color())
        p.drawText(QRectF(0, r.top() - 4, MARGIN - 2, 14), Qt.AlignmentFlag.AlignRight, f"{hi:g}")
        p.drawText(QRectF(0, r.bottom() - 10, MARGIN - 2, 14), Qt.AlignmentFlag.AlignRight, f"{lo:g}")
        line = QPolygonF([self._to_screen(k / SAMPLES, self._value(k / SAMPLES)) for k in range(SAMPLES + 1)])
        p.setPen(QPen(QColor("#f28c28"), 2))
        p.drawPolyline(line)
        p.setPen(QPen(self.palette().text().color(), 1))
        p.setBrush(QColor("#5aa9e6"))
        for x, y in self.points:
            p.drawEllipse(self._to_screen(x, y), 4, 4)

    def _value(self, x: float) -> float:
        """The curve at ``x`` as it is drawn: the model's own reading while nothing is dragged, else straight lines."""
        if self._drag is None:
            return self.model.at(x)
        pts = sorted(self.points)
        if x <= pts[0][0]:
            return pts[0][1]
        for (xa, ya), (xb, yb) in zip(pts, pts[1:], strict=False):
            if x <= xb:
                return ya if xb - xa < 1e-9 else ya + (yb - ya) * (x - xa) / (xb - xa)
        return pts[-1][1]

    def _paint_band(self, p: QPainter, r: QRectF) -> None:
        band = QRectF(r.left(), r.top() + r.height() * 0.25, r.width(), r.height() * 0.5)
        gradient = QLinearGradient(band.topLeft(), band.topRight())
        for t, c in self.stops:
            gradient.setColorAt(min(max(t, 0.0), 1.0), QColor.fromRgbF(*(min(max(v, 0.0), 1.0) for v in c[:3])))
        p.fillRect(band, gradient if self.stops else QColor("white"))
        p.setPen(QPen(self.palette().text().color(), 1))
        for t, c in self.stops:
            x = r.left() + t * r.width()
            p.setBrush(QColor.fromRgbF(*(min(max(v, 0.0), 1.0) for v in c[:3])))
            p.drawPolygon(QPolygonF([QPointF(x, band.bottom()), QPointF(x - 6, band.bottom() + 10),
                                     QPointF(x + 6, band.bottom() + 10)]))  # fmt: skip

    # the mouse
    def mousePressEvent(self, e) -> None:
        if self.model is None or self.read_only:
            return
        k = self._hit(e.position())
        if e.button() == Qt.MouseButton.RightButton and k is not None:
            self._remove(k)
        elif e.button() == Qt.MouseButton.LeftButton:
            self._drag, self._moved = k, False

    def mouseMoveEvent(self, e) -> None:
        if self._drag is None:
            return
        x, y = self._from_screen(e.position())
        if self.model.kind == COLORS:
            self._moved |= self.stops[self._drag][0] != x
            self.stops[self._drag] = (x, self.stops[self._drag][1])
        else:
            self._moved |= self.points[self._drag] != (x, y)
            self.points[self._drag] = (x, y)
        self.update()

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is not None:
            self._drag = None
            if self._moved:
                self._emit()

    def mouseDoubleClickEvent(self, e) -> None:
        if self.model is None or self.read_only:
            return
        k = self._hit(e.position())
        x, y = self._from_screen(e.position())
        if self.model.kind != COLORS:
            if k is None:
                self.points.append((x, y))
                self._emit()
            return
        if k is not None:
            self._pick(k)
        else:
            self.stops.append((x, self._colour_at(x)))
            self._emit()

    def _remove(self, k: int) -> None:
        held = self.stops if self.model.kind == COLORS else self.points
        if len(held) > 1:
            del held[k]
            self._drag = None  # a drag under way holds a place in the list that is another point's now
            self._emit()

    def _colour_at(self, t: float) -> tuple:
        stops = sorted(self.stops, key=lambda s: s[0])
        if not stops:
            return (1.0, 1.0, 1.0, 1.0)
        for (ta, ca), (tb, cb) in zip(stops, stops[1:], strict=False):
            if ta <= t <= tb:
                return _mix(ca, cb, (t - ta) / (tb - ta) if tb > ta else 0.0)
        return stops[0][1] if t < stops[0][0] else stops[-1][1]

    def _pick(self, k: int) -> None:
        t, c = self.stops[k]
        picked = widgets.pick_qcolor(self, QColor.fromRgbF(*(min(max(v, 0.0), 1.0) for v in c[:3])))
        if picked is not None:
            self.stops[k] = (t, (picked.redF(), picked.greenF(), picked.blueF(), c[3] if len(c) > 3 else 1.0))
            self._emit()

    def _emit(self) -> None:
        if self.model.kind == COLORS:
            self.stops_edited.emit(sorted(self.stops, key=lambda s: s[0]))
        else:
            self.points_edited.emit(sorted(self.points))
        self.update()
