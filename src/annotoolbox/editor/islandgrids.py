"""The gameplay grids of a vanilla new-engine island that an edit sets cell by cell: ``ford`` (``WorldManager/FordGrid``,
the river tiles units may cross) by ``apply_ford`` and ``environment`` (``WorldManager/EnvironmentGrid``, a zone per
4 x 4 tiles) by ``apply_environment``; the rest are in docs/island-editor.md ("Island data").

A ford edit is ``edits["ford"]``: ``"x,z"`` -> 1 (a ford) or 0 (none); a ford lies on a river tile in every shipped island
checked (``RiverGrid``), so a cell off the river is not written and is listed in the report. An environment edit is
``edits["environment"]``: ``"x,z"`` -> the zone, a number of the game's ``EnvironmentType`` (``ZONES``).
"""

from __future__ import annotations

import numpy as np

from ..infra.i18n import N_, tr
from ..islands.islandgrid import cell_key, read_bits, write_bits

ZONES = (  # (number, name): the zones of the game's EnvironmentType the grid holds, the ones painted most first
    (8, N_("islands.meadow")), (4, N_("islands.forest")), (9, N_("islands.mountain")), (10, N_("islands.mountain_top")), (1, N_("islands.coast_rock")),
    (2, N_("islands.coast_sand")), (7, N_("islands.marsh")), (5, N_("islands.lake")), (6, N_("islands.lake_shore")), (3, N_("islands.field")),
    (31, N_("islands.desert")), (29, N_("islands.river_crossing")), (30, N_("islands.embankment")), (20, N_("islands.sea")),
    (21, N_("islands.sea_near_coast")), (32, N_("islands.sea_calm")), (0, N_("islands.none")),
)  # fmt: skip


def parse_cells(cells: dict, size_cells: int, is_bit: bool = True) -> tuple[list[tuple[int, int, int]], list[str]]:
    """The (x, z, value) of the edit's cells that lie on the island and the keys of those that do not; ``bit``: the
    value is on or off (1 / 0), else a byte (a value off 0 to 255 is off too)."""
    kept, off = [], []
    for key, value in sorted(cells.items()):
        cell = cell_key(key, size_cells)
        try:
            value = (1 if value else 0) if is_bit else int(value)
        except (TypeError, ValueError):
            cell = None
        if cell is not None and 0 <= value <= 255:
            kept.append((*cell, value))
        else:
            off.append(str(key))
    return kept, off


def apply_ford(island, cells: dict) -> dict:
    """Set the ford cells (``"x,z"`` -> 1 or 0) in the island's ``FordGrid``; only a tile of the ``RiverGrid`` can be a
    ford. Returns the cells changed and the ones left out (``unmatched``)."""
    world_manager = island.game_session_manager.path("WorldManager")
    ford, river = (
        (world_manager.find("FordGrid"), world_manager.find("RiverGrid")) if world_manager is not None else (None, None)
    )
    report = {"cells": 0, "unmatched": []}
    if ford is None or river is None:
        report["unmatched"].append(tr("islands.no_ford_grid_island"))
        return report
    grid = np.asarray(read_bits(ford), np.int64)
    on_river = np.asarray(read_bits(river), bool)
    kept, off = parse_cells(cells, grid.shape[0])
    report["unmatched"] += [tr("islands.ford_key", key=key) for key in off]
    for x, z, value in kept:
        if value and not on_river[z, x]:
            report["unmatched"].append(tr("islands.ford_key", key=f"{x},{z}"))
        elif grid[z, x] != value:
            grid[z, x] = value
            report["cells"] += 1
    if report["cells"]:
        write_bits(ford, grid.tolist())
    return report


def apply_environment(island, cells: dict) -> dict:
    """Set the zone cells (``"x,z"`` -> a number 0 to 255) in the island's ``EnvironmentGrid``. Returns the cells
    changed and the ones left out (``unmatched``)."""
    grid = island.game_session_manager.path("WorldManager/EnvironmentGrid/EnvironmentGRid")
    report = {"cells": 0, "unmatched": []}
    if grid is None:
        report["unmatched"].append(tr("islands.no_environment_grid_island"))
        return report
    grid_size = grid.find("x").u32()
    zones = bytearray(grid.find("val").data)
    kept, off = parse_cells(cells, grid_size, is_bit=False)
    report["unmatched"] += [tr("islands.environment_key", key=key) for key in off]
    for x, z, value in kept:
        if zones[z * grid_size + x] != value:
            zones[z * grid_size + x] = value
            report["cells"] += 1
    if report["cells"]:
        grid.find("val").data = bytes(zones)
    return report
