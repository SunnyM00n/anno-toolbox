"""The island's distance clusters in the island view's look "The game's own shaders", as Anno 117 draws far objects:
per region the static instances of every model with a far-view model merged into one mesh in the background
(``render.distanceclusters``), again where an edit settled; past the game's ``ClusterStartDistance`` a region's
mesh fades in over its objects, then stands for them (``render.clustersnative``).

``CGameClusters(island)`` is the entry, one per ``desktopgameobjects.CGameObjects``: ``update(batches, options)``
before the frame's key, ``frame(...)`` the draws, ``hidden(table, frame)`` the instances they replace (the
rules: docs/rendering/distance-clusters.md)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ...infra.i18n import tr
from ...render import gameshadow
from ...render.distanceclusters import CClusterAtlas, CFarViewError, SPiece, merge, read_far_view, region_of
from ...render.quality import game_levels

FAR_JOB, BUILD_JOB = "game-cluster-far:{}", "game-cluster-build:{}"
SHADERS_JOB, DISTANCES_JOB = "game-cluster-shaders", "game-cluster-distances"
ENGINE_SETTINGS = "data/base/config/engine/enginesettings/default.xml"  # ClusterStartDistance per quality level
KINDS = ("objects", "props")  # the batches merged; the view draws its grass only near (``GRASS_FAR``)
# the regions' sides per level [I]: level 0's 16 (the vertex's 8 bits step 0.06, half a pixel at the start
# distance); level L's twice the one below, drawn from 2^L times the start distance, where its steps are as small
# on screen (distance-clusters.md "Regions")
LEVELS_M = (16.0, 32.0, 64.0, 128.0)
FADE_M = 20.0  # the cross-fade's width past the start distance [I]: the engine's window is not read
SETTLE_S = 1.0  # a region an edit touched is built again once it has not changed for this long [I]
# vanilla 117's ClusterStartDistance per quality level (enginesettings/default.xml), until the file is read
VANILLA_START = (180.0, 200.0, 200.0, 220.0, 240.0)
ANIMATED_SHADERS = (7, 28)  # water and banner parts move on the view's clock: their models stay individual
MAX_BUILDS = 2  # region builds in flight at once [ours]: the view's workers also compile parts


@dataclass
class _SMembers:
    """A merged batch's instances: its matrices (entry order, to find what an edit moved), each row's region (cell
    order, as the frame's table holds them), the far-view model's material indices."""

    matrices: np.ndarray
    regions: np.ndarray  # [n, levels]: each row's region of each level (``region_keys``)
    cells: object
    materials: np.ndarray


@dataclass
class _SRegion:
    """A region: its member batches, when it last changed, its build in flight and its mesh on the device."""

    batches: set = field(default_factory=set)
    changed_s: float = 0.0
    version: int = 0
    built: int = -1  # the version its mesh is of
    job: str = ""
    job_version: int = 0
    mesh: object = None
    parent: int = -1  # the region of the next level that holds it


@dataclass
class SClusterFrame:
    """One frame's clusters: the G-buffer's and the cascades' runs, the regions that replace their objects, counts."""

    gbuffer: list = field(default_factory=list)
    casters: list = field(default_factory=list)
    full: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))  # their dense numbers (``hidden``)
    drawn: int = 0
    triangles: int = 0


def region_keys(positions: np.ndarray) -> np.ndarray:
    """[n, levels]: the region of each level of each place ([n, 3], the view's space), one integer each (the level in
    its lowest two bits)."""
    out = np.empty((len(positions), len(LEVELS_M)), np.int64)
    for level, side_m in enumerate(LEVELS_M):
        cells = region_of(positions, side_m)
        out[:, level] = (cells[:, 0] * 100003 + cells[:, 1]) * 4 + level
    return out


def level_of(keys):
    """The level of a region key (``region_keys``), or of an array of them."""
    return keys & 3


def box_distances(eye: np.ndarray, centres: np.ndarray, scales: np.ndarray) -> np.ndarray:
    """The distance from ``eye`` to the nearest point of each box (centres and half extents, [n, 3])."""
    return np.linalg.norm(np.maximum(np.abs(np.asarray(eye, float) - centres) - scales, 0.0), axis=1)


def fade_of(distances_m: np.ndarray, start_m: float) -> np.ndarray:
    """The regions' ``cClusterAlpha``: 0 before the start distance, 1 from ``FADE_M`` past it."""
    return np.clip((distances_m - start_m) / FADE_M, 0.0, 1.0)


def is_static(batch) -> bool:
    """Whether a batch's model holds still in the view: no part of it moves on the view's clock."""
    return not any(part.get("shader") in ANIMATED_SHADERS or part.get("flow") or part.get("flipbook")
                   for part, _specs in batch.m_parts or ())  # fmt: skip


def _reach(shadows: tuple, centres: np.ndarray, scales: np.ndarray) -> np.ndarray:
    """[n, cascades]: the cascades each region's box casts into (``shadows``: the cascades of the last frames or
    None for every used one, how many are used, a cascade's texels)."""
    cascades, used, texels = shadows
    if cascades is None:
        out = np.zeros((len(centres), gameshadow.COUNT), bool)
        out[:, :used] = True
        return out
    return gameshadow.cascade_mask(cascades, centres, np.linalg.norm(scales, axis=1), texels=texels, used=used)


class CGameClusters:
    """The island's distance clusters (module docstring)."""

    def __init__(self, island) -> None:
        self.m_island, self.m_renderer = island, island.m_renderer
        self.m_atlas = CClusterAtlas()
        self.m_far: dict[str, object] = {}  # model -> its far-view model, None (none to merge) or the material indices
        self.m_members: dict[tuple, _SMembers] = {}
        self.m_regions: dict[int, _SRegion] = {}
        self.m_native = None  # render.clustersnative.CNativeClusters, made with the native objects
        self._m_objects = None  # the native objects it was made for
        self.m_waiting = 0  # far-view models still read, regions still built
        self.m_serial = 0  # counts what changes the frame's draws: a region built or changed, an atlas change
        self.m_error = ""
        self._m_jobs = 0
        self._m_ready: tuple = (None, ())  # (the serial, ``_ready``'s arrays)
        self.m_dense: dict[int, int] = {}  # region key -> a dense number (``hidden``)
        self.m_skipped: dict[tuple, object] = {}  # batch key -> the cells it was found not to merge with
        self._m_rows: tuple = (None, None, None)  # (the frame's table, the serial, its rows' dense regions)

    def is_on(self, options) -> bool:
        return getattr(options, "distance_clusters", False) and not self.m_error

    def update(self, batches: list, options) -> int:
        """The far-view models of the shown batches read, their instances put in regions, the regions built where
        they changed; the serial the frame's key takes."""
        if not self.is_on(options) or not self._device():
            return -1
        self.m_waiting = 0
        now = time.monotonic()
        for batch in batches:
            if batch.m_key[0] in KINDS and batch.m_cells is not None:
                self._sync_batch(batch, now)
        shown = {batch.m_key for batch in batches}
        for key in [key for key in self.m_members if key not in shown]:
            self._leave(key, now)
        self._build(now)
        return self.m_serial

    def _device(self) -> bool:
        """The device side, made with the native objects (again when they were made again): False while the shaders
        are read or when they do not draw natively."""
        objects = getattr(self.m_island, "m_native_objects", None)
        if objects is not self._m_objects:
            self._drop_device()
            self._m_objects = objects
        if objects is None or self.m_native is not None:
            return self.m_native is not None
        from ...render import clustersnative

        natives = getattr(self.m_island, "_m_natives", None)
        found = self.m_renderer.m_loader.get(SHADERS_JOB, lambda: clustersnative.shaders(natives))
        if found is None:
            self.m_waiting += 1
            return False
        try:
            if isinstance(found, Exception):
                raise found
            self.m_native = clustersnative.CNativeClusters(objects, found)
        except Exception as exc:  # pattern: ok (the device side: the objects draw as they did, said once)
            self.m_error = f"{type(exc).__name__}: {exc}"[:300]
            self.m_renderer._message(tr("islands.games_distance_clusters", reason=self.m_error))
            return False
        return True

    def fail(self, error: str) -> None:
        """A cluster draw raised: the clusters stay off for this island view, said once."""
        if not self.m_error:
            self.m_error = str(error)[:300]
            self.m_renderer._message(tr("islands.games_distance_clusters", reason=self.m_error))
        self._drop_device()

    def _drop_device(self) -> None:
        if self.m_native is not None:
            self.m_native.release()
        self.m_native = None
        for region in self.m_regions.values():  # a build in flight would hold a slot of ``MAX_BUILDS`` for good
            if region.job:
                self.m_renderer.m_loader.forget(region.job)
        self._m_jobs = 0
        self.m_atlas = CClusterAtlas()
        self.m_far, self.m_members, self.m_regions, self.m_dense, self.m_skipped = {}, {}, {}, {}, {}
        self._m_rows = (None, None, None)
        self.m_serial += 1

    def _far_view(self, model: str):
        """(a model's far-view model, its material indices) once read and placed; None: it has none to merge (or the
        atlas is full); False: still read."""
        if model in self.m_far:
            return self.m_far[model]
        files, job = self.m_renderer.m_editor.m_files, FAR_JOB.format(model)

        def read() -> tuple:
            try:
                return (read_far_view(files.read(model[:-4] + ".bfg")),)
            except (OSError, KeyError, CFarViewError):  # no baked data file, or one that does not read
                return (None,)

        got = self.m_renderer.m_loader.get(job, read) if model.endswith(".cfg") else (None,)
        if got is None:
            self.m_waiting += 1
            return False
        self.m_renderer.m_loader.forget(job)
        far = got[0] if isinstance(got, tuple) else None
        materials = self.m_atlas.add(far) if far is not None else None
        self.m_far[model] = (far, materials) if materials is not None else None
        return self.m_far[model]

    def _sync_batch(self, batch, now: float) -> None:
        """A batch's instances into their regions; the regions whose instances moved marked changed."""
        members = self.m_members.get(batch.m_key)
        if (members.cells if members is not None else self.m_skipped.get(batch.m_key)) is batch.m_cells:
            return
        if batch.m_parts is None:
            self.m_waiting += 1
            return
        far = self._far_view(batch.m_key[1]) if is_static(batch) else None
        if far is False:
            return
        if far is None:
            self.m_skipped[batch.m_key] = batch.m_cells
            self._leave(batch.m_key, now)
            return
        matrices = np.asarray(batch.m_mats, np.float32)
        cell_matrices = batch.m_cells.m_matrices
        regions = region_keys(cell_matrices[:, :3, 3])
        touched = set(regions.ravel().tolist())
        if members is not None:
            if len(members.matrices) == len(matrices):  # an edit: the regions of what moved, before and after
                moved = np.flatnonzero(np.any(members.matrices != matrices, axis=(1, 2)))
                before = region_keys(members.matrices[moved][:, :3, 3])
                touched = set(before.ravel().tolist()) | set(region_keys(matrices[moved][:, :3, 3]).ravel().tolist())
            else:
                touched |= set(members.regions.ravel().tolist())
        self.m_members[batch.m_key] = _SMembers(matrices.copy(), regions, batch.m_cells, far[1])
        for row in set(map(tuple, regions.tolist())):
            for level, key in enumerate(row):
                self.m_dense.setdefault(key, len(self.m_dense))
                region = self.m_regions.setdefault(key, _SRegion())
                region.batches.add(batch.m_key)
                region.parent = row[level + 1] if level + 1 < len(row) else -1
        for code in touched:
            self._touch(code, now)

    def _touch(self, code: int, now: float) -> None:
        region = self.m_regions.setdefault(code, _SRegion())
        region.version += 1
        region.changed_s = now if region.built >= 0 else 0.0  # a first build does not wait for an edit to settle
        self.m_serial += 1

    def _leave(self, key: tuple, now: float) -> None:
        """A batch no longer merged (gone, hidden, or its model has no far-view model): its regions built again."""
        members = self.m_members.pop(key, None)
        if members is None:
            return
        for code in set(members.regions.ravel().tolist()):
            region = self.m_regions.get(code)
            if region is not None:
                region.batches.discard(key)
                self._touch(code, now)

    def _build(self, now: float) -> None:
        """The regions whose mesh is not of their version: built in the background once settled, put on the device
        when in."""
        loader = self.m_renderer.m_loader
        for code, region in list(self.m_regions.items()):
            if region.built == region.version and not region.job:
                continue
            if region.job:
                got = loader.get(region.job, None)
                if got is None:
                    self.m_waiting += 1
                    continue
                loader.forget(region.job)
                region.job, self._m_jobs = "", self._m_jobs - 1
                self._install(code, region, got)
                continue
            if now - region.changed_s < SETTLE_S:
                self.m_waiting += 1
                continue
            if self._m_jobs >= MAX_BUILDS:
                self.m_waiting += 1
                continue
            pieces = self._pieces(code, region)
            region.job, region.job_version = BUILD_JOB.format(f"{code}:{region.version}"), region.version
            self._m_jobs += 1
            loader.get(region.job, lambda pieces=pieces: (merge(pieces),))  # a tuple: None is "still running"
            self.m_waiting += 1

    def _pieces(self, code: int, region: _SRegion) -> list[SPiece]:
        """A region's member instances as the build takes them (copies: the build runs on a worker)."""
        out = []
        for key in region.batches:
            members = self.m_members.get(key)
            far = self.m_far.get(key[1])
            if members is None or not far:
                continue
            rows = np.flatnonzero(members.regions[:, level_of(code)] == code)
            if len(rows):
                out.append(SPiece(far[0], members.cells.m_matrices[rows].copy(), far[1]))
        return out

    def _install(self, code: int, region: _SRegion, got) -> None:
        """A build's result (``(mesh or None,)``, or the exception it raised) on the device in place of the old."""
        if region.mesh is not None:
            self.m_native.drop(region.mesh)
            region.mesh = None
        if isinstance(got, tuple) and got[0] is not None:
            region.mesh = self.m_native.put(got[0])
        region.built = region.job_version
        if not region.batches and region.mesh is None:
            del self.m_regions[code]
        self.m_serial += 1

    def frame(self, view_frame, options, start_m: float, far_m: float, shadows) -> SClusterFrame:
        """The regions to draw this frame, faded by their distance from the eye past ``start_m`` up to ``far_m`` (the
        view's object distance: the island view's whole-island camera lies past the game's ``ClusterViewDistance``,
        docs/rendering/distance-clusters.md), and their casters into the cascades ``shadows`` reaches (``_reach``;
        None: no shadow)."""
        out = SClusterFrame()
        if not self.is_on(options) or self.m_native is None:
            return out
        self.m_native.sync(self.m_atlas)
        ready = self._ready()
        if not len(ready[0]):
            return out
        keys, meshes, centres, scales, parents, dense = ready
        distances = box_distances(view_frame.eye, centres, scales)
        alphas = fade_of(distances, start_m)
        levels, chosen = level_of(keys), np.zeros(len(keys), bool)
        covered = np.zeros(0, np.int64)  # the regions drawn or held by one drawn, level by level from the top
        for level in range(len(LEVELS_M) - 1, -1, -1):
            here = levels == level
            held = here & np.isin(parents, covered)
            chosen |= here & ~held & (distances >= start_m * 2**level) & (alphas > 0.0) & (distances <= far_m)
            covered = keys[(chosen | held) & here]
        picked = np.flatnonzero(chosen)
        drawn = [(meshes[k], alpha) for k, alpha in zip(picked.tolist(), alphas[picked].tolist(), strict=True)]
        out.triangles = sum(mesh.m_merged.triangle_count for mesh, _alpha in drawn)
        out.full = dense[chosen & (alphas >= 1.0)]
        reach = _reach(shadows, centres[picked], scales[picked]) if shadows is not None and drawn else None
        out.gbuffer, out.casters = self.m_native.runs(drawn, reach)
        out.drawn = len(drawn)
        return out

    def _ready(self) -> tuple:
        """The regions whose mesh is of their version, as arrays (keys, meshes, centres, half extents, parents, dense
        numbers), made again when a region changed (a region still built or settling draws its objects)."""
        if self._m_ready[0] != self.m_serial:
            regions = [(key, region) for key, region in self.m_regions.items()
                       if region.mesh is not None and region.built == region.version]  # fmt: skip
            meshes = [region.mesh for _key, region in regions]
            self._m_ready = (self.m_serial, (
                np.array([key for key, _region in regions], np.int64), meshes,
                np.array([mesh.m_merged.centre for mesh in meshes], np.float64).reshape(-1, 3),
                np.array([mesh.m_merged.scale for mesh in meshes], np.float64).reshape(-1, 3),
                np.array([region.parent for _key, region in regions], np.int64),
                np.array([self.m_dense[key] for key, _region in regions], np.int64),
            ))  # fmt: skip
        return self._m_ready[1]

    def hidden(self, table, frame: SClusterFrame) -> np.ndarray | None:
        """Per row of the frame's table (``desktopgametables.CFrameTable``), whether a cluster drawn in full stands
        for it this frame; None when none does."""
        if not len(frame.full):
            return None
        if self._m_rows[0] is not table or self._m_rows[1] != self.m_serial:  # each row's regions as dense numbers
            rows = np.full((int(table.m_starts[-1]), len(LEVELS_M)), -1, np.int64)
            for k, batch in enumerate(table.m_batches):
                members = self.m_members.get(batch.m_key)
                if members is not None and members.cells is batch.m_cells:
                    keys, inverse = np.unique(members.regions, return_inverse=True)
                    dense = np.array([self.m_dense[key] for key in keys.tolist()], np.int64)
                    rows[table.m_starts[k] : table.m_starts[k + 1]] = dense[inverse].reshape(members.regions.shape)
            self._m_rows = (table, self.m_serial, rows)
        full = np.zeros(len(self.m_dense) + 1, bool)  # the last (-1): no region
        full[frame.full] = True
        return full[self._m_rows[2]].any(axis=1)

    def start_distance(self, quality) -> float:
        """The quality level's ``ClusterStartDistance`` from the game's engine settings (vanilla's until read)."""
        level = getattr(quality, "level", 1)
        files = getattr(self.m_renderer.m_editor.m_files, "m_game", self.m_renderer.m_editor.m_files)

        def read() -> list:
            return game_levels(files.read(ENGINE_SETTINGS).decode("utf-8", errors="replace"))

        levels = self.m_renderer.m_loader.get(DISTANCES_JOB, read)
        if isinstance(levels, list) and level < len(levels) and "ClusterStartDistance" in levels[level]:
            return float(levels[level]["ClusterStartDistance"])
        return VANILLA_START[min(max(level, 0), len(VANILLA_START) - 1)]

    def release(self) -> None:
        self._drop_device()
        self._m_objects = None
