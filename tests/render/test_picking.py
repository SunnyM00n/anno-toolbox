"""The picking maths shared by the 3D views (``render.picking``): a ray against a plane and an axis, a point on the
screen, the nearest point of a segment; the camera's ray is the same one."""

from __future__ import annotations

import math

import numpy as np
import pytest

from annotoolbox.render import picking
from annotoolbox.render.camera import COrbitCamera


def ray(origin, direction):
    return np.asarray(origin, float), np.asarray(direction, float)


def test_a_ray_meets_a_plane_ahead_but_not_a_parallel_one_or_one_behind():
    down = ray((0, 5, 0), (0, -1, 0))
    assert np.allclose(picking.ray_plane(down, (3, 0, 3), (0, 1, 0)), (0, 0, 0))
    assert picking.ray_plane(ray((0, 5, 0), (1, 0, 0)), (0, 0, 0), (0, 1, 0)) is None  # parallel
    assert picking.ray_plane(ray((0, -5, 0), (0, -1, 0)), (0, 0, 0), (0, 1, 0)) is None  # behind
    assert np.allclose(picking.ray_plane(ray((0, 0, 0), (0, -1, 0)), (0, 0, 0), (0, 1, 0)), (0, 0, 0))  # at the origin


def test_the_point_nearest_a_ray_along_an_axis_and_none_for_a_parallel_axis():
    # the x axis through (0, 0, 0); a ray down at x = 2 comes closest at 2
    assert picking.along_axis(ray((2, 5, 1), (0, -1, 0)), (0, 0, 0), (1, 0, 0)) == pytest.approx(2.0)
    assert picking.along_axis(ray((2, 5, 1), (1, 0, 0)), (0, 0, 0), (1, 0, 0)) is None
    # an axis not through the origin: measured from its point
    assert picking.along_axis(ray((2, 5, 1), (0, -1, 0)), (1, 0, 0), (1, 0, 0)) == pytest.approx(1.0)


def test_points_go_to_the_screen_and_those_behind_the_camera_are_none():
    camera = COrbitCamera((0, 0, 0), 20.0)
    view_proj = camera.matrix(1.0)
    here, behind = picking.to_screen(view_proj, (200, 100), [camera.m_target, 2 * camera.eye() - camera.m_target])
    assert here[0] == pytest.approx(100, abs=0.5) and here[1] == pytest.approx(50, abs=0.5) and behind is None
    ray_through = picking.ray(view_proj, (200, 100), here[0], here[1])
    assert np.allclose(ray_through[0] + ray_through[1] * np.dot(camera.m_target - ray_through[0], ray_through[1]),
                       camera.m_target, atol=1e-3)  # fmt: skip


def test_the_nearest_point_of_a_segment_is_clamped_to_its_ends():
    t, point, distance = picking.on_segment((5, 3), (0, 0), (10, 0))
    assert (t, point, distance) == (0.5, (5.0, 0.0), 3.0)
    assert (
        picking.on_segment((-4, 3), (0, 0), (10, 0))[0] == 0.0
        and picking.on_segment((14, 3), (0, 0), (10, 0))[2] == 5.0
    )
    assert picking.on_segment((3, 4), (0, 0), (0, 0)) == (0.0, (0.0, 0.0), 5.0)  # a point, not a segment
    assert math.isclose(picking.on_segment((1, 1), (0, 0), (1, 0))[2], 1.0)


def test_the_cameras_ray_is_the_pickers_ray():
    camera = COrbitCamera((3, 1, 2), 20.0)
    origin, direction = camera.ray(30, 40, 200, 100)
    other = picking.ray(camera.matrix(2.0), (200, 100), 30, 40)
    assert np.allclose(origin, other[0], atol=1e-3) and np.allclose(direction, other[1], atol=1e-5)
    assert float(np.linalg.norm(direction)) == pytest.approx(1.0, abs=1e-5)
