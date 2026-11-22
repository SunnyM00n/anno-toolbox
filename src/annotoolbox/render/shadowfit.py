"""The sun's cascades fitted by the game's own analysis on the GPU (``shadow.fx``, technique
``CalculateShadowMatrices``): the depth's range, the splits, each cascade's light-space box and its matrices, written
into the ``cShadowInformation`` buffer the casters and the deferred shading read.

``CShadowFit(rhi, stages)`` is the entry (the compiled compute shaders by function name, ``FUNCTIONS``); ``run(ctx,
depth, size, camera, sun, info)`` records the passes after the G-buffer. ``read_back`` asks for the buffers once
(the tests). What the engine sets on the CPU and the viewer's values: docs/rendering/frame-117-viewer.md section 2.
"""

from __future__ import annotations

from functools import partial

import numpy as np
from PySide6.QtGui import QRhiReadbackResult, QRhiTexture

from . import gameshader as gs
from . import gameshadow
from .gamecompute import CComputeChain, CComputeStage, groups, record, storage_buffer

EFFECT = "data/shaders/fx/shadow.fx"
COPY = "csCopyDepthAfterTransparent"
TECHNIQUE = "CalculateShadowMatrices"
# its passes p0-p4 in order; p5 and p6 (the occupation map and its debug view) serve the terrain's cached shadow
PASSES = ("csClearZReduction", "csReduceZ", "csCalculateSplits", "csReduceLightBounds", "csCalculateShadowMatrices")
# function -> (effect, technique, function): what ``ShaderSource.stage_set`` compiles
STAGES = {
    COPY: (EFFECT, "CopyDepthAfterTransparent", COPY),
    **{function: (EFFECT, TECHNIQUE, function) for function in PASSES},
}
FUNCTIONS = tuple(STAGES)
TILE = 64  # Z_REDUCTION_TILE_SIZE of the shipped variant: the pixels one group reduces across
COPY_GROUP = 16  # csCopyDepthAfterTransparent's numthreads
BOUNDS_BYTES = 112  # ShadowZBounds: two uints, a uint2, six int[4]


def light_view(sun) -> np.ndarray:
    """``cMtxLightView``: world into the light's view (row vectors), z along the light's travel."""
    right, up, direction = gameshadow.light_basis(sun)
    matrix = np.identity(4)
    matrix[:3, 0], matrix[:3, 1], matrix[:3, 2] = right, up, direction
    return matrix


def fill(constants: gs.CConstants, camera: gs.SCamera, sun, size_px: tuple[int, int]) -> None:
    """The analysis's constants by name: the camera, the cascades' reach, the light's view, the depth's viewport and
    the cascades' parts of the map (the native fit's too: ``atmospherenative.CNativeFit``)."""
    gs.fill_frame(constants, camera)
    gameshadow.fill(constants, gameshadow.SCascades((), np.zeros(3), camera.shadow_distance, gpu=True))
    view = light_view(sun)
    constants.set("cMtxLightView", view)
    constants.set("cMtxLightViewInv", np.linalg.inv(view))
    constants.set("cDepthViewport", [0, 0, size_px[0], size_px[1]])
    constants.set_rows(
        "cPartitionInfo", [[part_width, part_height, x, y] for x, y, part_width, part_height in gameshadow.DIVISIONS]
    )


class CShadowFit(CComputeChain):
    """The analysis passes and their buffers (module docstring)."""

    def __init__(self, rhi, stages: dict) -> None:
        self.m_rhi = rhi
        self.m_stages = {name: CComputeStage(rhi, stages[name]) for name in FUNCTIONS}
        self.m_bounds = storage_buffer(rhi, BOUNDS_BYTES)
        self.m_last = storage_buffer(rhi, gameshadow.INFO_BYTES * gameshadow.COUNT)  # last frame's splits: no blend
        self.m_half = None
        self._m_zeroed = False
        self.m_read_back = False
        self.m_readbacks: list[QRhiReadbackResult] = []

    def _half(self, ctx, size_px: tuple[int, int]):
        """The half-size depth the analysis reads every fourth pixel from (the game's is r16 unorm; ours r32 float)."""
        half = (groups(size_px[0], 2), groups(size_px[1], 2))
        if self.m_half is None or self.m_half.size != half:
            if self.m_half is not None:
                self.m_half.release()
            self.m_half = ctx.native_texture(half, QRhiTexture.Format.R32F, storage=True)
        return self.m_half

    def run(self, ctx, depth, size_px: tuple[int, int], camera: gs.SCamera, sun, shadow_information) -> None:
        """The copy of the depth to half size, then p0-p4 over ``depth`` (the G-buffer's) into ``info`` (the
        ``cShadowInformation`` buffer), each dispatch a compute pass of its own."""
        half = self._half(ctx, size_px)
        inputs = {"cDepthCopySrc": depth.native, "cDepthCopyDst": half.native, "cDepthTexture": depth.native,
                  "cDepthTexAfterTransparent": half.native, "cZBoundsOut": self.m_bounds,
                  "cShadowInformationUAV": shadow_information, "cShadowInfoLastFrame": self.m_last}  # fmt: skip
        tiles = (groups(size_px[0], TILE), groups(size_px[1], TILE), 1)
        copy = (groups(half.size[0], COPY_GROUP), groups(half.size[1], COPY_GROUP), 1)
        where = {COPY: copy, "csReduceZ": tiles, "csReduceLightBounds": tiles}
        for name in FUNCTIONS:
            after = partial(self._readback, shadow_information) if name == FUNCTIONS[-1] and self.m_read_back else None
            record(ctx, self.m_stages[name], inputs, lambda constants: fill(constants, camera, sun, size_px),
                   where.get(name, (1, 1, 1)), after, self._zero_last)  # fmt: skip

    def _zero_last(self, batch) -> None:
        """Last frame's splits start as zeros (no blend), uploaded once with the first pass."""
        if not self._m_zeroed:
            batch.uploadStaticBuffer(self.m_last, bytes(gameshadow.INFO_BYTES * gameshadow.COUNT))
            self._m_zeroed = True

    def _readback(self, shadow_information):
        """The bounds and the information as the analysis left them, read once (they arrive after the frame)."""
        self.m_read_back = False
        batch = self.m_rhi.nextResourceUpdateBatch()
        self.m_readbacks = [QRhiReadbackResult(), QRhiReadbackResult()]
        batch.readBackBuffer(self.m_bounds, 0, BOUNDS_BYTES, self.m_readbacks[0])
        batch.readBackBuffer(shadow_information, 0, gameshadow.INFO_BYTES * gameshadow.COUNT, self.m_readbacks[1])
        return batch

    def release(self) -> None:
        super().release()
        for buffer in (self.m_bounds, self.m_last):
            buffer.destroy()
        if self.m_half is not None:
            self.m_half.release()
            self.m_half = None
