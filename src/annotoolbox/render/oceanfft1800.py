"""Anno 1800's large ocean waves by its own compute shaders (``water_ocean.fx``): the setting's wave spectrum once
(``InitWaveHeightMap``), its state at the wave time each frame (``UpdateWaveSpectrumCS``), the inverse FFT of
512 x 512 in six radix-8 passes (``FftCS1``, ``FftCS2``), the displacement map (``OceanFFTWrapUp``) and its gradients
(``OceanGradients2``): the surface's ``cLargeWaveDisplacementMap`` and ``cLargeWaveGradientMap``.

``compile_stages(source)`` runs in the background; ``CLargeWaves1800(ctx, stages, water)``'s ``run(ctx, seconds)``
records the passes and returns the two maps. The engine's values: docs/rendering/island-1800-2205-view.md 17.
"""

from __future__ import annotations

import math
import re

from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from . import terrain1800, waves1800
from .gamecompute import CComputeChain, CComputeStage, groups, record, storage_buffer

EFFECT = waves1800.EFFECT
TECHNIQUES = {"init": "InitWaveHeightMap", "update": "UpdateWaveSpectrumCS", "fft": "FftCS1", "last": "FftCS2",
              "wrap": "OceanFFTWrapUp", "gradients": "OceanGradients2"}  # fmt: skip
SIDE, IN_WIDTH, SLICES = 512, 516, 3  # ACTUAL_DIM, IN_WIDTH; the height and the two choppy offsets
GROUP, WRAP_GROUP, LINE = 16, 32, 128  # CS_BLOCK_SIZE, FFT_WRAPUP_THREAD_GROUPS, COHERENCY_GRANULARITY
# cbHeightMapInit as the engine fills it from the simulator: the patch, the amplitude x 1e-8, the setting's wave
# size as the wind's speed, its damping, the smallest wave 1000 (never changed), the seed x 10
PATCH, AMPLITUDE_SCALE, MIN_WAVE, SEED_SCALE = 4096.0, 1e-8, 1000.0, 10
WIND = (1.0, 0.0)  # cWindDirection in the view's axes [ours: the session's wind not read]
WATER = "constants_water.fxh"
BUFFERS, FLOATS = (b"cSrcData", b"cDstData", b"cSrcOmega", b"cDstOmega"), (b"cSrcOmega", b"cDstOmega")
_NAMES = b"|".join(BUFFERS)
_DECLARED_RE = re.compile(rb"(?<!\w)(?:RW)?StructuredBuffer\s*<\s*(?:uint|float)\s*>\s*(" + _NAMES + rb")\b")
_STORE_RE = re.compile(rb"\b(" + _NAMES + rb")\[([^\[\]]+)\]\s*=\s*([^;]+);")
_LOAD_RE = re.compile(rb"\b(" + _NAMES + rb")\[([^\[\]]+)\]")


def fft_passes(slices: int = SLICES) -> list[dict]:
    """``cbFFTPerCall`` of the six passes as the engine's plan holds them: three along one axis (out stride 32768,
    the twiddles' period 512), three along the other (64, 1), each in stride and phase base 8 times the last."""
    threads, out = slices * SIDE * SIDE // 8, []
    for k in range(6):
        out.append({"cThreadCount": threads, "cOStride": 32768 if k < 3 else 64, "cIStride": 32768 >> (3 * k),
                    "cPStride": 512 if k < 3 else 1, "cPhaseBase": -2 * math.pi / (SIDE * SIDE) * 8**k})  # fmt: skip
    return out


def init_values(water) -> dict:
    """``cbHeightMapInit`` and the wind of a setting's ``Water`` block (``ocean1800.SWater``)."""
    return {"cOceanPatchLength": PATCH, "cWaveAmplitude": water.amplitude * AMPLITUDE_SCALE,
            "cWaveWindSpeed": water.size, "cWaveWindDamping": water.damping, "cWaveMinSize": MIN_WAVE,
            "cWaveSeed": int(water.seed) * SEED_SCALE, "cWindDirection": list(WIND)}  # fmt: skip


def raw_buffers(text: bytes) -> bytes:
    """The FFT's structured buffers of words as writable raw buffers, each element four bytes: QRhi binds a storage
    buffer by a raw view only, as an unordered-access one on Direct3D 12 even where it is only read, and the
    viewer's compile turns a read-only structured one into a small constant buffer."""
    text = _DECLARED_RE.sub(lambda m: b"RWByteAddressBuffer " + m.group(1), text)
    text = _STORE_RE.sub(lambda m: m.group(1) + b".Store((" + m.group(2) + b") * 4, " + _word(m) + b");", text)
    return _LOAD_RE.sub(lambda m: _loaded(m.group(1), b"(" + m.group(1) + b".Load((" + m.group(2) + b") * 4))"), text)


def _word(match: re.Match) -> bytes:
    return b"asuint(" + match.group(3) + b")" if match.group(1) in FLOATS else match.group(3)


def _loaded(name: bytes, load: bytes) -> bytes:
    return b"asfloat" + load if name in FLOATS else load


class CFftSource(terrain1800.CTerrainSource):
    """The ocean's effect with the FFT's buffers as raw buffers (``raw_buffers``)."""

    def _read(self, path: str) -> bytes | None:
        raw = super()._read(path)
        return raw_buffers(raw) if raw is not None and path.endswith((WATER, EFFECT)) else raw


def compile_stages(source) -> dict:
    """In the background: the passes' compute stages, with the engine's defines of the ocean's effect (``source``:
    the look's, whose files and compiler they take)."""
    fft = CFftSource(source.m_files, source.m_compiler, source.m_game)
    return {key: fft.call_stage(EFFECT, waves1800.DEFINES, technique) for key, technique in TECHNIQUES.items()}


class CLargeWaves1800(CComputeChain):
    """The spectrum's buffers, the passes and the two maps with their mips (module docstring)."""

    def __init__(self, ctx, stages: dict, water) -> None:
        self.m_rhi = rhi = ctx.m_rhi
        self.m_water = water
        self.m_stages = {key: CComputeStage(rhi, stages[key]) for key in ("init", "update", "wrap", "gradients")}
        lead = {"fft": None, "last": None}
        for k, pass_values in enumerate(fft_passes()):  # a stage a pass: each dispatch has its own constants
            key = "fft" if pass_values["cIStride"] > 1 else "last"
            self.m_stages[f"fft{k}"] = stage = CComputeStage(rhi, stages[key], lead[key])
            lead[key] = lead[key] or stage
        words = SLICES * SIDE * SIDE * 4
        self.m_h0, self.m_omega = (
            storage_buffer(rhi, IN_WIDTH * IN_WIDTH * 4),
            storage_buffer(rhi, IN_WIDTH * IN_WIDTH * 4),
        )
        self.m_ht, self.m_temp, self.m_out = (
            storage_buffer(rhi, words),
            storage_buffer(rhi, words),
            storage_buffer(rhi, words),
        )
        texture_flags = QRhiTexture.Flag
        self.m_maps = []
        for _ in range(2):
            texture = rhi.newTexture(QRhiTexture.Format.RGBA16F, QSize(SIDE, SIDE), 1,
                                 texture_flags.MipMapped | texture_flags.UsedWithGenerateMips | texture_flags.UsedWithLoadStore)  # fmt: skip
            texture.create()
            self.m_maps.append(texture)
        self._m_started = False

    def _mips(self, texture):
        batch = self.m_rhi.nextResourceUpdateBatch()
        batch.generateMips(texture)
        return batch

    def _start(self, batch) -> None:
        """The spectrum's buffers zeroed: the update reads one row and column past what the start writes."""
        for buffer in (self.m_h0, self.m_omega):
            batch.uploadStaticBuffer(buffer, bytes(buffer.size()))

    def run(self, ctx, seconds: float) -> tuple:
        """The spectrum once, then this frame's waves at the wave time of ``seconds``; returns (the displacement
        map, its gradient map)."""
        values = {**init_values(self.m_water), "cWaveTime": waves1800.wave_time(seconds),
                  "cChoppyScale": self.m_water.choppiness, "cGridLen": waves1800.GRID_LEN}  # fmt: skip

        def fill(extra=None):
            return lambda constants: gs.set_values([constants], {**values, **(extra or {})})

        named = {"cDstData": self.m_h0, "cDstOmega": self.m_omega}
        group_count = groups(SIDE, GROUP)
        if not self._m_started:
            record(ctx, self.m_stages["init"], named, fill(), (group_count, group_count, 1), before=self._start)
            self._m_started = True
        named = {"cSrcData": self.m_h0, "cSrcOmega": self.m_omega, "cDstData": self.m_ht}
        record(ctx, self.m_stages["update"], named, fill(), (group_count, group_count, 1))
        source_buffer, target_buffer = self.m_ht, self.m_temp
        for k, pass_values in enumerate(fft_passes()):  # ht -> temp -> out -> temp -> out -> temp -> out
            record(ctx, self.m_stages[f"fft{k}"], {"cSrcData": source_buffer, "cDstData": target_buffer}, fill(pass_values),
                   (pass_values["cThreadCount"] // LINE, 1, 1))  # fmt: skip
            source_buffer, target_buffer = target_buffer, (self.m_out if target_buffer is self.m_temp else self.m_temp)
        displacement, gradients = self.m_maps
        wrap_group_count = groups(SIDE, WRAP_GROUP)
        record(ctx, self.m_stages["wrap"], {"cSrcData": self.m_out, "cDstDataTex": displacement}, fill(), (wrap_group_count, wrap_group_count, 1),
               lambda: self._mips(displacement))  # fmt: skip
        named = {"cLargeWaveDisplacementMap": displacement, "cDstDataTex": gradients}
        record(
            ctx, self.m_stages["gradients"], named, fill(), (group_count, group_count, 1), lambda: self._mips(gradients)
        )
        return displacement, gradients

    def release(self) -> None:
        super().release()
        for resource in (self.m_h0, self.m_omega, self.m_ht, self.m_temp, self.m_out, *self.m_maps):
            resource.destroy()
        self.m_maps = []
