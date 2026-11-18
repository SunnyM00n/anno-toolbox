"""The blocking lists of an Anno 1701 island's rivers (``RiverBlockings`` tiles, ``RiverPathBlockings`` half-tile
cells), redrawn after the river is edited: ``redraw``.

The game's rule is not known; the lists are matched to the shipped rivers by a cell's distance to the ribbon
(docs/formats/anno1701-world-format.md, "Rivers' blocking lists"). An edit keeps every entry where the river did not
move: it drops those the old ribbon held and the new one does not reach, and adds the cells the new one covers.
"""

from __future__ import annotations

import copy
import struct

import numpy as np

from ....formats.isd2070 import Node
from ....othergames.isd1701 import TILE, list_items

SAMPLES = 5  # points a side that stand for a cell
TILE_RULE, TILE_REACH = 0.0, 4.0  # gap to the ribbon's edge (world units): listed within RULE, held within REACH
CELL_RULE, CELL_REACH = 4.0, 14.0  # the same for the path cells
FIXED = 4096  # path cell positions: units to a tile
HALF = TILE / 2

Ribbon = tuple[np.ndarray, np.ndarray]  # points (n, 2) in world units (x, z), the width at each


def ribbon_gaps(ribbon: Ribbon, cell: float, reach: float, limit: int) -> dict[tuple[int, int], float]:
    """The cells (``cell`` world units a side, within ``limit`` cells of the island's edge) that lie within ``reach`` of
    the ribbon's edge, each with its smallest gap: the distance of its nearest sample point to the line minus half
    the width there (negative inside the ribbon)."""
    pts, widths = ribbon
    out: dict[tuple[int, int], float] = {}
    grid = (np.arange(SAMPLES) + 0.5) / SAMPLES
    inner = np.stack(np.meshgrid(grid, grid), axis=-1).reshape(-1, 2)
    for a, b, w in zip(pts[:-1], pts[1:], (widths[:-1] + widths[1:]) / 2, strict=True):
        lo = np.floor((np.minimum(a, b) - w / 2 - reach) / cell).astype(int).clip(0, limit - 1)
        hi = np.floor((np.maximum(a, b) + w / 2 + reach) / cell).astype(int).clip(0, limit - 1)
        cx, cz = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1))
        cells = np.stack([cx.ravel(), cz.ravel()], axis=1)
        points = (cells[:, None, :] + inner[None, :, :]) * cell
        ab = b - a
        t = np.clip(((points - a) @ ab) / max(float(ab @ ab), 1e-9), 0, 1)
        gap = (np.linalg.norm(points - (a + t[..., None] * ab), axis=-1) - w / 2).min(axis=1)
        for (x, z), g in zip(cells.tolist(), gap.tolist(), strict=True):
            if g <= reach and g < out.get((x, z), np.inf):
                out[(x, z)] = g
    return out


def _tile_entries(header: Node) -> list[tuple[int, int]]:
    node = header.find("RiverBlockings")
    return [(int(i.value("x", 0)), int(i.value("y", 0))) for i in list_items(node)] if node is not None else []


def _cell_entries(header: Node) -> list[tuple[int, int]]:
    node = header.find("RiverPathBlockings")
    out = []
    for item in list_items(node) if node is not None else []:
        data = item.find("data")
        if data is not None and data.data is not None and len(data.data) == 8:
            x, z = struct.unpack("<ii", data.data)
            out.append((int(x // (FIXED // 2)), int(z // (FIXED // 2))))
    return out


def set_list(node: Node, items: list[Node]) -> None:
    """Make ``items`` the elements of the list ``node`` in its generation's form: ``i`` each, or numbered
    ``i0000``... with ``arraysize`` counting them (what else the node holds stays in front)."""
    old = {id(c) for c in list_items(node)}
    size = node.find("arraysize")
    for n, item in enumerate(items):
        item.name = f"i{n:04d}" if size is not None else "i"
    node.children = [c for c in node.children if id(c) not in old] + items
    if size is not None:
        size.text = str(len(items))


def _set_items(node: Node, cells: list[tuple[int, int]], make) -> None:
    """The list ``node`` holding ``cells``: each item a copy of the first old one (or fresh) set by ``make(item, cell)``."""
    old = list_items(node)
    items = []
    for cell in cells:
        item = copy.deepcopy(old[0]) if old else Node("i")
        make(item, cell)
        items.append(item)
    set_list(node, items)


def _make_tile(item: Node, cell: tuple[int, int]) -> None:
    for name, value in zip("xy", cell, strict=True):
        child = item.find(name)
        if child is None:
            child = Node(name)
            item.children.append(child)
        child.text = str(value)


def _make_cell(item: Node, cell: tuple[int, int]) -> None:
    data = item.find("data")
    if data is None:
        data = Node("data")
        item.children.append(data)
    # the centre of the half-tile cell: a quarter tile in, as the shipped lists have it
    data.data = struct.pack("<ii", *((2 * c + 1) * (FIXED // 4) for c in cell))


def _redraw(old: list[tuple[int, int]], before, after, others, rule: float, reach: float, cell: float, limit: int):
    """``old`` entries with those the move of one ribbon frees gone and the cells the new ribbon covers added."""
    near_before = ribbon_gaps(before, cell, reach, limit)
    near_after = ribbon_gaps(after, cell, reach, limit)
    held = set()
    for other in others:
        held |= set(ribbon_gaps(other, cell, reach, limit))
    freed = set(near_before) - set(near_after) - held
    kept = [c for c in old if c not in freed]
    present = set(kept)
    added = sorted(c for c, g in near_after.items() if g <= rule and c not in present)
    return kept + added, len(old) - len(kept), len(added)


def redraw(header: Node, limit: int, before: Ribbon, after: Ribbon, others: list[Ribbon]) -> dict:
    """Redraw both lists of ``header`` for a river that moved from ``before`` to ``after`` (``others``: the island's
    other rivers, whose entries stay); ``limit``: the island's width in tiles. Returns the entries removed and added."""
    report = {}
    for name, entries, make, rule, reach, cell, per in (
        ("RiverBlockings", _tile_entries, _make_tile, TILE_RULE, TILE_REACH, TILE, 1),
        ("RiverPathBlockings", _cell_entries, _make_cell, CELL_RULE, CELL_REACH, HALF, 2),
    ):
        node = header.find(name)
        if node is None:
            continue
        cells, gone, new = _redraw(entries(header), before, after, others, rule, reach, cell, limit * per)
        if gone or new:
            _set_items(node, cells, make)
        report[name] = {"removed": gone, "added": new}
    return report
