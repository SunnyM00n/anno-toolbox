"""Anno 117's visible sky and haze by the game's environment shaders (``environment.fx``): the depth at half size,
the clouds' and the fog and atmosphere's scattering per half-size pixel, upsampled by depth and blended over the lit
colour (``colour = inscatter + transmittance x colour``), the sun's disc on the sky.

``Atmosphere(rhi, stages, atmosphere)`` is the entry (the compiled shaders, ``STAGES``; the ambient's
``gamesky.read_atmosphere`` values); ``run(ctx, depth, lit_fbo, size, camera, sky, inputs)`` records the passes after
the transparent pass (``Look``: blur, fade; ``gameclouds.Clouds``: the clouds). The engine's values and the viewer's
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
from .gamecompute import ComputeChain, ComputeStage, groups, record
from .gamedraw import Program, stand_in, upload_level
from .gamepost import PostPass, full_screen
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
# an island's fog (``Atmosphere.set_fog``): the engine's own values, which no file holds (noise: scale, strength,
# speed), the timing globals.xml's (island-117-view.md "Phase 7: the session fog", round 11)
FOG = {"cFogDensityNoise": [0.014, 0.75, 0.11], "cFogHeight": 90.0, "cFogColorNoise": [0.04, 0.39, 0.12],
       "cFogDensity": 0.012, "cFogColorFactor": [1.0, 1.0, 1.0], "cFogFadeInStart": 0.13, "cFogFadeInEnd": 0.19,
       "cFogFadeOutStart": 0.21, "cFogFadeOutEnd": 0.3}  # fmt: skip
# the cloud layer's dome bounds the atmosphere's passes even without clouds: the engine's (frame-117-viewer.md 12)
CLOUD_LAYER = {"cCloudDomeRadius": gameclouds.DOME_RADIUS, "cWeathermapScale": gameclouds.WEATHERMAP_SCALE,
               "cCloudLayerHeight": gameclouds.LAYER_HEIGHT}  # fmt: skip


@dataclass(frozen=True)
class Look:
    """Which optional parts of the passes run: the blur, the haze's fade."""

    blur: bool = True
    fade: bool = True


class Atmosphere(ComputeChain):
    """The passes and their half-size targets (module docstring)."""

    def __init__(self, rhi, stages: dict, atmosphere: dict) -> None:
        self.rhi, self.atmosphere = rhi, atmosphere
        self.stages = {key: ComputeStage(rhi, stages[key], stand_in=self._white)
                       for key in ("clouds", "end", "fog", "blur.h", "blur.v")}  # fmt: skip
        self.down = PostPass(rhi, Program(stages["quad"], stages["down"]), depth_always=True)
        self.up = PostPass(rhi, Program(stages["quad"], stages["up"]), pass_state(stages["up"].state))
        self.half = self.target = self.half_fbo = self.blurred = None
        self.size: tuple[int, int] | None = None
        self._white_tex = None
        self.fog = None  # the island's fog map (``set_fog``; None: no fog, a model view)
        self.fog_size = 0.0  # the island's side the fog map covers
        self.heights = None  # the session's coarse height texture the fog stands on (None: none)
        self._fog_layers: np.ndarray | None = None  # the map's texels to upload

    def set_fog(self, raw: bytes, size: float) -> None:
        """The island's fog map (``rd3d`` ``Fog`` bytes, ``fog_layers``) over its ``size``: the fog from the next run;
        b"": none."""
        layers = fog_layers(raw) if raw else None
        self._fog_layers = layers if layers is not None and layers.size else None  # too short for a texel: none
        self.fog_size = float(size)
        if self.fog is not None:
            self.fog.destroy()
        self.fog = None

    @property
    def error(self) -> str:
        return super().error or self.down.error or self.up.error

    def _white(self, batch, name: str, kind: str):
        """The stand-in that reads 1 (``WHITE``), None for the rest (``gamebind``'s zeros)."""
        if kind != "texture" or name not in WHITE:
            return None
        if self._white_tex is None:
            self._white_tex = stand_in(self.rhi, "texture", "Texture2D", batch, 1.0)
        return self._white_tex

    def _targets(self, ctx, size: tuple[int, int]) -> None:
        if self.size == size:
            return
        self._drop()
        half = (groups(size[0], DOWNSAMPLE), groups(size[1], DOWNSAMPLE))
        self.half = ctx.depth_renderbuffer(half)
        self.half_fbo = ctx.framebuffer([], self.half)
        self.target = ctx.native_texture(half, QRhiTexture.Format.RGBA16F, storage=True)
        self.blurred = ctx.native_texture(half, QRhiTexture.Format.RGBA16F, storage=True)
        self.size = size

    def _fill(self, c: gs.Constants, camera: gs.Camera, sky, look: Look, clouds=None) -> None:
        gs.fill_frame(c, camera)
        gs.fill_sun(c, sky.sun, sky.night)
        for key, value in {**self.atmosphere, **ENVIRONMENT, **(FADE if look.fade else NO_FADE)}.items():
            c.set(key, value)
        for key, value in CLOUD_LAYER.items():
            c.set(key, value)
        c.set("cGlobalFlags", 0)  # GCF_CLOUDS clear: the clouds' pass writes "no clouds"
        c.set("cEnvironmentFlags", 0)
        if self.fog is not None:  # the island's fog over the session's heights (none handed: the terrain at 0)
            s, valid = self.fog_size, int(self.heights is not None)
            for key, value in {**FOG, "cTerrainSize": [s, s, 1 / s, 1 / s], "cTerrainHeightTexIsValid": valid}.items():
                c.set(key, value)
        if clouds is not None:
            clouds.fill(c, camera)

    def run(self, ctx, lit_fbo, size: tuple[int, int], camera: gs.Camera, sky, inputs: dict,
            look: Look | None = None, clouds=None) -> None:  # fmt: skip
        """The passes over the G-buffer's depth into the lit colour of ``lit_fbo``; ``inputs``: that depth
        (``cDepthBuffer``), the sky passes' tables, the shadow map and the cascades' information, the light buffer,
        the blue noise, by name; ``look``: which of the optional parts run; ``clouds`` (``gameclouds.Clouds``): the
        clouds, None for none."""
        look = look or Look()
        self._targets(ctx, size)
        half = tuple(self.target.size[:2])

        def fill(c: gs.Constants) -> None:
            self._fill(c, camera, sky, look, clouds)

        named = {**inputs, "cDownsampledDepthBuffer": self.half.native, "cScatteringTarget": self.target.native,
                 "cScatteringTexture": self.target.native}  # fmt: skip
        if self._fog_layers is not None:
            self._upload_fog(ctx)
        if self.fog is not None:
            named["cFogMapTexture"] = self.fog
            if self.heights is not None:
                named["cTerrainHeightTex"] = self.heights
        if clouds is not None:
            clouds.update(ctx, camera, sky, named)  # the self-shadow cache the clouds' march reads
            named.update(clouds.inputs())
        full_screen(ctx, self.down, self.half_fbo, half, named, fill, targets=0, clear=False)
        grid = (groups(half[0], GROUP), groups(half[1], GROUP), 1)
        record(ctx, self.stages["clouds"], named, fill, grid)
        # the atmosphere beyond the cloud layer, then the fog and the atmosphere before it
        record(ctx, self.stages["end"], named, fill, (groups(half[0], BLUR_GROUP), groups(half[1], BLUR_GROUP), 1))
        record(ctx, self.stages["fog"], named, fill, grid)
        if look.blur:  # across into the second target and back (SCATTER_BLUR_ENABLE)
            blur = (groups(half[0], BLUR_GROUP), groups(half[1], BLUR_GROUP), 1)
            for key, src, dst in (("blur.h", self.target, self.blurred), ("blur.v", self.blurred, self.target)):
                record(ctx, self.stages[key], {**named, "cScatteringBlurSrc": src.native,
                                               "cScatteringBlurDst": dst.native}, fill, blur)  # fmt: skip
        targets = len(lit_fbo.color_attachments)
        full_screen(ctx, self.up, lit_fbo, size, named, fill, targets=targets, clear=False)

    def _upload_fog(self, ctx) -> None:
        """The fog map as a two-layer RGBA8 array (the shader's ``Texture2DArray``), uploaded by a pass of its own."""
        layers, self._fog_layers = self._fog_layers, None
        n = layers.shape[1]
        self.fog = self.rhi.newTextureArray(QRhiTexture.Format.RGBA8, 2, QSize(n, n))
        self.fog.create()
        fog = self.fog

        def upload(cb, batch) -> None:  # an empty compute pass hands the batch on
            for k in range(2):
                upload_level(batch, fog, np.ascontiguousarray(layers[k]).tobytes(), layer=k)
            cb.beginComputePass(batch)
            cb.endComputePass()

        ctx.native_pass(upload)

    def _drop(self) -> None:
        for r in (self.half_fbo, self.half, self.target, self.blurred):
            if r is not None:
                r.release()
        self.half = self.target = self.half_fbo = self.blurred = None
        self.size = None

    def release(self) -> None:
        super().release()
        self.down.release()
        self.up.release()
        self._drop()
        if self._white_tex is not None:
            self._white_tex.destroy()
            self._white_tex = None
        if self.fog is not None:
            self.fog.destroy()
            self.fog = None


def fog_layers(raw: bytes) -> np.ndarray:
    """An island's fog map (``rd3d`` ``Fog``: per texel of 4 x 4 tiles, rows along z, 8 bytes: ground density and its
    height factor, sea-level density and its height factor, the colour and the type) as the shader's two layers
    ([2, n, n, 4] bytes), x turned over into the view's mirrored axes."""
    n = math.isqrt(len(raw) // 8)  # a map cut short: its whole rows
    texels = np.frombuffer(raw[: n * n * 8], np.uint8).reshape(n, n, 2, 4)[:, ::-1]
    return np.ascontiguousarray(texels.transpose(2, 0, 1, 3))
