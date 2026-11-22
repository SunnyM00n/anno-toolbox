"""The map from above (Qt): the sea, the map's edge, the margin outside the playable area, the initial playable area,
each fixed island as its map picture, random slots as squares of their size coloured by type, start positions as
rings; select (an island of a complex selects the complex, Alt only the island), drag (snapped), turn, remove, zoom
and pan. North is up: x to the right, z upwards.

``CMapView.set_doc(doc)`` shows an open map (``mapdoc.CMapDoc``); every edit goes through the document's undo stack and
``edited`` is emitted after it. docs/map-editor.md "The window".
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..infra.i18n import tr
from . import pool
from .placing import complex_of
from .template import KIND_FIXED, KIND_SLOT, KIND_START, SNAP

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
LETTERS = dict(enumerate(("S", "M", "L", "XL", "C")))  # a slot's size class as drawn on it


class CMapView(QWidget):
    edited = Signal()  # after an edit of the document (the window refreshes its panel and checks)
    selection_changed = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 320)
        self.m_doc = None
        self.m_pictures: dict[str, QImage] = {}  # island path -> its map picture
        self.m_selected: list[int] = []
        self.m_flagged: set[int] = set()  # elements a check names
        self.m_scale, self.m_origin = 0.3, QPointF(20, 20)
        self._m_drag: tuple[QPointF, int, int] | None = None  # press point, dx, dz (tiles, snapped)
        self._m_pan: QPointF | None = None
        self._m_has_user_view = False  # the user zoomed or panned: a resize keeps the view

    # the document
    def set_doc(self, doc) -> None:
        self.m_doc, self.m_selected, self.m_flagged = doc, [], set()
        self.fit()

    def set_picture(self, path: str, data: bytes) -> None:
        image = QImage.fromData(data)
        if not image.isNull():
            self.m_pictures[path] = _cleaned(image)
            self.update()

    def select(self, indexes: list[int]) -> None:
        self.m_selected = [i for i in indexes if self.m_doc is not None and 0 <= i < len(self.m_doc.elements)]
        self.selection_changed.emit(list(self.m_selected))
        self.update()

    # coordinates: x to the right, z upwards
    def fit(self) -> None:
        if self.m_doc is None:
            return
        self._m_has_user_view = False
        width_tiles, height_tiles = self.m_doc.size
        side = max(width_tiles, height_tiles, 1)
        self.m_scale = 0.95 * min(self.width(), self.height()) / side
        self.m_origin = QPointF(
            (self.width() - width_tiles * self.m_scale) / 2, (self.height() + height_tiles * self.m_scale) / 2
        )
        self.update()

    def to_screen(self, x: float, z: float) -> QPointF:
        return QPointF(self.m_origin.x() + x * self.m_scale, self.m_origin.y() - z * self.m_scale)

    def to_map(self, point: QPointF) -> tuple[float, float]:
        return (point.x() - self.m_origin.x()) / self.m_scale, (self.m_origin.y() - point.y()) / self.m_scale

    def _rect(self, x0: float, z0: float, x1: float, z1: float) -> QRectF:
        return QRectF(self.to_screen(x0, z1), self.to_screen(x1, z0))

    # painting
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 26, 30))
        if self.m_doc is None:
            painter.setPen(EDGE)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("maps.open_game_map_left"))
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width_tiles, height_tiles = self.m_doc.size
        painter.fillRect(self._rect(0, 0, width_tiles, height_tiles), SEA)
        self._margin(painter, width_tiles, height_tiles)
        for i, element in enumerate(self.m_doc.elements):
            if element.kind != KIND_START:
                self._island(painter, i, element)
        for i, element in enumerate(self.m_doc.elements):
            if element.kind == KIND_START:
                self._start(painter, i, element)
        painter.end()

    def _margin(self, painter: QPainter, width_tiles: int, height_tiles: int) -> None:
        x0, z0, x1, z1 = self.m_doc.playable
        outer = self._rect(0, 0, width_tiles, height_tiles)
        inner = self._rect(x0, z0, x1, z1)
        for band in (
            QRectF(outer.left(), outer.top(), outer.width(), inner.top() - outer.top()),
            QRectF(outer.left(), inner.bottom(), outer.width(), outer.bottom() - inner.bottom()),
            QRectF(outer.left(), inner.top(), inner.left() - outer.left(), inner.height()),
            QRectF(inner.right(), inner.top(), outer.right() - inner.right(), inner.height()),
        ):
            painter.fillRect(band, MARGIN)
        painter.setPen(QPen(EDGE, 1))
        painter.drawRect(outer)
        painter.setPen(QPen(EDGE.darker(130), 1))
        painter.drawRect(inner)
        initial = self.m_doc.initial
        if initial and any(initial):
            painter.setPen(QPen(EDGE, 1, Qt.PenStyle.DashLine))
            painter.drawRect(self._rect(*initial))

    def _shift(self, i: int) -> tuple[int, int]:
        return (self._m_drag[1], self._m_drag[2]) if self._m_drag is not None and i in self.m_selected else (0, 0)

    def _island(self, painter: QPainter, i: int, element) -> None:
        dx, dz = self._shift(i)
        edge = self.m_doc.extent(element)
        rect = self._rect(element.x + dx, element.z + dz, element.x + dx + edge, element.z + dz + edge)
        if element.kind == KIND_FIXED:
            image = self.m_pictures.get(element.MapFilePath)
            if image is not None:
                painter.save()
                painter.translate(rect.center())
                painter.rotate(
                    -90 * element.Rotation90
                )  # a quarter turn counter-clockwise from above (docs/map-editor.md)
                painter.drawImage(QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height()), image)
                painter.restore()
            else:
                land = self.m_doc.land(element)
                painter.fillRect(self._rect(land[0] + dx, land[1] + dz, land[2] + dx, land[3] + dz), LAND)
            pen = QPen(EDGE, 1, Qt.PenStyle.DotLine)
        else:
            colour = QColor(TYPE_COLOURS.get(element.type, TYPE_COLOURS[0]))
            colour.setAlpha(110)
            painter.fillRect(rect, QBrush(colour))
            pen = QPen(TYPE_COLOURS.get(element.type, TYPE_COLOURS[0]), 1)
            painter.setPen(EDGE)
            text = LETTERS.get(element.size, "?") if self.m_doc.is_template else str(edge)  # i18n: ok
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if i in self.m_flagged:
            pen = QPen(FLAGGED, 2)
        if i in self.m_selected:
            pen = QPen(SELECTED, 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    def _start(self, painter: QPainter, i: int, element) -> None:
        dx, dz = self._shift(i)
        centre = self.to_screen(element.x + dx, element.z + dz)
        colour = SELECTED if i in self.m_selected else FLAGGED if i in self.m_flagged else START
        painter.setPen(QPen(colour, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, START_RADIUS, START_RADIUS)
        painter.drawLine(centre + QPointF(-3, 0), centre + QPointF(3, 0))

    # picking and editing
    def element_at(self, position: QPointF) -> int | None:
        """The element under a point: start positions first, then slots, then fixed islands, the smallest first."""
        if self.m_doc is None:
            return None
        x, z = self.to_map(position)
        best, best_edge = None, None
        for i, element in enumerate(self.m_doc.elements):
            if element.kind == KIND_START:
                centre = self.to_screen(element.x, element.z)
                if (centre - position).manhattanLength() <= START_RADIUS * 2:
                    return i
                continue
            edge = self.m_doc.extent(element)
            rank = edge + (0 if element.kind == KIND_SLOT else 100_000)
            if (
                element.x <= x <= element.x + edge
                and element.z <= z <= element.z + edge
                and (best_edge is None or rank < best_edge)
            ):
                best, best_edge = i, rank
        return best

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton or event.button() == Qt.MouseButton.MiddleButton:
            self._m_pan = event.position()
            return
        hit = self.element_at(event.position())
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if hit is None:
            if not shift:
                self.select([])
            return
        alone = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        group = (
            [hit] if alone or self.m_doc.elements[hit].kind != KIND_FIXED else complex_of(hit, self.m_doc.complexes())
        )
        if shift:
            held = [i for i in self.m_selected if i not in group]
            self.select(held if hit in self.m_selected else [*self.m_selected, *group])
        elif hit not in self.m_selected:
            self.select(group)
        self._m_drag = (event.position(), 0, 0)

    def mouseMoveEvent(self, event) -> None:
        if self._m_pan is not None:
            self.m_origin += event.position() - self._m_pan
            self._m_has_user_view = True
            self._m_pan = event.position()
            self.update()
            return
        if self._m_drag is not None:
            start = self._m_drag[0]
            dx = round((event.position().x() - start.x()) / self.m_scale / SNAP) * SNAP
            dz = round((start.y() - event.position().y()) / self.m_scale / SNAP) * SNAP
            self._m_drag = (start, dx, dz)
            self.update()

    def mouseReleaseEvent(self, _event) -> None:
        self._m_pan = None
        if self._m_drag is not None:
            _start, dx, dz = self._m_drag
            self._m_drag = None
            if (dx or dz) and self.m_selected:
                self.m_doc.move(list(self.m_selected), dx, dz)
                self.edited.emit()
            self.update()

    def wheelEvent(self, event) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        position = event.position()
        before = self.to_map(position)
        self.m_scale = min(max(self.m_scale * factor, 0.02), 20.0)
        after = self.to_screen(*before)
        self.m_origin += position - after
        self._m_has_user_view = True
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
        elif self.m_doc is None or not self.m_selected:
            super().keyPressEvent(event)
        elif key in steps:
            self.m_doc.move(list(self.m_selected), *steps[key])
            self.edited.emit()
            self.update()
        elif key == Qt.Key.Key_R:
            self.rotate_selected()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_selected()
        else:
            super().keyPressEvent(event)

    def rotate_selected(self) -> None:
        """A quarter turn: one fixed island in place, a selection of several as a group (``CMapDoc.rotate``)."""
        chosen = list(self.m_selected)
        if self.m_doc is not None and (
            len(chosen) > 1 or (chosen and self.m_doc.elements[chosen[0]].kind == KIND_FIXED)
        ):
            self.m_doc.rotate(chosen)
            self.edited.emit()
            self.update()

    def remove_selected(self) -> None:
        if self.m_doc is not None and self.m_selected:
            self.m_doc.remove(list(self.m_selected))
            self.select([])
            self.edited.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.m_doc is not None and not self._m_has_user_view:
            self.fit()  # until the user moves the view, a resize (the first show) refits


def _cleaned(image: QImage) -> QImage:
    """``pool.clean_picture`` on a QImage: the black around the land and the game's stripes below it transparent."""
    import numpy as np

    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width_px, height_px = rgba.width(), rgba.height()
    pixels = np.frombuffer(rgba.constBits(), np.uint8, count=rgba.bytesPerLine() * height_px).reshape(
        height_px, rgba.bytesPerLine()
    )
    clean = np.ascontiguousarray(pool.clean_picture(pixels[:, : width_px * 4].reshape(height_px, width_px, 4)))
    return QImage(
        clean.data, width_px, height_px, width_px * 4, QImage.Format.Format_RGBA8888
    ).copy()  # copy: the array goes away
