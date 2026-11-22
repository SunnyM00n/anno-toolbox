"""The free-fly camera: it answers what the renderer and the tools ask of the orbit camera, and the two switch."""

import math

import numpy as np
import pytest

from annotoolbox.editor.desktop import flycamera
from annotoolbox.editor.desktop.flycamera import CFlyCamera
from annotoolbox.render.camera import COrbitCamera, ground_hit


def orbit() -> COrbitCamera:
    cam = COrbitCamera((40, 3, 50), 120)
    cam.m_yaw_rad, cam.m_pitch_rad, cam.m_fov_rad, cam.m_speed = 0.7, 0.9, math.radians(50), 1.5
    return cam


def test_switching_keeps_the_eye_the_look_and_the_settings_in_both_directions():
    cam = orbit()
    fly = flycamera.switch(cam, "fly")
    assert isinstance(fly, CFlyCamera) and np.allclose(fly.eye(), cam.eye(), atol=1e-4)
    assert np.allclose(fly.m_target, cam.m_target, atol=1e-3) and fly.m_distance_m == pytest.approx(120)
    assert np.allclose(fly.matrix(1.5), cam.matrix(1.5), atol=1e-4)  # the picture does not jump
    assert fly.m_fov_rad == cam.m_fov_rad and fly.m_speed == 1.5
    assert flycamera.switch(fly, "fly") is fly
    back = flycamera.switch(fly, "orbit")
    assert type(back) is COrbitCamera and np.allclose(back.eye(), cam.eye(), atol=1e-3)
    assert flycamera.switch(cam, "orbit") is cam
    with pytest.raises(ValueError):
        flycamera.switch(cam, "hover")


def test_turning_turns_about_the_eye_and_the_pitch_may_look_up():
    fly = flycamera.switch(orbit(), "fly")
    eye = fly.eye().copy()
    fly.turn(60, 0)
    assert np.allclose(fly.eye(), eye) and fly.m_yaw_rad == pytest.approx(0.7 - 60 * 0.008)
    assert not np.allclose(fly.m_target, flycamera.switch(orbit(), "fly").m_target)  # what it looks at moved
    fly.turn(0, -10_000)
    assert fly.m_pitch_rad == pytest.approx(flycamera.PITCH[0]) and fly.forward()[1] > 0.9  # up
    fly.turn(0, 10_000)
    assert fly.m_pitch_rad == pytest.approx(flycamera.PITCH[1])


def test_the_wheel_flies_forward_and_back_and_the_keys_slide_and_lift():
    fly = flycamera.switch(orbit(), "fly")
    eye = fly.eye().copy()
    fly.zoom(1)
    gone = fly.eye() - eye
    assert np.dot(gone, fly.forward()) > 0 and np.allclose(gone / np.linalg.norm(gone), fly.forward(), atol=1e-5)
    fly.zoom(-1)
    assert np.allclose(fly.eye(), eye, atol=1e-4)
    fly.pan(40, 0)
    assert fly.eye()[1] == pytest.approx(eye[1])  # sliding keeps the height
    before = fly.eye().copy()
    fly.lift(1)
    assert fly.eye()[1] > before[1] and np.allclose(fly.eye()[[0, 2]], before[[0, 2]])
    fast, slow = (flycamera.switch(orbit(), "fly") for _ in range(2))
    fast.m_speed, slow.m_speed = 2.0, 1.0
    fast.zoom(1), slow.zoom(1)
    assert np.linalg.norm(fast.eye() - orbit().eye()) == pytest.approx(2 * np.linalg.norm(slow.eye() - orbit().eye()))


def test_the_pivot_and_the_distance_move_the_camera_as_the_views_focus_expects():
    fly = flycamera.switch(orbit(), "fly")
    fly.m_target = np.array([10.0, 0.0, 10.0])
    assert np.allclose(fly.m_target, [10, 0, 10], atol=1e-3)
    fly.m_distance_m = 30.0
    assert np.allclose(fly.m_target, [10, 0, 10], atol=1e-3) and np.linalg.norm(
        fly.eye() - fly.m_target
    ) == pytest.approx(30, abs=1e-3)
    fly.look(np.array([20.0, 1.0, 20.0]), 50.0, 15.0)
    assert np.allclose(fly.m_target, [20, 1, 20], atol=1e-3) and np.linalg.norm(
        fly.eye() - fly.m_target
    ) == pytest.approx(50, abs=1e-3)


def test_rays_and_the_ground_work_from_the_fly_camera():
    fly = flycamera.switch(orbit(), "fly")
    o, d = fly.ray(400, 300, 800, 600)  # the middle of the screen looks along the forward direction
    assert np.allclose(d, fly.forward(), atol=1e-3)
    assert np.linalg.norm(o - fly.eye()) < 1.0  # the ray starts at the near plane, in front of the eye
    heights = np.zeros((161, 161), "f4")
    hit = ground_hit(heights, 0.5, o, d)
    assert hit is not None and abs(hit[1]) < 1e-3
    assert fly.proj(1.5).shape == (4, 4) and np.isfinite(fly.matrix(1.5)).all()
