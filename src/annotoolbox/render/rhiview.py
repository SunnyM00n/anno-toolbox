"""The backend setting and the QRhi view: which device a 3D view draws with, and a ``QRhiWidget`` that hands each
frame to a renderer through ``rhidevice.RhiContext``.

``chosen(setting)`` is the backend in use (``ANNO_RENDERER`` over the setting over Direct3D 12 where there is one,
else ``DEFAULT``); ``gl`` is the OpenGL device (moderngl, the views' own ``QOpenGLWidget``), the others are QRhi's
backends. ``RhiView`` is the
widget: a subclass draws in ``draw(ctx, screen, size)``; ``picture()`` draws one frame without showing the widget
(the offscreen pictures and the tests). docs/tool-suite.md section 21, "The device layer"; docs/viewer.md.
"""

from __future__ import annotations

import functools
import os
import sys
import traceback

import shiboken6
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QOpenGLContext, QRhi, QRhiGles2InitParams, QSurfaceFormat
from PySide6.QtWidgets import QRhiWidget

from .rhidevice import RhiContext
from .shaderbake import TARGETS

ENV = "ANNO_RENDERER"
BACKENDS = ("gl", "d3d12", "d3d11", "vulkan", "opengl")  # gl: moderngl; opengl: QRhi's OpenGL backend
DEFAULT = "gl"
_API = {
    "d3d12": QRhiWidget.Api.Direct3D12,
    "d3d11": QRhiWidget.Api.Direct3D11,
    "vulkan": QRhiWidget.Api.Vulkan,
    "opengl": QRhiWidget.Api.OpenGL,
}


def chosen(setting: str = "") -> str:
    """The backend a view uses: ``ANNO_RENDERER``, else the setting, else Direct3D 12 where the machine can make a
    device of it (117's islands open in the game's look there: island-117-view.md "Speed", part 7), else
    ``DEFAULT``; an unknown name counts as none."""
    for name in (os.environ.get(ENV, ""), setting):
        if name.lower() in BACKENDS:
            return name.lower()
    return "d3d12" if _has_d3d12() else DEFAULT


@functools.cache
def _has_d3d12() -> bool:
    """Whether a Direct3D 12 device can be made (probed once a session)."""
    try:
        return available("d3d12")
    except Exception:  # no QRhi device at all: the default
        return False


def is_rhi(name: str) -> bool:
    return name in _API


def api_of(name: str) -> str:
    """The API a backend composes its window with: a window keeps the first it shows (``gl`` and ``opengl`` share
    OpenGL), so a view can switch only between backends of one API while its window is open."""
    return "opengl" if name in ("gl", "opengl") else name


def composable(name: str, hosts_gl: bool) -> str:
    """The backend a view can use in its window: one that also holds OpenGL views (the island editor's) composes
    only OpenGL, so Direct3D and Vulkan become QRhi's OpenGL backend there."""
    return "opengl" if hosts_gl and name in ("d3d12", "d3d11", "vulkan") else name


def available(name: str) -> bool:
    """Whether this machine can draw with the backend (a device of it can be made, for OpenGL one that runs our
    shaders); ``gl`` is not probed here."""
    if name in ("d3d12", "d3d11") and sys.platform == "win32":
        from PySide6.QtGui import QRhiD3D11InitParams, QRhiD3D12InitParams  # Qt's Windows builds only

        if name == "d3d12":
            return QRhi.probe(QRhi.Implementation.D3D12, QRhiD3D12InitParams())
        return QRhi.probe(QRhi.Implementation.D3D11, QRhiD3D11InitParams())
    if name == "opengl":
        params = QRhiGles2InitParams()
        params.fallbackSurface = QRhiGles2InitParams.newFallbackSurface()
        return QRhi.probe(QRhi.Implementation.OpenGLES2, params) and _opengl_runs_our_glsl()
    if name == "vulkan":
        return True  # QRhi needs a Vulkan instance first; the widget says when it cannot make one
    return False


def _opengl_runs_our_glsl() -> bool:
    """Whether QRhi's OpenGL context is desktop OpenGL of the GLSL version ``shaderbake`` bakes: an older driver
    (Qt's software OpenGL is 3.0) makes the device but refuses every pipeline of ours."""
    version = int(TARGETS[TARGETS.index("--glsl") + 1])
    ctx = QOpenGLContext()
    ctx.setFormat(QSurfaceFormat.defaultFormat())  # what the backend asks for
    if not ctx.create():
        return False
    fmt = ctx.format()
    return not ctx.isOpenGLES() and (fmt.majorVersion(), fmt.minorVersion()) >= divmod(version // 10, 10)


class RhiView(QRhiWidget):
    """A 3D view on a QRhi backend; ``draw`` is the subclass's frame, ``failed`` the first error a frame raised."""

    def __init__(self, backend: str = "d3d12", parent=None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.setApi(_API[backend])
        self.ctx: RhiContext | None = None
        self.failed = ""
        self._device = None

    def initialize(self, cb) -> None:
        device = shiboken6.getCppPointer(self.rhi())[0]
        if self.ctx is None or device != self._device:
            self.ctx, self._device = RhiContext(self.rhi()), device
            self.ready(self.ctx)

    def ready(self, ctx: RhiContext) -> None:
        """The device is made (again, after the widget moved to another window): the subclass makes its renderer."""

    def render(self, cb) -> None:
        if self.ctx is None:
            return
        screen = self.ctx.begin(cb, self.colorTexture())
        size = self.colorTexture().pixelSize()
        for step in (lambda: self.draw(self.ctx, screen, (size.width(), size.height())), self.ctx.end):
            try:
                step()
            except Exception as exc:  # an error inside Qt's call would vanish: keep it for the view to show
                if not self.failed:
                    self.failed = f"{type(exc).__name__}: {exc}"
                    traceback.print_exc()

    def draw(self, ctx: RhiContext, screen, size: tuple[int, int]) -> None:
        """One frame into ``screen`` (``size`` pixels)."""

    def picture(self, width: int, height: int):
        """One frame at ``width`` x ``height`` as a ``QImage``: a standalone view is drawn off screen; a view hidden
        inside a window (another app's page in front) gives None and keeps its place and size."""
        if not self.isVisible():
            if self.parentWidget() is not None:
                return None
            self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)  # never shown: one device for every grab
            self.resize(width, height)
            self.show()
        self.setFixedColorBufferSize(width, height)
        try:
            return self.grabFramebuffer()
        finally:
            self.setFixedColorBufferSize(QSize())  # the widget's own size again for the frames on screen
