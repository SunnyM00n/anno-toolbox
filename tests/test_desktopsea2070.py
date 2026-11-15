"""A 2070 island's sea by the game's own water shaders in the island view (``editor.desktop.desktopsea2070``;
island-editor-view "2070's sea by the game's shaders"): which islands and devices it draws on, and its camera in the
game's axes, upside down as the view's offscreen pictures stand on Direct3D."""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop import desktopsea2070
from annotoolbox.render.camera import OrbitCamera

TREE = object()  # an install stands in


def _renderer(game_island: dict, backend: str = "D3D12", tree=TREE, flip: bool = True):
    rhi = SimpleNamespace(backendName=lambda: backend)
    ctx = SimpleNamespace(rhi=rhi, native_pass=lambda fn: None, flip_offscreen=flip)
    session = SimpleNamespace(island=game_island, size=100)
    return SimpleNamespace(ctx=ctx, session=session, editor=SimpleNamespace(tree2070=tree))


def _opts(**kw):
    return SimpleNamespace(layers=kw.get("layers", {"water": True}), underwater=kw.get("underwater", False))


def test_it_draws_on_2070_islands_on_direct3d_with_the_water_on():
    sea = desktopsea2070.GameSea2070(_renderer({"game": "anno2070"}))
    assert sea.wanted(_opts())
    assert desktopsea2070.GameSea2070(_renderer({"mode": "2070"})).wanted(_opts())  # an island edited for 2070
    for r in (_renderer({"game": "anno1404"}), _renderer({}), _renderer({"game": "anno2070"}, "OpenGL"),
              _renderer({"game": "anno2070"}, tree=None), _renderer({"game": "anno2070", "sea": False})):  # fmt: skip
        assert not desktopsea2070.GameSea2070(r).wanted(_opts())
    assert not sea.wanted(_opts(layers={"water": False})) and not sea.wanted(_opts(underwater=True))
    sea.error = True  # it failed once: the view's own sea from then on
    assert not sea.wanted(_opts())


def test_an_island_lies_in_the_ambient_area_of_its_kind():
    assert desktopsea2070.area_of("data/levels/islands/normal/n_l03.isd") == "NorthIsland"
    assert desktopsea2070.area_of("data\\levels\\islands\\underwater\\u_m14_rift.isd") == "Underwater_Island_North"
    assert desktopsea2070.area_of("") == "NorthIsland"  # an island edited for 2070 from another game


def test_its_camera_is_the_views_in_the_games_axes_and_upside_down_on_direct3d():
    """Every point lands where the view's own camera puts it (the island's x mirrored about its middle), its y turned
    over as the view's own passes turn theirs into an offscreen target."""
    camera = OrbitCamera((40.0, 0.0, 30.0), 80.0)
    camera.yaw, camera.pitch = math.radians(30), math.radians(35)
    size = (320, 200)
    for flip in (False, True):
        cam = desktopsea2070.GameSea2070(_renderer({"game": "anno2070"}, flip=flip))._camera(camera, size)
        view_proj = camera.proj(size[0] / size[1]) @ camera.view()
        for p in ((40.0, 0.0, 30.0), (10.0, 5.0, 60.0), (70.0, -3.0, 20.0)):
            ours = view_proj @ np.array([*p, 1.0])
            game = np.array([100.0 - p[0], p[1], p[2], 1.0]) @ cam.view @ cam.proj
            want = ours[:2] / ours[3] * (1.0, -1.0 if flip else 1.0)
            assert np.allclose(game[:2] / game[3], want, atol=1e-4)
