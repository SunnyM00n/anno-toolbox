"""The viewer's 3D view: ``modelrender.CModelRenderer`` in a widget of the chosen device, the orbit camera on the
mouse (left drag turns, right or middle drag pans, the wheel zooms, F frames the model), and a clock for animations
and effects.

``make_view(backend)`` is the entry: ``CModelView`` (a ``QOpenGLWidget`` on moderngl, the format of
``render.glformat``) for ``gl``, ``CRhiModelView`` (a ``render.rhiview.CRhiView``) for a QRhi backend; both take
``show_scene(scene)``, ``state`` (a ``modelrender.SViewState``) changed by the window, ``image()`` for a picture of
the view. docs/viewer.md, "The renderer".
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QImageWriter
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QFileDialog

from ..infra.i18n import tr
from ..render import rhiview
from .modelfiles import CModelFiles
from .modelrender import CModelCamera, CModelRenderer, SViewState
from .modelscene import CScene

TICK_MS = 33  # 30 frames a second while something plays or loads


def _local_clock() -> tuple[int, int, int]:
    """The computer's local hour, minute and second (the clock alignments read it, engine-animation "Spinning
    parts")."""
    local_time = time.localtime()
    return local_time.tm_hour, local_time.tm_min, local_time.tm_sec


class _CControls:
    """What both views share: the scene, the camera on the mouse, the clock; a view draws with ``_frame``."""

    def _setup(self) -> None:
        self.m_files: CModelFiles | None = None
        self.m_renderer: CModelRenderer | None = None
        self.m_scene: CScene | None = None
        self._m_pending: CScene | None = None
        self.m_camera = CModelCamera((0, 0, 0), 5.0)
        self.m_state = SViewState()
        self.m_playing = False
        self.m_speed = 1.0
        self._m_framed = False
        self._m_press: QPoint | None = None
        self._m_button = None
        self._m_moved = 0
        self._m_clock = time.monotonic()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._m_timer = QTimer(self, interval=TICK_MS, timeout=self._tick)
        self._m_timer.start()

    def show_scene(self, scene: CScene, files: CModelFiles, frame: bool = True) -> None:
        """Draw ``scene`` (its files read through ``files``) and frame it once it is in (``frame``; a scene redrawn
        after an edit keeps the camera)."""
        self.m_files, self.m_scene, self._m_pending = files, scene, scene
        self._m_framed = not frame
        self.update()

    def clear(self) -> None:
        self.m_scene, self._m_pending = None, None
        if self.m_renderer is not None:
            with self._current():
                self.m_renderer.release_scene()
        self.update()

    def frame_model(self) -> None:
        if self.m_renderer is not None and self.m_renderer.m_box is not None:
            self.m_camera.frame(*self.m_renderer.m_box)
            self._m_framed = True
            self.update()

    def set_time(self, seconds: float) -> None:
        self.m_state.time = max(0.0, seconds)
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        if self.m_playing:
            self.m_state.time += (now - self._m_clock) * self.m_speed
            self.update()
        self._m_clock = now
        renderer = self.m_renderer
        if renderer is not None and (renderer.changed() or renderer.busy()):
            self.update()
        elif renderer is not None and (
            self.m_state.fading(renderer.m_longest_fade)
            or (renderer.m_clock_parts and _local_clock() != self.m_state.clock)
        ):
            self.update()  # a time slice fading after View > Night, a clock hand's next tick
        if renderer is not None and not self._m_framed and renderer.m_box is not None and not renderer.busy():
            self.frame_model()

    def _frame(self, ctx, fbo, size_px: tuple[int, int]) -> None:
        """One frame of the scene with the device ``ctx`` into ``fbo``."""
        if self.m_renderer is None or self.m_renderer.m_files is not self.m_files:
            if self.m_renderer is not None:
                self.m_renderer.release()
            self.m_renderer = CModelRenderer(ctx, self.m_files)
        if self._m_pending is not None:
            self.m_renderer.show(self._m_pending)
            self._m_pending = None
        self.m_state.now, self.m_state.clock = time.monotonic(), _local_clock()
        self.m_renderer.render(fbo, size_px, self.m_camera, self.m_state)
        for message in self.m_renderer.m_messages:
            self.status.emit(message)
        self.m_renderer.m_messages.clear()

    def _pixels(self) -> tuple[int, int]:
        pixel_ratio = self.devicePixelRatioF()
        return max(1, int(self.width() * pixel_ratio)), max(1, int(self.height() * pixel_ratio))

    def release(self) -> None:
        if self.m_renderer is not None:
            with self._current():
                self.m_renderer.release()
            self.m_renderer = None

    def closeEvent(self, event) -> None:
        self.release()
        super().closeEvent(event)

    # the mouse
    def mousePressEvent(self, event) -> None:
        self._m_press, self._m_button = event.position().toPoint(), event.button()
        self._m_moved = 0

    def mouseMoveEvent(self, event) -> None:
        if self._m_press is None:
            return
        position = event.position().toPoint()
        delta_x, delta_y = position.x() - self._m_press.x(), position.y() - self._m_press.y()
        self._m_moved += abs(delta_x) + abs(delta_y)
        self._m_press = position
        if self._m_button == Qt.MouseButton.LeftButton:
            self.m_camera.turn(delta_x, delta_y)
        else:
            k = self.m_camera.m_distance_m * 0.0025
            self.m_camera.m_target = self.m_camera.m_target + (
                -self.m_camera.view()[0, :3] * delta_x * k + self.m_camera.view()[1, :3] * delta_y * k
            ).astype("f4")
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        clicked = self._m_press is not None and self._m_button == Qt.MouseButton.LeftButton and self._m_moved <= 3
        self._m_press = None
        if clicked:
            self.pick_at(event.position().x(), event.position().y())

    def pick_at(self, x: float, y: float) -> str:
        """Select the part under the widget's point (x, y): ``picked`` says which ("" for none)."""
        if self.m_renderer is None:
            return ""
        pixel_ratio = self.devicePixelRatioF()
        with self._current():
            node = (
                self.m_renderer.pick(self._pixels(), self.m_camera, self.m_state, x * pixel_ratio, y * pixel_ratio)
                or ""
            )
        self.picked.emit(node)
        return node

    def wheelEvent(self, event) -> None:
        self.m_camera.zoom(event.angleDelta().y() / 120)
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_F:
            self.frame_model()
        else:
            super().keyPressEvent(event)

    def image(self) -> QImage:
        """The view as a ``QImage``."""
        return self.grabFramebuffer()


class _CGlCurrent:
    """The OpenGL context made current around a renderer call outside a paint."""

    def __init__(self, widget: QOpenGLWidget) -> None:
        self.m_widget = widget

    def __enter__(self) -> None:
        self.m_widget.makeCurrent()

    def __exit__(self, *exc) -> None:
        self.m_widget.doneCurrent()


class _CNoContext:
    def __enter__(self) -> None:
        pass

    def __exit__(self, *exc) -> None:
        pass


class CModelView(_CControls, QOpenGLWidget):
    """One model at a time on moderngl. ``status`` says what the renderer reports (a file that does not read);
    ``picked`` gives the node of the part clicked (a click without a drag), "" for none."""

    status = Signal(str)
    picked = Signal(str)
    m_backend = "gl"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_ctx = None
        self._setup()

    def _current(self):
        return _CGlCurrent(self)

    def initializeGL(self) -> None:
        import moderngl

        self.m_ctx = moderngl.create_context()

    def paintGL(self) -> None:
        fbo = self.m_ctx.detect_framebuffer(self.defaultFramebufferObject())
        if self.m_files is None:
            fbo.use()
            fbo.clear(0.2, 0.2, 0.2, 1.0)
            return
        self._frame(self.m_ctx, fbo, self._pixels())


class CRhiModelView(_CControls, rhiview.CRhiView):
    """One model at a time on a QRhi backend (``CModelView``'s signals; ``frameSwapped`` after each frame, as the
    OpenGL widget's)."""

    status = Signal(str)
    picked = Signal(str)
    frameSwapped = Signal()

    def __init__(self, backend: str = "d3d12", parent=None) -> None:
        super().__init__(backend, parent)
        self._setup()
        self.frameSubmitted.connect(self.frameSwapped.emit)

    def _current(self):
        return _CNoContext()

    def ready(self, ctx) -> None:
        if self.m_renderer is not None:  # its resources were the old device's
            self.m_renderer = None
            self._m_pending = self.m_scene

    def released(self, ctx) -> None:
        """The device goes away: the renderer's resources are its own, let go while it still stands (the scene is
        shown again by the next device's first frame)."""
        if self.m_renderer is not None:
            renderer, self.m_renderer = self.m_renderer, None
            self._m_pending = self.m_scene
            renderer.release()

    def draw(self, ctx, screen, size_px: tuple[int, int]) -> None:
        if self.m_files is None:
            screen.use()
            screen.clear(0.2, 0.2, 0.2, 1.0)
            return
        self._frame(ctx, screen, size_px)


def make_view(backend: str, parent=None) -> CModelView | CRhiModelView:
    """The 3D view for ``backend`` (``render.rhiview.BACKENDS``)."""
    return CRhiModelView(backend, parent) if rhiview.is_rhi(backend) else CModelView(parent)


def save_picture(window, make_image) -> None:
    """Ask for a PNG file and write ``make_image()`` into it; a file that could not be written is said in the window's
    status bar (a folder that takes no files, a suffix Qt writes no picture for)."""
    name, _f = QFileDialog.getSaveFileName(window, tr("common.save_picture"), "", "PNG (*.png)")  # i18n: ok
    if name:
        writer = QImageWriter(name)
        if not writer.write(make_image()):
            window.statusBar().showMessage(
                tr("viewer.could_not_write_name", name=name, error=writer.errorString()), 12000
            )


def rhi_picture(files: CModelFiles, scene: CScene, state: SViewState, size_px: tuple[int, int], backend: str,
                wait_s: float = 30.0) -> QImage | None:  # fmt: skip
    """``scene`` drawn on a QRhi backend without showing a window, framed once the files of the parts ``state`` shows
    are in, as a ``QImage`` once two frames after the framing found nothing loading (the offscreen pictures and the
    tests; a ``QApplication`` must exist). Raises ``RuntimeError`` when a frame failed."""
    view = CRhiModelView(backend)
    view.m_state = state
    view.show_scene(scene, files)
    start, image, framed, quiet = time.monotonic(), None, False, 0
    try:
        while time.monotonic() - start < wait_s:
            image = view.picture(*size_px)
            renderer = view.m_renderer
            if view.m_failed:
                raise RuntimeError(view.m_failed)
            # a frame part asks for its files only once what it is made of is in (the old engines' skies: their
            # stages, then their maps), so one quiet frame is not enough: two in a row, nothing loaded in between
            loaded = renderer.changed() if renderer is not None else False
            if (
                renderer is None
                or renderer.m_box is None
                or loaded
                or renderer.busy()
                or not renderer.shown_parts_in(state)
            ):
                quiet = 0
                time.sleep(0.02)
            elif not framed:
                view.frame_model()
                framed = True
            else:
                quiet += 1
                if quiet == 2:
                    break
        errors = list(view.m_ctx.m_errors) if view.m_ctx is not None else []
    finally:
        view.release()
        view.close()
        view.deleteLater()
    if errors:
        raise RuntimeError("; ".join(sorted(set(errors))))
    return image
