"""What Anno 117's water shader (``model_water.fx``) reads besides the frame's light: the copies of the lit scene
the engine makes before its water (the lit colour, and the depth as view distance by the game's own
``csDepthCopyToViewZ``), the global foam and noise maps from the install, the water's constants.

``CSceneCopies(rhi, stages, maps)`` is the entry (the compiled copy shader, ``STAGES``; the maps as float arrays,
``MAPS``); ``run(ctx, lit, depth, size_px, camera)`` records the copies after the deferred shading, ``inputs`` hands
them (and the ocean's ``waves``) by name to the transparent draws, ``fill`` writes the constants.
docs/rendering/frame-117-viewer.md sections 7 and 10.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .gamebind import keyed
from .gamecompute import CComputeChain, CComputeStage, copy_into, groups, record
from .gamedraw import upload_level

STAGES = {"depth": ("data/shaders/fx/copy.fx", "DepthCopyToViewZ", "csDepthCopyToViewZ")}
ENGINE = "data/base/graphics/engine/"
# the global maps, chosen by their channels: which files the engine binds is not written anywhere read [I]
MAPS = {"cWaterFoamMap": f"{ENGINE}water/foam_0.dds", "cNoiseTex2": f"{ENGINE}noise/noise01_0.dds"}
GROUP = 8  # csDepthCopyToViewZ's numthreads
# without the ocean simulation the wave gradients are zero, this only scales them (a zero divides by zero) [ours]
CONSTANTS = {"cBand3PatchDims": 10.0}


class CSceneCopies(CComputeChain):
    """The two copies and the maps (module docstring)."""

    def __init__(self, rhi, stages: dict, maps: dict) -> None:
        self.m_rhi = rhi
        self.m_stages = {key: CComputeStage(rhi, stages[key]) for key in STAGES}
        self.m_maps = {name: self._map(image) for name, image in maps.items()}
        self._m_pending = dict(maps)  # uploaded with the first run
        self.m_color = self.m_viewz = None
        self.m_size_px: tuple[int, int] | None = None
        self.m_waves: dict = {}  # the ocean simulation's arrays by name (gameocean), empty without it
        self.m_constants: dict = dict(CONSTANTS)
        self.m_native = None  # copiesnative.CNativeCopies: the depth copy by the shipped shader (None: ours)

    def _map(self, image):
        texture = self.m_rhi.newTexture(QRhiTexture.Format.RGBA16F, QSize(image.shape[1], image.shape[0]))
        texture.create()
        return texture

    def _targets(self, ctx, size_px: tuple[int, int]) -> None:
        if self.m_size_px == size_px:
            return
        self._drop()
        self.m_color = ctx.native_texture(size_px, QRhiTexture.Format.RGBA16F)
        self.m_viewz = ctx.native_texture(size_px, QRhiTexture.Format.R32F, storage=True)
        self.m_size_px = size_px

    def inputs(self) -> dict:
        """The copies and the maps by the names the water shader reads (empty before the first run)."""
        if self.m_color is None:
            return {}
        return {"cColorBufferCopy": self.m_color.native, "cDepthBufferCopy": self.m_viewz.native, **self.m_maps,
                **self.m_waves}  # fmt: skip

    def fill(self, constants: gs.CConstants) -> None:
        """The water's constants into a transparent draw's buffer: the ocean's band sizes, else ours."""
        for key, value in self.m_constants.items():
            constants.set(key, value)

    def run(self, ctx, lit, depth, size_px: tuple[int, int], camera: gs.SCamera) -> None:
        """The depth as view distance (the game's compute shader over ``depth``, the G-buffer's), then the lit
        colour (``lit``, after the deferred shading) copied: one compute pass ending with the copy."""
        native = self.m_native
        if native is not None and not native.m_error:
            native.run(ctx, self, lit, depth, size_px, camera)
            return
        self._targets(ctx, size_px)
        inputs = {"cSourceDepth": depth.native, "cDstViewZ": self.m_viewz.native}

        def after():
            return copy_into(self.m_rhi, self.m_color.native, lit.native, size_px)

        framed = keyed(lambda constants: gs.fill_frame(constants, camera), ("frame", gs.camera_key(camera)))
        record(ctx, self.m_stages["depth"], inputs, framed,
               (groups(size_px[0], GROUP), groups(size_px[1], GROUP), 1), after, self._upload)  # fmt: skip

    def _upload(self, batch) -> None:
        """The maps' pixels, once (half floats, level 0)."""
        for name, image in self._m_pending.items():
            upload_level(batch, self.m_maps[name], image.astype("f2").tobytes())
        self._m_pending = {}

    def _drop(self) -> None:
        for texture in (self.m_color, self.m_viewz):
            if texture is not None:
                texture.release()
        self.m_color = self.m_viewz = None
        self.m_size_px = None

    def release(self) -> None:
        super().release()
        self._drop()
        for texture in self.m_maps.values():
            texture.destroy()
        self.m_maps = {}
