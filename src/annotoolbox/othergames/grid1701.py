"""1701's occupation grid (``m_GRIDManager/m_GRID``) and the footprints read off it: which tiles a building holds.

A game object that holds tiles has its handle in the low 28 bits of each tile's word (bit 30 set); a footprint is a
rectangle of ``SShape`` tiles, centred on the object's position by its ``m_GRIDOffsetX/Z`` and turned by quarter turns
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

from ..formats.isd2070 import SNode
from .isd1701 import CIsland1701, hid_position, islands, load

OWNER = 0x0FFFFFFF  # the low bits of an occupied tile: the occupying object's handle
OCCUPIED = 0x40000000  # set on every tile a handle holds in the shipped files
OFFSET_UNITS = 32.0  # ``m_GRIDOffsetX/Z`` counts world units, 32 to a tile
GRID_PATH = "m_GOPManager/m_GRIDManager/m_GRID"


class SShape(NamedTuple):
    """A footprint: ``w`` x ``h`` tiles unturned, the object's origin ``ox`` / ``oz`` world units past the corner of the
    tile it stands in (the file's grid offsets)."""

    w: int
    h: int
    ox: int = 0
    oz: int = 0


def grid_of(isl: CIsland1701) -> SNode | None:
    """The ``m_GRID`` node when it holds one word per tile, else None (old levels carry none)."""
    node = isl.m_root.path(GRID_PATH)
    ok = node is not None and node.data is not None and len(node.data) == 4 * isl.m_width * isl.m_height
    return node if ok else None


def read_words(node: SNode) -> list[int]:
    return list(struct.unpack(f"<{len(node.data) // 4}I", node.data))


def write_words(node: SNode, words: list[int]) -> None:
    node.data = struct.pack(f"<{len(words)}I", *words)


def quarter_of(grid_direction: int) -> int | None:
    """The quarter turns (0 to 3) of ``m_GRIDDirection`` (64ths of a turn); None between quarters, where a footprint
    is not on the grid."""
    return grid_direction // 16 % 4 if grid_direction % 16 == 0 else None


def corners(shape: SShape) -> tuple[float, float, float, float]:
    """The unturned footprint's x0, z0, x1, z1 in tiles from the object's origin: the cells are
    ``[-w // 2, w - w // 2)`` round the origin's tile, the origin ``ox`` / ``oz`` into it."""
    x0, z0 = -(shape.w // 2) - shape.ox / OFFSET_UNITS, -(shape.h // 2) - shape.oz / OFFSET_UNITS
    return x0, z0, x0 + shape.w, z0 + shape.h


def polygon(shape: SShape) -> list[tuple[float, float]]:
    """The unturned footprint as a polygon (the corners, tiles from the origin) as ``editor.footprints`` takes it."""
    x0, z0, x1, z1 = corners(shape)
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def cells(x: float, z: float, shape: SShape, quarter: int) -> set[tuple[int, int]]:
    """The tiles (x, z) of the island a ``shape`` at origin (``x``, ``z``) tiles, turned ``quarter`` quarter turns, holds."""
    x0, z0, x1, z1 = corners(shape)
    pts = [(x0, z0), (x1, z1)]
    for _ in range(quarter % 4):  # a quarter turn takes (dx, dz) to (-dz, dx)
        pts = [(-corner_z, corner_x) for corner_x, corner_z in pts]
    xs = sorted(math.floor(x + corner[0] + 0.5) for corner in pts)  # corners are whole tiles; no banker's rounding
    zs = sorted(math.floor(z + corner[1] + 0.5) for corner in pts)
    return {(i, j) for i in range(xs[0], xs[1]) for j in range(zs[0], zs[1])}


def holds(words: list[int], width_tiles: int, handle: int) -> set[tuple[int, int]]:
    """The tiles whose word names ``handle``."""
    return {
        (k % width_tiles, k // width_tiles)
        for k, word in enumerate(words)
        if word & OWNER == handle and word & OCCUPIED
    }


def _shape_of_object(node: SNode, held: set[tuple[int, int]]) -> tuple[int, SShape] | None:
    """The GUID and unturned shape of an object whose held tiles are a whole rectangle at a quarter turn."""
    quarter = quarter_of(node.int("m_GRIDDirection"))
    xs, zs = [cell[0] for cell in held], [cell[1] for cell in held]
    width_tiles, height_tiles = max(xs) - min(xs) + 1, max(zs) - min(zs) + 1
    if quarter is None or width_tiles * height_tiles != len(held):
        return None
    width_tiles, height_tiles = (width_tiles, height_tiles) if quarter % 2 == 0 else (height_tiles, width_tiles)
    return node.int("m_GUID"), SShape(width_tiles, height_tiles, node.int("m_GRIDOffsetX"), node.int("m_GRIDOffsetZ"))


def island_shapes(isl: CIsland1701) -> dict[int, SShape]:
    """GUID -> its footprint, from the island's own objects that hold tiles (the commonest shape of an asset)."""
    grid = grid_of(isl)
    if grid is None:
        return {}
    owner: dict[int, set[tuple[int, int]]] = {}
    for k, word in enumerate(read_words(grid)):
        if word & OCCUPIED and word & OWNER:
            owner.setdefault(word & OWNER, set()).add((k % isl.m_width, k // isl.m_width))
    seen: dict[int, Counter] = {}
    for kind, node in isl.object_nodes():
        held = owner.get(int(node.name, 16)) if kind == "HID" else None
        found = _shape_of_object(node, held) if held else None
        if found is not None:
            seen.setdefault(found[0], Counter())[found[1]] += 1
    return {guid: counts.most_common(1)[0][0] for guid, counts in seen.items()}


def merge_shapes(tables: list[dict[int, SShape]]) -> dict[int, SShape]:
    """The commonest shape of each GUID over several islands' tables."""
    counts: dict[int, Counter] = {}
    for table in tables:
        for guid, shape in table.items():
            counts.setdefault(guid, Counter())[shape] += 1
    return {guid: counter.most_common(1)[0][0] for guid, counter in counts.items()}


def scan_shapes(tree, paths: list[str] | None = None) -> dict[int, SShape]:
    """The footprints of every asset over the islands of a 1701 ``tree`` (all the shipped ones, minutes of work: the
    editor keeps the result once made)."""
    return merge_shapes([island_shapes(load(tree, path)) for path in (paths if paths is not None else islands(tree))])


def to_json(table: dict[int, SShape]) -> dict[str, list[int]]:
    return {str(guid): list(shape) for guid, shape in table.items()}


def from_json(data: dict[str, list[int]]) -> dict[int, SShape]:
    return {int(guid): SShape(*shape) for guid, shape in data.items()}


def vacate(isl: CIsland1701, handle: int) -> None:
    """Free every tile ``handle`` holds (its owner bits cleared, the other bits kept)."""
    grid = grid_of(isl)
    if grid is None:
        return
    words = read_words(grid)
    words = [word & ~OWNER if word & OCCUPIED and word & OWNER == handle else word for word in words]
    write_words(grid, words)


def occupy(isl: CIsland1701, node: SNode, shape: SShape) -> int:
    """Put the object ``node``'s handle on the tiles its ``shape`` holds at its position and turn now, after freeing the
    tiles it held; the tiles it takes from another object go to it. Returns the tiles held (0: off the island, or the
    turn is between quarters)."""
    grid = grid_of(isl)
    quarter = quarter_of(node.int("m_GRIDDirection"))
    if grid is None or quarter is None:
        return 0
    handle = int(node.name, 16)
    x, _y, z = hid_position(node)
    words = [word & ~OWNER if word & OCCUPIED and word & OWNER == handle else word for word in read_words(grid)]
    held = [(i, j) for i, j in cells(x, z, shape, quarter) if 0 <= i < isl.m_width and 0 <= j < isl.m_height]
    for i, j in held:
        words[j * isl.m_width + i] = (words[j * isl.m_width + i] & ~OWNER) | OCCUPIED | handle
    write_words(grid, words)
    return len(held)
