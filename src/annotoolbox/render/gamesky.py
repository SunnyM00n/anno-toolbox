"""Anno 117's sky light by the game's own sky passes (``sky.fx``): the transmittance and multiple-scattering tables,
the sun's light through the atmosphere, the sky cube, its spherical harmonics and the prefiltered specular cube, all
on the GPU, into a ``LightData`` buffer and a cube the deferred shading and the transparent pass read.

``CSkyPasses(rhi, stages, atmosphere)`` is the entry (the compiled compute shaders by pass, ``STAGES``;
``read_atmosphere`` reads an ambient's values); ``run(ctx, sky)`` records the passes when the light changed.
``light_bytes`` packs a ``LightData`` on the CPU (the fallback). docs/rendering/frame-117-viewer.md section 4.
"""

from __future__ import annotations

import math
import re
from functools import partial
from typing import NamedTuple

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiReadbackResult, QRhiTexture

from . import gameshader as gs
from .gamecompute import CComputeChain, CComputeStage, copy_into, groups, record, storage_buffer

EFFECT = "data/shaders/fx/sky.fx"
# pass -> (effect, technique, function): what ``ShaderSource.stage_set`` compiles
STAGES = {"transmittance": (EFFECT, "ComputeTransmittance", "csComputeTransmittanceLUT"),
          "multiscat": (EFFECT, "ComputeMultiscat", "csComputeMultiscatLUT"),
          "light": (EFFECT, "ComputeAtmosphericLight", "csComputeAtmosphericLight"),
          "sky": (EFFECT, "ComputeSky", "csComputeSky"),
          "sh": (EFFECT, "ConvolveIrradianceSH", "csConvolveSkyIrradianceSH"),
          "filter": (EFFECT, "FilterReflection", "csFilterReflection")}  # fmt: skip
TRANSMITTANCE, MULTISCAT, CUBE = (256, 64), (32, 32), 64  # the engine's sizes are settings: ours [E]
LEVELS = int(math.log2(CUBE)) + 1
# the clouds in the sky's light are made again this often: about 180 passes, a frame's spike, for a drift too slow to
# see in the 64-pixel cube (island-117-view.md "Speed", part 10) [ours]
CLOUD_STEP = 10.0
LIGHT_BYTES = 9 * 16 * 2 + 32  # LightData: two float4[9], LgtDiffuse and LgtSky with their padding
AMBIENT = "data/base/config/engine/ambientsettings/{}.xml"
# a face's direction, right and up axis in Direct3D's cube order (+X, -X, +Y, -Y, +Z, -Z)
FACES = (((1, 0, 0), (0, 0, -1), (0, 1, 0)), ((-1, 0, 0), (0, 0, 1), (0, 1, 0)), ((0, 1, 0), (1, 0, 0), (0, 0, -1)),
         ((0, -1, 0), (1, 0, 0), (0, 0, 1)), ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0)))  # fmt: skip
# what no file holds [E]: Earth's radii (the Rayleigh numbers are Earth's per metre), Mie bases, the ozone layer
ENGINE = {"cAtmosphereRadiusBottom": 6360000.0, "cAtmosphereRadiusTop": 6460000.0, "cAtmosphereMieScattering": 1.0,
          "cAtmosphereMieAbsorption": 0.11, "cAtmosphereOzoneAbsorption": (0.650, 1.881, 0.085),
          "cAtmosphereOzonePeak": 25000.0, "cAtmosphereOzoneWidth": 15000.0}  # fmt: skip
SKY_GEN = {"cSkyStrength": 1.0, "cSkyNumBaseSamples": 64, "cSkyNumRoughBasedSamples": 64, "cMinimumSkyLight": 0.07}


_TAG = re.compile(r"<(/?)(\w*)>")


def block(text: str, name: str) -> str:
    """The text inside the first ``<name>`` of an ambient file (whose values close with ``</>``), "" without one."""
    match = re.search(rf"<{name}>", text)
    if match is None:
        return ""
    depth, i = 1, match.end()
    while depth and (tag := _TAG.search(text, i)) is not None:
        depth += -1 if tag.group(1) else 1
        i = tag.end()
        if depth == 0:
            return text[match.end() : tag.start()]
    return ""


def block_number(text: str, name: str, default: float = 0.0) -> float:
    """The number inside the first ``<name>`` of an ambient text (``block``), ``default`` without one."""
    found = block(text, name).strip()
    return float(found) if found else default


def block_vector(text: str, name: str, default=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    """The ``x``, ``y``, ``z`` inside the first ``<name>`` of an ambient text, ``default`` for what it lacks."""
    found = block(text, name)
    return (
        tuple(block_number(found, k, default_value) for k, default_value in zip("xyz", default, strict=True))
        if found
        else tuple(default)
    )


def read_atmosphere(text: str) -> dict:
    """``cbAtmosphere`` and the sky's ground colour from an ambient file's text (its first ``PhysicalAtmosphere``,
    ``MoonLight``, ``MoonSkyStrength``, ``GroundColorAmbient``), with ``ENGINE`` for what no file holds."""
    atm = block(text, "PhysicalAtmosphere")
    sun = tuple(
        component * block_number(atm, "SunStrength", 1.0)
        for component in block_vector(atm, "SunIrradiance", (1.0, 1.0, 1.0))
    )
    return {**ENGINE, "cAtmosphereSunIrradiance": sun, "cAtmosphereGroundAlbedo": block_vector(atm, "GroundAlbedo"),
            "cAtmosphereRayleighScattering": block_vector(atm, "RayleighScattering", (0.058, 0.135, 0.331)),
            "cAtmosphereRayleighScaleHeight": block_number(atm, "RayleighScaleHeight", 8000),
            "cAtmosphereRayleighStrength": block_number(atm, "RayleighStrength", 1e-4),
            "cAtmosphereOzoneStrength": block_number(atm, "OzoneStrength", 1e-6), "cAtmosphereMieG": block_number(atm, "MieG", 0.8),
            "cAtmosphereMieScaleHeight": block_number(atm, "MieScaleHeight", 1200),
            "cAtmosphereMieStrength": block_number(atm, "MieStrength", 3e-6),
            "cAtmosphereCustomLayerScattering": block_vector(atm, "CustomScattering"),
            "cAtmosphereCustomLayerAbsorption": block_vector(atm, "CustomAbsorption"),
            "cAtmosphereCustomLayerStrength": block_number(atm, "CustomStrength"),
            "cAtmosphereCustomLayerHeight": block_number(atm, "CustomHeight"),
            "cAtmosphereCustomLayerWidth": block_number(atm, "CustomWidth"),
            "cAtmosphereMoonStrength": block_vector(text, "MoonLight"),
            "cAtmosphereMoonScatterFactor": block_number(text, "MoonSkyStrength", 1.0),
            "cGroundColorAmbient": block_vector(text, "GroundColorAmbient", (0.2, 0.2, 0.2))}  # fmt: skip


def light_bytes(harmonics, sun_diffuse) -> bytes:
    """A ``LightData`` packed as the structured buffer holds it: the clear and the cloudy sky's coefficients (the
    same, no clouds), ``LgtDiffuse`` and ``LgtSky`` (the CPU's light: the look's)."""
    rows = np.zeros((9, 4), np.float32)
    rows[:, :3] = np.asarray(harmonics, np.float32)[:9, :3]
    light = np.r_[np.asarray(sun_diffuse, np.float32)[:3], 0.0].astype(np.float32)
    return rows.tobytes() * 2 + light.tobytes() * 2


class SSlot(NamedTuple):
    """One dispatch of a pass: a frame's uniform buffer holds one value, so each has a stage of its own."""

    key: str
    face: int = 0
    level: int = 0
    clear: bool = False


class CSkyPasses(CComputeChain):
    """The sky passes and their results (module docstring): ``light`` (the ``LightData`` buffer), ``reflection``
    (the prefiltered cube), ``transmittance`` and ``multiscat`` (the tables)."""

    def __init__(self, rhi, stages: dict, atmosphere: dict) -> None:
        self.m_rhi, self.m_atmosphere = rhi, atmosphere
        self.m_compiled = {key: stages[key] for key in STAGES}
        self.m_stages: dict[SSlot, CComputeStage] = {}
        self.m_light = storage_buffer(rhi, LIGHT_BYTES)
        storage = QRhiTexture.Flag.UsedWithLoadStore
        self.m_transmittance = self._texture(TRANSMITTANCE, storage)
        self.m_multiscat = self._texture(MULTISCAT, storage)
        self.m_targets = [self._texture((CUBE >> k, CUBE >> k), storage | QRhiTexture.Flag.UsedAsTransferSource)
                        for k in range(LEVELS)]  # fmt: skip
        cube = QRhiTexture.Flag.CubeMap | QRhiTexture.Flag.MipMapped
        self.m_sky = self._texture((CUBE, CUBE), cube)
        self.m_reflection = self._texture((CUBE, CUBE), cube)
        self._m_done: tuple = ()
        self._m_clouds = None  # the clouds the last run added to the cloudy sky (gameclouds.CClouds)
        self.m_read_back = False  # the next run reads the light buffer back (the tests): ``readback``
        self.m_readback = None

    def _texture(self, size_px, flags):
        texture = self.m_rhi.newTexture(QRhiTexture.Format.RGBA16F, QSize(*size_px), 1, flags)
        texture.create()
        return texture

    def _fill(self, constants: gs.CConstants, sky, slot: SSlot) -> None:
        if slot.key == "clouds":  # their constants first: the sky's own values below win where both name one
            self._m_clouds.fill(constants, None)
        for key, value in {**self.m_atmosphere, **SKY_GEN}.items():
            constants.set(key, value)
        gs.fill_sun(constants, sky.sun, sky.night)  # sky.fx: toSun = -cSunDirection
        direction, right, up = FACES[slot.face]
        for key, value in (("cSkyFaceDir", direction), ("cSkyFaceX", right), ("cSkyFaceY", up),
                           ("cSkyFaceID", slot.face), ("cSkyRoughness", slot.level / (LEVELS - 1)),
                           ("cSkyConvolveClearSky", int(slot.clear))):  # fmt: skip
            constants.set(key, value)

    def run(self, ctx, sky, clouds=None) -> bool:
        """Record the passes when ``sky`` (a ``gamelight.SSky``) or the ``clouds`` (``gameclouds.CClouds``, their
        maps up: they go into the cloudy sky's light, frame-117-viewer.md 12) changed since the last run, the clouds'
        time by ``CLOUD_STEP``; True when they ran."""
        clouds = clouds if clouds is not None and clouds.m_reflected is not None and clouds.maps_ready() else None
        moment = (int(clouds.m_seconds // CLOUD_STEP), clouds.m_wind) if clouds is not None else None
        key = (tuple(np.round(np.asarray(sky.sun, float), 4)), sky.night, moment)
        if key == self._m_done:
            return False
        # only the clouds moved: the tables and the clear sky's coefficients in the light buffer still hold
        clouds_only = clouds is not None and self._m_done[:2] == key[:2]
        self._m_done, self._m_clouds = key, clouds
        tables = {"cAtmosphereTransmittanceTarget": self.m_transmittance, "cAtmosphereMultiscatTarget": self.m_multiscat,
                  "cAtmosphereTransmittanceLUT": self.m_transmittance, "cAtmosphereMultiscatLUT": self.m_multiscat,
                  "cLightDataUAV": self.m_light}  # fmt: skip
        if not clouds_only:
            self._pass(
                ctx, SSlot("transmittance"), tables, sky, (groups(TRANSMITTANCE[0]), groups(TRANSMITTANCE[1]), 1)
            )
            self._pass(ctx, SSlot("multiscat"), tables, sky, (*MULTISCAT, 1))
            self._pass(ctx, SSlot("light"), tables, sky, (1, 1, 1))
        for clear in (False,) if clouds_only else (True, False):  # the clear sky's coefficients, then the cloudy
            if not clear and clouds is not None:  # sky's (the clear one's again without clouds): with the clouds added
                self.m_compiled["clouds"] = clouds.m_reflected
                self._sky_cube(ctx, tables, sky, {**tables, **clouds.inputs()})
            elif clear:
                self._sky_cube(ctx, tables, sky)
            for face in range(6):
                after = self._read_light if self.m_read_back and not clear and face == 5 else None
                self._pass(
                    ctx, SSlot("sh", face, 0, clear), {**tables, "cSkyTexture": self.m_sky}, sky, (1, 1, 1), after
                )
        for level in range(LEVELS):
            for face in range(6):
                self._cube_face(ctx, SSlot("filter", face, level), {**tables, "cSkyTexture": self.m_sky}, sky,
                                self.m_reflection)  # fmt: skip
        return True

    def _sky_cube(self, ctx, tables: dict, sky, clouds: dict | None = None) -> None:
        """The sky cube: every level integrated at its own size (QRhi has no cube mips); with ``clouds`` (their
        inputs) each face's clouds added before it goes into the cube."""
        for level in range(LEVELS):
            for face in range(6):
                self._cube_face(ctx, SSlot("sky", face, level), {**tables}, sky, self.m_sky, clouds)

    def _cube_face(self, ctx, slot: SSlot, inputs: dict, sky, cube, clouds: dict | None = None) -> None:
        """A face of one level of ``cube``: integrated into the level's target (the clouds added with ``clouds``),
        then copied into the cube."""
        size = CUBE >> slot.level
        target = self.m_targets[slot.level]
        after = partial(copy_into, self.m_rhi, cube, target, (size, size), slot.face, slot.level)
        grid = (groups(size), groups(size), 1)
        if clouds is None:
            self._pass(ctx, slot, {**inputs, "cSkyTarget": target}, sky, grid, after)
            return
        self._pass(ctx, slot, {**inputs, "cSkyTarget": target}, sky, grid)
        self._pass(ctx, slot._replace(key="clouds"), {**clouds, "cSkyTarget": target}, sky, grid, after)

    def _pass(self, ctx, slot: SSlot, inputs: dict, sky, grid, after=None) -> None:
        """One dispatch as a compute pass of its own (QRhi orders them); ``after`` makes its closing batch."""
        if slot not in self.m_stages:
            first = self.m_stages.get(SSlot(slot.key)) or self.m_stages.get(SSlot(slot.key, clear=True))
            self.m_stages[slot] = CComputeStage(self.m_rhi, self.m_compiled[slot.key], first)
        record(ctx, self.m_stages[slot], inputs, lambda constants: self._fill(constants, sky, slot), grid, after)

    def _read_light(self):
        """The light buffer read back once (the batch a pass ends with)."""
        after = self.m_rhi.nextResourceUpdateBatch()
        self.m_readback, self.m_read_back = QRhiReadbackResult(), False
        after.readBackBuffer(self.m_light, 0, LIGHT_BYTES, self.m_readback)
        return after

    def release(self) -> None:
        super().release()
        for resource in (
            self.m_light,
            self.m_transmittance,
            self.m_multiscat,
            *self.m_targets,
            self.m_sky,
            self.m_reflection,
        ):
            resource.destroy()
