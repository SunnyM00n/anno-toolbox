"""The ocean's wave simulation by the game's shipped ``ocean_simulation.fx`` on the native backend's own command list,
ahead of QRhi's frame: the spectrum once, then per frame its update, the inverse FFT per component and the wave
gradients (``gameocean``'s chain, its constants and grid), into the module's texture arrays in the engine's formats.

``CNativeOcean(device, natives, handover, water)`` is the entry (``water``: ``gameocean.read_water``'s); ``run(ctx,
seconds)`` records and sends the frame's passes, ``inputs()`` gives QRhi's wrappers of the displacements and the
gradients by the names the water shaders read. docs/rendering/native-backend.md section 16.
"""

from __future__ import annotations

import os

from PySide6.QtGui import QRhiTexture

from . import gameocean, nativecompute, nativegpu
from . import gameshader as gs
from .gamecompute import groups

ENV = "ANNO_NATIVE_OCEAN"  # "0": the ocean's simulation by the current path (the rest of the water stays native)
STEM = "ocean_simulation"
KERNELS = {key: fn for key, (_e, _t, fn) in gameocean.STAGES.items()}
SLICES = 2 * len(gameocean.BANDS)
# the engine's declared formats (``RWTEX_FORMAT``): the current path holds the rg16f ones as RGBA16F (QRhi has none)
SPECTRUM, HEIGHT, SLOPE = "RG16_FLOAT", "R16_FLOAT", "RGBA16_FLOAT"
QRHI = {HEIGHT: QRhiTexture.Format.R16F, SLOPE: QRhiTexture.Format.RGBA16F}


def is_enabled() -> bool:
    return os.environ.get(ENV, "") != "0" and nativegpu.on()


def shaders(natives) -> dict:
    """The chain's shaders of the first shipped variant by ``KERNELS``' keys, each reflected (in the background)."""
    variant = natives.variant(STEM)
    out = {key: natives.call(variant, fn) for key, fn in KERNELS.items()}
    for shader in out.values():
        natives.reflection(shader)
    return out


class CNativeOcean(nativecompute.CTold):
    """The simulation's kernels and arrays (module docstring)."""

    def __init__(self, device, natives, handover, water: dict) -> None:
        self.m_device, self.m_handover, self.m_rows = device, handover, gameocean.spectrum_rows(water)
        self.m_messages, self.m_foreign = [], []
        self.m_error = ""
        found = shaders(natives)
        self.m_kernels = {
            key: nativecompute.CKernel.compute(device, shader, natives.reflection(shader))
            for key, shader in found.items()
        }
        self.m_list = device.command_list()
        self.m_list.open()
        size = gameocean.SIZE

        def array(texture_format: str, name: str):
            return device.texture(size, size, texture_format, depth=SLICES, kind="2darray", usage="uav", name=name)

        self.m_h0, self.m_omega, self.m_temp = (
            array(SPECTRUM, "H0"),
            array(HEIGHT, "Omega"),
            array(SPECTRUM, "IFFT temp"),
        )
        self.m_ht = {constants: array(SPECTRUM, f"Ht{constants}") for constants in gameocean.COMPONENTS}
        self.m_waves = {constants: array(HEIGHT, f"Wave{constants}") for constants in gameocean.COMPONENTS}
        self.m_gradients = array(SLOPE, "wave gradients")
        self.m_qrhi = {f"cWave{constants}": self._give(texture, HEIGHT) for constants, texture in self.m_waves.items()}
        self.m_qrhi["cWaveGradients"] = self._give(self.m_gradients, SLOPE)
        # the slices' spectrum values: a structured buffer in the shipped shaders (the rewrite's constants), once
        element = next(
            cbuffer for cbuffer in natives.reflection(found["init"]).cbuffers if cbuffer.name == "cSpectrumParams"
        )
        params = gs.CConstants(nativecompute.records(element, SLICES))
        self._rows(params)
        self.m_params = device.buffer(len(params.m_data), "structured", stride=element.size, name="cSpectrumParams")
        self.m_list.write_buffer(self.m_params, bytes(params.m_data))
        self._m_started = False

    def _give(self, texture, texture_format: str):
        size = (gameocean.SIZE, gameocean.SIZE)
        return self.m_handover.give(texture, QRHI[texture_format], size, layers=SLICES)

    def inputs(self) -> dict:
        """What the water shaders read of the simulation, by name (QRhi's wrappers: ``gameocean.inputs``')."""
        return dict(self.m_qrhi)

    def _fill(self, constants: gs.CConstants, seconds: float, is_dy: bool = False) -> None:
        gs.replay(constants, "ocean-native", (self.m_rows,), self._rows)  # 100 values a pass, the same every frame
        constants.set("cWaveTime", seconds)
        constants.set("cIsDy", int(is_dy))

    def _rows(self, constants: gs.CConstants) -> None:
        for k, row in enumerate(self.m_rows):
            for key, value in row.items():
                constants.set(f"cSpectrumParams[{k}].{key}", value)
        for key, value in gameocean.CONSTANTS.items():
            constants.set(key, value)

    def _dispatch(self, key: str, named: dict, seconds: float, grid: tuple, is_dy: bool = False) -> None:
        # one array is written by a pass and read by the next under other sets: a barrier between every two
        self.m_list.uav_barrier()
        named = {**named, "cSpectrumParams": self.m_params}
        self.m_kernels[key].dispatch(self.m_list, named, lambda constants: self._fill(constants, seconds, is_dy), grid)

    def run(self, _ctx, seconds: float) -> None:
        """``gameocean.COceanSimulation.run``'s passes on the module's list, sent to the queue (QRhi's frame, which
        reads the results, goes after it)."""
        self._told_before()
        size, grid = gameocean.SIZE, (groups(gameocean.SIZE, gameocean.GROUP),) * 2 + (SLICES,)
        self.m_handover.take_back(self.m_list, list(self.m_qrhi.values()))  # QRhi's passes read them since the last run
        spectra = {
            "cH0": self.m_h0,
            "cOmega": self.m_omega,
            **{f"cHt{constants}": self.m_ht[constants] for constants in gameocean.COMPONENTS},
        }
        if not self._m_started:
            self._dispatch("init", spectra, seconds, grid)
            self._m_started = True
        self._dispatch("update", spectra, seconds, grid)
        for component in gameocean.COMPONENTS:
            named = {"cIFFTInput": self.m_ht[component], "cIFFTTemp": self.m_temp, "cIFFTOutput": self.m_waves[component],
                     "cHtDx": self.m_ht[component]}  # fmt: skip
            self._dispatch("vertical", named, seconds, (size, 1, SLICES))
            self._dispatch("horizontal", named, seconds, (1, size, SLICES), component == "Dy")
        named = {"cGradientInputX": self.m_waves["Dx"], "cGradientInputY": self.m_waves["Dy"],
                 "cGradientInputZ": self.m_waves["Dz"], "cGradientOutput": self.m_gradients}  # fmt: skip
        self._dispatch("gradients", named, seconds, grid)
        self.m_list.execute()
        self.m_handover.handed_back()
        self._told_after()

    def release(self) -> None:
        for wrapped in self.m_qrhi.values():  # the displacements and the gradients with QRhi's wrappers
            self.m_handover.drop(wrapped)
        for kernel in self.m_kernels.values():
            kernel.release()
        for texture in (self.m_h0, self.m_omega, self.m_temp, *self.m_ht.values(), self.m_params):
            texture.release()
        self.m_list.close()
        self.m_kernels, self.m_qrhi = {}, {}
