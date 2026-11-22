"""Anno 117's visible sky and haze by the game's environment shaders (``environment.fx``): the depth at half size,
the clouds' and the fog and atmosphere's scattering per half-size pixel, upsampled by depth and blended over the lit
colour (``colour = inscatter + transmittance x colour``), the sun's disc on the sky.

``CAtmosphere(rhi, stages, atmosphere)`` is the entry (the compiled shaders, ``STAGES``; the ambient's
``gamesky.read_atmosphere`` values); ``run(ctx, depth, lit_fbo, size_px, camera, sky, inputs)`` records the passes after
the transparent pass (``SLook``: blur, fade; ``gameclouds.CClouds``: the clouds). The engine's values and the viewer's
stand-ins: docs/rendering/frame-117-viewer.md sections 8, 11 and 12.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import gameclouds
from . import gameshader as gs
from .gamecompute import CComputeChain, CComputeStage, groups, record
from .gamedraw import CProgram, stand_in, upload_level
from .gamepost import CPostPass, full_screen
from .renderstates import pass_state

EFFECT = "data/shaders/fx/environment.fx"
# key -> (effect, technique, function): what ``ShaderSource.stage_set`` compiles; the upsample's vertex shader is
# the same full-screen strip as the downsample's
STAGES = {"quad": (EFFECT, "DownsampleDepth", "vsCommonFullScreenQuad"),
          "down": (EFFECT, "DownsampleDepth", "psDownsampleDepth"),
          "clouds": (EFFECT, "ScatteringVolumeClouds", "csVolumetricClouds"),
          "end": (EFFECT, "ComputeAtmosphereEnd", "csComputeAtmosphereEnd"),
          "fog": (EFFECT, "ScatteringVolumeFog", "csVolumetricFog"),
          "blur.h": (EFFECT, "BlurScattering", "csBlurScattering"),
          "blur.v": (EFFECT, "BlurScattering", "csBlurScattering#1"),
          "up": (EFFECT, "UpsampleScatteringVolumes", "psUpsampleScatteringVolumes")}  # fmt: skip
DOWNSAMPLE = 2  # DOWNSAMPLE_FACTOR of the shipped variant
GROUP, BLUR_GROUP = 16, 8  # the scattering passes' numthreads, the blur's and the atmosphere's end
WHITE = ("cShadowWeatherMap", "cTransmittanceMap")  # no cloud shadow: clear air (the clouds hand their own)
# the haze's fade near the camera: the engine's environment record's first seven floats [I] (frame-117-viewer.md 11)
FADE = {"cScatterFadeSunY": 0.35, "cScatterFadeSunYBlend": 0.1, "cScatterFadeRayY": -0.4, "cScatterFadeRayYBlend": 0.2,
        "cScatterFadeMinScatter": 0.05, "cScatterFadeScatterDist": 100.0, "cScatterFadeScatterBlendDist": 40.0}  # fmt: skip
NO_FADE = {**FADE, "cScatterFadeMinScatter": 1.0}
# ours: no distant band, no fog [E]
ENVIRONMENT = {"cAtmosphereDistantScatterStrength": 0.0, "cVolumetricMaxDistance": 3000.0, "cFogDensity": 0.0}
# an island's fog (``CAtmosphere.set_fog``): the engine's own values, which no file holds (noise: scale, strength,
# speed), the timing globals.xml's (island-117-view.md "Phase 7: the session fog", round 11)
FOG = {"cFogDensityNoise": [0.014, 0.75, 0.11], "cFogHeight": 90.0, "cFogColorNoise": [0.04, 0.39, 0.12],
       "cFogDensity": 0.012, "cFogColorFactor": [1.0, 1.0, 1.0], "cFogFadeInStart": 0.13, "cFogFadeInEnd": 0.19,
       "cFogFadeOutStart": 0.21, "cFogFadeOutEnd": 0.3}  # fmt: skip
# the cloud layer's dome bounds the atmosphere's passes even without clouds: the engine's (frame-117-viewer.md 12)
CLOUD_LAYER = {"cCloudDomeRadius": gameclouds.DOME_RADIUS, "cWeathermapScale": gameclouds.WEATHERMAP_SCALE,
               "cCloudLayerHeight": gameclouds.LAYER_HEIGHT}  # fmt: skip


@dataclass(frozen=True)
class SLook:
    """Which optional parts of the passes run: the blur, the haze's fade."""

    blur: bool = True
    fade: bool = True


class CAtmosphere(CComputeChain):
    """The passes and their half-size targets (module docstring)."""

    def __init__(self, rhi, stages: dict, atmosphere: dict) -> None:
        self.m_rhi, self.m_atmosphere = rhi, atmosphere
        self.m_stages = {key: CComputeStage(rhi, stages[key], stand_in=self._white)
                       for key in ("clouds", "end", "fog", "blur.h", "blur.v")}  # fmt: skip
        self.m_down = CPostPass(rhi, CProgram(stages["quad"], stages["down"]), depth_always=True)
        self.m_up = CPostPass(rhi, CProgram(stages["quad"], stages["up"]), pass_state(stages["up"].state))
        self.m_half = self.m_target = self.m_half_fbo = self.m_blurred = None
        self.m_size_px: tuple[int, int] | None = None
        self._m_white_tex = None
        self.m_fog = None  # the island's fog map (``set_fog``; None: no fog, a model view)
        self.m_fog_size = 0.0  # the island's side the fog map covers
        self.m_heights = None  # the session's coarse height texture the fog stands on (None: none)
        self._m_fog_layers: np.ndarray | None = None  # the map's texels to upload

    def set_fog(self, raw: bytes, size_tiles: float) -> None:
        """The island's fog map (``rd3d`` ``Fog`` bytes, ``fog_layers``) over its ``size_tiles``: the fog from the next run;
        b"": none."""
        layers = fog_layers(raw) if raw else None
        self._m_fog_layers = layers if layers is not None and layers.size else None  # too short for a texel: none
        self.m_fog_size = float(size_tiles)
        if self.m_fog is not None:
            self.m_fog.destroy()
        self.m_fog = None

    @property
    def m_error(self) -> str:
        return super().m_error or self.m_down.m_error or self.m_up.m_error

    def _white(self, batch, name: str, kind: str):
        """The stand-in that reads 1 (``WHITE``), None for the rest (``gamebind``'s zeros)."""
        if kind != "texture" or name not in WHITE:
            return None
        if self._m_white_tex is None:
            self._m_white_tex = stand_in(self.m_rhi, "texture", "Texture2D", batch, 1.0)
        return self._m_white_tex

    def targets(self, ctx, size_px: tuple[int, int]) -> None:
        """The half-size depth and the two scattering targets for a view of ``size_px``, made when it changed."""
        if self.m_size_px == size_px:
            return
        self._drop()
        half = (groups(size_px[0], DOWNSAMPLE), groups(size_px[1], DOWNSAMPLE))
        self.m_half = ctx.depth_renderbuffer(half)
        self.m_half_fbo = ctx.framebuffer([], self.m_half)
        self.m_target = ctx.native_texture(half, QRhiTexture.Format.RGBA16F, storage=True)
        self.m_blurred = ctx.native_texture(half, QRhiTexture.Format.RGBA16F, storage=True)
        self.m_size_px = size_px

    def fill(self, constants: gs.CConstants, camera: gs.SCamera, sky, look: SLook, clouds=None) -> None:
        """The passes' constants by name (the native atmosphere's too: ``atmospherenative``)."""
        gs.fill_frame(constants, camera)
        gs.fill_sun(constants, sky.sun, sky.night)
        for key, value in {**self.m_atmosphere, **ENVIRONMENT, **(FADE if look.fade else NO_FADE)}.items():
            constants.set(key, value)
        for key, value in CLOUD_LAYER.items():
            constants.set(key, value)
        constants.set("cGlobalFlags", 0)  # GCF_CLOUDS clear: the clouds' pass writes "no clouds"
        constants.set("cEnvironmentFlags", 0)
        if self.m_fog is not None:  # the island's fog over the session's heights (none handed: the terrain at 0)
            fog_size, valid = self.m_fog_size, int(self.m_heights is not None)
            for key, value in {
                **FOG,
                "cTerrainSize": [fog_size, fog_size, 1 / fog_size, 1 / fog_size],
                "cTerrainHeightTexIsValid": valid,
            }.items():
                constants.set(key, value)
        if clouds is not None:
            clouds.fill(constants, camera)

    def run(self, ctx, lit_fbo, size_px: tuple[int, int], camera: gs.SCamera, sky, inputs: dict,
            look: SLook | None = None, clouds=None) -> None:  # fmt: skip
        """The passes over the G-buffer's depth into the lit colour of ``lit_fbo``; ``inputs``: that depth
        (``cDepthBuffer``), the sky passes' tables, the shadow map and the cascades' information, the light buffer,
        the blue noise, by name; ``look``: which of the optional parts run; ``clouds`` (``gameclouds.CClouds``): the
        clouds, None for none."""
        look = look or SLook()
        self.targets(ctx, size_px)
        half = tuple(self.m_target.size[:2])

        def fill(constants: gs.CConstants) -> None:
            self.fill(constants, camera, sky, look, clouds)

        named = {**inputs, "cDownsampledDepthBuffer": self.m_half.native, "cScatteringTarget": self.m_target.native,
                 "cScatteringTexture": self.m_target.native}  # fmt: skip
        if self.fog_map(ctx) is not None:
            named["cFogMapTexture"] = self.m_fog
            if self.m_heights is not None:
                named["cTerrainHeightTex"] = self.m_heights
        if clouds is not None:
            clouds.update(ctx, camera, sky, named)  # the self-shadow cache the clouds' march reads
            named.update(clouds.inputs())
        full_screen(ctx, self.m_down, self.m_half_fbo, half, named, fill, targets=0, clear=False)
        grid = (groups(half[0], GROUP), groups(half[1], GROUP), 1)
        record(ctx, self.m_stages["clouds"], named, fill, grid)
        # the atmosphere beyond the cloud layer, then the fog and the atmosphere before it
        record(ctx, self.m_stages["end"], named, fill, (groups(half[0], BLUR_GROUP), groups(half[1], BLUR_GROUP), 1))
        record(ctx, self.m_stages["fog"], named, fill, grid)
        if look.blur:  # across into the second target and back (SCATTER_BLUR_ENABLE)
            blur = (groups(half[0], BLUR_GROUP), groups(half[1], BLUR_GROUP), 1)
            for key, source, destination in (
                ("blur.h", self.m_target, self.m_blurred),
                ("blur.v", self.m_blurred, self.m_target),
            ):
                record(ctx, self.m_stages[key], {**named, "cScatteringBlurSrc": source.native,
                                               "cScatteringBlurDst": destination.native}, fill, blur)  # fmt: skip
        targets = len(lit_fbo.color_attachments)
        full_screen(ctx, self.m_up, lit_fbo, size_px, named, fill, targets=targets, clear=False)

    def fog_map(self, ctx):
        """The island's fog map, uploaded once it was set (None: no fog)."""
        if self._m_fog_layers is not None:
            self._upload_fog(ctx)
        return self.m_fog

    def _upload_fog(self, ctx) -> None:
        """The fog map as a two-layer RGBA8 array (the shader's ``Texture2DArray``), uploaded by a pass of its own."""
        layers, self._m_fog_layers = self._m_fog_layers, None
        layer_count = layers.shape[1]
        self.m_fog = self.m_rhi.newTextureArray(QRhiTexture.Format.RGBA8, 2, QSize(layer_count, layer_count))
        self.m_fog.create()
        fog = self.m_fog

        def upload(command_buffer, batch) -> None:  # an empty compute pass hands the batch on
            for k in range(2):
                upload_level(batch, fog, np.ascontiguousarray(layers[k]).tobytes(), layer=k)
            command_buffer.beginComputePass(batch)
            command_buffer.endComputePass()

        ctx.native_pass(upload)

    def _drop(self) -> None:
        for resource in (self.m_half_fbo, self.m_half, self.m_target, self.m_blurred):
            if resource is not None:
                resource.release()
        self.m_half = self.m_target = self.m_half_fbo = self.m_blurred = None
        self.m_size_px = None

    def release(self) -> None:
        super().release()
        self.m_down.release()
        self.m_up.release()
        self._drop()
        if self._m_white_tex is not None:
            self._m_white_tex.destroy()
            self._m_white_tex = None
        if self.m_fog is not None:
            self.m_fog.destroy()
            self.m_fog = None


def fog_layers(raw: bytes) -> np.ndarray:
    """An island's fog map (``rd3d`` ``Fog``: per texel of 4 x 4 tiles, rows along z, 8 bytes: ground density and its
    height factor, sea-level density and its height factor, the colour and the type) as the shader's two layers
    ([2, n, n, 4] bytes), x turned over into the view's mirrored axes."""
    side = math.isqrt(len(raw) // 8)  # a map cut short: its whole rows
    texels = np.frombuffer(raw[: side * side * 8], np.uint8).reshape(side, side, 2, 4)[:, ::-1]
    return np.ascontiguousarray(texels.transpose(2, 0, 1, 3))
