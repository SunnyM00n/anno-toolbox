"""117's deferred shading on QRhi (``render.gamelight``): the key of what its fill writes, so a frame whose camera,
light, cascades, clouds and lights stand leaves its constant buffer as it is (``gamebind.keyed``)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import gameclouds, gamelight, gameshadow
from annotoolbox.render import gameshader as gs


def _camera(x: float = 0.0) -> gs.SCamera:
    view = np.identity(4)
    view[3, 0] = x
    return gs.SCamera(np.zeros(3), view, np.identity(4), 0.1, 100.0, (8, 8))


def test_the_shadings_fill_key_stands_for_a_still_frame_and_follows_each_input_it_writes():
    from types import SimpleNamespace

    shading = gamelight.CDeferredShading.__new__(gamelight.CDeferredShading)
    shading.m_cube_log2 = 5.0
    sky = gamelight.SSky((0.0, 1.0, 0.0), (1.0, 1.0, 1.0), ((0.0, 0.0, 0.0),) * 9)
    cascades = gameshadow.SCascades((), np.zeros(3), 100.0, gpu=True, used=3)
    clouds = SimpleNamespace(moment=lambda: ("ambient", 0.0, gameclouds.CALM))
    lights = SimpleNamespace(spots=np.zeros(8, np.float32), areas=np.zeros(8, np.float32))

    def key(camera=None, sky=sky, cascades=cascades, clusters=lights):
        return shading.fill_key(camera or _camera(), sky, gamelight.SExtras(cascades, clusters, clouds))

    still = key()
    assert key() == still  # every input made anew, equal by content
    assert key(_camera(1.0)) != still
    assert key(sky=gamelight.SSky((1.0, 1.0, 0.0), sky.sun_diffuse, sky.sh)) != still
    assert key(cascades=gameshadow.SCascades((), np.zeros(3), 100.0, gpu=True, used=2)) != still
    assert key(cascades=None) != still
    spot = SimpleNamespace(spots=np.ones(8, np.float32), areas=lights.areas)
    assert key(clusters=spot) != still and key(clusters=None) != still
    assert gameshadow.fill_key(None) == () and gameshadow.fill_key(cascades) == (3, gameshadow.SIZE, 100.0)
