"""The viewer's look by Anno 1404's own shaders: ``model.fx`` compiled from the install by the compiler the game's
generation used (``d3dcompiler_43``, shader model 4) and drawn forward-lit on Direct3D 11 with the sun's shadow, the
sky and the post sequence (``render.frame1404``, ``sky2070``, ``post2070``); a group that does not compile keeps our
look.

``FxcPass1404(renderer)`` is an ``ownpass.OwnPass`` (``why_not``, ``busy``, ``draw``, ``finish``, ``release_scene``,
``release``); the ambient set's light and atmosphere are ``frame1404.read_sun``'s. docs/rendering/shader-compile.md
section 12.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..models import shader2070
from ..models.modelread import SHADER_DYE
from ..models.shader2070 import NoPass
from ..render import clothmesh, frame1404, fxcdraw, ocean1404, post2070, sky2070
from ..render import gameshader as gs
from ..render.frame1404 import FX, TECHNIQUE, Frame1404, Model1404, Sun1404, Water1404, granny_mesh, material_defines
from ..render.gamedraw import Program
from .fxcpass import object_rgba, old_compiler, optional_pass
from .ownpass import PLAYER_COLOR, OwnPass

GAME = "anno1404"


class _Source:
    """What the pass needs of the install, read once in the background: the compiler, the sources and the light."""

    def __init__(self, tree) -> None:
        self.compiler = old_compiler(tree, GAME)
        self.shaders = shader2070.Source2070(tree, self.compiler, shader2070.GLOBALS_1404)
        text = frame1404.config_text(tree, frame1404.AMBIENT)
        self.sun, self.waves = frame1404.read_sun(text), ocean1404.read_waves(text, "North")


def sea_work(source: _Source) -> dict:
    """In the background: the sea's passes (the grid's effect includes a ``noise.fxh`` no install ships: read as
    empty, as the island view does) and the sky cube's."""
    shaders = shader2070.Source2070(source.shaders.files, source.compiler, shader2070.GLOBALS_1404, missing_empty=True)
    stages = {k: shaders.effect_pass(*v) for k, v in ocean1404.STAGES.items()}
    return {**stages, "cube": shaders.effect_pass(frame1404.SKY_FX, (), sky2070.CUBE_TECHNIQUE)}


def granny_vertices(item: dict):
    """A group's Granny mesh data (position, normal, uv per row), ``NoPass`` for anything else."""
    data = item.get("data")
    if data is None or getattr(data, "vertices", None) is None or data.vertices.shape[1] < 8:
        raise NoPass("not a mesh of the game's shaders")
    return data


def follow_skin(item: dict, model) -> None:
    """A skinned group's mesh brought to the pose the view's rig gave it this frame (``item``'s ``matrices``, set by
    the renderer's ``_pose``; ``frame1404.posed_vertices``); its three draws share the mesh and upload it again."""
    skin, matrices, pose = item.get("skinned"), item.get("matrices"), item.get("pose")
    mesh = clothmesh.mesh_of(model)
    if skin is None or matrices is None or mesh is None or getattr(model, "pose", ()) == pose:
        return
    mesh.set_vertices(frame1404.posed_vertices(mesh, skin, matrices), mesh.version + 1)
    model.pose = pose


class FxcPass1404(OwnPass):
    """The parts of one scene as 1404's shaders draw them (module docstring)."""

    GAME = GAME
    PREFIX = "fxc1404"
    SOURCE = _Source
    NOT_OURS = NoPass
    FINISH = 3  # the post sequence leaves linear light: the view's final pass encodes it, no tone curve
    GAME_AXES, MIRROR_CUBES = True, False
    look2070 = True  # the view's own ground in the light of 2070's look, the nearest of ours to 1404's
    FRAME, MODEL = Frame1404, Model1404
    WATER_MODEL = Water1404  # a water sheet's model (1701's draws as any other)
    COLOR_MAP = "cTexture0"
    CUBES = ("tSkyEnv", "cTexture2")  # the shader's cube maps

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.frame: Frame1404 | None = None
        self.sky_stages = None  # the compiled sky, once
        self.sky_broken = False
        self.post: post2070.PostChain2070 | None = None
        self.sun_now: Sun1404 | None = None
        self.sea = None  # the game's sea under the model (Ocean waves)
        self.sea_broken = False
        self.ground = False  # the sea drew last frame: it stands for the view's ground (modelrender)
        self._view = None  # this frame's (camera, size, ground place) for the sea
        self.sky_cube: sky2070.SkyCube | None = None  # the sky the sea reflects

    def _work(self, source: _Source, part, item):
        """In the background: the group's three passes (lit, lit with the shadow, the caster), its vertices, its
        maps by the shader's names, its object colour (a dye material's: the player's) and whether it is water."""
        data = granny_vertices(item)
        material = part.material(item["key"])
        values = dict(material.values)
        if frame1404.is_water(values):
            return self._water_work(source, data, item, values)
        dye = int(values.get("ShaderIndex", 0)) == SHADER_DYE and bool(values.get("Texture.mask"))
        fx = frame1404.DYE_FX if dye else FX
        defines = material_defines(material.diffuse, material.normal)
        stages = [source.shaders.effect_pass(fx, defines, TECHNIQUE)]
        for technique in (frame1404.SHADOWED, frame1404.CASTER):
            stages.append(optional_pass(lambda t=technique: source.shaders.effect_pass(fx, defines, t)))
        mesh = granny_mesh(data.vertices, item["idx"], tangents=bool(material.normal))
        maps = {"cTexture0": material.diffuse, "cTexture1": material.normal}
        maps.update({"cTexture4": values["Texture.mask"]} if dye else {})
        return stages, mesh, maps, PLAYER_COLOR if dye else (1.0, 1.0, 1.0), False

    def _water_work(self, source: _Source, data, item: dict, values: dict) -> tuple:
        """A water sheet's passes (``model_water.fx`` refracting the scene with its caustics), its vertices, its ripple
        map, sky cube (the material's environment, else the cloud cube **[I]**) and caustic map and its colour with its
        alpha."""
        defines = (*shader2070.material_defines(values), *frame1404.WATER_DEFINES)
        stages = [source.shaders.effect_pass(frame1404.WATER_FX, defines, t) for t in (TECHNIQUE, frame1404.SHADOWED)]
        mesh = granny_mesh(data.vertices, item["idx"], tangents=("P3N3G3B3T2", "1") in defines)
        cube = values.get("Texture.environment") or frame1404.SKY_CUBE
        maps = {"cTexture0": values.get("Texture.diffuse", ""), "tSkyEnv": cube, "cTexture2": cube,
                "cWaterCausticMap": frame1404.CAUSTICS}  # fmt: skip
        return [*stages, None], mesh, maps, object_rgba(values), True

    def _build(self, key: tuple, got) -> Model1404:
        stages, mesh, maps, color, water = got
        draws = [fxcdraw.FxcDraw(self.r.ctx, Program(s.vs, s.ps), mesh, s.state, self._dummies()) if s else None
                 for s in stages]  # fmt: skip
        model = self.models[key] = (self.WATER_MODEL if water else self.MODEL)(*draws)
        model.paths, model.color, model.water = maps, tuple(color[:3]), water
        model.alpha = float(color[3]) if len(color) > 3 else 1.0  # a water sheet's colour carries its alpha
        return model

    def _sky_compiled(self, key: str, compile_):
        """The sky's compiled stages (``compile_(source)`` once, in the background), None while they compile or
        where they failed (said once: the view's sky)."""
        if self.sky_stages is None and not self.sky_broken:
            self.sky_stages = self._part(key, compile_, lambda got: got, "the game's sky", "the view's sky",
                                         lambda: setattr(self, "sky_broken", True))  # fmt: skip
        return self.sky_stages

    def _sky(self, frame: Frame1404) -> None:
        """The frame's sky as compiled and switched on, its clouds once the cube is in (off while it compiles)."""
        stages = self._sky_compiled("sky1404", lambda s: s.shaders.effect_pass(frame1404.SKY_FX, (), sky2070.TECHNIQUE))
        make = (lambda: sky2070.SkyDome(self.r.ctx, stages, self._dummies())) if stages is not None else None
        sky = fxcdraw.switched(frame, "sky", self._on("atmosphere"), make)
        if sky is not None:  # Clouds off: no cube, its stand-in reads no density (sky2070.NO_CLOUDS)
            sky.cube = self._cube(frame1404.SKY_CUBE) if self._on("clouds") else None

    def draw(self, drawn: list, target, size: tuple[int, int], camera, light, sky=None, ground=None) -> list | None:
        """Every group the game's shaders draw into ``target`` with its depth; the rest of ``drawn`` is returned for
        the renderer, or None while a shader compiles."""
        self._view = (camera, size, ground)
        ready, rest, waiting = self._split(drawn)
        if waiting:
            return None
        found = []
        for _key, _group, item, place, model in ready:
            follow_skin(item, model)
            model.world = self._world(place)
            for name, path in model.paths.items():
                model.maps[name] = self._map_of(name, path)
            if self._on("water") or not model.water:
                found.append(model)
        if found:
            self.frame = self.frame or self.FRAME(self.r.ctx)
            self._sky(self.frame)
            self._sea(self.frame)
            cam = self._camera(camera, size)
            seconds = float(getattr(self.r.state, "time", 0.0))
            self.sun_now = self._light_now(self._ready().sun, self._direction(light.sun), seconds)
            self.frame.render(target, size, found, cam, self.sun_now, self._on("shadows"))
        return rest

    def _sea(self, frame) -> None:
        """The frame's sea while Ocean waves is on (``_sea_now``): it stands for the view's ground, and the sun
        shadow's depth reaches as far as it."""
        frame.sea = self._sea_now()
        self.ground = frame.sea is not None
        frame.beyond = self._sea_reach() if self.ground else None

    def _sea_now(self):
        """The sea's draw for this frame (``Frame1404.sea``): Ocean waves on, a ground under the model and the sea's
        passes and maps in; None otherwise. No terrain under the model: the depth map reads open sea and nothing is
        mirrored but the sky **[E]** (shader-compile.md 12, "1404's sea in a model viewer")."""
        view = self._sea_view("ocean1404", sea_work, self._make_sea)
        if view is None:
            return None
        camera, size = view
        waves, ripples, caustics = (self._map(p) for p in (ocean1404.WAVES, ocean1404.RIPPLES, ocean1404.CAUSTICS))
        clouds = self._cube(frame1404.SKY_CUBE)
        if self.sea is None or None in (waves, ripples, caustics, clouds):
            return None
        cam = self._camera(camera, size)
        self.sea.mirrored = self.GAME_AXES
        self.sky_cube.volume = clouds
        source, ocean, sky = self._ready(), self.sea, self.sky_cube

        def draw(fbo, at: tuple[int, int], scene, depth, shadow=None) -> None:  # the frame's light is set by then
            sun = self.sun_now

            def fill(c: dict) -> None:
                values = {**gs.camera_values(cam), **frame1404.light_values(sun)}
                values.update({**frame1404.scattering_values(sun, cam), **sky2070.cloud_turns(sun.seconds)})
                gs.set_values(c.values(), values)

            maps = {"waves": waves, "cTexture0": sky.render(fill), "cTexture2": ripples, "cTexture6": scene,
                    "cTexture8": depth, "cTexture9": caustics}  # fmt: skip
            ocean.draw(fbo, at, (cam, cam), sun, source.waves, maps, shadow)

        return draw

    def _sea_view(self, key: str, compile_, make) -> tuple | None:
        """This frame's (camera, size) for the sea while Ocean waves is on and a ground is under the model, its
        passes asked for (``compile_(source)``, then ``make(stages)`` into ``sea``; a failure said once); None
        otherwise."""
        camera, size, ground = self._view or (None, None, None)
        if ground is None or not self._on("waves") or self.sea_broken:
            return None
        if self.sea is None:
            self.sea = self._part(key, compile_, make, "the game's sea", "no sea",
                                  lambda: setattr(self, "sea_broken", True))  # fmt: skip
        return camera, size

    def _make_sea(self, stages: dict):
        """The sea and the sky cube it reflects (``sky2070.SkyCube`` of 1404's ``sky.fx``) once compiled."""
        self.sky_cube = sky2070.SkyCube(self.r.ctx, stages["cube"], self._dummies())
        return ocean1404.Ocean1404(self.r.ctx, stages, self._dummies())

    def _sea_reach(self) -> np.ndarray:
        """Points of the sea as far as the view sees (its far plane round the camera's target, on the water): the
        sun shadow's depth reaches them, so that the sea past the casters reads lit."""
        camera = self._view[0]
        _near, far = camera.planes()
        x, _y, z = (float(v) for v in camera.target)
        return np.array([[x + dx, 0.0, z + dz] for dx in (-far, far) for dz in (-far, far)])

    def _light_now(self, setting, sun: tuple, seconds: float = 0.0):
        """The install's light with the view's sun direction (``sun``, towards the sun) and time (the clouds')."""
        return replace(setting, direction=sun, seconds=seconds)

    def _map_of(self, name: str, path: str):
        """The texture for the shader's map ``name`` (None without a file or while it loads): a cube for the cube
        maps (``CUBES``); the colour map shows the checker when its file does not read."""
        if not path:
            return None
        return self._cube(path) if name in self.CUBES else self._map(path, missing=name == self.COLOR_MAP)

    def finish(self, source, size: tuple[int, int], lut=None):
        """1404's post sequence over the view's picture (``post2070.STEPS_1404``): the finished linear texture, the
        picture as it is while it compiles or when the post is off."""
        if self.frame is None or self.sun_now is None:
            return None
        if not self._on("post"):
            return source
        if self.post is None:
            self.post = self._part(
                "post1404",
                lambda ready: post2070.compile_steps(ready.shaders, post2070.STEPS_1404),
                lambda stages: post2070.PostChain2070(self.r.ctx, stages, self._dummies(), post2070.STEPS_1404, 1.0),
                "the game's post sequence",
                "the view's finish",
            )
        if self.post is None:
            return source
        return self.post.render(source, None, size, {"cMoodColor": list(self.sun_now.mood)})

    def _release_frame(self) -> None:
        for part in (self.sea, self.sky_cube):
            if part is not None:
                part.release()
        self.sea = self.sky_cube = None
        if self.post is not None:
            self.post.release()
            self.post = None
        if self.frame is not None:
            self.frame.release()
            self.frame = None
