"""Anno 117's ocean wave simulation by the game's own compute shaders (``ocean_simulation.fx``): the wave spectrum of
two simulations of four bands each, its inverse FFT per frame into the displacement arrays, and the wave gradients
the water shaders read (``cWaveGradients``).

``COceanSimulation(rhi, stages, water)`` is the entry (the compiled shaders, ``STAGES``; the ambient's ``Water`` values,
``read_water``); ``run(ctx, seconds)`` records the passes, ``inputs()`` and ``CONSTANTS`` are what the water shader
reads. The engine's band table and the viewer's stand-ins: docs/rendering/frame-117-viewer.md section 10.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .gamecompute import CComputeChain, CComputeStage, groups, record
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
    fields = (
        ("Fetch", 16000.0),
        ("WindSpeed", 1.0),
        ("Choppiness", 1.0),
        ("Directionality", 0.5),
        ("OceanDepth", 100.0),
    )
    sims = {
        simulation: {suffix: block_number(water, f"{simulation}Wave{suffix}", default) for suffix, default in fields}
        for simulation in SIMULATIONS
    }
    return {"simulations": sims, "seed": int(block_number(water, "WaveSeed", 0))}


def spectrum_rows(water: dict) -> list[dict]:
    """``FFTWaveSpectrumParams`` per slice: the simulation's values with the band's patch and range."""
    rows = []
    for sim in SIMULATIONS:
        simulation_values = water["simulations"][sim]
        for patch, kmin, kmax in BANDS:
            rows.append({"Fetch": simulation_values["Fetch"], "WindSpeed": simulation_values["WindSpeed"], "OceanDepth": simulation_values["OceanDepth"],
                         "Directionality": simulation_values["Directionality"], "PatchDim": patch, "MinKLength": kmin,
                         "MaxKLength": kmax, "WaveSeed": water["seed"], "ChoppyScale": simulation_values["Choppiness"],
                         "WindDirection": WIND, "Pad0": 0})  # fmt: skip
    return rows


class COceanSimulation(CComputeChain):
    """The passes and their texture arrays (module docstring)."""

    def __init__(self, rhi, stages: dict, water: dict) -> None:
        self.m_rhi, self.m_rows = rhi, spectrum_rows(water)
        self.m_stages = {key: CComputeStage(rhi, stages[key]) for key in ("init", "update", "gradients")}
        for k, comp in enumerate(COMPONENTS):  # a stage per component: each dispatch has its own constants
            first_v, first_h = self.m_stages.get("vertical.Dx"), self.m_stages.get("horizontal.Dx")
            self.m_stages[f"vertical.{comp}"] = CComputeStage(rhi, stages["vertical"], first_v if k else None)
            self.m_stages[f"horizontal.{comp}"] = CComputeStage(rhi, stages["horizontal"], first_h if k else None)
        half4, half = QRhiTexture.Format.RGBA16F, QRhiTexture.Format.R16F  # rg16f as RGBA16F (no RG16F in QRhi here)
        self.m_h0, self.m_omega = self._array(half4), self._array(half)
        self.m_ht = {comp: self._array(half4) for comp in COMPONENTS}
        self.m_temp = self._array(half4)
        self.m_waves = {comp: self._array(half) for comp in COMPONENTS}
        self.m_gradients = self._array(half4)
        self._m_started = False

    def _array(self, texture_format):
        texture = self.m_rhi.newTextureArray(
            texture_format, 2 * len(BANDS), QSize(SIZE, SIZE), 1, QRhiTexture.Flag.UsedWithLoadStore
        )
        texture.create()
        return texture

    def _fill(self, constants: gs.CConstants, seconds: float, is_dy: bool = False) -> None:
        gs.replay(constants, "ocean", (self.m_rows,), self._fill_rows)  # 100 values a pass, the same every frame
        constants.set("cWaveTime", seconds)
        constants.set("cIsDy", int(is_dy))

    def _fill_rows(self, constants: gs.CConstants) -> None:
        for k, row in enumerate(self.m_rows):
            for key, value in row.items():
                constants.set(f"cSpectrumParams[{k}].{key}", value)
        for key, value in CONSTANTS.items():
            constants.set(key, value)

    def inputs(self) -> dict:
        """What the water shader reads of the simulation, by name."""
        return {"cWaveGradients": self.m_gradients, "cWaveDx": self.m_waves["Dx"], "cWaveDy": self.m_waves["Dy"],
                "cWaveDz": self.m_waves["Dz"]}  # fmt: skip

    def run(self, ctx, seconds: float) -> None:
        """The spectrum once, then this frame's spectrum, its inverse FFT per component and the gradients."""
        slices = 2 * len(BANDS)
        grid = (groups(SIZE, GROUP), groups(SIZE, GROUP), slices)

        def fill(constants: gs.CConstants, is_dy: bool = False) -> None:
            self._fill(constants, seconds, is_dy)

        spectra = {
            "cH0": self.m_h0,
            "cOmega": self.m_omega,
            **{f"cHt{component}": self.m_ht[component] for component in COMPONENTS},
        }
        if not self._m_started:
            record(ctx, self.m_stages["init"], spectra, fill, grid)
            self._m_started = True
        record(ctx, self.m_stages["update"], spectra, fill, grid)
        for comp in COMPONENTS:
            named = {"cIFFTInput": self.m_ht[comp], "cIFFTTemp": self.m_temp, "cIFFTOutput": self.m_waves[comp],
                     "cHtDx": self.m_ht[comp]}  # fmt: skip
            record(ctx, self.m_stages[f"vertical.{comp}"], named, fill, (SIZE, 1, slices))
            record(ctx, self.m_stages[f"horizontal.{comp}"], named, lambda constants, is_dy=comp == "Dy": fill(constants, is_dy),
                   (1, SIZE, slices))  # fmt: skip
        named = {"cGradientInputX": self.m_waves["Dx"], "cGradientInputY": self.m_waves["Dy"],
                 "cGradientInputZ": self.m_waves["Dz"], "cGradientOutput": self.m_gradients}  # fmt: skip
        record(ctx, self.m_stages["gradients"], named, fill, grid)

    def release(self) -> None:
        super().release()
        for texture in (
            self.m_h0,
            self.m_omega,
            self.m_temp,
            self.m_gradients,
            *self.m_ht.values(),
            *self.m_waves.values(),
        ):
            texture.destroy()
