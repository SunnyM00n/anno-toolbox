"""The viewer's look by Anno 2070's own shaders (``models.shader2070``, the game's compiler) on Direct3D 11: the
G-buffer, the shadow, the deferred shading, the sky, the point and spot lights and the post sequence
(``render.frame2070``, ``lights2070``, ``sky2070``, ``post2070``), model water, cloth and the blended pass (foam,
icons, faded objects); a material without such a pass (cut-outs, waterfalls) or that does not compile keeps our look.

``FxcPass(renderer)`` is an ``ownpass.OwnPass`` with the interface of ``gamepass.GamePass`` (``why_not``, ``busy``,
``draw``, ``finish``, ``release_scene``, ``release``): ``nativelook.GAMES`` names it. docs/rendering/shader-compile.md
section 10; docs/tool-suite.md section 21.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from ..infra import fxc
from ..models import shader2070, shaderview
from ..models.shader2070 import NoPass
from ..render import cloth2070, clothforward, frame2070, fxcdraw, lights2070, nativelook, ocean2070, post2070, sky2070
from ..render.frame2070 import SLOT_NAMES, Frame2070, Model2070, Sun2070
from ..render.gamedraw import Mesh, Program
from ..render.lightsforward import Lamp
from ..sources import ambient2070
from .ownpass import PLAYER_COLOR, OwnPass

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


def old_compiler(tree, game: str) -> fxc.Fxc:
    """The compiler build an old engine's game used (``nativelook.GAMES``' ``library``: ``d3dcompiler_43``), the
    install's own first."""
    return fxc.Fxc.find(getattr(tree, "folder", None), library=nativelook.GAMES[game].library)


def optional_pass(compile_pass):
    """A pass the frame can do without (the shadowed lighting, a shadow caster): None when it does not compile."""
    try:
        return compile_pass()
    except (NoPass, RuntimeError):
        return None


class _Source:
    """What the pass needs of the install, read once in the background: the compiler, the sources and the light."""

    def __init__(self, tree) -> None:
        self.compiler = old_compiler(tree, GAME)
        self.shaders = shader2070.Source2070(tree, self.compiler)
        self.sun = ambient2070.parse(tree.read(ambient2070.AMBIENT_MAP).decode("latin-1")).setting(*SETTING)
        self.files = tree


def compile_parts(source: _Source) -> dict:
    """In the background: the frame's parts beyond the lighting (the shadowed lighting, the sky, the lights) and the
    sky's noise volume."""
    shaders = source.shaders
    out = {"shadowed": shaders.effect_pass(frame2070.FX, FRAME_DEFINES, frame2070.SHADOWED),
           "sky": shaders.effect_pass(sky2070.FX, (), sky2070.TECHNIQUE), "volume": sky2070.read_volume(source.files)}  # fmt: skip
    out["lights"] = {t: shaders.effect_pass(lights2070.FX, (), t) for t in lights2070.TECHNIQUES.values()}
    return out


def lamps_of(renderer, world) -> list[Lamp]:
    """The scene's lights where they stand this frame (``world``: a place in the pass's axes), as the engine feeds
    them: the colour
    premultiplied by its strength, the range scaled with the object, a spot along its local -y and its ``Theta`` and
    ``Phi`` as the cone's inner and outer half-angles."""
    r, out = renderer, []
    for lamp in r.scene.lights if r.scene is not None else []:
        place = r.place(lamp, r.state) if r.state.shows(lamp) else None
        if place is None:
            continue
        m = world(place)
        scale = float(np.mean(np.linalg.norm(m[:3, :3], axis=0)))
        color = r.state.light_color(lamp)
        found = Lamp(tuple(m[:3, 3]), float(lamp.range) * scale, color, float(lamp.falloff),
                     volume=float(getattr(lamp, "volume", 0.0)))  # fmt: skip
        if int(lamp.kind) == SPOT:
            axis = -m[:3, 1] / max(float(np.linalg.norm(m[:3, 1])), 1e-9)
            inner, outer = (math.cos(float(a)) for a in lamp.cone)
            found = Lamp(found.position, found.range, color, found.falloff, tuple(axis), inner, outer, found.volume)
        out.append(found)
    return out


def object_rgba(values: dict) -> tuple:
    """A material's object colour (rgba) from its model's ``COLOR_TRANSFORM`` (``shader2070.material_values``): the
    owner's colour, our look's player blue, multiplied in where it is."""
    r, g, b, a = values.get("ObjectColor", (1.0, 1.0, 1.0, 1.0))
    if int(values.get("ObjectPlayer", 0)):
        r, g, b = (c * p for c, p in zip((r, g, b), PLAYER_COLOR, strict=True))
    return (r, g, b, a)


def blend_pass(shaders, values: dict) -> shader2070.PassStages:
    """In the background: a material's pass of the blended pass, the first of ``BLENDS`` its effect has."""
    for technique in BLENDS[:-1]:
        try:
            return shaders.model_pass(values, technique)
        except NoPass:
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
    fx = cloth2070.effect(values)
    if fx is None:
        raise NoPass(f"the cloth shader {values.get('ShaderIndex')} is not one this reader draws")
    draw, cast = cloth2070.techniques(values)
    stages = shaders.effect_pass(fx, cloth2070.defines(values), draw)
    if int(values.get("DoubleSided", 0)):
        stages = replace(stages, state=replace(stages.state, cull="none"))
    caster = optional_pass(lambda: shaders.effect_pass(fx, cloth2070.defines(values), cast))
    return stages, caster, cloth2070.cloth_mesh(vertices, indices)


class FxcPass(OwnPass):
    """The parts of one scene as the game's shaders draw them (module docstring)."""

    GAME = GAME
    PREFIX = "fxc"
    SOURCE = _Source
    NOT_OURS = NoPass
    FINISH = 3  # the post sequence leaves the picture linear: the view's final pass encodes it
    GAME_AXES, MIRROR_CUBES = True, False
    look2070 = True  # the view finishes with the game's tone curve (modelrender._light)

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.frame: Frame2070 | None = None
        # the lighting does not compile: our look draws everything (``broken``, the base's, says why a frame failed)
        self.shading_broken = False
        self.parts: dict | None = None  # the compiled parts, once
        self.parts_broken = False
        self.ocean_broken = False
        self.ground = False  # the sea drew last frame: it stands for the view's ground (modelrender)
        self.post: post2070.PostChain2070 | None = None
        self.sun_now: Sun2070 | None = None  # this frame's light, for the post sequence

    def _work(self, source: _Source, part, item):
        """In the background: the group's two stages, its shadow caster's (None when it has none), its mesh, its
        material's values and whether it is a cloth."""
        values = shaderview.first_values(part.material(item["key"]).values)
        data = item.get("data")
        stored = getattr(data, "stored", None)
        if not values or stored is None:
            raise NoPass("not a model of the game's shaders")
        if clothforward.is_cloth(part.node):
            return (*_cloth_work(source.shaders, values, data.vertices, item["idx"]), values, True)
        mesh = Mesh.of_rdm(stored, item["idx"])
        if is_blended(values):  # the blended pass only, casting no shadow (frame-2070.md "Which pass")
            return blend_pass(source.shaders, values), None, mesh, values, False
        stages = source.shaders.model_pass(values)
        caster = optional_pass(lambda: source.shaders.model_pass(values, CASTER))
        return stages, caster, mesh, values, False

    def _build(self, key: tuple, got) -> Model2070:
        stages, caster, mesh, values, cloth = got
        ctx, dummies = self.r.ctx, self._dummies()
        state = stages.state
        if stages.technique in BLENDS:  # its layer keeps the coverage (one, 1 - alpha) for the composite over the view
            state = replace(state, source_alpha="one", dest_alpha="inv_src_alpha")
        draw = fxcdraw.FxcDraw(ctx, Program(stages.vs, stages.ps), mesh, state, dummies)
        shadow = fxcdraw.FxcDraw(ctx, Program(caster.vs, caster.ps), mesh, caster.state, dummies) if caster else None
        model = self.models[key] = (cloth2070.ClothModel2070 if cloth else Model2070)(draw, shadow)
        model.values = values
        if cloth:  # coloured as the engine colours it (1.25 x the object colour unless white)
            model.color, model.ripple = object_rgba(values), tuple(values.get("ClothRipple", model.ripple))
            return model
        model.bones = mesh.bone_count(int(values.get("Bones", 0)))
        effect = shader2070.model_effect(values)
        if stages.technique in BLENDS:  # its object colour with the fade in its alpha
            model.blended, model.color = True, object_rgba(values)
        elif effect.endswith(DYE_EFFECT):  # the dye mask shows the player's colour
            model.color = PLAYER_COLOR
        elif effect.endswith(WATER_EFFECT):  # its object colour is the water's, its alpha the depth to it
            model.water, model.diffuse = True, tuple(values.get("DiffuseColor", model.diffuse))
            model.color = object_rgba(values)
        return model

    def _maps(self, model: Model2070) -> None:
        """The model's maps this frame: the viewer's textures (None while one loads)."""
        for slot, name in SLOT_NAMES.items():
            path = model.values.get(f"Texture.{slot}", "")
            if slot == "environment":
                model.maps[name] = self._cube(path) if path else None
                continue
            model.maps[name] = self._map(path, missing=slot == "diffuse")

    def _frame(self):
        """The frame (the G-buffer and the lighting pass) of this device; None while the lighting shaders compile,
        False when they do not."""
        if self.frame is None and not self.shading_broken:
            fx, technique = frame2070.SHADING
            self.frame = self._part(
                "shading",
                lambda source: source.shaders.effect_pass(fx, FRAME_DEFINES, technique),
                lambda stages: Frame2070(self.r.ctx, stages, self._dummies()),
                "the game's deferred shading",
                "our look",
                lambda: setattr(self, "shading_broken", True),
            )
        if self.frame is not None:
            self._set_parts(self.frame)
        return False if self.shading_broken else self.frame

    def _set_parts(self, frame: Frame2070) -> None:
        """The frame's shadow, sky and lights as compiled and switched on (each off while it compiles)."""
        if self.parts is None and not self.parts_broken:
            self.parts = self._part("parts2070", compile_parts, lambda got: got, "the game's shadow, sky and lights",
                                    "the frame without them", lambda: setattr(self, "parts_broken", True))  # fmt: skip
        got, ctx = self.parts, self.r.ctx
        if got is None:
            return

        def sky() -> sky2070.SkyDome:
            dome = sky2070.SkyDome(ctx, got["sky"], self._dummies())
            dome.set_volume(got["volume"])
            return dome

        made = {"shadowed": lambda: frame2070.shading_pass(ctx, got["shadowed"], self._dummies()), "sky": sky,
                "lights": lambda: lights2070.LightVolumes(ctx, got["lights"], self._dummies())}  # fmt: skip
        for name, switch in (("shadowed", "shadows"), ("sky", "atmosphere"), ("lights", "lights")):
            fxcdraw.switched(frame.parts, name, self._on(switch), made[name])
        make_ocean = None if self.ocean_broken else lambda: self._make_ocean(got["volume"])
        fxcdraw.switched(frame.parts, "ocean", self._on("waves"), make_ocean)

    def _make_ocean(self, volume):
        """The sea's passes (``ocean2070``, its sky cube's clouds of ``volume``) once they compiled, None while they
        do; a failure is said once."""

        def made(got) -> ocean2070.Ocean2070:
            ocean = ocean2070.Ocean2070(self.r.ctx, got, self._dummies(), self._ready().sun.water)
            ocean.cube.set_volume(volume)
            return ocean

        return self._part(
            "ocean2070",
            lambda source: {k: source.shaders.effect_pass(*v) for k, v in ocean2070.STAGES.items()},
            made,
            "the game's ocean",
            "no sea",
            lambda: setattr(self, "ocean_broken", True),
        )

    def _sea(self, ground) -> ocean2070.Sea | None:
        """Where the sea goes this frame: round the view's ground (``ground``: its place, None without one) once
        the engine's wave, ripple and caustic maps are in; None with Ocean waves off or while they load."""
        if ground is None or not self._on("waves") or self.frame is None or self.frame.parts.ocean is None:
            return None
        paths = (*ocean2070.WAVES.values(), ocean2070.RIPPLES, ocean2070.CAUSTICS)
        maps = {p: self._map(p) for p in paths}
        if any(m is None for m in maps.values()):
            return None
        place = self._world(ground)
        return ocean2070.Sea((float(place[0, 3]), float(place[2, 3])), float(abs(place[0, 0])), maps)

    def _sun(self, light, source: _Source) -> Sun2070:
        seconds = float(getattr(self.r.state, "time", 0.0))
        return frame2070.sun_of(source.sun, self._direction(light.sun), seconds, self._on("clouds"))

    def draw(self, drawn: list, target, size: tuple[int, int], camera, light, sky=None, ground=None) -> list | None:
        """The G-buffer pass of every group the game's shaders draw, lit by its deferred shading, into ``target``
        with its depth; the rest of ``drawn`` is returned for the renderer, or None while a shader compiles."""
        ready, rest, waiting = self._split(drawn)
        found = []
        for _key, _group, item, place, model in ready:
            model.world = self._world(place)
            model.palette = item.get("matrices") if item.get("skinned") is not None else None
            self._maps(model)
            water, blended = getattr(model, "water", False), getattr(model, "blended", False)
            if (self._on("water") or not water) and (self._on("transparent") or not blended):
                found.append(model)
        frame = self._frame() if found else None
        if frame is False:  # the lighting does not compile: all of it is for our look
            return drawn
        if waiting or (found and frame is None):
            return None
        if frame is not None:
            cam = self._camera(camera, size)
            self.sun_now = self._sun(light, self._ready())
            sea = self._sea(ground)
            self.ground = sea is not None
            frame.render(target, size, found, cam, self.sun_now, lamps_of(self.r, self._world), sea=sea)
        return rest

    def finish(self, source, size: tuple[int, int], lut=None):
        """2070's post sequence over the view's resolved picture: the finished linear texture, or None while it
        compiles, when it is switched off or before a frame was drawn (the view's own finish then)."""
        if not self._on("post") or self.frame is None or self.sun_now is None:
            return None
        if self.post is None:
            self.post = self._part(
                "post2070",
                lambda ready: post2070.compile_steps(ready.shaders),
                lambda stages: post2070.PostChain2070(self.r.ctx, stages, self._dummies()),
                "the game's post sequence",
                "the view's finish",
            )
        if self.post is None:
            return None
        sun = self.sun_now
        values = {"cPostBlur": [sun.blur, 0.0, 0.0, 0.0], "cMoodColor": list(sun.mood)}
        return self.post.render(source, self.frame.targets[2] if self.frame.targets else None, size, values)

    def _release_frame(self) -> None:
        if self.post is not None:
            self.post.release()
            self.post = None
        if self.frame is not None:
            self.frame.release()
            self.frame = None
