"""Which terrain nodes draw an island for a camera: a quadtree over the island, a node split while the eye is near
it for its size, so that near ground is baked finer (a node's bake has the same texels whatever its size); and the
cache that keeps a slice of the baked arrays per node. No Qt, no GPU.

``select(size, eye, ...)`` is the entry (the nodes, nearest first); ``CSliceCache`` hands out and takes back slices;
``stand_ins`` what draws while new nodes wait for their bake; ``CNodeBoxes`` the nodes' boxes and their culling.
The engine's own rule is decided at run time and not read: ours is in docs/rendering/island-117-view.md.
"""

from __future__ import annotations

import heapq
import math
from collections import OrderedDict
from typing import NamedTuple

import numpy as np

from .cull import boxes_in_view

MIN_LEVEL = 2  # the finest node: 4 tiles [ours]
SPLIT = 2.5  # a node is split while the eye is nearer than this many times its size [ours]


class SNode(NamedTuple):
    """A square of the island: its level (2^level tiles a side), its corner in tiles (a tuple: hashed and compared
    in C, a frame's node selection does it thousands of times)."""

    level: int
    x: float
    z: float

    @property
    def tiles(self) -> float:
        return float(2**self.level)

    def distance(self, eye) -> float:
        """From ``eye`` (x, y, z in the island's tiles and world units) to the nearest point of the node's square at
        height 0..the eye's height (the ground below is never higher than the eye in a view from above)."""
        delta_x = max(self.x - eye[0], 0.0, eye[0] - self.x - self.tiles)
        delta_z = max(self.z - eye[2], 0.0, eye[2] - self.z - self.tiles)
        return math.sqrt(delta_x * delta_x + delta_z * delta_z + max(eye[1], 0.0) ** 2 * 0.25)

    def children(self, size_tiles: float) -> list[SNode]:
        """Its four quarters that lie on the island (``size_tiles`` tiles)."""
        half = self.tiles / 2
        return [SNode(self.level - 1, self.x + i * half, self.z + j * half)
                for j in (0, 1) for i in (0, 1) if self.x + i * half < size_tiles and self.z + j * half < size_tiles]  # fmt: skip

    def samples(self, step_tiles: float, width_samples: int) -> tuple[int, int, int, int]:
        """(first column, first row, columns, rows) of the height grid it spans, its far edge included."""
        column, row = round(self.x / step_tiles), round(self.z / step_tiles)
        per = round(self.tiles / step_tiles)
        return column, row, min(per, width_samples - 1 - column) + 1, min(per, width_samples - 1 - row) + 1


def root(size_tiles: float) -> SNode:
    """The node over the whole island: the smallest power of two that covers it."""
    return SNode(max(MIN_LEVEL, math.ceil(math.log2(max(size_tiles, 1.0)))), 0.0, 0.0)


def select(size_tiles: float, eye, budget: int = 128, split: float = SPLIT, visible=None) -> list[SNode]:
    """The nodes that cover the island (``size_tiles`` tiles) for an eye at ``eye`` (island tiles), at most ``budget``:
    the node nearest for its size split first while it is nearer than ``split`` sizes; ``visible(node)`` (None: all)
    leaves out what the camera cannot see. Nearest first."""
    tree = _tree(size_tiles, eye, split)
    leaves = _pick(tree, budget)
    # nearest first, equal ones in the walk's order: the slices and the bake follow the order (a tie broken
    # otherwise changed the picture)
    leaves = leaves[np.argsort(tree.distances[leaves], kind="stable")]
    done = [SNode(level, x, z) for level, x, z in zip(tree.levels[leaves].tolist(), tree.xs[leaves].tolist(),
                                                       tree.zs[leaves].tolist(), strict=True)]  # fmt: skip
    many = getattr(visible, "many", None)  # all of them at once (``CNodeBoxes.view``)
    if many is not None:
        return [candidate for candidate, is_shown in zip(done, many(done), strict=True) if is_shown]
    return [candidate for candidate in done if visible is None or visible(candidate)]


class _SQuadTree(NamedTuple):
    """Every node of the quadtree ``_tree`` made, by number (the root 0), one array per field."""

    levels: np.ndarray
    xs: np.ndarray  # the corner in tiles
    zs: np.ndarray
    distances: np.ndarray  # ``SNode.distance`` from the eye
    needs: np.ndarray  # how much it wants splitting: its size over that distance
    wants: np.ndarray  # whether it wants splitting
    first_kids: np.ndarray  # the number of its first kid
    kid_counts: np.ndarray  # its kids on the island (0: none made)


def _tree(size_tiles: float, eye, split: float) -> _SQuadTree:
    """Every node down to where none wants splitting, a level at a time in array operations (one node at a time a
    turning frame's pick took 0.7 to 1 ms: performance.md section 4, "What is left")."""
    top = root(size_tiles)
    levels, xs, zs = np.array([top.level]), np.array([top.x]), np.array([top.z])
    # in the eye's own precision, as ``SNode.distance`` works it out (the view's eye is float32): the same nodes
    kind = np.float32 if np.asarray(eye).dtype == np.float32 else np.float64
    eye_x, eye_y, eye_z = (kind(eye[k]) for k in range(3))
    height = max(eye_y, kind(0.0)) ** 2 * kind(0.25)
    parts: list[tuple] = []
    count = 1
    while len(levels):
        tiles = np.exp2(levels.astype(float))
        own_xs, own_zs, own_tiles = xs.astype(kind), zs.astype(kind), tiles.astype(kind)
        delta_x = np.maximum(np.maximum(own_xs - eye_x, kind(0.0)), eye_x - own_xs - own_tiles)
        delta_z = np.maximum(np.maximum(own_zs - eye_z, kind(0.0)), eye_z - own_zs - own_tiles)
        distance = np.sqrt((delta_x * delta_x + delta_z * delta_z + height).astype(np.float64))
        need = tiles / np.maximum(distance, 1e-6)
        wants = (levels > MIN_LEVEL) & (need > 1.0 / split)
        half = tiles[wants][:, None] / 2  # the quarters in ``SNode.children``'s order
        kid_xs = (xs[wants][:, None] + half * np.array([0, 1, 0, 1])).ravel()
        kid_zs = (zs[wants][:, None] + half * np.array([0, 0, 1, 1])).ravel()
        on_island = (kid_xs < size_tiles) & (kid_zs < size_tiles)
        kid_counts = np.zeros(len(levels), np.int64)
        kid_counts[wants] = on_island.reshape(-1, 4).sum(axis=1)
        first_kids = count + np.cumsum(kid_counts) - kid_counts
        parts.append((levels, xs, zs, distance, need, wants, first_kids, kid_counts))
        count += int(kid_counts.sum())
        levels, xs, zs = np.repeat(levels[wants] - 1, 4)[on_island], kid_xs[on_island], kid_zs[on_island]
    return _SQuadTree(*(np.concatenate(column) for column in zip(*parts, strict=True)))


def _pick(tree: _SQuadTree, budget: int) -> np.ndarray:
    """The leaves within ``budget`` nodes: the node that wants it most split first while its kids fit, as the
    quadtree's walk orders them (a kid wants it at most half as much as its node: the order holds level to level)."""
    need, wants = tree.needs.tolist(), tree.wants.tolist()
    first_kids, kid_counts = tree.first_kids.tolist(), tree.kid_counts.tolist()
    heap = [(-need[0], 0, 0)]
    leaves: list[int] = []
    count, serial = 1, 1
    while heap:
        _need_now, _serial, node = heapq.heappop(heap)
        kids = kid_counts[node]
        if not wants[node] or count + kids - 1 > budget:
            leaves.append(node)
            continue
        count += kids - 1
        for kid in range(first_kids[node], first_kids[node] + kids):
            heapq.heappush(heap, (-need[kid], serial, kid))
            serial += 1
    return np.asarray(leaves, np.int64)


class CNodeBoxes:
    """The nodes' boxes from the heights' range per block of the finest nodes (``low``, ``high``: [rows, columns]),
    one min and max pyramid for every level: a node's box is one lookup, not a min over its blocks; ``view(planes)``
    tests nodes against the camera, many at once (performance.md section 4, item 2). ``size_tiles``: the island's
    side, x turned over into the view's axes."""

    BELOW, ABOVE = 0.5, 2.5  # how far a box reaches past the heights (the bake's and the tessellation's play) [ours]

    def __init__(self, low: np.ndarray, high: np.ndarray, size_tiles: float) -> None:
        self.m_size_tiles = float(size_tiles)
        self.m_levels = [(np.asarray(low, float), np.asarray(high, float))]
        while max(self.m_levels[-1][0].shape) > 1:
            below, above = self.m_levels[-1]
            self.m_levels.append((_halved(below, np.min, np.inf), _halved(above, np.max, -np.inf)))

    def bounds(self, nodes: np.ndarray) -> np.ndarray:
        """The lowest and highest height under each of ``nodes`` ([n, 3] of level, x, z), with the box's play: [n, 2],
        one lookup per level for all of them."""
        out = np.empty((len(nodes), 2))
        levels = nodes[:, 0].astype(np.int64)
        for level in np.unique(levels).tolist():
            low, high = self.m_levels[min(level - MIN_LEVEL, len(self.m_levels) - 1)]
            at = levels == level
            side = 2.0**level
            rows = np.minimum((nodes[at, 2] // side).astype(np.int64), low.shape[0] - 1)
            columns = np.minimum((nodes[at, 1] // side).astype(np.int64), low.shape[1] - 1)
            out[at, 0], out[at, 1] = low[rows, columns] - self.BELOW, high[rows, columns] + self.ABOVE
        return out

    def view(self, planes):
        """Whether a node is in view of ``planes`` (None: cull off, every node is): a test of one node, with
        ``many(nodes)`` for a list at once."""
        if planes is None:
            return None
        return _CNodeView(self, planes)


class _CNodeView:
    """``CNodeBoxes.view``: the nodes tested against one camera, each answer kept for the frame."""

    def __init__(self, boxes: CNodeBoxes, planes: np.ndarray) -> None:
        self.m_boxes, self.m_planes = boxes, planes
        self._m_seen: dict[SNode, bool] = {}

    def many(self, nodes: list[SNode]) -> list[bool]:
        new = [node for node in nodes if node not in self._m_seen]
        if new:
            size = self.m_boxes.m_size_tiles
            rows = np.array(new, float)  # level, x, z
            ranges = self.m_boxes.bounds(rows)
            xs, zs, tiles = rows[:, 1], rows[:, 2], np.exp2(rows[:, 0])
            box_lo = np.stack([size - xs - tiles, ranges[:, 0], zs], axis=1)
            box_hi = np.stack([size - xs, ranges[:, 1], zs + tiles], axis=1)
            self._m_seen.update(zip(new, boxes_in_view(self.m_planes, box_lo, box_hi).tolist(), strict=True))
        return [self._m_seen[node] for node in nodes]

    def __call__(self, node: SNode) -> bool:
        found = self._m_seen.get(node)
        return found if found is not None else self.many([node])[0]


def _halved(array: np.ndarray, reducer, pad: float) -> np.ndarray:
    """Each 2 x 2 of ``array`` reduced to one (an odd edge padded with ``pad``)."""
    rows, columns = array.shape
    padded = np.full((rows + rows % 2, columns + columns % 2), pad)
    padded[:rows, :columns] = array
    return reducer(padded.reshape(padded.shape[0] // 2, 2, padded.shape[1] // 2, 2), axis=(1, 3))


def stand_ins(wanted: list[SNode], ready, before, size_tiles: float, visible=None) -> list[SNode]:
    """The nodes to draw for ``wanted`` (``select``'s): each one ``ready`` (baked); one not yet by what was drawn
    before (``before``) over its square while that is ready: the larger node it split from, else the smaller ones it
    merged where they cover its ``visible`` part; else itself. A level-of-detail switch so never shows a node before
    its bake (an unbaked slice draws black or another node's ground) [ours]. Nearest first, as ``wanted``."""
    before = {candidate for candidate in before if ready(candidate)}
    if not before:
        return list(wanted)
    top = max(candidate.level for candidate in before)
    out: dict[SNode, None] = {}
    parents: set[SNode] = set()
    for node in wanted:
        if not ready(node) and (parent := _ancestor(node, before, top)) is not None:
            parents.add(parent)
    for node in wanted:
        parent = next((candidate_parent for candidate_parent in parents if _inside(node, candidate_parent)), None)
        if parent is not None:  # the larger node it split from, until all its quarters are baked
            out[parent] = None
            continue
        kids = (
            [] if ready(node) else [candidate for candidate in before if _inside(candidate, node) and candidate != node]
        )
        if kids and _covered(node, before, size_tiles, visible):  # the smaller ones it merged, until it is baked
            out.update(dict.fromkeys(sorted(kids, key=lambda candidate: (candidate.z, candidate.x))))
        else:
            out[node] = None
    return list(out)


def _inside(node: SNode, outer: SNode) -> bool:
    end = outer.tiles
    return outer.level >= node.level and outer.x <= node.x < outer.x + end and outer.z <= node.z < outer.z + end


def _ancestor(node: SNode, among: set[SNode], top: int) -> SNode | None:
    """The larger node of ``among`` that holds ``node``, None when there is none."""
    for level in range(node.level + 1, top + 1):
        side = 2.0**level
        larger = SNode(level, math.floor(node.x / side) * side, math.floor(node.z / side) * side)
        if larger in among:
            return larger
    return None


def _covered(node: SNode, among: set[SNode], size_tiles: float, visible) -> bool:
    """Whether the nodes of ``among`` cover the part of ``node`` in view (``visible``; None: all of it)."""
    if node in among or (visible is not None and not visible(node)):
        return True
    if node.level <= MIN_LEVEL:
        return False
    return all(_covered(kid, among, size_tiles, visible) for kid in node.children(size_tiles))


class CSliceCache:
    """``count`` slices of the baked arrays kept per node, the longest unused given away first; ``fresh`` are the
    slices handed out (or spoilt) since the last ``baked`` and so still to bake."""

    def __init__(self, count: int) -> None:
        self.m_count = count
        self.m_slices: OrderedDict[SNode, int] = OrderedDict()
        self.m_fresh: set[SNode] = set()
        self.m_dropped: list[SNode] = []  # nodes whose slice was given away (their models go too)

    def get(self, node: SNode) -> int:
        """The node's slice, a new one (the longest unused node's when all are taken) when it has none."""
        if node in self.m_slices:
            self.m_slices.move_to_end(node)
            return self.m_slices[node]
        if len(self.m_slices) < self.m_count:
            k = len(self.m_slices)
        else:
            old, k = self.m_slices.popitem(last=False)
            self.m_fresh.discard(old)
            self.m_dropped.append(old)
        self.m_slices[node] = k
        self.m_fresh.add(node)
        return k

    def spoil(self, box) -> list[SNode]:
        """The nodes whose square a box of tiles (x0, z0, x1, z1) touches are to bake again; they are returned."""
        hit = [node for node in self.m_slices if box[0] <= node.x + node.tiles and node.x <= box[2] and box[1] <= node.z + node.tiles
               and node.z <= box[3]]  # fmt: skip
        self.m_fresh.update(hit)
        return hit

    def baked(self, node: SNode) -> None:
        self.m_fresh.discard(node)

    def clear(self) -> None:
        self.m_dropped.extend(self.m_slices)
        self.m_slices.clear()
        self.m_fresh.clear()
