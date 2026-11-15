"""The streets an Anno 2070 island stores (``m_GOPManager/m_StreetGrid``): the campaign, scenario and world-event towns.

``read`` gives an island's ``StreetGrid`` (tile -> 2070 street GUID), ``runs`` its straight stretches between tile
centres, the edges a 117 road is built from (docs/roads.md, "Stored on islands").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..formats.isd2070 import Island
from .overlay import SourceTree

GRID = "m_GOPManager/m_StreetGrid"
# one byte a tile, row by row (z * width + x): the street type, its asset GUID less 9; 9 the cell under a bridge
STREET_TYPES = {1: 10, 2: 11, 3: 12, 4: 13, 5: 14, 6: 15}
UNDER_BRIDGE = 9  # Underbridge_Street (18): a bridge building stands over it
# 10 (the underwater building connectors, 19) and 14, 15 are worked out by the game when it loads the island
RUN_TIME = (10, 14, 15)
STEPS = ((1, 0), (0, 1))  # a run goes along x or along z: 2070's streets have no diagonals


@dataclass
class StreetGrid:
    """An island's stored streets: ``tiles`` (x, z) -> 2070 street GUID, ``bridges`` the tiles under a bridge,
    ``skipped`` how many bytes held a value the game works out at load time."""

    width: int
    height: int
    tiles: dict[tuple[int, int], int] = field(default_factory=dict)
    bridges: set[tuple[int, int]] = field(default_factory=set)
    skipped: int = 0


@dataclass(frozen=True)
class Run:
    """A straight stretch of one street type from tile ``start`` to tile ``end`` (inclusive, along x or z); a single
    tile with no neighbour of its type is a run of one tile."""

    guid: int
    start: tuple[int, int]
    end: tuple[int, int]


def read(island: Island) -> StreetGrid | None:
    """The island's stored streets; None when it stores no street grid, an empty grid when no tile has a street."""
    node = island.root.path(GRID)
    if node is None or node.data is None:
        return None
    width, height = island.width, island.height
    grid = StreetGrid(width, height)
    for index, value in enumerate(node.data[: width * height]):
        if not value:
            continue
        tile = (index % width, index // width)
        if value in STREET_TYPES:
            grid.tiles[tile] = STREET_TYPES[value]
        elif value == UNDER_BRIDGE:
            grid.bridges.add(tile)
        else:
            grid.skipped += 1
    return grid


def read_island(tree: SourceTree, path: str) -> StreetGrid | None:
    """``read`` on the ``.isd`` at ``path`` (through the overlay)."""
    return read(Island(tree.read_bytes(path)))


def runs(grid: StreetGrid) -> list[Run]:
    """The grid as straight runs of one street type: every pair of neighbouring tiles of one type lies in exactly
    one run, a tile with no such neighbour is a run of its own. A junction's tile ends or lies in a run each way."""
    out: list[Run] = []
    linked: set[tuple[int, int]] = set()
    for dx, dz in STEPS:
        for (x, z), guid in sorted(grid.tiles.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            if grid.tiles.get((x - dx, z - dz)) == guid or grid.tiles.get((x + dx, z + dz)) != guid:
                continue  # not the first tile of a run this way
            end = (x, z)
            while grid.tiles.get((end[0] + dx, end[1] + dz)) == guid:
                end = (end[0] + dx, end[1] + dz)
            out.append(Run(guid, (x, z), end))
            k = 0
            while (x + k * dx, z + k * dz) != (end[0] + dx, end[1] + dz):
                linked.add((x + k * dx, z + k * dz))
                k += 1
    out += [Run(guid, tile, tile) for tile, guid in sorted(grid.tiles.items()) if tile not in linked]
    return out
