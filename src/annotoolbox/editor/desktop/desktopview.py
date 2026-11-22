"""The PC application's 3D view: ``desktoprender`` in a Qt ``QOpenGLWidget`` (``CIslandView3D``; on QRhi
``desktoprhiview``), and the tools' input: the left
button orbits in every tool (a click selects, places or picks a river point), the middle button pans (the
right one too in Select), the brushes paint with the right button, the wheel zooms towards the cursor, the
arrow keys pan. Objects are moved, turned (or tilted) and scaled with a gizmo; every change goes through the
edit session (``editsession``), so it can be undone. Draws on demand (``docs/island-editor-view.md``, "GPU load").
"""

from __future__ import annotations

import math
import time
from collections import deque
from functools import partial

import numpy as np
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QLabel, QMenu

from ...infra import gpuinfo
from ...infra.i18n import number, tr
from ...render import picking
from ...render.camera import COrbitCamera, ground_hit
from ...render.viewcolors import palette
from ..editchange import SChange
from ..editlists import CHidden
from ..editsession import CEditSession, SEntry, group_key, label_of, type_of
from . import flycamera
from .desktopbrush import CBrushTools
from .desktopgizmo import CGizmo, CGizmoControls
from .desktopgrids import active_overlay, surf_overlay
from .desktopgroup import CGroupTools
from .desktopoverlays import COverlayDraw
from .desktopplace import CPlaceTool
from .desktoprender import CRenderer, SViewOptions

TOOLS = ("select", "height", "paint", "water", "forest", "place", "blocker", "spray", "active", "surf")
BRUSHES = ("height", "paint", "forest", "blocker", "spray", "active", "surf")
TOOL_KEYS = {
    Qt.Key.Key_S: "select", Qt.Key.Key_H: "height", Qt.Key.Key_P: "paint", Qt.Key.Key_L: "water",
    Qt.Key.Key_T: "forest", Qt.Key.Key_B: "blocker", Qt.Key.Key_A: "place", Qt.Key.Key_O: "spray",
    Qt.Key.Key_G: "active", Qt.Key.Key_U: "surf",
}  # fmt: skip
PAN_KEYS = {Qt.Key.Key_Left: (40, 0), Qt.Key.Key_Right: (-40, 0), Qt.Key.Key_Up: (0, 40), Qt.Key.Key_Down: (0, -40)}
CLICK = 4  # pixels a press may move and still be a click
IDLE_MS = 66  # 15 frames a second for flowing water, effects and models arriving


class CIslandControls(CGizmoControls, COverlayDraw):
    """The island in 3D and the tools' input, on either device (the widget classes below and in
    ``desktoprhiview``). One view lives as long as its window (a second 3D widget added to a shown window makes Qt
    recreate that window): ``load`` hands it an island's edit session and editor, ``unload`` takes it away again.
    The corners show the frame rate and the cursor's tile."""

    GPU_PICK = True  # a click picks from an id picture the device reads back; else by the objects' spheres

    def _setup(self) -> None:
        self.m_ctx = None
        self.m_session: CEditSession | None = None
        self.m_editor = None
        self.m_renderer: CRenderer | None = None
        self.m_camera = COrbitCamera((0, 0, 0), 300)
        self.m_camera_mode = "orbit"  # or "fly": kept from island to island
        self.m_scheme = "dark"  # the window's theme (set_scheme): the view's own colours follow it
        self.m_opts = SViewOptions(scheme=self.m_scheme)
        self.m_hidden = CHidden()
        self.m_tool = "select"
        self.m_allowed = set(TOOLS)  # the island's mode may leave tools out (desktopeditor.modes)
        self.m_brush = {"mode": "raise", "radius": 6, "strength": 0.5, "plant": True, "spacing": 2.0}
        self.m_brushes = CBrushTools(self)
        self.m_paint_material: int | None = None
        self.m_gizmo = CGizmo()
        self.m_selected: SEntry | None = None
        self.m_water_sel: dict | None = None
        self.m_pick: dict | None = None  # the library's pick, for Place
        self.m_place_yaw = 0.0
        self.m_checks: dict = {}
        self.m_cursor_hit = None
        self.m_group = CGroupTools(self)  # several selected, snapping, copy and paste, layers
        self.m_placer = CPlaceTool(self)  # a building on its grid, its footprint and the rules live (desktopplace)
        self._m_press: QPoint | None = None
        self._m_drag = None
        self._m_hot = None
        self.m_fov_deg, self.m_speed = 45.0, 1.0  # the status bar's FOV (degrees) and Speed, kept from island to island
        self.m_fps_label = QLabel(tr("islands.fps_n", n=0), self, objectName="hud")
        self.m_coord_label = QLabel("", self, objectName="hud")
        self.m_hint_label = QLabel(tr("islands.open_island_pick_one_islands"), self, objectName="hud")
        for label in (self.m_fps_label, self.m_coord_label, self.m_hint_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.m_coord_label.hide()
        self._m_frame_times: deque[float] = deque(maxlen=30)
        self._m_last_paint = 0.0
        self._m_painted = False
        self._m_hover: QPoint | None = None
        self._m_hover_done: QPoint | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._m_timer = QTimer(self, interval=IDLE_MS, timeout=self._tick)
        self._m_timer.start()
        self._m_hud_timer = QTimer(self, interval=500, timeout=self._hud)
        self._m_hud_timer.start()

    # loading
    def load(self, session: CEditSession, editor=None) -> None:
        self._release_renderer()
        self.m_session, self.m_editor = session, editor
        self.m_opts, self.m_hidden = SViewOptions(scheme=self.m_scheme), CHidden()
        self.m_tool, self.m_allowed = "select", set(TOOLS)
        self.m_selected = self.m_water_sel = self.m_pick = self.m_cursor_hit = self._m_drag = self._m_hot = (
            self._m_press
        ) = None
        self.m_paint_material, self.m_place_yaw, self.m_checks, self.m_gizmo = None, 0.0, {}, CGizmo()
        self.m_placer.reset()
        size_tiles = session.m_size_tiles
        self.m_camera = COrbitCamera(
            (size_tiles / 2, max(0.0, float(np.median(session.m_heights))), size_tiles / 2), size_tiles * 1.1
        )
        self.m_camera = flycamera.switch(self.m_camera, self.m_camera_mode)
        self.set_fov(self.m_fov_deg)
        self.set_speed(self.m_speed)
        game = bool(session.m_island.get("game")) or session.m_anno2070_editing
        if game:  # 117's rules are for 117's islands, built or vanilla
            self.m_opts.game_terrain = self.m_opts.native = False
        elif getattr(self, "m_backend", "gl") == "d3d12":  # a 117 island opens in the game's look (island-117-view.md
            # "Speed", part 7): its shaders over its near terrain
            self.m_opts.native = self.m_opts.game_terrain = True
        self.m_group.attach(session)
        self.m_hint_label.hide()
        self.update()

    def unload(self) -> None:
        """Back to the empty view (the sky), the island's resources freed."""
        self._release_renderer()
        self.m_session = self.m_editor = self.m_selected = self.m_water_sel = self.m_pick = self.m_cursor_hit = None
        self.m_checks, self._m_drag, self._m_hover, self._m_hot, self._m_press = {}, None, None, None, None
        self.m_group.detach()
        self.m_placer.reset()
        self.m_placer.m_panel = None  # the closed island's list (and through it the island) is let go
        self.m_coord_label.hide()
        self.m_hint_label.show()
        self._place_hud()
        self.update()

    def _release_renderer(self) -> None:
        if self.m_renderer is None:
            return
        self.makeCurrent()  # its GL objects are freed in this widget's context
        try:
            self.m_renderer.release()
        finally:
            self.m_renderer = None
            self.doneCurrent()

    def set_camera_mode(self, mode: str) -> None:
        """Orbit about a pivot or fly freely (``flycamera``); the camera stays where it looks."""
        self.m_camera_mode = mode
        self.m_camera = flycamera.switch(self.m_camera, mode)
        self.m_camera.m_fov_rad, self.m_camera.m_speed = math.radians(self.m_fov_deg), self.m_speed
        self.update()

    def set_fov(self, degrees: float) -> None:
        self.m_fov_deg = float(degrees)
        self.m_camera.m_fov_rad = math.radians(self.m_fov_deg)
        self.update()

    def set_scheme(self, scheme: str) -> None:
        """The window's theme changed: the sky, fog and overlays are drawn in its colours from the next frame."""
        self.m_scheme = self.m_opts.scheme = scheme
        self.update()

    def _rgba(self, key: str, alpha: float = 1.0) -> tuple:
        return (*palette(self.m_scheme)[key], alpha)

    def set_speed(self, speed: float) -> None:
        self.m_speed = float(speed)
        self.m_camera.m_speed = self.m_speed

    def closeEvent(self, event) -> None:
        self._release_renderer()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_hud()

    def _place_hud(self) -> None:
        hint = self.m_hint_label  # wraps on a narrow view (a small window, a high scale) instead of being cut off
        hint.setWordWrap(True)
        hint.setFixedWidth(min(hint.fontMetrics().horizontalAdvance(hint.text()) + 16, max(80, self.width() - 32)))
        for label in (self.m_fps_label, self.m_coord_label, hint):
            label.adjustSize()
        self.m_fps_label.move(8, 8)
        self.m_coord_label.move(8, self.height() - self.m_coord_label.height() - 8)
        self.m_hint_label.move(
            (self.width() - self.m_hint_label.width()) // 2, (self.height() - self.m_hint_label.height()) // 2
        )

    def _hud(self) -> None:
        if failed := getattr(self, "m_failed", ""):  # a frame that raised draws nothing: say why, not a blank view
            self.m_fps_label.setText(tr("islands.view_cannot_draw", error=failed))  # also after a single frame
            self.m_fps_label.adjustSize()
            return
        if self._m_painted and self._m_frame_times:
            fps = f"{len(self._m_frame_times) / max(1e-6, sum(self._m_frame_times)):.0f}"
            memory = gpuinfo.video_memory()  # the card's own and the system memory it maps for this process
            used = (memory.local + memory.shared) / 2**30 if memory is not None else 0.0
            text = (
                tr("islands.fps_gb_graphics_memory", n=fps, gb=number(used, 1)) if used else tr("islands.fps_n", n=fps)
            )
            self.m_fps_label.setText(text)
            self.m_fps_label.adjustSize()
            self._m_painted = False

    def _tick(self) -> None:
        renderer = self.m_renderer
        if renderer is not None and (renderer.changed() or renderer.m_animated):
            self.update()
        if self._m_hover is not None and self._m_hover != self._m_hover_done and self.m_session is not None:
            self._m_hover_done = self._m_hover
            hit = self.ground_at(self._m_hover)
            if hit is None:
                self.m_coord_label.hide()
            else:
                x, y, z = self.from_view(hit)
                self.m_coord_label.setText(tr("islands.x_z_height_y", x=f"{x:.0f}", z=f"{z:.0f}", y=number(y, 2)))
                self.m_coord_label.adjustSize()
                self.m_coord_label.show()
                self._place_hud()

    def _frame(self, ctx, framebuffer, width_px: int, height_px: int) -> None:
        """One frame of the island with the device ``ctx`` into ``fbo`` (``w`` x ``h`` pixels)."""
        now = time.perf_counter()
        if 0 < now - self._m_last_paint < 0.25:  # a longer gap is a rest, not a slow frame
            self._m_frame_times.append(now - self._m_last_paint)
        self._m_last_paint, self._m_painted = now, True
        pixel_ratio = self.devicePixelRatioF()
        if self.m_session is None:
            framebuffer.use()
            framebuffer.clear(*palette(self.m_scheme)["clear"], 1.0)
            return
        if self.m_renderer is None:
            self.m_renderer = CRenderer(ctx, self.m_session, self.m_editor)
            self.apply_hidden()
        self.m_renderer.render(
            framebuffer, (width_px, height_px), self.m_camera, self.m_opts, min(1.0, 1.5 / pixel_ratio), self.overlays()
        )
        for message in self.m_renderer.m_messages:
            self.status.emit(message)
        self.m_renderer.m_messages.clear()

    # coordinates
    def to_view(self, point) -> np.ndarray:
        return np.array([self.m_session.m_size_tiles - point[0], point[1], point[2]], float)

    def from_view(self, view_point) -> list[float]:
        return [float(self.m_session.m_size_tiles - view_point[0]), float(view_point[1]), float(view_point[2])]

    def ray(self, cursor) -> tuple[np.ndarray, np.ndarray]:
        return self.m_camera.ray(cursor.x(), cursor.y(), max(1, self.width()), max(1, self.height()))

    def ground_at(self, cursor):
        """The terrain point under the cursor (view space), or None."""
        origin, direction = self.ray(cursor)
        return ground_hit(self.m_session.m_heights, self.m_session.m_step_tiles, origin, direction)

    def project(self, point) -> tuple[float, float, float]:
        width_px, height_px = max(1, self.width()), max(1, self.height())
        return picking.to_screen(self.m_camera.matrix(width_px / height_px), (width_px, height_px), [point])[0] or (
            -1e9,
            -1e9,
            -1.0,
        )

    # tools
    def set_tool(self, name: str) -> None:
        if name not in TOOLS or name not in self.m_allowed:
            return
        self.m_tool = name
        if name != "select":
            self.select(None)
        if name != "water":
            self.m_water_sel = None
        self.m_gizmo.m_axes_only = None
        self.toolChanged.emit(name)
        if name in ("place", "spray") and not self.m_pick:
            self.status.emit(tr("islands.pick_model_library"))
        self.update()

    def set_pick(self, pick: dict | None) -> None:
        self.m_pick = pick
        self.m_placer.picked(pick)
        if self.m_renderer is not None:
            self.m_renderer.set_ghost(None)
        if pick:
            self.set_tool("place")

    def select(self, entry: SEntry | None) -> None:
        self.m_selected = entry if (entry is None or entry.alive) else None
        self.m_group.sync(self.m_selected)
        self.selectionChanged.emit(self.m_selected)
        self.update()

    def select_water(self, handle: dict | None) -> None:
        self.m_water_sel = handle
        self.m_gizmo.m_mode = "translate"
        self.m_gizmo.m_axes_only = (1,) if handle and "lake" in handle else None
        self.waterChanged.emit(handle)
        self.update()

    def apply_hidden(self) -> None:
        if self.m_session is None:
            return
        if self.m_hidden.count():
            changed = self.m_hidden.apply(self.m_session.m_entries)
        else:  # nothing hidden: only those still hidden change (asking each entry took a second on a continent)
            changed = {entry for entry in self.m_session.m_entries if entry.hidden}
            for entry in changed:
                entry.hidden = False
        if changed and self.m_renderer is not None:
            self.m_renderer.on_change(SChange(entries=changed))
        if self.m_selected is not None and self.m_selected.hidden:
            self.select(None)
        self.update()

    def focus_on(self, entries) -> None:
        points = [self.to_view(entry.position) for entry in entries if entry.shown]
        if not points:
            return
        points = np.asarray(points)
        radius_tiles = self._radius_tiles(entries[0])
        low_corner, high_corner = points.min(axis=0) - radius_tiles, points.max(axis=0) + radius_tiles
        self.m_camera.m_target = ((low_corner + high_corner) / 2).astype("f4")
        self.m_camera.m_distance_m = max(8.0, float(np.linalg.norm(high_corner - low_corner)) * 1.2)
        self.update()

    def go_to(self, x: float, z: float, distance_tiles: float = 25.0, turn: float = 0.0) -> None:
        """Look at tile (x, z) from ``distance``, turned ``turn`` degrees (the footer's "go to")."""
        self.m_camera.look(self.to_view([x, self.m_session.height_at(x, z), z]), distance_tiles, turn)
        self.update()

    def _radius_tiles(self, entry: SEntry) -> float:
        batch = self.m_renderer.batch_of(entry) if self.m_renderer is not None else None
        scale_value = entry.scale if not isinstance(entry.scale, list) else max(entry.scale)
        return (batch.m_radius_m if batch is not None else 4.0) * float(scale_value or 1.0)

    # overlays: brush ring, gizmo, water network, checks, selection box, place ghost
    def overlays(self) -> list:
        out = []
        session = self.m_session
        if self.m_tool in BRUSHES and self.m_cursor_hit is not None:
            centre, radius_tiles = self.m_cursor_hit, self.m_brush["radius"]
            angles = np.linspace(0, 2 * math.pi, 65)
            ring = []
            for start_rad, end_rad in zip(angles[:-1], angles[1:], strict=True):
                for angle_rad in (start_rad, end_rad):
                    x, z = (
                        centre[0] + radius_tiles * math.cos(angle_rad),
                        centre[2] + radius_tiles * math.sin(angle_rad),
                    )
                    ring.append((x, session.height_at(session.m_size_tiles - x, z) + 0.2, z, *self._rgba("brush")))
            out.append(("lines", np.asarray(ring, "f4")))
        if self.m_selected is not None and self.m_selected.shown and self.m_tool == "select":
            out += self._selection_box(self.m_selected) + self._footprint_under(self.m_selected)
            for other in self.m_group.others():
                out += self._selection_box(other) + self._footprint_under(other)
        if self.m_tool == "water" and session.m_water_net:
            out += self._water_overlay()
        if self.m_tool == "blocker" and session.m_path.m_blocked is not None:
            out.append(("points", self._blocker_points()))
        if self.m_tool == "active":
            out += active_overlay(self)
        if self.m_tool == "surf":
            out += surf_overlay(self)
        gizmo_handles = self._gizmo_handles()
        if gizmo_handles:
            active = self._rgba("select")
            lines, points = (
                self.m_gizmo.lines(gizmo_handles, self._m_hot, active),
                self.m_gizmo.points(gizmo_handles, self._m_hot, active),
            )
            out.append(("lines", np.asarray(lines, "f4").reshape(-1, 7)))
            out.append(("points", np.asarray(points, "f4").reshape(-1, 7)))
        out += self._check_overlays() + self.m_placer.overlay()
        return out

    # input
    def mousePressEvent(self, event) -> None:
        if self.m_session is None:
            return
        cursor = event.position().toPoint()
        self._m_press = cursor
        button = event.button()
        if button == Qt.MouseButton.LeftButton:
            handle = self._hit_handle(cursor)
            if handle is not None:
                self._start_gizmo(handle, cursor)
                return
            self._m_drag = ("orbit", cursor)
        elif button == Qt.MouseButton.MiddleButton:
            self._m_drag = ("pan", cursor)
        elif button == Qt.MouseButton.RightButton:
            if self.m_tool in BRUSHES and not self.m_session.m_readonly:
                hit = self.ground_at(cursor)
                if hit is not None:
                    self._start_brush(hit)
                    return
            self._m_drag = ("pan", cursor)

    def mouseMoveEvent(self, event) -> None:
        if self.m_session is None:
            return
        cursor = event.position().toPoint()
        self._m_hover = cursor
        drag = self._m_drag
        if drag is None:
            if self.m_tool in BRUSHES or self.m_tool == "place":
                self.m_cursor_hit = self.ground_at(cursor)
                if self.m_tool == "place" and self.m_renderer is not None:
                    self._ghost()
                self.update()
            elif self._gizmo_target() is not None:
                hot = self._hit_handle(cursor)
                if (hot is None) != (self._m_hot is None) or (
                    hot and self._m_hot and hot["axis"] != self._m_hot["axis"]
                ):
                    self._m_hot = hot
                    self.update()
            return
        if drag[0] in ("orbit", "pan"):
            delta = cursor - drag[1]
            self._m_drag = (drag[0], cursor)
            (self.m_camera.turn if drag[0] == "orbit" else self.m_camera.pan)(delta.x(), delta.y())
        elif drag[0] == "brush":
            hit = self.ground_at(cursor)
            self.m_cursor_hit = hit
            if hit is not None:
                self._brush_to(hit)
        elif drag[0] == "gizmo":
            self._drag_gizmo(cursor)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self.m_session is None:
            return
        cursor = event.position().toPoint()
        click = self._m_press is not None and (cursor - self._m_press).manhattanLength() <= CLICK
        drag, self._m_drag = self._m_drag, None
        if drag is not None and drag[0] == "brush":
            self._end_brush()
            return
        if drag is not None and drag[0] == "gizmo":
            self._end_gizmo(drag)
            return
        if not click:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if self.m_tool == "place":
                self.m_placer.place(self.ground_at(cursor))
            elif self.m_tool == "water":
                self._pick_water(cursor)
            elif self.m_tool == "select":
                self.m_group.click(self.pick_entry(cursor), bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        elif event.button() == Qt.MouseButton.RightButton and self.m_tool == "select":
            self.hide_menu(event.globalPosition().toPoint(), self.pick_entry(cursor))
        self.update()

    def leaveEvent(self, event) -> None:
        self._m_hover = self._m_hover_done = None
        self.m_coord_label.hide()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        if self.m_session is None:
            return
        steps = event.angleDelta().y() / 120
        hit = self.ground_at(event.position().toPoint()) if steps > 0 else None
        self.m_camera.zoom(steps, hit)
        self.update()

    def keyPressEvent(self, event) -> None:
        if self.m_session is None:
            return super().keyPressEvent(event)
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and event.key() == Qt.Key.Key_D:
            self.duplicate_selection()
        elif ctrl or (action := self._key_table().get(event.key())) is None:
            return super().keyPressEvent(event)  # the window's actions: save, undo, redo
        else:
            action()
        self.selectionChanged.emit(self.m_selected)
        self.update()

    def _key_table(self) -> dict:
        """Key -> what it does in the view now (a key the table lacks is the window's)."""
        keys = Qt.Key
        table = {keys.Key_Delete: self.delete_selection, keys.Key_Backspace: self.delete_selection}
        table |= {k: partial(self.m_camera.pan, *step) for k, step in PAN_KEYS.items()}
        table |= {k: partial(self.set_tool, tool) for k, tool in TOOL_KEYS.items()}
        table |= {keys.Key_Escape: self._deselect, keys.Key_W: partial(self._gizmo_key, "translate")}
        table |= {keys.Key_E: self._turn_key, keys.Key_R: partial(self._gizmo_key, "scale")}
        if self.m_camera_mode == "fly":
            table |= {
                keys.Key_PageUp: partial(self.m_camera.lift, 1),
                keys.Key_PageDown: partial(self.m_camera.lift, -1),
            }
        if self.m_tool == "place":
            table |= {
                keys.Key_BracketLeft: partial(self.m_placer.turn, -1),
                keys.Key_BracketRight: partial(self.m_placer.turn, 1),
            }
        if self.m_selected is not None:
            table[keys.Key_F] = partial(self.focus_on, [self.m_selected])
        return table

    def _deselect(self) -> None:
        self.select(None)
        self.select_water(None)

    def _gizmo_key(self, mode: str) -> None:
        self.m_gizmo.m_mode = mode

    def _turn_key(self) -> None:
        """E: the turn handle, and again the tilt handle (about the model's own axes)."""
        if self.m_gizmo.m_mode == "rotate":
            self.m_gizmo.m_tilt = not self.m_gizmo.m_tilt
        self.m_gizmo.m_mode = "rotate"
        self.status.emit(
            tr("islands.rotate_tilt_about_models_own") if self.m_gizmo.m_tilt else tr("islands.rotate_turn")
        )

    def river_selected(self) -> bool:
        return self.m_tool == "water" and bool(self.m_water_sel) and "river" in self.m_water_sel

    def delete_selection(self) -> None:
        """Del: the selected river point (water tool) or object."""
        if self.river_selected():
            if self.m_session.delete_river_point(self.m_water_sel["river"], self.m_water_sel["point"]):
                self.select_water(None)
        elif self.m_group.delete() or self.m_selected is not None:
            if self.m_selected is not None and self.m_selected.alive and self.m_selected.visible:
                self.m_session.delete(self.m_selected)
            self.select(None)

    def duplicate_selection(self) -> None:
        """Ctrl+D: a point after the selected river point (water tool), or a copy of the selected object."""
        if self.river_selected():
            i = self.m_session.insert_river_point(self.m_water_sel["river"], self.m_water_sel["point"])
            self.select_water({"river": self.m_water_sel["river"], "point": i})
        elif self.m_selected is not None and not self.m_group.duplicate():
            copy = self.m_session.duplicate(self.m_selected)
            if copy is not None:
                self.select(copy)

    # picking
    def gpu_pick(self, size_px: tuple[int, int], x: float, y: float) -> SEntry | None:
        """The entry the renderer's id picture shows at pixel (x, y), read at once (OpenGL)."""
        self.makeCurrent()
        try:
            return self.m_renderer.pick(self.m_camera, size_px, x, y, self.m_opts)
        finally:
            self.doneCurrent()

    def pick_entry(self, cursor) -> SEntry | None:
        if self.m_renderer is None:
            return None
        pixel_ratio = self.devicePixelRatioF()
        width_px, height_px = max(1, int(self.width() * pixel_ratio)), max(1, int(self.height() * pixel_ratio))
        found = (
            self.gpu_pick((width_px, height_px), cursor.x() * pixel_ratio, cursor.y() * pixel_ratio)
            if self.GPU_PICK
            else None
        )
        # banner leaves are points the shader spreads: a tree is also hit through a sphere round its crown
        origin, direction = self.ray(cursor)
        best = math.inf
        if found is not None:
            best = float(np.linalg.norm(self.to_view(found.position) - origin))
        for entry in self.m_session.m_entries:
            tree = entry.base.get("template") == "Tree"
            if (self.GPU_PICK and not tree) or not entry.shown or not self.m_opts.layers.get(entry.layer, True):
                continue
            radius_tiles = self._radius_tiles(entry) * 0.5
            crown = self.to_view(entry.position) + np.array([0, radius_tiles, 0])
            distance_along_ray = float(np.dot(crown - origin, direction))
            if (
                distance_along_ray > 0
                and np.linalg.norm(origin + distance_along_ray * direction - crown) < radius_tiles
                and distance_along_ray < best
            ):
                best, found = distance_along_ray, entry
        return found

    def _pick_water(self, cursor) -> None:
        best, best_distance_px = None, 14.0  # the nearest handle on screen within 14 px
        for handle, point in self.water_handles():
            x, y, z = self.project(point)
            distance_px = math.hypot(x - cursor.x(), y - cursor.y())
            if z < 1 and distance_px < best_distance_px:
                best, best_distance_px = handle, distance_px
        self.select_water(best)

    def hide_menu(self, global_position, entry: SEntry | None) -> None:
        menu, hidden = QMenu(self), self.m_hidden
        if entry is not None:
            menu.addAction(tr("islands.hide_object"), lambda: (hidden.m_entries.add(entry), self.apply_hidden()))
            group, type_name = group_key(entry.base), type_of(entry.base)
            label = label_of(entry.base)
            if group in hidden.m_groups:
                menu.addAction(
                    tr("islands.show_all_name", name=label),
                    lambda: (hidden.m_groups.discard(group), self.apply_hidden()),
                )
            else:
                menu.addAction(
                    tr("islands.hide_all_name", name=label), lambda: (hidden.m_groups.add(group), self.apply_hidden())
                )
            if type_name in hidden.m_types:
                menu.addAction(
                    tr("islands.show_category_name", name=type_name),
                    lambda: (hidden.m_types.discard(type_name), self.apply_hidden()),
                )
            else:
                menu.addAction(
                    tr("islands.hide_category_name", name=type_name),
                    lambda: (hidden.m_types.add(type_name), self.apply_hidden()),
                )
        if hidden.count():
            menu.addAction(
                tr("islands.show_everything_hidden", n=hidden.count()), lambda: (hidden.clear(), self.apply_hidden())
            )
        if not menu.isEmpty():
            menu.exec(global_position)

    # brushes
    def _start_brush(self, hit) -> None:
        if self.m_brushes.start(hit):
            self._m_drag = ("brush",)
            self.m_cursor_hit = hit

    def _brush_to(self, hit) -> None:
        self.m_brushes.to(hit)

    def _end_brush(self) -> None:
        self.m_brushes.end()

    def _ghost(self) -> None:
        self.m_placer.ghost()


def fly_to(view, entries: list) -> None:
    """A click in a list of objects: the Select tool, the first of ``entries`` selected and the camera on all of them."""
    if view.m_tool != "select":
        view.set_tool("select")
    view.select(entries[0])
    view.focus_on(entries)


def collect_dropped(ctx) -> None:
    """A moderngl context keeps the GPU objects nothing holds any more for ``free_dropped``. Left as it comes it
    frees nothing: every closed island's buffers and maps, and each replaced batch's, stayed on the GPU for as long
    as the window lived. (QRhi's objects free themselves.)"""
    ctx.gc_mode = "context_gc"


def free_dropped(ctx) -> int:
    """Free what ``ctx`` (current) collected since the last call; how many GPU objects. moderngl's own ``gc()``
    stops at an object that was also released by hand."""
    freed = 0
    while ctx.objects:
        release = getattr(ctx.objects.popleft(), "release", None)
        if release is not None:
            release()
            freed += 1
    return freed


class CIslandView3D(CIslandControls, QOpenGLWidget):
    """The view on moderngl (OpenGL)."""

    status, selectionChanged, toolChanged, waterChanged = Signal(str), Signal(object), Signal(str), Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup()

    def initializeGL(self) -> None:
        import moderngl

        self.m_ctx = moderngl.create_context()
        collect_dropped(self.m_ctx)

    def paintGL(self) -> None:
        free_dropped(self.m_ctx)  # what was let go of since the last frame
        pixel_ratio = self.devicePixelRatioF()
        width_px, height_px = max(1, int(self.width() * pixel_ratio)), max(1, int(self.height() * pixel_ratio))
        self._frame(self.m_ctx, self.m_ctx.detect_framebuffer(self.defaultFramebufferObject()), width_px, height_px)
