"""Anno 2070's coast waves (``render.coast2070``; shader-compile.md 10, "The coast waves") and the island view's
planar reflection camera (``render.camera.oblique_near``): where the engine lays the waves' area for a camera, how
their strength drops and comes back, and the mirrored camera clipped at the water."""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.models import fxstage
from annotoolbox.render import coast2070
from annotoolbox.render import gameshader as gs
from annotoolbox.render.camera import MIRROR_Y, look_at, oblique_near, perspective


def _camera(eye, target):
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    cam = gs.SCamera(eye, gs.look_at(eye, target), gs.perspective(0.6, 1.6, 1.0, 400.0), 1.0, 400.0, (160, 100))
    return cam.eye, -cam.view[:3, 2], cam.view @ cam.projection


def test_a_camera_looking_down_centres_the_area_on_its_footprint_on_the_texel_grid():
    eye, ahead, view_proj = _camera((100.0, 80.0, 100.0), (100.0, 0.0, 130.0))
    x, z = coast2070.area(eye, ahead, view_proj)
    assert x % coast2070.TEXEL == 0 and z % coast2070.TEXEL == 0
    assert abs(x + 64 - 100.0) < 1.0  # symmetric about the eye's x
    assert 100.0 < z + 64 < 160.0  # ahead, over the footprint seen between 5 below and 5 above the water


def test_a_low_or_flat_camera_lays_the_area_ahead_of_the_eye():
    eye, ahead, view_proj = _camera((10.0, 4.0, 0.0), (10.0, 0.0, 50.0))
    assert coast2070.area(eye, ahead, view_proj) == (
        coast2070.snap(10.0 + ahead[0] * 128 * 0.45 - 64),
        coast2070.snap(ahead[2] * 128 * 0.45 - 64),
    )


def test_the_waves_weaken_while_the_camera_moves_and_come_back():
    assert coast2070.recovery(1.0, 0.0, 16.0) == 1.0
    moving = coast2070.recovery(1.0, 3.0, 16.0)  # far in one step: down to the floor, then a little back
    assert 0.1 < moving < 0.2
    assert coast2070.recovery(moving, 0.0, 16.0) > moving


def test_a_blend_blocks_write_masks_are_read_per_target():
    assert fxstage.write_masks({"rendertargetwritemask[0]": "13"}) == (13,)
    assert fxstage.write_masks({"rendertargetwritemask[1]": "0x1"}) == (None, 1)
    assert fxstage.write_masks({"blendenable[0]": "TRUE"}) == ()


def test_the_mirrored_camera_keeps_what_lies_above_the_water_and_clips_below():
    eye, target = np.array([0.0, 20.0, 40.0]), np.array([0.0, 0.0, 0.0])
    view = look_at(eye, target).astype(float) @ MIRROR_Y
    proj = oblique_near(perspective(math.radians(50), 1.5, 1.0, 500.0), np.linalg.inv(view).T @ [0, 1, 0, 0])

    def depth(p):
        c = proj @ view @ np.array([*p, 1.0])
        return c[2] / c[3]

    assert -1.0 <= depth((0.0, 3.0, 0.0)) <= 1.0  # a mast above the water, seen in the mirror
    assert depth((0.0, -3.0, 0.0)) < -1.0  # the sea bed: in front of the moved near plane, clipped
    assert depth((0.0, 0.0, 0.0)) == pytest.approx(-1.0, abs=1e-6)  # the water itself lies on it
