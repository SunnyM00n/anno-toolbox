"""The island view on Qt's QRhi (Direct3D 12, 11, Vulkan or QRhi's OpenGL): ``desktopview``'s controls and
``desktoprender``'s renderer, drawn through ``render.rhidevice`` instead of moderngl.

``make_island_view(backend, parent)`` is the entry: ``CIslandView3D`` for ``gl``, ``CRhiIslandView3D`` for a QRhi
backend (``render.rhiview.BACKENDS``). A click picks from the id picture as on OpenGL: drawn in a frame of its own
and read back when that frame is done. docs/island-editor.md, "The renderer"; docs/tool-suite.md section 21.
"""

from __future__ import annotations

from PySide6.QtCore import Signal

from ...render import rhiview
from .desktopview import CIslandControls, CIslandView3D


class CRhiIslandView3D(CIslandControls, rhiview.CRhiView):
    """The view on a QRhi backend, with ``CIslandView3D``'s signals."""

    status, selectionChanged, toolChanged, waterChanged = Signal(str), Signal(object), Signal(str), Signal(object)

    def __init__(self, parent=None, backend: str = "d3d12") -> None:
        rhiview.CRhiView.__init__(self, backend, parent)
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


def make_island_view(backend: str, parent=None):
    """The island view drawing with ``backend``."""
    return CRhiIslandView3D(parent, backend) if rhiview.is_rhi(backend) else CIslandView3D(parent)
