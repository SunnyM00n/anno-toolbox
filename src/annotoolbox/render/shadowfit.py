"""The sun's cascades fitted by the game's own analysis on the GPU (``shadow.fx``, technique
``CalculateShadowMatrices``): the depth's range, the splits, each cascade's light-space box and its matrices, written
into the ``cShadowInformation`` buffer the casters and the deferred shading read.

``ShadowFit(rhi, stages)`` is the entry (the compiled compute shaders by function name, ``FUNCTIONS``); ``run(ctx,
depth, size, camera, sun, info)`` records the passes after the G-buffer. ``read_back`` asks for the buffers once
(the tests). What the engine sets on the CPU and the viewer's values: docs/rendering/frame-117-viewer.md section 2.
"""

from __future__ import annotations

from functools import partial

import numpy as np
from PySide6.QtGui import QRhiReadbackResult, QRhiTexture

from . import gameshader as gs
from . import gameshadow
from .gamecompute import ComputeChain, ComputeStage, groups, record, storage_buffer

EFFECT = "data/shaders/fx/shadow.fx"
COPY = "csCopyDepthAfterTransparent"
TECHNIQUE = "CalculateShadowMatrices"
# its passes p0-p4 in order; p5 and p6 (the occupation map and its debug view) serve the terrain's cached shadow
PASSES = ("csClearZReduction", "csReduceZ", "csCalculateSplits", "csReduceLightBounds", "csCalculateShadowMatrices")
# function -> (effect, technique, function): what ``ShaderSource.stage_set`` compiles
STAGES = {COPY: (EFFECT, "CopyDepthAfterTransparent", COPY), **{fn: (EFFECT, TECHNIQUE, fn) for fn in PASSES}}
FUNCTIONS = tuple(STAGES)
TILE = 64  # Z_REDUCTION_TILE_SIZE of the shipped variant: the pixels one group reduces across
COPY_GROUP = 16  # csCopyDepthAfterTransparent's numthreads
BOUNDS_BYTES = 112  # ShadowZBounds: two uints, a uint2, six int[4]


def light_view(sun) -> np.ndarray:
    """``cMtxLightView``: world into the light's view (row vectors), z along the light's travel."""
    r, u, d = gameshadow.light_basis(sun)
    m = np.identity(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = r, u, d
    return m


class ShadowFit(ComputeChain):
    """The analysis passes and their buffers (module docstring)."""

    def __init__(self, rhi, stages: dict) -> None:
        self.rhi = rhi
        self.stages = {name: ComputeStage(rhi, stages[name]) for name in FUNCTIONS}
        self.bounds = storage_buffer(rhi, BOUNDS_BYTES)
        self.last = storage_buffer(rhi, gameshadow.INFO_BYTES * gameshadow.COUNT)  # last frame's splits: no blend
        self.half = None
        self._zeroed = False
        self.read_back = False
        self.readbacks: list[QRhiReadbackResult] = []

    def _half(self, ctx, size: tuple[int, int]):
        """The half-size depth the analysis reads every fourth pixel from (the game's is r16 unorm; ours r32 float)."""
        half = (groups(size[0], 2), groups(size[1], 2))
        if self.half is None or self.half.size != half:
            if self.half is not None:
                self.half.release()
            self.half = ctx.native_texture(half, QRhiTexture.Format.R32F, storage=True)
        return self.half

    def _fill(self, c: gs.Constants, camera: gs.Camera, sun, size: tuple[int, int]) -> None:
        gs.fill_frame(c, camera)
        gameshadow.fill(c, gameshadow.Cascades((), np.zeros(3), camera.shadow_distance, gpu=True))
        view = light_view(sun)
        c.set("cMtxLightView", view)
        c.set("cMtxLightViewInv", np.linalg.inv(view))
        c.set("cDepthViewport", [0, 0, size[0], size[1]])
        c.set_rows("cPartitionInfo", [[w, h, x, y] for x, y, w, h in gameshadow.DIVISIONS])

    def run(self, ctx, depth, size: tuple[int, int], camera: gs.Camera, sun, info) -> None:
        """The copy of the depth to half size, then p0-p4 over ``depth`` (the G-buffer's) into ``info`` (the
        ``cShadowInformation`` buffer), each dispatch a compute pass of its own."""
        half = self._half(ctx, size)
        inputs = {"cDepthCopySrc": depth.native, "cDepthCopyDst": half.native, "cDepthTexture": depth.native,
                  "cDepthTexAfterTransparent": half.native, "cZBoundsOut": self.bounds,
                  "cShadowInformationUAV": info, "cShadowInfoLastFrame": self.last}  # fmt: skip
        tiles = (groups(size[0], TILE), groups(size[1], TILE), 1)
        copy = (groups(half.size[0], COPY_GROUP), groups(half.size[1], COPY_GROUP), 1)
        where = {COPY: copy, "csReduceZ": tiles, "csReduceLightBounds": tiles}
        for name in FUNCTIONS:
            after = partial(self._readback, info) if name == FUNCTIONS[-1] and self.read_back else None
            record(ctx, self.stages[name], inputs, lambda c: self._fill(c, camera, sun, size),
                   where.get(name, (1, 1, 1)), after, self._zero_last)  # fmt: skip

    def _zero_last(self, batch) -> None:
        """Last frame's splits start as zeros (no blend), uploaded once with the first pass."""
        if not self._zeroed:
            batch.uploadStaticBuffer(self.last, bytes(gameshadow.INFO_BYTES * gameshadow.COUNT))
            self._zeroed = True

    def _readback(self, info):
        """The bounds and the information as the analysis left them, read once (they arrive after the frame)."""
        self.read_back = False
        batch = self.rhi.nextResourceUpdateBatch()
        self.readbacks = [QRhiReadbackResult(), QRhiReadbackResult()]
        batch.readBackBuffer(self.bounds, 0, BOUNDS_BYTES, self.readbacks[0])
        batch.readBackBuffer(info, 0, gameshadow.INFO_BYTES * gameshadow.COUNT, self.readbacks[1])
        return batch

    def release(self) -> None:
        super().release()
        for b in (self.bounds, self.last):
            b.destroy()
        if self.half is not None:
            self.half.release()
            self.half = None
