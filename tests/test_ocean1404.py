"""Anno 1404's sea (``render.ocean1404``; shader-compile.md 12 "The sea"): the water depth map as the engine builds
it, a set's waves, the grid and the screen triangle, the trapezoid with 1404's numbers, the constants by name; the
island view's sea on 1404 islands only; the passes compile from the installed effects (with ``ANNO1404_DIR``)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import gameshader as gs
from annotoolbox.render import ocean1404, ocean1701
from annotoolbox.render.frame1404 import Sun1404, read_sun


def _camera(eye, target=(0.0, 0.0, 0.0), size=(320, 200)) -> gs.Camera:
    return gs.direct_camera(size, eye, target, 0.8, 1.0, 2000.0)


def test_the_depth_maps_channels_follow_the_engines_builder():
    heights = np.full((41, 41), -10.0)
    heights[15:26, 15:26] = 3.0  # an island of 10 units, land well above the mask's height
    m = ocean1404.depth_map(heights, 1.0, -3.5)
    assert m.shape == (40, 40, 4)
    deep, land = m[0, 0], m[20, 20]
    assert list(deep[1:3]) == [255, 255] and deep[0] == 255  # deep water everywhere around: no calming
    assert land[3] == 255 and land[2] == 1 and land[1] == 128  # land: masked, depth 1/255, the medium waves half
    assert m[20, 15, 3] == 0  # the coast: its neighbours two texels off lie in the water
    h = -1.0  # (h + 0.5) / -3.5 and twice it, truncated to bytes
    flat = ocean1404.depth_map(np.full((9, 9), h), 1.0, -3.5)
    assert flat[4, 4, 2] == int(np.float32(0.5 * 2 / 3.5) * 255) and flat[4, 4, 1] == int((0.5 / 3.5 + 0.5) * 255)
    # the red channel is the 9 x 9 minimum, then the 11 x 11 mean: deep water near land is calmed
    assert m[20, 10, 0] < m[20, 0, 0] and m[20, 2, 0] == 255


def test_the_blurs_rings_leave_the_texel_out_and_count_only_the_map():
    src = np.full((12, 12), 255, np.uint8)
    src[6, 6] = 0
    low = ocean1404.ring_min(src, 4)
    assert low[6, 6] == 255 and low[6, 10] == 0 and low[6, 11] == 255  # itself out, 4 texels reach
    assert ocean1404.ring_mean(np.full((3, 3), 51, np.uint8), 5)[0, 0] == 51  # the corner averages its 8 on the map
    assert sorted(ocean1404._ring(1)) == sorted((x, z) for x in (-1, 0, 1) for z in (-1, 0, 1) if (x, z) != (0, 0))
    assert ocean1404._ring(2)[0] == (-2, 2) and len(set(ocean1404._ring(3))) == 24


def test_a_sets_waves_and_the_managers_own():
    text = ("<North><WaterDescription><WaveLength><x>0.01</x><y>0.112</y></WaveLength><WaveHeight><x>1.7</x>"
            "<y>0.25</y></WaveHeight><Choppiness>2.4</Choppiness><MaxWaterDepth>-3.5</MaxWaterDepth>"
            "</WaterDescription></North>")  # fmt: skip
    w = ocean1404.read_waves(text, "North")
    assert (w.length, w.height, w.choppiness, w.depth) == ((0.01, 0.112), (1.7, 0.25), 2.4, -3.5)
    assert w.speed0 == ocean1404.Waves1404().speed0  # a value the set lacks keeps the manager's
    sun = read_sun("<North><OceanWaterColor><r>0.2</r><g>0.4</g><b>0.6</b><a>1</a></OceanWaterColor>"
                   "<OceanWaterColorPower>0.5</OceanWaterColorPower></North>")  # fmt: skip
    assert sun.ocean == pytest.approx((0.1, 0.2, 0.3))


def test_the_grid_and_the_screen_triangle():
    w, h = ocean1404.GRID
    mesh = ocean1404.grid_mesh()
    uv = np.frombuffer(mesh.data, "<f4").reshape(-1, 2)
    assert (
        mesh.count == w * h == 64400 and np.allclose(uv[-1], (1.0, 1.0)) and np.allclose(uv[w + 1], (1 / 229, 1 / 279))
    )
    assert list(mesh.indices[:6]) == [0, w, 1, 1, w, w + 1]  # the engine's order
    tri = np.frombuffer(ocean1404.screen_triangle().data, "<f4").reshape(3, 5)
    assert list(tri[0, 3:]) == [0.0, 1.0] and list(tri[2, 3:]) == [0.0, -1.0]  # v from the top down


def test_the_trapezoid_and_the_constants_by_name():
    cam = _camera((0.0, 60.0, 90.0))
    corners = ocean1701.trapezoid(cam.view @ cam.proj, ocean1404.TRAPEZOID)
    assert corners is not None
    assert ocean1701.trapezoid(cam.view @ cam.proj) != corners  # 1404's numbers, not 1701's
    low = _camera((0.0, 0.05, 30.0), (0.0, 0.0, 0.0))
    assert ocean1701.trapezoid(low.view @ low.proj, ocean1404.TRAPEZOID) is None  # under the near edge's 0.1
    waves = ocean1404.Waves1404()
    g = ocean1404.grid_values(cam, corners, waves, 100.0, 512)
    bl, br, tl, tr = (np.array(c) for c in corners)
    assert np.allclose(g["cProjGridTrapeze0"], [*bl, *(br - bl)])
    assert np.allclose(g["cProjGridTrapeze1"], [*(tl - bl), *(tr - br)])
    assert g["cReciprocalTerrainSize"][0] == 1 / 512 and g["cWaveChoppiness"][:2] == [2.1, 1.0]
    assert g["cWaveSpeed0"][0] == pytest.approx((100 * 1e-4 / 0.01) % 1.0)
    near, far = g["cOversampling"][1:3]
    assert 0.2 <= far <= near <= 1.0  # the medium waves fade with distance
    sun = Sun1404((0.0, 1.0, 0.0), seconds=10.0)
    v = ocean1404.water_values(sun, cam, waves)
    assert v["cWaterDepth"] == [-3.5, 3.5, -1 / 3.5, 1 / 3.5] and v["cReciprocalGridSize"] == [
        1.5 / 230,
        0,
        0,
        1.5 / 280,
    ]
    assert np.allclose(v["cWaterColor"], np.array(sun.ocean) * np.array(sun.diffuse))  # the sun overhead: all sun
    assert v["cWaterColorUnlighted"] == list(sun.ocean) and v["cWaterLightDirection"] == [0.0, 1.0, 0.0]


def test_the_island_sea_draws_on_1404_islands_only():
    from annotoolbox.editor.desktop import desktopsea1404, desktopsea1701, desktopsea2070

    def renderer(game: str, backend: str = "D3D12"):
        ctx = SimpleNamespace(rhi=SimpleNamespace(backendName=lambda: backend), native_pass=lambda fn: None)
        session = SimpleNamespace(island={"game": game}, size=64)
        return SimpleNamespace(ctx=ctx, session=session, editor=SimpleNamespace(tree2070=object()))

    opts = SimpleNamespace(layers={"water": True}, underwater=False)
    r = renderer("anno1404")
    assert desktopsea1404.GameSea1404(r).wanted(opts)
    assert not desktopsea2070.GameSea2070(r).wanted(opts) and not desktopsea1701.GameSea1701(r).wanted(opts)
    assert not desktopsea1404.GameSea1404(renderer("anno2070")).wanted(opts)
    assert not desktopsea1404.GameSea1404(renderer("anno1404", "OpenGL")).wanted(opts)
    assert desktopsea1404.setting_of(r"addondata\levels\islands\south\s_l01.isd") == "South"
    assert desktopsea1404.setting_of("data/levels/islands/north/n_l03.isd") == "North"


def test_the_installed_sea_effects_compile():
    root = os.environ.get("ANNO1404_DIR")
    if not root or not Path(root).is_dir():
        pytest.skip("ANNO1404_DIR is not set")
    from annotoolbox.infra import fxc
    from annotoolbox.models import shader2070
    from annotoolbox.sources import gametree

    try:
        compiler = fxc.Fxc.find(None, library="d3dcompiler_43")
    except Exception as e:  # no compiler on this machine
        pytest.skip(str(e))
    tree = gametree.open_game("anno1404", Path(root))
    shaders = shader2070.Source2070(tree, compiler, shader2070.GLOBALS_1404, missing_empty=True)
    grid = shaders.effect_pass(*ocean1404.STAGES["grid"])
    water = shaders.effect_pass(*ocean1404.STAGES["water"])
    assert {b.name for b in grid.ps.reflection.bindings} >= {"cTexture0", "cTexture1"}
    assert {b.name for b in water.vs.reflection.bindings} >= {"cTexture4", "cTexture5"}
    assert {b.name for b in water.ps.reflection.bindings} >= {"cTexture0", "cTexture2", "cTexture6", "cTexture7"}
    shadowed = shaders.effect_pass(*ocean1404.STAGES["shadowed"])  # the sun's shadow on the sea
    assert "cShadowMap" in {b.name for b in shadowed.ps.reflection.bindings}
    with pytest.raises(RuntimeError):  # noise.fxh is shipped by no install: only taken as empty when asked
        shader2070.Source2070(tree, compiler, shader2070.GLOBALS_1404).effect_pass(*ocean1404.STAGES["grid"])
