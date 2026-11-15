"""The coast-wave byte of an island's sea map: byte 1 of the render data's ``WaterInfos`` (one cell per 2 x 2 tiles,
four bytes a cell: depth, coast waves, beach, foam). The sea shader draws coast waves where it is set; vanilla islands
carry 255 nearly everywhere and lower values along some coasts (rendering/water.md "Physical waves and coast waves").

``read`` gives the cells of an opened island (``IslandFile``), ``apply`` writes the edit file's ``surf`` into them
(``"cx,cz"`` -> 0..255, only the cells that differ from the island's own). Format: island-editor-tools "The edit file".
"""

from __future__ import annotations

import numpy as np

CHANNEL = 1  # the coast-wave byte of a cell


def _cells(isl) -> tuple[np.ndarray, object] | None:
    wi = isl.rd.path("WaterInfos")
    if wi is None or wi.find("map") is None:
        return None
    w = wi.find("width").u32()
    return np.frombuffer(wi.find("map").data, np.uint8).reshape(w, w, -1).copy(), wi


def read(isl) -> np.ndarray | None:
    """The island's coast-wave byte per cell, rows z, as ``uint8``; None when it has no sea map."""
    found = _cells(isl)
    return None if found is None else found[0][..., CHANNEL].copy()


def apply(isl, changes: dict | None) -> dict:
    """Set the cells of ``changes`` (``"cx,cz"`` -> value) in the island's sea map; a cell off the map or a value off
    0..255 is left out. Returns the build report entry."""
    found = _cells(isl) if changes else None
    if found is None:
        return {}
    info, wi = found
    n, done = info.shape[0], 0
    for key, value in changes.items():
        cx, cz = map(int, key.split(","))
        if 0 <= cx < n and 0 <= cz < n and 0 <= int(value) <= 255:
            info[cz, cx, CHANNEL] = int(value)
            done += 1
    wi.find("map").data = info.tobytes()
    return {"surf cells": done} if done else {}
