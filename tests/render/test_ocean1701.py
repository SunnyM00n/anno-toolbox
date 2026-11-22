"""Anno 1701's sea (``render.ocean1701``; shader-compile.md 13 "The sea"): the engine's trapezoid, the grid and the
position pass's triangle, the constants by name, the ocean colour of a profile; the passes compile from the
installed effects (with ``ANNO1701_DIR``)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import gameshader as gs
from annotoolbox.render import ocean1701
from annotoolbox.render.frame1701 import SLight1701


def _camera(eye, target=(0.0, 0.0, 0.0), size=(320, 200)) -> gs.SCamera:
    return gs.direct_camera(size, eye, target, 0.8, 10.0, 20000.0)


def test_the_trapezoid_is_cut_from_the_widened_screen_as_the_engine_cuts_it():
    cam = _camera((0.0, 800.0, 1200.0))
    corners = ocean1701.trapezoid(cam.view @ cam.projection)
    assert corners is not None
    bl, br, tl, tr = (np.array(c) for c in corners)
    assert bl[0] < br[0] and tl[0] < tr[0]  # left of right, near and far
    assert tl[1] < bl[1] and tr[1] < br[1]  # the far edge lies farther from the eye (looking down -z)
    assert np.linalg.norm(tr - tl) > np.linalg.norm(br - bl)  # it widens away from the camera
    inv = np.linalg.inv(cam.view @ cam.projection)
    bounds = ocean1701.TRAPEZOID
    near = np.array([bounds.left, bounds.near_row, bounds.near_depth, 1.0]) @ inv
    far = np.array([bounds.left, bounds.near_row, 1.0, 1.0]) @ inv
    a, b = near[:3] / near[3], far[:3] / far[3]
    t = (bounds.near_height - a[1]) / (b[1] - a[1])
    assert np.allclose(bl, (a + (b - a) * t)[[0, 2]])  # the bottom left edge at 15 above the water


def test_a_camera_below_the_near_height_draws_no_sea_and_a_flat_one_cuts_the_far_plane():
    low = _camera((0.0, 10.0, 300.0), (0.0, 5.0, 0.0))
    assert ocean1701.trapezoid(low.view @ low.projection) is None
    flat = _camera((0.0, 300.0, 1200.0), (0.0, 250.0, 0.0))  # the top rays pass over the horizon
    corners = ocean1701.trapezoid(flat.view @ flat.projection)
    assert corners is not None and all(np.isfinite(corners).ravel())


def test_the_grid_reads_its_texels_at_their_middles_and_the_triangle_maps_them_as_direct3d_9_did():
    w, h = ocean1701.GRID
    mesh = ocean1701.grid_mesh()
    uv = np.frombuffer(mesh.m_data, "<f4").reshape(-1, 2)
    assert mesh.m_count == w * h == 44800 and len(mesh.m_indices) == (w - 1) * (h - 1) * 6
    assert np.allclose(uv[0], (0.5 / w, 0.5 / h)) and np.allclose(uv[w + 1], (1.5 / w, 1.5 / h))
    assert list(mesh.m_indices[:6]) == [1, 0, w, 1, w, w + 1]  # the engine's order
    tri = np.frombuffer(ocean1701.screen_triangle(ocean1701.GRID).m_data, "<f4").reshape(3, 5)
    # pixel i's middle lies at x = -1 + (2i + 1) / w: there the coordinate is i / w
    x = -1 + 1 / w
    u = tri[0, 3] + (x - tri[0, 0]) / (tri[1, 0] - tri[0, 0]) * (tri[1, 3] - tri[0, 3])
    assert u == pytest.approx(0.0, abs=1e-6)


def test_the_constants_by_name():
    cam = _camera((0.0, 800.0, 1200.0))
    corners = ocean1701.trapezoid(cam.view @ cam.projection)
    disp = ocean1701.disp_values(cam, corners, 10.0, (0.0, -1.0, 0.0))
    bl, br, tl, tr = (np.array(c) for c in corners)
    assert np.allclose(disp["pcUsrConstant0"], [*bl, *(br - bl)])
    assert np.allclose(disp["pcUsrConstant1"], [*(tl - bl), *(tr - br)])
    assert disp["vcUsrConstant0"][0] == 1.0  # looking straight down: no extra rows near the camera
    assert np.allclose(disp["pcUsrConstant2"], [0.3, 0.2, 0.1, 0.05])
    light = SLight1701((0.0, 1.0, 0.0), fog_start=6400.0, fog_end=15000.0)
    water = ocean1701.water_values(light, cam, (0.2, 0.4, 0.8, 0.0), 10.0)
    assert water["vcGlbFogSEDC"][:2] == [6400.0, 15000.0]  # world units, the camera's too
    assert water["pcUsrConstant1"] == [0.2, 0.4, 0.8, 0.0] and water["pcUsrConstant2"][:3] == [*light.fog]
    assert np.allclose(ocean1701.deep_color(light, (0.2, 0.4, 0.8, 0.0)), np.array([0.2, 0.4, 0.8]) * 0.85)


def test_a_profiles_ocean_colour():
    text = "<North><OceanWaterColor><r>0.2</r>\n<g>0.447059</g> <b>0.8</b>\n<a>0</a></OceanWaterColor></North>"
    assert ocean1701.ocean_color(text, "North") == (0.2, 0.447059, 0.8, 0.0)
    assert ocean1701.ocean_color("", "North") == (0.2, 0.447, 0.8, 0.0)


def test_the_installed_sea_effects_compile():
    root = os.environ.get("ANNO1701_DIR")
    if not root or not Path(root).is_dir():
        pytest.skip("ANNO1701_DIR is not set")
    from annotoolbox.infra import fxc
    from annotoolbox.models import shader1701
    from annotoolbox.sources import gametree

    try:
        compiler = fxc.CFxc.find(None, library="d3dcompiler_43")
    except Exception as e:  # no compiler on this machine
        pytest.skip(str(e))
    shaders = shader1701.CSource1701(gametree.open_game("anno1701", Path(root)), compiler)
    disp = shaders.effect_pass(*ocean1701.STAGES["disp"])
    water = shaders.effect_pass(*ocean1701.STAGES["water"])
    assert {b.name for b in water.vertex_stage.reflection.bindings} >= {"VertexSamplerTex0"}
    assert {b.name for b in water.pixel_stage.reflection.bindings} >= {
        "cSamplerReflec",
        "cSamplerRefrac",
        "cSamplerSkyRef",
    }
    assert {b.name for b in disp.pixel_stage.reflection.bindings} >= {"samplerTex0", "samplerTex2"}
    shadowed = shaders.effect_pass(*ocean1701.STAGES["shadowed"])
    assert ocean1701.SHADOW_MAP in {b.name for b in shadowed.pixel_stage.reflection.bindings}


def test_the_position_pass_writes_its_float_target_unclamped():
    from annotoolbox.models import shader1701

    assert "oTarget = c;" in shader1701.pixel_wrapper([], None, [], {}, "", clamp=False)
    assert "oTarget = saturate(c);" in shader1701.pixel_wrapper([], None, [], {}, "")
    assert ocean1701.DISP_FX.endswith(shader1701.FLOAT_TARGETS)


def test_the_suns_shadow_is_read_in_world_units_and_its_depth_reaches_the_sea():
    from annotoolbox.render import frame1701

    corners = np.array([[x, y, z] for x in (-1.0, 1.0) for y in (0.0, 3.0) for z in (-1.0, 1.0)])
    _cast, receive = frame1701.shadow_values((0.3, -1.0, 0.2), corners)
    sea = np.array([[x, 0.0, z] for x in (-500.0, 500.0) for z in (-500.0, 500.0)])
    _cast, far = frame1701.shadow_values((0.3, -1.0, 0.2), corners, sea)
    reach = [float(np.dot(far["vcShdConstants"], [*p, 1.0])) for p in sea]
    assert max(reach) <= 1.0 + 1e-6  # the sea past the casters lies inside the depth: lit, not shadowed
    assert far["vcGlbBias"][0] < receive["vcGlbBias"][0]  # the same distance over a longer depth
    values, maps = ocean1701.shadow_inputs((receive, {"samplerTexShadowMap": "map"}), 32.0)
    p = np.array([64.0, 32.0, -96.0, 1.0])  # world units: two tiles, one, three
    tiles = np.array([2.0, 1.0, -3.0, 1.0])
    assert np.allclose(p @ values["vcMtxShadowBias"], tiles @ receive["vcMtxShadowBias"])
    assert np.isclose(np.dot(values["vcShdConstants"], p), np.dot(receive["vcShdConstants"], tiles))
    assert maps == {ocean1701.SHADOW_MAP: "map"}
    assert dict(ocean1701.STAGES["shadowed"][1]) == {"VW_REFLECTION": "1", "VERTEXWATER_SHADOW": "1",
                                                      "VERTEXWATER_SOFTSHADOW": "1"}  # fmt: skip
