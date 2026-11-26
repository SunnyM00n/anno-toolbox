"""The base of the viewer's own-shader looks: what ``gamepass.CGamePass`` (117), ``fxcpass.CFxcPass`` (2070) and
``forwardpass.CForwardPass`` (2205) share. A subclass sets ``GAME``, ``PREFIX`` (its loader keys), ``SOURCE`` (the type
its ``_make_source`` returns, by default made of the install's files) and ``NOT_OURS`` (the error of a group the game's shaders do not draw); the title, the
backend and the compiler's error come from ``render.nativelook.GAMES``.

``COwnPass`` is the base: ``why_not``, ``busy``, ``release_scene`` and ``release`` are shared, ``_split`` sorts a frame's
groups through ``_model`` (a cloth's mesh follows its simulation), which calls the per-pass ``_make_source``, ``_work``
(background) and ``_build`` (device side); ``_part`` makes a frame part once it compiled. docs/viewer.md "The renderer".
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from ..infra import dxc, fxc
from ..infra.i18n import tr
from ..models import ddsdecode
from ..render import clothlogo, clothmesh, fxcdraw, nativelook
from ..render import gameshader as gs
from ..render.camera import MIRROR
from ..render.loader import CLoader

PARTICIPANT_COLOR = (0.13, 0.32, 0.72)  # the colour a dye material takes in the games' own shaders: a blue player
COMPILERS = {nativelook.DXC: (dxc.CDxcMissing, dxc.WHERE_TO_GET), nativelook.FXC: (fxc.CFxcMissing, fxc.WHERE_TO_GET)}


class COwnPass:
    """The parts of one scene as the game's own shaders draw them (module docstring)."""

    GAME = ""
    PREFIX = ""  # the key of a group's background work in the renderer's loader
    SOURCE: type = object
    NOT_OURS: type[Exception] = Exception
    TITLE = ""
    BACKEND = ""  # the QRhi backend's name it draws on
    SAMPLES = 4  # the samples of the view's target while it draws: the old engines' forward passes draw into it
    MISSING: type[Exception] = Exception  # the error of a compiler that is not there
    # the view's axes are the game's mirrored in x, and the games look their cubes up by world vectors: the cubes go
    # up mirrored (fxcdraw.cube_faces), unless a pass carries the mirror in a turn (shader-compile.md 11 "Sky cubes")
    MIRROR_CUBES = True
    # the old engines' passes draw in the game's own axes instead, the mirror in the projection: their cubes, maps and
    # turns as stored (shader-compile.md 11 "The game's own axes")
    GAME_AXES = False
    WHERE = ""  # the string id of the sentence that says where the compiler is from

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        native = nativelook.GAMES.get(cls.GAME)
        if native is not None:
            cls.TITLE, cls.BACKEND = native.title, native.backend.upper()
            cls.MISSING, cls.WHERE = COMPILERS[native.compiler]

    def __init__(self, renderer) -> None:
        self.m_renderer = renderer
        self.m_models: dict[tuple, object] = {}  # (part index, group key) -> its model, or the reason it is not one
        self.m_sources = CLoader()  # kept across scenes: the install's shaders are read once
        self.m_cubes: dict[str, object] = {}  # a cube map's file -> its texture on the device (None: not a cube)
        self.m_switches: dict[str, bool] = {}  # the frame's parts the view switched off (render.nativelook.FEATURES)
        self.m_dummies: fxcdraw.CDummies | None = None  # the FXC passes' stand-in textures, made on first use
        self.m_waiting = 0  # the groups of the last frame still compiling (or not yet asked for)
        self.m_broken = ""  # a pass of the frame that failed: our look draws the scene (until the next one)

    def why_not(self) -> str:
        """Why the look cannot draw here ("" when it can, or is still reading the install)."""
        ctx = self.m_renderer.m_ctx
        if self.m_broken:
            return self.m_broken
        if getattr(self.m_renderer.m_files.m_files, "m_game", "") != self.GAME:
            return tr("viewer.pass_draws_title_own_shaders", title=self.TITLE)
        if not hasattr(ctx, "native_pass") or ctx.m_rhi.backendName() != self.BACKEND:
            backend = _backend_title(self.BACKEND)
            return tr("viewer.title_own_shaders_draw_on_backend", title=self.TITLE, backend=backend)
        found = self.m_sources.get("source", lambda: self._make_source(self.m_renderer.m_files.m_files))
        if isinstance(found, self.MISSING):
            return tr("viewer.title_own_shaders_need_compiler", title=self.TITLE, where=tr(self.WHERE))
        if isinstance(found, Exception):
            kind = type(found).__name__
            return tr("viewer.title_shaders_not_read", title=self.TITLE, kind=kind, error=str(found))
        return ""

    def busy(self) -> int:
        """Work still to come: the install's reads and compiles, and the groups the last frame waited for (their
        compiles are asked for only once the source is read, a frame later); none once the pass failed or the
        install's read did (a frame drawn while it was still read left its groups waiting for a compiler that is not
        there)."""
        read = self.m_sources.m_futures.get("source")
        if self.m_broken or (read is not None and read.done() and not isinstance(read.result(), self.SOURCE)):
            return 0
        return self.m_sources.pending() + self.m_waiting

    def changed(self) -> bool:
        """The install's source or a frame part's compile arrived since the last call (the window draws again)."""
        arrived = self.m_sources.m_changed.is_set()
        self.m_sources.m_changed.clear()
        return arrived

    def again(self) -> bool:
        """Whether the last frame left work for the next that no arriving file starts (a read-back that moved)."""
        return False

    def _ready(self):
        """The install's source once it is read (None while it is not, or when it failed)."""
        source = self.m_sources.get("source", lambda: None)
        return source if isinstance(source, self.SOURCE) else None

    def _key(self, k: int, item: dict) -> tuple:
        """A group's identity: its part, its mesh in the file and its material (a material can span several meshes:
        a portrait's shirt is in two)."""
        return (k, item.get("mesh", 0), item["key"])

    def _model(self, k: int, part, item):
        """The group's model, an exception (drawn by our look), or None while it compiles."""
        key = self._key(k, item)
        if key in self.m_models:
            return self.m_models[key]
        source = self._ready()
        if source is None:
            return None
        job = f"{self.PREFIX}:{':'.join(map(str, key))}"
        what = tr("viewer.node_games_shader", node=part.node)
        got = self._job(key, job, lambda: self._work(source, part, item), what)
        return got if got is None or isinstance(got, Exception) else self._build(key, got)

    def _job(self, key: tuple, job: str, work: Callable, what: str):
        """The result of ``work`` run in the background as the loader's ``job``: None while it runs; an exception is
        kept in ``models`` under ``key`` (our look draws it) and said once with ``what``, unless the game's shaders
        just do not draw it (``NOT_OURS``)."""
        got = self.m_renderer.m_game_loader.get(job, work)  # workers of its own: the renderer's files never queue ahead
        if isinstance(got, Exception):
            self.m_models[key] = got
            if not isinstance(got, self.NOT_OURS):
                self.m_renderer.say(
                    tr("viewer.name_kind_error", name=what, kind=type(got).__name__, error=str(got)[:300])
                )
        return got

    def _split(self, drawn: list) -> tuple[list, list, int]:
        """A frame's groups (part, item, place) sorted: (key, part, item, place, model) of those the game's shaders
        draw, the (part, item, place) left to our look, and how many still compile."""
        self._take_failures()
        index = {id(scene_part): k for k, scene_part in enumerate(self.m_renderer.m_scene.m_parts)}
        ready, rest, waiting = [], [], 0
        for part, item, place in drawn:
            k = index.get(id(part), -1)
            model = self._model(k, part, item) if k >= 0 and part.banner is None else ValueError("not a model")
            if model is None:
                waiting += 1
            elif isinstance(model, Exception):
                rest.append((part, item, place))
            else:
                key = self._key(k, item)
                if getattr(part, "cloth", None) is not None:  # the simulated vertices of this frame
                    logo = self._logo()
                    if logo is None:  # the player logo is still read: the cloth is drawn once it is there
                        waiting += 1
                        continue
                    for piece in self._pieces(key, model):
                        if piece is not None:
                            clothmesh.follow(item, piece, place, PARTICIPANT_COLOR, logo)
                ready.append((key, part, item, place, model))
        self.m_waiting = waiting
        return ready, rest, waiting

    def _logo(self):
        """The player logo's texture for the cloths (``render.clothlogo``), read once in the background: None while it
        reads, False when the game has none."""
        files = self.m_renderer.m_files.m_files
        if not clothlogo.has_logos(files):
            return False
        got = self.m_renderer.m_loader.get("cloth-logo", lambda: clothlogo.default_logo(files) or False)
        return got if got is None or isinstance(got, ddsdecode.CDdsFile) else False

    def _take_failures(self) -> None:
        """What the device's last frame could not do: a group whose model did not prepare
        (``render.nativepass.prepared``) goes to our look, and a native pass that raised turns the look off for the
        scene (``why_not``), each said once. docs/viewer.md "When a pass fails"."""
        ctx = self.m_renderer.m_ctx
        failed = getattr(ctx, "m_failed_models", None)
        for model, error in failed or ():
            key = next(
                (k for k, entry in self.m_models.items() if any(model is x for x in self._pieces(k, entry))), None
            )
            if key is None:
                self._unowned(model, error)
            else:
                self._fall_back(key, error)
        if failed:
            failed.clear()
        errors = getattr(ctx, "m_pass_errors", None)
        if errors:
            error = errors[-1][:300]
            self.m_broken = tr("viewer.title_frame_failed_our_look_draws", title=self.TITLE, error=error)
            self.m_renderer.say(self.m_broken)
            errors.clear()

    def _pieces(self, key: tuple, model) -> tuple:
        """The device-side draws of a group's model: one that fails sends the group to our look."""
        if isinstance(model, Exception):
            return ()
        return (model, *getattr(model, "m_later", ()), getattr(model, "m_shadow", None))

    def _unowned(self, model, error: str) -> None:
        """A frame part of the pass's own (not a group's) that did not prepare."""
        self.m_renderer.say(tr("viewer.games_shaders_error", error=error[:300]))

    def _fall_back(self, key: tuple, error: str) -> None:
        """The group of ``key`` drawn by our look from now on (its model released), its error said once."""
        model = self.m_models.get(key)
        if model is not None and not isinstance(model, Exception):
            self._release_model(model)
        self.m_models[key] = RuntimeError(error)
        part = (
            self.m_renderer.m_scene.m_parts[key[0]]
            if isinstance(key[0], int) and self.m_renderer.m_scene is not None
            else None
        )
        what = part.node if part is not None else key[1]
        self.m_renderer.say(tr("viewer.node_games_shader_our_look_draws", node=what, error=error[:300]))

    def _part(self, key: str, compile_: Callable, make: Callable, what: str, instead: str,
              on_error: Callable[[], None] = lambda: None):  # fmt: skip
        """One frame part the game's shaders compile: ``make(compiled)`` once the install's source and the part are
        ready (the result is the caller's to keep), None while they compile; on an error ``what`` is said once with
        what is used ``instead`` (two string ids), ``on_error`` runs and None comes back."""
        source = self._ready()
        if source is None:
            return None
        got = self.m_sources.get(key, lambda: compile_(source))
        if isinstance(got, Exception):
            kind, error = type(got).__name__, str(got)[:300]
            self.m_renderer.say(
                tr("viewer.part_kind_error_instead", part=tr(what), kind=kind, error=error, instead=tr(instead))
            )
            on_error()
            return None
        return make(got) if got is not None else None

    def _world(self, place) -> np.ndarray:
        """A part's place (Anno's axes, column vectors) as the pass's world: as it is in the game's axes
        (``GAME_AXES``), else mirrored into the view's."""
        matrix = np.asarray(place, float)
        return matrix if self.GAME_AXES else MIRROR @ matrix

    def _camera(self, camera, size_px: tuple[int, int]) -> gs.SCamera:
        """The view's orbit camera as the pass's (``gameshader.direct_camera``, in the game's axes with
        ``GAME_AXES``)."""
        near, far = camera.planes()
        return gs.direct_camera(size_px, camera.eye(), camera.m_target, camera.m_fov_rad, near, far, self.GAME_AXES)

    def _direction(self, vector) -> tuple:
        """A direction of the view's (the sun's) in the pass's axes."""
        return tuple(MIRROR[:3, :3] @ np.asarray(vector, float)) if self.GAME_AXES else tuple(vector)

    def _map(self, path: str, missing: bool = False):
        """The device texture of a map file as stored (the shaders turn colours into linear light themselves), None
        without a path or while it loads; ``missing``: a colour map that does not read shows the checker."""
        texture = self.m_renderer._texture(path, False, missing=missing) if path else None
        return texture.native if texture is not None else None

    def _dummies(self) -> fxcdraw.CDummies:
        """The stand-in textures of this device (an FXC pass's draws share them)."""
        if self.m_dummies is None:
            self.m_dummies = fxcdraw.CDummies(self.m_renderer.m_ctx.m_rhi)
        return self.m_dummies

    def _make_source(self, tree):
        """The install's source (``SOURCE`` of the install's files, by default)."""
        return self.SOURCE(tree)

    def _on(self, part: str) -> bool:
        """Whether View > Rendering leaves the frame part ``part`` on (``nativelook.part_on``)."""
        return nativelook.part_on(self.GAME, self.m_switches, part)

    def _work(self, source, part, item):
        raise NotImplementedError

    def _build(self, key: tuple, got):
        """On the device: the model of the background work's result, kept in ``models`` under ``key``."""
        raise NotImplementedError

    def _release_model(self, model) -> None:
        model.release()

    def _release_frame(self) -> None:
        """The frame and what else the device holds for the pass."""

    def _cube(self, path: str):
        """The cube map of a file of the game's (a DDS of six faces), None while it loads or when it is no cube; read
        in the background, uploaded as stored or mirrored (``MIRROR_CUBES``; ``fxcdraw.cube_of``)."""
        if path in self.m_cubes:
            return self.m_cubes[path]
        files = self.m_renderer.m_files.m_files
        mirrored = self.MIRROR_CUBES

        def read():  # None for a file that is no cube
            dds = ddsdecode.CDdsFile(files.read(path))
            return fxcdraw.cube_faces(dds, mirrored) if dds.m_info.faces == 6 else None

        job = f"cube:{path}:{'mirrored' if mirrored else 'stored'}"
        got = self.m_renderer.m_loader.get(job, read)
        if got is None:
            if not self.m_renderer.m_loader.m_futures[job].done():
                return None
            got = self.m_renderer.m_loader.get(job, read)  # done between the two looks: not "no cube" for good
        is_loaded = got is not None and not isinstance(got, Exception)
        self.m_cubes[path] = fxcdraw.cube_of(self.m_renderer.m_ctx, got) if is_loaded else None
        return self.m_cubes[path]

    def release_scene(self) -> None:
        for model in self.m_models.values():
            if not isinstance(model, Exception):
                self._release_model(model)
        self.m_models, self.m_waiting, self.m_broken = {}, 0, ""
        for cube in self.m_cubes.values():  # made only on a QRhi device
            self.m_renderer.m_ctx.later(cube)
        self.m_cubes = {}

    def release(self) -> None:
        self.release_scene()
        self._release_frame()
        if self.m_dummies is not None:  # after the frame: its draws bound them
            self.m_dummies.release(self.m_renderer.m_ctx)
            self.m_dummies = None
        self.m_sources.close()


def _backend_title(name: str) -> str:
    return {"D3D11": "Direct3D 11", "D3D12": "Direct3D 12"}.get(name, name)
