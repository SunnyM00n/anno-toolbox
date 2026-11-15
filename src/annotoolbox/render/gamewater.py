"""What Anno 117's water shader (``model_water.fx``) reads besides the frame's light: the copies of the lit scene
the engine makes before its water (the lit colour, and the depth as view distance by the game's own
``csDepthCopyToViewZ``), the global foam and noise maps from the install, the water's constants.

``SceneCopies(rhi, stages, maps)`` is the entry (the compiled copy shader, ``STAGES``; the maps as float arrays,
``MAPS``); ``run(ctx, lit, depth, size, camera)`` records the copies after the deferred shading, ``inputs`` hands
them (and the ocean's ``waves``) by name to the transparent draws, ``fill`` writes the constants.
docs/rendering/frame-117-viewer.md sections 7 and 10.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .gamecompute import ComputeChain, ComputeStage, copy_into, groups, record
from .gamedraw import upload_level

STAGES = {"depth": ("data/shaders/fx/copy.fx", "DepthCopyToViewZ", "csDepthCopyToViewZ")}
ENGINE = "data/base/graphics/engine/"
# the global maps, chosen by their channels: which files the engine binds is not written anywhere read [I]
MAPS = {"cWaterFoamMap": f"{ENGINE}water/foam_0.dds", "cNoiseTex2": f"{ENGINE}noise/noise01_0.dds"}
GROUP = 8  # csDepthCopyToViewZ's numthreads
# without the ocean simulation the wave gradients are zero, this only scales them (a zero divides by zero) [ours]
CONSTANTS = {"cBand3PatchDims": 10.0}


class SceneCopies(ComputeChain):
    """The two copies and the maps (module docstring)."""

    def __init__(self, rhi, stages: dict, maps: dict) -> None:
        self.rhi = rhi
        self.stages = {key: ComputeStage(rhi, stages[key]) for key in STAGES}
        self.maps = {name: self._map(img) for name, img in maps.items()}
        self._pending = dict(maps)  # uploaded with the first run
        self.color = self.viewz = None
        self.size: tuple[int, int] | None = None
        self.waves: dict = {}  # the ocean simulation's arrays by name (gameocean), empty without it
        self.constants: dict = dict(CONSTANTS)

    def _map(self, img):
        tex = self.rhi.newTexture(QRhiTexture.Format.RGBA16F, QSize(img.shape[1], img.shape[0]))
        tex.create()
        return tex

    def _targets(self, ctx, size: tuple[int, int]) -> None:
        if self.size == size:
            return
        self._drop()
        self.color = ctx.native_texture(size, QRhiTexture.Format.RGBA16F)
        self.viewz = ctx.native_texture(size, QRhiTexture.Format.R32F, storage=True)
        self.size = size

    def inputs(self) -> dict:
        """The copies and the maps by the names the water shader reads (empty before the first run)."""
        if self.color is None:
            return {}
        return {"cColorBufferCopy": self.color.native, "cDepthBufferCopy": self.viewz.native, **self.maps,
                **self.waves}  # fmt: skip

    def fill(self, c: gs.Constants) -> None:
        """The water's constants into a transparent draw's buffer: the ocean's band sizes, else ours."""
        for key, value in self.constants.items():
            c.set(key, value)

    def run(self, ctx, lit, depth, size: tuple[int, int], camera: gs.Camera) -> None:
        """The depth as view distance (the game's compute shader over ``depth``, the G-buffer's), then the lit
        colour (``lit``, after the deferred shading) copied: one compute pass ending with the copy."""
        self._targets(ctx, size)
        inputs = {"cSourceDepth": depth.native, "cDstViewZ": self.viewz.native}

        def after():
            return copy_into(self.rhi, self.color.native, lit.native, size)

        record(ctx, self.stages["depth"], inputs, lambda c: gs.fill_frame(c, camera),
               (groups(size[0], GROUP), groups(size[1], GROUP), 1), after, self._upload)  # fmt: skip

    def _upload(self, batch) -> None:
        """The maps' pixels, once (half floats, level 0)."""
        for name, img in self._pending.items():
            upload_level(batch, self.maps[name], img.astype("f2").tobytes())
        self._pending = {}

    def _drop(self) -> None:
        for t in (self.color, self.viewz):
            if t is not None:
                t.release()
        self.color = self.viewz = None
        self.size = None

    def release(self) -> None:
        super().release()
        self._drop()
        for tex in self.maps.values():
            tex.destroy()
        self.maps = {}
