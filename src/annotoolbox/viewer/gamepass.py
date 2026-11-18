"""The viewer's look by Anno 117's own shaders: every part's materials compiled from the install by the game's
compiler (``models.shaderview``), drawn into the engine's G-buffer and lit by the game's deferred shading
(``render.gamedraw``, ``gameframe``, ``gamelight``), on Direct3D 12; a material whose effect has no G-buffer pass, or
that does not compile, is drawn by our plain look (viewer.md "Looks").

``GamePass(renderer)`` (an ``ownpass.OwnPass``) is the entry, one per ``modelrender.ModelRenderer``: ``draw(drawn, target, size, camera,
light)`` per frame returns what it left to the renderer (None while the shaders compile: the renderer draws its game
look). ``why_not`` says when it cannot draw at all. docs/viewer.md "The renderer"; docs/tool-suite.md section 21.
"""

from __future__ import annotations

import time

import numpy as np

from ..infra import dxc
from ..infra.i18n import N_
from ..models import shaderview
from ..render import (
    eyeadapt,
    gameatmosphere,
    gameclouds,
    gamedraw,
    gameground,
    gamelight,
    gameocean,
    gamepost,
    gameshader,
    gamesky,
    gamewater,
    lightclusters,
    nativelook,
    renderstates,
    shadowfit,
)
from ..render.camera import MIRROR
from ..render.gameframe import GameFrame, Light, Passes
from ..sources.assetdb import number
from .forwardpass import drawn_size
from .ownpass import OwnPass

GAME = "anno117"


def make_pass(renderer):
    """The own-shader pass of the renderer's game: ``pass_class`` of its ``nativelook.GAMES`` record ("module:Class",
    each with GamePass's interface); 117's ``GamePass`` for a game without one."""
    native = nativelook.GAMES.get(getattr(renderer.files.files, "game", ""))
    module, _, name = (native.pass_class if native else "").partition(":")
    if not name:
        return GamePass(renderer)
    from importlib import import_module

    return getattr(import_module(f"{__package__.partition('.')[0]}.{module}"), name)(renderer)


class GamePass(OwnPass):
    """The parts of one scene as the game's shaders draw them (module docstring)."""

    GAME = GAME
    PREFIX = "gs"
    SOURCE = shaderview.ShaderSource
    NOT_OURS = shaderview.NoGBufferPass

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.frame: GameFrame | None = None
        self.casters: dict[tuple, gamedraw.GameModel] = {}  # the same keys: the group's shadow technique
        self.programs: dict[tuple, gamedraw.Program] = {}
        self.shading: gamelight.DeferredShading | None = None
        self.fit: shadowfit.ShadowFit | None = None
        self.sky: gamesky.SkyPasses | None = None
        self.post: gamepost.GamePost | None = None
        self.eye: eyeadapt.EyeAdaptation | None = None
        self.water: gamewater.SceneCopies | None = None
        self.atmosphere: gameatmosphere.Atmosphere | None = None
        self.clouds: gameclouds.Clouds | None = None
        self.ocean: gameocean.OceanSimulation | None = None
        self.ground: gamedraw.GameModel | None | bool = None  # False: it does not compile
        self._last_frame: float | None = None  # when the eye adaptation last ran (its easing's time step)
        self.transparent: set[tuple] = set()  # the keys of groups drawn by their effect's Transparent technique
        self._bound: tuple = ()  # the view size and copies the transparent draws were bound with

    def _make_source(self, tree) -> shaderview.ShaderSource:
        return shaderview.ShaderSource(tree, dxc.Dxc.find(anno117=getattr(tree, "folder", None)))

    def _work(self, source, part, item):
        """In the background: the group's two stages, material and mesh as stored."""
        values = shaderview.first_values(part.material(item["key"]).values)
        stem = shaderview.effect_of(number(values.get("ShaderID"), -1))
        stored = getattr(item.get("data"), "stored", None)
        if stem is None or stored is None or not values:
            raise shaderview.NoGBufferPass("not a model of the game's shaders")
        got = source.draw_stages(stem, values)
        mesh = gamedraw.Mesh.of_rdm(stored, item["idx"])
        return got.vs, got.ps, source.material(got.refs, values), mesh, got.shadow, got.transparent

    def _build(self, key: tuple, got) -> gamedraw.GameModel:
        vs, ps, material, mesh, shadow, transparent = got
        state = renderstates.pass_state(ps.state) if transparent else renderstates.GBUFFER_STATE
        model = self.models[key] = gamedraw.GameModel(self.r.ctx.rhi, self._program(vs, ps), mesh, material,
                                                      state=state)  # fmt: skip
        if transparent:
            self.transparent.add(key)
        if shadow is not None:
            self.casters[key] = gamedraw.GameModel(self.r.ctx.rhi, self._program(*shadow), mesh, material, model)
        return model

    def _pieces(self, key: tuple, model) -> tuple:
        return (*super()._pieces(key, model), self.casters.get(key))

    def _fall_back(self, key: tuple, error: str) -> None:
        caster = self.casters.pop(key, None)
        if caster is not None:
            caster.release()
        self.transparent.discard(key)
        super()._fall_back(key, error)

    def _unowned(self, model, error: str) -> None:
        if model is self.ground:  # the view draws its own ground instead
            model.release()
            self.ground = False
        super()._unowned(model, error)

    def _program(self, vs, ps) -> gamedraw.Program:
        program = self.programs.get((id(vs), id(ps)))
        if program is None:
            program = self.programs[(id(vs), id(ps))] = gamedraw.Program(vs, ps)
        return program

    def _shading(self):
        """The deferred shading of this device (None while it compiles, or when it does not: our light then)."""
        if self.shading is None:
            self.shading = self._part(
                "shading",
                self.SOURCE.shading,
                lambda got: gamelight.DeferredShading(self.r.ctx.rhi, *got),
                N_("viewer.games_deferred_shading"),
                N_("viewer.our_light_instead"),
            )
        return self.shading

    def _fit(self):
        """The game's cascade analysis of this device (None while it compiles, or when it does not: the CPU's fit)."""
        if self.fit is None:
            self.fit = self._part(
                "shadowfit",
                self.SOURCE.shadow_fit,
                lambda got: shadowfit.ShadowFit(self.r.ctx.rhi, got),
                N_("viewer.games_shadow_analysis"),
                N_("viewer.fitted_on_cpu"),
            )
        return self.fit

    def finish(self, source, size: tuple[int, int], lut):
        """The game's grade and tone map over the view's resolved HDR colour (``source``, a device texture; ``lut``
        the ambient's grade cube or None): the tone-mapped texture, or None when the post chain is switched off or
        not ready (the view's own finish then)."""
        if not self.switches.get("post", True) or self.shading is None:
            return None
        if self.post is None:
            self.post = self._part(
                "post",
                self.SOURCE.post_stages,
                lambda got: gamepost.GamePost(self.r.ctx.rhi, got),
                N_("viewer.games_post_chain"),
                N_("viewer.our_finish"),
            )
        if self.post is None or self.post.error:
            return None
        inputs = {
            k: v
            for k, v in (
                ("cBlueNoiseTex", self.shading.fixed.get("cBlueNoiseTex")),
                ("cLUTTexture0", lut.native if lut is not None else None),
            )
            if v is not None
        }
        return self.post.render(self.r.ctx, source, size, inputs, self._exposure(source, size))

    def _exposure(self, source, size: tuple[int, int]):
        """The game's eye adaptation over ``source`` (frame-117-viewer.md 6): its exposure buffer, or None when it is
        switched off or not ready (the tone map then without it)."""
        if not self.switches.get("eye", True):
            self._last_frame = None
            return None
        if self.eye is None:
            self.eye = self._part(
                "eye",
                lambda source: source.stage_set(eyeadapt.STAGES),
                lambda got: eyeadapt.EyeAdaptation(self.r.ctx.rhi, got),
                N_("viewer.games_eye_adaptation"),
                N_("viewer.tone_map_without_it"),
            )
        if self.eye is None or self.eye.error:
            return None
        now = time.monotonic()
        step = None if self._last_frame is None else now - self._last_frame
        if step is None or step > eyeadapt.SETTLE:
            self.eye.restart()
        self._last_frame = now
        self.eye.run(self.r.ctx, source, size, step)
        return self.eye.exposure

    def _sky(self):
        """The game's sky passes of this device (None while they compile, or when they do not: the look's light)."""
        if self.sky is None:
            self.sky = self._part(
                "sky",
                self.SOURCE.sky_passes,
                lambda got: gamesky.SkyPasses(self.r.ctx.rhi, *got),
                N_("viewer.games_sky_passes"),
                N_("viewer.looks_sky_light"),
            )
        return self.sky

    def draw(self, drawn: list, target, size: tuple[int, int], camera, light: Light, sky=None,
             ground=None) -> list | None:  # fmt: skip
        """The G-buffer pass of every group the game's shaders draw, lit (``sky``: a ``gamelight.Sky`` for the
        game's deferred shading, else ``light``, ours) into ``target`` with its depth, the groups' shadow techniques
        into the sun's cascades, the groups without a G-buffer pass by their transparent technique over the lit
        colour (``switches``); the rest of ``drawn`` is returned for the renderer, or None while a shader still
        compiles. ``ground``: the view's ground plane (a part matrix), drawn into the G-buffer by the game's model
        shader (``render.gameground``)."""
        ready, rest, waiting = self._split(drawn)
        found, casters, see_through = [], [], []
        transparent_on = self.switches.get("transparent", True)
        for key, part, item, place, model in ready:
            if key in self.transparent and not transparent_on:
                rest.append((part, item, place))
                continue
            caster = self.casters.get(key)
            palette = _palette(item, model)
            for m in (model, caster) if caster is not None else (model,):
                m.world = MIRROR @ np.asarray(place, float)
                m.palette = palette
            (see_through if key in self.transparent else found).append(model)
            if caster is not None and self.switches.get("shadows", True):
                casters.append(caster)
        plane = self._ground(ground)
        if plane is not None:
            found.append(plane)
        shading = self._shading() if sky is not None else None
        if waiting or (sky is not None and shading is None and self.sources.pending()):
            return None
        if self.frame is None:
            self.frame = GameFrame(self.r.ctx)
        quality = getattr(self, "quality", None)  # the menu's (``modelrender``): 117's scale, shadow map, cascades
        self.frame.set_quality(quality)
        size = drawn_size(size, quality)  # the frame's composite stretches it over the view
        both = (shading, sky) if shading is not None and not shading.error else None
        box = self.r.box
        lights = self._lights() if self.switches.get("lights", True) else []
        fit = self._fit() if casters else None
        sky_passes = self._sky() if both is not None and self.switches.get("sky", True) else None
        water = self._water() if see_through else None
        waves = self._waves(water)
        atmosphere = self._atmosphere() if sky_passes is not None and self.switches.get("atmosphere", True) else None
        clouds = self._clouds() if atmosphere is not None and not atmosphere.error else None
        if clouds is not None:  # the weather and the noise drift with the wind over the view's time
            clouds.seconds = float(self.r.state.time)
        near, far = camera.planes()  # with clouds the rays reach the engine's far plane (frame-117-viewer.md 12)
        cam = self.frame.camera(size, camera.eye(), camera.target, camera.fov, near, far,
                                clouds.maps.far if clouds is not None else 0.0)  # fmt: skip
        self._rebind_see_through((size, water is not None and not water.error, waves))
        look = gameatmosphere.Look()
        passes = Passes(casters, (box[0], box[1]) if box is not None else None, lights, fit, see_through, sky_passes,
                        water, atmosphere, look, clouds)  # fmt: skip
        self.frame.render(target, size, found, cam, light, finish=False, shading=both, passes=passes)
        for part in (shading, fit, sky_passes, water, atmosphere, clouds):
            if part is not None and part.error:
                self.r.say(part.error)
        return rest

    def _ground(self, place):
        """The ground's model in the G-buffer at ``place`` (None: no ground, or while it compiles)."""
        if place is None:
            return None
        if self.ground is None:
            self.ground = (
                self._part(
                    "ground",
                    gameground.compile_ground,
                    lambda got: gamedraw.GameModel(self.r.ctx.rhi, self._program(*got[:2]), gameground.mesh(), got[2]),
                    N_("viewer.ground_by_games_model_shader"),
                    N_("viewer.none_in_games_frame"),
                    lambda: self.__setattr__("ground", False),
                )
                or self.ground
            )  # False after an error stays
        if not self.ground:
            return None
        self.ground.world = MIRROR @ np.asarray(place, float)
        return self.ground

    def _rebind_see_through(self, key: tuple) -> None:
        """The transparent draws made again (on their next prepare) when the scene's copies they bind by name were
        made anew: another view size, or the copies ready after they compiled."""
        if key == self._bound:
            return
        self._bound = key
        for k in self.transparent:
            for m in (self.models.get(k), self.casters.get(k)):
                if isinstance(m, gamedraw.GameModel):
                    m.release()

    def _atmosphere(self):
        """The visible sky and haze by the game's environment shaders (frame-117-viewer.md 8); None while they
        compile, or when they do not (the view's own sky then)."""
        if self.atmosphere is None:
            self.atmosphere = self._part(
                "atmosphere",
                lambda source: (source.stage_set(gameatmosphere.STAGES), source.sky_passes()[1]),
                lambda got: gameatmosphere.Atmosphere(self.r.ctx.rhi, *got),
                N_("viewer.games_visible_sky_haze"),
                N_("viewer.our_sky"),
            )
        return self.atmosphere

    def _clouds(self):
        """The clouds by the game's environment shaders (frame-117-viewer.md 12); None while they compile, when
        they do not, or when switched off."""
        if not self.switches.get("clouds", True):
            return None
        if self.clouds is None:
            self.clouds = self._part(
                "clouds",
                lambda source: (
                    source.stage_set(gameclouds.STAGES),
                    gameclouds.read_maps(source.files, source.ambient_text()),
                ),
                lambda got: gameclouds.Clouds(self.r.ctx.rhi, *got),
                N_("viewer.games_clouds"),
                N_("viewer.no_clouds"),
            )
        return self.clouds if self.clouds is not None and not self.clouds.error else None

    def _waves(self, water) -> bool:
        """The ocean's wave simulation for this frame into ``water``'s inputs (frame-117-viewer.md 10); False while
        it compiles, when it does not or when switched off (the water's flat surface then)."""
        if water is None or water.error:
            return False
        ocean = None
        if self.switches.get("waves", True):
            if self.ocean is None:
                self.ocean = self._part(
                    "ocean",
                    lambda source: (source.stage_set(gameocean.STAGES), gameocean.read_water(source.ambient_text())),
                    lambda got: gameocean.OceanSimulation(self.r.ctx.rhi, *got),
                    N_("viewer.games_ocean_simulation"),
                    N_("viewer.flat_water"),
                )
            ocean = self.ocean if self.ocean is not None and not self.ocean.error else None
        if ocean is None:
            water.waves, water.constants = {}, dict(gamewater.CONSTANTS)
            return False
        ocean.run(self.r.ctx, float(self.r.state.time))
        water.waves, water.constants = ocean.inputs(), dict(gameocean.CONSTANTS)
        return True

    def _water(self):
        """The scene's copies the water reads (frame-117-viewer.md 7); None while they compile, or when they do not
        (the water then reads zeros)."""
        if self.water is None:
            self.water = self._part(
                "water",
                lambda source: (source.stage_set(gamewater.STAGES), source.float_maps(gamewater.MAPS)),
                lambda got: gamewater.SceneCopies(self.r.ctx.rhi, *got),
                N_("viewer.scenes_copies_for_water"),
                N_("viewer.water_without_them"),
            )
        return self.water

    def _lights(self) -> list:
        """The config's lights where they stand this frame, in the view's axes (as the renderer's own look takes
        them, ``modelrender._light_set``): point, spot and area lights by their ``Type``, those with
        ``ClusteredShading`` 0 left out (docs/rendering/frame-117-viewer.md 1)."""
        r, out = self.r, []
        state = r.state
        for lamp in r.scene.lights if r.scene is not None else []:
            place = r.place(lamp, state) if state.shows(lamp) else None
            if place is None or not lamp.clustered:
                continue
            light = lightclusters.from_config(lamp, MIRROR @ np.asarray(place, float), state.light_color(lamp))
            if light is not None:
                out.append(light)
        return out

    def release_scene(self) -> None:
        for m in self.casters.values():
            m.release()
        self.casters, self.transparent = {}, set()
        self._last_frame = None  # a new model: the eye adaptation starts at its target
        super().release_scene()

    def _release_frame(self) -> None:
        if self.ocean is not None:
            self.ocean.release()
            self.ocean = None
        if self.atmosphere is not None:
            self.atmosphere.release()
            self.atmosphere = None
        if self.clouds is not None:
            self.clouds.release()
            self.clouds = None
        if self.ground:
            self.ground.release()
        self.ground = None
        if self.water is not None:
            self.water.release()
            self.water = None
        if self.eye is not None:
            self.eye.release()
            self.eye = None
        if self.post is not None:
            self.post.release()
            self.post = None
        if self.sky is not None:
            self.sky.release()
            self.sky = None
        if self.fit is not None:
            self.fit.release()
            self.fit = None
        if self.shading is not None:
            self.shading.release()
            self.shading = None
        if self.frame is not None:
            self.frame.release()
            self.frame = None


def _palette(item: dict, model) -> np.ndarray | None:
    """A skin's matrices this frame: the posed ones, or the bind pose (identity for every joint the vertices reach)
    while no animation plays: the renderer poses a skin only for an animation, and without a palette every joint
    but the first would read zero."""
    if item.get("skinned") is None:
        return None
    if item.get("matrices") is not None:
        return item["matrices"]
    joints = max(1, min(gameshader.LENGTHS["float4x3"], model.mesh.bone_count(4)))
    return np.broadcast_to(np.identity(4), (joints, 4, 4))
