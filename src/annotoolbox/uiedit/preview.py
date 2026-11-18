"""The preview of the UI editor (Qt): the placed frame drawn through the canvas, zoom and pan, the selected
element's outline with a size handle, a click selects, a drag moves, the handle sizes.

``Preview`` is the widget; it draws ``layout.Frame`` through ``canvas.PainterCanvas`` and reports what the user
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
from .layout import Box, Frame, apply, invert

SELECTED, HOVER, HANDLE = (255, 170, 40), (120, 200, 255), 7.0
CHECKER = (38, 38, 44)


class Preview(QWidget):
    """``selected(node id)``: a click; ``moved(node id, dx, dy)``: a drag in the parent's units; ``sized(node id,
    fx, fy)``: the handle dragged, as factors of the element's scale."""

    selected = Signal(object)  # node ids are Python ids: 64 bits, beyond a Qt int
    moved = Signal(object, float, float)
    sized = Signal(object, float, float)
    dropped = Signal(str, str, float, float)  # a palette entry (what, which) dropped at a point of the reference space

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.frame: Frame | None = None
        self.pictures = None
        self.world: worldscene.Scene | None = None  # View > the 3D scene behind the UI; None draws the checker
        self._world_image = None
        self.zoom, self.offset, self.fit = 0.5, QPointF(20, 20), True
        self.current: int | None = None
        self.outlines = True
        self.overlay: list[tuple[tuple[float, float, float, float], tuple[int, int, int]]] = []  # boxes of a comparison
        self._drag: tuple[str, QPointF] | None = None
        self._pan: QPointF | None = None
        self._delta = QPointF()
        self.play = None  # Play mode (``playmode.PlayController``): it takes the pointer and the keys while it is on
        self.play_effects = True  # View > Play the effects over time; off shows time zero
        self.effect_seconds = 0.0  # what the moving effects (a scroll, a blink) are drawn at
        self._moving = False
        self._began = monotonic()
        self._clock = QTimer(self)
        self._clock.setInterval(round(effects.STEP_SECONDS * 1000))
        self._clock.timeout.connect(self._tick)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # what is shown

    def show_frame(self, frame: Frame | None, pictures) -> None:
        self.frame, self.pictures = frame, pictures
        self._moving = frame is not None and canvas.moves(frame)
        self._set_clock()
        if self.fit:
            self.fit_view()
        self.update()

    def set_world(self, scene: worldscene.Scene | None) -> None:
        """Show ``scene`` behind the UI (the effects that read the 3D scene then draw from it), or none."""
        self.world = scene
        self._world_image = worldscene.to_qimage(scene) if scene is not None else None
        self.update()

    def set_play_effects(self, on: bool) -> None:
        """Run the moving effects over time, or freeze them at time zero."""
        self.play_effects = on
        self._set_clock()
        self.update()

    def _set_clock(self) -> None:
        """The clock runs only while the file has a moving effect and playing is on (a still file costs nothing)."""
        run = self.play_effects and self._moving
        if run and not self._clock.isActive():
            self._began = monotonic() - self.effect_seconds
            self._clock.start()
        elif not run:
            self._clock.stop()
            self.effect_seconds = 0.0

    def _tick(self) -> None:
        if self.isVisible():
            self.effect_seconds = monotonic() - self._began
            self.update()

    def select(self, node_id: int | None) -> None:
        self.current = node_id
        self.update()

    def fit_view(self) -> None:
        if self.frame is None:
            return
        w, h = self.frame.size
        self.zoom = max(0.05, min((self.width() - 40) / max(w, 1), (self.height() - 40) / max(h, 1)))
        self.offset = QPointF((self.width() - w * self.zoom) / 2, (self.height() - h * self.zoom) / 2)
        self.update()

    def to_ref(self, p: QPointF) -> QPointF:
        return QPointF((p.x() - self.offset.x()) / self.zoom, (p.y() - self.offset.y()) / self.zoom)

    # drawing

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.canvas()["bg"]))
        if self.frame is None:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("ui.open_scene_widget_file_left"))
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(self.offset)
        p.scale(self.zoom, self.zoom)
        w, h = self.frame.size
        if self._world_image is not None:
            p.drawImage(QRectF(0, 0, w, h), self._world_image)
        else:
            p.fillRect(QRectF(0, 0, w, h), QColor(*CHECKER))
        canvas.draw(self.frame, canvas.PainterCanvas(p), self.pictures, self.effect_seconds, self.world)
        for (x0, y0, x1, y1), rgb in self.overlay:
            p.setPen(QPen(QColor(*rgb), 0, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))
        box = self.frame.boxes.get(self.current) if self.current is not None else None
        if box is not None:
            m = box.matrix
            if self._drag is not None and self._drag[0] == "move":
                m = (*m[:4], m[4] + self._delta.x(), m[5] + self._delta.y())
            pc = canvas.PainterCanvas(p)
            pc.outline(m, self._shown_size(box), SELECTED, 2.0)
            x, y = apply(m, *self._shown_size(box))
            p.setPen(QPen(QColor(*SELECTED)))
            p.setBrush(QColor(*SELECTED))
            r = HANDLE / self.zoom
            p.drawRect(QRectF(x - r / 2, y - r / 2, r, r))
        p.setPen(QPen(QColor(90, 90, 100), 0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(0, 0, w, h))
        if self.play is not None:
            self.play.paint(p)  # the keyboard's widget and the info tip

    def _playing(self) -> bool:
        return self.play is not None and self.play.active

    def _shown_size(self, box: Box) -> tuple[float, float]:
        w, h = box.size
        if self._drag is not None and self._drag[0] == "size":
            fx, fy = self._factors(box)
            return w * fx, h * fy
        return w, h

    # input

    def pick(self, p: QPointF) -> int | None:
        """The element under a point of the reference space: the last drawn (children over parents)."""
        if self.frame is None:
            return None
        for node_id, box in reversed(list(self.frame.boxes.items())):
            if not box.visible or box.mask or box.size[0] <= 0 or box.size[1] <= 0:  # masks take no input
                continue
            x, y = apply(invert(box.matrix), p.x(), p.y())
            if 0 <= x <= box.size[0] and 0 <= y <= box.size[1]:
                return node_id
        return None

    def _on_handle(self, pos: QPointF) -> bool:
        box = self.frame.boxes.get(self.current) if self.frame and self.current is not None else None
        if box is None:
            return False
        x, y = apply(box.matrix, *box.size)
        r = HANDLE / self.zoom
        q = self.to_ref(pos)
        return abs(q.x() - x) <= r and abs(q.y() - y) <= r

    def _factors(self, box: Box) -> tuple[float, float]:
        inv = invert(box.matrix)
        x, y = apply(box.matrix, *box.size)
        lx, ly = apply(inv, x + self._delta.x(), y + self._delta.y())
        return max(0.01, lx / box.size[0]) if box.size[0] else 1.0, max(0.01, ly / box.size[1]) if box.size[1] else 1.0

    def mousePressEvent(self, event) -> None:
        pos = event.position()
        if event.button() == Qt.MouseButton.MiddleButton or event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._pan = pos
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._playing():  # Play mode: a press of the widget, no selection and no edit
            self.play.mouse("press", self.to_ref(pos), pos)
            return
        if self._on_handle(pos):
            self._drag, self._delta = ("size", self.to_ref(pos)), QPointF()
            return
        hit = self.pick(self.to_ref(pos))
        if hit is not None and hit != self.current:
            self.current = hit
            self.selected.emit(hit)
        if hit is not None:
            self._drag, self._delta = ("move", self.to_ref(pos)), QPointF()
        self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._pan is not None:
            self.offset += pos - self._pan
            self._pan, self.fit = pos, False
            self.update()
            return
        if self._playing():
            self.play.mouse("move", self.to_ref(pos), pos)
            return
        if self._drag is not None:
            self._delta = self.to_ref(pos) - self._drag[1]
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._pan is not None:
            self._pan = None
            return
        if self._playing():
            if event.button() == Qt.MouseButton.LeftButton:
                self.play.mouse("release", self.to_ref(event.position()), event.position())
            return
        drag, self._drag = self._drag, None
        if drag is None or self.current is None or self.frame is None:
            return
        box = self.frame.boxes.get(self.current)
        moved = abs(self._delta.x()) + abs(self._delta.y()) > 0.5
        if box is None or not moved:
            self.update()
            return
        if drag[0] == "size":
            fx, fy = self._factors(box)
            self.sized.emit(self.current, fx, fy)
        else:  # the world move in the parent's units
            pi = invert(box.parent)
            x0, y0 = apply(pi, 0.0, 0.0)
            x1, y1 = apply(pi, self._delta.x(), self._delta.y())
            self.moved.emit(self.current, x1 - x0, y1 - y0)
        self._delta = QPointF()

    def dragEnterEvent(self, event) -> None:
        if pickers.read_mime(event.mimeData()) is not None and not self._playing():  # Play mode edits nothing
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if pickers.read_mime(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        found = pickers.read_mime(event.mimeData())
        if found is not None:
            at = self.to_ref(event.position())
            self.dropped.emit(found[0], found[1], at.x(), at.y())
            event.acceptProposedAction()

    def wheelEvent(self, event) -> None:
        if self._playing() and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:  # Ctrl zooms in Play mode
            self.play.wheel(self.to_ref(event.position()), -1 if event.angleDelta().y() > 0 else 1)
            return
        before = self.to_ref(event.position())
        self.zoom = max(0.05, min(8.0, self.zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        after = self.to_ref(event.position())
        self.offset += QPointF((after.x() - before.x()) * self.zoom, (after.y() - before.y()) * self.zoom)
        self.fit = False
        self.update()

    def resizeEvent(self, event) -> None:
        if self.fit:
            self.fit_view()
        super().resizeEvent(event)

    def leaveEvent(self, event) -> None:
        if self._playing():
            self.play.leave()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._playing() and self.play.key(event):  # Enter or Space presses the focused widget
            return
        super().keyPressEvent(event)

    def focusNextPrevChild(self, forward: bool) -> bool:
        if self._playing():  # Tab walks the scene's widgets
            self.play.move_focus(1 if forward else -1)
            return True
        return super().focusNextPrevChild(forward)
