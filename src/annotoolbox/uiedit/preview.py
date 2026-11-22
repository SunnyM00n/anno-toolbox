"""The preview of the UI editor (Qt): the placed frame drawn through the canvas, zoom and pan, the selected
element's outline with a size handle, a click selects, a drag moves, the handle sizes.

``CPreview`` is the widget; it draws ``layout.Frame`` through ``canvas.CPainterCanvas`` and reports what the user
did as signals in the reference space (the window turns them into edits). docs/ui-editor.md, "The preview".
"""

from __future__ import annotations

from time import monotonic

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..infra.i18n import tr
from ..suite import theme
from . import canvas, effects, pickers, worldscene
from .layout import SBox, SFrame, apply, invert

SELECTED, HOVER, HANDLE = (255, 170, 40), (120, 200, 255), 7.0
CHECKER = (38, 38, 44)


class CPreview(QWidget):
    """``selected(node id)``: a click; ``moved(node id, dx, dy)``: a drag in the parent's units; ``sized(node id,
    fx, fy)``: the handle dragged, as factors of the element's scale."""

    selected = Signal(object)  # node ids are Python ids: 64 bits, beyond a Qt int
    moved = Signal(object, float, float)
    sized = Signal(object, float, float)
    dropped = Signal(str, str, float, float)  # a palette entry (what, which) dropped at a point of the reference space

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.m_frame: SFrame | None = None
        self.m_pictures = None
        self.m_world: worldscene.CScene | None = None  # View > the 3D scene behind the UI; None draws the checker
        self._m_world_image = None
        self.m_zoom, self.m_offset, self.m_fit = 0.5, QPointF(20, 20), True
        self.m_current: int | None = None
        self.m_outlines = True
        self.m_overlay: list[
            tuple[tuple[float, float, float, float], tuple[int, int, int]]
        ] = []  # boxes of a comparison
        self._m_drag: tuple[str, QPointF] | None = None
        self._m_pan: QPointF | None = None
        self._m_delta = QPointF()
        self.m_play = None  # Play mode (``playmode.CPlayController``): it takes the pointer and the keys while it is on
        self.m_play_effects = True  # View > Play the effects over time; off shows time zero
        self.m_effect_seconds = 0.0  # what the moving effects (a scroll, a blink) are drawn at
        self._m_moving = False
        self._m_began = monotonic()
        self._m_clock = QTimer(self)
        self._m_clock.setInterval(round(effects.STEP_SECONDS * 1000))
        self._m_clock.timeout.connect(self._tick)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # what is shown

    def show_frame(self, frame: SFrame | None, pictures) -> None:
        self.m_frame, self.m_pictures = frame, pictures
        self._m_moving = frame is not None and canvas.moves(frame)
        self._set_clock()
        if self.m_fit:
            self.fit_view()
        self.update()

    def set_world(self, scene: worldscene.CScene | None) -> None:
        """Show ``scene`` behind the UI (the effects that read the 3D scene then draw from it), or none."""
        self.m_world = scene
        self._m_world_image = worldscene.to_qimage(scene) if scene is not None else None
        self.update()

    def set_play_effects(self, is_playing: bool) -> None:
        """Run the moving effects over time, or freeze them at time zero."""
        self.m_play_effects = is_playing
        self._set_clock()
        self.update()

    def _set_clock(self) -> None:
        """The clock runs only while the file has a moving effect and playing is on (a still file costs nothing)."""
        run = self.m_play_effects and self._m_moving
        if run and not self._m_clock.isActive():
            self._m_began = monotonic() - self.m_effect_seconds
            self._m_clock.start()
        elif not run:
            self._m_clock.stop()
            self.m_effect_seconds = 0.0

    def _tick(self) -> None:
        if self.isVisible():
            self.m_effect_seconds = monotonic() - self._m_began
            self.update()

    def select(self, node_id: int | None) -> None:
        self.m_current = node_id
        self.update()

    def fit_view(self) -> None:
        if self.m_frame is None:
            return
        frame_width, frame_height = self.m_frame.size
        self.m_zoom = max(
            0.05, min((self.width() - 40) / max(frame_width, 1), (self.height() - 40) / max(frame_height, 1))
        )
        self.m_offset = QPointF(
            (self.width() - frame_width * self.m_zoom) / 2, (self.height() - frame_height * self.m_zoom) / 2
        )
        self.update()

    def to_ref(self, point: QPointF) -> QPointF:
        return QPointF((point.x() - self.m_offset.x()) / self.m_zoom, (point.y() - self.m_offset.y()) / self.m_zoom)

    # drawing

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.canvas()["bg"]))
        if self.m_frame is None:
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("ui.open_scene_widget_file_left"))
            painter.end()
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.m_offset)
        painter.scale(self.m_zoom, self.m_zoom)
        frame_width, frame_height = self.m_frame.size
        if self._m_world_image is not None:
            painter.drawImage(QRectF(0, 0, frame_width, frame_height), self._m_world_image)
        else:
            painter.fillRect(QRectF(0, 0, frame_width, frame_height), QColor(*CHECKER))
        canvas.draw(self.m_frame, canvas.CPainterCanvas(painter), self.m_pictures, self.m_effect_seconds, self.m_world)
        for (x0, y0, x1, y1), rgb in self.m_overlay:
            painter.setPen(QPen(QColor(*rgb), 0, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))
        box = self.m_frame.boxes.get(self.m_current) if self.m_current is not None else None
        if box is not None:
            matrix = box.matrix
            if self._m_drag is not None and self._m_drag[0] == "move":
                matrix = (*matrix[:4], matrix[4] + self._m_delta.x(), matrix[5] + self._m_delta.y())
            painter_canvas = canvas.CPainterCanvas(painter)
            painter_canvas.outline(matrix, self._shown_size(box), SELECTED, 2.0)
            x, y = apply(matrix, *self._shown_size(box))
            painter.setPen(QPen(QColor(*SELECTED)))
            painter.setBrush(QColor(*SELECTED))
            handle_radius = HANDLE / self.m_zoom
            painter.drawRect(QRectF(x - handle_radius / 2, y - handle_radius / 2, handle_radius, handle_radius))
        painter.setPen(QPen(QColor(90, 90, 100), 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(0, 0, frame_width, frame_height))
        if self.m_play is not None:
            self.m_play.paint(painter)  # the keyboard's widget and the info tip
        painter.end()

    def _playing(self) -> bool:
        return self.m_play is not None and self.m_play.m_active

    def _shown_size(self, box: SBox) -> tuple[float, float]:
        width, height = box.size
        if self._m_drag is not None and self._m_drag[0] == "size":
            factor_x, factor_y = self._factors(box)
            return width * factor_x, height * factor_y
        return width, height

    # input

    def pick(self, point: QPointF) -> int | None:
        """The element under a point of the reference space: the last drawn (children over parents)."""
        if self.m_frame is None:
            return None
        for node_id, box in reversed(list(self.m_frame.boxes.items())):
            if not box.visible or box.mask or box.size[0] <= 0 or box.size[1] <= 0:  # masks take no input
                continue
            x, y = apply(invert(box.matrix), point.x(), point.y())
            if 0 <= x <= box.size[0] and 0 <= y <= box.size[1]:
                return node_id
        return None

    def _on_handle(self, position: QPointF) -> bool:
        box = self.m_frame.boxes.get(self.m_current) if self.m_frame and self.m_current is not None else None
        if box is None:
            return False
        x, y = apply(box.matrix, *box.size)
        handle_radius = HANDLE / self.m_zoom
        reference_point = self.to_ref(position)
        return abs(reference_point.x() - x) <= handle_radius and abs(reference_point.y() - y) <= handle_radius

    def _factors(self, box: SBox) -> tuple[float, float]:
        inverse = invert(box.matrix)
        x, y = apply(box.matrix, *box.size)
        local_x, local_y = apply(inverse, x + self._m_delta.x(), y + self._m_delta.y())
        return max(0.01, local_x / box.size[0]) if box.size[0] else 1.0, max(0.01, local_y / box.size[1]) if box.size[
            1
        ] else 1.0

    def mousePressEvent(self, event) -> None:
        position = event.position()
        if event.button() == Qt.MouseButton.MiddleButton or event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._m_pan = position
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._playing():  # Play mode: a press of the widget, no selection and no edit
            self.m_play.mouse("press", self.to_ref(position), position)
            return
        if self._on_handle(position):
            self._m_drag, self._m_delta = ("size", self.to_ref(position)), QPointF()
            return
        hit = self.pick(self.to_ref(position))
        if hit is not None and hit != self.m_current:
            self.m_current = hit
            self.selected.emit(hit)
        if hit is not None:
            self._m_drag, self._m_delta = ("move", self.to_ref(position)), QPointF()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        position = event.position()
        if self._m_pan is not None:
            self.m_offset += position - self._m_pan
            self._m_pan, self.m_fit = position, False
            self.update()
            return
        if self._playing():
            self.m_play.mouse("move", self.to_ref(position), position)
            return
        if self._m_drag is not None:
            self._m_delta = self.to_ref(position) - self._m_drag[1]
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._m_pan is not None:
            self._m_pan = None
            return
        if self._playing():
            if event.button() == Qt.MouseButton.LeftButton:
                self.m_play.mouse("release", self.to_ref(event.position()), event.position())
            return
        drag, self._m_drag = self._m_drag, None
        if drag is None or self.m_current is None or self.m_frame is None:
            return
        box = self.m_frame.boxes.get(self.m_current)
        moved = abs(self._m_delta.x()) + abs(self._m_delta.y()) > 0.5
        if box is None or not moved:
            self.update()
            return
        if drag[0] == "size":
            factor_x, factor_y = self._factors(box)
            self.sized.emit(self.m_current, factor_x, factor_y)
        else:  # the world move in the parent's units
            parent_inverse = invert(box.parent)
            x0, y0 = apply(parent_inverse, 0.0, 0.0)
            x1, y1 = apply(parent_inverse, self._m_delta.x(), self._m_delta.y())
            self.moved.emit(self.m_current, x1 - x0, y1 - y0)
        self._m_delta = QPointF()

    def dragEnterEvent(self, event) -> None:
        if pickers.read_mime(event.mimeData()) is not None and not self._playing():  # Play mode edits nothing
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if pickers.read_mime(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        found = pickers.read_mime(event.mimeData())
        if found is not None:
            point = self.to_ref(event.position())
            self.dropped.emit(found[0], found[1], point.x(), point.y())
            event.acceptProposedAction()

    def wheelEvent(self, event) -> None:
        if self._playing() and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:  # Ctrl zooms in Play mode
            self.m_play.wheel(self.to_ref(event.position()), -1 if event.angleDelta().y() > 0 else 1)
            return
        before = self.to_ref(event.position())
        self.m_zoom = max(0.05, min(8.0, self.m_zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        after = self.to_ref(event.position())
        self.m_offset += QPointF((after.x() - before.x()) * self.m_zoom, (after.y() - before.y()) * self.m_zoom)
        self.m_fit = False
        self.update()

    def resizeEvent(self, event) -> None:
        if self.m_fit:
            self.fit_view()
        super().resizeEvent(event)

    def leaveEvent(self, event) -> None:
        if self._playing():
            self.m_play.leave()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._playing() and self.m_play.key(event):  # Enter or Space presses the focused widget
            return
        super().keyPressEvent(event)

    def focusNextPrevChild(self, forward: bool) -> bool:
        if self._playing():  # Tab walks the scene's widgets
            self.m_play.move_focus(1 if forward else -1)
            return True
        return super().focusNextPrevChild(forward)
