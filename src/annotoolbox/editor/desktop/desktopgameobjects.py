"""The island's objects in the island view's look "The game's own shaders": each part of the view's batches (a mesh
with one material) drawn by its material's own effect, many instances a draw (``render.gameinstances``), culled per
ground cell, at the level of detail the game picks by screen size, with its shadow technique culled per cascade; a
part that does not compile (or still compiles) stays the view's own; only models within reach (``CReach``) compile.

``CGameObjects(island)`` is the entry, one per ``desktopgame.CGameIsland``: ``frame(f, opts, shadows, size)`` per frame
gives the draws (``SDrawn``) and fills ``drawn``, the (batch key, part index) the view leaves out (picking reads the
view's own geometry); compiles in ``desktopgameparts``, tables in ``desktopgametables``. island-117-view.md "Phase 5".
"""

from __future__ import annotations

import time
from collections import ChainMap
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtGui import QRhiBuffer

from ...infra.i18n import tr
from ...render import cull, gamedraw, gameshadow
from ...render.gamecompute import carry
from ...render.gameinstances import CFrameInstances, CInstanceRun, CPartPass, SPartInputs
from ...render.gamemaps import CMapPool, CPooledTable
from ...render.objectsnative import CNativePart, SNativeStages, SPartLevel, SPartShaders
from ...render.quality import lod_rules
from .desktopframe import GRASS_FAR
from .desktopgameparts import (
    DEPTH_ONLY,
    SHADOW,
    CSharedMaps,
    SCutOutStages,
    SPartDraws,
    compile_part,
    cutout_states,
    main_state,
    part_key,
)
from .desktopgametables import CFrameTable, CReach, part_worlds

JOB = "game-object:{}"
RULES_JOB = "game-object-lod-rules"
ENGINE_SETTINGS = "data/base/config/engine/enginesettings/default.xml"  # the LOD rules (rendering/camera.md 3)
CASCADES = gameshadow.COUNT  # an unculled caster instance is drawn once per cascade
MAKE_BUDGET = 0.025  # seconds of a frame the device side of new parts may take (``_part``); the rest: the next frames
AHEAD = 2  # parts made on the device ahead of their first draw, a frame (a part's first pipelines: tens of ms) [ours]
# a model within AHEAD_REACH times the distance it can be drawn at has its parts compiled and made ahead; past
# KEEP_REACH times it for KEEP_SECONDS they are let go, compiled again as it nears (island-117-view.md "Speed",
# part 9) [ours]
AHEAD_REACH, KEEP_REACH, KEEP_SECONDS = 1.25, 1.6, 20.0


@dataclass
class SDrawn:
    """One frame's object draws: into the G-buffer, into the cascades, in the transparent pass."""

    gbuffer: list = field(default_factory=list)
    casters: list = field(default_factory=list)
    transparent: list = field(default_factory=list)
    cutouts: list = field(default_factory=list)  # the cut-out volumes': right after the terrain, before the decals
    native: list = field(default_factory=list)  # the native backend's runs into the G-buffer (``objectsnative``)
    native_casters: list = field(default_factory=list)  # and into the cascades
    native_cutouts: list = field(default_factory=list)  # the cut-out volumes' natively: right after the native terrain
    native_cut_casters: list = field(default_factory=list)
    instances: int = 0
    levels: list = field(default_factory=lambda: [0] * 6)  # instances per level of detail, too small last


class CAheadRun(CInstanceRun):
    """A draw of no instance that makes a part's pass on the device before the camera reaches it: its maps, its
    binding set, the mesh's buffers and the pipeline a first draw would make in its frame (island-117-view.md "Speed",
    part 8)."""

    ahead = True  # it draws nothing and binds nothing (``gameframe``: the shadow information's state after QRhi)

    def __init__(self, setup: CPartPass, mesh: gamedraw.CMesh, is_shadow: bool = False) -> None:
        super().__init__(
            setup, mesh, 0, np.zeros((0, 4, 4), np.float32), np.zeros((0, 4), np.uint32) if is_shadow else None
        )

    def draw(self, command_buffer, size_px: tuple[int, int], instances: int | None = None) -> None:
        """Nothing: it is made, not drawn."""

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        return np.zeros(3), np.zeros(3)


class _CPart:
    """A compiled part on the device: per pass (the G-buffer's or the transparent's, the depth prepass, the shadow)
    one ``CPartPass`` for all its levels and meshes, the meshes' buffers once for every pass; a frame's draws are
    ``CInstanceRun``s of them over the frame's instances (``frame``)."""

    m_cutout = False  # a cut-out volume's two passes (``SCutOutStages``): drawn right after the terrain

    def __init__(self, rhi, got: SPartDraws, programs: dict, textures: dict, pool: CMapPool,
                 frame: CFrameInstances) -> None:  # fmt: skip
        self.m_rhi, self.m_got, self.m_frame = rhi, got, frame
        self.m_maps: list = []  # the arrays its passes bind (one per file, from ``pool``), made by the first that draws
        self.m_table = CPooledTable(got.material, pool)  # their slots, shared by every pass
        stages = got.stages
        self.m_is_transparent, self.m_cutout = stages.is_transparent, isinstance(stages, SCutOutStages)
        if self.m_cutout:  # the stencil's count, then the depth cleared where it is odd; in the shadow map alike
            roles = {"main": (stages.mark, stages.states()[0]), "clear": (stages.clear, stages.states()[1])}
            if stages.shadow is not None:  # drawn once per cascade (``cCurrentCascade``), as the engine draws it
                for k in range(CASCADES):
                    for role, shaders, how in zip(
                        ("mark", "clear"), stages.shadow, stages.shadow_states(), strict=True
                    ):
                        roles[f"shadow_{role}{k}"] = (shaders, how)
        else:
            roles = {"main": ((stages.vertex_stage, stages.pixel_stage), main_state(stages)), "prepass": (stages.prepass, DEPTH_ONLY),
                     "shadow": (stages.shadow, SHADOW)}  # fmt: skip
        inputs = SPartInputs(got.material, frame, textures, self.m_maps, self.m_table, self._mesh)
        self.m_passes = {role: CPartPass(rhi, _program(programs, *shaders), how, inputs)
                       for role, (shaders, how) in roles.items() if shaders is not None}  # fmt: skip
        for role, setup in self.m_passes.items():
            if role.startswith("shadow_"):
                setup.m_values["cCurrentCascade"] = int(role[-1])
        self.m_levels = SPartLevel.of(got)
        self._m_meshes: dict[int, tuple] = {}  # id of a mesh -> (vertex buffer, index buffer, the mesh)
        self.m_made: set[str] = set()  # the passes made ahead of their first draw (``ahead``)
        self.begin()

    def begin(self) -> None:
        """A new frame: no draws yet (``models``)."""
        self.m_runs: list[CInstanceRun] = []

    def models(self, level: int, worlds: np.ndarray, has_shadows: bool, mask: np.ndarray | None = None,
               is_deformed: bool = False) -> tuple:  # fmt: skip
        """This frame's draws of ``worlds`` at ``level`` (and their casters when ``has_shadows``, into the cascades
        ``mask`` marks: None all), their instances placed in the frame's buffers (``is_deformed``: bent to the terrain)."""
        out, cast = [], []
        part_level = self.m_levels[min(level, len(self.m_levels) - 1)]
        prepass, shadow, clear = self.m_passes.get("prepass"), self.m_passes.get("shadow"), self.m_passes.get("clear")
        reach = mask if mask is not None else np.ones((len(worlds), CASCADES), bool)
        for mesh, joints in zip(part_level.meshes, part_level.joints, strict=True):
            slot = self.m_frame.place(worlds, joints, is_deformed)
            if prepass is not None:  # its depth first
                out.append(CInstanceRun(prepass, mesh, slot, worlds))
            out.append(CInstanceRun(self.m_passes["main"], mesh, slot, worlds))
            if clear is not None:  # a cut-out's second pass
                out.append(CInstanceRun(clear, mesh, slot, worlds))
            if has_shadows and shadow is not None:
                caster_slot, words = self.m_frame.place_casters(worlds, joints, reach, is_deformed)
                if len(words):
                    cast.append(CInstanceRun(shadow, mesh, caster_slot, worlds, words))
            elif has_shadows and self.m_cutout:
                cast += self._cut_casters(mesh, joints, worlds, reach, is_deformed)
        self.m_runs += out + cast
        return out, cast

    def _cut_casters(self, mesh, joints: int, worlds: np.ndarray, reach: np.ndarray, is_deformed: bool) -> list:
        """A cut-out volume's count and clear in each cascade it reaches (``CFrameInstances.place_each_cascade``)."""
        out = []
        if "shadow_mark0" not in self.m_passes:  # every cascade's passes or none (``SCutOutStages.shadow``)
            return out
        for k, caster_slot, words in self.m_frame.place_each_cascade(worlds, joints, reach, is_deformed):
            mark, clear = self.m_passes[f"shadow_mark{k}"], self.m_passes[f"shadow_clear{k}"]
            quarter = gameshadow.DIVISIONS[k]  # the engine's viewport for the cascade: the shaders do not place it
            out += [CInstanceRun(setup, mesh, caster_slot, worlds, words, quarter) for setup in (mark, clear)]
        return out

    def ahead(self, has_shadows: bool) -> tuple[list, list, list]:
        """Draws of no instance (``CAheadRun``) that make each pass not made yet with every level's meshes: (into the
        G-buffer, transparent, casters); the shadow pass only when the frame casts."""
        meshes = list({id(mesh): mesh for part_level in self.m_levels for mesh in part_level.meshes}.values())
        out: tuple[list, list, list] = ([], [], [])
        for role, setup in self.m_passes.items():
            cast = role.startswith("shadow")  # a pass into the cascades
            if role in self.m_made or (cast and not has_shadows):
                continue
            self.m_made.add(role)
            into = out[2] if cast else out[1] if role == "main" and self.m_is_transparent else out[0]
            into.extend(CAheadRun(setup, mesh, cast) for mesh in meshes)
        self.m_runs += [*out[0], *out[1], *out[2]]
        return out

    def _mesh(self, mesh: gamedraw.CMesh, batch) -> tuple:
        """A mesh's vertex and index buffers, uploaded once for every pass (its vertex bytes let go after)."""
        got = self._m_meshes.get(id(mesh))
        if got is None:
            usage = QRhiBuffer.UsageFlag
            vertices = gamedraw.immutable_buffer(self.m_rhi, batch, usage.VertexBuffer, mesh.m_data)
            indices = gamedraw.immutable_buffer(
                self.m_rhi, batch, usage.IndexBuffer, mesh.m_indices.astype("<u4").tobytes()
            )
            got = self._m_meshes[id(mesh)] = (vertices, indices, mesh)
            mesh.m_data = b""
        return got[0], got[1]

    def all(self) -> list:
        """Its passes and this frame's draws (a failed draw is found among them)."""
        return [*self.m_passes.values(), *self.m_runs]

    def release(self) -> None:
        for setup in self.m_passes.values():
            setup.release()
        for vertices, indices, _mesh in self._m_meshes.values():
            vertices.destroy()
            indices.destroy()
        self._m_meshes = {}
        self.m_table.release()
        self.m_maps.clear()
        self.m_made = set()
        self.begin()


def _on_device(part) -> bool:
    """Whether a part is on the device: the current path's or the native backend's (not an exception, not compiling)."""
    return isinstance(part, (_CPart, CNativePart))


def _unmade(part: _CPart, has_shadows: bool) -> bool:
    """Whether a pass of ``part`` (the shadow's only when the frame casts) was not made ahead yet: a pass already
    drawn may still meet a coarser level's mesh of another vertex layout (a pipeline of its own)."""
    return any(role not in part.m_made for role in part.m_passes if has_shadows or not role.startswith("shadow"))


def _program(programs: dict, vertex_shader, pixel_shader) -> gamedraw.CProgram:
    """One program per pair of compiled shaders, shared by every part that compiles to them."""
    key = (vertex_shader.entry, vertex_shader.blob, pixel_shader.entry, pixel_shader.blob)
    if key not in programs:
        programs[key] = gamedraw.CProgram(vertex_shader, pixel_shader)
    return programs[key]


class CGameObjects:
    """The island's objects by the game's model shaders (module docstring)."""

    def __init__(self, island) -> None:
        self.m_island, self.m_renderer = island, island.m_renderer
        self.m_parts: dict[tuple, _CPart | Exception] = {}
        self.m_programs: dict = {}
        self.m_jobs: dict[tuple, str] = {}  # part key -> its loader key
        self.m_keys: dict[int, tuple] = {}  # id of a view part -> (the part, its key)
        self.m_drawn: set[tuple] = set()
        self.m_fallback: set[str] = set()  # the models (configs) with a part the view's own look draws
        self.m_native_off: set[tuple] = set()  # part keys the native objects gave up on: the current path's
        self.m_waiting = 0
        self.m_neutral: dict | None = None  # the session maps an island view has none of, as the shaders read "none"
        self.m_rules: cull.SLodRules | None = None  # the game's level-of-detail rules, once read
        self.m_files: CSharedMaps | None = None  # the editor's files, the maps read once for all parts
        self.m_pool: CMapPool | None = None  # the maps on the device, once per file for all parts
        self.m_instances: CFrameInstances | None = None  # the frame's instance data of every draw
        self.m_failures = 0  # parts sent back to the view's look (a frame's draws are made again)
        self.m_ahead_left = 0  # compiled parts within reach not yet made on the device (``_ahead``)
        self._m_made = 0.0  # this frame's time on the device side of new parts (``MAKE_BUDGET``)
        self.m_is_terrain_adapted = (
            False  # the frame hands the session's terrain: the models adapt to it (``CFrameInstances``)
        )
        self.m_near_at: dict[str, float] = {}  # model -> when it was last within ``KEEP_REACH`` (``_near``)
        self._m_kept: list[str] = []  # the models within ``KEEP_REACH`` at the last frame worked out
        self.m_users: dict[tuple, set[str]] = {}  # part key -> the models it was asked for by (``_let_go``)
        self._m_swept = 0.0  # when ``_let_go`` last looked
        self._m_last: tuple = (None, SDrawn())  # the last frame's key and draws (``_frame_key``)
        self._m_frame_table: CFrameTable | None = None  # every shown batch's instances (``_draws``)
        self._m_reach: CReach | None = None  # every shown batch's cells against the camera (``_near``)

    def _key(self, part: dict) -> tuple:
        got = self.m_keys.get(id(part))
        if got is None or got[0] is not part:
            got = self.m_keys[id(part)] = (part, part_key(part))
        return got[1]

    def _part(self, part: dict, source):
        """The device side of a view part: its ``_CPart``, an exception (the view draws it) or None while it compiles."""
        key = self._key(part)
        found = self.m_parts.get(key)
        if found is not None:
            return found
        job = self.m_jobs.setdefault(key, JOB.format(len(self.m_jobs)))
        if self.m_files is None:
            self.m_files = CSharedMaps(self.m_renderer.m_editor.m_files)
        files, natives = self.m_files, self._natives(key)
        got = self.m_renderer.m_loader.get(job, lambda: compile_part(source, files, part, natives))
        if got is None:
            return None
        if isinstance(got, Exception):
            self.m_parts[key] = got
            if type(got).__name__ != "CNoGBufferPass":
                self.m_renderer._message(tr("islands.mesh_games_shader_reason", mesh=part["mesh"],
                                   reason=f"{type(got).__name__}: {got}"[:300]))  # fmt: skip
            return got
        terrain = getattr(self.m_island, "m_terrain", None)  # its session maps: grit, wetness, wildfire read as none
        textures = ChainMap(self.m_neutral or {}, terrain.m_textures if terrain is not None else {})
        if self.m_pool is None:
            quality = getattr(self.m_island, "m_quality", None)
            self.m_pool = CMapPool(self.m_renderer.m_ctx.m_rhi, quality.max_texture if quality is not None else 0)
            self.m_instances = CFrameInstances(self.m_renderer.m_ctx.m_rhi)
            self.m_instances.m_is_terrain_adapted = self.m_is_terrain_adapted
        if self._m_made > MAKE_BUDGET:
            return None  # made in a later frame (a continental island's came in one frame, seconds)
        started = time.perf_counter()
        native = getattr(self.m_island, "m_native_objects", None)
        shaders = getattr(got, "native", None)
        cutout = isinstance(shaders, SPartShaders) and getattr(shaders.flags, "water", None) is not None
        if cutout and getattr(self.m_island, "m_native", None) is None:  # recorded after the native terrain only
            self.m_native_off.add(key)
            native = None
        if native is not None and isinstance(getattr(got, "native", None), SPartShaders):
            states = cutout_states(got.stages) if cutout else {"main": main_state(got.stages),
                                                                "prepass": DEPTH_ONLY, "shadow": SHADOW}  # fmt: skip
            try:
                self.m_parts[key] = native.part(got, got.native, self.m_instances, states)
                self._m_made += time.perf_counter() - started
                return self.m_parts[key]
            except Exception as exc:  # the current path draws it, said once
                self.m_native_off.add(key)
                self.m_renderer._message(tr("islands.mesh_games_shader_reason", mesh=part["mesh"],
                                   reason=f"native: {type(exc).__name__}: {exc}"[:300]))  # fmt: skip
        if isinstance(getattr(got, "stages", None), SNativeStages):  # nothing of the current path compiled: now
            self.m_renderer.m_loader.forget(job)
            return None
        self.m_parts[key] = _CPart(
            self.m_renderer.m_ctx.m_rhi, got, self.m_programs, textures, self.m_pool, self.m_instances
        )
        self._m_made += time.perf_counter() - started
        return self.m_parts[key]

    def _natives(self, key: tuple):
        """The shipped shaders a part's compile finds natively too (None: the current path draws it)."""
        if key in self.m_native_off or getattr(self.m_island, "m_native_objects", None) is None:
            return None
        return getattr(self.m_island, "_m_natives", None)

    def frame(self, view_frame, options, has_shadows: bool, size_px: tuple[int, int] = (1, 1), cascades=None) -> SDrawn:
        """The draws of every shown batch's parts the game's shaders draw (``drawn`` names them), the instances in
        view at the level of detail the game picks for ``size_px`` (too small: not drawn), their casters into the
        cascades they reach (``cascades``: a ``gameshadow.SCascades`` of the last frames; None: into all); the markers,
        the part under the cursor and the grass far off stay the view's."""
        source = getattr(self.m_island, "m_source", None)
        if source is None or self.m_renderer.m_editor is None:
            self.m_drawn, self.m_waiting = set(), 0
            return SDrawn()
        self._neutral()
        # the quality of this frame (a Custom bias applies at once), not the one the look was made for
        rules = lod_rules(getattr(options, "quality", None), self._rules())
        projection = view_frame.view_proj @ np.linalg.inv(view_frame.view)
        known = cascades if cascades is not None and len(cascades.cascades) else None
        batches = [batch for batch in self.m_renderer.m_batches.values() if self._shown(batch, options, view_frame)]
        key = (
            self._frame_key(view_frame, projection, size_px, has_shadows, known, batches),
            rules,
            self.m_is_terrain_adapted,
        )
        still = key == self._m_last[0]
        self._let_go(self._m_kept if still else ())  # a still camera keeps what is near it: the last frame drew it
        if still:  # the camera stands and nothing changed: the last frame's draws and instances
            return self._m_last[1]
        out, self.m_drawn, self.m_waiting = SDrawn(), set(), 0
        self._m_made = 0.0
        if self.m_instances is not None:
            self.m_instances.begin()
            self.m_instances.m_is_terrain_adapted = self.m_is_terrain_adapted
        for part in self.m_parts.values():
            if _on_device(part):
                part.begin()
        drawing, fallback, near_parts = [], set(), {}
        cold = getattr(self.m_island, "m_cold", False)
        near = self._near(batches, view_frame, projection, size_px, rules)
        for batch in near:
            built, seen = [], {}
            for k, (view_part, _vaos) in enumerate(batch.m_vaos):
                if id(view_part) not in seen:
                    self.m_users.setdefault(self._key(view_part), set()).add(batch.m_key[1])
                part = seen[id(view_part)] if id(view_part) in seen else self._part(view_part, source)
                if _on_device(part):
                    self.m_drawn.add((batch.m_key, k))
                    near_parts[id(part)] = part
                    if id(view_part) not in seen:  # a part of several meshes is one game part: drawn once
                        built.append((view_part, part))
                elif part is not None or cold or batch.m_key[1] in self.m_fallback:
                    fallback.add(
                        batch.m_key[1]
                    )  # the view's look draws it; also while it compiles on a first start or anew
                self.m_waiting += part is None and id(view_part) not in seen
                seen[id(view_part)] = part
            if built:
                drawing.append((batch, built))
        # a model not looked at this frame keeps its look: its batch's parts are made again for a frame when it
        # changes looks, and leaving it out made it flip between the looks every frame (its meshes loaded and let go)
        self.m_fallback = fallback | (self.m_fallback - {batch.m_key[1] for batch in near})
        if drawing:
            self._draws(out, drawing, view_frame, (view_frame.view, projection, size_px, rules), has_shadows, known)
        self._ahead(out, has_shadows, list(near_parts.values()))
        if not self.m_waiting and self.m_files is not None:  # every part near compiled: the shared reads are done with
            self.m_files.clear()
        # while parts compile or are made ahead the next frame asks again: a still camera would never take them in
        self._m_last = (key if not self.m_waiting and not self.m_ahead_left else None, out)
        return out

    def _near(
        self, batches: list, view_frame, projection: np.ndarray, size_px: tuple[int, int], rules: cull.SLodRules
    ) -> list:
        """The batches within ``AHEAD_REACH`` of the camera (their parts compiled, made and drawn); the models of
        those within ``KEEP_REACH`` marked as near now (``_let_go``)."""
        if self._m_reach is None or not self._m_reach.same(batches):
            self._m_reach = CReach(batches)
        ratios = self._m_reach.ratios(view_frame.eye, projection, size_px, rules).tolist()
        self._m_kept = [batch.m_key[1] for batch, ratio in zip(batches, ratios, strict=True) if ratio <= KEEP_REACH]
        self.m_near_at.update(dict.fromkeys(self._m_kept, time.monotonic()))
        return [batch for batch, ratio in zip(batches, ratios, strict=True) if ratio <= AHEAD_REACH]

    def _let_go(self, kept=()) -> None:
        """The parts no model has been near for ``KEEP_SECONDS`` (``kept``: models near now) let go: their device data,
        maps and compiled draws (compiled again, from the build cache, when one nears); a compile finished for a part
        no longer near too. Looked at once a second."""
        now = time.monotonic()
        if now - self._m_swept < 1.0:
            return
        self._m_swept = now
        self.m_near_at.update(dict.fromkeys(kept, now))
        old = {model for model, last_seen in self.m_near_at.items() if now - last_seen > KEEP_SECONDS}
        for model in old:
            del self.m_near_at[model]
        for key in [
            candidate_key
            for candidate_key, models in self.m_users.items()
            if not any(model in self.m_near_at for model in models)
        ]:
            part = self.m_parts.get(key)
            if _on_device(part):
                part.release()
                del self.m_parts[key]
            job = self.m_jobs.get(key)
            if key not in self.m_parts and job is not None and not self.m_renderer.m_loader.forget_done(job):
                continue  # still compiling: let go once it is in
            del self.m_users[key]

    def _ahead(self, out: SDrawn, has_shadows: bool, near: list) -> None:
        """``AHEAD`` compiled parts within reach (``near``) not made on the device yet made in this frame by draws of
        no instance (a part the frame draws is made by its draws); ``ahead_left`` counts the rest."""
        left = [candidate for candidate in near if _unmade(candidate, has_shadows)]
        for part in left[:AHEAD]:
            gbuffer, transparent, casters = part.ahead(has_shadows)
            out.gbuffer.extend(gbuffer)
            out.transparent.extend(transparent)
            out.casters.extend(casters)
        self.m_ahead_left = max(0, len(left) - AHEAD)

    def upload(self, batch) -> None:
        """The frame's instances into their buffers, before the passes (``desktopgame``; nothing new at a still
        camera)."""
        if self.m_instances is not None:
            self.m_instances.upload(batch)
            native = getattr(self.m_island, "m_native_objects", None)
            if native is not None:  # the native parts' copy, on the module's list
                native.upload(self.m_instances)

    def _shown(self, batch, options, view_frame) -> bool:
        if batch.m_key[0] in ("markers", "ghost") or not batch.m_count or not batch.m_parts or batch.m_cells is None:
            return False
        return self.m_renderer._layer_on(batch, options) and not (
            batch.m_key[0] == "grass" and view_frame.distance_m > GRASS_FAR
        )

    def _frame_key(self, view_frame, projection, size_px, has_shadows: bool, cascades, batches: list) -> tuple:
        """What a frame's draws depend on: the camera, the size, the cascades, the batches shown and their instances
        (the objects themselves, not their ids: a freed one's id is soon another's), the parts sent back to the view;
        a frame with parts still compiling is never repeated (``frame``)."""
        known = tuple(cascade.matrix.tobytes() for cascade in cascades.cascades) if cascades is not None else None
        camera = (
            np.asarray(view_frame.view).tobytes(),
            np.asarray(projection).tobytes(),
            tuple(size_px),
            has_shadows,
            known,
        )
        return camera, tuple((batch, batch.m_version, batch.m_parts) for batch in batches), self.m_failures

    def _in_view(self, batch, view_frame) -> np.ndarray:
        """Which of a batch's cells hold instances in view and not too small to draw: the view's own codes of this
        frame for every batch at once (``Renderer._pass_codes``), the batch's own culling where it has none."""
        if view_frame.planes is None:
            return np.ones(len(batch.m_cells.m_counts), bool)
        table, index, every = self.m_renderer._pass_codes("model", view_frame.planes, view_frame.lod)
        batch_index = index.get(id(batch))
        return (
            table.of(every, batch_index) >= 0
            if batch_index is not None
            else batch.m_cells.visible(view_frame.planes, batch.m_reach)
        )

    def _table(self, batches: list) -> CFrameTable:
        if self._m_frame_table is None or not self._m_frame_table.same(batches):
            self._m_frame_table = CFrameTable(batches)
        return self._m_frame_table

    def _draws(self, out: SDrawn, drawing: list, view_frame, camera: tuple, has_shadows: bool, cascades) -> None:
        """The instances in view of every batch of ``drawing`` ((batch, its built parts)) by level of detail into
        ``out``, worked out for all of them at once (``CFrameTable``): the level per instance of the model, as the game
        picks it (its bounding sphere: all its parts switch together; rendering/camera.md 3), each part drawn at it (at
        its own coarsest where it has fewer levels), its casters into the cascades the model reaches."""
        table = self._table([batch for batch, _built in drawing])
        index = table.visible(np.concatenate([self._in_view(batch, view_frame) for batch, _built in drawing]))
        levels = cull.lod_levels(table.m_centres[index], table.m_radii[index], *camera, 5)
        masks = None
        if has_shadows and cascades is not None:
            frame = getattr(self.m_island, "m_frame", None)
            texels = (frame.m_shadow_size if frame is not None else gameshadow.SIZE) // 2  # a cascade: a quarter
            used = frame.m_shadow_cascades if frame is not None else gameshadow.COUNT
            masks = gameshadow.cascade_mask(cascades, table.m_centres[index], table.m_radii[index], texels=texels,
                                            used=used)  # fmt: skip
        counts = np.bincount(levels + 1, minlength=6)
        out.levels = [*counts[1:].tolist(), int(counts[0])]
        out.instances = int(counts[1:].sum())
        keep = levels >= 0
        index, levels = index[keep], levels[keep]
        order, runs = table.runs(index, levels)
        matrices = table.m_mats[index[order]]
        masks = masks[keep][order] if masks is not None else None
        cut: list = []  # the cut-out volumes' casters
        for k, (_batch, built) in enumerate(drawing):
            got = runs.get(k)
            if not got:
                continue
            batch_start, batch_stop = got[0][1], got[-1][2]  # the batch's instances, level by level
            for view_part, part in built:
                worlds = part_worlds(matrices[batch_start:batch_stop], view_part["matrix"])
                for level, run_start, run_stop in got:
                    masked = masks[run_start:run_stop] if masks is not None else None
                    draws, cast = part.models(
                        level,
                        worlds[run_start - batch_start : run_stop - batch_start],
                        has_shadows,
                        masked,
                        bool(view_part.get("deform")),
                    )
                    native = getattr(part, "m_native", False)  # objectsnative.CNativePart: recorded by the backend
                    into = out.transparent if part.m_is_transparent else out.cutouts if part.m_cutout else out.gbuffer
                    if native and part.m_cutout:  # after the native terrain in both passes
                        out.native_cutouts.extend(draws)
                        out.native_cut_casters.extend(cast)
                        continue
                    (out.native if native and not part.m_is_transparent else into).extend(draws)  # ordered with QRhi's
                    (cut if part.m_cutout else out.native_casters if native else out.casters).extend(cast)
        out.casters[:0] = cut  # the terrain's shadow cleared inside the volumes before any model casts into it

    def _rules(self) -> cull.SLodRules:
        """The game's level-of-detail rules, read once in the background (vanilla's until then, or without them)."""
        if self.m_rules is None:
            files = getattr(self.m_renderer.m_editor.m_files, "m_game", self.m_renderer.m_editor.m_files)

            def read() -> cull.SLodRules:
                return cull.read_lod_rules(files.read(ENGINE_SETTINGS).decode("utf-8", errors="replace"))

            got = self.m_renderer.m_loader.get(RULES_JOB, read)
            if got is None:
                return cull.SLodRules()
            self.m_rules = got if isinstance(got, cull.SLodRules) else cull.SLodRules()
        return self.m_rules

    def _neutral(self) -> None:
        """The terrain's tint as none (mid grey: the shaders decode it as YCC, 0.5 is no tint), made once."""
        if self.m_neutral is not None:
            return
        self.m_neutral = {}
        rhi = self.m_renderer.m_ctx.m_rhi

        def make(batch) -> None:
            self.m_neutral["cTerrainTintTex"] = gamedraw.stand_in(rhi, "texture", "Texture2D", batch, 0.5)

        self.m_renderer.m_ctx.native_pass(lambda command_buffer, batch: carry(command_buffer, batch, make))

    def take_failures(self) -> None:
        """A draw that did not prepare in the last frame sends its part to the view's look (said once); a native
        part whose draw raised is compiled again for the current path."""
        native = getattr(self.m_island, "m_native_objects", None)
        for part, error in native.m_failed if native is not None else ():
            key = next((candidate_key for candidate_key, candidate in self.m_parts.items() if candidate is part), None)
            if key is not None:
                part.release()
                del self.m_parts[key]
                self.m_native_off.add(key)
                self.m_renderer.m_loader.forget(self.m_jobs.get(key, ""))  # its meshes and maps went to the device
                self.m_renderer._message(
                    tr("islands.mesh_games_shader_reason", mesh=key[0], reason=f"native: {error}"[:300])
                )
        if native is not None:
            native.m_failed.clear()
        ctx = self.m_renderer.m_ctx
        failed = getattr(ctx, "m_failed_models", None)
        if not failed:
            return
        owners = {id(run): key for key, part in self.m_parts.items() if isinstance(part, _CPart) for run in part.all()}
        mine = [(failed_model, reason) for failed_model, reason in failed if id(failed_model) in owners]
        if not mine:
            return
        ctx.m_failed_models[:] = [failure for failure in failed if all(failure is not other for other in mine)]
        for model, error in mine:
            key = owners[id(model)]
            part = self.m_parts.get(key)
            if isinstance(part, _CPart):
                part.release()
                self.m_parts[key] = RuntimeError(error)
                self.m_failures += 1
                self.m_renderer._message(tr("islands.mesh_games_shader_reason", mesh=key[0], reason=error[:300]))

    def drop_native(self) -> None:
        """Every native part let go and compiled again for the current path (the native objects failed: their
        owner releases them after)."""
        for key, part in [
            (candidate_key, candidate)
            for candidate_key, candidate in self.m_parts.items()
            if getattr(candidate, "m_native", False)
        ]:
            part.release()
            del self.m_parts[key]
            self.m_renderer.m_loader.forget(self.m_jobs.get(key, ""))
        self._m_last = (None, SDrawn())

    def release(self) -> None:
        for part in self.m_parts.values():
            if _on_device(part):
                part.release()
        for texture in (self.m_neutral or {}).values():
            self.m_renderer.m_ctx.later(texture)
        if self.m_pool is not None:
            self.m_pool.release()
            self.m_instances.release()
        self.m_neutral, self.m_pool, self.m_instances = None, None, None
        for job in self.m_jobs.values():  # compiled again when asked: the pool took their maps
            self.m_renderer.m_loader.forget(job)
        self.m_parts, self.m_keys, self.m_drawn, self.m_jobs, self.m_files = {}, {}, set(), {}, None
        self.m_native_off = set()
        self._m_frame_table, self._m_reach, self.m_near_at, self.m_users, self._m_kept = None, None, {}, {}, []
        self._m_last = (None, SDrawn())
