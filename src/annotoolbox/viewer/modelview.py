"""The viewer's 3D view: ``modelrender.ModelRenderer`` in a widget of the chosen device, the orbit camera on the
mouse (left drag turns, right or middle drag pans, the wheel zooms, F frames the model), and a clock for animations
and effects.

``make_view(backend)`` is the entry: ``ModelView`` (a ``QOpenGLWidget`` on moderngl, the format of
``render.glformat``) for ``gl``, ``RhiModelView`` (a ``render.rhiview.RhiView``) for a QRhi backend; both take
``show_scene(scene)``, ``state`` (a ``modelrender.ViewState``) changed by the window, ``image()`` for a picture of
the view. docs/viewer.md, "The renderer".
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from ..render import rhiview
from .modelfiles import ModelFiles
from .modelrender import ModelCamera, ModelRenderer, ViewState
from .modelscene import Scene

TICK_MS = 33  # 30 frames a second while something plays or loads


def _local_clock() -> tuple[int, int, int]:
    """The computer's local hour, minute and second (the clock alignments read it, engine-animation "Spinning
    parts")."""
    t = time.localtime()
    return t.tm_hour, t.tm_min, t.tm_sec


class _Controls:
    """What both views share: the scene, the camera on the mouse, the clock; a view draws with ``_frame``."""

    def _setup(self) -> None:
        self.files: ModelFiles | None = None
        self.renderer: ModelRenderer | None = None
        self.scene: Scene | None = None
        self._pending: Scene | None = None
        self.camera = ModelCamera((0, 0, 0), 5.0)
        self.state = ViewState()
        self.playing = False
        self.speed = 1.0
        self._framed = False
        self._press: QPoint | None = None
        self._button = None
        self._moved = 0
        self._clock = time.monotonic()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._timer = QTimer(self, interval=TICK_MS, timeout=self._tick)
        self._timer.start()

    def show_scene(self, scene: Scene, files: ModelFiles, frame: bool = True) -> None:
        """Draw ``scene`` (its files read through ``files``) and frame it once it is in (``frame``; a scene redrawn
        after an edit keeps the camera)."""
        self.files, self.scene, self._pending = files, scene, scene
        self._framed = not frame
        self.update()

    def clear(self) -> None:
        self.scene, self._pending = None, None
        if self.renderer is not None:
            with self._current():
                self.renderer.release_scene()
        self.update()

    def frame_model(self) -> None:
        if self.renderer is not None and self.renderer.box is not None:
            self.camera.frame(*self.renderer.box)
            self._framed = True
            self.update()

    def set_time(self, seconds: float) -> None:
        self.state.time = max(0.0, seconds)
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        if self.playing:
            self.state.time += (now - self._clock) * self.speed
            self.update()
        self._clock = now
        r = self.renderer
        if r is not None and (r.changed() or r.busy()):
            self.update()
        elif r is not None and (
            self.state.fading(r.longest_fade) or (r.clock_parts and _local_clock() != self.state.clock)
        ):
            self.update()  # a time slice fading after View > Night, a clock hand's next tick
        if r is not None and not self._framed and r.box is not None and not r.busy():
            self.frame_model()

    def _frame(self, ctx, fbo, size: tuple[int, int]) -> None:
        """One frame of the scene with the device ``ctx`` into ``fbo``."""
        if self.renderer is None or self.renderer.files is not self.files:
            if self.renderer is not None:
                self.renderer.release()
            self.renderer = ModelRenderer(ctx, self.files)
        if self._pending is not None:
            self.renderer.show(self._pending)
            self._pending = None
        self.state.now, self.state.clock = time.monotonic(), _local_clock()
        self.renderer.render(fbo, size, self.camera, self.state)
        for m in self.renderer.messages:
            self.status.emit(m)
        self.renderer.messages.clear()

    def _pixels(self) -> tuple[int, int]:
        dpr = self.devicePixelRatioF()
        return max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr))

    def release(self) -> None:
        if self.renderer is not None:
            with self._current():
                self.renderer.release()
            self.renderer = None

    def closeEvent(self, e) -> None:
        self.release()
        super().closeEvent(e)

    # the mouse
    def mousePressEvent(self, e) -> None:
        self._press, self._button = e.position().toPoint(), e.button()
        self._moved = 0

    def mouseMoveEvent(self, e) -> None:
        if self._press is None:
            return
        pos = e.position().toPoint()
        dx, dy = pos.x() - self._press.x(), pos.y() - self._press.y()
        self._moved = getattr(self, "_moved", 0) + abs(dx) + abs(dy)
        self._press = pos
        if self._button == Qt.MouseButton.LeftButton:
            self.camera.turn(dx, dy)
        else:
            k = self.camera.distance * 0.0025
            self.camera.target = self.camera.target + (
                -self.camera.view()[0, :3] * dx * k + self.camera.view()[1, :3] * dy * k
            ).astype("f4")
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        clicked = self._press is not None and self._button == Qt.MouseButton.LeftButton and self._moved <= 3
        self._press = None
        if clicked:
            self.pick_at(e.position().x(), e.position().y())

    def pick_at(self, x: float, y: float) -> str:
        """Select the part under the widget's point (x, y): ``picked`` says which ("" for none)."""
        if self.renderer is None:
            return ""
        dpr = self.devicePixelRatioF()
        with self._current():
            node = self.renderer.pick(self._pixels(), self.camera, self.state, x * dpr, y * dpr) or ""
        self.picked.emit(node)
        return node

    def wheelEvent(self, e) -> None:
        self.camera.zoom(e.angleDelta().y() / 120)
        self.update()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_F:
            self.frame_model()
        else:
            super().keyPressEvent(e)

    def image(self):
        """The view as a ``QImage``."""
        return self.grabFramebuffer()


class _GlCurrent:
    """The OpenGL context made current around a renderer call outside a paint."""

    def __init__(self, widget: QOpenGLWidget) -> None:
        self.widget = widget

    def __enter__(self) -> None:
        self.widget.makeCurrent()

    def __exit__(self, *exc) -> None:
        self.widget.doneCurrent()


class _NoContext:
    def __enter__(self) -> None:
        pass

    def __exit__(self, *exc) -> None:
        pass


class ModelView(_Controls, QOpenGLWidget):
    """One model at a time on moderngl. ``status`` says what the renderer reports (a file that does not read);
    ``picked`` gives the node of the part clicked (a click without a drag), "" for none."""

    status = Signal(str)
    picked = Signal(str)
    backend = "gl"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.ctx = None
        self._setup()

    def _current(self):
        return _GlCurrent(self)

    def initializeGL(self) -> None:
        import moderngl

        self.ctx = moderngl.create_context()

    def paintGL(self) -> None:
        fbo = self.ctx.detect_framebuffer(self.defaultFramebufferObject())
        if self.files is None:
            fbo.use()
            fbo.clear(0.2, 0.2, 0.2, 1.0)
            return
        self._frame(self.ctx, fbo, self._pixels())


class RhiModelView(_Controls, rhiview.RhiView):
    """One model at a time on a QRhi backend (``ModelView``'s signals; ``frameSwapped`` after each frame, as the
    OpenGL widget's)."""

    status = Signal(str)
    picked = Signal(str)
    frameSwapped = Signal()

    def __init__(self, backend: str = "d3d12", parent=None) -> None:
        super().__init__(backend, parent)
        self._setup()
        self.frameSubmitted.connect(self.frameSwapped.emit)

    def _current(self):
        return _NoContext()

    def ready(self, ctx) -> None:
        if self.renderer is not None:  # its resources were the old device's
            self.renderer = None
            self._pending = self.scene

    def draw(self, ctx, screen, size: tuple[int, int]) -> None:
        if self.files is None:
            screen.use()
            screen.clear(0.2, 0.2, 0.2, 1.0)
            return
        self._frame(ctx, screen, size)


def make_view(backend: str, parent=None):
    """The 3D view for ``backend`` (``render.rhiview.BACKENDS``)."""
    return RhiModelView(backend, parent) if rhiview.is_rhi(backend) else ModelView(parent)


def rhi_picture(files: ModelFiles, scene: Scene, state: ViewState, size: tuple[int, int], backend: str,
                wait: float = 30.0):  # fmt: skip
    """``scene`` drawn on a QRhi backend without showing a window, framed once the files of the parts ``state`` shows
    are in, as a ``QImage`` once two frames after the framing found nothing loading (the offscreen pictures and the
    tests; a ``QApplication`` must exist). Raises ``RuntimeError`` when a frame failed."""
    view = RhiModelView(backend)
    view.state = state
    view.show_scene(scene, files)
    start, image, framed, quiet = time.monotonic(), None, False, 0
    try:
        while time.monotonic() - start < wait:
            image = view.picture(*size)
            r = view.renderer
            if view.failed:
                raise RuntimeError(view.failed)
            # a frame part asks for its files only once what it is made of is in (the old engines' skies: their
            # stages, then their maps), so one quiet frame is not enough: two in a row, nothing loaded in between
            loaded = r.changed() if r is not None else False
            if r is None or r.box is None or loaded or r.busy() or not r.shown_parts_in(state):
                quiet = 0
                time.sleep(0.02)
            elif not framed:
                view.frame_model()
                framed = True
            else:
                quiet += 1
                if quiet == 2:
                    break
        errors = list(view.ctx.errors) if view.ctx is not None else []
    finally:
        view.release()
        view.close()
        view.deleteLater()
    if errors:
        raise RuntimeError("; ".join(sorted(set(errors))))
    return image
