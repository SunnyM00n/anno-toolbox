"""The shown batches' instances of the look "The game's own shaders" as tables (``desktopgameobjects``): every
instance's place and bounding sphere in one table a frame culls and levels in a few array operations
(``CFrameTable``), each batch's cells against the camera for what is near (``CReach``).
docs/rendering/island-117-view.md "Speed".
"""

from __future__ import annotations

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


class CFrameTable(_COfBatches):
    """Every shown batch's instances in one table (``batches``: their places in cell order, each one's batch, its
    bounding sphere, whether it is shown), made again when a batch or its instances change: a frame culls them and
    picks their levels and cascades in a few array operations over all of them (``pick``), not a few per batch
    (island-117-view.md "Speed", part 4)."""

    def __init__(self, batches: list) -> None:
        self.m_batches = batches
        super().__init__(batches)
        cells = [batch.m_cells for batch in batches]
        rows = np.concatenate([cell.m_rows for cell in cells]) if cells else np.zeros((0, 16), np.float32)
        self.m_mats = rows.reshape(-1, 4, 4).transpose(0, 2, 1)  # column vectors, the view's axes
        counts = [len(cell.m_rows) for cell in cells]
        self.m_batch = np.repeat(np.arange(len(batches)), counts).astype(np.int64)
        self.m_cell_counts = np.concatenate([cell.m_counts for cell in cells]) if cells else np.zeros(0, int)
        self.m_shown = np.abs(self.m_mats[:, :3, :3]).reshape(len(rows), 9).max(axis=1) > 0  # hidden: scale 0
        # each instance its model's sphere, all in one pass (per batch it was a loop on the window's thread)
        models = [model_sphere(batch) for batch in batches]
        centre = np.repeat(
            np.array([sphere_centre for sphere_centre, _sphere_radius in models], np.float32).reshape(-1, 3),
            counts,
            axis=0,
        )
        radius = np.repeat(np.array([sphere_radius for _sphere_centre, sphere_radius in models], np.float32), counts)
        self.m_centres, self.m_radii = spheres(self.m_mats, (centre, radius))

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

    def __init__(self, batches: list) -> None:
        super().__init__(batches)
        cells = [batch.m_cells for batch in batches]
        counts = [len(cell.m_min_box) for cell in cells]
        self.m_starts = np.r_[0, np.cumsum(counts)[:-1]].astype(np.int64) if all(counts) else None  # each its run
        self.m_batch = np.repeat(np.arange(len(batches)), counts)
        self.m_min_box = np.concatenate([cell.m_min_box for cell in cells]) if cells else np.zeros((0, 3))
        self.m_max_box = np.concatenate([cell.m_max_box for cell in cells]) if cells else np.zeros((0, 3))
        models = [(model_sphere(batch), cell.m_scale) for batch, cell in zip(batches, cells, strict=True)]
        self.m_radius_m = np.array(
            [sphere_radius * scale for (_sphere_centre, sphere_radius), scale in models], float
        )  # the model's sphere at its largest scale
        # how far an instance's sphere reaches past its place (the origin the cells' boxes are made of)
        reach = [
            (float(np.linalg.norm(sphere_centre)) + sphere_radius) * scale
            for (sphere_centre, sphere_radius), scale in models
        ]
        self.m_extent_m = np.repeat(np.asarray(reach, float), counts)

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
