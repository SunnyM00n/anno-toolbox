"""Anno 1800's large ocean waves (``render.oceanfft1800``): the engine's six FFT passes, run on the CPU as the
shader's radix-8 kernels read them, are the forward FFT of each 512 x 512 slice; their phase bases are the engine's
words; the spectrum's start values come from the setting's water block; installed (ANNO1800_DIR): every pass compiles.
docs/rendering/island-1800-2205-view.md 17."""

from __future__ import annotations

import numpy as np
import pytest

from annotoolbox.render import ocean1800, oceanfft1800

C = 0.70710678118654752  # COS_PI_4_16


def _ft2(a, b):
    return a + b, a - b


def _upd(a, b):
    """``UPD_forward`` on arrays of complex numbers."""
    return a.real + b.imag + 1j * (a.imag - b.real), a.real - b.imag + 1j * (a.imag + b.real)


def _fft8(d: list) -> list:
    """``FFT_forward_8`` (functions_water.fxh) on arrays of complex numbers."""
    d = list(d)
    for i in range(4):
        d[i], d[i + 4] = _ft2(d[i], d[i + 4])
    d[4], d[6] = _upd(d[4], d[6])
    d[5], d[7] = _upd(d[5], d[7])
    d[5], d[7] = d[5] * (C - 1j * C), d[7] * (-C - 1j * C)
    d[0], d[2] = _ft2(d[0], d[2])
    d[1], d[3] = _ft2(d[1], d[3])
    d[0], d[1] = _ft2(d[0], d[1])
    d[2], d[3] = _upd(d[2], d[3])
    d[4], d[5] = _ft2(d[4], d[5])
    d[6], d[7] = _ft2(d[6], d[7])
    return d


def _radix(src: np.ndarray, p: dict) -> np.ndarray:
    """``csRadix008A_1`` (``cIStride`` > 1) or ``csRadix008A_2`` over all threads at once."""
    t = np.arange(p["cThreadCount"])
    istride = p["cIStride"]
    if istride > 1:
        imod = t & (istride - 1)
        d = [src[((t - imod) << 3) + imod + i * istride] for i in range(8)]
    else:
        d = [src[(t << 3) + i] for i in range(8)]
    d = _fft8(d)
    if istride > 1:  # TWIDDLE_8
        phase = np.float32(p["cPhaseBase"]) * (t & (istride - p["cPStride"]))
        for k, i in enumerate((4, 2, 6, 1, 5, 3, 7)):
            d[i] = d[i] * np.exp(1j * (k + 1) * phase)
    omod = t & (p["cOStride"] - 1)
    oaddr = ((t - omod) << 3) + omod
    out = np.zeros_like(src)
    for k, i in enumerate((0, 4, 2, 6, 1, 5, 3, 7)):
        out[oaddr + k * p["cOStride"]] = d[i]
    return out


def test_the_six_passes_are_the_forward_fft_of_each_slice():
    rng = np.random.default_rng(1)
    n = oceanfft1800.SIDE
    data = rng.standard_normal(oceanfft1800.SLICES * n * n) + 1j * rng.standard_normal(oceanfft1800.SLICES * n * n)
    got = data
    for p in oceanfft1800.fft_passes():
        got = _radix(got, p)
    want = np.fft.fft2(data.reshape(-1, n, n))
    assert np.abs(got.reshape(-1, n, n) - want).max() < 1e-5 * np.abs(want).max()


def test_the_passes_hold_the_engines_words():
    passes = oceanfft1800.fft_passes()
    words = [int(np.float32(p["cPhaseBase"]).view(np.uint32)) for p in passes]
    assert words == [0xB7C90FDB, 0xB9490FDB, 0xBAC90FDB, 0xBC490FDB, 0xBDC90FDB, 0xBF490FDB]
    assert [(p["cOStride"], p["cIStride"], p["cPStride"]) for p in passes] == [
        (0x8000, 0x8000, 0x200), (0x8000, 0x1000, 0x200), (0x8000, 0x200, 0x200),
        (0x40, 0x40, 1), (0x40, 8, 1), (0x40, 1, 1)]  # fmt: skip
    assert {p["cThreadCount"] for p in passes} == {3 << 18 >> 3}


def test_the_spectrum_starts_from_the_settings_water_block():
    v = oceanfft1800.init_values(ocean1800.Water())  # moderate_01: 0.25, 800, 0.5, 13
    assert np.isclose(v["cWaveAmplitude"], 0.25e-8) and v["cWaveWindSpeed"] == 800.0
    assert (v["cWaveWindDamping"], v["cWaveSeed"], v["cWaveMinSize"], v["cOceanPatchLength"]) == (0.5, 130, 1000.0,
                                                                                                    4096.0)  # fmt: skip


def test_the_ffts_structured_buffers_become_writable_raw_buffers_of_four_bytes_an_element():
    text = (b"shared root_descriptor12 StructuredBuffer<uint> cSrcData;\nshared RWStructuredBuffer<float> cDstOmega;\n"
            b"x = UnpackFloat16_2(cSrcData[a + b]);\ncDstOmega[i] = sqrt(k);\ncDstData[o + 1 * s] = PackFloat16_2(d);\n"
            b"RWStructuredBuffer<uint> cOther;\n")  # fmt: skip
    got = oceanfft1800.raw_buffers(text).decode()
    assert "shared root_descriptor12 RWByteAddressBuffer cSrcData;" in got and "RWByteAddressBuffer cDstOmega;" in got
    assert "UnpackFloat16_2((cSrcData.Load((a + b) * 4)))" in got
    assert (
        "cDstOmega.Store((i) * 4, asuint(sqrt(k)));" in got
        and "cDstData.Store((o + 1 * s) * 4, PackFloat16_2(d));" in got
    )
    assert "RWStructuredBuffer<uint> cOther;" in got  # another buffer stays as it is


def test_the_large_waves_passes_compile():
    import os
    from pathlib import Path

    root = os.environ.get("ANNO1800_DIR")
    if not root:
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR)")
    from annotoolbox.editor.formats.a7m.reader import GameFiles
    from annotoolbox.infra.fxc import Fxc
    from annotoolbox.islands.islandview import Files
    from annotoolbox.othergames.installs import GAMES
    from annotoolbox.render import terrain1800

    files = Files(GameFiles(GAMES["anno1800"].index(Path(root), None)))
    compiler = Fxc.find(Path(root))
    if compiler is None:
        pytest.skip("no FXC in the install")
    stages = oceanfft1800.compile_stages(terrain1800.TerrainSource(files, compiler, "anno1800"))
    assert set(stages) == set(oceanfft1800.TECHNIQUES)
    names = {cb.name: {v.name for v in cb.variables} for cb in stages["fft"].reflection.cbuffers}
    assert set(oceanfft1800.fft_passes()[0]) <= names["cbFFTPerCall"]
    init = {v.name for cb in stages["init"].reflection.cbuffers for v in cb.variables}
    assert set(oceanfft1800.init_values(ocean1800.Water())) <= init
