"""The viewer's look by Anno 2205's and 1800's own shaders: a material's effect compiled from the install by the
compiler the game ships (``models.shaderforward``) and drawn forward-lit with its shadow and its blended pass
(``render.frameforward``), on Direct3D 11; a material whose shader this reader does not draw, or that does not
compile, keeps our look.

``ForwardPass(renderer)`` (2205) and ``ForwardPass1800`` are ``ownpass.OwnPass``es, the ``pass_class`` of their games
in ``nativelook.GAMES``; the light and the sky's cubes are the install's ambient setting (``render.ambientforward``).
docs/rendering/shader-compile.md section 11.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace

import numpy as np

from ..infra import fxc
from ..infra.i18n import N_, tr
from ..models import fileresolve, shaderforward, shaderview
from ..models.shaderforward import NoPass
from ..render import (
    ambientforward,
    clothforward,
    decalforward,
    eyeforward,
    frameforward,
    fxcdraw,
    gameshadow,
    lightclusters,
    ocean1800,
    ocean2070,
    particleforward,
    postforward,
    sea2205,
    skyforward,
    ssaoforward,
    ssrforward,
    terrain1800,
    waves1800,
)
from ..render.camera import MIRROR
from ..render.frameforward import ForwardModel
from ..render.gamedraw import Mesh, Program
from ..render.lightsforward import ClusterTextures, Lamp
from ..render.particleforward import ForwardEffect, Placement
from ..render.shadowforward import CASTER_BIAS, CASTER_SLOPE
from ..sources.assetdb import number
from .modellights import NIGHT as VIEW_NIGHT
from .ownpass import OwnPass

GAME = "anno2205"
# the technique a caster draws the sun's shadow map with, per game
SHADOWS = {GAME: "Obj2Shadow", "anno1800": "ModelObj2Shadow"}
CLOUDS = "data/graphics/effects/sky/clouds.dds"  # 2205's cloud noise (its alpha: the cloud shadows)
WATER_SHADER = 7  # both games' model_water materials (View > Rendering > Water)
# 2205's water info under a model, the open sea [ours]: not shallow, no coast waves, full waves, deep sea
OPEN_SEA = (0, 0, 255, 255)
# 1800's (its WaterInfos: depth, coast waves, beach, static foam): deep, the large waves full, no coast [ours]
OPEN_SEA_1800 = bytes((255, 0, 0, 0))


def frame_values(setting: ambientforward.Light, sun, night: bool, eye_height: float, near: float, far: float,
                 on=lambda _part: True) -> dict:  # fmt: skip
    """The frame's light and haze constants by name for the setting, the sun direction of the view and the camera;
    ``on(part)``: View > Rendering's switches (``atmosphere`` the haze, ``clouds`` the cloud shadows)."""
    values = ambientforward.light_values(setting, sun, night)
    values.update(ambientforward.scattering_values(setting, eye_height, near, far, night))
    if not on("atmosphere"):
        values["cScatteringIntensity"] = 0.0  # the haze's depth is saturate(depth x intensity): none
    if not on("clouds"):
        values["cCloudShadows"] = [*values["cCloudShadows"][:2], 1.0, values["cCloudShadows"][3]]
        values["cInvertCloudShadows"] = 1.0
    return values


def sky_work(source) -> tuple:
    """In the background: the game's sky technique's (vertex, pixel) stages and its states (``skyforward``)."""
    shaders = source.shaders
    return shaders.effect_pass(skyforward.FX, (), skyforward.TECHNIQUE), shaders.technique_state(
        skyforward.FX, (), skyforward.TECHNIQUE
    )


def drawn_size(size: tuple[int, int], q) -> tuple[int, int]:
    """The size the models are drawn at: the view's times the rendering quality's ``render_scale`` (the composite
    stretches them over the view; ``q`` None: the view's)."""
    scale = q.render_scale if q is not None else 1.0
    return (max(1, round(size[0] * scale)), max(1, round(size[1] * scale)))


def shadow_quality(q) -> tuple[int | None, int]:
    """The sun's shadow map side (None: the frame's own) and the cascades read by the rendering quality."""
    return (q.shadow_size, q.shadow_cascades) if q is not None else (None, gameshadow.COUNT)


class _Source:
    """The compiler, the sources and the light of the install, read once in the background."""

    def __init__(self, tree, game: str = GAME) -> None:
        self.compiler = fxc.Fxc.find(getattr(tree, "folder", None))
        self.shaders = shaderforward.ForwardSource(tree, self.compiler, game)
        try:
            self.light = ambientforward.read_light(tree, ambientforward.LOCALS.get(game, ambientforward.DEFAULT_LOCAL))
        except (OSError, KeyError, ValueError, SyntaxError):  # an install without the setting: the record's defaults
            self.light = ambientforward.Light()


class ForwardPass(OwnPass):
    """The parts of one scene as the game's shaders draw them (module docstring)."""

    GAME = GAME
    PREFIX = "fwd"
    CASCADED = False  # the sun's shadow in cascades (1800) or in one map
    EYE = False  # the game's eye adaptation before its post chain (1800's eye_adaptation.fx)
    SSAO = False  # the game's ambient occlusion over a depth pre-pass (1800's ambient_ssao.fx)
    WAVES = False  # the engine's small-wave map its model water reads (1800's water_ocean.fx OceanGradients)
    SSR = False  # its model water's screen-space reflections (1800's posteffect_ssr.fx)
    SEA = True  # 2205's sea under the model in place of the view's ground (render.sea2205), with Ocean waves on
    FINISH = 4  # the view's final pass shows the chain's picture as it is (the game's combine encodes it)
    SOURCE = _Source
    NOT_OURS = NoPass

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.frame = None
        self.clusters: ClusterTextures | None = None
        self.drawn_effects: set[str] = set()  # the effect files the game's particle shader drew this frame
        self.drawn_decals: set[str] = set()  # the decal nodes the game's decal shaders drew this frame
        self.post: postforward.PostChain | None = None  # the post chain, made once it compiled
        self.tables: dict = {}  # 1800's colour grade tables by picture (None: it did not read)
        self.quality = None  # the rendering quality the renderer hands over (render.quality.Quality; None: the best)
        self.sky: skyforward.SkyForward | None = None  # the game's visible sky, once it compiled
        self.sky_broken = False
        self.eye: eyeforward.EyeAdaptation1800 | None = None  # the eye adaptation, made once it compiled
        self.eye_time: float | None = None  # when it last ran (its easing's time step)
        self.again = False  # the exposure read back changed: the view draws once more
        self.ssao: ssaoforward.Ssao1800 | None = None  # the ambient occlusion, made once it compiled
        self.waves: waves1800.SmallWaves1800 | None = None  # 1800's small-wave map, made once compiled and read
        self.ssr: ssrforward.Ssr1800 | None = None  # 1800's water reflections, made once compiled
        self.sea: sea2205.Sea2205 | None = None  # 2205's sea, made once compiled
        self.sea_broken = False
        self.ground = False  # the sea drew last frame: it stands for the view's ground (modelrender)

    def busy(self) -> int:
        reflecting = self.ssr is not None and self.ssr.pending  # its trace reads the last frame: once more
        return super().busy() + int(self.again) + int(reflecting)

    def _make_source(self, tree) -> _Source:
        return _Source(tree, self.GAME)

    def _work(self, source: _Source, part, item):
        """In the background: the group's colour pass, its render states, its shadow pass (a caster), its mesh, its
        technique's further passes and its own passes of the frame (``_frame_passes``)."""
        values = shaderview.first_values(part.material(item["key"]).values)
        stored = getattr(item.get("data"), "stored", None)
        if not values or stored is None:
            raise NoPass("not a model of the game's shaders")
        if self.GAME == GAME and clothforward.is_cloth(part.node):  # cloth.fx, whatever ShaderID the material names
            fx, defines, tech = clothforward.FX, clothforward.defines(values), clothforward.TECHNIQUE
            state = source.shaders.technique_state(fx, defines, tech)
            stages = source.shaders.effect_pass(fx, defines, tech)
            return stages, state, None, Mesh.of_rdm(stored, item["idx"]), values, [], {}
        technique = source.shaders.colour_technique(values)
        stages = source.shaders.model_pass(values, technique)  # vertex, pixel and a geometry shader where it has one
        state = source.shaders.model_state(values, technique)
        shadow = None
        if source.shaders.has_technique(values, SHADOWS[self.GAME]):
            shadow = source.shaders.model_pass(values, SHADOWS[self.GAME])
        count = source.shaders.pass_count(values, technique) if self.GAME == GAME else 1
        passes = [f"{technique}#{k}" for k in range(1, count)]  # the planet: its atmosphere, then its surface
        later = [(source.shaders.model_pass(values, p), source.shaders.model_state(values, p)) for p in passes]
        own = self._frame_passes(source, values, state)
        return stages, state, shadow, Mesh.of_rdm(stored, item["idx"]), values, later, own

    def _frame_passes(self, source: _Source, values: dict, state) -> dict:
        """1800's passes of a material for the frame's parts, by the ``ForwardModel`` member they become: the depth
        pre-pass of an opaque one (``prepass``, the ambient occlusion's) and the model water's reflection trace
        (``ssr``); each (stages, states). Without one the model is drawn all the same, that part left out."""
        out: dict = {}
        water = number(values.get("ShaderID"), -1) == WATER_SHADER
        try:
            found = source.shaders.prepass(values) if self.SSAO and not state.blend else None
            if found is not None:
                out["prepass"] = found
            if self.SSR and water and source.shaders.has_technique(values, ssrforward.TECHNIQUE):
                tech = ssrforward.TECHNIQUE
                out["ssr"] = (source.shaders.model_pass(values, tech), source.shaders.model_state(values, tech))
        except (NoPass, RuntimeError):
            pass
        return out

    def _model_of(self, kind: type, stages: tuple, state, mesh: Mesh, values: dict) -> ForwardModel:
        """One pass of a material as a model of the forward frame: its draw in the states the viewer can keep."""
        if state.blend:  # the blended layers write no depth here: the composite reads it as the opaque coverage
            state = replace(state, depth_write=False)
        if state.depth_func == "equal":  # 1800 tests against its depth pre-pass, which the viewer does not draw
            state = replace(state, depth_func="less_equal", depth_write=True)
        draw = fxcdraw.FxcDraw(self.r.ctx, Program(*stages), mesh, state, self._dummies())
        draw.mirrored = True  # the view's x mirror turns the winding over
        model = kind(self.r.ctx, draw)
        model.fields, model.blended = values, state.blend
        model.bones = number(values.get("NumBonesPerVertex"), 0)
        return model

    def _build(self, key: tuple, got) -> ForwardModel:
        stages, state, shadow, mesh, values, later, own = got
        cloth = self.GAME == GAME and clothforward.is_cloth(self.r.scene.parts[key[0]].node)
        model = self.models[key] = self._model_of(
            clothforward.ClothModel if cloth else ForwardModel, stages, state, mesh, values
        )
        draw = model.draw_call
        model.later = [self._model_of(ForwardModel, s, st, mesh, values) for s, st in later]
        for m in model.later:
            m.maps = model.maps  # one material: the passes read the same maps
        if shadow is not None:
            biased = replace(shaderforward.block_state({}), depth_bias=CASTER_BIAS, slope_bias=CASTER_SLOPE)
            caster = fxcdraw.FxcDraw(self.r.ctx, Program(*shadow), mesh, biased, self._dummies())
            # 1800's cascades look along the light with x right and y up (gameshadow.light_basis): that mirrors the
            # picture once more, so the view's x mirror and theirs cancel
            caster.mirrored = not self.CASCADED
            caster.fallbacks = dict(draw.fallbacks)
            caster.instances = gameshadow.COUNT if self.CASCADED else 1  # 1800: a copy per cascade
            model.shadow = caster
        for role, (passes, st) in own.items():  # the pre-pass, the reflection's trace: the colour pass's winding
            extra = fxcdraw.FxcDraw(self.r.ctx, Program(*passes), mesh, st, self._dummies())
            extra.mirrored, extra.fallbacks = True, dict(draw.fallbacks)
            setattr(model, role, extra)
        return model

    def _field_maps(self, model: ForwardModel, fields, slots) -> None:
        """The maps the material's ``fields`` name (texture references, as the config writes them) bound as ``slots``
        (the shader's names), read as stored; None while one loads."""
        for field, slot in zip(fields, slots, strict=True):
            raw = str(model.fields.get(field, "")).replace(chr(92), "/")
            model.maps[slot] = self._map(fileresolve.texture_name(raw, self.GAME) if raw else "")

    def _maps(self, model: ForwardModel, material) -> None:
        """The model's maps this frame: the viewer's textures (None while one loads), every one read as stored - the
        shaders turn the diffuse colour into linear light themselves (``GammaToLinear``) - and its cube map."""
        if isinstance(model, clothforward.ClothModel):
            self._field_maps(model, clothforward.MAPS, clothforward.MAPS)
            return
        for name, path in (("cModelDiffTex", material.diffuse), ("cModelNormalTex", material.normal),
                           ("cModelMetallicTex", material.metal)):  # fmt: skip
            model.maps[name] = self._map(path, missing=name == "cModelDiffTex")
        env = str(model.fields.get("cModelEnvTex", "")).replace(chr(92), "/")
        model.maps["cModelEnvTex"] = self._cube(env) if env else None
        # the effect's own maps (interior mapping's room atlases, arctic's surface and ice): a 2D map the shaders
        # read under the name of a material field
        names = {n for n, kind in model.draw_call.program.numbers if kind == "texture"}
        own = ("cModelDiffTex", "cModelNormalTex", "cModelMetallicTex", "cModelEnvTex")
        extra = sorted(n for n in names & set(model.fields) if n not in own)
        self._field_maps(model, extra, extra)

    def _frame_maps(self, light: ambientforward.Light, on=lambda _part: True) -> tuple[dict, dict]:
        """The frame's textures from the install by name, and the values that go with them: the sky box as the sky
        the engine renders (2205's sky and dynamic cubes), the cloud noise, 1800's prefiltered sky and its table;
        ``on(part)``: View > Rendering's switches (``sky`` off: the cubes' stand-ins, ``clouds`` off: no noise)."""
        maps: dict = {}
        values: dict = {}
        if self.GAME == GAME and on("clouds"):  # off: the frame's stand-in, no noise (frameforward.FALLBACKS)
            maps["cCloudTex"] = self._map(CLOUDS)
        if light.specular_cubes:  # the BRDF table is the material's, not the sky's: it stays with the sky off
            maps["cPrefilteredBRDF"] = self._map(ambientforward.BRDF_1800)
        if not on("sky"):
            return {k: v for k, v in maps.items() if v is not None}, values
        if light.skybox:
            sky = self._cube(light.skybox)
            maps.update({"cSkyRenderTarget": sky, "cEnvironmentCubeTex": sky})
        if light.specular_cubes:
            cubes = [self._cube(p) for p in light.specular_cubes[:2]]
            maps.update({"cPrefilteredSpecularCube0": cubes[0], "cPrefilteredSpecularCube1": cubes[-1]})
            if cubes[0] is not None:
                values["cPrefilteredSpecCubeSizeLog2"] = math.log2(max(1, cubes[0].pixelSize().width()))  # full chain
            values["cMtxEnvironmentRotation"] = ambientforward.cube_turn(light)  # the view's axes are mirrored
            values["cPrefilteredCubeMapLerpFac"] = 0.0  # noon: the first cube alone
        return {k: v for k, v in maps.items() if v is not None}, values

    def _lamps(self) -> tuple[list, list]:
        """The config's point and spot lights where they stand this frame, in the view's axes (those with
        ``ClusteredShading`` 0 left out, as 117's pass does), by the kinds 117 numbers alike."""
        r, points, spots = self.r, [], []
        for lamp in r.scene.lights if r.scene is not None else []:
            place = r.place(lamp, r.state) if r.state.shows(lamp) else None
            kind = lightclusters.CONFIG_KINDS.get(int(lamp.kind), ("",))[0]
            if place is None or not lamp.clustered or kind not in ("point", "spot"):
                continue
            m = MIRROR @ np.asarray(place, float)
            color = r.state.light_color(lamp)
            if kind == "point":
                points.append(Lamp(tuple(m[:3, 3]), float(lamp.range), color, float(lamp.falloff)))
            else:
                axis = -m[:3, 1] / max(float(np.linalg.norm(m[:3, 1])), 1e-9)
                inner, outer = (math.cos(float(a) / 2) for a in lamp.cone)
                spots.append(
                    Lamp(tuple(m[:3, 3]), float(lamp.range), color, float(lamp.falloff), tuple(axis), inner, outer)
                )
        return points, spots

    def _decal_work(self, source: _Source, values: dict) -> tuple:
        """In the background: the pass and states of a decal material (``decalforward.FILES`` by its ShaderID)."""
        shader = number(values.get("ShaderID"), -1)
        fx = decalforward.FILES.get(shader)
        if fx is None:
            raise NoPass(f"the decal ShaderID {shader} is not a shader this reader draws")
        stem = "model_pbr" if shader == 8 else "decal"
        defines = tuple(shaderforward.material_defines(values, stem, self.GAME, ("NORMAL_ENABLED",)))
        vs, ps = source.shaders.effect_pass(fx, defines, decalforward.TECHNIQUE)
        return vs, ps, source.shaders.technique_state(fx, defines, decalforward.TECHNIQUE)

    def _decals(self) -> list:
        """The scene's decals the game's decal shaders draw this frame, placed and with their maps (their nodes go
        into ``drawn_decals``; the others, and those still compiling, are left to our look)."""
        r, state, source = self.r, self.r.state, self._ready()
        self.drawn_decals = set()
        if self.GAME != GAME or source is None or r.scene is None:
            return []
        out = []
        for decal in r.scene.decals:
            place = r.place(decal, state) if state.shows(decal) else None
            model = self._decal(source, decal) if place is not None else None
            if not isinstance(model, decalforward.DecalModel):
                continue
            model.world = MIRROR @ np.asarray(place, float)
            slots = decalforward.SLOTS[number(model.fields.get("ShaderID"), 0)]
            self._field_maps(model, decalforward.DECAL_MAPS, slots)
            out.append(model)
            self.drawn_decals.add(decal.node)
        return out

    def _decal(self, source: _Source, decal):
        """The game's draw of a decal: a ``DecalModel``, an exception (our look keeps it) or None while it compiles."""
        key = ("decal", decal.node)
        if key in self.models:
            return self.models[key]
        values = shaderview.first_values(decal.material.values)
        job, what = f"{self.PREFIX}:decal:{decal.node}", tr("viewer.node_games_decal_shader", node=decal.node)
        got = self._job(key, job, lambda: self._decal_work(source, values), what)
        if got is None or isinstance(got, Exception):
            return got
        vs, ps, state = got
        draw = fxcdraw.FxcDraw(self.r.ctx, Program(vs, ps), decalforward.quad(decal.extents), state, self._dummies())
        draw.mirrored = True
        model = self.models[key] = decalforward.DecalModel(self.r.ctx, draw)
        model.fields, model.blended = values, True
        return model

    def _effect_work(self, source: _Source, effect, shadows: bool) -> tuple[list, particleforward.Tracks]:
        """In the background: the track textures of an emitter and the pass and states of each of its runs
        (``shadows``: the placing node's ``ReceiveShadows``)."""
        why = particleforward.not_drawn(effect)
        if why:
            raise NoPass(why)
        tracks = particleforward.tracks(effect)
        passes = []
        for additive, _first, _rows in tracks.runs:
            fx, tech = particleforward.FX, particleforward.TECHNIQUE
            defines = particleforward.defines(effect, additive, shadows)
            slot = particleforward.ADDITIVE_SLOT if additive else particleforward.ALPHA_SLOT
            passes.append(
                (
                    *source.shaders.effect_pass(fx, defines, tech),
                    source.shaders.technique_state(fx, defines, tech, slot),
                )
            )
        return passes, tracks

    def _effect(self, source: _Source, placed, k: int, effect):
        """The game's draw of the ``k``-th emitter of the effect file of ``placed`` (``modelscene.PlacedEffect``): a
        ``ForwardEffect`` (one per placement: each draws with constants of its own), an exception (our drawer keeps
        it) or None while it compiles (once per file and shadow variant)."""
        path, shadows = placed.file, placed.shadows
        key = ("fx", placed.node, path, k)
        if key in self.models:
            return self.models[key]
        job, what = f"{self.PREFIX}:fx:{path}:{k}:{int(shadows)}", tr("viewer.path_games_particle_shader", path=path)
        got = self._job(key, job, lambda: self._effect_work(source, effect, shadows), what)
        if got is None or isinstance(got, Exception):
            return got
        passes, tracks = got
        mesh, dummies = particleforward.quad_mesh(), self._dummies()
        draws = [fxcdraw.FxcDraw(self.r.ctx, Program(vs, ps), mesh, state, dummies) for vs, ps, state in passes]
        self.models[key] = ForwardEffect(self.r.ctx, effect, tracks, draws)
        return self.models[key]

    def _effects(self, eye) -> list:
        """The scene's effects the game's particle shader draws this frame, placed (their files go into
        ``drawn_effects``; the others, and those still compiling, are left to our drawer)."""
        r, state, source = self.r, self.r.state, self._ready()
        self.drawn_effects = set()
        if self.GAME != GAME or source is None or r.scene is None or not getattr(state, "particles", False):
            return []
        out = []
        for e in r.scene.effects:
            effects = r._ask("fx:" + e.file, lambda f=e.file: r.files.effect(f)) if state.shows(e) else None
            place = r.place(e, state) if effects else None
            if place is None:
                continue
            models = [self._effect(source, e, k, fx) for k, fx in enumerate(effects)]
            if not all(isinstance(m, ForwardEffect) for m in models):
                continue
            for m in models:
                m.atlas = self._map(m.effect.texture)
                m.place = Placement(MIRROR @ np.asarray(place, float), tuple(e.tint), float(e.alpha) * state.fade(e),
                                    state.time, 1.0, eye, e.soft, e.depth_bias)  # fmt: skip
            out += models
            self.drawn_effects.add(e.file)
        return out

    def draw(self, drawn: list, target, size: tuple[int, int], camera, light, sky=None, ground=None) -> list | None:
        """The forward pass of every group the game's shaders draw into ``target`` with its depth; the rest of
        ``drawn`` is returned for the renderer, or None while a shader compiles."""
        ready, rest, waiting = self._split(drawn)
        self.ground = False  # until the sea is drawn: a frame that draws none leaves the ground to the view
        found = []
        for _key, part, item, place, model in ready:
            model.world = MIRROR @ np.asarray(place, float)
            model.palette = item.get("matrices") if item.get("skinned") is not None else None
            self._maps(model, part.material(item["key"]))
            for m in (model, *model.later):
                m.world, m.palette = model.world, model.palette
                m.update_matrices()
                found.append(m)
        if waiting:
            return None
        on = self._on
        found = [m for m in found if on("transparent") or not m.blended]
        for m in found:  # the model water refracts the opaque scene's copies (frameforward.SceneCopies)
            m.water = number(m.fields.get("ShaderID"), -1) == WATER_SHADER
        found = [m for m in found if on("water") or not m.water]
        found += self._decals()
        effects = self._effects(np.asarray(camera.eye(), float))
        if found or effects:
            near, far = camera.planes()
            frame = self.frame = self.frame or frameforward.ForwardFrame(self.r.ctx, self.CASCADED)
            size = drawn_size(size, self.quality)
            frame.shadow_size, frame.shadow_cascades = shadow_quality(self.quality)
            frame.scale = self._unstored()
            cam = self._camera(camera, size)
            night = bool(getattr(self.r.state, "night", False))
            setting = self._ready().light
            maps, values = self._frame_maps(setting, on)
            values.update(frame_values(setting, light.sun, night, float(cam.eye[1]), near, far, on))
            seconds = float(getattr(self.r.state, "time", 0.0))  # the render clock: the water's flow and foam [I]
            values["cTimer"] = [seconds, 0.0, seconds, 0.0]
            waves = self._small_waves(found, seconds)
            if waves is not None:
                maps[waves1800.MAP] = waves
            if on("lights"):  # both games' cells (1800: a buffer, lightsforward)
                self.clusters = self.clusters or ClusterTextures(self.r.ctx)
                light_maps, light_rows = self.clusters.update(*self._lamps(), self.GAME)
                maps.update(light_maps)
                values.update(light_rows)
            found = found + self._sea(ground, cam, values, seconds)
            self._draw_sky(
                target, size, cam, {**values, **skyforward.sky_values(setting, on("clouds"), self.MIRROR_CUBES)}
            )
            passes = frameforward.Passes(effects, on("shadows"), self._ssao(), self._ssr(found))
            frame.render(target, size, found, cam, values, maps, passes)
        return rest

    def _sea(self, ground, cam, values: dict, seconds: float) -> list:
        """2205's sea by its own shaders (``render.sea2205``) as the frame's blended draws, over a square round the
        model (``ground``: the view's ground place) at least ``ocean2070.MIN_REACH`` wide, while Ocean waves is on;
        none without a ground or while it compiles (shader-compile.md 11 "2205's sea in a model viewer")."""
        on = self.SEA and ground is not None and self._on("waves")
        self.ground = False
        sea = fxcdraw.switched(self, "sea", on, None if self.sea_broken else self._make_sea)
        if sea is None:
            return []
        place = self._world(ground)
        sea.size = 2 * max(abs(float(place[0, 0])), ocean2070.MIN_REACH)
        origin = np.array([place[0, 3] - sea.size / 2, 0.0, place[2, 3] - sea.size / 2])
        sea.run(self.r.ctx, seconds)
        values.update(sea.frame_values())  # the setting's water in the frame's constants (1800's)
        draws = self._sea_draws(sea, np.asarray(cam.eye, float) - origin, values.get("cLgtDirection"), origin)
        self.ground = bool(draws)
        return draws

    def _sea_draws(self, sea, eye, sun, origin: np.ndarray) -> list:
        """The sea's draws round ``eye`` (relative to the square's corner ``origin``), moved under the model."""
        draws = sea.models(eye, sun)
        for d in draws:  # the sea's square starts at 0
            d.rows = d.rows.copy()
            d.rows[:, :2] += origin[[0, 2]]
            d.world[:3, 3] += origin
            d.draw_call.fallbacks["cWaterInfoMap"] = OPEN_SEA
        return draws

    def _make_sea(self):
        """The sea once its shaders compiled and its maps were read in the background, None until then."""

        def parts(ready) -> tuple:
            files = ready.shaders.files
            local = ambientforward.LOCALS.get(self.GAME, ambientforward.DEFAULT_LOCAL)
            try:
                water = sea2205.read_water(files.read(f"{ambientforward.AMBIENT_DIR}local/{local}.xml").decode())
            except (OSError, KeyError, ValueError, SyntaxError):  # the record's values
                water = sea2205.Water()
            return sea2205.compile_stages(ready.shaders), water, sea2205.read_maps(files)

        return self._part("sea2205", parts, lambda got: sea2205.Sea2205(self.r.ctx, *got, self._dummies()),
                          N_("viewer.games_sea"), N_("viewer.views_ground"), lambda: setattr(self, "sea_broken", True))  # fmt: skip

    def _draw_sky(self, target, size: tuple[int, int], cam, values: dict) -> None:
        """The game's visible sky into ``target`` before the models (``skyforward``): made once it compiled, its boxes
        as they load; with The visible sky and haze off, none (the view's own sky)."""

        def make():  # None while it compiles (asked again next frame)
            return self._part("sky", sky_work, lambda got: skyforward.SkyForward(self.r.ctx, *got, self._dummies()),
                              N_("viewer.games_sky"), N_("viewer.views_sky"), lambda: setattr(self, "sky_broken", True))  # fmt: skip

        if fxcdraw.switched(self, "sky", self._on("atmosphere"), None if self.sky_broken else make) is not None:
            boxes = {
                name: self._cube(path) for name, path in skyforward.box_maps(self._ready().light, self.GAME).items()
            }
            self.sky.scale = self._unstored()
            self.sky.render(target, size, cam, values, {k: v for k, v in boxes.items() if v is not None})

    def _unstored(self) -> float:
        """The factor on the game's colour in the view: 1800 stores a quarter of its light for its post chain
        (``DecompressLDR``, ``postforward.STORED``), so without the chain its models and sky are multiplied back
        before the view's finish (shader-compile.md 11 "1800's post chain")."""
        return 1.0 if self._on("post") else 1.0 / postforward.STORED.get(self.GAME, 1.0)

    def view_light(self, lt: dict, night: bool) -> dict:
        """The light the view draws its own ground and sky with while the game's post chain finishes the frame: the
        setting's sun and ambient in the game's units and formula (ambient x albedo + (sun - ambient) x N.L x albedo,
        2205's ``functions_lighting_base.fxh``) and its sky colours, so the chain tones them as it tones the models
        (1800: divided as its models' colour is, ``postforward.STORED``); the view's own finish (the chain switched
        off) shows the linear picture through ACES. ``lt`` unchanged while the install is read.
        docs/rendering/shader-compile.md section 11 "Post chain"."""
        if not self._on("post") or self.why_not():
            return lt
        source = self._ready()  # after why_not, which starts reading the install
        if source is None:
            return lt
        setting, stored = source.light, postforward.STORED.get(self.GAME, 1.0)
        k = (ambientforward.NIGHT / VIEW_NIGHT if night else 1.0) * stored  # the ground at the models' night light
        ambient = tuple(c * k for c in setting.ambient)
        sun = tuple(max(0.0, d - a) * k for d, a in zip(setting.diffuse, setting.ambient, strict=True))
        sky_k = (ambientforward.NIGHT if night else 1.0) * stored
        zenith, horizon = (tuple(c * sky_k for c in col) for col in (setting.zenith, setting.horizon))
        fall = max(1e-3, setting.horizon_falloff)
        return {**lt, "sunColor": sun, "ambientMode": 2, "flatAmbient": ambient, "sky": (2, zenith, horizon, fall),
                "fogColor": horizon, "finish": 0, "exposure": 0.6}  # fmt: skip

    def finish(self, source, size: tuple[int, int], lut=None):
        """The game's post chain over the view's resolved HDR colour (``source``; ``postforward.CHAINS``: 2205's
        bloom, tone map and image adjustments, 1800's bloom, lite tone map and colour grade): the finished texture
        (encoded: the view shows it as it is, ``FINISH``), or None while the chain compiles or when it is switched
        off (the view's finish then)."""
        if not self._on("post"):
            return None
        chain = postforward.CHAINS[self.GAME]
        if self.post is None:
            self.post = self._part(
                "post",
                lambda ready: postforward.compile_chain(ready.shaders, chain),
                lambda stages: postforward.PostChain(self.r.ctx, stages, self._dummies(), chain),
                N_("viewer.games_post_chain"),
                N_("viewer.views_finish"),
            )
        if self.post is None:
            return None
        light = self._ready().light
        values, extra = {**light.post, **light.lite, **chain.engine, **self._exposure(source, size)}, {}
        table = self._lut(light.lut) if light.lut else None
        if table is not None:
            values.update({"cUsedLUTTextures": 1, "cLUTWeights": [1.0, 0.0, 0.0]})
            extra["cLUTTexture0"] = table
        return self.post.render(source, size, values, extra)

    def _exposure(self, source, size: tuple[int, int]) -> dict:
        """The game's eye adaptation over ``source`` (``eyeforward``; shader-compile.md 11 "1800's eye adaptation"):
        the post chain's values for the exposure read back last frame, none while it is off or compiles."""
        if not self.EYE or not self._on("eye"):
            self.eye_time, self.again = None, False
            return {}
        if self.eye is None:
            self.eye = self._part("eye", lambda ready: eyeforward.compile_stages(ready.shaders),
                                  lambda got: eyeforward.EyeAdaptation1800(self.r.ctx.rhi, got),
                                  N_("viewer.games_eye_adaptation"), N_("viewer.post_chain_without_it"))  # fmt: skip
        if self.eye is None or self.eye.error:
            return {}
        self.again = self.eye.take()
        now = time.monotonic()
        step = None if self.eye_time is None else now - self.eye_time
        if step is None or step > eyeforward.eyeadapt.SETTLE:
            self.eye.restart()
        self.eye_time = now
        self.eye.run(self.r.ctx, source, size, step)
        return {"cUseEyeAdaptation": 1, "cExposureBuffer[0]": self.eye.value}

    def _ssao(self) -> ssaoforward.Ssao1800 | None:
        """The game's ambient occlusion (``ssaoforward``; shader-compile.md 11 "1800's ambient occlusion") while it is
        on and compiled, else None (the colour pass then reads none). Switched off, its targets and the pre-pass's
        are released."""
        on = self.SSAO and self._on("ssao")
        if not on and self.frame is not None:
            self.frame.drop_prepass()
        part = fxcdraw.switched(self, "ssao", on, lambda: self._part(
            "ssao", lambda ready: ssaoforward.compile_stages(ready.shaders),
            lambda got: ssaoforward.Ssao1800(self.r.ctx.rhi, got), N_("viewer.games_ambient_occlusion"),
            N_("viewer.models_without_it")))  # fmt: skip
        return part if part is not None and not part.error else None

    def _ssr(self, found: list) -> ssrforward.Ssr1800 | None:
        """The model water's screen-space reflections (``ssrforward``; shader-compile.md 11 "1800's screen-space
        reflections") while they are on, compiled and a drawn sheet traces; else None, and its targets are released."""
        on = self.SSR and self._on("ssr") and any(m.ssr is not None for m in found)
        part = fxcdraw.switched(self, "ssr", on, lambda: self._part(
            "ssr", lambda ready: ssrforward.compile_stages(ready.shaders),
            lambda got: ssrforward.Ssr1800(self.r.ctx, got, self._dummies()), N_("viewer.games_screen_space_reflections"),
            N_("viewer.water_without_them")))  # fmt: skip
        return part if part is not None and not part.error else None

    def _small_waves(self, found: list, seconds: float):
        """1800's small-wave gradient map at ``seconds`` (``waves1800``; shader-compile.md 11 "1800's small ocean
        waves") while Ocean waves is on and a drawn water material asks for it; else None (its flat stand-in), and the
        volumes are released."""
        wanted = any(m.water and shaderforward.switched_on(m.fields.get(waves1800.SWITCH)) for m in found)
        on = self.WAVES and wanted and self._on("waves")
        files = self.r.files.files
        part = fxcdraw.switched(self, "waves", on, lambda: self._part(
            "waves", lambda ready: (waves1800.compile_stage(ready.shaders), waves1800.read_volumes(files)),
            lambda got: waves1800.SmallWaves1800(self.r.ctx, *got), N_("viewer.games_small_ocean_waves"),
            N_("viewer.still_water")))  # fmt: skip
        return part.run(self.r.ctx, seconds) if part is not None and not part.error else None

    def _lut(self, path: str):
        """1800's colour grade table of the setting's picture as a 3D texture (``postforward.lut_volume``), read
        once; None while it loads or when it does not read (the grade then leaves the picture as it is)."""
        if path not in self.tables:
            files, n = self.r.files.files, postforward.LUT_SIZE
            got = self.r.loader.get(f"lut:{path}", lambda: postforward.lut_volume(files.read(path)))
            if got is None:
                return None
            self.tables[path] = None if isinstance(got, Exception) else self.r.ctx.texture3d((n, n, n), 4, got)
        found = self.tables[path]
        return found.native if found is not None else None

    def _release_frame(self) -> None:
        for tex in self.tables.values():
            if tex is not None:
                tex.release()
        self.tables = {}
        if self.sky is not None:
            self.sky.release()
            self.sky = None
        if self.post is not None:
            self.post.release()
        self.post = None
        if self.eye is not None:
            self.eye.release()
        self.eye, self.eye_time, self.again = None, None, False
        for name in ("ssao", "waves", "ssr", "sea"):
            fxcdraw.switched(self, name, False)
        if self.clusters is not None:
            self.clusters.release()
            self.clusters = None
        if self.frame is not None:
            self.frame.release()
            self.frame = None


class ForwardPass1800(ForwardPass):
    """Anno 1800's models by its own shaders: the same forward route, its technique and its matrix buffer."""

    GAME = "anno1800"
    PREFIX = "fwd1800"
    CASCADED = True
    EYE = True
    SSAO = True
    WAVES = True
    SSR = True
    MIRROR_CUBES = False  # its turn (cMtxEnvironmentRotation, the sky's) carries the mirror

    def _sea_draws(self, sea, eye, sun, origin: np.ndarray) -> list:
        """1800's ocean places its patches itself (``ocean1800.Ocean1800.models``'s origin)."""
        return sea.models(eye, sun, origin[[0, 2]])

    def _make_sea(self):
        """1800's ocean (``render.ocean1800``: its small and large waves) once compiled and read in the background,
        over an open sea: deep everywhere, no coast or beach, no foam [ours]; None until then."""

        def parts(ready) -> tuple:
            files, local = ready.shaders.files, ambientforward.LOCALS[self.GAME]
            source = terrain1800.TerrainSource(files, ready.shaders.compiler, self.GAME)
            try:
                water = ocean1800.read_water(files.read(f"{ambientforward.AMBIENT_DIR}local/{local}.xml").decode())
            except (OSError, KeyError, ValueError, SyntaxError):  # the record's values
                water = ocean1800.Water()
            maps, volumes = ocean1800.read_maps(files), waves1800.read_volumes(files)
            return ocean1800.compile_stages(source), water, maps, volumes

        def make(got: tuple):
            sea = ocean1800.Ocean1800(self.r.ctx, *got, self._dummies())
            sea.set_session(OPEN_SEA_1800 * 16, b"", 1.0)  # a 4 x 4 info map; the flow map's stand-in
            return sea

        return self._part("ocean1800", parts, make, N_("viewer.games_sea"), N_("viewer.views_ground"),
                          lambda: setattr(self, "sea_broken", True))  # fmt: skip
