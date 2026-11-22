"""The handles over the 3D view: an event filter on the view that takes a press near a handle (a part's gizmo, a point
of an info-file polygon, a footprint being drawn) before the view's camera sees it, and paints what it grabs.

``CHandleTool(view, overlay, host)`` is the entry; the window tells it the selected row (``select``), the mode
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
from .handledrag import CPartDrag, CVertexDrag
from .overlay import project

MODES = ("off", "move", "rotate", "scale")
GRAB = 9.0  # pixels: a press this near a handle is the tool's
RING_POINTS = 48
REACH = 0.16  # the gizmo's size: this part of the distance from the eye to the pivot
SCALE_RING = 70.0  # pixels: the scale handle's circle about the pivot
OLD_GAMES = installs.keys(lambda game: game.family == installs.OLD)  # their info files are in fixed point
COLORS = ("#e05050", "#6cc04a", "#4f9fd9")  # x, y, z
HOT, SQUARE = "#ffd040", 4.0


@dataclass
class SHost:
    """What the tool asks of the window: the open object, a step to apply, a preview to show, a line to say."""

    object_doc: Callable[[], object | None]
    apply: Callable[..., None]  # (role, *edits)
    preview: Callable[[str], None]  # the bytes of a file the view reads changed
    say: Callable[[str], None]


def _polyline_distance(point: QPointF, points: list[QPointF | None]) -> float:
    sides = [
        (start, end) for start, end in zip(points, points[1:], strict=False) if start is not None and end is not None
    ]
    return min(
        (
            picking.on_segment((point.x(), point.y()), (start.x(), start.y()), (end.x(), end.y()))[2]
            for start, end in sides
        ),
        default=math.inf,
    )


class CHandleTool(QObject):
    drawing_changed = Signal(bool)  # a footprint is being drawn / the drawing ended

    def __init__(self, view, overlay, host: SHost) -> None:
        super().__init__(view)
        self.m_view, self.m_overlay, self.m_host = view, overlay, host
        self.m_mode = "off"
        self.m_target: handles.SPartTarget | None = None
        self.m_area: areaedit.SArea | None = None
        self.m_vertex: int | None = None
        self.m_hover: str | None = None
        self.m_drawing, self.m_draft = False, []
        self.m_drag: CPartDrag | CVertexDrag | None = None
        self._m_remove: int | None = None
        view.installEventFilter(self)
        overlay.m_extras.append(self.paint)

    # what is selected
    def select(self, row) -> None:
        """The row now selected in the object tree (None for none): a part or a polygon to grab."""
        object_doc = self.m_host.object_doc()
        self.m_target, self.m_area, self.m_vertex = None, None, None
        document = (
            object_doc.document(row.role) if object_doc is not None and row is not None and row.kind == "part" else None
        )
        if document is not None and document.exact:
            if row.role == "ifo":
                self.m_area = areaedit.area_at(document, row.path, object_doc.m_game in OLD_GAMES)
            elif row.role == "cfg":
                self.m_target = handles.part_target(document, row.role, row.path)
        self.m_overlay.update()

    def set_mode(self, mode: str) -> None:
        self.m_mode = mode if mode in MODES else "off"
        self.m_overlay.update()

    def draw_footprint(self, is_drawn: bool) -> None:
        self.m_drawing, self.m_draft = is_drawn, []
        self.drawing_changed.emit(is_drawn)
        if is_drawn:
            self.m_host.say(tr("common.click_ground_draw_footprint_double"))
        self.m_overlay.update()

    # geometry
    def _camera(self):
        return getattr(self.m_view, "m_camera", None)

    def _size(self) -> tuple[int, int]:
        return self.m_view.width(), self.m_view.height()

    def _ray(self, position: QPointF):
        return handles.model_ray(self._camera(), position.x(), position.y(), *self._size())

    def _screen(self, position: QPointF, pivot) -> float:
        """The distance in pixels from the pivot's place on the screen to ``position``."""
        centre = project([tuple(pivot)], self._camera(), self._size())[0]
        return max(1.0, math.hypot(position.x() - centre.x(), position.y() - centre.y())) if centre is not None else 1.0

    def _ground(self, position: QPointF, is_free: bool) -> tuple[float, float] | None:
        hit = picking.ray_plane(self._ray(position), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        if hit is None:
            return None
        x, z = float(hit[0]), float(hit[2])
        return (x, z) if is_free else (areaedit.snap(x), areaedit.snap(z))

    def _gizmo(self) -> dict[str, list[QPointF | None]]:
        """The handles of the part's gizmo as screen polylines by name: ``axis0..2`` and ``plane`` (move), ``ring0..2``
        (rotate), ``scale``."""
        pivot, camera, size_px = self.m_target.pivot, self._camera(), self._size()
        eye = camera.eye()
        reach = REACH * float(np.linalg.norm(np.array([-eye[0], eye[1], eye[2]]) - pivot))

        def line(points) -> list[QPointF | None]:
            return project([tuple(point) for point in points], camera, size_px)

        out: dict[str, list[QPointF | None]] = {}
        if self.m_mode == "move":
            out |= {f"axis{k}": line([pivot, pivot + handles.AXES[k] * reach]) for k in range(3)}
            inner, outer = 0.25 * reach, 0.55 * reach
            out["plane"] = line(
                [
                    pivot + np.array([corner_x, 0, corner_z])
                    for corner_x, corner_z in (
                        (inner, inner),
                        (outer, inner),
                        (outer, outer),
                        (inner, outer),
                        (inner, inner),
                    )
                ]
            )
        elif self.m_mode == "rotate":
            angles = np.linspace(0, 2 * math.pi, RING_POINTS + 1)
            for k in range(3):
                first_axis, second_axis = handles.AXES[(k + 1) % 3], handles.AXES[(k + 2) % 3]
                out[f"ring{k}"] = line(
                    [
                        pivot + reach * (math.cos(corner_x) * first_axis + math.sin(corner_x) * second_axis)
                        for corner_x in angles
                    ]
                )
        elif self.m_mode == "scale":
            centre = line([pivot])[0]
            if centre is not None:
                out["scale"] = [
                    centre + QPointF(math.cos(corner_x), math.sin(corner_x)) * SCALE_RING
                    for corner_x in np.linspace(0, 2 * math.pi, RING_POINTS + 1)
                ]
        return out

    def _handle_at(self, position: QPointF) -> str | None:
        if self.m_target is None or self.m_mode == "off" or self._camera() is None:
            return None
        best, found = GRAB, None
        for key, points in self._gizmo().items():
            inside = (
                key == "plane"
                and None not in points
                and QPolygonF(points).containsPoint(position, Qt.FillRule.OddEvenFill)
            )
            near = 0.0 if inside else _polyline_distance(position, points)
            if near < best:
                best, found = near, key
        return found

    def _vertex_at(self, position: QPointF) -> int | None:
        if self.m_area is None or self._camera() is None:
            return None
        pts = project([(x, 0.0, z) for x, z in self.m_area.points()], self._camera(), self._size())
        near = min(
            (
                (math.hypot(point.x() - position.x(), point.y() - position.y()), k)
                for k, point in enumerate(pts)
                if point is not None
            ),
            default=(math.inf, None),
        )
        return near[1] if near[0] <= GRAB else None

    # the mouse
    def eventFilter(self, object_doc, event) -> bool:
        if object_doc is not self.m_view or self._camera() is None:
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

    def _press(self, event, is_double: bool) -> bool:
        position, button = event.position(), event.button()
        is_free = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if self.m_drawing:
            return button == Qt.MouseButton.LeftButton and self._draw(position, is_double, is_free)
        if self.m_area is not None:
            return self._press_area(position, button, is_double, is_free)
        name = self._handle_at(position) if button == Qt.MouseButton.LeftButton and not is_double else None
        if name is None:
            return False
        self.m_drag = CPartDrag(self.m_target, name, self._ray(position), self._screen(position, self.m_target.pivot))
        self.m_overlay.update()
        return True

    def _press_area(self, position: QPointF, button, is_double: bool, is_free: bool) -> bool:
        hit = self._vertex_at(position)
        if button == Qt.MouseButton.RightButton and hit is not None:
            self._m_remove = hit
            return True
        if button != Qt.MouseButton.LeftButton:
            return False
        if is_double:
            return self._insert_at(position, is_free)
        if hit is None:
            return False
        self.m_vertex, self.m_drag = hit, CVertexDrag(self.m_area, hit)
        return True

    def _insert_at(self, position: QPointF, is_free: bool) -> bool:
        """A double-click on a side puts a point there."""
        ground = self._ground(position, True)
        points = self.m_area.points()
        shown = project([(x, 0.0, z) for x, z in points], self._camera(), self._size())
        if ground is None or _polyline_distance(position, [*shown, shown[0]]) > GRAB:
            return False
        index, x, z, _distance_to_side = areaedit.nearest_side(points, *ground)
        x, z = (x, z) if is_free else (areaedit.snap(x), areaedit.snap(z))
        self.m_host.apply("ifo", areaedit.insert_point(self.m_area, index, x, z))
        return True

    def _draw(self, position: QPointF, is_double: bool, is_free: bool) -> bool:
        if is_double:
            self._finish_footprint()
            return True
        ground = self._ground(position, is_free)
        if ground is not None:
            if len(self.m_draft) >= areaedit.MIN_POINTS and ground == self.m_draft[0]:
                self._finish_footprint()
            else:
                self.m_draft.append(ground)
            self.m_overlay.update()
        return True

    def _finish_footprint(self) -> None:
        object_doc, points = self.m_host.object_doc(), self.m_draft
        self.m_drawing, self.m_draft = False, []
        self.drawing_changed.emit(False)
        document = object_doc.document("ifo") if object_doc is not None else None
        if object_doc is not None and document is None:
            self.m_host.say(tr("common.object_no_info_file_draw"))
        elif document is not None and self.m_area is None and areaedit.has_footprint(document):
            self.m_host.say(tr("common.object_footprint_select_draw_again"))
        elif document is not None and len(points) >= areaedit.MIN_POINTS:
            if self.m_area is not None:
                self.m_host.apply("ifo", *areaedit.replace_points(self.m_area, points))
            else:
                edit = areaedit.new_footprint(document, points, object_doc.m_game in OLD_GAMES)
                self.m_host.apply("ifo", *([edit] if edit is not None else []))
        self.m_overlay.update()

    def _move(self, event) -> bool:
        position, mods = event.position(), event.modifiers()
        if self.m_drag is None:
            hover = self._handle_at(position)
            if hover != self.m_hover:
                self.m_hover = hover
                self.m_overlay.update()
            return False
        if isinstance(self.m_drag, CVertexDrag):
            self.m_drag.move(self._ground(position, bool(mods & Qt.KeyboardModifier.AltModifier)))
        else:
            snap = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            self.m_drag.move(self._ray(position), self._screen(position, self.m_drag.m_pivot), snap)
        self.m_host.preview(self.m_drag.m_role)
        self.m_overlay.update()
        return True

    def _release(self, event) -> bool:
        if self._m_remove is not None and event.button() == Qt.MouseButton.RightButton:
            edit, self._m_remove = areaedit.remove_point(self.m_area, self._m_remove), None
            if edit is None:
                self.m_host.say(tr("common.polygon_keeps_three_points"))
            else:
                self.m_host.apply("ifo", edit)
            return True
        drag, self.m_drag = self.m_drag, None
        if drag is None:
            return False
        edits = drag.end()
        if edits:
            self.m_host.apply(drag.m_role, *edits)
        else:
            self.m_host.preview(drag.m_role)
        self.m_overlay.update()
        return True

    def _key(self, event) -> bool:
        key = event.key()
        if self.m_drawing and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish_footprint()
            return True
        if self.m_drawing and key == Qt.Key.Key_Escape:
            self.draw_footprint(False)
            return True
        return False

    # drawing
    def paint(self, painter: QPainter, camera, size_px: tuple[int, int]) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.m_drawing:
            self._paint_draft(painter, camera, size_px)
        elif self.m_area is not None:
            self._paint_vertices(painter, camera, size_px)
        elif self.m_target is not None and self.m_mode != "off":
            self._paint_gizmo(painter)

    def _paint_gizmo(self, painter: QPainter) -> None:
        active = self.m_drag.m_name if isinstance(self.m_drag, CPartDrag) else self.m_hover
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

    def _paint_vertices(self, painter: QPainter, camera, size_px: tuple[int, int]) -> None:
        held = self.m_drag.m_index if isinstance(self.m_drag, CVertexDrag) else self.m_vertex
        for k, point in enumerate(project([(x, 0.0, z) for x, z in self.m_area.points()], camera, size_px)):
            if point is not None:
                painter.setPen(QPen(QColor(HOT if k == held else "#ffffff"), 1.5))
                painter.setBrush(QColor(HOT if k == held else "#e05050"))
                painter.drawRect(point.x() - SQUARE, point.y() - SQUARE, 2 * SQUARE, 2 * SQUARE)

    def _paint_draft(self, painter: QPainter, camera, size_px: tuple[int, int]) -> None:
        pts = [point for point in project([(x, 0.0, z) for x, z in self.m_draft], camera, size_px) if point is not None]
        painter.setPen(QPen(QColor(HOT), 2.0))
        painter.setBrush(QColor(HOT))
        if len(pts) > 1:
            painter.drawPolyline(QPolygonF(pts))
        for point in pts:
            painter.drawRect(point.x() - 3, point.y() - 3, 6, 6)
