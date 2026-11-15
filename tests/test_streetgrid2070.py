"""The streets a 2070 island stores (streetgrid2070): the grid's byte order and types, and its straight runs."""

import os

import pytest

from annotoolbox.formats.isd2070 import Island, Node
from annotoolbox.sources import streetgrid2070


def island(width, height, cells):
    """An island of ``width`` x ``height`` tiles whose street grid holds ``cells`` ((x, z) -> byte)."""
    data = bytearray(width * height)
    for (x, z), v in cells.items():
        data[z * width + x] = v
    gop = Node("m_GOPManager", children=[Node("m_StreetGrid", data=bytes(data))])
    return Island.from_tree(Node("", children=[Node("Width", str(width)), Node("Height", str(height)), gop]))


def test_the_grid_is_read_row_by_row_with_the_street_guid_less_nine():
    grid = streetgrid2070.read(island(8, 4, {(5, 1): 1, (6, 1): 2, (2, 3): 6, (3, 3): 9, (0, 0): 14}))
    assert grid.tiles == {(5, 1): 10, (6, 1): 11, (2, 3): 15}  # x along a row, z the row
    assert grid.bridges == {(3, 3)} and grid.skipped == 1  # 14: worked out by the game at load time
    assert streetgrid2070.read(Island.from_tree(Node("", children=[Node("Width", "8")]))) is None


def test_runs_cover_every_link_once_and_split_at_a_change_of_type():
    # an L of the plain road, a T arm of asphalt, a lone tile
    cells = {(x, 2): 1 for x in range(1, 6)} | {(1, z): 1 for z in (3, 4)} | {(3, 1): 2, (3, 0): 2, (7, 7): 4}
    grid = streetgrid2070.read(island(8, 8, cells))
    got = {(r.guid, r.start, r.end) for r in streetgrid2070.runs(grid)}
    assert got == {
        (10, (1, 2), (5, 2)),  # along x
        (10, (1, 2), (1, 4)),  # along z from the corner
        (11, (3, 0), (3, 1)),  # the asphalt arm: its own type, its own run
        (13, (7, 7), (7, 7)),  # a single tile
    }


ISLAND = "data/levels/islands/campaign/chapter 03/chap3_n_m11.isd"  # a campaign town of 646 street tiles


@pytest.mark.skipif(not os.environ.get("ANNO2070_DIR"), reason="needs the installed Anno 2070 (ANNO2070_DIR)")
def test_a_stored_town_lies_on_2070s_flat_plateau_in_the_grids_order():
    """2070 lays its streets on the flat 0.7 plateau (roads "Where streets lie"): read in the grid's order most street
    tiles are flat at 0.7, read transposed far fewer, which pins x along a row."""
    from annotoolbox.sources.overlay import SourceTree

    tree = SourceTree(os.environ["ANNO2070_DIR"], language="eng")
    isl = Island(tree.read_bytes(ISLAND))
    grid = streetgrid2070.read(isl)
    assert grid and len(grid.tiles) > 600 and set(grid.tiles.values()) <= set(streetgrid2070.STREET_TYPES.values())
    h = isl.heightmap()

    def flat(x, z):
        c = (h[z][x], h[z][x + 1], h[z + 1][x], h[z + 1][x + 1])
        return max(c) - min(c) <= 0.01 and abs(c[0] - 0.7) < 0.01

    share = sum(flat(x, z) for x, z in grid.tiles) / len(grid.tiles)
    transposed = sum(flat(z, x) for x, z in grid.tiles) / len(grid.tiles)
    assert share > 0.95 and transposed < share - 0.3  # 0.99 against 0.55
    assert streetgrid2070.runs(grid)
