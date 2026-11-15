"""The map from above (Qt): the sea, the map's edge, the margin outside the playable area, the initial playable area,
each fixed island as its map picture, random slots as squares of their size coloured by type, start positions as
rings; select (an island of a complex selects the complex, Alt only the island), drag (snapped), turn, remove, zoom
and pan. North is up: x to the right, z upwards.

``MapView.set_doc(doc)`` shows an open map (``mapdoc.MapDoc``); every edit goes through the document's undo stack and
``edited`` is emitted after it. docs/map-editor.md "The window".
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..infra.i18n import tr
from .placing import complex_of
from .template import KIND_FIXED, KIND_SLOT, KIND_START, SIZES, SNAP

SEA, EDGE, MARGIN, LAND = QColor(36, 70, 102), QColor(220, 220, 220), QColor(0, 0, 0, 90), QColor(190, 170, 120)
TYPE_COLOURS = {
    0: QColor(110, 170, 90),  # Normal
    1: QColor(230, 200, 60),  # Starter
    2: QColor(150, 150, 150),  # Decoration
    3: QColor(90, 150, 220),  # Third party
    4: QColor(200, 70, 60),  # Pirate
    5: QColor(120, 100, 160),  # Cliff island
    6: QColor(240, 150, 60),  # Reserved for a player
    7: QColor(170, 80, 40),  # Volcanic
}
SELECTED, FLAGGED, START = QColor(255, 255, 255), QColor(255, 80, 80), QColor(255, 230, 120)
START_RADIUS = 7  # pixels
LETTERS = {i: s[0] if s != "XL" else "XL" for i, s in enumerate(SIZES)}


class MapView(QWidget):
    edited = Signal()  # after an edit of the document (the window refreshes its panel and checks)
    selection_changed = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 320)
        self.doc = None
        self.pictures: dict[str, QImage] = {}  # island path -> its map picture
        self.selected: list[int] = []
        self.flagged: set[int] = set()  # elements a check names
        self.scale, self.origin = 0.3, QPointF(20, 20)
        self._drag: tuple[QPointF, int, int] | None = None  # press point, dx, dz (tiles, snapped)
        self._pan: QPointF | None = None

    # the document
    def set_doc(self, doc) -> None:
        self.doc, self.selected, self.flagged = doc, [], set()
        self.fit()

    def set_picture(self, path: str, data: bytes) -> None:
        image = QImage.fromData(data)
        if not image.isNull():
            self.pictures[path] = image
            self.update()

    def select(self, indexes: list[int]) -> None:
        self.selected = [i for i in indexes if self.doc is not None and 0 <= i < len(self.doc.elements)]
        self.selection_changed.emit(list(self.selected))
        self.update()

    # coordinates: x to the right, z upwards
    def fit(self) -> None:
        if self.doc is None:
            return
        w, h = self.doc.size
        side = max(w, h, 1)
        self.scale = 0.95 * min(self.width(), self.height()) / side
        self.origin = QPointF((self.width() - w * self.scale) / 2, (self.height() + h * self.scale) / 2)
        self.update()

    def to_screen(self, x: float, z: float) -> QPointF:
        return QPointF(self.origin.x() + x * self.scale, self.origin.y() - z * self.scale)

    def to_map(self, p: QPointF) -> tuple[float, float]:
        return (p.x() - self.origin.x()) / self.scale, (self.origin.y() - p.y()) / self.scale

    def _rect(self, x0: float, z0: float, x1: float, z1: float) -> QRectF:
        return QRectF(self.to_screen(x0, z1), self.to_screen(x1, z0))

    # painting
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(24, 26, 30))
        if self.doc is None:
            p.setPen(EDGE)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("Open a game, then a map on the left."))
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.doc.size
        p.fillRect(self._rect(0, 0, w, h), SEA)
        self._margin(p, w, h)
        for i, e in enumerate(self.doc.elements):
            if e.kind != KIND_START:
                self._island(p, i, e)
        for i, e in enumerate(self.doc.elements):
            if e.kind == KIND_START:
                self._start(p, i, e)
        p.end()

    def _margin(self, p: QPainter, w: int, h: int) -> None:
        x0, z0, x1, z1 = self.doc.playable
        outer = self._rect(0, 0, w, h)
        inner = self._rect(x0, z0, x1, z1)
        for band in (
            QRectF(outer.left(), outer.top(), outer.width(), inner.top() - outer.top()),
            QRectF(outer.left(), inner.bottom(), outer.width(), outer.bottom() - inner.bottom()),
            QRectF(outer.left(), inner.top(), inner.left() - outer.left(), inner.height()),
            QRectF(inner.right(), inner.top(), outer.right() - inner.right(), inner.height()),
        ):
            p.fillRect(band, MARGIN)
        p.setPen(QPen(EDGE, 1))
        p.drawRect(outer)
        p.setPen(QPen(EDGE.darker(130), 1))
        p.drawRect(inner)
        initial = self.doc.initial
        if initial and any(initial):
            p.setPen(QPen(EDGE, 1, Qt.PenStyle.DashLine))
            p.drawRect(self._rect(*initial))

    def _shift(self, i: int) -> tuple[int, int]:
        return (self._drag[1], self._drag[2]) if self._drag is not None and i in self.selected else (0, 0)

    def _island(self, p: QPainter, i: int, e) -> None:
        dx, dz = self._shift(i)
        edge = self.doc.extent(e)
        rect = self._rect(e.x + dx, e.z + dz, e.x + dx + edge, e.z + dz + edge)
        if e.kind == KIND_FIXED:
            image = self.pictures.get(e.map_file_path)
            if image is not None:
                p.save()
                p.translate(rect.center())
                p.rotate(-90 * e.rotation90)  # a quarter turn counter-clockwise from above (docs/map-editor.md)
                p.drawImage(QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height()), image)
                p.restore()
            else:
                land = self.doc.land(e)
                p.fillRect(self._rect(land[0] + dx, land[1] + dz, land[2] + dx, land[3] + dz), LAND)
            pen = QPen(EDGE, 1, Qt.PenStyle.DotLine)
        else:
            colour = QColor(TYPE_COLOURS.get(e.type, TYPE_COLOURS[0]))
            colour.setAlpha(110)
            p.fillRect(rect, QBrush(colour))
            pen = QPen(TYPE_COLOURS.get(e.type, TYPE_COLOURS[0]), 1)
            p.setPen(EDGE)
            text = LETTERS.get(e.size, "?") if self.doc.is_template else str(edge)  # i18n: ok
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if i in self.flagged:
            pen = QPen(FLAGGED, 2)
        if i in self.selected:
            pen = QPen(SELECTED, 2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)

    def _start(self, p: QPainter, i: int, e) -> None:
        dx, dz = self._shift(i)
        c = self.to_screen(e.x + dx, e.z + dz)
        colour = SELECTED if i in self.selected else FLAGGED if i in self.flagged else START
        p.setPen(QPen(colour, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, START_RADIUS, START_RADIUS)
        p.drawLine(c + QPointF(-3, 0), c + QPointF(3, 0))

    # picking and editing
    def element_at(self, pos: QPointF) -> int | None:
        """The element under a point: start positions first, then slots, then fixed islands, the smallest first."""
        if self.doc is None:
            return None
        x, z = self.to_map(pos)
        best, best_edge = None, None
        for i, e in enumerate(self.doc.elements):
            if e.kind == KIND_START:
                c = self.to_screen(e.x, e.z)
                if (c - pos).manhattanLength() <= START_RADIUS * 2:
                    return i
                continue
            edge = self.doc.extent(e)
            rank = edge + (0 if e.kind == KIND_SLOT else 100_000)
            if e.x <= x <= e.x + edge and e.z <= z <= e.z + edge and (best_edge is None or rank < best_edge):
                best, best_edge = i, rank
        return best

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton or event.button() == Qt.MouseButton.MiddleButton:
            self._pan = event.position()
            return
        hit = self.element_at(event.position())
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if hit is None:
            if not shift:
                self.select([])
            return
        alone = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        group = [hit] if alone or self.doc.elements[hit].kind != KIND_FIXED else complex_of(hit, self.doc.complexes())
        if shift:
            held = [i for i in self.selected if i not in group]
            self.select(held if hit in self.selected else [*self.selected, *group])
        elif hit not in self.selected:
            self.select(group)
        self._drag = (event.position(), 0, 0)

    def mouseMoveEvent(self, event) -> None:
        if self._pan is not None:
            self.origin += event.position() - self._pan
            self._pan = event.position()
            self.update()
            return
        if self._drag is not None:
            start = self._drag[0]
            dx = round((event.position().x() - start.x()) / self.scale / SNAP) * SNAP
            dz = round((start.y() - event.position().y()) / self.scale / SNAP) * SNAP
            self._drag = (start, dx, dz)
            self.update()

    def mouseReleaseEvent(self, _event) -> None:
        self._pan = None
        if self._drag is not None:
            _start, dx, dz = self._drag
            self._drag = None
            if (dx or dz) and self.selected:
                self.doc.move(list(self.selected), dx, dz)
                self.edited.emit()
            self.update()

    def wheelEvent(self, event) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        pos = event.position()
        before = self.to_map(pos)
        self.scale = min(max(self.scale * factor, 0.02), 20.0)
        after = self.to_screen(*before)
        self.origin += pos - after
        self.update()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        steps = {
            Qt.Key.Key_Left: (-SNAP, 0),
            Qt.Key.Key_Right: (SNAP, 0),
            Qt.Key.Key_Up: (0, SNAP),
            Qt.Key.Key_Down: (0, -SNAP),
        }
        if key == Qt.Key.Key_Home:
            self.fit()
        elif self.doc is None or not self.selected:
            super().keyPressEvent(event)
        elif key in steps:
            self.doc.move(list(self.selected), *steps[key])
            self.edited.emit()
            self.update()
        elif key == Qt.Key.Key_R:
            self.rotate_selected()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_selected()
        else:
            super().keyPressEvent(event)

    def rotate_selected(self) -> None:
        """A quarter turn: one fixed island in place, a selection of several as a group (``MapDoc.rotate``)."""
        chosen = list(self.selected)
        if self.doc is not None and (len(chosen) > 1 or (chosen and self.doc.elements[chosen[0]].kind == KIND_FIXED)):
            self.doc.rotate(chosen)
            self.edited.emit()
            self.update()

    def remove_selected(self) -> None:
        if self.doc is not None and self.selected:
            self.doc.remove(list(self.selected))
            self.select([])
            self.edited.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.doc is not None and not self.selected:
            self.fit()
