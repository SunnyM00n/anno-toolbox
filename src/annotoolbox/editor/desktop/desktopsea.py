"""What the island view's seas by an old game's own water shaders share (2070's ``desktopsea2070``, 1404's
``desktopsea1404``, 1701's ``desktopsea1701``): its shaders compiled once, its layer over the view's depth and the
copies it reads, the view's camera and sun in the game's axes, the view's scene mirrored, the failure said once.

``CIslandSea(renderer)`` is the base: a sea says its games (``GAMES``), makes itself ready (``_ready``, by
``_compiled``) and records (``_record``: ``_begin``, its passes, ``_end``); ``draw(frame, options, targets, camera, size_px)`` is
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
from ...render.nativepass import CComposite
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


def sun_direction(frame) -> tuple[float, float, float]:
    """The view's way towards the sun this frame (``f.lt``) in the game's axes (x mirrored)."""
    direction = frame.lt["sunDir"]
    return -float(direction[0]), float(direction[1]), float(direction[2])


def upload(ctx, pixels: np.ndarray):
    """RGBA bytes [z, x] as a texture of the view's with its mip levels."""
    texture = ctx.texture((pixels.shape[1], pixels.shape[0]), 4, pixels.tobytes())
    texture.build_mipmaps()
    return texture


@dataclass(frozen=True)
class SViewShadow:
    """The view's sun shadow map as a game's sea reads it: ``texture`` the game's world (row vectors) into the map's
    texture coordinates, ``depth`` the plane of the depth the map stores, ``map`` the map (``QRhiTexture``). Its
    casters are the view's models, the game's are its own **[E]**."""

    texture: np.ndarray
    depth: np.ndarray
    map: object


class CFiles:
    """An install's files as the shader readers ask for them (``read``, ``exists``, ``folder``)."""

    def __init__(self, tree) -> None:
        self.m_tree = tree
        root = Path(getattr(tree, "m_root", "."))
        self.m_folder = root.parent if root.name.lower() == "maindata" else root

    def read(self, path: str) -> bytes:
        return self.m_tree.read_bytes(path)

    def exists(self, path: str) -> bool:
        return self.m_tree.exists(path)

    def compiler(self, game: str):
        """The compiler build ``game`` used (``nativelook.GAMES``' ``library``), the install's own first."""
        from ...infra.fxc import CFxc
        from ...render import nativelook

        return CFxc.find(self.m_folder, library=nativelook.GAMES[game].library)

    def level0(self, path: str) -> np.ndarray:
        """A DDS file's first level as RGBA bytes [z, x] (in the background)."""
        from ...models import ddsdecode

        return np.ascontiguousarray(ddsdecode.CDdsFile(self.read(path)).level(0))


class CIslandSea:
    """The base of the island view's game seas (module docstring). ``SCALE``: the game's world units a view unit
    holds; ``MIRROR_SIDE``: the reflection map's side."""

    GAMES: tuple[str, ...] = ()
    SCALE = 1.0
    MIRROR_SIDE = 512
    MAP_JOB = ""  # the loader's job name of the map a sea builds from the island's heights (``_heights_map``)

    def __init__(self, renderer) -> None:
        self.m_renderer = renderer
        self.m_mirror: dict = {}  # the planar reflection's targets
        self.m_empty_mirror: dict | None = None  # the targets (``m_mirror``) cleared while the reflections are off
        self.m_targets: dict = {}
        self.m_passes: dict = {}
        self.m_error = False  # said once: the view's own sea from then on
        self.m_heights_map = None  # the heights' map: (the heights' version, its texture, its texels a side)
        self._m_asked = None  # its job in the background

    def _heights_map(self, heights: np.ndarray, build, point: bool = False):
        """The map ``build(heights, step)`` makes from the heights shown (RGBA bytes [z, x]), made again in the
        background when they change; the last one meanwhile (None before the first). A failed build is raised (the
        sea then says it once and the view draws its own)."""
        version = self.m_renderer._m_ground_version
        if self.m_heights_map is None or self.m_heights_map[0] != version:
            job = (self.MAP_JOB, version)
            if self._m_asked not in (None, job):  # heights edited again before it was done: that map is not wanted
                self.m_renderer.m_loader.forget(self._m_asked)
            self._m_asked = job
            got = self.m_renderer.m_loader.get(
                job, lambda: build(heights.copy(), self.m_renderer.m_session.m_step_tiles)
            )
            if isinstance(got, Exception):
                raise got
            if isinstance(got, np.ndarray):
                self.m_renderer.m_loader.forget(job)
                if self.m_heights_map is not None:
                    self.m_heights_map[1].release()
                texture = self.m_renderer.m_ctx.texture((got.shape[1], got.shape[0]), 4, got.tobytes())
                if point:
                    texture.m_filter = (0x2600, 0x2600)  # GL_NEAREST
                texture.repeat_x = texture.repeat_y = False
                self.m_heights_map = (version, texture, got.shape[1])
        return self.m_heights_map

    def wanted(self, options) -> bool:
        """Whether this sea draws the island's: an island of one of ``GAMES`` with its install, the water layer and
        the sea on, above water, on Direct3D."""
        renderer, session = self.m_renderer, self.m_renderer.m_session
        return (
            not self.m_error
            and game_of(session.m_island) in self.GAMES
            and getattr(renderer.m_editor, "m_tree2070", None) is not None
            and hasattr(renderer.m_ctx, "native_pass")
            and renderer.m_ctx.m_rhi.backendName() in BACKENDS
            and options.layers.get("water", True)
            and session.m_island.get("sea", True)
            and not options.underwater
        )

    def _compiled(self, job: str, compile_):
        """The result of ``compile_(tree, source)`` (the install's tree and the island file's path) run once in the
        background as the loader's ``job``; None while it runs and where it failed (said once)."""
        renderer = self.m_renderer
        got = renderer.m_loader.get(
            job, lambda: compile_(renderer.m_editor.m_tree2070, str(getattr(renderer.m_editor, "m_source", "")))
        )
        if isinstance(got, Exception):
            self.fail(got)
            return None
        return got

    def _ready(self) -> bool:
        raise NotImplementedError

    def _record(self, frame, targets: dict, camera, options) -> bool:
        raise NotImplementedError

    def fail(self, exc: Exception) -> None:
        """Said once on the message line; the view's own sea from then on."""
        self.m_error = True
        self.m_renderer._message(tr("islands.games_sea_reason", reason=f"{type(exc).__name__}: {exc}"[:300]))

    def draw(self, frame, options, targets: dict, camera, size_px: tuple[int, int]) -> bool:
        """The sea into the view's frame ``targets`` (single-sampled: its depth is read) after its opaque parts; False when
        the view draws its own (while it compiles, while its inputs load, where it failed: said once)."""
        if not self.wanted(options) or not self._ready():
            return False
        try:
            drew = self._record(frame, targets, camera, options)
        except Exception as exc:  # recorded in the view's frame: a failure would end the view's every frame
            self.fail(exc)
            self.m_renderer.m_ctx.enable(self.m_renderer.m_ctx.DEPTH_TEST)
            return False
        self.m_renderer.m_animated |= drew and not options.is_clock_held  # the waves move with the view's clock
        return drew

    def _targets(self, targets: dict) -> dict:
        """The sea's layer (over the view's depth) and the copies it reads, made again with the view's targets."""
        if self.m_targets.get("depth") is targets["depth"]:
            return self.m_targets
        self._drop()
        ctx, size = self.m_renderer.m_ctx, targets["size"]
        sea = ctx.native_texture(size, QRhiTexture.Format.RGBA16F)
        refraction = ctx.native_texture(size, QRhiTexture.Format.RGBA16F)
        depth = ctx.native_texture(size, QRhiTexture.Format.R32F)
        self.m_targets = {"depth": targets["depth"], "sea": sea, "sea_fbo": ctx.framebuffer([sea], targets["depth"]),
                        "clear_fbo": ctx.framebuffer([sea]), "refraction": refraction,
                        "refraction_fbo": ctx.framebuffer([refraction]), "read": depth,
                        "read_fbo": ctx.framebuffer([depth])}  # fmt: skip
        return self.m_targets

    def _begin(self, frame, options, targets: dict) -> tuple[dict, object]:
        """A frame's start: the sea's targets (``_targets``), its layer cleared, the view's scene mirrored
        (``_reflection``); (the targets, the reflection map)."""
        own = self._targets(targets)
        reflection = self._reflection(frame, options, targets)
        self._pass("clear", CLEAR_FS).draw(own["clear_fbo"], {}, {}, depth_test=False)
        return own, reflection

    def _end(self, targets: dict, fragment: str, drew: bool = True) -> None:
        """The sea's layer into the view's frame where it drew (``fragment``: the layer's light back into the view's),
        and the depth test on again for the view's passes after it (a composite leaves it off)."""
        if drew:
            self._pass("sea", fragment).draw(targets["msaa"], {"gSea": self.m_targets["sea"]}, {}, depth_test=False)
        self.m_renderer.m_ctx.enable(self.m_renderer.m_ctx.DEPTH_TEST)

    def _camera(self, camera, size_px: tuple[int, int]) -> gs.SCamera:
        """The view's camera in the game's axes (the island's x mirrored about its middle) and units (``SCALE``), as
        the view's offscreen pictures stand (upside down on Direct3D), with the view's depth planes."""
        return self._flipped(self._game_camera(camera, size_px))

    def _game_camera(self, camera, size_px: tuple[int, int]) -> gs.SCamera:
        """``_camera`` as the game sees it: the picture upright."""
        island_size, scale = float(self.m_renderer.m_session.m_size_tiles), self.SCALE
        eye, target = np.asarray(camera.eye(), float), np.asarray(camera.m_target, float)
        shift = np.array([island_size, 0.0, 0.0])
        near, far = frame_planes(camera)
        return gs.direct_camera(size_px, (eye - shift) * scale, (target - shift) * scale, camera.m_fov_rad, near * scale, far * scale,
                                use_game_axes=True)  # fmt: skip

    def _flipped(self, camera: gs.SCamera) -> gs.SCamera:
        return (
            replace(camera, projection=camera.projection @ FLIP_Y) if self.m_renderer.m_ctx.m_flip_offscreen else camera
        )

    def _view_shadow(self, frame) -> SViewShadow | None:
        """The view's own sun shadow of this frame as an old game's sea reads it, in the game's axes and units;
        None where the view casts none (shadows off)."""
        if frame.shadow is None:
            return None
        texture, matrix = frame.shadow
        scale, island_size = self.SCALE, float(self.m_renderer.m_session.m_size_tiles)
        to_view = np.array([[-1 / scale, 0, 0, 0], [0, 1 / scale, 0, 0], [0, 0, 1 / scale, 0], [island_size, 0, 0, 1]])
        light = to_view @ np.asarray(matrix, float).T  # row vectors: the game's world into the sun's clip space
        return SViewShadow(light @ MAP_TEXTURE, 0.5 * light[:, 2] + np.array([0.0, 0.0, 0.0, 0.5]), texture.native)

    def _pass(self, name: str, fragment: str) -> CComposite:
        if name not in self.m_passes:
            self.m_passes[name] = CComposite(self.m_renderer.m_ctx, fragment)
        return self.m_passes[name]

    def _reflection(self, frame, options, targets: dict):
        """The view's terrain and opaque models mirrored in the water plane and clipped there, into the reflection
        map (``GAMMA_FS``: the games' maps hold the scene's colours in gamma values and its coverage in alpha; the
        view's lit colours stand in for theirs), with the main pass's instances in view."""
        renderer, ctx = self.m_renderer, self.m_renderer.m_ctx
        own = self._mirror_targets()
        if not options.reflections:  # no scene drawn again: the map is cleared once and stays empty
            if self.m_empty_mirror is not own:
                own["fbo"].use()
                own["fbo"].clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
                self._pass("gamma", GAMMA_FS).draw(own["gamma_fbo"], {"gLit": own["color"]}, {}, depth_test=False)
                targets["msaa"].use()
                ctx.viewport = (0, 0, *targets["size"])
                self.m_empty_mirror = own
            return own["gamma"].native
        self.m_empty_mirror = None
        view = np.asarray(frame.view, float) @ MIRROR_Y
        proj = np.asarray(frame.view_proj, float) @ np.linalg.inv(np.asarray(frame.view, float))
        mirrored = oblique_near(proj, np.linalg.inv(view).T @ np.array([0.0, 1.0, 0.0, 0.0]))
        eye = np.asarray(frame.eye, float) * (1.0, -1.0, 1.0)
        mirrored_frame = replace(frame, view=view, view_proj=mirrored @ view, eye=eye, planes=None)
        stats, renderer._m_lit = dict(renderer.m_stats), {}
        own["fbo"].use()
        ctx.viewport = (0, 0, self.MIRROR_SIDE, self.MIRROR_SIDE)
        own["fbo"].clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.CULL_FACE)
        renderer._draw_ground(mirrored_frame, options)
        renderer.m_uniforms.forget()
        for batch, part, vaos in renderer.m_drawn_opaque:
            renderer._draw_part(batch, part, vaos, mirrored_frame)
        renderer.m_uniforms.forget()
        renderer.m_stats, renderer._m_lit = (
            stats,
            {},
        )  # the frame's own figures, and its light set again by the next part
        self._pass("gamma", GAMMA_FS).draw(own["gamma_fbo"], {"gLit": own["color"]}, {}, depth_test=False)
        targets["msaa"].use()
        ctx.viewport = (0, 0, *targets["size"])
        return own["gamma"].native

    def _mirror_targets(self) -> dict:
        if not self.m_mirror:
            ctx, side = self.m_renderer.m_ctx, (self.MIRROR_SIDE, self.MIRROR_SIDE)
            color, depth = ctx.texture(side, 4, dtype="f2"), ctx.depth_renderbuffer(side)
            gamma = ctx.native_texture(side, QRhiTexture.Format.RGBA8)
            self.m_mirror = {"color": color, "depth": depth, "fbo": ctx.framebuffer([color], depth), "gamma": gamma,
                           "gamma_fbo": ctx.framebuffer([gamma])}  # fmt: skip
        return self.m_mirror

    def _drop(self) -> None:
        for key in ("sea_fbo", "clear_fbo", "refraction_fbo", "read_fbo", "sea", "refraction", "read"):
            if key in self.m_targets:
                self.m_targets[key].release()
        self.m_targets = {}

    def release(self) -> None:
        self._drop()
        for key in ("fbo", "gamma_fbo", "color", "depth", "gamma"):
            if key in self.m_mirror:
                self.m_mirror[key].release()
        self.m_mirror, self.m_empty_mirror = {}, None
        if self.m_heights_map is not None:
            self.m_heights_map[1].release()
            self.m_heights_map = None
        for sea_pass in self.m_passes.values():
            sea_pass.release()
        self.m_passes = {}
