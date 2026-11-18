"""The island view on Qt's QRhi (Direct3D 12, 11, Vulkan or QRhi's OpenGL): ``desktopview``'s controls and
``desktoprender``'s renderer, drawn through ``render.rhidevice`` instead of moderngl.

``make_island_view(backend, parent)`` is the entry: ``IslandView3D`` for ``gl``, ``RhiIslandView3D`` for a QRhi
backend (``render.rhiview.BACKENDS``). A click picks from the id picture as on OpenGL: drawn in a frame of its own
and read back when that frame is done. docs/island-editor.md, "The renderer"; docs/tool-suite.md section 21.
"""

from __future__ import annotations

from PySide6.QtCore import Signal

from ...render import rhiview
from .desktopview import IslandControls, IslandView3D


class RhiIslandView3D(IslandControls, rhiview.RhiView):
    """The view on a QRhi backend, with ``IslandView3D``'s signals."""

    status, selectionChanged, toolChanged, waterChanged = Signal(str), Signal(object), Signal(str), Signal(object)

    def __init__(self, parent=None, backend: str = "d3d12") -> None:
        rhiview.RhiView.__init__(self, backend, parent)
        self._setup()
        self._pick: tuple | None = None  # (size, x, y) to draw the id picture of in the next frame
        self._picked: tuple | None = None  # (its readback, the batches' table)

    def makeCurrent(self) -> None:  # QRhi has no current context: resources are freed at the next frame's end
        pass

    def doneCurrent(self) -> None:
        pass

    def ready(self, ctx) -> None:
        self.renderer = None  # its resources were the old device's (the widget moved to another window)

    def released(self, ctx) -> None:
        """The device goes away (the widget moved to another window, its window closes): the renderer's resources
        are that device's and are released with it, and so is a pick still waiting for its readback."""
        self._pick = self._picked = None
        self._release_renderer()

    def draw(self, ctx, screen, size: tuple[int, int]) -> None:
        self._frame(ctx, screen, *size)
        if self._pick is not None and self.renderer is not None:
            tex, table = self.renderer.pick_draw(self.camera, *self._pick, self.opts)
            self._picked = (ctx.read_later(tex), table)
        self._pick = None

    def gpu_pick(self, size: tuple[int, int], x: float, y: float):
        """The id picture drawn in a frame of its own and read back once that frame is done (QRhi's readbacks
        arrive after the frame: the grab waits for it)."""
        self._pick, self._picked = (size, x, y), None
        if self.isVisible():
            self.grabFramebuffer()
        else:
            self.picture(*size)
        if self._picked is None:
            return None
        result, table = self._picked
        self._picked = None
        data = bytes(result.data)
        return self.renderer.picked(data, table) if len(data) >= 4 else None


def make_island_view(backend: str, parent=None):
    """The island view drawing with ``backend``."""
    return RhiIslandView3D(parent, backend) if rhiview.is_rhi(backend) else IslandView3D(parent)
