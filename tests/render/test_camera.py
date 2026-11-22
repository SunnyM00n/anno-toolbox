"""The 3D view's camera (``render.camera``): the ray against a height grid that is not square."""

from __future__ import annotations

import numpy as np

from annotoolbox.render.camera import ground_hit


def test_a_ray_finds_the_ground_of_a_grid_wider_than_it_is_deep():
    heights = np.zeros((5, 11), "f4")  # five rows (z), eleven columns (x, mirrored: view x = 10 - column)
    heights[:, 8:] = 5.0
    hit = ground_hit(heights, 1.0, [1.0, 20.0, 2.0], [0.0, -1.0, 0.0])  # view x 1 is column 9: on the plateau
    assert hit is not None and abs(hit[1] - 5.0) < 1e-3
    flat = ground_hit(heights, 1.0, [9.0, 20.0, 2.0], [0.0, -1.0, 0.0])  # view x 9 is column 1: flat ground
    assert flat is not None and abs(flat[1]) < 1e-3
