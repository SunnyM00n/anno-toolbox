"""The viewer's look by Anno 117's own shaders: every part's materials compiled from the install by the game's
compiler (``models.shaderview``), drawn into the engine's G-buffer and lit by the game's deferred shading
(``render.gamedraw``, ``gameframe``, ``gamelight``), on Direct3D 12; a material whose effect has no G-buffer pass, or
that does not compile, is drawn by our plain look (viewer.md "Looks").

``CGamePass(renderer)`` (an ``ownpass.COwnPass``) is the entry, one per ``modelrender.CModelRenderer``: ``draw(drawn, target, size_px, camera,
light)`` per frame returns what it left to the renderer (None while the shaders compile: the renderer draws its game
look). ``why_not`` says when it cannot draw at all. docs/viewer.md "The renderer"; docs/tool-suite.md section 21.
"""

from __future__ import annotations

import functools
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
from ..render.gameframe import CGameFrame, SLight, SPasses
from ..sources.assetdb import number
from .forwardpass import drawn_size
from .ownpass import COwnPass

GAME = "anno117"


def make_pass(renderer):
    """The own-shader pass of the renderer's game: ``pass_class`` of its ``nativelook.GAMES`` record ("module:Class",
    each with CGamePass's interface); 117's ``CGamePass`` for a game without one."""
    native = nativelook.GAMES.get(getattr(renderer.m_files.m_files, "m_game", ""))
    module, _, name = (native.pass_class if native else "").partition(":")
    if not name:
        return CGamePass(renderer)
    from importlib import import_module

    return getattr(import_module(f"{__package__.partition('.')[0]}.{module}"), name)(renderer)


class CGamePass(COwnPass):
    """The parts of one scene as the game's shaders draw them (module docstring)."""

    GAME = GAME
    PREFIX = "gs"
    SAMPLES = 1  # 117's frame is deferred and has no multisampling of its own (performance.md section 4, item 7)
    SOURCE = shaderview.CShaderSource
    NOT_OURS = shaderview.CNoGBufferPass

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.m_frame: CGameFrame | None = None
        self.m_casters: dict[tuple, gamedraw.CGameModel] = {}  # the same keys: the group's shadow technique
        self.m_programs: dict[tuple, gamedraw.CProgram] = {}
        self.m_shading: gamelight.CDeferredShading | None = None
        self.m_fit: shadowfit.CShadowFit | None = None
        self.m_sky: gamesky.CSkyPasses | None = None
        self.m_post: gamepost.CGamePost | None = None
        self.m_eye: eyeadapt.CEyeAdaptation | None = None
        self.m_water: gamewater.CSceneCopies | None = None
        self.m_atmosphere: gameatmosphere.CAtmosphere | None = None
        self.m_clouds: gameclouds.CClouds | None = None
        self.m_ocean: gameocean.COceanSimulation | None = None
        self.m_ground: gamedraw.CGameModel | None | bool = None  # False: it does not compile
        self._m_last_frame: float | None = None  # when the eye adaptation last ran (its easing's time step)
        self.m_transparent: set[tuple] = set()  # the keys of groups drawn by their effect's Transparent technique
        self._m_bound: tuple = ()  # the view size and copies the transparent draws were bound with
        self.m_settling = False  # the last frame's clouds and fog still fill in over frames: one more frame

    def busy(self) -> int:
        return super().busy() + int(self.m_settling)

    def _make_source(self, tree) -> shaderview.CShaderSource:
        return shaderview.CShaderSource(tree, dxc.CDxc.find(anno117=getattr(tree, "m_folder", None)))

    def _work(self, source, part, item):
        """In the background: the group's two stages, material and mesh as stored."""
        values = shaderview.first_values(part.material(item["key"]).values)
        stem = shaderview.effect_of(number(values.get("ShaderID"), -1))
        stored = getattr(item.get("data"), "stored", None)
        if stem is None or stored is None or not values:
            raise shaderview.CNoGBufferPass("not a model of the game's shaders")
        got = source.draw_stages(stem, values)
        mesh = gamedraw.CMesh.of_rdm(stored, item["idx"])
        return (
            got.vertex_stage,
            got.pixel_stage,
            source.material(got.refs, values),
            mesh,
            got.shadow,
            got.is_transparent,
        )

    def _build(self, key: tuple, got) -> gamedraw.CGameModel:
        vertex_shader, pixel_shader, material, mesh, shadow, transparent = got
        state = renderstates.pass_state(pixel_shader.state) if transparent else renderstates.GBUFFER_STATE
        model = self.m_models[key] = gamedraw.CGameModel(self.m_renderer.m_ctx.m_rhi, self._program(vertex_shader, pixel_shader), mesh, material,
                                                      state=state)  # fmt: skip
        if transparent:
            self.m_transparent.add(key)
        if shadow is not None:
            self.m_casters[key] = gamedraw.CGameModel(
                self.m_renderer.m_ctx.m_rhi, self._program(*shadow), mesh, material, model
            )
        return model

    def _pieces(self, key: tuple, model) -> tuple:
        return (*super()._pieces(key, model), self.m_casters.get(key))

    def _fall_back(self, key: tuple, error: str) -> None:
        caster = self.m_casters.pop(key, None)
        if caster is not None:
            caster.release()
        self.m_transparent.discard(key)
        super()._fall_back(key, error)

    def _unowned(self, model, error: str) -> None:
        if model is self.m_ground:  # the view draws its own ground instead
            model.release()
            self.m_ground = False
        super()._unowned(model, error)

    def _program(self, vertex_shader, pixel_shader) -> gamedraw.CProgram:
        # a program holds its stages, so their ids stay theirs for as long as the entry lives
        key = (id(vertex_shader), id(pixel_shader))  # pattern: ok (the program holds both)
        program = self.m_programs.get(key)
        if program is None:
            program = self.m_programs[key] = gamedraw.CProgram(vertex_shader, pixel_shader)
        return program

    def _shading(self):
        """The deferred shading of this device (None while it compiles, or when it does not: our light then)."""
        if self.m_shading is None:
            self.m_shading = self._part(
                "shading",
                self.SOURCE.shading,
                lambda got: gamelight.CDeferredShading(self.m_renderer.m_ctx.m_rhi, *got),
                N_("viewer.games_deferred_shading"),
                N_("viewer.our_light_instead"),
            )
        return self.m_shading

    def _fit(self):
        """The game's cascade analysis of this device (None while it compiles, or when it does not: the CPU's fit)."""
        if self.m_fit is None:
            self.m_fit = self._part(
                "shadowfit",
                self.SOURCE.shadow_fit,
                lambda got: shadowfit.CShadowFit(self.m_renderer.m_ctx.m_rhi, got),
                N_("viewer.games_shadow_analysis"),
                N_("viewer.fitted_on_cpu"),
            )
        return self.m_fit

    def finish(self, source, size_px: tuple[int, int], lut):
        """The game's grade and tone map over the view's resolved HDR colour (``source``, a device texture; ``lut``
        the ambient's grade cube or None): the tone-mapped texture, or None when the post chain is switched off or
        not ready (the view's own finish then)."""
        if not self.m_switches.get("post", True) or self.m_shading is None:
            return None
        if self.m_post is None:
            self.m_post = self._part(
                "post",
                self.SOURCE.post_stages,
                lambda got: gamepost.CGamePost(self.m_renderer.m_ctx.m_rhi, got),
                N_("viewer.games_post_chain"),
                N_("viewer.our_finish"),
            )
        if self.m_post is None or self.m_post.m_error:
            return None
        inputs = {
            k: value
            for k, value in (
                ("cBlueNoiseTex", self.m_shading.m_fixed.get("cBlueNoiseTex")),
                ("cLUTTexture0", lut.native if lut is not None else None),
            )
            if value is not None
        }
        return self.m_post.render(self.m_renderer.m_ctx, source, size_px, inputs, self._exposure(source, size_px))

    def _exposure(self, source, size_px: tuple[int, int]):
        """The game's eye adaptation over ``source`` (frame-117-viewer.md 6): its exposure buffer, or None when it is
        switched off or not ready (the tone map then without it)."""
        if not self.m_switches.get("eye", True):
            self._m_last_frame = None
            return None
        if self.m_eye is None:
            self.m_eye = self._part(
                "eye",
                lambda source: source.stage_set(eyeadapt.STAGES),
                lambda got: eyeadapt.CEyeAdaptation(self.m_renderer.m_ctx.m_rhi, got),
                N_("viewer.games_eye_adaptation"),
                N_("viewer.tone_map_without_it"),
            )
        if self.m_eye is None or self.m_eye.m_error:
            return None
        now = time.monotonic()
        step = None if self._m_last_frame is None else now - self._m_last_frame
        if step is None or step > eyeadapt.SETTLE:
            self.m_eye.restart()
        self._m_last_frame = now
        self.m_eye.run(self.m_renderer.m_ctx, source, size_px, step)
        return self.m_eye.m_exposure

    def _sky(self):
        """The game's sky passes of this device (None while they compile, or when they do not: the look's light)."""
        if self.m_sky is None:
            self.m_sky = self._part(
                "sky",
                self.SOURCE.sky_passes,
                lambda got: gamesky.CSkyPasses(self.m_renderer.m_ctx.m_rhi, *got),
                N_("viewer.games_sky_passes"),
                N_("viewer.looks_sky_light"),
            )
        return self.m_sky

    def draw(self, drawn: list, target, size_px: tuple[int, int], camera, light: SLight, sky=None,
             ground=None) -> list | None:  # fmt: skip
        """The G-buffer pass of every group the game's shaders draw, lit (``sky``: a ``gamelight.SSky`` for the
        game's deferred shading, else ``light``, ours) into ``target`` with its depth, the groups' shadow techniques
        into the sun's cascades, the groups without a G-buffer pass by their transparent technique over the lit
        colour (``switches``); the rest of ``drawn`` is returned for the renderer, or None while a shader still
        compiles. ``ground``: the view's ground plane (a part matrix), drawn into the G-buffer by the game's model
        shader (``render.gameground``)."""
        self.m_settling = False
        ready, rest, waiting = self._split(drawn)
        found, casters, see_through, scene = [], [], [], []
        transparent_on = self.m_switches.get("transparent", True)
        for key, part, item, place, model in ready:
            if key in self.m_transparent and not transparent_on:
                rest.append((part, item, place))
                continue
            caster = self.m_casters.get(key)
            palette = _palette(item, model)
            for drawn_model in (model, caster) if caster is not None else (model,):
                drawn_model.m_world = MIRROR @ np.asarray(place, float)
                drawn_model.m_palette = palette
            (see_through if key in self.m_transparent else found).append(model)
            casts = caster is not None and self.m_switches.get("shadows", True)
            if casts:
                casters.append(caster)
            scene.append(((key, casts), model))  # a caster stands where its model stands
        plane = self._ground(ground)
        if plane is not None:
            found.append(plane)
            scene.append(("ground", plane))
        shading = self._shading() if sky is not None else None
        if waiting or (sky is not None and shading is None and self.m_sources.pending()):
            return None
        if self.m_frame is None:
            self.m_frame = CGameFrame(self.m_renderer.m_ctx)
        quality = getattr(self, "m_quality", None)  # the menu's (``modelrender``): 117's scale, shadow map, cascades
        self.m_frame.set_quality(quality)
        size_px = drawn_size(size_px, quality)  # the frame's composite stretches it over the view
        both = (shading, sky) if shading is not None and not shading.m_error else None
        box = self.m_renderer.m_box
        lights = self._lights() if self.m_switches.get("lights", True) else []
        fit = self._fit() if casters else None
        sky_passes = self._sky() if both is not None and self.m_switches.get("sky", True) else None
        water = self._water() if see_through else None
        waves = self._waves(water)
        atmosphere = self._atmosphere() if sky_passes is not None and self.m_switches.get("atmosphere", True) else None
        clouds = self._clouds() if atmosphere is not None and not atmosphere.m_error else None
        if clouds is not None:  # the weather and the noise drift with the wind over the view's time
            clouds.m_seconds = float(self.m_renderer.m_state.time)
        near, far = camera.planes()  # with clouds the rays reach the engine's far plane (frame-117-viewer.md 12)
        cam = self.m_frame.camera(size_px, camera.eye(), camera.m_target, camera.m_fov_rad, near, far,
                                clouds.m_maps.far if clouds is not None else 0.0)  # fmt: skip
        self._rebind_see_through((size_px, water is not None and not water.m_error, waves))
        look = gameatmosphere.SLook(temporal=True)  # a quarter of the pixels marched a frame, as the game does
        passes = SPasses(casters, (box[0], box[1]) if box is not None else None, lights, fit, see_through, sky_passes,
                        water, atmosphere, look, clouds,
                        scene=_scene_key(self.m_renderer.m_state.time, scene))  # fmt: skip
        self.m_frame.render(target, size_px, found, cam, light, finish=False, shading=both, passes=passes)
        self.m_settling = atmosphere is not None and atmosphere.settling()
        for part in (shading, fit, sky_passes, water, atmosphere, clouds):
            if part is not None and part.m_error:
                self.m_renderer.say(part.m_error)
        return rest

    def _ground(self, place):
        """The ground's model in the G-buffer at ``place`` (None: no ground, or while it compiles)."""
        if place is None:
            return None
        if self.m_ground is None:
            self.m_ground = (
                self._part(
                    "ground",
                    gameground.compile_ground,
                    lambda got: gamedraw.CGameModel(
                        self.m_renderer.m_ctx.m_rhi, self._program(*got[:2]), gameground.mesh(), got[2]
                    ),
                    N_("viewer.ground_by_games_model_shader"),
                    N_("viewer.none_in_games_frame"),
                    lambda: setattr(self, "m_ground", False),
                )
                or self.m_ground
            )  # False after an error stays
        if not self.m_ground:
            return None
        self.m_ground.m_world = MIRROR @ np.asarray(place, float)
        return self.m_ground

    def _rebind_see_through(self, key: tuple) -> None:
        """The transparent draws made again (on their next prepare) when the scene's copies they bind by name were
        made anew: another view size, or the copies ready after they compiled."""
        if key == self._m_bound:
            return
        self._m_bound = key
        for k in self.m_transparent:
            for model in (self.m_models.get(k), self.m_casters.get(k)):
                if isinstance(model, gamedraw.CGameModel):
                    model.release()

    def _atmosphere(self):
        """The visible sky and haze by the game's environment shaders (frame-117-viewer.md 8); None while they
        compile, or when they do not (the view's own sky then)."""
        if self.m_atmosphere is None:
            self.m_atmosphere = self._part(
                "atmosphere",
                lambda source: (
                    source.stage_set({**gameatmosphere.STAGES, **gameatmosphere.TEMPORAL_STAGES}),
                    source.sky_passes()[1],
                ),  # fmt: skip
                lambda got: gameatmosphere.CAtmosphere(self.m_renderer.m_ctx.m_rhi, *got),
                N_("viewer.games_visible_sky_haze"),
                N_("viewer.our_sky"),
            )
        return self.m_atmosphere

    def _clouds(self):
        """The clouds by the game's environment shaders (frame-117-viewer.md 12); None while they compile, when
        they do not, or when switched off."""
        if not self.m_switches.get("clouds", True):
            return None
        if self.m_clouds is None:
            self.m_clouds = self._part(
                "clouds",
                lambda source: (
                    source.stage_set(gameclouds.STAGES),
                    gameclouds.read_maps(source.m_files, source.ambient_text()),
                ),
                lambda got: gameclouds.CClouds(self.m_renderer.m_ctx.m_rhi, *got),
                N_("viewer.games_clouds"),
                N_("viewer.no_clouds"),
            )
        return self.m_clouds if self.m_clouds is not None and not self.m_clouds.m_error else None

    def _waves(self, water) -> bool:
        """The ocean's wave simulation for this frame into ``water``'s inputs (frame-117-viewer.md 10); False while
        it compiles, when it does not or when switched off (the water's flat surface then)."""
        if water is None or water.m_error:
            return False
        ocean = None
        if self.m_switches.get("waves", True):
            if self.m_ocean is None:
                self.m_ocean = self._part(
                    "ocean",
                    lambda source: (source.stage_set(gameocean.STAGES), gameocean.read_water(source.ambient_text())),
                    lambda got: gameocean.COceanSimulation(self.m_renderer.m_ctx.m_rhi, *got),
                    N_("viewer.games_ocean_simulation"),
                    N_("viewer.flat_water"),
                )
            ocean = self.m_ocean if self.m_ocean is not None and not self.m_ocean.m_error else None
        if ocean is None:
            water.m_waves, water.m_constants = {}, dict(gamewater.CONSTANTS)
            return False
        ocean.run(self.m_renderer.m_ctx, float(self.m_renderer.m_state.time))
        water.m_waves, water.m_constants = ocean.inputs(), dict(gameocean.CONSTANTS)
        return True

    def _water(self):
        """The scene's copies the water reads (frame-117-viewer.md 7); None while they compile, or when they do not
        (the water then reads zeros)."""
        if self.m_water is None:
            self.m_water = self._part(
                "water",
                lambda source: (source.stage_set(gamewater.STAGES), source.float_maps(gamewater.MAPS)),
                lambda got: gamewater.CSceneCopies(self.m_renderer.m_ctx.m_rhi, *got),
                N_("viewer.scenes_copies_for_water"),
                N_("viewer.water_without_them"),
            )
        return self.m_water

    def _lights(self) -> list:
        """The config's lights where they stand this frame, in the view's axes (as the renderer's own look takes
        them, ``modelrender._light_set``): point, spot and area lights by their ``Type``, those with
        ``ClusteredShading`` 0 left out (docs/rendering/frame-117-viewer.md 1)."""
        renderer, out = self.m_renderer, []
        state = renderer.m_state
        for lamp in renderer.m_scene.m_lights if renderer.m_scene is not None else []:
            place = renderer.place(lamp, state) if state.shows(lamp) else None
            if place is None or not lamp.clustered:
                continue
            light = lightclusters.from_config(lamp, MIRROR @ np.asarray(place, float), state.light_color(lamp))
            if light is not None:
                out.append(light)
        return out

    def release_scene(self) -> None:
        for model in self.m_casters.values():
            model.release()
        self.m_casters, self.m_transparent = {}, set()
        self._m_last_frame = None  # a new model: the eye adaptation starts at its target
        super().release_scene()

    def _release_frame(self) -> None:
        if self.m_ocean is not None:
            self.m_ocean.release()
            self.m_ocean = None
        if self.m_atmosphere is not None:
            self.m_atmosphere.release()
            self.m_atmosphere = None
        if self.m_clouds is not None:
            self.m_clouds.release()
            self.m_clouds = None
        if self.m_ground:
            self.m_ground.release()
        self.m_ground = None
        if self.m_water is not None:
            self.m_water.release()
            self.m_water = None
        if self.m_eye is not None:
            self.m_eye.release()
            self.m_eye = None
        if self.m_post is not None:
            self.m_post.release()
            self.m_post = None
        if self.m_sky is not None:
            self.m_sky.release()
            self.m_sky = None
        if self.m_fit is not None:
            self.m_fit.release()
            self.m_fit = None
        if self.m_shading is not None:
            self.m_shading.release()
            self.m_shading = None
        if self.m_frame is not None:
            self.m_frame.release()
            self.m_frame = None


def _palette(item: dict, model) -> np.ndarray | None:
    """A skin's matrices this frame: the posed ones, or the bind pose (identity for every joint the vertices reach)
    while no animation plays: the renderer poses a skin only for an animation, and without a palette every joint
    but the first would read zero."""
    if item.get("skinned") is None:
        return None
    if item.get("matrices") is not None:
        return item["matrices"]
    return _rest_pose(max(1, min(gameshader.LENGTHS["float4x3"], model.m_mesh.bone_count(4))))


def _scene_key(seconds: float, drawn: list[tuple]) -> tuple:
    """``SPasses.scene``: the view's clock and the drawn models (key, model) with their places in one block of bytes,
    and the pose, values and raw bytes of those that have any (``_drawn_key``)."""
    places = np.stack([np.asarray(model.m_world, float) for _key, model in drawn]).tobytes() if drawn else b""
    posed = tuple((key, _drawn_key(model)) for key, model in drawn
                  if model.m_palette is not None or getattr(model, "m_values", None) or getattr(model, "m_raw", None))  # fmt: skip
    return float(seconds), tuple(key for key, _model in drawn), places, posed


def _drawn_key(model) -> tuple:
    """What a drawn model adds to the frame's depth besides its place, comparable by content: its pose, its owner's
    values and its raw bytes (a cloth's simulated vertices)."""
    key = gameshader.value_key
    values = tuple((path, key(value)) for path, value in getattr(model, "m_values", {}).items())
    raw = tuple((name, key(data)) for name, data in getattr(model, "m_raw", {}).items())
    return key(model.m_palette), values, raw


@functools.lru_cache(maxsize=8)
def _rest_pose(joints: int) -> np.ndarray:
    """The bind pose of ``joints`` joints, made once: ``fill_instance`` keeps the packing of a pose it met."""
    return np.broadcast_to(np.identity(4), (joints, 4, 4))
