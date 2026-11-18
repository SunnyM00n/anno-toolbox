"""The older games' seas by their own water shaders in the island view (``editor.desktop.desktopsea1701``,
``desktopsea2070``, ``desktopsea1404``'s shadow values; island-editor-view "1701's sea by the game's shaders" and
"2070's sea by the game's shaders"): which islands and devices each draws on, 1701's profile and sea floor, 2070's
ambient area, the camera in the game's axes and units, the view's sun shadow."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop import desktopsea1701, desktopsea2070
from annotoolbox.othergames.isd1701 import TILE
from annotoolbox.render.camera import OrbitCamera

TREE = object()  # an install stands in


def _renderer(game_island: dict, backend: str = "D3D11", tree=TREE, flip: bool = False, size: int = 64):
    rhi = SimpleNamespace(backendName=lambda: backend)
    ctx = SimpleNamespace(rhi=rhi, native_pass=lambda fn: None, flip_offscreen=flip)
    session = SimpleNamespace(island=game_island, size=size)
    return SimpleNamespace(ctx=ctx, session=session, editor=SimpleNamespace(tree2070=tree))


def _opts(**kw):
    return SimpleNamespace(layers=kw.get("layers", {"water": True}), underwater=kw.get("underwater", False))


def test_1701s_sea_draws_on_1701_islands_and_the_2070_sea_does_not():
    r = _renderer({"game": "anno1701"})
    assert desktopsea1701.GameSea1701(r).wanted(_opts()) and not desktopsea2070.GameSea2070(r).wanted(_opts())
    for game in ("anno2070", "anno1404", "anno117"):
        assert not desktopsea1701.GameSea1701(_renderer({"game": game})).wanted(_opts())
    assert not desktopsea1701.GameSea1701(_renderer({"game": "anno1701"}, "OpenGL")).wanted(_opts())


def test_2070s_sea_draws_on_2070_islands_on_direct3d_with_the_water_on():
    sea = desktopsea2070.GameSea2070(_renderer({"game": "anno2070"}, "D3D12"))
    assert sea.wanted(_opts())
    assert desktopsea2070.GameSea2070(_renderer({"mode": "2070"})).wanted(_opts())  # an island edited for 2070
    for r in (_renderer({"game": "anno1404"}), _renderer({}), _renderer({"game": "anno2070"}, "OpenGL"),
              _renderer({"game": "anno2070"}, tree=None), _renderer({"game": "anno2070", "sea": False})):  # fmt: skip
        assert not desktopsea2070.GameSea2070(r).wanted(_opts())
    assert not sea.wanted(_opts(layers={"water": False})) and not sea.wanted(_opts(underwater=True))
    sea.error = True  # it failed once: the view's own sea from then on
    assert not sea.wanted(_opts())


def test_1701s_profile_and_the_sea_floor_round_the_island():
    assert desktopsea1701.profile_of("data/levels/islands/south/s_g01.isd") == "South"
    assert desktopsea1701.profile_of(r"data\levels\islands\south\s_g01.isd") == "South"  # Windows separators
    heights = np.full((5, 5), -100.0 / TILE)
    heights[1:4, 1:4] = 2.0
    assert desktopsea1701.seabed(heights) == pytest.approx(-100.0 / TILE)


def test_a_2070_island_lies_in_the_ambient_area_of_its_kind():
    assert desktopsea2070.area_of("data/levels/islands/normal/n_l03.isd") == "NorthIsland"
    assert desktopsea2070.area_of("data\\levels\\islands\\underwater\\u_m14_rift.isd") == "Underwater_Island_North"
    assert desktopsea2070.area_of("") == "NorthIsland"  # an island edited for 2070 from another game


@pytest.mark.parametrize(
    "sea, game, size, unit, flip",
    [
        (desktopsea1701.GameSea1701, "anno1701", 64, TILE, False),  # 1701's units: a tile is ``TILE`` of them
        (desktopsea2070.GameSea2070, "anno2070", 100, 1.0, False),
        (desktopsea2070.GameSea2070, "anno2070", 100, 1.0, True),  # upside down, as Direct3D's offscreen pictures
    ],
)
def test_a_seas_camera_is_the_views_in_the_games_axes_and_units(sea, game, size, unit, flip):
    """Every point lands where the view's own camera puts it (the island's x mirrored about its middle), its y turned
    over where the view's own passes turn theirs into an offscreen target."""
    camera = OrbitCamera((40.0, 0.0, 30.0), 80.0)
    camera.yaw, camera.pitch = math.radians(30), math.radians(35)
    picture = (320, 200)
    cam = sea(_renderer({"game": game}, flip=flip, size=size))._camera(camera, picture)
    view_proj = camera.proj(picture[0] / picture[1]) @ camera.view()
    for p in ((40.0, 0.0, 30.0), (10.0, 5.0, 60.0), (30.0, -3.0, 20.0)):
        ours = view_proj @ np.array([*p, 1.0])
        game_point = np.array([(size - p[0]) * unit, p[1] * unit, p[2] * unit, 1.0]) @ cam.view @ cam.proj
        want = ours[:2] / ours[3] * (1.0, -1.0 if flip else 1.0)
        assert np.allclose(game_point[:2] / game_point[3], want, atol=1e-4)


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
