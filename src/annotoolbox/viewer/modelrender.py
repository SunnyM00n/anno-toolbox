"""The model view's renderer (no Qt): one ``modelscene.CScene`` by the game's own shaders (``gamepass``), with our
plain look as the fallback for what they do not draw (``plainshading``; or as a look of its own, the editor's), sun
shadows on a ground plane, skins posed on the GPU (``gpu_skin``), parts that spin or hang on joints (``modelposes``),
the effects, the selection, ``pick``.

``CModelRenderer(ctx, files)`` is the entry: ``show(scene)``, then ``render(fbo, size_px, camera, state)`` per frame;
files load in the background (``busy``). ``scripts/editor_shots.py viewer`` renders it offscreen. What differs from
the game: docs/tool-suite.md "The viewer", "Drawn as the game draws".
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace

import numpy as np

from ..infra.i18n import tr
from ..models import skinning
from ..render import decalmesh, nativelook, picking, viewcolors
from ..render import shaders as S
from ..render.camera import MIRROR, COrbitCamera, gl, look_at, ortho, perspective
from ..render.gameframe import SLight
from ..render.gamelight import SSky
from ..render.light import LOOK2070
from ..render.loader import CLoader
from ..render.modellibrary import banner_arrays
from ..render.particledraw import CParticleDrawer, SPlaced, collect
from . import motion, plainshading
from .clothplay import CClothPlayer
from .gamepass import make_pass
from .modelfiles import CModelFiles
from .modellights import NIGHT, SHADERS, CModelLights, under
from .modelposes import CModelPoses
from .modelscene import BANNER_SHADER, FOAM_SHADER, WATER_SHADER, CScene, SMaterial, SPart
from .states import SStates

SHADOW_SIZE = 2048
DECAL_SHADER = 6  # a decal laid as a mesh: blended over what is below in the game's-shaders look
STREET_SHADER = 19  # a street model: between the decals before and after the streets (decalmesh.pass_of)
# a skin's pose before its first (``_pose``): unlike any pose, so the bind pose (key None) is posed too and the game's
# shaders get a matrix for every joint (with one, a skin's other pieces read no matrix: tool-suite.md 21, round 16)
UNPOSED = ("unposed",)
MIRROR_F4 = MIRROR.astype("f4")  # as the instance buffer and the effects take it


@dataclass
class SViewState:
    """What a frame shows: the look, shadows, the ground, wireframe, effects, the time (seconds into the sequence or
    animation), the parts hidden and selected (config node paths: a node hides or selects every part below it)."""

    look: str = nativelook.PLAIN
    scheme: str = "light"
    shadows: bool = True
    ground: bool = True
    wireframe: bool = False
    particles: bool = True
    # an effect a sequence starts shows only in its windows; False in views with no sequence player
    gate_effects: bool = True
    time: float = 0.0
    animation: dict = field(default_factory=dict)  # part node -> (file, the time it started at, scale, loops)
    hidden: frozenset = frozenset()
    selected: str | None = None
    night: bool = False  # the game's look at night: its lights shine
    switches: dict = field(default_factory=dict)  # the own-shader frame's parts switched off (nativelook.FEATURES)
    states: SStates = field(default_factory=SStates)  # the condition bits the sequence holds (viewer.states)
    cloth: bool = False  # cloths simulated (viewer.clothplay), else drawn at rest
    torn: bool = False  # the object damaged: a cloth that may tear lets its white pins go
    quality: object = None  # the rendering quality of 117 and 1800 (render.quality.SQuality; None: the best)
    # the view's clock (monotonic seconds) this frame and when ``night`` last switched: the time slices' fade
    now: float = 0.0
    night_changed: float = -math.inf
    clock: tuple[int, int, int] | None = None  # the local hour, minute, second (the clock alignments); None: 12:00:00

    def shows(self, thing) -> bool:
        """Whether a part, effect, light or decal shows this frame: not hidden, its masks met, its time slices'
        fade above 0, an effect a sequence starts inside one of its windows."""
        started = self._started_slot(thing)
        return (
            not under(thing.node, self.hidden)
            and self.states.shown(thing.when)
            and self.fade(thing) > 0
            and (started < 0 or self.states.effect_age(started) is not None)
        )

    def _started_slot(self, thing) -> int:
        """The slot a sequence starts ``thing`` by, -1 for a thing no sequence starts or where none is played."""
        return getattr(thing, "started", -1) if self.gate_effects else -1

    def effect_seconds(self, effect) -> float:
        """The seconds into an effect's play: the view's time, or for one a sequence starts the time since its
        element began (``SStates.effect_age``)."""
        started = self._started_slot(effect)
        age = self.states.effect_age(started) if started >= 0 else None
        return self.time if age is None else age

    def fade(self, thing) -> float:
        """How far a thing's time slices let it show (0..1): its fade after the last switch of ``night``
        (``viewer.states``; the light's colour and an effect's alpha are scaled by it)."""
        return self.states.day_night(thing.times, self.night, self.now - self.night_changed)

    def fading(self, longest: float) -> bool:
        """Whether a switch of ``night`` may still be fading (``longest``: the scene's longest ``FadeTime``)."""
        return self.now - self.night_changed < longest

    def light_color(self, lamp) -> tuple[float, float, float]:
        """A config light's colour this frame: its graph's at ``time``, faded by its time slices."""
        k = self.fade(lamp)
        return tuple(float(channel) * k for channel in lamp.color_at(self.time))


def part_bounds(part, mesh, place) -> tuple[np.ndarray, np.ndarray] | None:
    """The box (lo, hi) of a part's mesh (a banner's arrays or a file's meshes) at ``place`` in the view's axes (x
    mirrored); None for a mesh without vertices."""
    verts = [mesh[0][0]] if part.banner is not None else [mesh_part.vertices for mesh_part in mesh.meshes]
    points = [vertices[:, :3] @ place[:3, :3].T + place[:3, 3] for vertices in verts if len(vertices)]
    if not points:
        return None
    bounds_points = np.concatenate(points) * (-1.0, 1.0, 1.0)
    return bounds_points.min(axis=0), bounds_points.max(axis=0)


def ground_place(min_bounds, max_bounds) -> np.ndarray:
    """Where the grey plane under a model of bounds ``lo``, ``hi`` (the view's axes) lies: at its lowest point (its
    own ground where it reaches y = 0), wide enough for its shadow; a part matrix (the instance mirrors it)."""
    reach = max(float(np.linalg.norm(max_bounds - min_bounds)), 0.5) * 1.5
    centre = (min_bounds + max_bounds) / 2
    place = np.diag([reach, 1.0, reach, 1.0]).astype("f4")
    floor = 0.0 if min_bounds[1] <= 0.0 <= max_bounds[1] else min_bounds[1]
    place[:3, 3] = (-centre[0], floor - 0.002, centre[2])
    return place


@dataclass(frozen=True)
class _SFrame:
    """What one frame's draws share: the camera's matrices and eye, the light, the sun's shadow map (None: none)."""

    view_proj: np.ndarray
    view: np.ndarray
    lt: dict
    eye: np.ndarray
    shadow: object = None


class CModelCamera(COrbitCamera):
    """The island view's orbit camera, for models of a few units: closer, and a far plane by the distance."""

    MIN_DISTANCE = 0.05
    PAN_NEAR = 0.0

    def planes(self) -> tuple[float, float]:
        """The near and far plane."""
        return max(0.005, self.m_distance_m / 200), self.m_distance_m * 40 + 10

    def proj(self, aspect: float) -> np.ndarray:
        return perspective(self.m_fov_rad, aspect, *self.planes())

    def zoom(self, steps: float, towards=None) -> None:
        self.m_distance_m = min(500.0, max(self.MIN_DISTANCE, self.m_distance_m * 0.88**steps))

    def frame(self, min_bounds: np.ndarray, max_bounds: np.ndarray) -> None:
        """Look at a box (view space) from where it fills the view."""
        self.m_target = ((min_bounds + max_bounds) / 2).astype("f4")
        radius = max(float(np.linalg.norm(max_bounds - min_bounds)) / 2, 0.05)
        self.m_distance_m = radius / math.sin(self.m_fov_rad / 2) * 1.1
        self.m_yaw_rad, self.m_pitch_rad = math.radians(-145), math.radians(25)


class CModelRenderer(CModelLights, CModelPoses):
    """Draws one scene at a time; ``files`` is the ``CModelFiles`` its parts load from."""

    def __init__(self, ctx, files: CModelFiles) -> None:
        self.m_ctx, self.m_files = ctx, files
        P = ctx.program
        self.m_prog = {
            "model": P(vertex_shader=S.MODEL_VS, fragment_shader=S.MODEL_FS),
            "lit": P(vertex_shader=S.MODEL_VS, fragment_shader=plainshading.LIT_FS),
            "skin_model": P(vertex_shader=plainshading.SKIN_VS, fragment_shader=S.MODEL_FS),
            "skin_lit": P(vertex_shader=plainshading.SKIN_VS, fragment_shader=plainshading.LIT_FS),
            "skin_depth": P(vertex_shader=plainshading.SKIN_VS, fragment_shader=S.DEPTH_FS),
            "banner": P(vertex_shader=S.BANNER_VS, fragment_shader=S.MODEL_FS),
            "depth_model": P(vertex_shader=S.MODEL_VS, fragment_shader=S.DEPTH_FS),
            "depth_banner": P(vertex_shader=S.BANNER_VS, fragment_shader=S.DEPTH_FS),
            "sky": P(vertex_shader=S.SKY_VS, fragment_shader=S.SKY_FS),
            "final": P(vertex_shader=S.FINAL_VS, fragment_shader=S.FINAL_FS),
            "overlay": P(vertex_shader=S.OVERLAY_VS, fragment_shader=S.OVERLAY_FS),
        }
        self.m_fullscreen = {k: ctx.vertex_array(self.m_prog[k], []) for k in ("sky", "final")}
        self.m_inst = ctx.buffer(MIRROR_F4.T.tobytes())  # one instance, the mirror (MODEL_VS takes rows as GLSL does)
        self.m_loader = CLoader()
        self.m_scene: CScene | None = None
        self.m_clock_parts = False
        self.m_longest_fade = 0.0  # the scene's longest time slice fade (seconds): the view draws again while it runs
        self.m_by_node: dict[str, tuple[int, SPart]] = {}
        self._m_rigs: dict = {}
        self._m_frame: dict = {}  # this frame's places and joints, by part node
        self._m_shadow_taken: tuple | None = None  # what the shadow map as it stands is of (``_shadow_key``)
        self._m_lit: dict = {}  # program -> whether this frame's light is in it without the shadow (``_put_light``)
        self._m_names: dict = {}  # program -> its uniform names (``_uniform_names``)
        self.m_items: dict[int, list] = {}  # part index -> [(group key, vbo, ibo, count, vaos)]
        self.m_textures: dict[str, object] = {}
        self.m_failed: dict[str, str] = {}  # a texture file that did not load -> why (the Checks panel lists it)
        self.m_messages: list[str] = []
        self.m_box: tuple[np.ndarray, np.ndarray] | None = None
        self.m_particles = CParticleDrawer(ctx)
        self._m_dummy = ctx.texture((1, 1), 4, bytes([128, 128, 128, 255]))
        self._m_dummy_3d = ctx.texture3d((2, 2, 2), 3, np.zeros(24, "f4").tobytes(), dtype="f4")
        self._m_dummy_shadow = ctx.depth_texture((1, 1), np.ones(1, "f4").tobytes())
        self._m_dummy_shadow.compare_func = "<="
        self._m_targets = None
        self._m_shadow = None
        self._m_ground = None
        self._m_decals: dict[int, dict] = {}
        self.m_gpu_skin = True
        self._m_rest_joints = ctx.texture((4, 1), 4, np.identity(4, "f4").tobytes(), dtype="f4")  # a skin not posed yet
        self.m_state = SViewState()
        self._m_lights = (0, b"", b"", b"")
        self._m_game_drew = False  # the game's pass drew the frame before (its ground, effects and decals stand)
        self.m_game_pass = make_pass(self)
        self.m_cloths = CClothPlayer()

    # loading
    def show(self, scene: CScene) -> None:
        """Draw this scene from now on; its files start loading."""
        self.release_scene()
        self.m_scene, self.m_box, self._m_game_drew = scene, None, False
        self._m_shadow_taken = None
        self.m_failed = {}  # what this scene fails to load: the textures that failed before are tried again
        self.m_textures = {k: v for k, v in self.m_textures.items() if v is not None}
        self.m_by_node = {part.node: (k, part) for k, part in enumerate(scene.m_parts)}
        self.m_clock_parts = any(motion.on_the_clock(part.motion) for part in scene.m_parts)  # the view ticks with them
        self.m_longest_fade = max((t[2] for thing in (*scene.m_parts, *scene.m_effects, *scene.m_lights, *scene.m_decals)
                                 for t in thing.times), default=0.0)  # fmt: skip
        self._m_rigs: dict[str, skinning.CRig | None] = {}
        self._m_frame: dict = {}
        self.m_cloths.clear()
        for k, part in enumerate(scene.m_parts):
            self._ask(f"mesh:{k}", lambda part=part: self._mesh_of(part))
        for e in scene.m_effects:
            self._ask("fx:" + e.file, lambda f=e.file: self.m_files.effect(f))

    def _ask(self, key: str, work):
        found = self.m_loader.get(key, work)
        if isinstance(found, Exception):
            self.say(tr("viewer.name_kind_error", name=key.split(":", 1)[-1], kind=type(found).__name__, error=found))
            return None
        return found

    def say(self, text: str) -> None:
        """Add a note for the view's message line (once)."""
        if text not in self.m_messages:
            self.m_messages.append(text)

    def _mesh_of(self, part: SPart):
        return banner_arrays(part.banner) if part.banner is not None else self.m_files.mesh(part.mesh)

    def busy(self) -> int:
        """Files still loading, and the game's shaders still compiling while their look is shown."""
        return self.m_loader.pending() + (self.m_game_pass.busy() if self.m_state.look == SHADERS else 0)

    def shown_parts_in(self, state: SViewState) -> bool:
        """Whether every part ``state`` shows has its mesh in, or failed to read; a part a condition bit hides (a
        fountain's water, the lobster farm's) is never read, so it is not waited for."""
        for k, part in enumerate(self.m_scene.m_parts if self.m_scene else []):
            if k in self.m_items or not state.shows(part):
                continue
            future = self.m_loader.m_futures.get(f"mesh:{k}")
            if future is None or not future.done() or not isinstance(future.result(), Exception):
                return False
        return True

    def changed(self) -> bool:
        """Something loaded since the last call (the window draws again)."""
        if self.m_loader.m_changed.is_set():
            self.m_loader.m_changed.clear()
            return True
        return False

    def _texture(self, path: str, srgb: bool, missing: bool = False):
        """The GPU texture of a file; None while it loads. One that cannot be read (not there, a format the decoders
        do not take) is kept in ``failed`` and, with ``missing`` (a colour map), drawn as the missing-file checker."""
        if not path:
            return None
        most = getattr(self.m_state.quality, "max_texture", 0) or 0  # the rendering quality's largest map (117, 1800)
        key = ("s:" if srgb else "l:") + (f"{most}:" if most else "") + path
        if key in self.m_textures:
            return self.m_textures[key] if self.m_textures[key] is not None or not missing else self._missing_texture()
        job = f"tex:{most}:{path}"
        got = self._ask(job, lambda: self.m_files.texture(path, most))
        if got is None:
            future = self.m_loader.m_futures.get(job)
            if future is not None and future.done():
                self.m_textures[key] = None
                error = future.result()  # the loader hands the exception back as the result
                self.m_failed[path] = (
                    f"{type(error).__name__}: {error}" if isinstance(error, Exception) else "does not read"
                )
                return self._missing_texture() if missing else None
            return None
        width, height, comps, raw = got
        texture = self.m_ctx.texture(
            (width, height), comps, raw, internal_format=0x8C43 if srgb else None
        )  # SRGB8_ALPHA8
        texture.build_mipmaps()
        texture.anisotropy = 8.0
        self.m_textures[key] = texture
        return texture

    def _missing_texture(self):
        return viewcolors.missing_texture(self.m_ctx, self.m_textures)

    def _items(self, k: int, part: SPart) -> list | None:
        """The GPU side of a part, made once its mesh is in: per mesh and submesh group a buffer pair and its VAOs."""
        if k in self.m_items:
            return self.m_items[k]
        mesh = self._ask(f"mesh:{k}", lambda: self._mesh_of(part))
        if mesh is None:
            return None
        out = []
        if part.banner is not None:
            verts, indices = mesh[0]
            out.append(self._item("leaves", verts, indices, True))
        else:
            for i, mesh_part in enumerate(mesh.meshes):
                for key, indices in mesh_part.groups.items():
                    if len(indices) and not part.material(key).hidden:
                        skin = (mesh_part, mesh) if mesh_part.skinned else (None, None)
                        out.append(self._item(key, mesh_part.vertices, indices, False, *skin))
                        out[-1]["data"], out[-1]["mesh"] = mesh_part, i
        self.m_items[k] = out
        self._grow_box(part, mesh)
        return out

    def _item(self, key, verts: np.ndarray, indices: np.ndarray, banner: bool, skinned=None, file=None) -> dict:
        """One mesh group on the GPU: its buffers and a VAO for the main and the shadow pass; a skinned one keeps its
        mesh and file so ``_pose`` can move it."""
        ctx = self.m_ctx
        is_gpu_skinned = skinned is not None and self.m_gpu_skin
        if is_gpu_skinned:  # the joints and weights ride along, the shader poses
            verts = np.concatenate([verts, skinned.joints.astype("f4"), skinned.weights.astype("f4")], axis=1)
        vertex_buffer = ctx.buffer(
            np.ascontiguousarray(verts, "f4").tobytes(), dynamic=skinned is not None and not is_gpu_skinned
        )
        index_buffer = ctx.buffer(np.ascontiguousarray(indices, "u4").tobytes())
        buffer_format = (
            ("3f 3f 2f 2f", "pos", "normal", "uv", "corner") if banner else ("3f 3f 2f", "pos", "normal", "uv")
        )
        kind = "banner" if banner else "model"
        programs = (("main", kind), ("depth", "depth_" + kind)) + ((("lit", "lit"),) if not banner else ())
        if is_gpu_skinned:
            buffer_format = ("3f 3f 2f 4f 4f", "pos", "normal", "uv", "joints", "weights")
            programs = (("main", "skin_model"), ("depth", "skin_depth"), ("lit", "skin_lit"))
        vaos = {
            name: ctx.vertex_array(
                self.m_prog[prog], [(vertex_buffer, *buffer_format), (self.m_inst, "16f/i", "inst")], index_buffer=index_buffer, index_element_size=4,
                skip_errors=True,
            )
            for name, prog in programs
        }  # fmt: skip
        tris = None if banner else np.asarray(indices, np.int64)[: len(indices) // 3 * 3].reshape(-1, 3)
        return {"key": key, "vbo": vertex_buffer, "ibo": index_buffer, "vaos": vaos, "skinned": skinned, "file": file, "pose": UNPOSED, "idx": indices,
                "verts": None if banner else verts[:, :8], "tris": tris, "posed": None, "gpu": is_gpu_skinned,
                "joints": None, "matrices": None}  # fmt: skip

    def _grow_box(self, part: SPart, mesh) -> None:
        found = part_bounds(part, mesh, part.matrix)
        if found is not None:
            min_bounds, max_bounds = found
            self.m_box = (
                (min_bounds, max_bounds)
                if self.m_box is None
                else (np.minimum(self.m_box[0], min_bounds), np.maximum(self.m_box[1], max_bounds))
            )

    # frames
    def _frame_targets(self, width_px: int, height_px: int):
        if self._m_targets is None or self._m_targets["size"] != (width_px, height_px):
            self._release_targets()
            ctx = self.m_ctx
            samples = min(4, ctx.max_samples)
            color = ctx.renderbuffer((width_px, height_px), 4, samples=samples, dtype="f2")
            depth = ctx.depth_renderbuffer((width_px, height_px), samples=samples)
            resolved = ctx.texture((width_px, height_px), 4, dtype="f2")
            self._m_targets = {"size": (width_px, height_px), "color": color, "depth": depth, "resolved": resolved,
                             "msaa": ctx.framebuffer([color], depth), "resolve": ctx.framebuffer([resolved])}  # fmt: skip
        return self._m_targets

    def render(self, fbo, size_px: tuple[int, int], camera: COrbitCamera, state: SViewState) -> None:
        """One frame of the scene into ``fbo`` (``size_px`` pixels)."""
        ctx = self.m_ctx
        width, height = max(1, size_px[0]), max(1, size_px[1])
        light_values = self._light(state)
        targets = self._frame_targets(width, height)
        view, eye = camera.view(), camera.eye()
        view_proj = camera.proj(width / height) @ view
        self._m_frame, self._m_lit, self.m_state = {}, {}, state
        self.m_cloths.next_frame(MIRROR[:3, :3] @ np.asarray(eye, float), camera.m_fov_rad)  # the eye in model space
        self._m_motion = self._motion_env(state, view, light_values)
        drawn = self._drawn(state)
        self._m_lights = self._light_set(state)
        shadow = self._shadow_pass(drawn, light_values) if state.shadows and self.m_box is not None else None
        frame = _SFrame(view_proj, view, light_values, eye, shadow)
        targets["msaa"].use()
        ctx.viewport = (0, 0, width, height)
        targets["msaa"].clear(0, 0, 0, 1, depth=1.0)
        self._sky(view_proj, light_values, eye)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.wireframe = state.wireframe
        game_ground = state.look == SHADERS and bool(self._by_game("m_ground", None))  # as of the frame before
        if state.ground and self.m_box is not None and not game_ground:  # else in the game's G-buffer (gameground)
            self._draw_ground(frame)
        see_through = (WATER_SHADER, FOAM_SHADER) + ((DECAL_SHADER,) if state.look == SHADERS else ())
        blended = [d for d in drawn if d[0].material(d[1]["key"]).shader in see_through]
        opaque = [d for d in drawn if d[0].material(d[1]["key"]).shader not in see_through]
        if state.look == SHADERS:  # water too, unless switched off (frame-117-viewer.md 7)
            water = [d for d in blended if self._is_water(d)] if state.switches.get("water", True) else []
            rest = self._game_shaders(opaque + water, targets["msaa"], (width, height), camera, light_values)
            opaque = [d for d in rest if not self._is_water(d)]
            blended = [d for d in blended if not any(d is x for x in water)] + [d for d in rest if self._is_water(d)]
        streets = [d for d in opaque if d[0].material(d[1]["key"]).shader == STREET_SHADER]
        self._draw_decals(state, frame, decalmesh.BEFORE_STREETS)  # the ground stage's order (decalmesh)
        for part, item, place in streets:
            self._draw(part, item, place, frame)
        self._draw_decals(state, frame, decalmesh.AFTER_STREETS)
        for part, item, place in opaque:
            if not any(part is s[0] and item is s[1] for s in streets):
                self._draw(part, item, place, frame)
        self._draw_decals(state, frame, decalmesh.VEHICLE)
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        for part, item, place in blended:
            self._draw(part, item, place, replace(frame, shadow=None))
        ctx.wireframe = False
        if state.particles:  # tested against the depth, never written (moderngl keeps the mask per framebuffer)
            targets["msaa"].depth_mask = False
            self._draw_particles(state, view, view_proj, eye)
            targets["msaa"].depth_mask = True
        ctx.disable(ctx.BLEND)
        self._finish(targets, fbo, (width, height), light_values)
        if state.selected is not None:
            self._draw_selection(state, view_proj, fbo, (width, height))

    def _game_shaders(self, opaque: list, target, size_px, camera, light_values: dict) -> list:
        """The groups the game's own shaders draw, drawn (``gamepass``); what is left for our plain look: all of them
        while the shaders compile or when the pass cannot draw, a group that failed, a kind the pass leaves."""
        self._m_game_drew = False
        why = self.m_game_pass.why_not()
        if why:
            self.say(why)
            return opaque
        day = NIGHT if self.m_state.night else 1.0
        harmonics = [
            tuple(channel * day for channel in rgb) for rgb in light_values.get("sh", [(0.0, 0.0, 0.0)] * 9)
        ]  # a 2070 pass has its own sky
        sun = tuple(channel * day for channel in light_values["sunColor"])
        light = SLight(
            tuple(light_values["sunDir"]),
            sun,
            tuple(channel * light_values.get("shFactor", 1.0) * 0.886 for channel in harmonics[0]),
        )
        sky = (
            SSky(
                tuple(light_values["sunDir"]),
                sun,
                tuple(harmonics),
                light_values.get("shFactor", 1.0),
                self.m_state.night,
            )
            if "sh" in light_values
            else None
        )
        self.m_game_pass.m_switches = nativelook.with_quality(self.m_state.switches, self.m_state.quality)
        self.m_game_pass.m_quality = self.m_state.quality
        ground = ground_place(*self.m_box) if self.m_state.ground and self.m_box is not None else None
        rest = self.m_game_pass.draw(opaque, target, size_px, camera, light, sky, ground)
        if rest is None:
            self.say(tr("viewer.compiling_games_shaders_once_they"))
            return opaque
        self._m_game_drew = True
        return rest

    def _by_game(self, name: str, default):
        """What the game's pass says it drew itself (its ``ground``, ``drawn_effects``, ``drawn_decals``): only of a
        frame it drew. A pass that failed or waits keeps what it said last, and the view's own would stay away."""
        return getattr(self.m_game_pass, name, default) if self._m_game_drew else default

    @staticmethod
    def _is_water(drawn) -> bool:
        part, item, _place = drawn
        return part.material(item["key"]).shader == WATER_SHADER

    def _drawn(self, state: SViewState) -> list:
        """(part, item, place) of every part shown, its pose applied when it plays an animation; a part on a joint
        whose model is not in yet waits."""
        out = []
        for k, part in enumerate(self.m_scene.m_parts if self.m_scene else []):
            if not state.shows(part):
                continue
            items = self._items(k, part) or []
            place = self.place(part, state) if items else None
            if place is None:
                continue
            for item in items:
                if item["skinned"] is not None:
                    self._pose(part, item, state)
                elif part.cloth is not None:
                    self.m_cloths.pose(part, item, state, place, getattr(self, "_m_motion", None))
                out.append((part, item, place))
        return out

    def pick(self, size_px: tuple[int, int], camera: COrbitCamera, state: SViewState, x: float, y: float) -> str | None:
        """The node of the part under pixel (x, y) of a ``size_px`` view, as the parts stand at ``state``; None for none.
        Call it with the context current (skinned parts are posed on the way)."""
        width, height = max(1, size_px[0]), max(1, size_px[1])
        origin, direction = picking.ray(camera.proj(width / height) @ camera.view(), (width, height), x, y)
        self._m_frame = {}
        best: tuple[float, str] | None = None
        for part, item, place in self._drawn(state):
            if item.get("verts") is None or part.material(item["key"]).hidden:
                continue
            world = MIRROR @ place
            verts = self._posed(item)
            found = picking.nearest(origin, direction, verts[:, :3] @ world[:3, :3].T + world[:3, 3], item["tris"])
            if found is not None and (best is None or found < best[0]):
                best = (found, part.node)
        return best[1] if best else None

    def _draw_decals(self, state: SViewState, frame: _SFrame, kind: int) -> None:
        """The config's decals of the pass ``kind`` (``decalmesh.pass_of``) on the ground under them, blended over
        what is drawn so far, without writing depth."""
        if self.m_scene is None or not self.m_scene.m_decals:
            return
        by_game = self._by_game("m_drawn_decals", set()) if state.look == SHADERS else set()
        ctx, target = self.m_ctx, self._m_targets["msaa"]
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        target.depth_mask = False
        for k, decal in enumerate(self.m_scene.m_decals):
            if decalmesh.pass_of(decal.kind, decal.on_streets) != kind:
                continue
            place = self.place(decal, state)
            if place is None or not state.shows(decal) or decal.node in by_game:
                continue
            if k not in self._m_decals:
                self._m_decals[k] = self._item("decal", *plainshading.decal_quad(decal.extents), False)
            part = SPart(decal.node, "", place, default=decal.material)
            self._draw(part, self._m_decals[k], place, frame, decal=True)
        target.depth_mask = True
        ctx.disable(ctx.BLEND)

    def _draw(self, part: SPart, item: dict, place, frame: _SFrame, decal: bool = False) -> None:
        material = part.material(item["key"])
        banner = material.shader == BANNER_SHADER
        water = material.shader == WATER_SHADER
        foam = (
            material.foam if material.shader == FOAM_SHADER else ()
        )  # a sheet's speeds and opacity: a plain read, not lit by us
        if (
            self.m_state.look == SHADERS and not banner and not water and not foam
        ):  # in the frame's light (plainshading)
            return self._draw_lit(material, item, place, frame, decal or material.shader == DECAL_SHADER)
        prog = self.m_prog["skin_model" if item.get("gpu") else "banner" if banner else "model"]
        self._bind_joints(prog, item)
        prog["viewProj"].write(gl(frame.view_proj))
        if "view" in self._uniform_names(prog):
            prog["view"].write(gl(frame.view))
        prog["part"].write(gl(place))
        self._put_light(prog, frame.lt, frame.eye, None if water or foam else frame.shadow)
        texture = self._texture(material.diffuse, not foam, missing=True)
        normal = self._texture(material.normal, False) if not banner else None
        prog["textured"].value = texture is not None
        (texture or self._m_dummy).use(0)
        prog["diffuseTex"].value = 0
        if "hasNormal" in self._uniform_names(prog):
            prog["hasNormal"].value = normal is not None
            (normal or self._m_dummy).use(2)
            prog["normalTex"].value = 2
        color = material.color if texture is not None or banner or water else (0.6, 0.6, 0.6)
        prog["tint"].value = tuple(float(min(c, 1.5)) for c in color)
        prog["emissive"].value = 0.0
        prog["kind"].value = 4 if foam else 1 if water or decal else 2 if banner else 0
        if foam:
            prog["foam"].value = tuple(float(v) for v in foam)
            prog["foamTime"].value = time.monotonic() % 3600.0
        if "opacity" in self._uniform_names(prog):
            prog["opacity"].value = material.opacity if decal else 0.8
        if "flip" in self._uniform_names(prog):
            prog["flip"].value = (0, 0, 0, 0)
        if "uvOffset" in self._uniform_names(prog):
            prog["uvOffset"].value = (0.0, 0.0)
        item["vaos"]["main"].render()

    def _draw_lit(self, material: SMaterial, item: dict, place, frame: _SFrame, blended: bool) -> None:
        """One group by our plain shading in the frame's light: its colour and normal maps, the sun, the sky and the
        config's lights."""
        prog = self.m_prog["skin_lit" if item.get("gpu") else "lit"]
        self._bind_joints(prog, item)
        prog["viewProj"].write(gl(frame.view_proj))
        prog["part"].write(gl(place))
        self._put_light(prog, frame.lt, frame.eye, frame.shadow)
        maps = (("diffuseTex", "textured", material.diffuse, True), ("normalTex", "hasNormal", material.normal, False))
        for unit, (sampler, flag, path, srgb) in enumerate(maps):
            texture = self._texture(path, srgb, missing=sampler == "diffuseTex")
            (texture or self._m_dummy).use(unit)
            prog[sampler].value = unit
            prog[flag].value = texture is not None
        prog["tint"].value = tuple(
            float(min(c, 1.5)) for c in (material.color if material.diffuse else (0.6, 0.6, 0.6))
        )
        prog["opacity"].value = material.opacity
        prog["dayLight"].value = NIGHT if self.m_state.night else 1.0
        prog["kind"].value = 3 if blended else 0
        prog["uvOffset"].value = (0.0, 0.0)
        count, pos, col, reach = self._m_lights
        prog["lightCount"].value = count
        if count:
            prog["lightPos"].write(pos)
            prog["lightColor"].write(col)
            prog["lightReach"].write(reach)
        item["vaos"].get("lit", item["vaos"]["main"]).render()

    def _shadow_pass(self, drawn: list, light_values: dict):
        ctx = self.m_ctx
        if self._m_shadow is None:
            depth_texture = ctx.depth_texture((SHADOW_SIZE, SHADOW_SIZE))
            depth_texture.compare_func = "<="
            depth_texture.repeat_x = depth_texture.repeat_y = False
            self._m_shadow = (depth_texture, ctx.framebuffer(depth_attachment=depth_texture))
        depth_texture, fbo = self._m_shadow
        min_bounds, max_bounds = self.m_box
        reach = max(float(np.linalg.norm(max_bounds - min_bounds)), 0.5)
        target = (min_bounds + max_bounds) / 2
        sun = np.asarray(light_values["sunDir"], "f4")
        lview = look_at(target + sun * reach * 2, target)
        m = ortho(reach, 0.01, reach * 4) @ lview
        taken = self._shadow_key(drawn, m)
        if taken is not None and taken == self._m_shadow_taken:
            return depth_texture, m  # the same sun, parts and poses: the camera alone moved
        fbo.use()
        ctx.viewport = (0, 0, SHADOW_SIZE, SHADOW_SIZE)
        fbo.clear(depth=1.0)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.BLEND)
        for part, item, place in drawn:
            material = part.material(item["key"])
            if material.shader in (WATER_SHADER, FOAM_SHADER):
                continue
            prog = self.m_prog[
                "skin_depth"
                if item.get("gpu")
                else "depth_banner"
                if material.shader == BANNER_SHADER
                else "depth_model"
            ]
            self._bind_joints(prog, item)
            prog["viewProj"].write(gl(m))
            if "view" in self._uniform_names(prog):
                prog["view"].write(gl(lview))
            prog["part"].write(gl(place))
            diffuse = self._texture(material.diffuse, True)
            if "textured" in self._uniform_names(prog):
                prog["textured"].value = diffuse is not None
            (diffuse or self._m_dummy).use(0)
            item["vaos"]["depth"].render()
        self._m_shadow_taken = taken
        return depth_texture, m

    def _shadow_key(self, drawn: list, matrix: np.ndarray) -> tuple | None:
        """What the shadow map is a picture of: the sun's matrix, each drawn group with its place, pose and material,
        and how many maps are in (a cut-out map arriving changes it); None when a cloth is in the scene (it moves
        every frame)."""
        if any(part.cloth is not None for part, _item, _place in drawn):
            return None
        groups = tuple(
            (
                part.node,
                item["key"],
                place.tobytes(),
                item["pose"],
                part.material(item["key"]).shader,
                part.material(item["key"]).diffuse,
            )
            for part, item, place in drawn
        )
        return matrix.tobytes(), len(self.m_textures), groups

    def _draw_ground(self, frame: _SFrame) -> None:
        """A grey plane under the model at its lowest point, wide enough for its shadow."""
        min_bounds, max_bounds = self.m_box
        if self._m_ground is None:
            quad = np.array([[-1, 0, -1, 0, 1, 0, 0, 0], [1, 0, -1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 0, 1, 1],
                             [-1, 0, 1, 0, 1, 0, 0, 1]], "f4")  # fmt: skip
            self._m_ground = self._item("ground", quad, np.array([0, 1, 2, 0, 2, 3], "u4"), False)
        place = ground_place(min_bounds, max_bounds)
        ground = SPart("", "", place, default=SMaterial(color=(0.55, 0.55, 0.52)))
        self._draw(ground, self._m_ground, place, frame)

    def _sky(self, view_proj, light_values: dict, eye) -> None:
        self.m_ctx.disable(self.m_ctx.DEPTH_TEST)
        sky = self.m_prog["sky"]
        sky["invViewProj"].write(gl(np.linalg.inv(view_proj)))
        mode, zenith, horizon, fall = light_values["sky"]
        sky["skyMode"].value = mode
        sky["zenith"].value, sky["horizon"].value, sky["fallOff"].value = zenith, horizon, fall
        sky["below"].value = light_values.get("skyBelow", horizon)
        self._put_light(sky, light_values, eye, None)
        self.m_fullscreen["sky"].render(vertices=3)

    def _finish(self, targets: dict, fbo, size_px: tuple[int, int], light_values: dict) -> None:
        ctx = self.m_ctx
        ctx.copy_framebuffer(targets["resolve"], targets["msaa"])
        source, finish = targets["resolved"], light_values["finish"]
        if self.m_state.look == SHADERS and hasattr(self.m_game_pass, "finish"):  # the game's grade and tone map
            graded = self.m_game_pass.finish(source, size_px, self._lut())
            source, finish = (
                (graded, getattr(self.m_game_pass, "FINISH", 3)) if graded is not None else (source, finish)
            )
        fbo.use()
        ctx.viewport = (0, 0, *size_px)
        ctx.disable(ctx.DEPTH_TEST)
        final_program = self.m_prog["final"]
        source.use(0)
        final_program["frame"].value = 0
        final_program["finish"].value = finish
        final_program["exposure"].value = light_values["exposure"]
        if "mood" in final_program:
            final_program["mood"].value = LOOK2070["mood"]
        lut = self._lut() if light_values["finish"] == 1 else None
        (lut or self._m_dummy_3d).use(1)
        if "hasLut" in final_program:
            final_program["hasLut"].value = lut is not None
        if "lut" in final_program:
            final_program["lut"].value = 1
        self.m_fullscreen["final"].render(vertices=3)

    def _lut(self):
        """The game's grade cube (117's ambient names it), when the files are 117's."""
        if "lut" not in self.m_textures:
            from ..render import gamelook

            def read():
                return gamelook.read_lut(self.m_files.m_files.read(gamelook.lut_path(self.m_files.m_files)))

            got = self.m_loader.get("lut", read)
            if got is None:
                return None
            if isinstance(got, Exception):
                self.m_textures["lut"] = None
                return None
            size = got.shape[0]
            texture = self.m_ctx.texture3d((size, size, size), 3, np.ascontiguousarray(got, "f4").tobytes(), dtype="f4")
            texture.repeat_x = texture.repeat_y = texture.repeat_z = False
            self.m_textures["lut"] = texture
        return self.m_textures["lut"]

    def _draw_particles(self, state: SViewState, view, view_proj, eye) -> None:
        items = []
        by_game = self._by_game("m_drawn_effects", set()) if state.look == SHADERS else set()
        for effect in self.m_scene.m_effects if self.m_scene else []:
            if not state.shows(effect) or effect.file in by_game:  # by_game: drawn by the game's own particle shader
                continue
            effects = self._ask("fx:" + effect.file, lambda effect_file=effect.file: self.m_files.effect(effect_file))
            place = self.place(effect, state)
            if effects and place is not None:
                faded = effect.alpha * state.fade(effect)  # its time slices' fade after View > Night [I]
                begun_s = state.time - state.effect_seconds(effect)  # a started effect plays from its element's start
                items.append((effects, SPlaced(effect.file, place.astype("f4"), effect.tint, faded, begun_s=begun_s),
                              MIRROR_F4[None]))  # fmt: skip
        if not items:
            return
        groups = collect(items, state.time, eye)
        self.m_particles.draw(groups, lambda name: self._texture(name, False, missing=True), view, view_proj, eye)

    def _draw_selection(self, state: SViewState, view_proj, fbo, size_px) -> None:
        """The box around the selected node's parts, over the finished frame."""
        min_bounds, max_bounds = np.full(3, np.inf), np.full(3, -np.inf)
        for k, part in enumerate(self.m_scene.m_parts if self.m_scene else []):
            place = self.place(part, state) if under(part.node, (state.selected,)) and k in self.m_items else None
            found = part_bounds(part, self._ask(f"mesh:{k}", lambda: None), place) if place is not None else None
            if found is not None:
                min_bounds, max_bounds = np.minimum(min_bounds, found[0]), np.maximum(max_bounds, found[1])
        if not np.isfinite(min_bounds).all():
            return
        corners = np.array(
            [
                [x, y, z]
                for x in (min_bounds[0], max_bounds[0])
                for y in (min_bounds[1], max_bounds[1])
                for z in (min_bounds[2], max_bounds[2])
            ],
            "f4",
        )
        edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
        rows = np.array([[*corners[i], 1.0, 0.82, 0.25, 1.0] for e in edges for i in e], "f4")
        prog = self.m_prog["overlay"]
        prog["viewProj"].write(gl(view_proj))
        if "pointSize" in prog:
            prog["pointSize"].value = 1.0
        vertex_buffer = self.m_ctx.buffer(rows.tobytes())
        vertex_array = self.m_ctx.vertex_array(prog, [(vertex_buffer, "3f 4f", "pos", "rgba")], skip_errors=True)
        fbo.use()
        vertex_array.render(self.m_ctx.LINES)
        vertex_array.release()
        vertex_buffer.release()

    # cleanup
    @staticmethod
    def _release_item(item: dict) -> None:
        for vertex_array in item["vaos"].values():
            vertex_array.release()
        if item.get("joints") is not None:
            item["joints"].release()
        item["vbo"].release()
        item["ibo"].release()

    def release_scene(self) -> None:
        for items in [*self.m_items.values(), list(self._m_decals.values())]:
            for item in items:
                self._release_item(item)
        self.m_items, self._m_decals = {}, {}
        self.m_game_pass.release_scene()
        self.m_loader.close()
        self.m_loader = CLoader()

    def _release_targets(self) -> None:
        if self._m_targets is not None:
            for k in ("msaa", "resolve", "color", "depth", "resolved"):
                self._m_targets[k].release()
            self._m_targets = None

    def release(self) -> None:
        self.release_scene()
        self.m_game_pass.release()
        self.m_loader.close()
        self._release_targets()
        self.m_particles.release()
        for texture in self.m_textures.values():
            if texture is not None:
                texture.release()
        self.m_textures = {}
        if self._m_ground is not None:  # what the renderer made for itself: a view makes a new one per game opened
            self._release_item(self._m_ground)
        own = (self._m_dummy, self._m_dummy_3d, self._m_dummy_shadow, self._m_rest_joints, self.m_inst)
        for held in (*(self._m_shadow or ()), *own, *self.m_fullscreen.values(), *self.m_prog.values()):
            held.release()
        self._m_ground = self._m_shadow = None
        self.m_fullscreen, self.m_prog = {}, {}
