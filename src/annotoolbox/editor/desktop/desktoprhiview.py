"""The island view on Qt's QRhi (Direct3D 12, 11, Vulkan or QRhi's OpenGL): ``desktopview``'s controls and
``desktoprender``'s renderer, drawn through ``render.rhidevice`` instead of moderngl.

``make_island_view(backend, parent, is_own_window)`` is the entry: ``CIslandView3D`` for ``gl``, ``CRhiIslandView3D``
for a QRhi backend (``render.rhiview.BACKENDS``), ``CWindowIslandView3D`` in a window of its own (``render.rhiwindow``).
A click picks from the id picture as on OpenGL: drawn in a frame of its own and read back when that frame is done.
docs/island-editor.md, "The renderer"; docs/tool-suite.md section 21; docs/rendering/performance.md section 6.7.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Signal

from ...render import rhiview, rhiwindow
from .desktopview import CIslandControls, CIslandView3D


class CRhiIslandControls(CIslandControls):
    """What both QRhi views share: the frame, the device's coming and going and the click's id picture."""

    def _setup_rhi(self) -> None:
        self._setup()
        self._m_pick: tuple | None = None  # (size, x, y) to draw the id picture of in the next frame
        self._m_picked: tuple | None = None  # (its readback, the batches' table)

    def makeCurrent(self) -> None:  # QRhi has no current context: resources are freed at the next frame's end
        pass

    def doneCurrent(self) -> None:
        pass

    def ready(self, ctx) -> None:
        self.m_renderer = None  # its resources were the old device's (the widget moved to another window)

    def released(self, ctx) -> None:
        """The device goes away (the widget moved to another window, its window closes): the renderer's resources
        are that device's and are released with it, and so is a pick still waiting for its readback."""
        self._m_pick = self._m_picked = None
        self._release_renderer()

    def draw(self, ctx, screen, size_px: tuple[int, int]) -> None:
        self._frame(ctx, screen, *size_px)
        if self._m_pick is not None and self.m_renderer is not None:
            texture, table = self.m_renderer.pick_draw(self.m_camera, *self._m_pick, self.m_opts)
            self._m_picked = (ctx.read_later(texture), table)
        self._m_pick = None

    def gpu_pick(self, size_px: tuple[int, int], x: float, y: float):
        """The id picture drawn in a frame of its own and read back once that frame is done (QRhi's readbacks
        arrive after the frame: the grab waits for it)."""
        self._m_pick, self._m_picked = (size_px, x, y), None
        if self.isVisible():
            self.grabFramebuffer()
        else:
            self.picture(*size_px)
        if self._m_picked is None:
            return None
        result, table = self._m_picked
        self._m_picked = None
        data = bytes(result.data)
        return self.m_renderer.picked(data, table) if len(data) >= 4 else None


class CRhiIslandView3D(CRhiIslandControls, rhiview.CRhiView):
    """The view on a QRhi backend as a ``QRhiWidget``, with ``CIslandView3D``'s signals."""

    status, selectionChanged, toolChanged, waterChanged = Signal(str), Signal(object), Signal(str), Signal(object)

    def __init__(self, parent=None, backend: str = "d3d12") -> None:
        rhiview.CRhiView.__init__(self, backend, parent)
        self._setup_rhi()


class CWindowIslandView3D(CRhiIslandControls, rhiwindow.CRhiWindowView):
    """The view in a window of its own, with ``CIslandView3D``'s signals: a frame's recording overlaps the GPU's
    work on the frame before it."""

    status, selectionChanged, toolChanged, waterChanged = Signal(str), Signal(object), Signal(str), Signal(object)

    def __init__(self, parent=None, backend: str = "d3d12") -> None:
        rhiwindow.CRhiWindowView.__init__(self, backend, parent)
        self._setup_rhi()

    def frame_now(self) -> None:
        """A frame, timed as the widget's paint is: the window's frames are no paint events, and a slow one is
        followed by a rest all the same (``CIslandControls._resting``)."""
        start = time.monotonic()
        rhiwindow.CRhiWindowView.frame_now(self)
        self._frame_took(start)


def make_island_view(backend: str, parent=None, is_own_window: bool = False):
    """The island view drawing with ``backend``; ``is_own_window``: in a window of its own where one can be made
    (Direct3D 12 on Windows' own platform), else as a widget."""
    if is_own_window and rhiwindow.is_possible(backend):
        return CWindowIslandView3D(parent, backend)
    return CRhiIslandView3D(parent, backend) if rhiview.is_rhi(backend) else CIslandView3D(parent)
