"""Where an island's herds roam: ``AnimalManager/HerdGrid`` (a bit a tile) and ``HerdAreas`` (one entry per connected
region of it, carrying the region's tile count). The island build draws them from the animals' paths; the editor's herd
brush (``edits["herd"]``, ``"x,z"`` -> 1 or 0) changes tiles of the grid and the areas are drawn again from it
(island-editor "Herd areas").
"""

from __future__ import annotations

import copy
import struct

import numpy as np

from ..formats import filedb
from ..infra.i18n import tr
from .islandgrid import bits_array, cell_key, write_bits

HERD_AREA_MIN = 40  # tiles: a smaller patch is not worth an area of its own


def entries(roam: np.ndarray, template: filedb.STag, minimum: int = HERD_AREA_MIN) -> tuple[list, np.ndarray]:
    """One ``HerdAreas`` entry per connected region of ``roam`` (a copy of ``template`` with the region's tile count;
    the rest of an entry is run-time state, zero in the shipped islands) and ``roam`` without the regions below
    ``minimum`` tiles."""
    from scipy import ndimage

    roam = np.array(roam, bool)
    labels, count = ndimage.label(roam)
    sizes = np.bincount(labels.ravel(), minlength=count + 1)  # one pass, not one scan of the grid per region
    roam[(sizes < minimum)[labels] & (labels > 0)] = False
    rows = []
    for i in range(1, count + 1):
        if sizes[i] < minimum:
            continue
        entry = copy.deepcopy(template)
        entry.find("CellsCount").data = struct.pack("<I", int(sizes[i]))
        entry.find("BBox").data = bytes(len(entry.find("BBox").data))
        entry.find("RandomPoint").data = bytes(len(entry.find("RandomPoint").data))
        rows.append(entry)
    return rows, roam


def _bits(session_manager: filedb.STag, name: str, shape: tuple) -> np.ndarray:
    """A ``WorldManager`` bit grid, empty (no tile) when the island has none or another size."""
    tag = session_manager.path(f"WorldManager/{name}")
    grid = bits_array(tag) if tag is not None and tag.find("bits") is not None else None
    return grid if grid is not None and grid.shape == shape else np.zeros(shape, bool)


def free_tiles(session_manager: filedb.STag, shape: tuple) -> np.ndarray:
    """The tiles a herd may roam on: land that is no river (none when the island lacks either grid)."""
    land, river = (_bits(session_manager, name, shape) for name in ("Water", "RiverGrid"))
    return land & ~river


def read(session_manager: filedb.STag) -> np.ndarray | None:
    """The herd grid as booleans (rows z), None for an island without one."""
    manager = session_manager.path("AnimalManager")
    grid = manager.find("HerdGrid") if manager is not None else None
    return bits_array(grid) if grid is not None else None


def apply(session_manager: filedb.STag, cells: dict, template: filedb.STag | None = None) -> dict:
    """Set herd tiles (``"x,z"`` -> 1 roams, 0 none) and draw the areas again from the grid. A tile off the grid, in the
    water or on a river, and any edit of an island with no grid or with no entry to copy (``template``, else the island's own first), is ``unmatched``.
    Returns ``tiles`` (changed), ``dropped`` (patches under the minimum), ``areas`` (now) and ``unmatched``."""
    report: dict = {"tiles": 0, "areas": 0, "unmatched": []}
    if not cells:
        return report
    manager = session_manager.path("AnimalManager")
    tag = manager.find("HerdGrid") if manager is not None else None
    areas = manager.find("HerdAreas") if manager is not None else None
    template = next(
        (child for child in (areas.children if areas is not None else []) if isinstance(child, filedb.STag)), template
    )
    if tag is None or areas is None or template is None:
        report["unmatched"].append(tr("islands.no_herd_grid_island"))
        return report
    roam = bits_array(tag)
    free = free_tiles(session_manager, roam.shape)
    for name, value in sorted(cells.items()):
        cell = cell_key(name, roam.shape[0])
        if cell is None or (value and not free[cell[1], cell[0]]):
            report["unmatched"].append(tr("islands.herd_tile_key", key=name))
            continue
        x, z = cell
        report["tiles"] += int(roam[z, x] != bool(value))
        roam[z, x] = bool(value)
    if not report["tiles"]:  # nothing changed: the island's own areas stay as its file draws them
        report["areas"] = len([child for child in areas.children if isinstance(child, filedb.STag)])
        return report
    before = int(roam.sum())
    rows, roam = entries(roam, template)
    report["dropped"] = before - int(roam.sum())  # a patch under the minimum is not a herd area
    areas.children = rows
    write_bits(tag, roam.astype(int).tolist())
    report["areas"] = len(rows)
    return report
