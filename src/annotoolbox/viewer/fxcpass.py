"""The viewer's look by Anno 2070's own shaders (``models.shader2070``, the game's compiler) on Direct3D 11: the
G-buffer, the shadow, the deferred shading, the sky, the point and spot lights and the post sequence
(``render.frame2070``, ``lights2070``, ``sky2070``, ``post2070``), model water, cloth and the blended pass (foam,
icons, faded objects); a material without such a pass (cut-outs, waterfalls) or that does not compile keeps our look.

``CFxcPass(renderer)`` is an ``ownpass.COwnPass`` with the interface of ``gamepass.CGamePass`` (``why_not``, ``busy``,
``draw``, ``finish``, ``release_scene``, ``release``): ``nativelook.GAMES`` names it. docs/rendering/shader-compile.md
section 10; docs/tool-suite.md section 21.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from ..infra import fxc
from ..infra.i18n import N_
from ..models import shader2070, shaderview
from ..models.shader2070 import CNoPass
from ..render import cloth2070, clothforward, frame2070, fxcdraw, lights2070, nativelook, ocean2070, post2070, sky2070
from ..render.frame2070 import SLOT_NAMES, CFrame2070, CModel2070, SSun2070
from ..render.gamedraw import CMesh, CProgram
from ..render.lightsforward import SLamp
from ..sources import ambient2070
from .ownpass import PARTICIPANT_COLOR, COwnPass

DYE_EFFECT = "model_dye.fx"
WATER_EFFECT = "model_water.fx"
CASTER = "Obj2Shadow"
# the blended pass's technique: model.fxh's, else the one of shadow method 0 (foam falls, icons)
BLENDS = ("DefaultBlend", "DefaultBlendStandardShadow")
# the shaders that never go to the G-buffer: foam falls, icons, animated icons (frame-2070.md "Which pass")
BLENDED_EFFECTS = ("model_foamfall.fx", "model_icon.fx", "model_animated_icon.fx")

GAME = "anno2070"
FRAME_DEFINES = ()  # the lighting effect takes none of the material switches
SETTING = ("North", 0)  # the ambient set shown: the day at its best ecology (rendering-2070.md 4.1)
SPOT = 2  # a LIGHT's Type of a spot (0: a point)


def old_compiler(tree, game: str) -> fxc.CFxc:
    """The compiler build an old engine's game used (``nativelook.GAMES``' ``library``: ``d3dcompiler_43``), the
    install's own first."""
    return fxc.CFxc.find(getattr(tree, "m_folder", None), library=nativelook.GAMES[game].library)


def optional_pass(compile_pass):
    """A pass the frame can do without (the shadowed lighting, a shadow caster): None when it does not compile."""
    try:
        return compile_pass()
    except (CNoPass, RuntimeError):
        return None


class _CSource:
    """What the pass needs of the install, read once in the background: the compiler, the sources and the light."""

    def __init__(self, tree) -> None:
        self.m_compiler = old_compiler(tree, GAME)
        self.m_shaders = shader2070.CSource2070(tree, self.m_compiler)
        self.m_sun = ambient2070.parse(tree.read(ambient2070.AMBIENT_MAP).decode("latin-1")).setting(*SETTING)
        self.m_files = tree


def compile_parts(source: _CSource) -> dict:
    """In the background: the frame's parts beyond the lighting (the shadowed lighting, the sky, the lights) and the
    sky's noise volume."""
    shaders = source.m_shaders
    out = {"shadowed": shaders.effect_pass(frame2070.FX, FRAME_DEFINES, frame2070.SHADOWED),
           "sky": shaders.effect_pass(sky2070.FX, (), sky2070.TECHNIQUE), "volume": sky2070.read_volume(source.m_files)}  # fmt: skip
    out["lights"] = {
        technique: shaders.effect_pass(lights2070.FX, (), technique) for technique in lights2070.TECHNIQUES.values()
    }
    return out


def lamps_of(renderer, world) -> list[SLamp]:
    """The scene's lights where they stand this frame (``world``: a place in the pass's axes), as the engine feeds
    them: the colour
    premultiplied by its strength, the range scaled with the object, a spot along its local -y and its ``Theta`` and
    ``Phi`` as the cone's inner and outer half-angles."""
    r, out = renderer, []
    for lamp in r.m_scene.m_lights if r.m_scene is not None else []:
        place = r.place(lamp, r.m_state) if r.m_state.shows(lamp) else None
        if place is None:
            continue
        matrix = world(place)
        scale = float(np.mean(np.linalg.norm(matrix[:3, :3], axis=0)))
        color = r.m_state.light_color(lamp)
        found = SLamp(tuple(matrix[:3, 3]), float(lamp.range) * scale, color, float(lamp.falloff),
                     volume=float(getattr(lamp, "volume", 0.0)))  # fmt: skip
        if int(lamp.kind) == SPOT:
            axis = -matrix[:3, 1] / max(float(np.linalg.norm(matrix[:3, 1])), 1e-9)
            inner, outer = (math.cos(float(cone_angle)) for cone_angle in lamp.cone)
            found = SLamp(found.position, found.range, color, found.falloff, tuple(axis), inner, outer, found.volume)
        out.append(found)
    return out


def object_rgba(values: dict) -> tuple:
    """A material's object colour (rgba) from its model's ``COLOR_TRANSFORM`` (``shader2070.material_values``): the
    owner's colour, our look's player blue, multiplied in where it is."""
    red, green, blue, alpha = values.get("ObjectColor", (1.0, 1.0, 1.0, 1.0))
    if int(values.get("ObjectPlayer", 0)):
        red, green, blue = (
            channel * participant_channel
            for channel, participant_channel in zip((red, green, blue), PARTICIPANT_COLOR, strict=True)
        )
    return (red, green, blue, alpha)


def blend_pass(shaders, values: dict) -> shader2070.SPassStages:
    """In the background: a material's pass of the blended pass, the first of ``BLENDS`` its effect has."""
    for technique in BLENDS[:-1]:
        try:
            return shaders.model_pass(values, technique)
        except CNoPass:
            continue
    return shaders.model_pass(values, BLENDS[-1])


def is_blended(values: dict) -> bool:
    """Whether a material draws in the blended pass only: a blended-only shader, or its object drawn faded (its
    ``COLOR_TRANSFORM`` alpha below 1; model water keeps its own step, its alpha is the water's depth)."""
    effect = shader2070.model_effect(values)
    if effect.endswith(WATER_EFFECT):
        return False
    return effect.endswith(BLENDED_EFFECTS) or object_rgba(values)[3] < 1.0


def _cloth_work(shaders, values: dict, vertices, indices) -> tuple:
    """A cloth group's stages (a double-sided one drawn without culling: the reader compiles a technique's first
    pass, the front), its shadow caster's and its mesh (``render.cloth2070``)."""
    effect = cloth2070.effect(values)
    if effect is None:
        raise CNoPass(f"the cloth shader {values.get('ShaderIndex')} is not one this reader draws")
    draw, cast = cloth2070.techniques(values)
    stages = shaders.effect_pass(effect, cloth2070.defines(values), draw)
    if int(values.get("DoubleSided", 0)):
        stages = replace(stages, state=replace(stages.state, cull="none"))
    caster = optional_pass(lambda: shaders.effect_pass(effect, cloth2070.defines(values), cast))
    return stages, caster, cloth2070.cloth_mesh(vertices, indices)


class CFxcPass(COwnPass):
    """The parts of one scene as the game's shaders draw them (module docstring)."""

    GAME = GAME
    PREFIX = "fxc"
    SOURCE = _CSource
    NOT_OURS = CNoPass
    FINISH = 3  # the post sequence leaves the picture linear: the view's final pass encodes it
    GAME_AXES, MIRROR_CUBES = True, False
    m_look2070 = True  # the view finishes with the game's tone curve (modelrender._light)

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.m_frame: CFrame2070 | None = None
        # the lighting does not compile: our look draws everything (``broken``, the base's, says why a frame failed)
        self.m_shading_broken = False
        self.m_parts: dict | None = None  # the compiled parts, once
        self.m_parts_broken = False
        self.m_ocean_broken = False
        self.m_ground = False  # the sea drew last frame: it stands for the view's ground (modelrender)
        self.m_post: post2070.CPostChain2070 | None = None
        self.m_sun_now: SSun2070 | None = None  # this frame's light, for the post sequence

    def _work(self, source: _CSource, part, item):
        """In the background: the group's two stages, its shadow caster's (None when it has none), its mesh, its
        material's values and whether it is a cloth."""
        values = shaderview.first_values(part.material(item["key"]).values)
        data = item.get("data")
        stored = getattr(data, "stored", None)
        if not values or stored is None:
            raise CNoPass("not a model of the game's shaders")
        if clothforward.is_cloth(part.node):
            return (*_cloth_work(source.m_shaders, values, data.vertices, item["idx"]), values, True)
        mesh = CMesh.of_rdm(stored, item["idx"])
        if is_blended(values):  # the blended pass only, casting no shadow (frame-2070.md "Which pass")
            return blend_pass(source.m_shaders, values), None, mesh, values, False
        stages = source.m_shaders.model_pass(values)
        caster = optional_pass(lambda: source.m_shaders.model_pass(values, CASTER))
        return stages, caster, mesh, values, False

    def _build(self, key: tuple, got) -> CModel2070:
        stages, caster, mesh, values, cloth = got
        ctx, dummies = self.m_renderer.m_ctx, self._dummies()
        state = stages.state
        if stages.technique in BLENDS:  # its layer keeps the coverage (one, 1 - alpha) for the composite over the view
            state = replace(state, source_alpha="one", dest_alpha="inv_src_alpha")
        draw = fxcdraw.CFxcDraw(ctx, CProgram(stages.vertex_stage, stages.pixel_stage), mesh, state, dummies)
        shadow = (
            fxcdraw.CFxcDraw(ctx, CProgram(caster.vertex_stage, caster.pixel_stage), mesh, caster.state, dummies)
            if caster
            else None
        )
        model = self.m_models[key] = (cloth2070.CClothModel2070 if cloth else CModel2070)(draw, shadow)
        model.m_values = values
        if cloth:  # coloured as the engine colours it (1.25 x the object colour unless white)
            model.m_color, model.m_ripple = object_rgba(values), tuple(values.get("ClothRipple", model.m_ripple))
            return model
        model.m_bones = mesh.bone_count(int(values.get("Bones", 0)))
        effect = shader2070.model_effect(values)
        if stages.technique in BLENDS:  # its object colour with the fade in its alpha
            model.m_blended, model.m_color = True, object_rgba(values)
        elif effect.endswith(DYE_EFFECT):  # the dye mask shows the player's colour
            model.m_color = PARTICIPANT_COLOR
        elif effect.endswith(WATER_EFFECT):  # its object colour is the water's, its alpha the depth to it
            model.m_water, model.m_diffuse = True, tuple(values.get("DiffuseColor", model.m_diffuse))
            model.m_color = object_rgba(values)
        return model

    def _maps(self, model: CModel2070) -> None:
        """The model's maps this frame: the viewer's textures (None while one loads)."""
        for slot, name in SLOT_NAMES.items():
            path = model.m_values.get(f"Texture.{slot}", "")
            if slot == "environment":
                model.m_maps[name] = self._cube(path) if path else None
                continue
            model.m_maps[name] = self._map(path, missing=slot == "diffuse")

    def _frame(self):
        """The frame (the G-buffer and the lighting pass) of this device; None while the lighting shaders compile,
        False when they do not."""
        if self.m_frame is None and not self.m_shading_broken:
            effect, technique = frame2070.SHADING
            self.m_frame = self._part(
                "shading",
                lambda source: source.m_shaders.effect_pass(effect, FRAME_DEFINES, technique),
                lambda stages: CFrame2070(self.m_renderer.m_ctx, stages, self._dummies()),
                N_("viewer.games_deferred_shading"),
                N_("viewer.our_look"),
                lambda: setattr(self, "m_shading_broken", True),
            )
        if self.m_frame is not None:
            self._set_parts(self.m_frame)
        return False if self.m_shading_broken else self.m_frame

    def _set_parts(self, frame: CFrame2070) -> None:
        """The frame's shadow, sky and lights as compiled and switched on (each off while it compiles)."""
        if self.m_parts is None and not self.m_parts_broken:
            self.m_parts = self._part("parts2070", compile_parts, lambda got: got, N_("viewer.games_shadow_sky_lights"),
                                    N_("viewer.frame_without_them"), lambda: setattr(self, "m_parts_broken", True))  # fmt: skip
        got, ctx = self.m_parts, self.m_renderer.m_ctx
        if got is None:
            return

        def sky() -> sky2070.CSkyDome:
            dome = sky2070.CSkyDome(ctx, got["sky"], self._dummies())
            dome.set_volume(got["volume"])
            return dome

        made = {"shadowed": lambda: frame2070.shading_pass(ctx, got["shadowed"], self._dummies()), "sky": sky,
                "lights": lambda: lights2070.CLightVolumes(ctx, got["lights"], self._dummies())}  # fmt: skip
        for name, switch in (("shadowed", "shadows"), ("sky", "atmosphere"), ("lights", "lights")):
            fxcdraw.switched(frame.m_parts, name, self._on(switch), made[name])
        make_ocean = None if self.m_ocean_broken else lambda: self._make_ocean(got["volume"])
        fxcdraw.switched(frame.m_parts, "ocean", self._on("waves"), make_ocean)

    def _make_ocean(self, volume):
        """The sea's passes (``ocean2070``, its sky cube's clouds of ``volume``) once they compiled, None while they
        do; a failure is said once."""

        def made(got) -> ocean2070.COcean2070:
            ocean = ocean2070.COcean2070(self.m_renderer.m_ctx, got, self._dummies(), self._ready().m_sun.water)
            ocean.m_cube.set_volume(volume)
            return ocean

        return self._part(
            "ocean2070",
            lambda source: {k: source.m_shaders.effect_pass(*stage) for k, stage in ocean2070.STAGES.items()},
            made,
            N_("viewer.games_ocean"),
            N_("viewer.no_sea"),
            lambda: setattr(self, "m_ocean_broken", True),
        )

    def _sea(self, ground) -> ocean2070.SSea | None:
        """Where the sea goes this frame: round the view's ground (``ground``: its place, None without one) once
        the engine's wave, ripple and caustic maps are in; None with Ocean waves off or while they load."""
        if ground is None or not self._on("waves") or self.m_frame is None or self.m_frame.m_parts.ocean is None:
            return None
        paths = (*ocean2070.WAVES.values(), ocean2070.RIPPLES, ocean2070.CAUSTICS)
        maps = {path: self._map(path) for path in paths}
        if any(map_texture is None for map_texture in maps.values()):
            return None
        place = self._world(ground)
        return ocean2070.SSea((float(place[0, 3]), float(place[2, 3])), float(abs(place[0, 0])), maps)

    def _sun(self, light, source: _CSource) -> SSun2070:
        seconds = float(getattr(self.m_renderer.m_state, "time", 0.0))
        return frame2070.sun_of(source.m_sun, self._direction(light.sun), seconds, self._on("clouds"))

    def draw(self, drawn: list, target, size_px: tuple[int, int], camera, light, sky=None, ground=None) -> list | None:
        """The G-buffer pass of every group the game's shaders draw, lit by its deferred shading, into ``target``
        with its depth; the rest of ``drawn`` is returned for the renderer, or None while a shader compiles."""
        ready, rest, waiting = self._split(drawn)
        self.m_ground = False  # until the sea is drawn: a frame that draws none leaves the ground to the view
        found = []
        for _key, _group, item, place, model in ready:
            model.m_world = self._world(place)
            model.m_palette = item.get("matrices") if item.get("skinned") is not None else None
            self._maps(model)
            water, blended = getattr(model, "m_water", False), getattr(model, "m_blended", False)
            if (self._on("water") or not water) and (self._on("transparent") or not blended):
                found.append(model)
        frame = self._frame() if found else None
        if frame is False:  # the lighting does not compile: all of it is for our look
            return drawn
        if waiting or (found and frame is None):
            return None
        if frame is not None:
            cam = self._camera(camera, size_px)
            self.m_sun_now = self._sun(light, self._ready())
            sea = self._sea(ground)
            self.m_ground = sea is not None
            frame.render(target, size_px, found, cam, self.m_sun_now, lamps_of(self.m_renderer, self._world), sea=sea)
        return rest

    def finish(self, source, size_px: tuple[int, int], lut=None):
        """2070's post sequence over the view's resolved picture: the finished linear texture, or None while it
        compiles, when it is switched off or before a frame was drawn (the view's own finish then)."""
        if not self._on("post") or self.m_frame is None or self.m_sun_now is None:
            return None
        if self.m_post is None:
            self.m_post = self._part(
                "post2070",
                lambda ready: post2070.compile_steps(ready.m_shaders),
                lambda stages: post2070.CPostChain2070(self.m_renderer.m_ctx, stages, self._dummies()),
                N_("viewer.games_post_sequence"),
                N_("viewer.views_finish"),
            )
        if self.m_post is None:
            return None
        sun = self.m_sun_now
        values = {"cPostBlur": [sun.blur, 0.0, 0.0, 0.0], "cMoodColor": list(sun.mood)}
        return self.m_post.render(
            source, self.m_frame.m_targets[2] if self.m_frame.m_targets else None, size_px, values
        )

    def _release_frame(self) -> None:
        if self.m_post is not None:
            self.m_post.release()
            self.m_post = None
        if self.m_frame is not None:
            self.m_frame.release()
            self.m_frame = None
