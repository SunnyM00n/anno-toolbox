"""1701's occupation grid (``m_GRIDManager/m_GRID``) and the footprints read off it: which tiles a building holds.

A game object that holds tiles has its handle in the low 28 bits of each tile's word (bit 30 set); a footprint is a
rectangle of ``Shape`` tiles, centred on the object's position by its ``m_GRIDOffsetX/Z`` and turned by quarter turns
(``m_GRIDDirection`` / 16). The files carry no footprint, so a shape is read off the shipped islands: ``island_shapes``
of one island, ``merge_shapes`` over many. ``cells`` is the rule (95 % of the 21,000 occupied footprints of the 407
shipped islands exactly, 98 % where objects clipped each other), ``occupy`` and ``vacate`` write a change back.
docs/island-editor.md "Placed objects"; the word's other bits and the game's own rewriting of the grid are not known.
"""

from __future__ import annotations

import math
import struct
from collections import Counter
from typing import NamedTuple

from ..formats.isd2070 import Node
from .isd1701 import Island1701, hid_position

OWNER = 0x0FFFFFFF  # the low bits of an occupied tile: the occupying object's handle
OCCUPIED = 0x40000000  # set on every tile a handle holds in the shipped files
OFFSET_UNITS = 32.0  # ``m_GRIDOffsetX/Z`` counts world units, 32 to a tile
GRID_PATH = "m_GOPManager/m_GRIDManager/m_GRID"


class Shape(NamedTuple):
    """A footprint: ``w`` x ``h`` tiles unturned, the object's origin ``ox`` / ``oz`` world units past the corner of the
    tile it stands in (the file's grid offsets)."""

    w: int
    h: int
    ox: int = 0
    oz: int = 0


def grid_of(isl: Island1701) -> Node | None:
    """The ``m_GRID`` node when it holds one word per tile, else None (old levels carry none)."""
    node = isl.root.path(GRID_PATH)
    ok = node is not None and node.data is not None and len(node.data) == 4 * isl.width * isl.height
    return node if ok else None


def read_words(node: Node) -> list[int]:
    return list(struct.unpack(f"<{len(node.data) // 4}I", node.data))


def write_words(node: Node, words: list[int]) -> None:
    node.data = struct.pack(f"<{len(words)}I", *words)


def quarter_of(grid_direction: int) -> int | None:
    """The quarter turns (0 to 3) of ``m_GRIDDirection`` (64ths of a turn); None between quarters, where a footprint
    is not on the grid."""
    return grid_direction // 16 % 4 if grid_direction % 16 == 0 else None


def corners(shape: Shape) -> tuple[float, float, float, float]:
    """The unturned footprint's x0, z0, x1, z1 in tiles from the object's origin: the cells are
    ``[-w // 2, w - w // 2)`` round the origin's tile, the origin ``ox`` / ``oz`` into it."""
    x0, z0 = -(shape.w // 2) - shape.ox / OFFSET_UNITS, -(shape.h // 2) - shape.oz / OFFSET_UNITS
    return x0, z0, x0 + shape.w, z0 + shape.h


def polygon(shape: Shape) -> list[tuple[float, float]]:
    """The unturned footprint as a polygon (the corners, tiles from the origin) as ``editor.footprints`` takes it."""
    x0, z0, x1, z1 = corners(shape)
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def cells(x: float, z: float, shape: Shape, quarter: int) -> set[tuple[int, int]]:
    """The tiles (x, z) of the island a ``shape`` at origin (``x``, ``z``) tiles, turned ``quarter`` quarter turns, holds."""
    x0, z0, x1, z1 = corners(shape)
    pts = [(x0, z0), (x1, z1)]
    for _ in range(quarter % 4):  # a quarter turn takes (dx, dz) to (-dz, dx)
        pts = [(-b, a) for a, b in pts]
    xs = sorted(math.floor(x + p[0] + 0.5) for p in pts)  # corners are whole tiles; no banker's rounding
    zs = sorted(math.floor(z + p[1] + 0.5) for p in pts)
    return {(i, j) for i in range(xs[0], xs[1]) for j in range(zs[0], zs[1])}


def holds(words: list[int], width: int, handle: int) -> set[tuple[int, int]]:
    """The tiles whose word names ``handle``."""
    return {(k % width, k // width) for k, w in enumerate(words) if w & OWNER == handle and w & OCCUPIED}


def _shape_of_object(node: Node, held: set[tuple[int, int]]) -> tuple[int, Shape] | None:
    """The GUID and unturned shape of an object whose held tiles are a whole rectangle at a quarter turn."""
    q = quarter_of(node.int("m_GRIDDirection"))
    xs, zs = [c[0] for c in held], [c[1] for c in held]
    w, h = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
    if q is None or w * h != len(held):
        return None
    w, h = (w, h) if q % 2 == 0 else (h, w)
    return node.int("m_GUID"), Shape(w, h, node.int("m_GRIDOffsetX"), node.int("m_GRIDOffsetZ"))


def island_shapes(isl: Island1701) -> dict[int, Shape]:
    """GUID -> its footprint, from the island's own objects that hold tiles (the commonest shape of an asset)."""
    grid = grid_of(isl)
    if grid is None:
        return {}
    owner: dict[int, set[tuple[int, int]]] = {}
    for k, word in enumerate(read_words(grid)):
        if word & OCCUPIED and word & OWNER:
            owner.setdefault(word & OWNER, set()).add((k % isl.width, k // isl.width))
    seen: dict[int, Counter] = {}
    for kind, node in isl.object_nodes():
        held = owner.get(int(node.name, 16)) if kind == "HID" else None
        found = _shape_of_object(node, held) if held else None
        if found is not None:
            seen.setdefault(found[0], Counter())[found[1]] += 1
    return {guid: counts.most_common(1)[0][0] for guid, counts in seen.items()}


def merge_shapes(tables: list[dict[int, Shape]]) -> dict[int, Shape]:
    """The commonest shape of each GUID over several islands' tables."""
    counts: dict[int, Counter] = {}
    for table in tables:
        for guid, shape in table.items():
            counts.setdefault(guid, Counter())[shape] += 1
    return {guid: c.most_common(1)[0][0] for guid, c in counts.items()}


def scan_shapes(tree, paths: list[str] | None = None) -> dict[int, Shape]:
    """The footprints of every asset over the islands of a 1701 ``tree`` (all the shipped ones, minutes of work: the
    editor keeps the result once made)."""
    from .isd1701 import islands, load

    return merge_shapes([island_shapes(load(tree, p)) for p in (paths if paths is not None else islands(tree))])


def to_json(table: dict[int, Shape]) -> dict[str, list[int]]:
    return {str(g): list(s) for g, s in table.items()}


def from_json(data: dict[str, list[int]]) -> dict[int, Shape]:
    return {int(g): Shape(*s) for g, s in data.items()}


def vacate(isl: Island1701, handle: int) -> None:
    """Free every tile ``handle`` holds (its owner bits cleared, the other bits kept)."""
    grid = grid_of(isl)
    if grid is None:
        return
    words = read_words(grid)
    words = [w & ~OWNER if w & OCCUPIED and w & OWNER == handle else w for w in words]
    write_words(grid, words)


def occupy(isl: Island1701, node: Node, shape: Shape) -> int:
    """Put the object ``node``'s handle on the tiles its ``shape`` holds at its position and turn now, after freeing the
    tiles it held; the tiles it takes from another object go to it. Returns the tiles held (0: off the island, or the
    turn is between quarters)."""
    grid = grid_of(isl)
    q = quarter_of(node.int("m_GRIDDirection"))
    if grid is None or q is None:
        return 0
    handle = int(node.name, 16)
    x, _y, z = hid_position(node)
    words = [w & ~OWNER if w & OCCUPIED and w & OWNER == handle else w for w in read_words(grid)]
    held = [(i, j) for i, j in cells(x, z, shape, q) if 0 <= i < isl.width and 0 <= j < isl.height]
    for i, j in held:
        words[j * isl.width + i] = (words[j * isl.width + i] & ~OWNER) | OCCUPIED | handle
    write_words(grid, words)
    return len(held)
