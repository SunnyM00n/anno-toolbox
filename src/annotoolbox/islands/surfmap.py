"""The coast-wave byte of an island's sea map: byte 1 of the render data's ``WaterInfos`` (one cell per 2 x 2 tiles,
four bytes a cell: depth, coast waves, beach, foam). The sea shader draws coast waves where it is set; vanilla islands
carry 255 nearly everywhere and lower values along some coasts (rendering/water.md "Physical waves and coast waves").

``read`` gives the cells of an opened island (``CIslandFile``), ``apply`` writes the edit file's ``surf`` into them
(``"cx,cz"`` -> 0..255, only the cells that differ from the island's own). Format: island-editor-tools "The edit file".
"""

from __future__ import annotations

import numpy as np

from .islandgrid import cell_key

CHANNEL = 1  # the coast-wave byte of a cell


def _cells(island) -> tuple[np.ndarray, object] | None:
    water_infos = island.rd3d.path("WaterInfos")
    if water_infos is None or water_infos.find("map") is None:
        return None
    width = water_infos.find("width").u32()
    return np.frombuffer(water_infos.find("map").data, np.uint8).reshape(width, width, -1).copy(), water_infos


def read(island) -> np.ndarray | None:
    """The island's coast-wave byte per cell, rows z, as ``uint8``; None when it has no sea map."""
    found = _cells(island)
    return None if found is None else found[0][..., CHANNEL].copy()


def apply(island, changes: dict | None) -> dict:
    """Set the cells of ``changes`` (``"cx,cz"`` -> value) in the island's sea map; a cell off the map or a value off
    0..255 is left out. Returns the build report entry."""
    found = _cells(island) if changes else None
    if found is None:
        return {}
    cells, water_infos = found
    size, done = cells.shape[0], 0
    for key, value in changes.items():
        cell = cell_key(key, size)
        try:
            byte = int(value)
        except (TypeError, ValueError):
            continue
        if cell is not None and 0 <= byte <= 255:
            cells[cell[1], cell[0], CHANNEL] = byte
            done += 1
    water_infos.find("map").data = cells.tobytes()
    return {"surf cells": done} if done else {}
