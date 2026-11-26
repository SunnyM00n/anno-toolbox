"""View-frustum culling for the renderer (``desktoprender``): the terrain in chunks and every batch's instances in
ground cells, each with a bounding box tested against the camera's six planes, so only what is in view is drawn
(island-editor-view "GPU load"); and 117's level of detail by screen size (rendering/camera.md 3), which both of the
island view's renderers follow. No GL, no Qt.

``CTerrainChunks``: the terrain's index runs in view; ``CInstanceCells``: a batch's instance matrices by cell, those in
view; ``CCellTable``: every cell of many batches its level at once (``SLodCamera``); ``lod_levels``: the rule per
instance, ``read_lod_rules`` reads it; ``drawn_within``: how far off a model can be drawn at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

CELL = 32.0  # world units: an instance batch is cut into square cells of this size
SPLIT_MIN = 64  # a batch of fewer instances is one cell
CHUNKS = 16  # the terrain is cut into at most this many chunks a side
MARGIN = 1.1  # on the model's reach: banner leaves are spread by the shader past the stored box


def frustum_planes(view_proj: np.ndarray) -> np.ndarray:
    """The six planes ``(a, b, c, d)`` of a view-projection matrix (rows, OpenGL clip space), normals inwards."""
    matrix = np.asarray(view_proj, "f8")
    planes = np.stack(
        [
            matrix[3] + matrix[0],
            matrix[3] - matrix[0],
            matrix[3] + matrix[1],
            matrix[3] - matrix[1],
            matrix[3] + matrix[2],
            matrix[3] - matrix[2],
        ]
    )
    return planes / np.maximum(np.linalg.norm(planes[:, :3], axis=1, keepdims=True), 1e-12)


def boxes_in_view(planes: np.ndarray, min_box: np.ndarray, max_box: np.ndarray) -> np.ndarray:
    """Which axis-aligned boxes ``lo``..``hi`` ([n, 3] each) touch the frustum: not wholly outside one plane."""
    normal = planes[:, :3]
    far_corner = np.where(normal[None] >= 0, max_box[:, None, :], min_box[:, None, :])  # the corner furthest inwards
    return ((far_corner * normal[None]).sum(-1) + planes[None, :, 3] >= 0).all(axis=1)


def reach(box) -> float:
    """How far any point of a model's box (Anno axes, ``(lo, hi)``) lies from its origin; infinite if unknown."""
    if box is None:
        return float("inf")
    min_box, max_box = np.asarray(box[0], "f8"), np.asarray(box[1], "f8")
    return float(np.linalg.norm(np.maximum(np.abs(min_box), np.abs(max_box)))) * MARGIN


class CTerrainChunks:
    """The terrain grid (``width`` x ``width`` vertices, ``step`` apart, x mirrored) in chunks of quads: ``indices``
    lists the triangles chunk by chunk (row-major chunks), so a run of neighbouring chunks is one range of it."""

    def __init__(self, width_samples: int, step_tiles: float) -> None:
        self.m_width_samples, self.m_step_tiles = width_samples, step_tiles
        quads = width_samples - 1
        self.m_quads_per_chunk = max(8, -(-quads // CHUNKS))  # quads a chunk side
        self.m_chunks_per_side = -(-quads // self.m_quads_per_chunk)  # chunks a side
        qj, qi = np.divmod(np.arange(quads * quads), quads)
        chunk = (qj // self.m_quads_per_chunk) * self.m_chunks_per_side + qi // self.m_quads_per_chunk
        order = np.argsort(chunk, kind="stable")
        quad = order  # quad number in the row-major grid of quads
        corner = (quad // quads) * width_samples + quad % quads
        self.m_indices = np.stack(
            [
                corner,
                corner + 1,
                corner + width_samples,
                corner + 1,
                corner + width_samples + 1,
                corner + width_samples,
            ],
            -1,
        )
        self.m_indices = self.m_indices.astype("u4").ravel()
        self.m_counts = np.bincount(chunk, minlength=self.m_chunks_per_side * self.m_chunks_per_side) * 6
        self.m_starts = np.concatenate([[0], np.cumsum(self.m_counts)[:-1]])
        j, i = np.divmod(np.arange(self.m_chunks_per_side * self.m_chunks_per_side), self.m_chunks_per_side)
        self.m_j0, self.m_i0 = j * self.m_quads_per_chunk, i * self.m_quads_per_chunk
        self.m_j1, self.m_i1 = (
            np.minimum(self.m_j0 + self.m_quads_per_chunk, quads),
            np.minimum(self.m_i0 + self.m_quads_per_chunk, quads),
        )
        size = quads * step_tiles
        self.m_min_box = np.stack([size - self.m_i1 * step_tiles, np.zeros(len(j)), self.m_j0 * step_tiles], -1)
        self.m_max_box = np.stack([size - self.m_i0 * step_tiles, np.zeros(len(j)), self.m_j1 * step_tiles], -1)

    def set_heights(self, heights: np.ndarray) -> None:
        """The chunks' height ranges from the grid the vertices carry (a vertex either side of each chunk)."""
        for k in range(len(self.m_counts)):
            block = heights[self.m_j0[k] : self.m_j1[k] + 1, self.m_i0[k] : self.m_i1[k] + 1]
            self.m_min_box[k, 1], self.m_max_box[k, 1] = block.min(), block.max()

    def runs(self, planes: np.ndarray) -> list[tuple[int, int]]:
        """(first index, index count) of the chunks in view, neighbours joined."""
        seen = boxes_in_view(planes, self.m_min_box, self.m_max_box)
        out: list[tuple[int, int]] = []
        for k in np.flatnonzero(seen):
            if out and out[-1][0] + out[-1][1] == self.m_starts[k]:
                out[-1] = (out[-1][0], out[-1][1] + int(self.m_counts[k]))
            else:
                out.append((int(self.m_starts[k]), int(self.m_counts[k])))
        return out


class CInstanceCells:
    """A batch's instances cut into ground cells: ``rows`` are the instance matrices as the GPU reads them ([n, 16]
    floats), sorted by cell; ``lo`` / ``hi`` each cell's box over the instances' origins."""

    def __init__(self, matrices: np.ndarray) -> None:
        """``matrices``: the instance matrices as rows ([n, 4, 4]); the translation is the instance's place."""
        positions = matrices[:, :3, 3]
        if len(positions) >= SPLIT_MIN:
            cell = np.floor(positions[:, [0, 2]] / CELL).astype("i8")
            key = cell[:, 0] * 100003 + cell[:, 1]
            order = np.argsort(key, kind="stable")
            _, starts = np.unique(key[order], return_index=True)
        else:
            order, starts = np.arange(len(positions)), np.zeros(1, "i8")
        self.m_rows = np.ascontiguousarray(matrices[order].transpose(0, 2, 1)).reshape(len(positions), 16)
        self.m_order = order  # each row's instance in the given order (what else is kept per instance follows it)
        placed = positions[order]
        self.m_starts = starts
        self.m_counts = np.diff(np.append(starts, len(positions)))
        self.m_min_box, self.m_max_box = np.minimum.reduceat(placed, starts), np.maximum.reduceat(placed, starts)
        self.m_scale = float(np.abs(matrices[:, :3, :3]).max()) if len(positions) else 1.0  # the largest axis factor

    @property
    def m_matrices(self) -> np.ndarray:
        """The instance matrices as rows ([n, 4, 4]), in cell order."""
        return self.m_rows.reshape(-1, 4, 4).transpose(0, 2, 1)

    def visible(self, planes: np.ndarray, model_reach: float) -> np.ndarray:
        """Which cells have an instance in view, each instance taken as a ball of the model's reach."""
        if not np.isfinite(model_reach):  # no box known: always in view
            return np.ones(len(self.m_min_box), bool)
        pad = model_reach * self.m_scale
        return boxes_in_view(planes, self.m_min_box - pad, self.m_max_box + pad)

    def gather(self, cells: np.ndarray) -> np.ndarray:
        """The rows of the given cells (a boolean mask), in cell order."""
        if cells.all():
            return self.m_rows
        return self.m_rows[np.repeat(cells, self.m_counts)]  # one mask: a loop over the cells was most of a frame


class CCellTable:
    """The cells of many batches in one table, so a frame's culling and levels of detail are a few array operations
    over all of them, not a few per batch (``codes``; ``entries``: per batch its ``CInstanceCells``, the model's reach
    and how many levels it has)."""

    def __init__(self, entries: list[tuple[CInstanceCells, float, int]]) -> None:
        counts = [len(cells.m_min_box) for cells, _reach, _levels in entries]
        self.m_starts = np.concatenate([[0], np.cumsum(counts)]).astype(int)
        pads = [reach * cells.m_scale if np.isfinite(reach) else 0.0 for cells, reach, _levels in entries]
        self.m_pad = np.repeat(np.asarray(pads, float), counts)
        # typed: with no batch at all the empty lists would be floats, and ``seen | always`` would raise
        self.m_always = np.repeat(np.asarray([not np.isfinite(reach) for _c, reach, _l in entries], bool), counts)
        self.m_top = np.repeat(np.asarray([max(1, levels) - 1 for _c, _r, levels in entries], int), counts)
        min_boxes = [cells.m_min_box for cells, _reach, _levels in entries]
        max_boxes = [cells.m_max_box for cells, _reach, _levels in entries]
        self.m_min_box = np.concatenate(min_boxes) - self.m_pad[:, None] if min_boxes else np.zeros((0, 3))
        self.m_max_box = np.concatenate(max_boxes) + self.m_pad[:, None] if max_boxes else np.zeros((0, 3))

    def codes(self, planes: np.ndarray, camera: SLodCamera | None) -> np.ndarray:
        """Per cell of all batches: -2 out of view (``planes``), else the level of detail its nearest instance gets by
        117's rule (``lod_levels``, the instance a ball of the model's reach at the cell's point nearest the eye: no
        instance draws coarser than its own rule asks; -1: too small to draw), at most its batch's last; every cell
        in view at the finest when ``camera`` is None."""
        seen = boxes_in_view(planes, self.m_min_box, self.m_max_box) | self.m_always
        if camera is None:
            return np.where(seen, 0, -2)
        nearest = np.clip(camera.eye, self.m_min_box, self.m_max_box)
        out = lod_levels(nearest, self.m_pad, camera.view, camera.proj, camera.size, camera.rules, 5)
        out = np.where(self.m_always, 0, np.minimum(out, self.m_top))
        return np.where(seen, out, -2)

    def of(self, codes: np.ndarray, k: int) -> np.ndarray:
        """Batch ``k``'s part of ``codes``."""
        return codes[self.m_starts[k] : self.m_starts[k + 1]]

    def of_many(self, codes: np.ndarray, batches: list[int]) -> np.ndarray:
        """The parts of ``codes`` of the batches ``batches`` (their numbers), one after the other: one gather for all,
        not a slice per batch."""
        numbers = np.asarray(batches, np.int64)
        if not len(numbers):
            return codes[:0]
        starts, lengths = self.m_starts[numbers], self.m_starts[numbers + 1] - self.m_starts[numbers]
        firsts = np.repeat(starts - np.r_[0, np.cumsum(lengths)[:-1]], lengths)
        return codes[firsts + np.arange(int(lengths.sum()))]


@dataclass(frozen=True)
class SLodRules:
    """117's level-of-detail choice (docs/rendering/camera.md 3): per level 1-4 the screen area it needs to be below
    (``LODAreas``) and the distance it needs to be beyond (``LODMinDistances``), the quality level's ``LODBias`` and
    the smallest size drawn (``MinimumObjectSizeInPixels``, its square in pixels [I]); vanilla's highest quality."""

    areas: tuple = (0.25, 0.057, 0.015, 0.0045)
    near: tuple = (5.0, 10.0, 15.0, 20.0)
    bias: float = 2.5
    min_pixels: float = 12.0
    far: float = float("inf")  # past this distance from the eye nothing draws (``render.quality``) [ours]


def read_lod_rules(text: str, quality: int = -1) -> SLodRules:
    """The rules of an ``enginesettings`` file (``quality``: its quality level, default the highest); what it lacks
    keeps vanilla's."""

    def table(key: str) -> list[float]:
        match = re.search(rf"<{key}>(.*?)</{key}>", text, re.S)
        return [float(value) for value in re.findall(r"<LOD\d>([-\d.]+)</LOD\d>", match.group(1))] if match else []

    def per_level(key: str) -> list[float]:
        return [float(value) for value in re.findall(rf"<{key}>([-\d.]+)</{key}>", text)]

    base = SLodRules()
    areas, near = table("LODAreas"), table("LODMinDistances")
    bias, pixels = per_level("LODBias"), per_level("MinimumObjectSizeInPixels")
    return SLodRules(tuple(areas[:4]) if len(areas) >= 4 else base.areas,
                    tuple(near[1:5]) if len(near) >= 5 else base.near,
                    bias[quality] if bias else base.bias, pixels[quality] if pixels else base.min_pixels)  # fmt: skip


def lod_levels(centres: np.ndarray, radii: np.ndarray, view: np.ndarray, projection: np.ndarray, size_px: tuple[int, int],
               rules: SLodRules, levels: int) -> np.ndarray:  # fmt: skip
    """Each instance's level (0 .. ``levels`` - 1; -1: too small to draw, or past ``rules.far``) by 117's rule: its
    bounding sphere's screen area (full screen 4) times the bias, the coarsest level its size and distance allow
    (``view``, ``projection``: column vectors, OpenGL's)."""
    hom = np.c_[centres, np.ones(len(centres))] @ np.asarray(view, float).T
    depth = -hom[:, 2]
    outside = depth > radii  # the camera inside a sphere sees it over the whole screen
    scale = abs(projection[0, 0] * projection[1, 1])
    area = np.where(outside, 4 * radii**2 * scale / np.maximum(depth, 1e-6) ** 2, 4.0)
    area = np.minimum(area, 4.0) * rules.bias
    distance = np.linalg.norm(hom[:, :3], axis=1)
    out = np.zeros(len(centres), np.int32)
    for i in range(1, levels):
        if i <= len(rules.areas):
            allowed = (area < rules.areas[i - 1]) & (distance > rules.near[i - 1] * rules.bias)
            out[allowed] = i
    out[(area * size_px[0] * size_px[1] / 4 < rules.min_pixels**2) | (distance - radii > rules.far)] = -1
    return out


def drawn_within(radii, projection: np.ndarray, size_px: tuple[int, int], rules: SLodRules):
    """How far from the eye a sphere of ``radii`` can still be drawn by ``lod_levels`` in any direction the camera
    turns: its screen size above the smallest at its view depth, that depth at most the distance at a corner of the
    picture, and not past ``rules.far``."""
    scale = abs(projection[0, 0] * projection[1, 1])
    depth = (
        np.asarray(radii, float) * np.sqrt(scale * rules.bias * size_px[0] * size_px[1]) / max(rules.min_pixels, 1e-6)
    )
    corner = np.sqrt(
        1 + 1 / projection[0, 0] ** 2 + 1 / projection[1, 1] ** 2
    )  # the distance per view depth at a corner
    return np.minimum(depth * corner, rules.far + np.asarray(radii, float))


def level_numbers(lod_materials: list, number: int | None, level: int) -> list[int] | None:
    """The mesh's material numbers at ``level`` that draw the config material the part's ``number`` names at level
    0 (``MaterialLODInfos``; without it the same number); None: the whole mesh."""
    if number is None:
        return None
    if not lod_materials or level >= len(lod_materials) or not lod_materials[0]:
        return [number]
    first = lod_materials[0]
    material = first[number] if number < len(first) else number
    return [k for k, candidate in enumerate(lod_materials[level]) if candidate == material]


@dataclass
class SLodCamera:
    """What the level of detail is chosen for (``CCellTable.codes``): the eye (the cells' axes), the camera's
    view and projection (column vectors, OpenGL's), the screen in pixels, the rules."""

    eye: np.ndarray
    view: np.ndarray
    proj: np.ndarray
    size: tuple[int, int]
    rules: SLodRules = SLodRules()

    def signature(self) -> tuple:
        """A value that is equal for equal cameras: the level of detail chosen from it is the same."""
        return (self.eye.tobytes(), self.view.tobytes(), self.proj.tobytes(), self.size, self.rules)
