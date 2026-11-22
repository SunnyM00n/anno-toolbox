"""The cell 2070 draws a street tile with (streetcells2070): the shape by the neighbours, the turn, the sheet's place."""

import numpy as np
import pytest

from annotoolbox.sources import streetcells2070 as cells2070
from annotoolbox.sources.streetgrid2070 import SStreetGrid

PLUS_Z, PLUS_X, MINUS_Z, MINUS_X = 1, 2, 4, 8


def grid(tiles, bridges=()):
    out = SStreetGrid(16, 16)
    out.tiles = dict.fromkeys(tiles, 10)
    out.bridges = set(bridges)
    return out


@pytest.mark.parametrize(
    ("mask", "first_cell", "turn"),
    [
        (0, cells2070.SINGLE, None),
        (PLUS_X, cells2070.END, 0),  # the end cell opens to +x at turn 0 (measured on the sheets)
        (PLUS_Z, cells2070.END, 3),
        (MINUS_X, cells2070.END, 2),
        (MINUS_Z, cells2070.END, 1),
        (PLUS_X | MINUS_X, cells2070.STRAIGHT, 0),
        (PLUS_Z | MINUS_Z, cells2070.STRAIGHT, 1),
        (PLUS_X | MINUS_Z, cells2070.CORNER, 0),  # the corner has +x and -z at turn 0
        (PLUS_X | PLUS_Z, cells2070.CORNER, 3),  # the corner piece: arms +x and +z, turn 3
        (PLUS_X | MINUS_X | MINUS_Z, cells2070.TEE, 0),  # closed towards +z
        (PLUS_X | MINUS_X | PLUS_Z, cells2070.TEE, 2),
        (15, cells2070.CROSS, None),
    ],
)
def test_the_neighbour_mask_picks_the_shape_and_its_turn(mask, first_cell, turn):
    cell, picked_turn = cells2070.pick(mask, 5, 7)
    assert first_cell <= cell < first_cell + {0: 2, 2: 2, 4: 4, 8: 4, 12: 2, 14: 2}[first_cell]
    if turn is not None:
        assert picked_turn == turn
    else:
        assert 0 <= picked_turn < 4


def test_every_mask_has_a_cell_and_the_pick_is_the_same_each_time():
    for mask in range(16):
        assert cells2070.pick(mask, 3, 4) == cells2070.pick(mask, 3, 4)


def test_a_grid_gets_a_cell_from_each_tiles_neighbours():
    # a row along x with a tile above its middle: end, T, end, and the end of the arm
    found = {c.tile: c for c in cells2070.cells(grid([(1, 1), (2, 1), (3, 1), (2, 2)]))}
    assert found[(1, 1)].cell in range(cells2070.END, cells2070.END + 2) and found[(1, 1)].turn == 0  # opens to +x
    assert found[(3, 1)].cell in range(cells2070.END, cells2070.END + 2) and found[(3, 1)].turn == 2  # opens to -x
    assert found[(2, 1)].cell in range(cells2070.TEE, cells2070.TEE + 2) and found[(2, 1)].turn == 2  # closed to -z
    assert found[(2, 2)].cell in range(cells2070.END, cells2070.END + 2) and found[(2, 2)].turn == 1  # opens to -z


def test_a_bridge_cell_joins_its_neighbours_and_takes_their_type():
    found = {c.tile: c for c in cells2070.cells(grid([(1, 1), (4, 1)], bridges=[(2, 1), (3, 1)]))}
    assert found[(2, 1)].guid == 10 and found[(2, 1)].cell in range(cells2070.STRAIGHT, cells2070.STRAIGHT + 4)
    assert found[(1, 1)].cell in range(cells2070.END, cells2070.END + 2)  # its neighbour is the bridge cell
    assert cells2070.cells(grid([], bridges=[(5, 5)])) == []  # a bridge cell with no street beside it


def test_the_sheet_place_turns_a_quarter_clockwise_and_never_mirrors():
    # the cell's top (t = 1) is at v = 0 at turn 0, at +x (s = 1) one step on
    assert cells2070.tile_uv(0, 0.5, 1.0) == (0.5, 0.0)
    assert cells2070.tile_uv(1, 1.0, 0.5) == (0.5, 0.0)
    # cell 5 is column 1, row 1: its corner (u, v) = (0, 0) lies at (1/4, 1/8) on the sheet
    assert cells2070.sheet_uv(5, 0, 0.0, 1.0) == (0.25, 0.125)
    u, v = cells2070.sheet_uv(31, 2, np.array([0.0, 1.0]), np.array([0.0, 1.0]))
    assert u.shape == (2,) and (u >= 0.75).all() and (v <= 1.0).all()


def test_the_sheets_are_read_by_street_guid(tmp_path):
    class FakeStreetTree:
        def read_bytes(self, path):
            return b"""<Assets>
              <Asset><Template>Street</Template><Values><GUID>10</GUID><Name>Street_Normal</Name>
                <Street><Diffuse>data\\street\\basic.png</Diffuse></Street></Values></Asset>
              <Asset><Template>Street</Template><Values><GUID>18</GUID></Values></Asset>
              <Asset><Template>Street</Template><Values><GUID>11</GUID><Street><Diffuse>data\\none.png</Diffuse></Street>
                </Values></Asset></Assets>"""

        def exists(self, path):
            return False

        def texture_key(self, path):
            return {"data/street/basic.png": "data/street/basic_0.dds"}.get(path)

    assert cells2070.sheets(FakeStreetTree()) == {
        10: "data/street/basic_0.dds"
    }  # 18 has no sheet, 11's file is not there
