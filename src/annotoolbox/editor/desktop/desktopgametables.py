"""The shown batches' instances of the look "The game's own shaders" as tables (``desktopgameobjects``): every
instance's place and bounding sphere in one table a frame culls and levels in a few array operations
(``CFrameTable``), each batch's cells against the camera for what is near (``CReach``).
docs/rendering/island-117-view.md "Speed".
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from ...render import cull


def part_worlds(matrices: np.ndarray, part_matrix: np.ndarray) -> np.ndarray:
    """The places ([n, 4, 4], column vectors, the view's axes) of a part at the instances ``mats``."""
    return (matrices @ np.asarray(part_matrix, np.float32)).astype(np.float32)


def model_sphere(batch) -> tuple:
    """The bounding sphere (centre, radius) of a batch's model in its own space, from the box of its parts."""
    if batch.m_box is None:
        return np.zeros(3), float(batch.m_radius_m)
    box_min, box_max = (np.asarray(corner, np.float32) for corner in batch.m_box)
    return (box_min + box_max) / 2, float(np.linalg.norm(box_max - box_min) / 2)


def sphere_of(batch, kept: dict) -> tuple:
    """A batch's ``model_sphere``, kept in ``kept`` (batch key -> (batch, box, sphere)) while its box stays: made per
    batch each time one batch changed, it was a loop over thousands on the window's thread."""
    got = kept.get(batch.m_key)
    if got is None or got[0] is not batch or got[1] is not batch.m_box:
        got = kept[batch.m_key] = (batch, batch.m_box, model_sphere(batch))
    return got[2]


def spheres(worlds: np.ndarray, sphere: tuple) -> tuple[np.ndarray, np.ndarray]:
    """The instances' bounding spheres (centres [n, 3], radii [n]) from the part's own (``SPartDraws.sphere``)."""
    centre, radius = sphere  # one for all, or one per instance ([n, 3], [n])
    centre = np.broadcast_to(np.asarray(centre, np.float32), (len(worlds), 3))
    centres = np.einsum("nij,nj->ni", worlds[:, :3, :3], centre) + worlds[:, :3, 3]
    scale = np.sqrt(np.einsum("nij,nij->nj", worlds[:, :3, :3], worlds[:, :3, :3]).max(axis=1))
    return centres, radius * scale


class _COfBatches:
    """A table made of shown batches, made again when one of them, its instances or its model's box changed."""

    def __init__(self, batches: list) -> None:
        self.m_key = [(batch, batch.m_cells, batch.m_box) for batch in batches]

    def same(self, batches: list) -> bool:
        """Whether ``batches`` are the ones it was made of, with the same instances and models."""
        return len(batches) == len(self.m_key) and all(
            batch is k[0] and batch.m_cells is k[1] and batch.m_box is k[2]
            for batch, k in zip(batches, self.m_key, strict=True)
        )


class SBatchRows(NamedTuple):
    """One batch's part of the tables, kept while its instances and its model's box stay (``batch_rows``): its places
    (column vectors, the view's axes), which are shown, their bounding spheres and its model's sphere."""

    batch: object
    cells: object
    box: object
    sphere: tuple
    mats: np.ndarray
    shown: np.ndarray
    centres: np.ndarray
    radii: np.ndarray


def batch_rows(batches: list, kept: dict) -> list[SBatchRows]:
    """Each batch's ``SBatchRows``, taken from ``kept`` (batch key -> rows, updated here) where its instances and box
    stayed, the others made in one pass over all of them: one changed batch made every batch's again, 0.26 to 0.38 s
    after a placement on a continental island (performance.md 6, item 6)."""
    out = [kept.get(batch.m_key) for batch in batches]
    fresh = [
        k
        for k, (batch, got) in enumerate(zip(batches, out, strict=True))
        if got is None or got.batch is not batch or got.cells is not batch.m_cells or got.box is not batch.m_box
    ]
    if fresh:
        made = [batches[k] for k in fresh]
        counts = [len(batch.m_cells.m_rows) for batch in made]
        rows = np.concatenate([batch.m_cells.m_rows for batch in made])
        mats = rows.reshape(-1, 4, 4).transpose(0, 2, 1)
        shown = np.abs(mats[:, :3, :3]).reshape(len(rows), 9).max(axis=1) > 0  # hidden: scale 0
        models = [model_sphere(batch) for batch in made]
        centre = np.repeat(np.array([centre for centre, _radius in models], np.float32).reshape(-1, 3), counts, axis=0)
        radius = np.repeat(np.array([radius for _centre, radius in models], np.float32), counts)
        centres, radii = spheres(mats, (centre, radius))  # each instance its model's sphere, all in one pass
        cuts = np.cumsum(counts)[:-1]
        split = [np.split(values, cuts) for values in (mats, shown, centres, radii)]
        for k, batch, sphere, *values in zip(fresh, made, models, *split, strict=True):
            out[k] = kept[batch.m_key] = SBatchRows(batch, batch.m_cells, batch.m_box, sphere, *values)
    return out


class CFrameTable(_COfBatches):
    """Every shown batch's instances in one table (``batches``: their places in cell order, each one's batch, its
    bounding sphere, whether it is shown), made again when a batch or its instances change (``kept``: the batches'
    parts, ``batch_rows``): a frame culls them and picks their levels and cascades in a few array operations over all
    of them (``pick``), not a few per batch (island-117-view.md "Speed", part 4)."""

    def __init__(self, batches: list, kept: dict | None = None) -> None:
        self.m_batches = batches
        super().__init__(batches)
        parts = batch_rows(batches, {} if kept is None else kept)
        counts = [len(part.mats) for part in parts]
        self.m_mats = np.concatenate([part.mats for part in parts]) if parts else np.zeros((0, 4, 4), np.float32)
        self.m_batch = np.repeat(np.arange(len(batches)), counts).astype(np.int64)
        self.m_starts = np.r_[0, np.cumsum(counts)].astype(np.int64)  # batch k's rows: m_starts[k] to m_starts[k + 1]
        self.m_cell_counts = np.concatenate([b.m_cells.m_counts for b in batches]) if parts else np.zeros(0, int)
        self.m_shown = np.concatenate([part.shown for part in parts]) if parts else np.zeros(0, bool)
        self.m_centres = np.concatenate([part.centres for part in parts]) if parts else np.zeros((0, 3), np.float32)
        self.m_radii = np.concatenate([part.radii for part in parts]) if parts else np.zeros(0, np.float32)

    def visible(self, seen: np.ndarray) -> np.ndarray:
        """The instances (their indices, in table order) of the cells ``seen`` marks (every batch's cells in turn),
        hidden ones left out."""
        return np.flatnonzero(np.repeat(seen, self.m_cell_counts) & self.m_shown)

    def runs(self, index: np.ndarray, levels: np.ndarray) -> tuple[np.ndarray, dict[int, list[tuple[int, int, int]]]]:
        """The order that sorts the instances ``index`` at ``levels`` (0 or more) by batch and level, keeping their
        cell order within each, and per batch its runs (level, start, end) in that order."""
        keys = self.m_batch[index] * 8 + levels
        order = np.argsort(keys, kind="stable")
        keys = keys[order]
        cuts = np.flatnonzero(np.diff(keys)) + 1
        out: dict[int, list] = {}
        for run_start, run_end in zip(np.r_[0, cuts].tolist(), np.r_[cuts, len(keys)].tolist(), strict=True):
            if run_end > run_start:
                out.setdefault(int(keys[run_start]) // 8, []).append((int(keys[run_start]) % 8, run_start, run_end))
        return order, out


class CReach(_COfBatches):
    """How near each shown batch's instances lie to the eye against the distance its model can be drawn at
    (``cull.drawn_within``), for all batches in a few array operations over their cells' boxes; made again when the
    batches change."""

    def __init__(self, batches: list, kept: dict | None = None) -> None:
        """``kept``: the batches' model spheres (``sphere_of``), kept between the reaches made."""
        super().__init__(batches)
        kept = {} if kept is None else kept
        cells = [batch.m_cells for batch in batches]
        counts = [len(cell.m_min_box) for cell in cells]
        self.m_starts = np.r_[0, np.cumsum(counts)[:-1]].astype(np.int64) if all(counts) else None  # each its run
        self.m_batch = np.repeat(np.arange(len(batches)), counts)
        self.m_min_box = np.concatenate([cell.m_min_box for cell in cells]) if cells else np.zeros((0, 3))
        self.m_max_box = np.concatenate([cell.m_max_box for cell in cells]) if cells else np.zeros((0, 3))
        models = [sphere_of(batch, kept) for batch in batches]
        scales = np.array([cell.m_scale for cell in cells], float)
        centres = np.array([centre for centre, _radius in models], float).reshape(-1, 3)
        radii = np.array([radius for _centre, radius in models], float)
        self.m_radius_m = radii * scales  # the model's sphere at its largest scale
        # how far an instance's sphere reaches past its place (the origin the cells' boxes are made of)
        self.m_extent_m = np.repeat((np.linalg.norm(centres, axis=1) + radii) * scales, counts)

    def ratios(self, eye, projection: np.ndarray, size_px: tuple[int, int], rules: cull.SLodRules) -> np.ndarray:
        """Per batch its nearest instance's distance from ``eye`` (the view's axes) over the distance its model can be
        drawn at: 1 or less, it can be drawn from here as the camera turns."""
        eye = np.asarray(eye, float)
        outside = np.maximum(self.m_min_box - eye, 0.0) + np.maximum(eye - self.m_max_box, 0.0)
        gap = np.maximum(np.linalg.norm(outside, axis=1) - self.m_extent_m, 0.0)
        if self.m_starts is not None and len(gap):
            nearest = np.minimum.reduceat(gap, self.m_starts)
        else:  # a batch without a cell
            nearest = np.full(len(self.m_radius_m), np.inf)
            np.minimum.at(nearest, self.m_batch, gap)
        return nearest / np.maximum(cull.drawn_within(self.m_radius_m, projection, size_px, rules), 1e-6)
