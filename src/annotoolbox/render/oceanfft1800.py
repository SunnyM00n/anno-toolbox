"""Anno 1800's large ocean waves by its own compute shaders (``water_ocean.fx``): the setting's wave spectrum once
(``InitWaveHeightMap``), its state at the wave time each frame (``UpdateWaveSpectrumCS``), the inverse FFT of
512 x 512 in six radix-8 passes (``FftCS1``, ``FftCS2``), the displacement map (``OceanFFTWrapUp``) and its gradients
(``OceanGradients2``): the surface's ``cLargeWaveDisplacementMap`` and ``cLargeWaveGradientMap``.

``compile_stages(source)`` runs in the background; ``LargeWaves1800(ctx, stages, water)``'s ``run(ctx, seconds)``
records the passes and returns the two maps. The engine's values: docs/rendering/island-1800-2205-view.md 17.
"""

from __future__ import annotations

import math
import re

from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from . import terrain1800, waves1800
from .gamecompute import ComputeChain, ComputeStage, groups, record, storage_buffer

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
    """``cbHeightMapInit`` and the wind of a setting's ``Water`` block (``ocean1800.Water``)."""
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


def _word(m: re.Match) -> bytes:
    return b"asuint(" + m.group(3) + b")" if m.group(1) in FLOATS else m.group(3)


def _loaded(name: bytes, load: bytes) -> bytes:
    return b"asfloat" + load if name in FLOATS else load


class FftSource(terrain1800.TerrainSource):
    """The ocean's effect with the FFT's buffers as raw buffers (``raw_buffers``)."""

    def _read(self, path: str) -> bytes | None:
        raw = super()._read(path)
        return raw_buffers(raw) if raw is not None and path.endswith((WATER, EFFECT)) else raw


def compile_stages(source) -> dict:
    """In the background: the passes' compute stages, with the engine's defines of the ocean's effect (``source``:
    the look's, whose files and compiler they take)."""
    fft = FftSource(source.files, source.compiler, source.game)
    return {key: fft.call_stage(EFFECT, waves1800.DEFINES, tech) for key, tech in TECHNIQUES.items()}


class LargeWaves1800(ComputeChain):
    """The spectrum's buffers, the passes and the two maps with their mips (module docstring)."""

    def __init__(self, ctx, stages: dict, water) -> None:
        self.rhi = rhi = ctx.rhi
        self.water = water
        self.stages = {key: ComputeStage(rhi, stages[key]) for key in ("init", "update", "wrap", "gradients")}
        lead = {"fft": None, "last": None}
        for k, p in enumerate(fft_passes()):  # a stage a pass: each dispatch has its own constants
            key = "fft" if p["cIStride"] > 1 else "last"
            self.stages[f"fft{k}"] = stage = ComputeStage(rhi, stages[key], lead[key])
            lead[key] = lead[key] or stage
        words = SLICES * SIDE * SIDE * 4
        self.h0, self.omega = storage_buffer(rhi, IN_WIDTH * IN_WIDTH * 4), storage_buffer(rhi, IN_WIDTH * IN_WIDTH * 4)
        self.ht, self.temp, self.out = (
            storage_buffer(rhi, words),
            storage_buffer(rhi, words),
            storage_buffer(rhi, words),
        )
        f = QRhiTexture.Flag
        self.maps = []
        for _ in range(2):
            tex = rhi.newTexture(QRhiTexture.Format.RGBA16F, QSize(SIDE, SIDE), 1,
                                 f.MipMapped | f.UsedWithGenerateMips | f.UsedWithLoadStore)  # fmt: skip
            tex.create()
            self.maps.append(tex)
        self._started = False

    def _mips(self, tex):
        batch = self.rhi.nextResourceUpdateBatch()
        batch.generateMips(tex)
        return batch

    def _start(self, batch) -> None:
        """The spectrum's buffers zeroed: the update reads one row and column past what the start writes."""
        for buf in (self.h0, self.omega):
            batch.uploadStaticBuffer(buf, bytes(buf.size()))

    def run(self, ctx, seconds: float) -> tuple:
        """The spectrum once, then this frame's waves at the wave time of ``seconds``; returns (the displacement
        map, its gradient map)."""
        values = {**init_values(self.water), "cWaveTime": waves1800.wave_time(seconds),
                  "cChoppyScale": self.water.choppiness, "cGridLen": waves1800.GRID_LEN}  # fmt: skip

        def fill(extra=None):
            return lambda c: gs.set_values([c], {**values, **(extra or {})})

        named = {"cDstData": self.h0, "cDstOmega": self.omega}
        n = groups(SIDE, GROUP)
        if not self._started:
            record(ctx, self.stages["init"], named, fill(), (n, n, 1), before=self._start)
            self._started = True
        named = {"cSrcData": self.h0, "cSrcOmega": self.omega, "cDstData": self.ht}
        record(ctx, self.stages["update"], named, fill(), (n, n, 1))
        src, dst = self.ht, self.temp
        for k, p in enumerate(fft_passes()):  # ht -> temp -> out -> temp -> out -> temp -> out
            record(ctx, self.stages[f"fft{k}"], {"cSrcData": src, "cDstData": dst}, fill(p),
                   (p["cThreadCount"] // LINE, 1, 1))  # fmt: skip
            src, dst = dst, (self.out if dst is self.temp else self.temp)
        displacement, gradients = self.maps
        m = groups(SIDE, WRAP_GROUP)
        record(ctx, self.stages["wrap"], {"cSrcData": self.out, "cDstDataTex": displacement}, fill(), (m, m, 1),
               lambda: self._mips(displacement))  # fmt: skip
        named = {"cLargeWaveDisplacementMap": displacement, "cDstDataTex": gradients}
        record(ctx, self.stages["gradients"], named, fill(), (n, n, 1), lambda: self._mips(gradients))
        return displacement, gradients

    def release(self) -> None:
        super().release()
        for res in (self.h0, self.omega, self.ht, self.temp, self.out, *self.maps):
            res.destroy()
        self.maps = []
