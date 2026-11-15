"""The PC application's 3D view: ``desktoprender`` in a Qt ``QOpenGLWidget`` (``IslandView3D``; on QRhi
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
from ...render.camera import OrbitCamera, ground_hit
from ...render.viewcolors import palette
from ..editchange import Change
from ..editlists import Hidden
from ..editsession import EditSession, Entry, group_key, label_of, type_of
from . import flycamera
from .desktopbrush import BrushTools
from .desktopgizmo import Gizmo, GizmoControls
from .desktopgrids import active_overlay, surf_overlay
from .desktopgroup import GroupTools
from .desktopoverlays import OverlayDraw
from .desktopplace import PlaceTool
from .desktoprender import Renderer, ViewOptions

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


class IslandControls(GizmoControls, OverlayDraw):
    """The island in 3D and the tools' input, on either device (the widget classes below and in
    ``desktoprhiview``). One view lives as long as its window (a second 3D widget added to a shown window makes Qt
    recreate that window): ``load`` hands it an island's edit session and editor, ``unload`` takes it away again.
    The corners show the frame rate and the cursor's tile."""

    GPU_PICK = True  # a click picks from an id picture the device reads back; else by the objects' spheres

    def _setup(self) -> None:
        self.ctx = None
        self.session: EditSession | None = None
        self.editor = None
        self.renderer: Renderer | None = None
        self.camera = OrbitCamera((0, 0, 0), 300)
        self.camera_mode = "orbit"  # or "fly": kept from island to island
        self.scheme = "dark"  # the window's theme (set_scheme): the view's own colours follow it
        self.opts = ViewOptions(scheme=self.scheme)
        self.hidden = Hidden()
        self.tool = "select"
        self.allowed = set(TOOLS)  # the island's mode may leave tools out (desktopeditor.modes)
        self.brush = {"mode": "raise", "radius": 6, "strength": 0.5, "plant": True, "spacing": 2.0}
        self.brushes = BrushTools(self)
        self.paint_material: int | None = None
        self.gizmo = Gizmo()
        self.selected: Entry | None = None
        self.water_sel: dict | None = None
        self.pick: dict | None = None  # the library's pick, for Place
        self.place_yaw = 0.0
        self.checks: dict = {}
        self.cursor_hit = None
        self.group = GroupTools(self)  # several selected, snapping, copy and paste, layers
        self.placer = PlaceTool(self)  # a building on its grid, its footprint and the rules live (desktopplace)
        self._press: QPoint | None = None
        self._drag = None
        self._hot = None
        self.fov, self.speed = 45.0, 1.0  # the status bar's FOV (degrees) and Speed, kept from island to island
        self.fps_label = QLabel(tr("FPS: {n}", n=0), self, objectName="hud")
        self.coord_label = QLabel("", self, objectName="hud")
        self.hint_label = QLabel(tr("Open an island: pick one in the Islands panel."), self, objectName="hud")
        for label in (self.fps_label, self.coord_label, self.hint_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.coord_label.hide()
        self._frame_times: deque[float] = deque(maxlen=30)
        self._last_paint = 0.0
        self._painted = False
        self._hover: QPoint | None = None
        self._hover_done: QPoint | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._timer = QTimer(self, interval=IDLE_MS, timeout=self._tick)
        self._timer.start()
        self._hud_timer = QTimer(self, interval=500, timeout=self._hud)
        self._hud_timer.start()

    # loading
    def load(self, session: EditSession, editor=None) -> None:
        self._release_renderer()
        self.session, self.editor = session, editor
        self.opts, self.hidden = ViewOptions(scheme=self.scheme), Hidden()
        self.tool, self.allowed = "select", set(TOOLS)
        self.selected = self.water_sel = self.pick = self.cursor_hit = self._drag = self._hot = self._press = None
        self.paint_material, self.place_yaw, self.checks, self.gizmo = None, 0.0, {}, Gizmo()
        self.placer.reset()
        size = session.size
        self.camera = OrbitCamera((size / 2, max(0.0, float(np.median(session.heights))), size / 2), size * 1.1)
        self.camera = flycamera.switch(self.camera, self.camera_mode)
        self.set_fov(self.fov)
        self.set_speed(self.speed)
        game = bool(session.island.get("game")) or session.anno2070_editing
        if game:  # 117's rules are for 117's islands, built or vanilla
            self.opts.game_terrain = self.opts.native = False
        elif getattr(self, "backend", "gl") == "d3d12":  # a 117 island opens in the game's look (island-117-view.md
            # "Speed", part 7): its shaders over its near terrain
            self.opts.native = self.opts.game_terrain = True
        self.group.attach(session)
        self.hint_label.hide()
        self.update()

    def unload(self) -> None:
        """Back to the empty view (the sky), the island's resources freed."""
        self._release_renderer()
        self.session = self.editor = self.selected = self.water_sel = self.pick = self.cursor_hit = None
        self.checks, self._drag, self._hover = {}, None, None
        self.group.detach()
        self.coord_label.hide()
        self.hint_label.show()
        self._place_hud()
        self.update()

    def _release_renderer(self) -> None:
        if self.renderer is None:
            return
        self.makeCurrent()  # its GL objects are freed in this widget's context
        try:
            self.renderer.release()
        finally:
            self.renderer = None
            self.doneCurrent()

    def set_camera_mode(self, mode: str) -> None:
        """Orbit about a pivot or fly freely (``flycamera``); the camera stays where it looks."""
        self.camera_mode = mode
        self.camera = flycamera.switch(self.camera, mode)
        self.camera.fov, self.camera.speed = math.radians(self.fov), self.speed
        self.update()

    def set_fov(self, degrees: float) -> None:
        self.fov = float(degrees)
        self.camera.fov = math.radians(self.fov)
        self.update()

    def set_scheme(self, scheme: str) -> None:
        """The window's theme changed: the sky, fog and overlays are drawn in its colours from the next frame."""
        self.scheme = self.opts.scheme = scheme
        self.update()

    def _rgba(self, key: str, alpha: float = 1.0) -> tuple:
        return (*palette(self.scheme)[key], alpha)

    def set_speed(self, speed: float) -> None:
        self.speed = float(speed)
        self.camera.speed = self.speed

    def closeEvent(self, e) -> None:
        self._release_renderer()
        super().closeEvent(e)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_hud()

    def _place_hud(self) -> None:
        hint = self.hint_label  # wraps on a narrow view (a small window, a high scale) instead of being cut off
        hint.setWordWrap(True)
        hint.setFixedWidth(min(hint.fontMetrics().horizontalAdvance(hint.text()) + 16, max(80, self.width() - 32)))
        for label in (self.fps_label, self.coord_label, hint):
            label.adjustSize()
        self.fps_label.move(8, 8)
        self.coord_label.move(8, self.height() - self.coord_label.height() - 8)
        self.hint_label.move(
            (self.width() - self.hint_label.width()) // 2, (self.height() - self.hint_label.height()) // 2
        )

    def _hud(self) -> None:
        if failed := getattr(self, "failed", ""):  # a frame that raised draws nothing: say why, not a blank view
            self.fps_label.setText(tr("The view cannot draw: {error}", error=failed))  # also after a single frame
            self.fps_label.adjustSize()
            return
        if self._painted and self._frame_times:
            fps = f"{len(self._frame_times) / max(1e-6, sum(self._frame_times)):.0f}"
            memory = gpuinfo.video_memory()  # the card's own and the system memory it maps for this process
            used = (memory.local + memory.shared) / 2**30 if memory is not None else 0.0
            text = tr("FPS: {n}, {gb} GB graphics memory", n=fps, gb=number(used, 1)) if used else tr("FPS: {n}", n=fps)
            self.fps_label.setText(text)
            self.fps_label.adjustSize()
            self._painted = False

    def _tick(self) -> None:
        r = self.renderer
        if r is not None and (r.changed() or r.animated):
            self.update()
        if self._hover is not None and self._hover != self._hover_done and self.session is not None:
            self._hover_done = self._hover
            hit = self.ground_at(self._hover)
            if hit is None:
                self.coord_label.hide()
            else:
                x, y, z = self.from_view(hit)
                self.coord_label.setText(tr("({x}, {z})\nheight {y}", x=f"{x:.0f}", z=f"{z:.0f}", y=number(y, 2)))
                self.coord_label.adjustSize()
                self.coord_label.show()
                self._place_hud()

    def _frame(self, ctx, fbo, w: int, h: int) -> None:
        """One frame of the island with the device ``ctx`` into ``fbo`` (``w`` x ``h`` pixels)."""
        now = time.perf_counter()
        if 0 < now - self._last_paint < 0.25:  # a longer gap is a rest, not a slow frame
            self._frame_times.append(now - self._last_paint)
        self._last_paint, self._painted = now, True
        dpr = self.devicePixelRatioF()
        if self.session is None:
            fbo.use()
            fbo.clear(*palette(self.scheme)["clear"], 1.0)
            return
        if self.renderer is None:
            self.renderer = Renderer(ctx, self.session, self.editor)
            self.apply_hidden()
        self.renderer.render(fbo, (w, h), self.camera, self.opts, min(1.0, 1.5 / dpr), self.overlays())
        for m in self.renderer.messages:
            self.status.emit(m)
        self.renderer.messages.clear()

    # coordinates
    def to_view(self, p) -> np.ndarray:
        return np.array([self.session.size - p[0], p[1], p[2]], float)

    def from_view(self, v) -> list[float]:
        return [float(self.session.size - v[0]), float(v[1]), float(v[2])]

    def ray(self, pos) -> tuple[np.ndarray, np.ndarray]:
        return self.camera.ray(pos.x(), pos.y(), max(1, self.width()), max(1, self.height()))

    def ground_at(self, pos):
        """The terrain point under the cursor (view space), or None."""
        o, d = self.ray(pos)
        return ground_hit(self.session.heights, self.session.step, o, d)

    def project(self, p) -> tuple[float, float, float]:
        w, h = max(1, self.width()), max(1, self.height())
        return picking.to_screen(self.camera.matrix(w / h), (w, h), [p])[0] or (-1e9, -1e9, -1.0)

    # tools
    def set_tool(self, name: str) -> None:
        if name not in TOOLS or name not in self.allowed:
            return
        self.tool = name
        if name != "select":
            self.select(None)
        if name != "water":
            self.water_sel = None
        self.gizmo.axes_only = None
        self.toolChanged.emit(name)
        if name in ("place", "spray") and not self.pick:
            self.status.emit(tr("pick a model in the library"))
        self.update()

    def set_pick(self, pick: dict | None) -> None:
        self.pick = pick
        self.placer.picked(pick)
        if self.renderer is not None:
            self.renderer.set_ghost(None)
        if pick:
            self.set_tool("place")

    def select(self, e: Entry | None) -> None:
        self.selected = e if (e is None or e.alive) else None
        self.group.sync(self.selected)
        self.selectionChanged.emit(self.selected)
        self.update()

    def select_water(self, handle: dict | None) -> None:
        self.water_sel = handle
        self.gizmo.mode = "translate"
        self.gizmo.axes_only = (1,) if handle and "lake" in handle else None
        self.waterChanged.emit(handle)
        self.update()

    def apply_hidden(self) -> None:
        if self.session is None:
            return
        if self.hidden.count():
            changed = self.hidden.apply(self.session.entries)
        else:  # nothing hidden: only those still hidden change (asking each entry took a second on a continent)
            changed = {e for e in self.session.entries if e.hidden}
            for e in changed:
                e.hidden = False
        if changed and self.renderer is not None:
            self.renderer.on_change(Change(entries=changed))
        if self.selected is not None and self.selected.hidden:
            self.select(None)
        self.update()

    def focus_on(self, entries) -> None:
        pts = [self.to_view(e.position) for e in entries if e.shown]
        if not pts:
            return
        pts = np.asarray(pts)
        r = self._radius(entries[0])
        lo, hi = pts.min(axis=0) - r, pts.max(axis=0) + r
        self.camera.target = ((lo + hi) / 2).astype("f4")
        self.camera.distance = max(8.0, float(np.linalg.norm(hi - lo)) * 1.2)
        self.update()

    def go_to(self, x: float, z: float, distance: float = 25.0, turn: float = 0.0) -> None:
        """Look at tile (x, z) from ``distance``, turned ``turn`` degrees (the footer's "go to")."""
        self.camera.look(self.to_view([x, self.session.height_at(x, z), z]), distance, turn)
        self.update()

    def _radius(self, e: Entry) -> float:
        b = self.renderer.batch_of(e) if self.renderer is not None else None
        s = e.scale if not isinstance(e.scale, list) else max(e.scale)
        return (b.radius if b is not None else 4.0) * float(s or 1.0)

    # overlays: brush ring, gizmo, water network, checks, selection box, place ghost
    def overlays(self) -> list:
        out = []
        s = self.session
        if self.tool in BRUSHES and self.cursor_hit is not None:
            c, r = self.cursor_hit, self.brush["radius"]
            ts = np.linspace(0, 2 * math.pi, 65)
            ring = []
            for a, b in zip(ts[:-1], ts[1:], strict=True):
                for t in (a, b):
                    x, z = c[0] + r * math.cos(t), c[2] + r * math.sin(t)
                    ring.append((x, s.height_at(s.size - x, z) + 0.2, z, *self._rgba("brush")))
            out.append(("lines", np.asarray(ring, "f4")))
        if self.selected is not None and self.selected.shown and self.tool == "select":
            out += self._selection_box(self.selected) + self._footprint_under(self.selected)
            for other in self.group.others():
                out += self._selection_box(other) + self._footprint_under(other)
        if self.tool == "water" and s.water_net:
            out += self._water_overlay()
        if self.tool == "blocker" and s.path.blocked is not None:
            out.append(("points", self._blocker_points()))
        if self.tool == "active":
            out += active_overlay(self)
        if self.tool == "surf":
            out += surf_overlay(self)
        gz = self._gizmo_handles()
        if gz:
            active = self._rgba("select")
            lines, pts = self.gizmo.lines(gz, self._hot, active), self.gizmo.points(gz, self._hot, active)
            out.append(("lines", np.asarray(lines, "f4").reshape(-1, 7)))
            out.append(("points", np.asarray(pts, "f4").reshape(-1, 7)))
        out += self._check_overlays() + self.placer.overlay()
        return out

    # input
    def mousePressEvent(self, e) -> None:
        if self.session is None:
            return
        pos = e.position().toPoint()
        self._press = pos
        btn = e.button()
        if btn == Qt.MouseButton.LeftButton:
            h = self._hit_handle(pos)
            if h is not None:
                self._start_gizmo(h, pos)
                return
            self._drag = ("orbit", pos)
        elif btn == Qt.MouseButton.MiddleButton:
            self._drag = ("pan", pos)
        elif btn == Qt.MouseButton.RightButton:
            if self.tool in BRUSHES and not self.session.readonly:
                hit = self.ground_at(pos)
                if hit is not None:
                    self._start_brush(hit)
                    return
            self._drag = ("pan", pos)

    def mouseMoveEvent(self, e) -> None:
        if self.session is None:
            return
        pos = e.position().toPoint()
        self._hover = pos
        d = self._drag
        if d is None:
            if self.tool in BRUSHES or self.tool == "place":
                self.cursor_hit = self.ground_at(pos)
                if self.tool == "place" and self.renderer is not None:
                    self._ghost()
                self.update()
            elif self._gizmo_target() is not None:
                hot = self._hit_handle(pos)
                if (hot is None) != (self._hot is None) or (hot and self._hot and hot["axis"] != self._hot["axis"]):
                    self._hot = hot
                    self.update()
            return
        if d[0] in ("orbit", "pan"):
            delta = pos - d[1]
            self._drag = (d[0], pos)
            (self.camera.turn if d[0] == "orbit" else self.camera.pan)(delta.x(), delta.y())
        elif d[0] == "brush":
            hit = self.ground_at(pos)
            self.cursor_hit = hit
            if hit is not None:
                self._brush_to(hit)
        elif d[0] == "gizmo":
            self._drag_gizmo(pos)
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self.session is None:
            return
        pos = e.position().toPoint()
        click = self._press is not None and (pos - self._press).manhattanLength() <= CLICK
        d, self._drag = self._drag, None
        if d is not None and d[0] == "brush":
            self._end_brush()
            return
        if d is not None and d[0] == "gizmo":
            self._end_gizmo(d)
            return
        if not click:
            return
        if e.button() == Qt.MouseButton.LeftButton:
            if self.tool == "place":
                self.placer.place(self.ground_at(pos))
            elif self.tool == "water":
                self._pick_water(pos)
            elif self.tool == "select":
                self.group.click(self.pick_entry(pos), bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
        elif e.button() == Qt.MouseButton.RightButton and self.tool == "select":
            self.hide_menu(e.globalPosition().toPoint(), self.pick_entry(pos))
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = self._hover_done = None
        self.coord_label.hide()
        super().leaveEvent(e)

    def wheelEvent(self, e) -> None:
        if self.session is None:
            return
        steps = e.angleDelta().y() / 120
        hit = self.ground_at(e.position().toPoint()) if steps > 0 else None
        self.camera.zoom(steps, hit)
        self.update()

    def keyPressEvent(self, e) -> None:
        if self.session is None:
            return super().keyPressEvent(e)
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if ctrl and e.key() == Qt.Key.Key_D:
            self.duplicate_selection()
        elif ctrl or (action := self._key_table().get(e.key())) is None:
            return super().keyPressEvent(e)  # the window's actions: save, undo, redo
        else:
            action()
        self.selectionChanged.emit(self.selected)
        self.update()

    def _key_table(self) -> dict:
        """Key -> what it does in the view now (a key the table lacks is the window's)."""
        keys = Qt.Key
        table = {keys.Key_Delete: self.delete_selection, keys.Key_Backspace: self.delete_selection}
        table |= {k: partial(self.camera.pan, *step) for k, step in PAN_KEYS.items()}
        table |= {k: partial(self.set_tool, tool) for k, tool in TOOL_KEYS.items()}
        table |= {keys.Key_Escape: self._deselect, keys.Key_W: partial(self._gizmo_key, "translate")}
        table |= {keys.Key_E: self._turn_key, keys.Key_R: partial(self._gizmo_key, "scale")}
        if self.camera_mode == "fly":
            table |= {keys.Key_PageUp: partial(self.camera.lift, 1), keys.Key_PageDown: partial(self.camera.lift, -1)}
        if self.tool == "place":
            table |= {
                keys.Key_BracketLeft: partial(self.placer.turn, -1),
                keys.Key_BracketRight: partial(self.placer.turn, 1),
            }
        if self.selected is not None:
            table[keys.Key_F] = partial(self.focus_on, [self.selected])
        return table

    def _deselect(self) -> None:
        self.select(None)
        self.select_water(None)

    def _gizmo_key(self, mode: str) -> None:
        self.gizmo.mode = mode

    def _turn_key(self) -> None:
        """E: the turn handle, and again the tilt handle (about the model's own axes)."""
        if self.gizmo.mode == "rotate":
            self.gizmo.tilt = not self.gizmo.tilt
        self.gizmo.mode = "rotate"
        self.status.emit(tr("rotate: tilt (about the model's own axes)") if self.gizmo.tilt else tr("rotate: turn"))

    def river_selected(self) -> bool:
        return self.tool == "water" and bool(self.water_sel) and "river" in self.water_sel

    def delete_selection(self) -> None:
        """Del: the selected river point (water tool) or object."""
        if self.river_selected():
            if self.session.delete_river_point(self.water_sel["river"], self.water_sel["point"]):
                self.select_water(None)
        elif self.group.delete() or self.selected is not None:
            if self.selected is not None and self.selected.alive and self.selected.visible:
                self.session.delete(self.selected)
            self.select(None)

    def duplicate_selection(self) -> None:
        """Ctrl+D: a point after the selected river point (water tool), or a copy of the selected object."""
        if self.river_selected():
            i = self.session.insert_river_point(self.water_sel["river"], self.water_sel["point"])
            self.select_water({"river": self.water_sel["river"], "point": i})
        elif self.selected is not None and not self.group.duplicate():
            n = self.session.duplicate(self.selected)
            if n is not None:
                self.select(n)

    # picking
    def gpu_pick(self, size: tuple[int, int], x: float, y: float) -> Entry | None:
        """The entry the renderer's id picture shows at pixel (x, y), read at once (OpenGL)."""
        self.makeCurrent()
        try:
            return self.renderer.pick(self.camera, size, x, y, self.opts)
        finally:
            self.doneCurrent()

    def pick_entry(self, pos) -> Entry | None:
        if self.renderer is None:
            return None
        dpr = self.devicePixelRatioF()
        w, h = max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr))
        found = self.gpu_pick((w, h), pos.x() * dpr, pos.y() * dpr) if self.GPU_PICK else None
        # banner leaves are points the shader spreads: a tree is also hit through a sphere round its crown
        o, d = self.ray(pos)
        best = math.inf
        if found is not None:
            best = float(np.linalg.norm(self.to_view(found.position) - o))
        for e in self.session.entries:
            tree = e.base.get("template") == "Tree"
            if (self.GPU_PICK and not tree) or not e.shown or not self.opts.layers.get(e.layer, True):
                continue
            r = self._radius(e) * 0.5
            crown = self.to_view(e.position) + np.array([0, r, 0])
            t = float(np.dot(crown - o, d))
            if t > 0 and np.linalg.norm(o + t * d - crown) < r and t < best:
                best, found = t, e
        return found

    def _pick_water(self, pos) -> None:
        best, bd = None, 14.0  # the nearest handle on screen within 14 px
        for h, p in self.water_handles():
            x, y, z = self.project(p)
            dist = math.hypot(x - pos.x(), y - pos.y())
            if z < 1 and dist < bd:
                best, bd = h, dist
        self.select_water(best)

    def hide_menu(self, at, e: Entry | None) -> None:
        menu, hid = QMenu(self), self.hidden
        if e is not None:
            menu.addAction(tr("Hide this object"), lambda: (hid.entries.add(e), self.apply_hidden()))
            g, t = group_key(e.base), type_of(e.base)
            label = label_of(e.base)
            if g in hid.groups:
                menu.addAction(tr("Show all {name}", name=label), lambda: (hid.groups.discard(g), self.apply_hidden()))
            else:
                menu.addAction(tr("Hide all {name}", name=label), lambda: (hid.groups.add(g), self.apply_hidden()))
            if t in hid.types:
                menu.addAction(tr("Show category {name}", name=t), lambda: (hid.types.discard(t), self.apply_hidden()))
            else:
                menu.addAction(tr("Hide category {name}", name=t), lambda: (hid.types.add(t), self.apply_hidden()))
        if hid.count():
            menu.addAction(
                tr("Show everything hidden ({n})", n=hid.count()), lambda: (hid.clear(), self.apply_hidden())
            )
        if not menu.isEmpty():
            menu.exec(at)

    # brushes
    def _start_brush(self, hit) -> None:
        if self.brushes.start(hit):
            self._drag = ("brush",)
            self.cursor_hit = hit

    def _brush_to(self, hit) -> None:
        self.brushes.to(hit)

    def _end_brush(self) -> None:
        self.brushes.end()

    def _ghost(self) -> None:
        self.placer.ghost()


class IslandView3D(IslandControls, QOpenGLWidget):
    """The view on moderngl (OpenGL)."""

    status, selectionChanged, toolChanged, waterChanged = Signal(str), Signal(object), Signal(str), Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup()

    def initializeGL(self) -> None:
        import moderngl

        self.ctx = moderngl.create_context()

    def paintGL(self) -> None:
        dpr = self.devicePixelRatioF()
        w, h = max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr))
        self._frame(self.ctx, self.ctx.detect_framebuffer(self.defaultFramebufferObject()), w, h)
