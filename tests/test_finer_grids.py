"""117's finer grids are derived from 2070's one-value-per-tile data, never copied or stretched nearest-neighbour: heights
are interpolated smoothly, material weights blend across the tile edges (island-conversion "Finer grids")."""

from __future__ import annotations

import numpy as np

from annotoolbox.islands import islandground, islandterrain


def test_heights_on_a_slope_are_interpolated_without_tile_steps():
    grid = np.add.outer(np.zeros(16), np.arange(16) * 0.5) ** 1.0  # a ramp of 0.5 per tile along x
    xs = np.arange(4, 10, 1 / 8)  # eight samples a tile, as the pages are
    row = islandterrain.smooth_sample(grid, xs, np.array([8.0]))[0]
    steps = np.diff(row)
    assert np.allclose(steps, 0.5 / 8, atol=2e-4)  # an even step per sample: no plateau per tile


def test_a_hard_material_edge_becomes_a_ramp_wider_than_a_tile():
    texels = 8
    weights = np.zeros((2, 8, 8 * texels), np.float32)
    weights[0, :, : 4 * texels] = 1  # 2070: whole tiles of one material
    weights[1] = 1 - weights[0]
    islandground.blend_materials(weights, texels)
    row = weights[0, 4]
    steps = np.abs(np.diff(row))
    assert steps.max() < 0.5 / 2  # no single texel step: the edge is spread over several texels
    assert (row[: 2 * texels] > 0.97).all() and np.isclose(weights.sum(axis=0), 1).all()
    assert ((row > 0.05) & (row < 0.95)).sum() >= texels  # the blend reaches about a tile across


def test_a_grid_filtered_once_samples_to_the_bytes_of_a_filter_per_call():
    from scipy import ndimage

    rng = np.random.default_rng(7)
    for n, dtype in ((5, np.float64), (40, np.float64), (33, np.float32)):
        grid = rng.normal(0, 40, (n, n)).cumsum(axis=0).astype(dtype)
        filtered = islandterrain.cubic_filter(grid)
        xs, zs = np.clip(rng.uniform(-1, n, 23), 0, n - 1), np.clip(rng.uniform(-1, n, 17), 0, n - 1)
        zz, xx = np.meshgrid(zs, xs, indexing="ij")
        once = islandterrain._cubic(grid, zz, xx, filtered)
        assert once.tobytes() == ndimage.map_coordinates(grid, [zz, xx], order=3, mode="nearest").tobytes()
        assert islandterrain.smooth_sample(grid, xs, zs, filtered).tobytes() == (
            islandterrain.smooth_sample(grid, xs, zs).tobytes()
        )


def test_the_strongest_slots_worked_out_in_bands_are_those_of_the_whole_map():
    rng = np.random.default_rng(5)
    for m, n in ((1, 7), (9, 40), (31, 23)):
        bw = rng.choice(np.array([0, 0, 0.5, 0.5, 1, 2], np.float32), (m, n, n))  # ties, as flat ground has
        want = np.argsort(-bw, axis=0)[:6]
        for rows in (1, 3, n):
            got = islandground.top_slots(bw, rows)
            assert got.dtype == want.dtype and np.array_equal(got, want)
