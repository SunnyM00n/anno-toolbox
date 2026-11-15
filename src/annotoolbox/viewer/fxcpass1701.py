"""The viewer's look by Anno 1701's own shaders: each material's Direct3D 9 effect compiled from the install by
``d3dcompiler_43`` as shader model 4 (``models.shader1701``) and drawn forward-lit on Direct3D 11 with the sun's
shadow and its sky and clouds (``render.frame1701``, ``sky1701``); a group that does not compile keeps our look.

``FxcPass1701(renderer)`` is 1404's pass (``fxcpass1404.FxcPass1404``) with 1701's source, frame, sky and maps; no
post effects (``frame1701.read_light`` reads its ambient profile). docs/rendering/shader-compile.md section 13.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..models import shader1701
from ..othergames.isd1701 import TILE
from ..render import frame1701, fxcdraw, ocean1701, sky1701
from ..render import gameshader as gs
from ..render.frame1404 import config_text, granny_mesh
from ..render.frame1701 import AMBIENT, LIGHT, PROFILE, Frame1701, Model1701, read_light
from .fxcpass import old_compiler, optional_pass
from .fxcpass1404 import FxcPass1404, granny_vertices
from .ownpass import PLAYER_COLOR

GAME = "anno1701"
NO_FOG = 1e9  # the fog's start with the haze switched off (world units)


class _Source:
    """What the pass needs of the install, read once in the background: the compiler, the sources and the light."""

    def __init__(self, tree) -> None:
        self.compiler = old_compiler(tree, GAME)
        self.shaders = shader1701.Source1701(tree, self.compiler)
        ambient = config_text(tree, AMBIENT)
        self.sun = read_light(ambient, config_text(tree, LIGHT))
        self.ocean = ocean1701.ocean_color(ambient, PROFILE)


def sky_work(source: _Source) -> tuple:
    """In the background: the sky dome's and the clouds' passes."""
    return tuple(source.shaders.effect_pass(fx, (), sky1701.TECHNIQUE) for fx in (sky1701.SKY_FX, sky1701.CLOUDS_FX))


class FxcPass1701(FxcPass1404):
    """The parts of one scene as 1701's shaders draw them (module docstring)."""

    GAME = GAME
    PREFIX = "fxc1701"
    SOURCE = _Source
    FRAME, MODEL = Frame1701, Model1701
    WATER_MODEL = Model1701  # mesh/water.fx is an ordinary model effect
    COLOR_MAP = "samplerTexDiffuseColor"
    CUBES = ("samplerCubeTexReflection",)

    def _sea_now(self):
        """The sea's draw for this frame (``Frame1701.sea``): Ocean waves on, a ground under the model and the sea's
        passes and maps in; None otherwise. In the game's world units (the view counts tiles): no terrain under the
        model, so the depth map reads deep water and nothing is mirrored but the sky cube **[E]**."""
        view = self._sea_view("ocean1701", lambda s: {k: s.shaders.effect_pass(*v) for k, v in ocean1701.STAGES.items()},
                              lambda got: ocean1701.Ocean1701(self.r.ctx, got, self._dummies()))  # fmt: skip
        if view is None:
            return None
        camera, size = view
        waves, sky_cube = self._map(ocean1701.WAVES), self._cube(ocean1701.SKY_CUBE)
        if self.sea is None or waves is None or sky_cube is None:
            return None
        near, far = camera.planes()
        eye, target = np.asarray(camera.eye(), float) * TILE, np.asarray(camera.target, float) * TILE
        cam = gs.direct_camera(size, eye, target, camera.fov, near * TILE, far * TILE, self.GAME_AXES)
        self.sea.mirrored = self.GAME_AXES
        maps = {"cSamplerWave": waves, "cSamplerSkyRef": sky_cube}
        source, ocean = self._ready(), self.sea

        def draw(fbo, at: tuple[int, int], scene, _depth, shadow=None) -> None:  # the frame's light is set by then
            light, sun = self.sun_now, ocean1701.shadow_inputs(shadow, TILE) if shadow is not None else None
            ocean.draw(fbo, at, (cam, cam), light, {**maps, "cSamplerRefrac": scene}, light.seconds, source.ocean, sun)

        return draw

    def _work(self, source: _Source, part, item):
        """In the background: the group's three passes, its vertices (with tangent frames for a normal-mapped
        effect, a second uv where the effect reads one), its maps by sampler name, its dye colour and whether it is
        a water sheet."""
        data = granny_vertices(item)
        values = dict(part.material(item["key"]).values)
        stages = [source.shaders.model_pass(values, frame1701.TECHNIQUE)]
        for technique in (frame1701.SHADOWED, frame1701.CASTER):
            stages.append(optional_pass(lambda t=technique: source.shaders.model_pass(values, t)))
        semantics = {i.semantic for i in stages[0].vs.inputs}
        mesh = granny_mesh(
            data.vertices, item["idx"], tangents="TANGENT" in semantics, uvs=2 if "TEXCOORD1" in semantics else 1
        )
        maps = {name: values.get(f"Texture.{slot}", "") for name, slot in shader1701.MAPS.items()}
        # water.fx tints the sheet by pcObjDyeColor, which the dye effects read as the player's: white there **[I]**
        water = shader1701.model_effect(values).endswith("/" + shader1701.WATER)
        return stages, mesh, maps, (1.0, 1.0, 1.0) if water else PLAYER_COLOR, water

    def _light_now(self, setting, sun: tuple, seconds: float = 0.0):
        """The profile's light with the view's sun and time; View > Rendering's haze switch off pushes the depth fog
        beyond any distance (its factor ``(end - z) / (end - start)`` saturates to 1)."""
        light = replace(setting, direction=sun, seconds=seconds)
        return light if self._on("atmosphere") else replace(light, fog_start=NO_FOG, fog_end=2 * NO_FOG)

    def _sky(self, frame) -> None:
        """The frame's sky dome and clouds (``sky1701``) as compiled and switched on (The visible sky and haze: both,
        Clouds: the layers), their textures once they are in."""
        stages = self._sky_compiled("sky1701", sky_work)
        make = (lambda: sky1701.Sky1701(self.r.ctx, *stages, self._dummies())) if stages is not None else None
        sky = fxcdraw.switched(frame, "backdrop", self._on("atmosphere"), make)
        if sky is not None:
            sky.sky_map = self._map(sky1701.SKY_TEXTURE)
            sky.cloud_map = self._map(sky1701.CLOUD_TEXTURE)
            sky.clouds = self._on("clouds")

    def finish(self, source, size: tuple[int, int], lut=None):
        """No post effects: the default sequence is a copy, glow and heat take only effects' input (the picture as
        it is)."""
        return source if self.frame is not None and self.sun_now is not None else None
