"""A 1701 island's sea by the game's own water shaders in the island view (``editor.desktop.desktopsea1701``;
island-editor-view "1701's sea by the game's shaders"): which islands it draws on, its profile, the sea floor round
the island, its camera in the game's units."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop import desktopsea1701, desktopsea2070
from annotoolbox.othergames.isd1701 import TILE
from annotoolbox.render.camera import OrbitCamera


def _renderer(game_island: dict, backend: str = "D3D11", flip: bool = False):
    rhi = SimpleNamespace(backendName=lambda: backend)
    ctx = SimpleNamespace(rhi=rhi, native_pass=lambda fn: None, flip_offscreen=flip)
    session = SimpleNamespace(island=game_island, size=64)
    return SimpleNamespace(ctx=ctx, session=session, editor=SimpleNamespace(tree2070=object()))


def _opts():
    return SimpleNamespace(layers={"water": True}, underwater=False)


def test_it_draws_on_1701_islands_and_the_2070_sea_does_not():
    r = _renderer({"game": "anno1701"})
    assert desktopsea1701.GameSea1701(r).wanted(_opts()) and not desktopsea2070.GameSea2070(r).wanted(_opts())
    for game in ("anno2070", "anno1404", "anno117"):
        assert not desktopsea1701.GameSea1701(_renderer({"game": game})).wanted(_opts())
    assert not desktopsea1701.GameSea1701(_renderer({"game": "anno1701"}, "OpenGL")).wanted(_opts())


def test_the_profile_and_the_sea_floor_round_the_island():
    assert desktopsea1701.profile_of("data/levels/islands/south/s_g01.isd") == "South"
    assert desktopsea1701.profile_of(r"data\levels\islands\south\s_g01.isd") == "South"  # Windows separators
    heights = np.full((5, 5), -100.0 / TILE)
    heights[1:4, 1:4] = 2.0
    assert desktopsea1701.seabed(heights) == pytest.approx(-100.0 / TILE)


def test_its_camera_is_the_views_in_the_games_axes_and_units():
    camera = OrbitCamera((30.0, 0.0, 20.0), 40.0)
    camera.yaw, camera.pitch = math.radians(20), math.radians(40)
    size = (320, 200)
    cam = desktopsea1701.GameSea1701(_renderer({"game": "anno1701"}))._camera(camera, size)
    view_proj = camera.proj(size[0] / size[1]) @ camera.view()
    for p in ((30.0, 0.0, 20.0), (10.0, 2.0, 50.0)):
        ours = view_proj @ np.array([*p, 1.0])
        game = np.array([(64.0 - p[0]) * TILE, p[1] * TILE, p[2] * TILE, 1.0]) @ cam.view @ cam.proj
        assert np.allclose(game[:2] / game[3], ours[:2] / ours[3], atol=1e-4)


def test_the_views_sun_shadow_is_read_in_the_games_axes_as_the_view_reads_it():
    from annotoolbox.editor.desktop import desktopsea1404
    from annotoolbox.render import ocean1701
    from annotoolbox.render.camera import look_at, ortho

    matrix = ortho(40.0, 1.0, 160.0) @ look_at(np.array([30.0, 60.0, 50.0]), np.array([20.0, 0.0, 30.0]))
    tex = SimpleNamespace(native="map")
    sea = desktopsea1701.GameSea1701(_renderer({"game": "anno1701"}))
    shadow = sea._view_shadow(SimpleNamespace(shadow=(tex, matrix)))
    for p in ((20.0, 0.0, 30.0), (12.0, 1.5, 41.0)):
        s = matrix @ np.array([*p, 1.0])
        want = s[:3] / s[3] * 0.5 + 0.5  # the view's own lookup (render/shaders.py shadowAt)
        game = np.array([(64.0 - p[0]) * TILE, p[1] * TILE, p[2] * TILE, 1.0])
        uv = game @ shadow.texture
        assert np.allclose(uv[:2] / uv[3], want[:2]) and np.isclose(game @ shadow.depth, want[2])
    assert sea._view_shadow(SimpleNamespace(shadow=None)) is None
    values, maps = desktopsea1701.shadow_values(shadow)
    assert set(values) == {"vcMtxShadowBias", "vcShdConstants"} and maps == {ocean1701.SHADOW_MAP: "map"}
    values, maps = desktopsea1404.shadow_values(shadow)
    assert values["cMtxShadowVPP0"] is shadow.texture and maps == {"cShadowMap": "map"}
    assert values["cShadowBias"] == [0.0] * 4  # the sea's comparison has no bias (vsShadow2ObjNoBias)
