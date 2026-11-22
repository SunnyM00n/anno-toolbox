"""Anno 1800's small ocean waves by its own compute shader (``water_ocean.fx``'s ``OceanGradients``): the engine's two
animated displacement volumes, read from the install, sampled at the wave time into the gradient and fold map the
model water reads as ``cSmallWaveGradientMap`` when its material asks for it (``USE_SMALL_WAVE_TEXTURE``).

``compile_stage(shaders)`` and ``read_volumes(files)`` run in the background; ``CSmallWaves1800(ctx, stage, volumes)``'s
``run(ctx, seconds)`` records the pass and returns the map. docs/rendering/shader-compile.md section 11, "1800's small
ocean waves".
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ..models import ddsdecode
from . import gameshader as gs
from .fxcdraw import volume_of
from .gamecompute import CComputeChain, CComputeStage, groups, record

EFFECT = "data/shaders/fx/water_ocean.fx"
TECHNIQUE = "OceanGradients"
SIZE, GROUP = 512, 16  # FFT_MAP_SIZE, FFT_GRADIENTS_THREAD_GROUPS
# the engine's define list of the ocean's effect (taken from how the game behaves)
DEFINES = (("CS_BLOCK_SIZE_X", "16"), ("CS_BLOCK_SIZE_Y", "16"), ("COHERENCY_GRANULARITY", "128"),
           ("FFT_MAP_SIZE", "512.0"), ("FFT_MAP_SIZE_DIV", "256.0"), ("ACTUAL_DIM", "512"), ("IN_WIDTH", "516"),
           ("OUT_WIDTH", "512"), ("OUT_HEIGHT", "512"), ("ADDRESS_OFFSET_DX", "262144"),
           ("ADDRESS_OFFSET_DY", "524288"), ("PATCH_BLEND_BEGIN", "35.0"), ("PATCH_BLEND_END", "320.0"),
           ("FFT_WRAPUP_THREAD_GROUPS", "32"), ("FFT_GRADIENTS_THREAD_GROUPS", str(GROUP)))  # fmt: skip
WATER = "data/graphics/effects/water"
# the volume each name binds: (file, channels kept) - the horizontal displacement's red and green, the height's red
VOLUMES = {"cSmallWaveDisplacementMapXZ": (f"{WATER}/waves_small_xz.dds", 4),
           "cSmallWaveDisplacementMapY": (f"{WATER}/waves_small_y.dds", 1)}  # fmt: skip
MAP = "cSmallWaveGradientMap"
SWITCH = "USE_SMALL_WAVE_TEXTURE"
WAVE_TIME_START, WAVE_TIME_RATE = 5.0, 0.1  # cWaveTime: 5 at the start, 0.1 a second, never wrapped
GRID_LEN = 512 / 4096  # cGridLen: the map's size over the ocean patch's


def compile_stage(shaders):
    """In the background: the gradient pass's compute stage."""
    return shaders.call_stage(EFFECT, DEFINES, TECHNIQUE)


def read_volumes(files) -> dict:
    """In the background: each volume's slices as half floats by the name it binds (RGBA for the horizontal one, one
    channel for the height): their signed BC6H decoded, which QRhi's unsigned BC6H would misread."""
    out = {}
    for name, (path, channels) in VOLUMES.items():
        dds = ddsdecode.CDdsFile(files.read(path))
        out[name] = [dds.level_float(0, 0, z)[..., :channels].astype(np.float16) for z in range(dds.slices(0))]
    return out


def wave_time(seconds: float) -> float:
    """``cWaveTime`` after ``seconds`` of the view's clock."""
    return WAVE_TIME_START + WAVE_TIME_RATE * float(seconds)


class CSmallWaves1800(CComputeChain):
    """The volumes on the device, the gradient pass and its target with its mips (module docstring)."""

    def __init__(self, ctx, stage, volumes: dict) -> None:
        self.m_rhi = rhi = ctx.m_rhi
        self.m_stages = {"gradients": CComputeStage(rhi, stage)}
        texture_format = QRhiTexture.Format
        self.m_volumes = {name: volume_of(ctx, slices, texture_format.RGBA16F if VOLUMES[name][1] == 4 else texture_format.R16F)
                        for name, slices in volumes.items()}  # fmt: skip
        flags = QRhiTexture.Flag
        self.m_map = rhi.newTexture(texture_format.RGBA16F, QSize(SIZE, SIZE), 1,
                                  flags.MipMapped | flags.UsedWithGenerateMips | flags.UsedWithLoadStore)  # fmt: skip
        self.m_map.create()

    def _mips(self):
        batch = self.m_rhi.nextResourceUpdateBatch()
        batch.generateMips(self.m_map)
        return batch

    def run(self, ctx, seconds: float):
        """The gradients at the wave time of ``seconds``, their mips made after; returns the map."""
        inputs = {**self.m_volumes, "cDstDataTex": self.m_map}
        values = {"cWaveTime": wave_time(seconds), "cGridLen": GRID_LEN}
        group_count = groups(SIZE, GROUP)
        record(
            ctx,
            self.m_stages["gradients"],
            inputs,
            lambda constants: gs.set_values([constants], values),
            (group_count, group_count, 1),
            self._mips,
        )
        return self.m_map

    def release(self) -> None:
        super().release()
        for texture in (*self.m_volumes.values(), self.m_map):
            texture.destroy()
        self.m_volumes = {}
