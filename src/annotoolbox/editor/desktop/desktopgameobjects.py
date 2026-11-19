"""The island's objects in the island view's look "The game's own shaders": each part of the view's batches (a mesh
with one material) drawn by its material's own effect, many instances a draw (``render.gameinstances``), culled per
ground cell, at the level of detail the game picks by screen size, with its shadow technique culled per cascade; a
part that does not compile (or still compiles) stays the view's own; only models within reach (``Reach``) compile.

``GameObjects(island)`` is the entry, one per ``desktopgame.GameIsland``: ``frame(f, opts, shadows, size)`` per frame
gives the draws (``Drawn``) and fills ``drawn``, the (batch key, part index) the view leaves out; picking keeps reading
the view's own geometry, which lies where these draws lie. docs/rendering/island-117-view.md "Phase 5", "Speed".
"""

from __future__ import annotations

import threading
import time
from collections import ChainMap
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtGui import QRhiBuffer

from ...infra.i18n import tr
from ...render import cull, gamedraw, gameshadow, renderstates
from ...render.gamecompute import carry
from ...render.gameinstances import FrameInstances, InstanceRun, PartInputs, PartPass
from ...render.gamemaps import MapPool, PooledTable
from ...render.quality import lod_rules
from .desktopframe import GRASS_FAR

JOB = "game-object:{}"
RULES_JOB = "game-object-lod-rules"
ENGINE_SETTINGS = "data/base/config/engine/enginesettings/default.xml"  # the LOD rules (rendering/camera.md 3)
DEPTH_ONLY = renderstates.PassState(colour=False)  # a depth prepass: depth tested and written, no colour
SHADOW = renderstates.GBUFFER_STATE
CASCADES = gameshadow.COUNT  # an unculled caster instance is drawn once per cascade
# the cut-out volume's ShaderID: it draws no colour, it clears the terrain's depth inside it and marks the stencil the
# water tests (model_volume_cutout.fx; island-117-view.md "Round 12")
CUT_OUT_VOLUME = 4
CUT_OUT_FILE = "model_volume_cutout"
# the stencil bits it sets: the sea's and the rivers' cut-out bits with CUT_OUT_WATER, else the water's own (defines.fxh)
CUT_OUT_WATER_BITS, CUT_OUT_TERRAIN_BITS = 0x01 | 0x02, 0x08
SHADOW_CUT_OUT_BIT = 0x01  # the shadow map's stencil holds only the cut-outs' count [ours]
# CutOutShadow's pixel shaders flip and test the bit in the game's own array; with the bit in the stencil they draw
# nothing: this one stands in for both [ours]
SHADOW_CUT_OUT_PS = (
    "void psCutOutShadowStencil(float4 position : SV_Position) {}\n",
    "psCutOutShadowStencil",
    "ps_6_2",
)
MAKE_BUDGET = 0.025  # seconds of a frame the device side of new parts may take (``_part``); the rest: the next frames
AHEAD = 2  # parts made on the device ahead of their first draw, a frame (a part's first pipelines: tens of ms) [ours]
# a model within AHEAD_REACH times the distance it can be drawn at has its parts compiled and made ahead; past
# KEEP_REACH times it for KEEP_SECONDS they are let go, compiled again as it nears (island-117-view.md "Speed",
# part 9) [ours]
AHEAD_REACH, KEEP_REACH, KEEP_SECONDS = 1.25, 1.6, 20.0


@dataclass
class PartDraws:
    """A part's compiled material, its meshes as stored per level of detail (level 0 first) and their bounding
    sphere in the part's space (centre, radius) (``compile_part``)."""

    stages: object  # models.shaderview.DrawStages
    material: gamedraw.Material
    levels: list
    sphere: tuple = (np.zeros(3), 1.0)


@dataclass
class Drawn:
    """One frame's object draws: into the G-buffer, into the cascades, in the transparent pass."""

    gbuffer: list = field(default_factory=list)
    casters: list = field(default_factory=list)
    transparent: list = field(default_factory=list)
    cutouts: list = field(default_factory=list)  # the cut-out volumes': right after the terrain, before the decals
    instances: int = 0
    levels: list = field(default_factory=lambda: [0] * 6)  # instances per level of detail, too small last


class SharedMaps:
    """``files`` whose maps (``.dds``) are read once for every part that names them: a continental island's parts
    held 13 GB of maps, most of them the same files (island-117-view.md "Speed"); ``clear`` lets them go once the
    parts are compiled (each part's own copy goes when its arrays are made)."""

    def __init__(self, files) -> None:
        self.files, self.cache = files, {}
        self._lock = threading.Lock()

    def __getattr__(self, name: str):
        return getattr(self.files, name)

    def read(self, path: str) -> bytes:
        if not path.lower().endswith(".dds"):
            return self.files.read(path)
        with self._lock:
            got = self.cache.get(path)
        if got is None:
            got = self.files.read(path)
            with self._lock:
                got = self.cache.setdefault(path, got)
        return got

    def clear(self) -> None:
        with self._lock:
            self.cache.clear()


@dataclass
class CutOutStages:
    """A cut-out volume's two passes of ``CutOut`` (vertex and pixel shader each): ``mark`` counts the volume's faces
    in front of the terrain into the stencil, ``clear`` (None without ``CUT_OUT_TERRAIN``) sets the depth to the far
    plane where the count is odd; ``water``: ``CUT_OUT_WATER``, whose bits the water tests."""

    mark: tuple
    clear: tuple | None
    water: bool
    refs: tuple
    transparent: bool = False
    shadow: tuple | None = None  # CutOutShadow's two passes: (mark, clear), each (vertex shader, ours) (``states``)
    prepass: tuple | None = None

    def states(self) -> tuple[renderstates.PassState, renderstates.PassState]:
        """The two passes' states as ``sbCutOutMarkStencil`` and ``sbCutOutClearDepth`` set them."""
        bits = CUT_OUT_WATER_BITS if self.water else CUT_OUT_TERRAIN_BITS
        return _count_and_clear(bits, "KEEP" if self.water else "INVERT")

    @staticmethod
    def shadow_states() -> tuple[renderstates.PassState, renderstates.PassState]:
        """``CutOutShadow``'s two passes: the game flips and clears a bit per shadow texel in an array of its own
        (``cShadowCutout``); here the shadow map's stencil holds that bit, counted and cleared alike [ours]."""
        return _count_and_clear(SHADOW_CUT_OUT_BIT, "INVERT")


def _count_and_clear(bits: int, after: str) -> tuple[renderstates.PassState, renderstates.PassState]:
    """A cut-out's count of its faces in front of the depth (``bits`` flipped, the depth kept), then the far depth
    written where the count is odd (the bits then ``after``)."""
    mark = renderstates.Stencil("ALWAYS", "INVERT", bits, bits, bits)
    clear = renderstates.Stencil("EQUAL", after, bits, bits, bits)
    return (renderstates.PassState(depth_write=False, colour=False, stencil=mark),
            renderstates.PassState(depth_always=True, colour=False, stencil=clear))  # fmt: skip


def cutout_stages(source, values: dict) -> CutOutStages:
    """In the background: the cut-out volume's passes compiled with the material's switches (``CUT_OUT_TERRAIN``,
    ``CUT_OUT_WATER``: on by default, ``volume_cutout.fxm``)."""
    from ...models import shadercache

    info = source.game.info(CUT_OUT_FILE)
    if info is None:
        raise FileNotFoundError(f"{shadercache.FX_DIR}{CUT_OUT_FILE}.fx")
    switches = shadercache.material_switches(info.refs, source.game.materials, values)
    path = f"{shadercache.FX_DIR}{CUT_OUT_FILE}.fx"

    def stage(function: str, technique: str = "CutOut"):
        return source.pass_stages(path, switches, f"{technique}/{function}")[0]

    on = {k: values.get(k, "1") not in ("0", "false", "False") for k in ("CUT_OUT_TERRAIN", "CUT_OUT_WATER")}
    ps = stage("psCutOut")
    clear = (stage("vsCutOutClearDepth"), ps) if on["CUT_OUT_TERRAIN"] else None
    shadow = None
    if on["CUT_OUT_TERRAIN"]:  # the terrain's shadow cut where its depth is [I: the technique has no switch]
        own = source.own_stage(*SHADOW_CUT_OUT_PS)
        shadow = ((stage("vsCutOutMarkStencilShadow", "CutOutShadow"), own),
                  (stage("vsCutOutClearDepthShadow", "CutOutShadow"), own))  # fmt: skip
    return CutOutStages((stage("vsCutOutMarkStencil"), ps), clear, on["CUT_OUT_WATER"], info.refs, shadow=shadow)


def part_key(part: dict) -> tuple:
    """What a part's draws depend on: its mesh file, its material number and the material's fields."""
    return part["mesh"], part.get("material"), tuple(sorted(part.get("values", {}).items()))


def _level_meshes(files, path: str, numbers: list[int] | None, strict: bool = False) -> list:
    """The meshes of one level's file cut to the material ``numbers`` (None: whole); [] when it cannot be read
    (``strict``: it raises)."""
    from ...formats import rdmglb, rdmread

    try:
        meshes = rdmread.read_rdm(files.read(path)).meshes
    except Exception:  # a coarser level that does not read: the finer one stands in
        if strict:
            raise
        return []
    out = []
    for m in meshes:
        if not (m.vertex_count and m.indices):
            continue
        cut = (
            [rdmglb.material_indices(m, k) for k in numbers]
            if numbers is not None
            else [rdmglb.material_indices(m, None)]
        )
        indices = [i for part in cut for i in part]
        if indices:
            out.append(gamedraw.Mesh.of_rdm(m, indices))
    return out


def compile_part(source, files, part: dict) -> PartDraws:
    """In the background: a part's material compiled by its effect (``ShaderSource.draw_stages``), its maps read from
    ``files`` (the mod's over the game's), its mesh file's levels of detail (``_lodN.rdm``) cut to the part's material
    (``MaterialLODInfos``); ``NoGBufferPass`` for a part the game's model shaders do not draw (no shader id known, no
    geometry)."""
    from ...models import configtree, shaderview
    from ...sources.assetdb import number

    values = part.get("values") or {}
    shader_id = number(values.get("ShaderID"), -1)
    if shader_id == CUT_OUT_VOLUME:  # its hole in the terrain, drawn after the terrain (``Drawn.cutouts``)
        stages = cutout_stages(source, values)
    else:
        stem = (shaderview.effect_of(shader_id) or shaderview.NATURE_FILES.get(shader_id)) if values else None
        if stem is None:
            raise shaderview.NoGBufferPass("not a model of the game's shaders")
        stages = source.draw_stages(stem, values)
    levels: list = []
    for path in configtree.lod_files(part["mesh"], files.exists):
        numbers = cull.level_numbers(part.get("lod_materials") or [], part.get("material"), len(levels))
        found = _level_meshes(files, path, numbers, strict=not levels)
        levels.append(found or levels[-1])
    if not levels or not levels[0]:
        raise shaderview.NoGBufferPass("no geometry for the material")
    boxes = [m.box() for m in levels[0]]
    lo, hi = np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)
    sphere = ((lo + hi) / 2, float(np.linalg.norm(hi - lo) / 2))
    return PartDraws(stages, source.material(stages.refs, values, files), levels, sphere)


def part_worlds(mats: np.ndarray, part_matrix: np.ndarray) -> np.ndarray:
    """The places ([n, 4, 4], column vectors, the view's axes) of a part at the instances ``mats``."""
    return (mats @ np.asarray(part_matrix, np.float32)).astype(np.float32)


def model_sphere(batch) -> tuple:
    """The bounding sphere (centre, radius) of a batch's model in its own space, from the box of its parts."""
    if batch.box is None:
        return np.zeros(3), float(batch.radius)
    lo, hi = (np.asarray(v, np.float32) for v in batch.box)
    return (lo + hi) / 2, float(np.linalg.norm(hi - lo) / 2)


def spheres(worlds: np.ndarray, sphere: tuple) -> tuple[np.ndarray, np.ndarray]:
    """The instances' bounding spheres (centres [n, 3], radii [n]) from the part's own (``PartDraws.sphere``)."""
    centre, radius = sphere  # one for all, or one per instance ([n, 3], [n])
    centre = np.broadcast_to(np.asarray(centre, np.float32), (len(worlds), 3))
    centres = np.einsum("nij,nj->ni", worlds[:, :3, :3], centre) + worlds[:, :3, 3]
    scale = np.sqrt(np.einsum("nij,nij->nj", worlds[:, :3, :3], worlds[:, :3, :3]).max(axis=1))
    return centres, radius * scale


class _OfBatches:
    """A table made of shown batches, made again when one of them, its instances or its model's box changed."""

    def __init__(self, batches: list) -> None:
        self.key = [(b, b.cells, b.box) for b in batches]

    def same(self, batches: list) -> bool:
        """Whether ``batches`` are the ones it was made of, with the same instances and models."""
        return len(batches) == len(self.key) and all(
            b is k[0] and b.cells is k[1] and b.box is k[2] for b, k in zip(batches, self.key, strict=True)
        )


class FrameTable(_OfBatches):
    """Every shown batch's instances in one table (``batches``: their places in cell order, each one's batch, its
    bounding sphere, whether it is shown), made again when a batch or its instances change: a frame culls them and
    picks their levels and cascades in a few array operations over all of them (``pick``), not a few per batch
    (island-117-view.md "Speed", part 4)."""

    def __init__(self, batches: list) -> None:
        self.batches = batches
        super().__init__(batches)
        cells = [b.cells for b in batches]
        rows = np.concatenate([c.rows for c in cells]) if cells else np.zeros((0, 16), np.float32)
        self.mats = rows.reshape(-1, 4, 4).transpose(0, 2, 1)  # column vectors, the view's axes
        counts = [len(c.rows) for c in cells]
        self.batch = np.repeat(np.arange(len(batches)), counts).astype(np.int64)
        self.cell_counts = np.concatenate([c.counts for c in cells]) if cells else np.zeros(0, int)
        self.shown = np.abs(self.mats[:, :3, :3]).reshape(len(rows), 9).max(axis=1) > 0  # hidden: scale 0
        # each instance its model's sphere, all in one pass (per batch it was a loop on the window's thread)
        models = [model_sphere(b) for b in batches]
        centre = np.repeat(np.array([c for c, _r in models], np.float32).reshape(-1, 3), counts, axis=0)
        radius = np.repeat(np.array([r for _c, r in models], np.float32), counts)
        self.centres, self.radii = spheres(self.mats, (centre, radius))

    def visible(self, seen: np.ndarray) -> np.ndarray:
        """The instances (their indices, in table order) of the cells ``seen`` marks (every batch's cells in turn),
        hidden ones left out."""
        return np.flatnonzero(np.repeat(seen, self.cell_counts) & self.shown)

    def runs(self, index: np.ndarray, levels: np.ndarray) -> tuple[np.ndarray, dict[int, list[tuple[int, int, int]]]]:
        """The order that sorts the instances ``index`` at ``levels`` (0 or more) by batch and level, keeping their
        cell order within each, and per batch its runs (level, start, end) in that order."""
        keys = self.batch[index] * 8 + levels
        order = np.argsort(keys, kind="stable")
        keys = keys[order]
        cuts = np.flatnonzero(np.diff(keys)) + 1
        out: dict[int, list] = {}
        for a, z in zip(np.r_[0, cuts].tolist(), np.r_[cuts, len(keys)].tolist(), strict=True):
            if z > a:
                out.setdefault(int(keys[a]) // 8, []).append((int(keys[a]) % 8, a, z))
        return order, out


class Reach(_OfBatches):
    """How near each shown batch's instances lie to the eye against the distance its model can be drawn at
    (``cull.drawn_within``), for all batches in a few array operations over their cells' boxes; made again when the
    batches change."""

    def __init__(self, batches: list) -> None:
        super().__init__(batches)
        cells = [b.cells for b in batches]
        counts = [len(c.lo) for c in cells]
        self.starts = np.r_[0, np.cumsum(counts)[:-1]].astype(np.int64) if all(counts) else None  # each its run
        self.batch = np.repeat(np.arange(len(batches)), counts)
        self.lo = np.concatenate([c.lo for c in cells]) if cells else np.zeros((0, 3))
        self.hi = np.concatenate([c.hi for c in cells]) if cells else np.zeros((0, 3))
        models = [(model_sphere(b), c.scale) for b, c in zip(batches, cells, strict=True)]
        self.radius = np.array([r * s for (_c, r), s in models], float)  # the model's sphere at its largest scale
        # how far an instance's sphere reaches past its place (the origin the cells' boxes are made of)
        reach = [(float(np.linalg.norm(c)) + r) * s for (c, r), s in models]
        self.extent = np.repeat(np.asarray(reach, float), counts)

    def ratios(self, eye, proj: np.ndarray, size: tuple[int, int], rules: cull.LodRules) -> np.ndarray:
        """Per batch its nearest instance's distance from ``eye`` (the view's axes) over the distance its model can be
        drawn at: 1 or less, it can be drawn from here as the camera turns."""
        eye = np.asarray(eye, float)
        outside = np.maximum(self.lo - eye, 0.0) + np.maximum(eye - self.hi, 0.0)
        gap = np.maximum(np.linalg.norm(outside, axis=1) - self.extent, 0.0)
        if self.starts is not None and len(gap):
            nearest = np.minimum.reduceat(gap, self.starts)
        else:  # a batch without a cell
            nearest = np.full(len(self.radius), np.inf)
            np.minimum.at(nearest, self.batch, gap)
        return nearest / np.maximum(cull.drawn_within(self.radius, proj, size, rules), 1e-6)


class AheadRun(InstanceRun):
    """A draw of no instance that makes a part's pass on the device before the camera reaches it: its maps, its
    binding set, the mesh's buffers and the pipeline a first draw would make in its frame (island-117-view.md "Speed",
    part 8)."""

    def __init__(self, setup: PartPass, mesh: gamedraw.Mesh, shadow: bool = False) -> None:
        super().__init__(
            setup, mesh, 0, np.zeros((0, 4, 4), np.float32), np.zeros((0, 4), np.uint32) if shadow else None
        )

    def draw(self, cb, size: tuple[int, int], instances: int | None = None) -> None:
        """Nothing: it is made, not drawn."""

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        return np.zeros(3), np.zeros(3)


@dataclass
class _Level:
    """A level of detail of a compiled part: its meshes and their bones."""

    meshes: list
    joints: list


class _Part:
    """A compiled part on the device: per pass (the G-buffer's or the transparent's, the depth prepass, the shadow)
    one ``PartPass`` for all its levels and meshes, the meshes' buffers once for every pass; a frame's draws are
    ``InstanceRun``s of them over the frame's instances (``frame``)."""

    cutout = False  # a cut-out volume's two passes (``CutOutStages``): drawn right after the terrain

    def __init__(self, rhi, got: PartDraws, programs: dict, textures: dict, pool: MapPool,
                 frame: FrameInstances) -> None:  # fmt: skip
        self.rhi, self.got, self.frame = rhi, got, frame
        self.maps: list = []  # the arrays its passes bind (one per file, from ``pool``), made by the first that draws
        self.table = PooledTable(got.material, pool)  # their slots, shared by every pass
        st = got.stages
        self.transparent, self.cutout = st.transparent, isinstance(st, CutOutStages)
        if self.cutout:  # the stencil's count, then the depth cleared where it is odd; in the shadow map alike
            roles = {"main": (st.mark, st.states()[0]), "clear": (st.clear, st.states()[1])}
            if st.shadow is not None:  # drawn once per cascade (``cCurrentCascade``), as the engine draws it
                for k in range(CASCADES):
                    for role, shaders, how in zip(("mark", "clear"), st.shadow, st.shadow_states(), strict=True):
                        roles[f"shadow_{role}{k}"] = (shaders, how)
        else:
            state = renderstates.pass_state(st.ps.state) if st.transparent else renderstates.GBUFFER_STATE
            if st.prepass is not None:  # its depth first, then the G-buffer where it lies (the cut-outs)
                state = renderstates.PassState(depth_write=False, depth_equal=True)
            roles = {"main": ((st.vs, st.ps), state), "prepass": (st.prepass, DEPTH_ONLY),
                     "shadow": (st.shadow, SHADOW)}  # fmt: skip
        inputs = PartInputs(got.material, frame, textures, self.maps, self.table, self._mesh)
        self.passes = {role: PartPass(rhi, _program(programs, *shaders), how, inputs)
                       for role, (shaders, how) in roles.items() if shaders is not None}  # fmt: skip
        for role, setup in self.passes.items():
            if role.startswith("shadow_"):
                setup.values["cCurrentCascade"] = int(role[-1])
        self.levels = [_Level(ms, [m.bone_count(4) for m in ms]) for ms in got.levels]
        self._meshes: dict[int, tuple] = {}  # id of a mesh -> (vertex buffer, index buffer, the mesh)
        self.made: set[str] = set()  # the passes made ahead of their first draw (``ahead``)
        self.begin()

    def begin(self) -> None:
        """A new frame: no draws yet (``models``)."""
        self.runs: list[InstanceRun] = []

    def models(self, level: int, worlds: np.ndarray, shadows: bool, mask: np.ndarray | None = None,
               deform: bool = False) -> tuple:  # fmt: skip
        """This frame's draws of ``worlds`` at ``level`` (and their casters when ``shadows``, into the cascades
        ``mask`` marks: None all), their instances placed in the frame's buffers (``deform``: bent to the terrain)."""
        out, cast = [], []
        lv = self.levels[min(level, len(self.levels) - 1)]
        prepass, shadow, clear = self.passes.get("prepass"), self.passes.get("shadow"), self.passes.get("clear")
        reach = mask if mask is not None else np.ones((len(worlds), CASCADES), bool)
        for mesh, joints in zip(lv.meshes, lv.joints, strict=True):
            slot = self.frame.place(worlds, joints, deform)
            if prepass is not None:  # its depth first
                out.append(InstanceRun(prepass, mesh, slot, worlds))
            out.append(InstanceRun(self.passes["main"], mesh, slot, worlds))
            if clear is not None:  # a cut-out's second pass
                out.append(InstanceRun(clear, mesh, slot, worlds))
            if shadows and shadow is not None:
                at, words = self.frame.place_casters(worlds, joints, reach, deform)
                if len(words):
                    cast.append(InstanceRun(shadow, mesh, at, worlds, words))
            elif shadows and self.cutout:
                cast += self._cut_casters(mesh, joints, worlds, reach, deform)
        self.runs += out + cast
        return out, cast

    def _cut_casters(self, mesh, joints: int, worlds: np.ndarray, reach: np.ndarray, deform: bool) -> list:
        """A cut-out volume's count and clear in each cascade it reaches: its shadow shaders place it by the draw's
        cascade (``cCurrentCascade``), not by the instance's word as the models' do."""
        out = []
        for k in range(CASCADES):
            mark, clear = self.passes.get(f"shadow_mark{k}"), self.passes.get(f"shadow_clear{k}")
            only = np.zeros_like(reach)
            only[:, k] = reach[:, k]
            if mark is None or clear is None or not only.any():
                continue
            at, words = self.frame.place_casters(worlds, joints, only, deform)
            quarter = gameshadow.DIVISIONS[k]  # the engine's viewport for the cascade: the shaders do not place it
            out += [InstanceRun(setup, mesh, at, worlds, words, quarter) for setup in (mark, clear)]
        return out

    def ahead(self, shadows: bool) -> tuple[list, list, list]:
        """Draws of no instance (``AheadRun``) that make each pass not made yet with every level's meshes: (into the
        G-buffer, transparent, casters); the shadow pass only when the frame casts."""
        meshes = list({id(m): m for lv in self.levels for m in lv.meshes}.values())
        out: tuple[list, list, list] = ([], [], [])
        for role, setup in self.passes.items():
            cast = role.startswith("shadow")  # a pass into the cascades
            if role in self.made or (cast and not shadows):
                continue
            self.made.add(role)
            into = out[2] if cast else out[1] if role == "main" and self.transparent else out[0]
            into.extend(AheadRun(setup, m, cast) for m in meshes)
        self.runs += [*out[0], *out[1], *out[2]]
        return out

    def _mesh(self, mesh: gamedraw.Mesh, batch) -> tuple:
        """A mesh's vertex and index buffers, uploaded once for every pass (its vertex bytes let go after)."""
        got = self._meshes.get(id(mesh))
        if got is None:
            usage = QRhiBuffer.UsageFlag
            vertices = gamedraw.immutable_buffer(self.rhi, batch, usage.VertexBuffer, mesh.data)
            indices = gamedraw.immutable_buffer(
                self.rhi, batch, usage.IndexBuffer, mesh.indices.astype("<u4").tobytes()
            )
            got = self._meshes[id(mesh)] = (vertices, indices, mesh)
            mesh.data = b""
        return got[0], got[1]

    def all(self) -> list:
        """Its passes and this frame's draws (a failed draw is found among them)."""
        return [*self.passes.values(), *self.runs]

    def release(self) -> None:
        for setup in self.passes.values():
            setup.release()
        for vertices, indices, _mesh in self._meshes.values():
            vertices.destroy()
            indices.destroy()
        self._meshes = {}
        self.table.release()
        self.maps.clear()
        self.made = set()
        self.begin()


def _unmade(part: _Part, shadows: bool) -> bool:
    """Whether a pass of ``part`` (the shadow's only when the frame casts) was not made ahead yet: a pass already
    drawn may still meet a coarser level's mesh of another vertex layout (a pipeline of its own)."""
    return any(role not in part.made for role in part.passes if shadows or not role.startswith("shadow"))


def _program(programs: dict, vs, ps) -> gamedraw.Program:
    """One program per pair of compiled shaders, shared by every part that compiles to them."""
    key = (vs.entry, vs.blob, ps.entry, ps.blob)
    if key not in programs:
        programs[key] = gamedraw.Program(vs, ps)
    return programs[key]


class GameObjects:
    """The island's objects by the game's model shaders (module docstring)."""

    def __init__(self, island) -> None:
        self.island, self.r = island, island.r
        self.parts: dict[tuple, _Part | Exception] = {}
        self.programs: dict = {}
        self.jobs: dict[tuple, str] = {}  # part key -> its loader key
        self.keys: dict[int, tuple] = {}  # id of a view part -> (the part, its key)
        self.drawn: set[tuple] = set()
        self.fallback: set[str] = set()  # the models (configs) with a part the view's own look draws
        self.waiting = 0
        self.neutral: dict | None = None  # the session maps an island view has none of, as the shaders read "none"
        self.rules: cull.LodRules | None = None  # the game's level-of-detail rules, once read
        self.files: SharedMaps | None = None  # the editor's files, the maps read once for all parts
        self.pool: MapPool | None = None  # the maps on the device, once per file for all parts
        self.instances: FrameInstances | None = None  # the frame's instance data of every draw
        self.failures = 0  # parts sent back to the view's look (a frame's draws are made again)
        self.ahead_left = 0  # compiled parts within reach not yet made on the device (``_ahead``)
        self._made = 0.0  # this frame's time on the device side of new parts (``MAKE_BUDGET``)
        self.adapt = False  # the frame hands the session's terrain: the models adapt to it (``FrameInstances``)
        self.near_at: dict[str, float] = {}  # model -> when it was last within ``KEEP_REACH`` (``_near``)
        self._kept: list[str] = []  # the models within ``KEEP_REACH`` at the last frame worked out
        self.users: dict[tuple, set[str]] = {}  # part key -> the models it was asked for by (``_let_go``)
        self._swept = 0.0  # when ``_let_go`` last looked
        self._last: tuple = (None, Drawn())  # the last frame's key and draws (``_frame_key``)
        self._frame_table: FrameTable | None = None  # every shown batch's instances (``_draws``)
        self._reach: Reach | None = None  # every shown batch's cells against the camera (``_near``)

    def _key(self, part: dict) -> tuple:
        got = self.keys.get(id(part))
        if got is None or got[0] is not part:
            got = self.keys[id(part)] = (part, part_key(part))
        return got[1]

    def _part(self, part: dict, source):
        """The device side of a view part: its ``_Part``, an exception (the view draws it) or None while it compiles."""
        key = self._key(part)
        found = self.parts.get(key)
        if found is not None:
            return found
        job = self.jobs.setdefault(key, JOB.format(len(self.jobs)))
        if self.files is None:
            self.files = SharedMaps(self.r.editor.files)
        files = self.files
        got = self.r.loader.get(job, lambda: compile_part(source, files, part))
        if got is None:
            return None
        if isinstance(got, Exception):
            self.parts[key] = got
            if type(got).__name__ != "NoGBufferPass":
                self.r._message(tr("islands.mesh_games_shader_reason", mesh=part["mesh"],
                                   reason=f"{type(got).__name__}: {got}"[:300]))  # fmt: skip
            return got
        terrain = getattr(self.island, "terrain", None)  # its session maps: grit, wetness, wildfire read as none
        textures = ChainMap(self.neutral or {}, terrain.textures if terrain is not None else {})
        if self.pool is None:
            q = getattr(self.island, "quality", None)
            self.pool = MapPool(self.r.ctx.rhi, q.max_texture if q is not None else 0)
            self.instances = FrameInstances(self.r.ctx.rhi)
            self.instances.adapt = self.adapt
        if self._made > MAKE_BUDGET:
            return None  # made in a later frame (a continental island's came in one frame, seconds)
        started = time.perf_counter()
        self.parts[key] = _Part(self.r.ctx.rhi, got, self.programs, textures, self.pool, self.instances)
        self._made += time.perf_counter() - started
        return self.parts[key]

    def frame(self, f, opts, shadows: bool, size: tuple[int, int] = (1, 1), cascades=None) -> Drawn:
        """The draws of every shown batch's parts the game's shaders draw (``drawn`` names them), the instances in
        view at the level of detail the game picks for ``size`` (too small: not drawn), their casters into the
        cascades they reach (``cascades``: a ``gameshadow.Cascades`` of the last frames; None: into all); the markers,
        the part under the cursor and the grass far off stay the view's."""
        source = getattr(self.island, "source", None)
        if source is None or self.r.editor is None:
            self.drawn, self.waiting = set(), 0
            return Drawn()
        self._neutral()
        # the quality of this frame (a Custom bias applies at once), not the one the look was made for
        rules = lod_rules(getattr(opts, "quality", None), self._rules())
        proj = f.view_proj @ np.linalg.inv(f.view)
        known = cascades if cascades is not None and len(cascades.cascades) else None
        batches = [b for b in self.r.batches.values() if self._shown(b, opts, f)]
        key = self._frame_key(f, proj, size, shadows, known, batches), rules, self.adapt
        still = key == self._last[0]
        self._let_go(self._kept if still else ())  # a still camera keeps what is near it: the last frame drew it
        if still:  # the camera stands and nothing changed: the last frame's draws and instances
            return self._last[1]
        out, self.drawn, self.waiting = Drawn(), set(), 0
        self._made = 0.0
        if self.instances is not None:
            self.instances.begin()
            self.instances.adapt = self.adapt
        for part in self.parts.values():
            if isinstance(part, _Part):
                part.begin()
        drawing, fallback, near_parts = [], set(), {}
        cold = getattr(self.island, "cold", False)
        near = self._near(batches, f, proj, size, rules)
        for b in near:
            built, seen = [], {}
            for k, (p, _vaos) in enumerate(b.vaos):
                if id(p) not in seen:
                    self.users.setdefault(self._key(p), set()).add(b.key[1])
                part = seen[id(p)] if id(p) in seen else self._part(p, source)
                if isinstance(part, _Part):
                    self.drawn.add((b.key, k))
                    near_parts[id(part)] = part
                    if id(p) not in seen:  # a part of several meshes is one game part: drawn once
                        built.append((p, part))
                elif part is not None or cold or b.key[1] in self.fallback:
                    fallback.add(b.key[1])  # the view's look draws it; also while it compiles on a first start or anew
                self.waiting += part is None and id(p) not in seen
                seen[id(p)] = part
            if built:
                drawing.append((b, built))
        # a model not looked at this frame keeps its look: its batch's parts are made again for a frame when it
        # changes looks, and leaving it out made it flip between the looks every frame (its meshes loaded and let go)
        self.fallback = fallback | (self.fallback - {b.key[1] for b in near})
        if drawing:
            self._draws(out, drawing, f, (f.view, proj, size, rules), shadows, known)
        self._ahead(out, shadows, list(near_parts.values()))
        if not self.waiting and self.files is not None:  # every part near compiled: the shared reads are done with
            self.files.clear()
        # while parts compile or are made ahead the next frame asks again: a still camera would never take them in
        self._last = (key if not self.waiting and not self.ahead_left else None, out)
        return out

    def _near(self, batches: list, f, proj: np.ndarray, size: tuple[int, int], rules: cull.LodRules) -> list:
        """The batches within ``AHEAD_REACH`` of the camera (their parts compiled, made and drawn); the models of
        those within ``KEEP_REACH`` marked as near now (``_let_go``)."""
        if self._reach is None or not self._reach.same(batches):
            self._reach = Reach(batches)
        ratios = self._reach.ratios(f.eye, proj, size, rules).tolist()
        self._kept = [b.key[1] for b, ratio in zip(batches, ratios, strict=True) if ratio <= KEEP_REACH]
        self.near_at.update(dict.fromkeys(self._kept, time.monotonic()))
        return [b for b, ratio in zip(batches, ratios, strict=True) if ratio <= AHEAD_REACH]

    def _let_go(self, kept=()) -> None:
        """The parts no model has been near for ``KEEP_SECONDS`` (``kept``: models near now) let go: their device data,
        maps and compiled draws (compiled again, from the build cache, when one nears); a compile finished for a part
        no longer near too. Looked at once a second."""
        now = time.monotonic()
        if now - self._swept < 1.0:
            return
        self._swept = now
        self.near_at.update(dict.fromkeys(kept, now))
        old = {m for m, at in self.near_at.items() if now - at > KEEP_SECONDS}
        for m in old:
            del self.near_at[m]
        for key in [k for k, models in self.users.items() if not any(m in self.near_at for m in models)]:
            part = self.parts.get(key)
            if isinstance(part, _Part):
                part.release()
                del self.parts[key]
            job = self.jobs.get(key)
            if key not in self.parts and job is not None and not self.r.loader.forget_done(job):
                continue  # still compiling: let go once it is in
            del self.users[key]

    def _ahead(self, out: Drawn, shadows: bool, near: list) -> None:
        """``AHEAD`` compiled parts within reach (``near``) not made on the device yet made in this frame by draws of
        no instance (a part the frame draws is made by its draws); ``ahead_left`` counts the rest."""
        left = [p for p in near if _unmade(p, shadows)]
        for part in left[:AHEAD]:
            gbuffer, transparent, casters = part.ahead(shadows)
            out.gbuffer.extend(gbuffer)
            out.transparent.extend(transparent)
            out.casters.extend(casters)
        self.ahead_left = max(0, len(left) - AHEAD)

    def upload(self, batch) -> None:
        """The frame's instances into their buffers, before the passes (``desktopgame``; nothing new at a still
        camera)."""
        if self.instances is not None:
            self.instances.upload(batch)

    def _shown(self, b, opts, f) -> bool:
        if b.key[0] in ("markers", "ghost") or not b.count or not b.parts or b.cells is None:
            return False
        return self.r._layer_on(b, opts) and not (b.key[0] == "grass" and f.distance > GRASS_FAR)

    def _frame_key(self, f, proj, size, shadows: bool, cascades, batches: list) -> tuple:
        """What a frame's draws depend on: the camera, the size, the cascades, the batches shown and their instances
        (the objects themselves, not their ids: a freed one's id is soon another's), the parts sent back to the view;
        a frame with parts still compiling is never repeated (``frame``)."""
        known = tuple(c.matrix.tobytes() for c in cascades.cascades) if cascades is not None else None
        camera = (np.asarray(f.view).tobytes(), np.asarray(proj).tobytes(), tuple(size), shadows, known)
        return camera, tuple((b, b.version, b.parts) for b in batches), self.failures

    def _in_view(self, b, f) -> np.ndarray:
        """Which of a batch's cells hold instances in view and not too small to draw: the view's own codes of this
        frame for every batch at once (``Renderer._pass_codes``), the batch's own culling where it has none."""
        if f.planes is None:
            return np.ones(len(b.cells.counts), bool)
        table, index, every = self.r._pass_codes("model", f.planes, f.lod)
        k = index.get(id(b))
        return table.of(every, k) >= 0 if k is not None else b.cells.visible(f.planes, b.reach)

    def _table(self, batches: list) -> FrameTable:
        if self._frame_table is None or not self._frame_table.same(batches):
            self._frame_table = FrameTable(batches)
        return self._frame_table

    def _draws(self, out: Drawn, drawing: list, f, camera: tuple, shadows: bool, cascades) -> None:
        """The instances in view of every batch of ``drawing`` ((batch, its built parts)) by level of detail into
        ``out``, worked out for all of them at once (``FrameTable``): the level per instance of the model, as the game
        picks it (its bounding sphere: all its parts switch together; rendering/camera.md 3), each part drawn at it (at
        its own coarsest where it has fewer levels), its casters into the cascades the model reaches."""
        table = self._table([b for b, _built in drawing])
        index = table.visible(np.concatenate([self._in_view(b, f) for b, _built in drawing]))
        levels = cull.lod_levels(table.centres[index], table.radii[index], *camera, 5)
        masks = None
        if shadows and cascades is not None:
            frame = getattr(self.island, "frame", None)
            texels = (frame.shadow_size if frame is not None else gameshadow.SIZE) // 2  # a cascade: a quarter
            used = frame.shadow_cascades if frame is not None else gameshadow.COUNT
            masks = gameshadow.cascade_mask(cascades, table.centres[index], table.radii[index], texels=texels,
                                            used=used)  # fmt: skip
        counts = np.bincount(levels + 1, minlength=6)
        out.levels = [*counts[1:].tolist(), int(counts[0])]
        out.instances = int(counts[1:].sum())
        keep = levels >= 0
        index, levels = index[keep], levels[keep]
        order, runs = table.runs(index, levels)
        mats = table.mats[index[order]]
        masks = masks[keep][order] if masks is not None else None
        cut: list = []  # the cut-out volumes' casters
        for k, (_b, built) in enumerate(drawing):
            got = runs.get(k)
            if not got:
                continue
            a, z = got[0][1], got[-1][2]  # the batch's instances, level by level
            for p, part in built:
                worlds = part_worlds(mats[a:z], p["matrix"])
                for level, s, e in got:
                    masked = masks[s:e] if masks is not None else None
                    draws, cast = part.models(level, worlds[s - a : e - a], shadows, masked, bool(p.get("deform")))
                    into = out.transparent if part.transparent else out.cutouts if part.cutout else out.gbuffer
                    into.extend(draws)
                    (cut if part.cutout else out.casters).extend(cast)
        out.casters[:0] = cut  # the terrain's shadow cleared inside the volumes before any model casts into it

    def _rules(self) -> cull.LodRules:
        """The game's level-of-detail rules, read once in the background (vanilla's until then, or without them)."""
        if self.rules is None:
            files = getattr(self.r.editor.files, "game", self.r.editor.files)

            def read() -> cull.LodRules:
                return cull.read_lod_rules(files.read(ENGINE_SETTINGS).decode("utf-8", errors="replace"))

            got = self.r.loader.get(RULES_JOB, read)
            if got is None:
                return cull.LodRules()
            self.rules = got if isinstance(got, cull.LodRules) else cull.LodRules()
        return self.rules

    def _neutral(self) -> None:
        """The terrain's tint as none (mid grey: the shaders decode it as YCC, 0.5 is no tint), made once."""
        if self.neutral is not None:
            return
        self.neutral = {}
        rhi = self.r.ctx.rhi

        def make(batch) -> None:
            self.neutral["cTerrainTintTex"] = gamedraw.stand_in(rhi, "texture", "Texture2D", batch, 0.5)

        self.r.ctx.native_pass(lambda cb, batch: carry(cb, batch, make))

    def take_failures(self) -> None:
        """A draw that did not prepare in the last frame sends its part to the view's look (said once)."""
        ctx = self.r.ctx
        failed = getattr(ctx, "failed_models", None)
        if not failed:
            return
        owners = {id(m): key for key, part in self.parts.items() if isinstance(part, _Part) for m in part.all()}
        mine = [(m, e) for m, e in failed if id(m) in owners]
        if not mine:
            return
        ctx.failed_models[:] = [x for x in failed if all(x is not y for y in mine)]
        for model, error in mine:
            key = owners[id(model)]
            part = self.parts.get(key)
            if isinstance(part, _Part):
                part.release()
                self.parts[key] = RuntimeError(error)
                self.failures += 1
                self.r._message(tr("islands.mesh_games_shader_reason", mesh=key[0], reason=error[:300]))

    def release(self) -> None:
        for part in self.parts.values():
            if isinstance(part, _Part):
                part.release()
        for tex in (self.neutral or {}).values():
            self.r.ctx.later(tex)
        if self.pool is not None:
            self.pool.release()
            self.instances.release()
        self.neutral, self.pool, self.instances = None, None, None
        for job in self.jobs.values():  # compiled again when asked: the pool took their maps
            self.r.loader.forget(job)
        self.parts, self.keys, self.drawn, self.jobs, self.files = {}, {}, set(), {}, None
        self._frame_table, self._reach, self.near_at, self.users, self._kept = None, None, {}, {}, []
        self._last = (None, Drawn())
