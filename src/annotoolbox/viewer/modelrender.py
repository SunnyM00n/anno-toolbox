"""The model view's renderer (no Qt): one ``modelscene.Scene`` by the game's own shaders (``gamepass``), with our
plain look as the fallback for what they do not draw (``plainshading``; or as a look of its own, the editor's), sun
shadows on a ground plane, skins posed on the GPU (``gpu_skin``), parts that spin or hang on joints (``modelposes``),
the effects, the selection, ``pick``.

``ModelRenderer(ctx, files)`` is the entry: ``show(scene)``, then ``render(fbo, size, camera, state)`` per frame;
files load in the background (``busy``). ``scripts/editor_shots.py viewer`` renders it offscreen. What differs from
the game: docs/tool-suite.md "The viewer", "Drawn as the game draws".
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field, replace

import numpy as np

from ..infra.i18n import tr
from ..models import skinning
from ..render import decalmesh, nativelook, picking, viewcolors
from ..render import shaders as S
from ..render.camera import MIRROR, OrbitCamera, gl, look_at, ortho, perspective
from ..render.gameframe import Light
from ..render.gamelight import Sky
from ..render.light import LOOK2070
from ..render.loader import Loader
from ..render.modellibrary import banner_arrays
from ..render.particledraw import ParticleDrawer, Placed, collect
from . import motion, plainshading
from .clothplay import ClothPlayer
from .gamepass import make_pass
from .modelfiles import ModelFiles
from .modellights import NIGHT, SHADERS, ModelLights, under
from .modelposes import ModelPoses
from .modelscene import BANNER_SHADER, WATER_SHADER, Material, Part, Scene
from .states import States

SHADOW_SIZE = 2048
DECAL_SHADER = 6  # a decal laid as a mesh: blended over what is below in the game's-shaders look
STREET_SHADER = 19  # a street model: between the decals before and after the streets (decalmesh.pass_of)
# a skin's pose before its first (``_pose``): unlike any pose, so the bind pose (key None) is posed too and the game's
# shaders get a matrix for every joint (with one, a skin's other pieces read no matrix: tool-suite.md 21, round 16)
UNPOSED = ("unposed",)
MIRROR_F4 = MIRROR.astype("f4")  # as the instance buffer and the effects take it


@dataclass
class ViewState:
    """What a frame shows: the look, shadows, the ground, wireframe, effects, the time (seconds into the sequence or
    animation), the parts hidden and selected (config node paths: a node hides or selects every part below it)."""

    look: str = nativelook.PLAIN
    scheme: str = "light"
    shadows: bool = True
    ground: bool = True
    wireframe: bool = False
    particles: bool = True
    time: float = 0.0
    animation: dict = field(default_factory=dict)  # part node -> (file, the time it started at, scale, loops)
    hidden: frozenset = frozenset()
    selected: str | None = None
    night: bool = False  # the game's look at night: its lights shine
    switches: dict = field(default_factory=dict)  # the own-shader frame's parts switched off (nativelook.FEATURES)
    states: States = field(default_factory=States)  # the condition bits the sequence holds (viewer.states)
    cloth: bool = False  # cloths simulated (viewer.clothplay), else drawn at rest
    torn: bool = False  # the object damaged: a cloth that may tear lets its white pins go
    quality: object = None  # the rendering quality of 117 and 1800 (render.quality.Quality; None: the best)
    # the view's clock (monotonic seconds) this frame and when ``night`` last switched: the time slices' fade
    now: float = 0.0
    night_changed: float = -math.inf
    clock: tuple[int, int, int] | None = None  # the local hour, minute, second (the clock alignments); None: 12:00:00

    def shows(self, thing) -> bool:
        """Whether a part, effect, light or decal shows this frame: not hidden, its masks met, its time slices'
        fade above 0."""
        return not under(thing.node, self.hidden) and self.states.shown(thing.when) and self.fade(thing) > 0

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
        return tuple(float(c) * k for c in lamp.color_at(self.time))


def part_bounds(part, mesh, place) -> tuple[np.ndarray, np.ndarray] | None:
    """The box (lo, hi) of a part's mesh (a banner's arrays or a file's meshes) at ``place`` in the view's axes (x
    mirrored); None for a mesh without vertices."""
    verts = [mesh[0][0]] if part.banner is not None else [m.vertices for m in mesh.meshes]
    points = [v[:, :3] @ place[:3, :3].T + place[:3, 3] for v in verts if len(v)]
    if not points:
        return None
    p = np.concatenate(points) * (-1.0, 1.0, 1.0)
    return p.min(axis=0), p.max(axis=0)


def ground_place(lo, hi) -> np.ndarray:
    """Where the grey plane under a model of bounds ``lo``, ``hi`` (the view's axes) lies: at its lowest point (its
    own ground where it reaches y = 0), wide enough for its shadow; a part matrix (the instance mirrors it)."""
    reach = max(float(np.linalg.norm(hi - lo)), 0.5) * 1.5
    centre = (lo + hi) / 2
    place = np.diag([reach, 1.0, reach, 1.0]).astype("f4")
    floor = 0.0 if lo[1] <= 0.0 <= hi[1] else lo[1]
    place[:3, 3] = (-centre[0], floor - 0.002, centre[2])
    return place


@dataclass(frozen=True)
class _Frame:
    """What one frame's draws share: the camera's matrices and eye, the light, the sun's shadow map (None: none)."""

    view_proj: np.ndarray
    view: np.ndarray
    lt: dict
    eye: np.ndarray
    shadow: object = None


class ModelCamera(OrbitCamera):
    """The island view's orbit camera, for models of a few units: closer, and a far plane by the distance."""

    MIN_DISTANCE = 0.05
    PAN_NEAR = 0.0

    def planes(self) -> tuple[float, float]:
        """The near and far plane."""
        return max(0.005, self.distance / 200), self.distance * 40 + 10

    def proj(self, aspect: float) -> np.ndarray:
        return perspective(self.fov, aspect, *self.planes())

    def zoom(self, steps: float, towards=None) -> None:
        self.distance = min(500.0, max(self.MIN_DISTANCE, self.distance * 0.88**steps))

    def frame(self, lo: np.ndarray, hi: np.ndarray) -> None:
        """Look at a box (view space) from where it fills the view."""
        self.target = ((lo + hi) / 2).astype("f4")
        radius = max(float(np.linalg.norm(hi - lo)) / 2, 0.05)
        self.distance = radius / math.sin(self.fov / 2) * 1.1
        self.yaw, self.pitch = math.radians(-145), math.radians(25)


class ModelRenderer(ModelLights, ModelPoses):
    """Draws one scene at a time; ``files`` is the ``ModelFiles`` its parts load from."""

    def __init__(self, ctx, files: ModelFiles) -> None:
        self.ctx, self.files = ctx, files
        P = ctx.program
        self.prog = {
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
        self.fullscreen = {k: ctx.vertex_array(self.prog[k], []) for k in ("sky", "final")}
        self.inst = ctx.buffer(MIRROR_F4.T.tobytes())  # one instance, the mirror (MODEL_VS takes rows as GLSL does)
        self.loader = Loader()
        self.scene: Scene | None = None
        self.clock_parts = False
        self.longest_fade = 0.0  # the scene's longest time slice fade (seconds): the view draws again while it runs
        self.by_node: dict[str, tuple[int, Part]] = {}
        self._rigs: dict = {}
        self._frame: dict = {}  # this frame's places and joints, by part node
        self.items: dict[int, list] = {}  # part index -> [(group key, vbo, ibo, count, vaos)]
        self.textures: dict[str, object] = {}
        self.failed: dict[str, str] = {}  # a texture file that did not load -> why (the Checks panel lists it)
        self.messages: list[str] = []
        self.box: tuple[np.ndarray, np.ndarray] | None = None
        self.particles = ParticleDrawer(ctx)
        self._dummy = ctx.texture((1, 1), 4, bytes([128, 128, 128, 255]))
        self._dummy_3d = ctx.texture3d((2, 2, 2), 3, np.zeros(24, "f4").tobytes(), dtype="f4")
        self._dummy_shadow = ctx.depth_texture((1, 1), np.ones(1, "f4").tobytes())
        self._dummy_shadow.compare_func = "<="
        self._targets = None
        self._shadow = None
        self._ground = None
        self._decals: dict[int, dict] = {}
        self.gpu_skin = True
        self._rest_joints = ctx.texture((4, 1), 4, np.identity(4, "f4").tobytes(), dtype="f4")  # a skin not posed yet
        self.state = ViewState()
        self._lights = (0, b"", b"", b"")
        self._lock = threading.Lock()
        self.game_pass = make_pass(self)
        self.cloths = ClothPlayer()

    # loading
    def show(self, scene: Scene) -> None:
        """Draw this scene from now on; its files start loading."""
        self.release_scene()
        self.scene, self.box = scene, None
        self.failed = {}  # what this scene fails to load: the textures that failed before are tried again
        self.textures = {k: v for k, v in self.textures.items() if v is not None}
        self.by_node = {part.node: (k, part) for k, part in enumerate(scene.parts)}
        self.clock_parts = any(motion.on_the_clock(part.motion) for part in scene.parts)  # the view ticks with them
        self.longest_fade = max((t[2] for thing in (*scene.parts, *scene.effects, *scene.lights, *scene.decals)
                                 for t in thing.times), default=0.0)  # fmt: skip
        self._rigs: dict[str, skinning.Rig | None] = {}
        self._frame: dict = {}
        self.cloths.clear()
        for k, part in enumerate(scene.parts):
            self._ask(f"mesh:{k}", lambda part=part: self._mesh_of(part))
        for e in scene.effects:
            self._ask("fx:" + e.file, lambda f=e.file: self.files.effect(f))

    def _ask(self, key: str, work):
        found = self.loader.get(key, work)
        if isinstance(found, Exception):
            self.say(tr("{name}: {kind}: {error}", name=key.split(":", 1)[-1], kind=type(found).__name__, error=found))
            return None
        return found

    def say(self, text: str) -> None:
        """Add a note for the view's message line (once)."""
        if text not in self.messages:
            self.messages.append(text)

    def _mesh_of(self, part: Part):
        return banner_arrays(part.banner) if part.banner is not None else self.files.mesh(part.mesh)

    def busy(self) -> int:
        """Files still loading, and the game's shaders still compiling while their look is shown."""
        return self.loader.pending() + (self.game_pass.busy() if self.state.look == SHADERS else 0)

    def shown_parts_in(self, state: ViewState) -> bool:
        """Whether every part ``state`` shows has its mesh in, or failed to read; a part a condition bit hides (a
        fountain's water, the lobster farm's) is never read, so it is not waited for."""
        for k, part in enumerate(self.scene.parts if self.scene else []):
            if k in self.items or not state.shows(part):
                continue
            future = self.loader.futures.get(f"mesh:{k}")
            if future is None or not future.done() or not isinstance(future.result(), Exception):
                return False
        return True

    def changed(self) -> bool:
        """Something loaded since the last call (the window draws again)."""
        if self.loader.changed.is_set():
            self.loader.changed.clear()
            return True
        return False

    def _texture(self, path: str, srgb: bool, missing: bool = False):
        """The GPU texture of a file; None while it loads. One that cannot be read (not there, a format the decoders
        do not take) is kept in ``failed`` and, with ``missing`` (a colour map), drawn as the missing-file checker."""
        if not path:
            return None
        most = getattr(self.state.quality, "max_texture", 0) or 0  # the rendering quality's largest map (117, 1800)
        key = ("s:" if srgb else "l:") + (f"{most}:" if most else "") + path
        if key in self.textures:
            return self.textures[key] if self.textures[key] is not None or not missing else self._missing_texture()
        job = f"tex:{most}:{path}"
        got = self._ask(job, lambda: self.files.texture(path, most))
        if got is None:
            future = self.loader.futures.get(job)
            if future is not None and future.done():
                self.textures[key] = None
                exc = future.result()  # the loader hands the exception back as the result
                self.failed[path] = f"{type(exc).__name__}: {exc}" if isinstance(exc, Exception) else "does not read"
                return self._missing_texture() if missing else None
            return None
        w, h, comps, raw = got
        tex = self.ctx.texture((w, h), comps, raw, internal_format=0x8C43 if srgb else None)  # SRGB8_ALPHA8
        tex.build_mipmaps()
        tex.anisotropy = 8.0
        self.textures[key] = tex
        return tex

    def _missing_texture(self):
        return viewcolors.missing_texture(self.ctx, self.textures)

    def _items(self, k: int, part: Part) -> list | None:
        """The GPU side of a part, made once its mesh is in: per mesh and submesh group a buffer pair and its VAOs."""
        if k in self.items:
            return self.items[k]
        mesh = self._ask(f"mesh:{k}", lambda: self._mesh_of(part))
        if mesh is None:
            return None
        out = []
        if part.banner is not None:
            verts, idx = mesh[0]
            out.append(self._item("leaves", verts, idx, True))
        else:
            for i, m in enumerate(mesh.meshes):
                for key, idx in m.groups.items():
                    if len(idx) and not part.material(key).hidden:
                        skin = (m, mesh) if m.skinned else (None, None)
                        out.append(self._item(key, m.vertices, idx, False, *skin))
                        out[-1]["data"], out[-1]["mesh"] = m, i
        self.items[k] = out
        self._grow_box(part, mesh)
        return out

    def _item(self, key, verts: np.ndarray, idx: np.ndarray, banner: bool, skinned=None, file=None) -> dict:
        """One mesh group on the GPU: its buffers and a VAO for the main and the shadow pass; a skinned one keeps its
        mesh and file so ``_pose`` can move it."""
        ctx = self.ctx
        gpu = skinned is not None and self.gpu_skin
        if gpu:  # the joints and weights ride along, the shader poses
            verts = np.concatenate([verts, skinned.joints.astype("f4"), skinned.weights.astype("f4")], axis=1)
        vbo = ctx.buffer(np.ascontiguousarray(verts, "f4").tobytes(), dynamic=skinned is not None and not gpu)
        ibo = ctx.buffer(np.ascontiguousarray(idx, "u4").tobytes())
        fmt = ("3f 3f 2f 2f", "pos", "normal", "uv", "corner") if banner else ("3f 3f 2f", "pos", "normal", "uv")
        kind = "banner" if banner else "model"
        programs = (("main", kind), ("depth", "depth_" + kind)) + ((("lit", "lit"),) if not banner else ())
        if gpu:
            fmt = ("3f 3f 2f 4f 4f", "pos", "normal", "uv", "joints", "weights")
            programs = (("main", "skin_model"), ("depth", "skin_depth"), ("lit", "skin_lit"))
        vaos = {
            name: ctx.vertex_array(
                self.prog[prog], [(vbo, *fmt), (self.inst, "16f/i", "inst")], index_buffer=ibo, index_element_size=4,
                skip_errors=True,
            )
            for name, prog in programs
        }  # fmt: skip
        tris = None if banner else np.asarray(idx, np.int64)[: len(idx) // 3 * 3].reshape(-1, 3)
        return {"key": key, "vbo": vbo, "ibo": ibo, "vaos": vaos, "skinned": skinned, "file": file, "pose": UNPOSED, "idx": idx,
                "verts": None if banner else verts[:, :8], "tris": tris, "posed": None, "gpu": gpu,
                "joints": None, "matrices": None}  # fmt: skip

    def _grow_box(self, part: Part, mesh) -> None:
        found = part_bounds(part, mesh, part.matrix)
        if found is not None:
            lo, hi = found
            self.box = (lo, hi) if self.box is None else (np.minimum(self.box[0], lo), np.maximum(self.box[1], hi))

    # frames
    def _frame_targets(self, w: int, h: int):
        if self._targets is None or self._targets["size"] != (w, h):
            self._release_targets()
            ctx = self.ctx
            samples = min(4, ctx.max_samples)
            color = ctx.renderbuffer((w, h), 4, samples=samples, dtype="f2")
            depth = ctx.depth_renderbuffer((w, h), samples=samples)
            resolved = ctx.texture((w, h), 4, dtype="f2")
            self._targets = {"size": (w, h), "color": color, "depth": depth, "resolved": resolved,
                             "msaa": ctx.framebuffer([color], depth), "resolve": ctx.framebuffer([resolved])}  # fmt: skip
        return self._targets

    def render(self, fbo, size: tuple[int, int], camera: OrbitCamera, state: ViewState) -> None:
        """One frame of the scene into ``fbo`` (``size`` pixels)."""
        ctx = self.ctx
        w, h = max(1, size[0]), max(1, size[1])
        lt = self._light(state)
        t = self._frame_targets(w, h)
        view, eye = camera.view(), camera.eye()
        view_proj = camera.proj(w / h) @ view
        self._frame, self.state = {}, state
        self.cloths.next_frame(MIRROR[:3, :3] @ np.asarray(eye, float), camera.fov)  # the eye in model space
        self._motion = self._motion_env(state, view, lt)
        drawn = self._drawn(state)
        self._lights = self._light_set(state)
        shadow = self._shadow_pass(drawn, lt) if state.shadows and self.box is not None else None
        frame = _Frame(view_proj, view, lt, eye, shadow)
        t["msaa"].use()
        ctx.viewport = (0, 0, w, h)
        t["msaa"].clear(0, 0, 0, 1, depth=1.0)
        self._sky(view_proj, lt, eye)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.wireframe = state.wireframe
        game_ground = state.look == SHADERS and bool(getattr(self.game_pass, "ground", None))
        if state.ground and self.box is not None and not game_ground:  # else in the game's G-buffer (gameground)
            self._draw_ground(frame)
        see_through = (WATER_SHADER, DECAL_SHADER) if state.look == SHADERS else (WATER_SHADER,)
        blended = [d for d in drawn if d[0].material(d[1]["key"]).shader in see_through]
        opaque = [d for d in drawn if d[0].material(d[1]["key"]).shader not in see_through]
        if state.look == SHADERS:  # water too, unless switched off (frame-117-viewer.md 7)
            water = [d for d in blended if self._is_water(d)] if state.switches.get("water", True) else []
            rest = self._game_shaders(opaque + water, t["msaa"], (w, h), camera, lt)
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
            t["msaa"].depth_mask = False
            self._draw_particles(state, view, view_proj, eye)
            t["msaa"].depth_mask = True
        ctx.disable(ctx.BLEND)
        self._finish(t, fbo, (w, h), lt)
        if state.selected is not None:
            self._draw_selection(state, view_proj, fbo, (w, h))

    def _game_shaders(self, opaque: list, target, size, camera, lt: dict) -> list:
        """The groups the game's own shaders draw, drawn (``gamepass``); what is left for our plain look: all of them
        while the shaders compile or when the pass cannot draw, a group that failed, a kind the pass leaves."""
        why = self.game_pass.why_not()
        if why:
            self.say(why)
            return opaque
        day = NIGHT if self.state.night else 1.0
        sh = [tuple(c * day for c in rgb) for rgb in lt.get("sh", [(0.0, 0.0, 0.0)] * 9)]  # a 2070 pass has its own sky
        sun = tuple(c * day for c in lt["sunColor"])
        light = Light(tuple(lt["sunDir"]), sun, tuple(c * lt.get("shFactor", 1.0) * 0.886 for c in sh[0]))
        sky = (
            Sky(tuple(lt["sunDir"]), sun, tuple(sh), lt.get("shFactor", 1.0), self.state.night) if "sh" in lt else None
        )
        self.game_pass.switches = nativelook.with_quality(self.state.switches, self.state.quality)
        self.game_pass.quality = self.state.quality
        ground = ground_place(*self.box) if self.state.ground and self.box is not None else None
        rest = self.game_pass.draw(opaque, target, size, camera, light, sky, ground)
        if rest is None:
            self.say(tr("compiling the game's shaders (once: they are kept in the build cache)"))
            return opaque
        return rest

    @staticmethod
    def _is_water(drawn) -> bool:
        part, item, _place = drawn
        return part.material(item["key"]).shader == WATER_SHADER

    def _drawn(self, state: ViewState) -> list:
        """(part, item, place) of every part shown, its pose applied when it plays an animation; a part on a joint
        whose model is not in yet waits."""
        out = []
        for k, part in enumerate(self.scene.parts if self.scene else []):
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
                    self.cloths.pose(part, item, state, place, getattr(self, "_motion", None))
                out.append((part, item, place))
        return out

    def pick(self, size: tuple[int, int], camera: OrbitCamera, state: ViewState, x: float, y: float) -> str | None:
        """The node of the part under pixel (x, y) of a ``size`` view, as the parts stand at ``state``; None for none.
        Call it with the context current (skinned parts are posed on the way)."""
        w, h = max(1, size[0]), max(1, size[1])
        origin, direction = picking.ray(camera.proj(w / h) @ camera.view(), (w, h), x, y)
        self._frame = {}
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

    def _draw_decals(self, state: ViewState, frame: _Frame, kind: int) -> None:
        """The config's decals of the pass ``kind`` (``decalmesh.pass_of``) on the ground under them, blended over
        what is drawn so far, without writing depth."""
        if self.scene is None or not self.scene.decals:
            return
        by_game = getattr(self.game_pass, "drawn_decals", set()) if state.look == SHADERS else set()
        ctx, target = self.ctx, self._targets["msaa"]
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        target.depth_mask = False
        for k, decal in enumerate(self.scene.decals):
            if decalmesh.pass_of(decal.kind, decal.on_streets) != kind:
                continue
            place = self.place(decal, state)
            if place is None or not state.shows(decal) or decal.node in by_game:
                continue
            if k not in self._decals:
                self._decals[k] = self._item("decal", *plainshading.decal_quad(decal.extents), False)
            part = Part(decal.node, "", place, default=decal.material)
            self._draw(part, self._decals[k], place, frame, decal=True)
        target.depth_mask = True
        ctx.disable(ctx.BLEND)

    def _draw(self, part: Part, item: dict, place, frame: _Frame, decal: bool = False) -> None:
        mat = part.material(item["key"])
        banner = mat.shader == BANNER_SHADER
        water = mat.shader == WATER_SHADER
        if self.state.look == SHADERS and not banner and not water:  # in the frame's light (plainshading)
            return self._draw_lit(mat, item, place, frame, decal or mat.shader == DECAL_SHADER)
        prog = self.prog["skin_model" if item.get("gpu") else "banner" if banner else "model"]
        self._bind_joints(prog, item)
        prog["viewProj"].write(gl(frame.view_proj))
        if "view" in prog:
            prog["view"].write(gl(frame.view))
        prog["part"].write(gl(place))
        self._put_light(prog, frame.lt, frame.eye, None if water else frame.shadow)
        tex = self._texture(mat.diffuse, True, missing=True)
        nrm = self._texture(mat.normal, False) if not banner else None
        prog["textured"].value = tex is not None
        (tex or self._dummy).use(0)
        prog["diffuseTex"].value = 0
        if "hasNormal" in prog:
            prog["hasNormal"].value = nrm is not None
            (nrm or self._dummy).use(2)
            prog["normalTex"].value = 2
        color = mat.color if tex is not None or banner or water else (0.6, 0.6, 0.6)
        prog["tint"].value = tuple(float(min(c, 1.5)) for c in color)
        prog["emissive"].value = 0.0
        prog["kind"].value = 1 if water or decal else 2 if banner else 0
        if "opacity" in prog:
            prog["opacity"].value = mat.opacity if decal else 0.8
        if "flip" in prog:
            prog["flip"].value = (0, 0, 0, 0)
        if "uvOffset" in prog:
            prog["uvOffset"].value = (0.0, 0.0)
        item["vaos"]["main"].render()

    def _draw_lit(self, mat: Material, item: dict, place, frame: _Frame, blended: bool) -> None:
        """One group by our plain shading in the frame's light: its colour and normal maps, the sun, the sky and the
        config's lights."""
        prog = self.prog["skin_lit" if item.get("gpu") else "lit"]
        self._bind_joints(prog, item)
        prog["viewProj"].write(gl(frame.view_proj))
        prog["part"].write(gl(place))
        self._put_light(prog, frame.lt, frame.eye, frame.shadow)
        maps = (("diffuseTex", "textured", mat.diffuse, True), ("normalTex", "hasNormal", mat.normal, False))
        for unit, (sampler, flag, path, srgb) in enumerate(maps):
            tex = self._texture(path, srgb, missing=sampler == "diffuseTex")
            (tex or self._dummy).use(unit)
            prog[sampler].value = unit
            prog[flag].value = tex is not None
        prog["tint"].value = tuple(float(min(c, 1.5)) for c in (mat.color if mat.diffuse else (0.6, 0.6, 0.6)))
        prog["opacity"].value = mat.opacity
        prog["dayLight"].value = NIGHT if self.state.night else 1.0
        prog["kind"].value = 3 if blended else 0
        prog["uvOffset"].value = (0.0, 0.0)
        count, pos, col, reach = self._lights
        prog["lightCount"].value = count
        if count:
            prog["lightPos"].write(pos)
            prog["lightColor"].write(col)
            prog["lightReach"].write(reach)
        item["vaos"].get("lit", item["vaos"]["main"]).render()

    def _shadow_pass(self, drawn: list, lt: dict):
        ctx = self.ctx
        if self._shadow is None:
            tex = ctx.depth_texture((SHADOW_SIZE, SHADOW_SIZE))
            tex.compare_func = "<="
            tex.repeat_x = tex.repeat_y = False
            self._shadow = (tex, ctx.framebuffer(depth_attachment=tex))
        tex, fbo = self._shadow
        lo, hi = self.box
        reach = max(float(np.linalg.norm(hi - lo)), 0.5)
        target = (lo + hi) / 2
        sun = np.asarray(lt["sunDir"], "f4")
        lview = look_at(target + sun * reach * 2, target)
        m = ortho(reach, 0.01, reach * 4) @ lview
        fbo.use()
        ctx.viewport = (0, 0, SHADOW_SIZE, SHADOW_SIZE)
        fbo.clear(depth=1.0)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.BLEND)
        for part, item, place in drawn:
            mat = part.material(item["key"])
            if mat.shader == WATER_SHADER:
                continue
            prog = self.prog[
                "skin_depth" if item.get("gpu") else "depth_banner" if mat.shader == BANNER_SHADER else "depth_model"
            ]
            self._bind_joints(prog, item)
            prog["viewProj"].write(gl(m))
            if "view" in prog:
                prog["view"].write(gl(lview))
            prog["part"].write(gl(place))
            diffuse = self._texture(mat.diffuse, True)
            if "textured" in prog:
                prog["textured"].value = diffuse is not None
            (diffuse or self._dummy).use(0)
            item["vaos"]["depth"].render()
        return tex, m

    def _draw_ground(self, frame: _Frame) -> None:
        """A grey plane under the model at its lowest point, wide enough for its shadow."""
        lo, hi = self.box
        if self._ground is None:
            quad = np.array([[-1, 0, -1, 0, 1, 0, 0, 0], [1, 0, -1, 0, 1, 0, 1, 0], [1, 0, 1, 0, 1, 0, 1, 1],
                             [-1, 0, 1, 0, 1, 0, 0, 1]], "f4")  # fmt: skip
            self._ground = self._item("ground", quad, np.array([0, 1, 2, 0, 2, 3], "u4"), False)
        place = ground_place(lo, hi)
        ground = Part("", "", place, default=Material(color=(0.55, 0.55, 0.52)))
        self._draw(ground, self._ground, place, frame)

    def _sky(self, view_proj, lt: dict, eye) -> None:
        self.ctx.disable(self.ctx.DEPTH_TEST)
        sky = self.prog["sky"]
        sky["invViewProj"].write(gl(np.linalg.inv(view_proj)))
        mode, zen, hor, fall = lt["sky"]
        sky["skyMode"].value = mode
        sky["zenith"].value, sky["horizon"].value, sky["fallOff"].value = zen, hor, fall
        sky["below"].value = lt.get("skyBelow", hor)
        self._put_light(sky, lt, eye, None)
        self.fullscreen["sky"].render(vertices=3)

    def _finish(self, t: dict, fbo, size: tuple[int, int], lt: dict) -> None:
        ctx = self.ctx
        ctx.copy_framebuffer(t["resolve"], t["msaa"])
        source, finish = t["resolved"], lt["finish"]
        if self.state.look == SHADERS and hasattr(self.game_pass, "finish"):  # the game's grade and tone map
            graded = self.game_pass.finish(source, size, self._lut())
            source, finish = (graded, getattr(self.game_pass, "FINISH", 3)) if graded is not None else (source, finish)
        fbo.use()
        ctx.viewport = (0, 0, *size)
        ctx.disable(ctx.DEPTH_TEST)
        fin = self.prog["final"]
        source.use(0)
        fin["frame"].value = 0
        fin["finish"].value = finish
        fin["exposure"].value = lt["exposure"]
        if "mood" in fin:
            fin["mood"].value = LOOK2070["mood"]
        lut = self._lut() if lt["finish"] == 1 else None
        (lut or self._dummy_3d).use(1)
        if "hasLut" in fin:
            fin["hasLut"].value = lut is not None
        if "lut" in fin:
            fin["lut"].value = 1
        self.fullscreen["final"].render(vertices=3)

    def _lut(self):
        """The game's grade cube (117's ambient names it), when the files are 117's."""
        if "lut" not in self.textures:
            from ..render import gamelook

            def read():
                return gamelook.read_lut(self.files.files.read(gamelook.lut_path(self.files.files)))

            got = self.loader.get("lut", read)
            if got is None:
                return None
            if isinstance(got, Exception):
                self.textures["lut"] = None
                return None
            n = got.shape[0]
            tex = self.ctx.texture3d((n, n, n), 3, np.ascontiguousarray(got, "f4").tobytes(), dtype="f4")
            tex.repeat_x = tex.repeat_y = tex.repeat_z = False
            self.textures["lut"] = tex
        return self.textures["lut"]

    def _draw_particles(self, state: ViewState, view, view_proj, eye) -> None:
        items = []
        by_game = getattr(self.game_pass, "drawn_effects", set()) if state.look == SHADERS else set()
        for e in self.scene.effects if self.scene else []:
            if not state.shows(e) or e.file in by_game:  # by_game: drawn by the game's own particle shader
                continue
            effects = self._ask("fx:" + e.file, lambda f=e.file: self.files.effect(f))
            place = self.place(e, state)
            if effects and place is not None:
                faded = e.alpha * state.fade(e)  # its time slices' fade after View > Night [I]
                items.append((effects, Placed(e.file, place.astype("f4"), e.tint, faded), MIRROR_F4[None]))
        if not items:
            return
        groups = collect(items, state.time, eye)
        self.particles.draw(groups, lambda name: self._texture(name, False, missing=True), view, view_proj, eye)

    def _draw_selection(self, state: ViewState, view_proj, fbo, size) -> None:
        """The box around the selected node's parts, over the finished frame."""
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for k, part in enumerate(self.scene.parts if self.scene else []):
            place = self.place(part, state) if under(part.node, (state.selected,)) and k in self.items else None
            found = part_bounds(part, self._ask(f"mesh:{k}", lambda: None), place) if place is not None else None
            if found is not None:
                lo, hi = np.minimum(lo, found[0]), np.maximum(hi, found[1])
        if not np.isfinite(lo).all():
            return
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], "f4")
        edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
        rows = np.array([[*corners[i], 1.0, 0.82, 0.25, 1.0] for e in edges for i in e], "f4")
        prog = self.prog["overlay"]
        prog["viewProj"].write(gl(view_proj))
        if "pointSize" in prog:
            prog["pointSize"].value = 1.0
        vbo = self.ctx.buffer(rows.tobytes())
        vao = self.ctx.vertex_array(prog, [(vbo, "3f 4f", "pos", "rgba")], skip_errors=True)
        fbo.use()
        vao.render(self.ctx.LINES)
        vao.release()
        vbo.release()

    # cleanup
    def release_scene(self) -> None:
        for items in [*self.items.values(), list(self._decals.values())]:
            for item in items:
                for vao in item["vaos"].values():
                    vao.release()
                if item.get("joints") is not None:
                    item["joints"].release()
                item["vbo"].release()
                item["ibo"].release()
        self.items, self._decals = {}, {}
        self.game_pass.release_scene()
        self.loader.close()
        self.loader = Loader()

    def _release_targets(self) -> None:
        if self._targets is not None:
            for k in ("msaa", "resolve", "color", "depth", "resolved"):
                self._targets[k].release()
            self._targets = None

    def release(self) -> None:
        self.release_scene()
        self.game_pass.release()
        self.loader.close()
        self._release_targets()
        self.particles.release()
        for tex in self.textures.values():
            if tex is not None:
                tex.release()
        self.textures = {}
