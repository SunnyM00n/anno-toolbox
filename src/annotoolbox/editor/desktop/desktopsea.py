"""What the island view's seas by an old game's own water shaders share (2070's ``desktopsea2070``, 1404's
``desktopsea1404``, 1701's ``desktopsea1701``): its shaders compiled once, its layer over the view's depth and the
copies it reads, the view's camera and sun in the game's axes, the view's scene mirrored, the failure said once.

``IslandSea(renderer)`` is the base: a sea says its games (``GAMES``), makes itself ready (``_ready``, by
``_compiled``) and records (``_record``: ``_begin``, its passes, ``_end``); ``draw(f, opts, targets, camera, size)`` is
the seam ``desktoprender`` calls (False: the view's own sea). docs/island-editor-view.md "2070's sea by the game's
shaders".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PySide6.QtGui import QRhiTexture

from ...infra.i18n import tr
from ...render import gameshader as gs
from ...render.camera import MIRROR_Y, oblique_near
from ...render.nativepass import Composite
from ..editsession import game_of
from .desktopgame import frame_planes

BACKENDS = ("D3D11", "D3D12")  # the FXC shaders' bytecode runs on both through QRhi
FLIP_Y = np.diag([1.0, -1.0, 1.0, 1.0])  # the view's offscreen pictures stand upside down on Direct3D
CLEAR_FS = """
#version 330
in vec2 vNdc; out vec4 color;
void main() { color = vec4(0.0); }
"""
# the view's linear light as the old games' maps hold the scene (the mirrored scene, 1404's refraction): gamma
# values, its coverage in alpha
GAMMA_FS = """
#version 330
uniform sampler2D gLit;
in vec2 vNdc; out vec4 color;
void main() {
    vec4 c = texelFetch(gLit, ivec2(gl_FragCoord.xy), 0);
    color = vec4(pow(clamp(c.rgb, 0.0, 1.0), vec3(1.0 / 2.2)), c.a);
}
"""
# a sea's layer in gamma values back into the view's light where it drew (its depth is in the view's already)
GAMMA_LAYER_FS = """
#version 330
uniform sampler2D gSea;
in vec2 vNdc; out vec4 color;
void main() {
    vec4 c = texelFetch(gSea, ivec2(gl_FragCoord.xy), 0);
    if (c.a <= 0.0) discard;
    color = vec4(pow(max(c.rgb, 0.0), vec3(2.2)), 1.0);
}
"""


# the view's own passes store their pictures as OpenGL does (``rhipass``: y up, depth 0.5 z + 0.5): its shadow map's
# texture coordinates from the sun's clip space, row vectors
MAP_TEXTURE = np.array([[0.5, 0, 0, 0], [0, 0.5, 0, 0], [0, 0, 1, 0], [0.5, 0.5, 0, 1]])


def by_folder(source: str, folder: str, names: tuple[str, str]) -> str:
    """The second of ``names`` for an island file whose path (``source``) runs through ``folder`` (a southern or an
    underwater island's), else the first: an island in the editor has no place on a world map to pick its set by."""
    return names[1] if f"/{folder}/" in source.replace("\\", "/").lower() else names[0]


def sun_direction(f) -> tuple[float, float, float]:
    """The view's way towards the sun this frame (``f.lt``) in the game's axes (x mirrored)."""
    e = f.lt["sunDir"]
    return -float(e[0]), float(e[1]), float(e[2])


def upload(ctx, pixels: np.ndarray):
    """RGBA bytes [z, x] as a texture of the view's with its mip levels."""
    tex = ctx.texture((pixels.shape[1], pixels.shape[0]), 4, pixels.tobytes())
    tex.build_mipmaps()
    return tex


@dataclass(frozen=True)
class ViewShadow:
    """The view's sun shadow map as a game's sea reads it: ``texture`` the game's world (row vectors) into the map's
    texture coordinates, ``depth`` the plane of the depth the map stores, ``map`` the map (``QRhiTexture``). Its
    casters are the view's models, the game's are its own **[E]**."""

    texture: np.ndarray
    depth: np.ndarray
    map: object


class Files:
    """An install's files as the shader readers ask for them (``read``, ``exists``, ``folder``)."""

    def __init__(self, tree) -> None:
        self.tree = tree
        root = Path(getattr(tree, "root", "."))
        self.folder = root.parent if root.name.lower() == "maindata" else root

    def read(self, path: str) -> bytes:
        return self.tree.read_bytes(path)

    def exists(self, path: str) -> bool:
        return self.tree.exists(path)

    def compiler(self, game: str):
        """The compiler build ``game`` used (``nativelook.GAMES``' ``library``), the install's own first."""
        from ...infra.fxc import Fxc
        from ...render import nativelook

        return Fxc.find(self.folder, library=nativelook.GAMES[game].library)

    def level0(self, path: str) -> np.ndarray:
        """A DDS file's first level as RGBA bytes [z, x] (in the background)."""
        from ...models import ddsdecode

        return np.ascontiguousarray(ddsdecode.DdsFile(self.read(path)).level(0))


class IslandSea:
    """The base of the island view's game seas (module docstring). ``SCALE``: the game's world units a view unit
    holds; ``MIRROR_SIDE``: the reflection map's side."""

    GAMES: tuple[str, ...] = ()
    SCALE = 1.0
    MIRROR_SIDE = 512
    MAP_JOB = ""  # the loader's job name of the map a sea builds from the island's heights (``_heights_map``)

    def __init__(self, renderer) -> None:
        self.r = renderer
        self.mirror: dict = {}  # the planar reflection's targets
        self.targets: dict = {}
        self.passes: dict = {}
        self.error = False  # said once: the view's own sea from then on
        self.info = None  # the heights' map: (the heights' version, its texture, its texels a side)
        self._asked = None  # its job in the background

    def _heights_map(self, heights: np.ndarray, build, point: bool = False):
        """The map ``build(heights, step)`` makes from the heights shown (RGBA bytes [z, x]), made again in the
        background when they change; the last one meanwhile (None before the first). A failed build is raised (the
        sea then says it once and the view draws its own)."""
        version = self.r._ground_version
        if self.info is None or self.info[0] != version:
            job = (self.MAP_JOB, version)
            if self._asked not in (None, job):  # heights edited again before it was done: that map is not wanted
                self.r.loader.forget(self._asked)
            self._asked = job
            got = self.r.loader.get(job, lambda: build(heights.copy(), self.r.session.step))
            if isinstance(got, Exception):
                raise got
            if isinstance(got, np.ndarray):
                self.r.loader.forget(job)
                if self.info is not None:
                    self.info[1].release()
                tex = self.r.ctx.texture((got.shape[1], got.shape[0]), 4, got.tobytes())
                if point:
                    tex.filter = (0x2600, 0x2600)  # GL_NEAREST
                tex.repeat_x = tex.repeat_y = False
                self.info = (version, tex, got.shape[1])
        return self.info

    def wanted(self, opts) -> bool:
        """Whether this sea draws the island's: an island of one of ``GAMES`` with its install, the water layer and
        the sea on, above water, on Direct3D."""
        r, s = self.r, self.r.session
        return (
            not self.error
            and game_of(s.island) in self.GAMES
            and getattr(r.editor, "tree2070", None) is not None
            and hasattr(r.ctx, "native_pass")
            and r.ctx.rhi.backendName() in BACKENDS
            and opts.layers.get("water", True)
            and s.island.get("sea", True)
            and not opts.underwater
        )

    def _compiled(self, job: str, compile_):
        """The result of ``compile_(tree, source)`` (the install's tree and the island file's path) run once in the
        background as the loader's ``job``; None while it runs and where it failed (said once)."""
        r = self.r
        got = r.loader.get(job, lambda: compile_(r.editor.tree2070, str(getattr(r.editor, "source", ""))))
        if isinstance(got, Exception):
            self.fail(got)
            return None
        return got

    def _ready(self) -> bool:
        raise NotImplementedError

    def _record(self, f, t: dict, camera, opts) -> bool:
        raise NotImplementedError

    def fail(self, exc: Exception) -> None:
        """Said once on the message line; the view's own sea from then on."""
        self.error = True
        self.r._message(tr("islands.games_sea_reason", reason=f"{type(exc).__name__}: {exc}"[:300]))

    def draw(self, f, opts, t: dict, camera, size: tuple[int, int]) -> bool:
        """The sea into the view's frame ``t`` (single-sampled: its depth is read) after its opaque parts; False when
        the view draws its own (while it compiles, while its inputs load, where it failed: said once)."""
        if not self.wanted(opts) or not self._ready():
            return False
        try:
            drew = self._record(f, t, camera, opts)
        except Exception as exc:  # recorded in the view's frame: a failure would end the view's every frame
            self.fail(exc)
            self.r.ctx.enable(self.r.ctx.DEPTH_TEST)
            return False
        self.r.animated |= drew and opts.particle_time is None  # the waves move with the view's clock
        return drew

    def _targets(self, t: dict) -> dict:
        """The sea's layer (over the view's depth) and the copies it reads, made again with the view's targets."""
        if self.targets.get("depth") is t["depth"]:
            return self.targets
        self._drop()
        ctx, size = self.r.ctx, t["size"]
        sea = ctx.native_texture(size, QRhiTexture.Format.RGBA16F)
        refraction = ctx.native_texture(size, QRhiTexture.Format.RGBA16F)
        depth = ctx.native_texture(size, QRhiTexture.Format.R32F)
        self.targets = {"depth": t["depth"], "sea": sea, "sea_fbo": ctx.framebuffer([sea], t["depth"]),
                        "clear_fbo": ctx.framebuffer([sea]), "refraction": refraction,
                        "refraction_fbo": ctx.framebuffer([refraction]), "read": depth,
                        "read_fbo": ctx.framebuffer([depth])}  # fmt: skip
        return self.targets

    def _begin(self, f, opts, t: dict) -> tuple[dict, object]:
        """A frame's start: the sea's targets (``_targets``), its layer cleared, the view's scene mirrored
        (``_reflection``); (the targets, the reflection map)."""
        own = self._targets(t)
        reflection = self._reflection(f, opts, t)
        self._pass("clear", CLEAR_FS).draw(own["clear_fbo"], {}, {}, depth_test=False)
        return own, reflection

    def _end(self, t: dict, fragment: str, drew: bool = True) -> None:
        """The sea's layer into the view's frame where it drew (``fragment``: the layer's light back into the view's),
        and the depth test on again for the view's passes after it (a composite leaves it off)."""
        if drew:
            self._pass("sea", fragment).draw(t["msaa"], {"gSea": self.targets["sea"]}, {}, depth_test=False)
        self.r.ctx.enable(self.r.ctx.DEPTH_TEST)

    def _camera(self, camera, size: tuple[int, int]) -> gs.Camera:
        """The view's camera in the game's axes (the island's x mirrored about its middle) and units (``SCALE``), as
        the view's offscreen pictures stand (upside down on Direct3D), with the view's depth planes."""
        return self._flipped(self._game_camera(camera, size))

    def _game_camera(self, camera, size: tuple[int, int]) -> gs.Camera:
        """``_camera`` as the game sees it: the picture upright."""
        s, k = float(self.r.session.size), self.SCALE
        eye, target = np.asarray(camera.eye(), float), np.asarray(camera.target, float)
        shift = np.array([s, 0.0, 0.0])
        near, far = frame_planes(camera)
        return gs.direct_camera(size, (eye - shift) * k, (target - shift) * k, camera.fov, near * k, far * k,
                                game_axes=True)  # fmt: skip

    def _flipped(self, cam: gs.Camera) -> gs.Camera:
        return replace(cam, proj=cam.proj @ FLIP_Y) if self.r.ctx.flip_offscreen else cam

    def _view_shadow(self, f) -> ViewShadow | None:
        """The view's own sun shadow of this frame as an old game's sea reads it, in the game's axes and units;
        None where the view casts none (shadows off)."""
        if f.shadow is None:
            return None
        tex, matrix = f.shadow
        k, s = self.SCALE, float(self.r.session.size)
        to_view = np.array([[-1 / k, 0, 0, 0], [0, 1 / k, 0, 0], [0, 0, 1 / k, 0], [s, 0, 0, 1]])
        light = to_view @ np.asarray(matrix, float).T  # row vectors: the game's world into the sun's clip space
        return ViewShadow(light @ MAP_TEXTURE, 0.5 * light[:, 2] + np.array([0.0, 0.0, 0.0, 0.5]), tex.native)

    def _pass(self, name: str, fragment: str) -> Composite:
        if name not in self.passes:
            self.passes[name] = Composite(self.r.ctx, fragment)
        return self.passes[name]

    def _reflection(self, f, opts, t: dict):
        """The view's terrain and opaque models mirrored in the water plane and clipped there, into the reflection
        map (``GAMMA_FS``: the games' maps hold the scene's colours in gamma values and its coverage in alpha; the
        view's lit colours stand in for theirs), with the main pass's instances in view."""
        r, ctx = self.r, self.r.ctx
        own = self._mirror_targets()
        view = np.asarray(f.view, float) @ MIRROR_Y
        proj = np.asarray(f.view_proj, float) @ np.linalg.inv(np.asarray(f.view, float))
        mirrored = oblique_near(proj, np.linalg.inv(view).T @ np.array([0.0, 1.0, 0.0, 0.0]))
        eye = np.asarray(f.eye, float) * (1.0, -1.0, 1.0)
        fr = replace(f, view=view, view_proj=mirrored @ view, eye=eye, planes=None)
        stats, r._lit = dict(r.stats), {}
        own["fbo"].use()
        ctx.viewport = (0, 0, self.MIRROR_SIDE, self.MIRROR_SIDE)
        own["fbo"].clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.CULL_FACE)
        r._draw_ground(fr, opts)
        for b, p, vaos in r.drawn_opaque:
            r._draw_part(b, p, vaos, fr)
        r.stats, r._lit = stats, {}  # the frame's own figures, and its light set again by the next part
        self._pass("gamma", GAMMA_FS).draw(own["gamma_fbo"], {"gLit": own["color"]}, {}, depth_test=False)
        t["msaa"].use()
        ctx.viewport = (0, 0, *t["size"])
        return own["gamma"].native

    def _mirror_targets(self) -> dict:
        if not self.mirror:
            ctx, side = self.r.ctx, (self.MIRROR_SIDE, self.MIRROR_SIDE)
            color, depth = ctx.texture(side, 4, dtype="f2"), ctx.depth_renderbuffer(side)
            gamma = ctx.native_texture(side, QRhiTexture.Format.RGBA8)
            self.mirror = {"color": color, "depth": depth, "fbo": ctx.framebuffer([color], depth), "gamma": gamma,
                           "gamma_fbo": ctx.framebuffer([gamma])}  # fmt: skip
        return self.mirror

    def _drop(self) -> None:
        for key in ("sea_fbo", "clear_fbo", "refraction_fbo", "read_fbo", "sea", "refraction", "read"):
            if key in self.targets:
                self.targets[key].release()
        self.targets = {}

    def release(self) -> None:
        self._drop()
        for key in ("fbo", "gamma_fbo", "color", "depth", "gamma"):
            if key in self.mirror:
                self.mirror[key].release()
        self.mirror = {}
        if self.info is not None:
            self.info[1].release()
            self.info = None
        for p in self.passes.values():
            p.release()
        self.passes = {}
