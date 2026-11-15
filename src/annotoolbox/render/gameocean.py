"""Anno 117's ocean wave simulation by the game's own compute shaders (``ocean_simulation.fx``): the wave spectrum of
two simulations of four bands each, its inverse FFT per frame into the displacement arrays, and the wave gradients
the water shaders read (``cWaveGradients``).

``OceanSimulation(rhi, stages, water)`` is the entry (the compiled shaders, ``STAGES``; the ambient's ``Water`` values,
``read_water``); ``run(ctx, seconds)`` records the passes, ``inputs()`` and ``CONSTANTS`` are what the water shader
reads. The engine's band table and the viewer's stand-ins: docs/rendering/frame-117-viewer.md section 10.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .gamecompute import ComputeChain, ComputeStage, groups, record
from .gamesky import block, block_number

EFFECT = "data/shaders/fx/ocean_simulation.fx"
# pass -> (effect, technique, function); 256 is no power of 8, so the radix-2 passes (section 10)
STAGES = {"init": (EFFECT, "InitWaveSpectrum", "csInitWaveSpectrum"),
          "update": (EFFECT, "UpdateWaveSpectrumCS", "csUpdateWaveSpectrum"),
          "vertical": (EFFECT, "OceanIDFTRadix2Vertical", "csFFTRadix2Vertical"),
          "horizontal": (EFFECT, "OceanIDFTRadix2Horizontal", "csFFTRadix2Horizontal"),
          "gradients": (EFFECT, "OceanWaveGradients", "csOceanWaveGradients")}  # fmt: skip
SIZE, GROUP = 256, 16  # FFT_MAX_MAP_SIZE, the 16 x 16 groups of the spectrum and gradient passes
SIMULATIONS = ("Coastal", "Offshore")  # slices 0-3 and 4-7
# the engine's band table: patch size in metres, the wave numbers' range (frame-117-viewer.md 10)
BANDS = ((2521.0, 0.0, 0.13), (1201.0, 0.13, 0.2), (211.0, 0.2, 1.19), (31.0, 1.19, 3.4028234663852886e38))
CONSTANTS = {f"cBand{k}PatchDims": band[0] for k, band in enumerate(BANDS)}
WIND = (1.0, 0.0)  # the session's wind direction is the engine's [E]
COMPONENTS = ("Dx", "Dy", "Dz")


def read_water(text: str) -> dict:
    """The simulations' values of an ambient file's ``Water`` block: per simulation its fetch, wind speed,
    choppiness, directionality and depth, and the wave seed."""
    water = block(text, "Water")
    sims = {s: {k: block_number(water, f"{s}Wave{k}", d) for k, d in (("Fetch", 16000.0), ("WindSpeed", 1.0), ("Choppiness", 1.0),
                                                         ("Directionality", 0.5), ("OceanDepth", 100.0))}
            for s in SIMULATIONS}  # fmt: skip
    return {"simulations": sims, "seed": int(block_number(water, "WaveSeed", 0))}


def spectrum_rows(water: dict) -> list[dict]:
    """``FFTWaveSpectrumParams`` per slice: the simulation's values with the band's patch and range."""
    rows = []
    for sim in SIMULATIONS:
        s = water["simulations"][sim]
        for patch, kmin, kmax in BANDS:
            rows.append({"Fetch": s["Fetch"], "WindSpeed": s["WindSpeed"], "OceanDepth": s["OceanDepth"],
                         "Directionality": s["Directionality"], "PatchDim": patch, "MinKLength": kmin,
                         "MaxKLength": kmax, "WaveSeed": water["seed"], "ChoppyScale": s["Choppiness"],
                         "WindDirection": WIND, "Pad0": 0})  # fmt: skip
    return rows


class OceanSimulation(ComputeChain):
    """The passes and their texture arrays (module docstring)."""

    def __init__(self, rhi, stages: dict, water: dict) -> None:
        self.rhi, self.rows = rhi, spectrum_rows(water)
        self.stages = {key: ComputeStage(rhi, stages[key]) for key in ("init", "update", "gradients")}
        for k, comp in enumerate(COMPONENTS):  # a stage per component: each dispatch has its own constants
            first_v, first_h = self.stages.get("vertical.Dx"), self.stages.get("horizontal.Dx")
            self.stages[f"vertical.{comp}"] = ComputeStage(rhi, stages["vertical"], first_v if k else None)
            self.stages[f"horizontal.{comp}"] = ComputeStage(rhi, stages["horizontal"], first_h if k else None)
        half4, half = QRhiTexture.Format.RGBA16F, QRhiTexture.Format.R16F  # rg16f as RGBA16F (no RG16F in QRhi here)
        self.h0, self.omega = self._array(half4), self._array(half)
        self.ht = {c: self._array(half4) for c in COMPONENTS}
        self.temp = self._array(half4)
        self.waves = {c: self._array(half) for c in COMPONENTS}
        self.gradients = self._array(half4)
        self._started = False

    def _array(self, fmt):
        tex = self.rhi.newTextureArray(fmt, 2 * len(BANDS), QSize(SIZE, SIZE), 1, QRhiTexture.Flag.UsedWithLoadStore)
        tex.create()
        return tex

    def _fill(self, c: gs.Constants, seconds: float, is_dy: bool = False) -> None:
        gs.replay(c, "ocean", (self.rows,), self._fill_rows)  # 100 values a pass, the same every frame
        c.set("cWaveTime", seconds)
        c.set("cIsDy", int(is_dy))

    def _fill_rows(self, c: gs.Constants) -> None:
        for k, row in enumerate(self.rows):
            for key, value in row.items():
                c.set(f"cSpectrumParams[{k}].{key}", value)
        for key, value in CONSTANTS.items():
            c.set(key, value)

    def inputs(self) -> dict:
        """What the water shader reads of the simulation, by name."""
        return {"cWaveGradients": self.gradients, "cWaveDx": self.waves["Dx"], "cWaveDy": self.waves["Dy"],
                "cWaveDz": self.waves["Dz"]}  # fmt: skip

    def run(self, ctx, seconds: float) -> None:
        """The spectrum once, then this frame's spectrum, its inverse FFT per component and the gradients."""
        slices = 2 * len(BANDS)
        grid = (groups(SIZE, GROUP), groups(SIZE, GROUP), slices)

        def fill(c: gs.Constants, is_dy: bool = False) -> None:
            self._fill(c, seconds, is_dy)

        spectra = {"cH0": self.h0, "cOmega": self.omega, **{f"cHt{c}": self.ht[c] for c in COMPONENTS}}
        if not self._started:
            record(ctx, self.stages["init"], spectra, fill, grid)
            self._started = True
        record(ctx, self.stages["update"], spectra, fill, grid)
        for comp in COMPONENTS:
            named = {"cIFFTInput": self.ht[comp], "cIFFTTemp": self.temp, "cIFFTOutput": self.waves[comp],
                     "cHtDx": self.ht[comp]}  # fmt: skip
            record(ctx, self.stages[f"vertical.{comp}"], named, fill, (SIZE, 1, slices))
            record(ctx, self.stages[f"horizontal.{comp}"], named, lambda c, dy=comp == "Dy": fill(c, dy),
                   (1, SIZE, slices))  # fmt: skip
        named = {"cGradientInputX": self.waves["Dx"], "cGradientInputY": self.waves["Dy"],
                 "cGradientInputZ": self.waves["Dz"], "cGradientOutput": self.gradients}  # fmt: skip
        record(ctx, self.stages["gradients"], named, fill, grid)

    def release(self) -> None:
        super().release()
        for tex in (self.h0, self.omega, self.temp, self.gradients, *self.ht.values(), *self.waves.values()):
            tex.destroy()
