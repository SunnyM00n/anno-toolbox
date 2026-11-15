"""The handles over the 3D view: an event filter on the view that takes a press near a handle (a part's gizmo, a point
of an info-file polygon, a footprint being drawn) before the view's camera sees it, and paints what it grabs.

``HandleTool(view, overlay, host)`` is the entry; the window tells it the selected row (``select``), the mode
(``set_mode``: off, move, rotate, scale) and whether a footprint is being drawn (``draw_footprint``). A drag
(``objbuild.handledrag``) is shown as it goes (``host.preview``) and ends as one undo step (``host.apply``).
docs/tool-suite.md section 23, "Handles in the view".
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

from ..infra.i18n import tr
from ..othergames import installs
from ..render import picking
from . import areaedit, handles
from .handledrag import PartDrag, VertexDrag
from .overlay import project

MODES = ("off", "move", "rotate", "scale")
GRAB = 9.0  # pixels: a press this near a handle is the tool's
RING_POINTS = 48
REACH = 0.16  # the gizmo's size: this part of the distance from the eye to the pivot
SCALE_RING = 70.0  # pixels: the scale handle's circle about the pivot
OLD_GAMES = installs.keys(lambda g: g.family == installs.OLD)  # their info files are in fixed point
COLORS = ("#e05050", "#6cc04a", "#4f9fd9")  # x, y, z
HOT, SQUARE = "#ffd040", 4.0


@dataclass
class Host:
    """What the tool asks of the window: the open object, a step to apply, a preview to show, a line to say."""

    obj: Callable[[], object | None]
    apply: Callable[..., None]  # (role, *edits)
    preview: Callable[[str], None]  # the bytes of a file the view reads changed
    say: Callable[[str], None]


def _polyline_distance(p: QPointF, points: list[QPointF | None]) -> float:
    sides = [(a, b) for a, b in zip(points, points[1:], strict=False) if a is not None and b is not None]
    return min(
        (picking.on_segment((p.x(), p.y()), (a.x(), a.y()), (b.x(), b.y()))[2] for a, b in sides), default=math.inf
    )


class HandleTool(QObject):
    drawing_changed = Signal(bool)  # a footprint is being drawn / the drawing ended

    def __init__(self, view, overlay, host: Host) -> None:
        super().__init__(view)
        self.view, self.overlay, self.host = view, overlay, host
        self.mode = "off"
        self.target: handles.PartTarget | None = None
        self.area: areaedit.Area | None = None
        self.vertex: int | None = None
        self.hover: str | None = None
        self.drawing, self.draft = False, []
        self.drag: PartDrag | VertexDrag | None = None
        self._remove: int | None = None
        view.installEventFilter(self)
        overlay.extras.append(self.paint)

    # what is selected
    def select(self, row) -> None:
        """The row now selected in the object tree (None for none): a part or a polygon to grab."""
        obj = self.host.obj()
        self.target, self.area, self.vertex = None, None, None
        doc = obj.doc(row.role) if obj is not None and row is not None and row.kind == "part" else None
        if doc is not None and doc.exact:
            if row.role == "ifo":
                self.area = areaedit.area_at(doc, row.path, obj.game in OLD_GAMES)
            elif row.role == "cfg":
                self.target = handles.part_target(doc, row.role, row.path)
        self.overlay.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in MODES else "off"
        self.overlay.update()

    def draw_footprint(self, on: bool) -> None:
        self.drawing, self.draft = on, []
        self.drawing_changed.emit(on)
        if on:
            self.host.say(tr("click the ground to draw the footprint; double-click or Enter finishes, Esc cancels"))
        self.overlay.update()

    # geometry
    def _camera(self):
        return getattr(self.view, "camera", None)

    def _size(self) -> tuple[int, int]:
        return self.view.width(), self.view.height()

    def _ray(self, pos: QPointF):
        return handles.model_ray(self._camera(), pos.x(), pos.y(), *self._size())

    def _screen(self, pos: QPointF, pivot) -> float:
        """The distance in pixels from the pivot's place on the screen to ``pos``."""
        centre = project([tuple(pivot)], self._camera(), self._size())[0]
        return max(1.0, math.hypot(pos.x() - centre.x(), pos.y() - centre.y())) if centre is not None else 1.0

    def _ground(self, pos: QPointF, free: bool) -> tuple[float, float] | None:
        hit = picking.ray_plane(self._ray(pos), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        if hit is None:
            return None
        x, z = float(hit[0]), float(hit[2])
        return (x, z) if free else (areaedit.snap(x), areaedit.snap(z))

    def _gizmo(self) -> dict[str, list[QPointF | None]]:
        """The handles of the part's gizmo as screen polylines by name: ``axis0..2`` and ``plane`` (move), ``ring0..2``
        (rotate), ``scale``."""
        pivot, camera, size = self.target.pivot, self._camera(), self._size()
        eye = camera.eye()
        reach = REACH * float(np.linalg.norm(np.array([-eye[0], eye[1], eye[2]]) - pivot))

        def line(points) -> list[QPointF | None]:
            return project([tuple(p) for p in points], camera, size)

        out: dict[str, list[QPointF | None]] = {}
        if self.mode == "move":
            out |= {f"axis{k}": line([pivot, pivot + handles.AXES[k] * reach]) for k in range(3)}
            lo, hi = 0.25 * reach, 0.55 * reach
            out["plane"] = line(
                [pivot + np.array([a, 0, b]) for a, b in ((lo, lo), (hi, lo), (hi, hi), (lo, hi), (lo, lo))]
            )
        elif self.mode == "rotate":
            angles = np.linspace(0, 2 * math.pi, RING_POINTS + 1)
            for k in range(3):
                u, v = handles.AXES[(k + 1) % 3], handles.AXES[(k + 2) % 3]
                out[f"ring{k}"] = line([pivot + reach * (math.cos(a) * u + math.sin(a) * v) for a in angles])
        elif self.mode == "scale":
            centre = line([pivot])[0]
            if centre is not None:
                out["scale"] = [
                    centre + QPointF(math.cos(a), math.sin(a)) * SCALE_RING
                    for a in np.linspace(0, 2 * math.pi, RING_POINTS + 1)
                ]
        return out

    def _handle_at(self, pos: QPointF) -> str | None:
        if self.target is None or self.mode == "off" or self._camera() is None:
            return None
        best, found = GRAB, None
        for key, points in self._gizmo().items():
            inside = (
                key == "plane" and None not in points and QPolygonF(points).containsPoint(pos, Qt.FillRule.OddEvenFill)
            )
            near = 0.0 if inside else _polyline_distance(pos, points)
            if near < best:
                best, found = near, key
        return found

    def _vertex_at(self, pos: QPointF) -> int | None:
        if self.area is None or self._camera() is None:
            return None
        pts = project([(x, 0.0, z) for x, z in self.area.points()], self._camera(), self._size())
        near = min(
            ((math.hypot(p.x() - pos.x(), p.y() - pos.y()), k) for k, p in enumerate(pts) if p is not None),
            default=(math.inf, None),
        )
        return near[1] if near[0] <= GRAB else None

    # the mouse
    def eventFilter(self, obj, event) -> bool:
        if obj is not self.view or self._camera() is None:
            return False
        kind = event.type()
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            return self._press(event, kind == QEvent.Type.MouseButtonDblClick)
        if kind == QEvent.Type.MouseMove:
            return self._move(event)
        if kind == QEvent.Type.MouseButtonRelease:
            return self._release(event)
        if kind == QEvent.Type.KeyPress:
            return self._key(event)
        return False

    def _press(self, event, double: bool) -> bool:
        pos, button = event.position(), event.button()
        free = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if self.drawing:
            return button == Qt.MouseButton.LeftButton and self._draw(pos, double, free)
        if self.area is not None:
            return self._press_area(pos, button, double, free)
        name = self._handle_at(pos) if button == Qt.MouseButton.LeftButton and not double else None
        if name is None:
            return False
        self.drag = PartDrag(self.target, name, self._ray(pos), self._screen(pos, self.target.pivot))
        self.overlay.update()
        return True

    def _press_area(self, pos: QPointF, button, double: bool, free: bool) -> bool:
        hit = self._vertex_at(pos)
        if button == Qt.MouseButton.RightButton and hit is not None:
            self._remove = hit
            return True
        if button != Qt.MouseButton.LeftButton:
            return False
        if double:
            return self._insert_at(pos, free)
        if hit is None:
            return False
        self.vertex, self.drag = hit, VertexDrag(self.area, hit)
        return True

    def _insert_at(self, pos: QPointF, free: bool) -> bool:
        """A double-click on a side puts a point there."""
        ground = self._ground(pos, True)
        points = self.area.points()
        shown = project([(x, 0.0, z) for x, z in points], self._camera(), self._size())
        if ground is None or _polyline_distance(pos, [*shown, shown[0]]) > GRAB:
            return False
        index, x, z, _distance = areaedit.nearest_side(points, *ground)
        x, z = (x, z) if free else (areaedit.snap(x), areaedit.snap(z))
        self.host.apply("ifo", areaedit.insert_point(self.area, index, x, z))
        return True

    def _draw(self, pos: QPointF, double: bool, free: bool) -> bool:
        if double:
            self._finish_footprint()
            return True
        ground = self._ground(pos, free)
        if ground is not None:
            if len(self.draft) >= areaedit.MIN_POINTS and ground == self.draft[0]:
                self._finish_footprint()
            else:
                self.draft.append(ground)
            self.overlay.update()
        return True

    def _finish_footprint(self) -> None:
        obj, points = self.host.obj(), self.draft
        self.drawing, self.draft = False, []
        self.drawing_changed.emit(False)
        doc = obj.doc("ifo") if obj is not None else None
        if obj is not None and doc is None:
            self.host.say(tr("this object has no info file to draw a footprint in"))
        elif doc is not None and self.area is None and areaedit.has_footprint(doc):
            self.host.say(tr("the object has a footprint: select it to draw it again"))
        elif doc is not None and len(points) >= areaedit.MIN_POINTS:
            if self.area is not None:
                self.host.apply("ifo", *areaedit.replace_points(self.area, points))
            else:
                edit = areaedit.new_footprint(doc, points, obj.game in OLD_GAMES)
                self.host.apply("ifo", *([edit] if edit is not None else []))
        self.overlay.update()

    def _move(self, event) -> bool:
        pos, mods = event.position(), event.modifiers()
        if self.drag is None:
            hover = self._handle_at(pos)
            if hover != self.hover:
                self.hover = hover
                self.overlay.update()
            return False
        if isinstance(self.drag, VertexDrag):
            self.drag.move(self._ground(pos, bool(mods & Qt.KeyboardModifier.AltModifier)))
        else:
            snap = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            self.drag.move(self._ray(pos), self._screen(pos, self.drag.pivot), snap)
        self.host.preview(self.drag.role)
        self.overlay.update()
        return True

    def _release(self, event) -> bool:
        if self._remove is not None and event.button() == Qt.MouseButton.RightButton:
            edit, self._remove = areaedit.remove_point(self.area, self._remove), None
            if edit is None:
                self.host.say(tr("a polygon keeps three points"))
            else:
                self.host.apply("ifo", edit)
            return True
        drag, self.drag = self.drag, None
        if drag is None:
            return False
        edits = drag.end()
        if edits:
            self.host.apply(drag.role, *edits)
        else:
            self.host.preview(drag.role)
        self.overlay.update()
        return True

    def _key(self, event) -> bool:
        key = event.key()
        if self.drawing and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish_footprint()
            return True
        if self.drawing and key == Qt.Key.Key_Escape:
            self.draw_footprint(False)
            return True
        return False

    # drawing
    def paint(self, painter: QPainter, camera, size: tuple[int, int]) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.drawing:
            self._paint_draft(painter, camera, size)
        elif self.area is not None:
            self._paint_vertices(painter, camera, size)
        elif self.target is not None and self.mode != "off":
            self._paint_gizmo(painter)

    def _paint_gizmo(self, painter: QPainter) -> None:
        active = self.drag.name if isinstance(self.drag, PartDrag) else self.hover
        for key, points in self._gizmo().items():
            color = (
                HOT
                if key == active
                else "#d0d0d0"
                if key == "scale"
                else COLORS[int(key[-1]) if key[-1].isdigit() else 1]
            )
            painter.setPen(QPen(QColor(color), 3.0 if key == active else 2.0))
            if None not in points:
                painter.drawPolyline(QPolygonF(points))

    def _paint_vertices(self, painter: QPainter, camera, size: tuple[int, int]) -> None:
        held = self.drag.index if isinstance(self.drag, VertexDrag) else self.vertex
        for k, p in enumerate(project([(x, 0.0, z) for x, z in self.area.points()], camera, size)):
            if p is not None:
                painter.setPen(QPen(QColor(HOT if k == held else "#ffffff"), 1.5))
                painter.setBrush(QColor(HOT if k == held else "#e05050"))
                painter.drawRect(p.x() - SQUARE, p.y() - SQUARE, 2 * SQUARE, 2 * SQUARE)

    def _paint_draft(self, painter: QPainter, camera, size: tuple[int, int]) -> None:
        pts = [p for p in project([(x, 0.0, z) for x, z in self.draft], camera, size) if p is not None]
        painter.setPen(QPen(QColor(HOT), 2.0))
        painter.setBrush(QColor(HOT))
        if len(pts) > 1:
            painter.drawPolyline(QPolygonF(pts))
        for p in pts:
            painter.drawRect(p.x() - 3, p.y() - 3, 6, 6)
