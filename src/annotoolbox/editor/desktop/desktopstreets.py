"""The streets of an Anno 2070 island as the view's decals: one tile a decal, wearing the cell of its street type's sheet
that its neighbours pick (``sources.streetcells2070``), laid on the drawn ground by the decal layer
(``desktopdecals``) in the street pass (``render.decalmesh.STREET_CELLS``). island-editor-view "Streets of 2070 islands".
"""

from __future__ import annotations

import numpy as np

from ...render import decalmesh
from ...sources import streetcells2070
from ...sources.streetgrid2070 import SStreetGrid

TILE_EXTENT = (0.5, 0.5)  # half a tile each way


def grid_of(streets: dict) -> SStreetGrid:
    """The island data's ``streets`` (tiles [x, z, street GUID], bridges [x, z], the sheet of each street type) as the
    reader's grid; ``width`` and ``height`` are not needed to pick cells."""
    grid = SStreetGrid(0, 0)
    grid.tiles = {(int(x), int(z)): int(guid) for x, z, guid in streets.get("tiles", ())}
    grid.bridges = {(int(x), int(z)) for x, z in streets.get("bridges", ())}
    return grid


def entries(streets: dict | None, size_tiles: float) -> list[tuple[np.ndarray, list[dict]]]:
    """What ``CDecalLayer.update`` takes as ``placed`` for the island's street tiles: per tile the matrix that puts a
    unit decal on it in the view's space (x mirrored: ``size_tiles - x``) and its decal, whose ``retexture`` maps the
    flat rectangle's uv (u along the view's x, v along z) onto the tile's cell. A street without a sheet is left out."""
    if not streets:
        return []
    sheets = {int(guid): path for guid, path in streets.get("sheets", {}).items()}
    out = []
    for cell in streetcells2070.cells(grid_of(streets)):
        sheet = sheets.get(cell.guid)
        if sheet is None:
            continue
        place = np.identity(4, "f4")
        place[0, 3], place[2, 3] = size_tiles - (cell.tile[0] + 0.5), cell.tile[1] + 0.5
        decal = {
            "kind": decalmesh.STREET_KIND,
            "on_streets": False,
            "diffuse": sheet,
            "normal": "",
            "color": (1.0, 1.0, 1.0),
            "opacity": 1.0,
            "extents": TILE_EXTENT,
            "matrix": np.identity(4, "f4"),
            "retexture": _retexture(cell.cell, cell.turn),
        }
        out.append((place[None], [decal]))
    return out


def _retexture(cell: int, turn: int):
    def uv_of(rectangle_uv: np.ndarray) -> np.ndarray:
        """The sheet's uv of the tile's points: the view's x runs against 2070's, so s = 1 - u."""
        return np.stack(streetcells2070.sheet_uv(cell, turn, 1.0 - rectangle_uv[:, 0], rectangle_uv[:, 1]), 1)

    return uv_of
