"""Snapping places and turns to the game's grids."""

import math

import pytest

from annotoolbox.editor import snapping


@pytest.mark.parametrize(
    ("mode", "point", "want"),
    [
        ("tile", (3.2, 7.9), (3.5, 7.5)),
        ("corner", (3.2, 7.9), (3.0, 8.0)),
        ("half", (3.2, 7.8), (3.0, 8.0)),
        ("quarter", (3.2, 7.8), (3.25, 7.75)),
        ("cell", (11.0, 13.0), (8.0, 16.0)),
        ("off", (3.2, 7.8), (3.2, 7.8)),
    ],
)
def test_the_grid_modes(mode, point, want):
    assert snapping.snap_xz(*point, mode) == pytest.approx(want)


def test_the_diagonal_lattice_holds_tile_corners_and_tile_centres_only():
    for x, z in ((3.4, 7.6), (3.1, 7.05), (2.7, 2.2), (10.49, 0.51)):
        sx, sz = snapping.diagonal(x, z)
        assert (sx + sz).is_integer() and (sz - sx).is_integer()
        assert math.hypot(sx - x, sz - z) <= 0.5 + 1e-9  # the lattice's cells are half a tile across their middle
    assert snapping.diagonal(3.45, 7.55) == (3.5, 7.5)


def test_turns_step_by_eighths_and_a_small_negative_turn_reads_as_none():
    assert snapping.eighth(math.radians(-20)) == 0.0
    assert snapping.eighth(math.radians(50)) == pytest.approx(math.pi / 4)
    assert snapping.snap_yaw(math.radians(-50), "eighth") == pytest.approx(7 * math.pi / 4)
    assert snapping.snap_yaw(math.radians(22), "15") == pytest.approx(math.radians(15))
    assert snapping.snap_yaw(1.234, "off") == 1.234


def test_a_snap_setting_puts_a_place_on_the_ground():
    snap = snapping.Snap(mode="tile", ground=True)
    assert snap.place([3.2, 9.0, 7.9], lambda x, z: x + z) == [3.5, 11.0, 7.5]
    with pytest.raises(ValueError):
        snapping.snap_xz(0, 0, "hex")
