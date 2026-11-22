"""The config decals by 117's decal shader: the grid of a shape, the engine's instance rows, the grouping, the state."""

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import gamedecals, renderstates


def test_a_decal_shape_is_a_flat_grid_of_half_unit_cells_over_its_coordinate_rectangle():
    mesh = gamedecals.decal_grid((2.0, 1.0), (0.0, 0.5, 1.0, 1.0))
    v = np.frombuffer(mesh.m_data, gamedecals.VERTEX)
    assert mesh.m_count == len(v) == 9 * 5  # 8 x 4 cells of 0.5 over 4 x 2 units
    assert v["pos"][:, 0].min() == -2.0 and v["pos"][:, 2].max() == 1.0 and not v["pos"][:, 1].any()
    assert v["uv"][:, 1].min() == 0.5 and v["uv"][:, 1].max() == 1.0  # TexCoords.y .. TexCoords.w
    assert len(mesh.m_indices) == 8 * 4 * 6


def test_a_decals_rows_hold_its_colour_half_sizes_and_place_as_the_vertex_shader_unpacks_them():
    world = np.identity(4)
    world[:3, 3] = (10.0, 2.0, 30.0)
    rows = np.frombuffer(gamedecals.decal_rows(world[None], (2.0, 1.5)), np.uint32).reshape(-1, gamedecals.ROWS, 4)
    assert rows[0, 0, 0] == gamedecals.NEUTRAL  # ARGB: white (rgb x2 in the shader), opaque
    assert rows[0, 0, 1] & 0xFFFF == 512 and rows[0, 0, 1] >> 16 == 384  # 8.8 fixed point
    floats = rows.view(np.float32)
    assert floats[0, 0, 2] == gamedecals.NO_MIN_HEIGHT and rows[0, 0, 3] == gamedecals.FULL_FADE
    assert np.allclose(floats[0, 1:4], world[:3])  # the top three rows: transposed back by the shader
    assert not rows[0, 4].any()


def test_decals_are_grouped_by_material_and_shape_and_only_those_of_the_decal_shader():
    mats = np.stack([np.identity(4), np.diag([0.0, 0.0, 0.0, 1.0])])  # the second instance is hidden
    one = {"values": {"ShaderID": "1", "cOpacity": "1"}, "extents": (2, 2), "kind": "Terrain",
           "matrix": np.identity(4)}  # fmt: skip
    other = {**one, "values": {"ShaderID": "6"}}  # a model decal: not this shader's
    track = {**one, "kind": "Vehicle"}
    looks = gamedecals.decal_looks([(mats, [one, other, track])])
    assert list(looks) == [(("ShaderID", "1"), ("cOpacity", "1"))]
    ((shape, worlds),) = looks[(("ShaderID", "1"), ("cOpacity", "1"))].items()
    assert shape == ((2.0, 2.0), (0.0, 0.0, 1.0, 1.0)) and len(worlds) == 1


def test_the_decal_pass_keeps_the_g_buffers_alpha_and_velocity_by_its_write_masks():
    states = {"zwriteenable": "FALSE", "colorwriteenable[0]": "0x7", "colorwriteenable[3]": "0x0",
              "alphablendenable[0]": "TRUE", "srcblend[0]": "SRC_ALPHA", "dstblend[0]": "INV_SRC_ALPHA"}  # fmt: skip
    state = gamedecals.decal_state(states)
    assert state.writes == (7, None, None, 0) and not state.depth_write and state.blends[0] is not None
    assert renderstates.pass_state({}).writes == ()  # a block without masks writes everything
