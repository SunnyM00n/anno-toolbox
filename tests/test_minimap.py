"""The editor's minimap picture (``editor.minimap``) on synthetic heights: land, coast, mountains and underwater
plateaus."""

import numpy as np
import pytest

from annotoolbox.editor import minimap


def _island(n: int = 64) -> np.ndarray:
    h = np.full((n, n), -40.0, np.float32)
    h[16:48, 16:48] = 5.0  # land
    h[28:36, 28:36] = 60.0  # a mountain
    return h


def test_land_has_a_coast_and_a_mountain_colour():
    img = minimap.paint(_island(), 64, underwater=False)
    assert img.shape == (64, 64, 4) and img.dtype == np.uint8
    assert img[2, 2, 3] == 0  # sea is clear
    normal, mountain = img[20, 20], img[32, 32]
    assert normal[3] == 255 and mountain[3] == 255
    assert tuple(normal[:3]) != tuple(mountain[:3])
    assert tuple(mountain[:3]) == minimap.LAND_COLORS["mountain"]  # at 40 and above
    assert 0 < img[16, 32, 3] < 255 or img[15, 32, 3] > 0  # the coast fades in over a few pixels
    assert tuple(img[16, 32, :3]) != tuple(normal[:3])


def test_colors_are_replaceable():
    img = minimap.paint(_island(), 64, colors={"normal": (1, 2, 3), "mountain": (1, 2, 3)}, underwater=False)
    assert tuple(img[20, 20, :3]) == (1, 2, 3)


@pytest.mark.parametrize(
    "height, underwater",
    [(0.0, False), (-35.0, True)],
    ids=["exactly at sea level is sea", "below the underwater level is no island"],
)
def test_ground_that_is_neither_land_nor_a_plateau_stays_clear(height, underwater):
    img = minimap.paint(np.full((32, 32), height, np.float32), 32, underwater=underwater)
    assert not img[..., 3].any()


def test_an_underwater_plateau_is_teal_with_a_border_and_a_shelf_is_not():
    h = _island()
    h[4:12, 4:12] = -27.0  # a plateau with deep sea all round
    h[10:16, 30:34] = -5.0  # a shelf touching the land's coast: stays sea
    img = minimap.paint(h, 64)
    assert tuple(img[8, 8, :3]) != tuple(minimap.UNDERWATER_BORDER) and img[8, 8, 3] == 255
    assert abs(int(img[8, 8, 0]) - minimap.UNDERWATER_COLOR[0]) < 20 and img[8, 8, 2] > img[8, 8, 0]
    assert tuple(img[4, 8, :3]) == minimap.UNDERWATER_BORDER and tuple(img[8, 4, :3]) == minimap.UNDERWATER_BORDER
    assert img[13, 32, 3] == 0  # the shelf
    off = minimap.paint(h, 64, underwater=False)
    assert off[8, 8, 3] == 0


def test_a_tiny_picture_does_not_fail():
    assert minimap.paint(np.full((8, 8), 3.0, np.float32), 4).shape == (4, 4, 4)
