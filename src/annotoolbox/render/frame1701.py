"""Anno 1701's models drawn forward-lit by the game's own Direct3D 9 shaders on QRhi (Direct3D 11): ``mesh.fx``'s
``Object`` (lit per vertex, or per pixel from a normal map; fogged by depth), its ``ShadowMapToObject`` over the
sun's shadow map of ``ObjectToShadowMap``, composited as 1404's frame does (gamma values, with depth).

``Frame1701(ctx)`` is the entry (``Frame1404``'s ``render``, the sky of ``sky1701`` first); ``Model1701`` is one mesh
group, ``Light1701`` the light of an ambient profile (``read_light``), ``light_values`` / ``shadow_values`` the
constants by name. docs/rendering/shader-compile.md section 13.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..othergames.isd1701 import TILE
from . import frame2070
from . import gameshader as gs
from .frame1404 import Frame1404, Model1404, colour_in, number_in, profile_block

TECHNIQUE = "Object"
SHADOWED = "ShadowMapToObject"
CASTER = "ObjectToShadowMap"
# a map the material lacks: a flat normal without gloss, no dye mask; the rest white
FALLBACKS = {"samplerTexBump": (128, 128, 255, 0), "samplerTexOpacity": (0, 0, 0, 0)}
# data/config/game/shadow.xml (the highest quality): where the shadow fades out, in world units (TILE a tile)
FADE_START, FADE_DISTANCE = 2700.0, 500.0
# vcGlbBias.x in the fitted depth range's units (ours, as 2070's: the engine's zBias is over its own range)
SHADOW_BIAS = frame2070.SHADOW_BIAS
# vcGrnConstants: no heat, the y and w the comment gives, fully opaque; pcGrnConstants: no dirt, its brightness 1
GRANNY = {"vcGrnConstants": [0.0, 1.0, 1.0, 1.0], "pcGrnConstants": [0.0, 1.0, 0.0, 1.0]}


@dataclass(frozen=True)
class Light1701:
    """The light of an ambient profile: the direction towards the sun (the view's), the ambient, diffuse and
    specular colours, the fog's colour and its start and end (world units, ``TILE`` a tile), the time of day."""

    direction: tuple[float, float, float]
    ambient: tuple[float, float, float] = (0.486, 0.584, 0.686)
    diffuse: tuple[float, float, float] = (0.992, 0.949, 0.804)
    specular: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fog: tuple[float, float, float] = (0.475, 0.678, 0.886)
    fog_start: float = 5000.0
    fog_end: float = 10000.0
    time_of_day: float = 12.0  # hours (light.xml m_TimeOfDay): the sky texture's column
    seconds: float = 0.0  # the view's time: the clouds' scroll and turn


PROFILE = "North"  # the profile of the northern islands (DefaultNorthIslandProfile)
AMBIENT = "data/config/game/ambientmap.xml"
LIGHT = "data/config/game/light.xml"


def read_light(ambient: str, light: str = "", name: str = PROFILE) -> Light1701:
    """The light of a profile of ``ambientmap.xml`` (its colours and fog as stored) and the specular colour and time
    of day of ``light.xml``; a value the files lack keeps ``Light1701``'s."""
    body = profile_block(ambient, name)
    base = Light1701((0.0, 1.0, 0.0))

    def colour(within: str, tag: str, fallback: tuple) -> tuple:
        found = colour_in(within, tag)
        return found[:3] if found is not None else fallback

    return Light1701(base.direction, colour(body, "AmbientColor", base.ambient),
                     colour(body, "DiffuseColor", base.diffuse), colour(light, "m_Specular", base.specular),
                     colour(body, "FogColor", base.fog), number_in(body, "FogStart", base.fog_start),
                     number_in(body, "FogEnd", base.fog_end), number_in(light, "m_TimeOfDay", base.time_of_day))  # fmt: skip


def time_values(seconds: float) -> dict:
    """The engine's clocks as ``constants.fx`` describes them: ``vcGlbTime`` the time and its half, quarter and tenth
    repeating between 0 and 1, ``vcGlbTime2`` the time and its twentieth, hundredth and thousandth (the water's
    scroll); seconds of the view's time **[I]**."""
    t = float(seconds)
    return {"vcGlbTime": [t % 1.0, (t / 2) % 1.0, (t / 4) % 1.0, (t / 10) % 1.0],
            "vcGlbTime2": [t, t / 20, t / 100, t / 1000]}  # fmt: skip


def light_values(light: Light1701, cam: gs.Camera, world: np.ndarray) -> dict:
    """The colour pass's constants by name for an object at ``world`` (column vectors): its matrices as ours (row
    vectors: the shader's column-major ``mul(M, v)`` reads them turned, as 1404's), the camera, the light (plain
    and in ``vcLightingDir``), the depth fog in the view's units, no morph, no dirt, the dye colour white."""
    w = np.asarray(world, float).T
    toward = np.asarray(light.direction, float)
    lit = {"vcLgtAmbient": [*light.ambient, 1.0], "vcLgtDiffuse": [*light.diffuse, 1.0],
           "vcLgtSpecular": [*light.specular, 1.0], "vcLgtDirection": [*-toward, 0.0], "vcCamPosition": [*cam.eye, 1]}  # fmt: skip
    start, end = light.fog_start / TILE, light.fog_end / TILE
    fog = [start, end, 1.0, 1.0 / max(1e-6, end - start)]
    return {
        **time_values(light.seconds),
        **lit, **{f"vcLightingDir.{k}": v for k, v in lit.items()}, "vcMtxWorldViewProj": w @ cam.view @ cam.proj,
        "vcMtxViewProj": cam.view @ cam.proj, "vcGlbFogSEDC": fog, "vcFog.vcGlbFogSEDC": fog,
        "vcGlbFogColor": [*light.fog, 1.0], "cFixedFogColor": [*light.fog, 1.0], "vcObjMorphConstants": [0.0] * 4,
        "pcShdColor": [*light.ambient, 1.0], **GRANNY,
    }  # fmt: skip


def shadow_values(down, corners: np.ndarray, beyond=None) -> tuple[dict, dict]:
    """The casters' and the colour pass's shadow constants for one orthographic map fitted round ``corners``
    (``shadowforward.fit``; ``beyond``: receivers its depth reaches too, the sea): ``vcMtxShadowBias`` the map's
    matrix for the casters and the same times Direct3D's texture transform for the receivers **[I]**,
    ``vcShdConstants`` the plane the depth is measured on, the bias (the same distance however far the depth
    reaches), and where the shadow fades (``shadow.xml``)."""
    found, bias = frame2070.fitted(down, corners, beyond, SHADOW_BIAS)
    plane = found.values["cVecShadowVPDN"]
    cast = {"vcMtxShadowBias": found.view_proj, "vcShdConstants": plane}
    fade = [FADE_START / TILE, FADE_DISTANCE / TILE, 0.0, 0.0]
    receive = {"vcMtxShadowBias": found.view_proj @ frame2070.TEXTURE, "vcShdConstants": plane,
               "vcGlbBias": [bias, 0.0, 0.0, 0.0], "vcGlbShadowFade": fade}  # fmt: skip
    return cast, receive


class Model1701(Model1404):
    """One mesh group (``Model1404``'s draws: ``Object``, ``ShadowMapToObject``, ``ObjectToShadowMap``); ``color``
    is the dye colour ``pcObjDyeColor`` (the player's: only the dye effects read it)."""

    FALLBACKS = FALLBACKS

    def fill_model(self, c: dict) -> None:
        w = np.asarray(self.world, float)
        gs.set_values(c.values(), {"vcMtxWorld": w.T, "vcMtxWorldInv": np.linalg.inv(w).T,
                                   "pcObjDyeColor": [*self.color, 1.0]})  # fmt: skip

    def frame_values(self, cam: gs.Camera, sun: Light1701) -> dict:
        return light_values(sun, cam, self.world)


class Frame1701(Frame1404):
    """1404's frame (the offscreen target in gamma values, the composite) with 1701's sun shadow and its sky
    (``backdrop``: a ``sky1701.Sky1701`` drawn before the models, None while it compiles or when it is off)."""

    SHADOW = {"values_of": shadow_values, "texture": "samplerTexShadowMap"}

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.backdrop = None

    def _behind(self, size, cam: gs.Camera, sun) -> bool:
        return self.backdrop is not None and self.backdrop.draw(self.fbo, size, cam, sun)

    def release(self) -> None:
        super().release()
        if self.backdrop is not None:
            self.backdrop.release()
            self.backdrop = None
