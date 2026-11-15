"""Which terrain nodes draw an island for a camera: a quadtree over the island, a node split while the eye is near
it for its size, so that near ground is baked finer (a node's bake has the same texels whatever its size); and the
cache that keeps a slice of the baked arrays per node. No Qt, no GPU.

``select(size, eye, ...)`` is the entry (the nodes, nearest first); ``SliceCache`` hands out and takes back slices.
The engine's own rule is decided at run time and not read: ours is in docs/rendering/island-117-view.md.
"""

from __future__ import annotations

import heapq
import math
from collections import OrderedDict
from dataclasses import dataclass

MIN_LEVEL = 2  # the finest node: 4 tiles [ours]
SPLIT = 2.5  # a node is split while the eye is nearer than this many times its size [ours]


@dataclass(frozen=True)
class Node:
    """A square of the island: its level (2^level tiles a side), its corner in tiles."""

    level: int
    x: float
    z: float

    @property
    def tiles(self) -> float:
        return float(2**self.level)

    def distance(self, eye) -> float:
        """From ``eye`` (x, y, z in the island's tiles and world units) to the nearest point of the node's square at
        height 0..the eye's height (the ground below is never higher than the eye in a view from above)."""
        dx = max(self.x - eye[0], 0.0, eye[0] - self.x - self.tiles)
        dz = max(self.z - eye[2], 0.0, eye[2] - self.z - self.tiles)
        return math.sqrt(dx * dx + dz * dz + max(eye[1], 0.0) ** 2 * 0.25)

    def children(self, size: float) -> list[Node]:
        """Its four quarters that lie on the island (``size`` tiles)."""
        half = self.tiles / 2
        return [Node(self.level - 1, self.x + i * half, self.z + j * half)
                for j in (0, 1) for i in (0, 1) if self.x + i * half < size and self.z + j * half < size]  # fmt: skip

    def samples(self, step: float, width: int) -> tuple[int, int, int, int]:
        """(first column, first row, columns, rows) of the height grid it spans, its far edge included."""
        col, row = round(self.x / step), round(self.z / step)
        per = round(self.tiles / step)
        return col, row, min(per, width - 1 - col) + 1, min(per, width - 1 - row) + 1


def root(size: float) -> Node:
    """The node over the whole island: the smallest power of two that covers it."""
    return Node(max(MIN_LEVEL, math.ceil(math.log2(max(size, 1.0)))), 0.0, 0.0)


def select(size: float, eye, budget: int = 128, split: float = SPLIT, visible=None) -> list[Node]:
    """The nodes that cover the island (``size`` tiles) for an eye at ``eye`` (island tiles), at most ``budget``:
    the node nearest for its size split first while it is nearer than ``split`` sizes; ``visible(node)`` (None: all)
    leaves out what the camera cannot see. Nearest first."""
    top = root(size)
    heap = [(-_need(top, eye), 0, top)]
    done: list[Node] = []
    count, serial = 1, 1
    while heap:
        need, _, node = heapq.heappop(heap)
        kids = node.children(size) if node.level > MIN_LEVEL and -need > 1.0 / split else []
        if not kids or count + len(kids) - 1 > budget:
            done.append(node)
            continue
        count += len(kids) - 1
        for kid in kids:
            heapq.heappush(heap, (-_need(kid, eye), serial, kid))
            serial += 1
    shown = [n for n in done if visible is None or visible(n)]
    return sorted(shown, key=lambda n: n.distance(eye))


def _need(node: Node, eye) -> float:
    """How much a node wants splitting: its size over its distance (larger first)."""
    return node.tiles / max(node.distance(eye), 1e-6)


class SliceCache:
    """``count`` slices of the baked arrays kept per node, the longest unused given away first; ``fresh`` are the
    slices handed out (or spoilt) since the last ``baked`` and so still to bake."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.slices: OrderedDict[Node, int] = OrderedDict()
        self.fresh: set[Node] = set()
        self.dropped: list[Node] = []  # nodes whose slice was given away (their models go too)

    def get(self, node: Node) -> int:
        """The node's slice, a new one (the longest unused node's when all are taken) when it has none."""
        if node in self.slices:
            self.slices.move_to_end(node)
            return self.slices[node]
        if len(self.slices) < self.count:
            k = len(self.slices)
        else:
            old, k = self.slices.popitem(last=False)
            self.fresh.discard(old)
            self.dropped.append(old)
        self.slices[node] = k
        self.fresh.add(node)
        return k

    def spoil(self, box) -> list[Node]:
        """The nodes whose square a box of tiles (x0, z0, x1, z1) touches are to bake again; they are returned."""
        hit = [n for n in self.slices if box[0] <= n.x + n.tiles and n.x <= box[2] and box[1] <= n.z + n.tiles
               and n.z <= box[3]]  # fmt: skip
        self.fresh.update(hit)
        return hit

    def baked(self, node: Node) -> None:
        self.fresh.discard(node)

    def clear(self) -> None:
        self.dropped.extend(self.slices)
        self.slices.clear()
        self.fresh.clear()
