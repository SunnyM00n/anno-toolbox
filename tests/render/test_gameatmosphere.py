"""117's visible sky and haze by the game's environment shaders (``render.gameatmosphere``): the passes and the
viewer's values; with an installed Anno 117 (``ANNO117_DIR``) and Direct3D 12 the sky above the horizon is the
game's (a blue that deepens upwards), not the view's own."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import gameatmosphere as ga


def test_the_passes_and_the_values_of_ours():
    assert [ga.STAGES[k][2] for k in ("quad", "down", "clouds", "end", "fog", "blur.h", "blur.v", "up")] == [
        "vsCommonFullScreenQuad", "psDownsampleDepth", "csVolumetricClouds", "csComputeAtmosphereEnd",
        "csVolumetricFog", "csBlurScattering", "csBlurScattering#1", "psUpsampleScatteringVolumes"]  # fmt: skip
    assert ga.DOWNSAMPLE == 2 and ga.GROUP == 16
    # the fade near the camera: the engine's seven values in the shader's order; off, the full physical haze
    assert list(ga.FADE.values()) == [0.35, 0.1, -0.4, 0.2, 0.05, 100.0, 40.0]
    assert ga.NO_FADE["cScatterFadeMinScatter"] == 1.0 and ga.ENVIRONMENT["cFogDensity"] == 0.0
    assert ga.CLOUD_LAYER["cCloudDomeRadius"] > 370  # a dome wider than the layer is high (CLOUD_LAYER_UPPERBOUND)


def test_an_islands_fog_map_is_two_layers_turned_into_the_views_axes_and_sets_the_fog_values(monkeypatch):
    """``rd3d`` ``Fog``: per texel the ground and sea-level density and height bytes, then the colour and type; the
    shader's layer 0 and 1, x turned over as every island map the view reads (island-117-view.md "Phase 7")."""
    import numpy as np

    from annotoolbox.infra.dxc import SConstantBuffer, SVariable
    from annotoolbox.render import gameshader as gs

    raw = np.zeros((4, 4, 8), np.uint8)
    raw[1, 0] = (121, 36, 0, 0, 96, 100, 105, 255)  # row 1 (z), column 0 (x): ground fog, grey, morning only
    layers = ga.fog_layers(raw.tobytes())
    assert layers.shape == (2, 4, 4, 4)
    assert tuple(layers[0, 1, 3]) == (121, 36, 0, 0) and tuple(layers[1, 1, 3]) == (96, 100, 105, 255)
    atmosphere = ga.CAtmosphere.__new__(ga.CAtmosphere)
    atmosphere.m_atmosphere, atmosphere.m_fog, atmosphere.m_fog_size, atmosphere.m_heights = {}, None, 64.0, None
    names = [("cFogDensity", 0), ("cFogHeight", 4), ("cTerrainSize", 16)]
    c = gs.CConstants(SConstantBuffer("cbEnvironment", 32, [SVariable(n, o, 4 if o < 16 else 16, "float", 1, 1)
                                                          for n, o in names]))  # fmt: skip
    camera = gs.SCamera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 10.0, (4, 4))
    sky = type("Sky", (), {"sun": (0.0, 1.0, 0.0), "night": False})()
    monkeypatch.setattr(gs, "fill_frame", lambda c, camera: None)
    monkeypatch.setattr(gs, "fill_sun", lambda c, sun, night: None)
    atmosphere.fill(c, camera, sky, ga.SLook())
    assert np.frombuffer(bytes(c.m_data[:4]), "<f4")[0] == 0.0  # no fog map: no fog
    atmosphere.m_fog = object()
    atmosphere.fill(c, camera, sky, ga.SLook())
    density, height = np.frombuffer(bytes(c.m_data[:8]), "<f4")
    assert density == np.float32(ga.FOG["cFogDensity"]) and height == ga.FOG["cFogHeight"]
    assert np.allclose(np.frombuffer(bytes(c.m_data[16:20]), "<f4"), 64.0)


INSTALL_SKY = """
import json, math, sys, time
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import CModelFiles
from annotoolbox.viewer.modelrender import SViewState
from annotoolbox.viewer.modelview import CRhiModelView
app = QApplication([])
tree = open_game("anno117", Path(sys.argv[1]))
scene = modelscene.build(tree, "data/base/graphics/library/props/storage/barrels/barrel_01.cfg", "anno117")
out = {}
for key, switches in (("ours", {"atmosphere": False}), ("game", {})):
    view = CRhiModelView("d3d12")
    view.m_state = SViewState(look="shaders", particles=False, switches=switches)
    view.show_scene(scene, CModelFiles(tree))
    start = time.monotonic()
    while time.monotonic() - start < 600:
        img = view.picture(160, 120)
        r = view.m_renderer
        if r is not None and r.m_box is not None and not r.busy() and len(r.m_items) == len(scene.m_parts) and time.monotonic() - start > 2:
            view.frame_model()
            view.m_camera.m_pitch_rad = math.radians(5)  # the sky in the upper half
            for _ in range(3):
                img = view.picture(160, 120)
            break
        time.sleep(0.03)
    out[key] = [img.pixelColor(4, y).getRgb()[:3] for y in (2, 36)]  # the left edge: sky, beside the barrel
    out.setdefault("errors", []).extend(list(view.m_ctx.m_errors))
    view.release()
print(json.dumps(out))
"""


def test_the_sky_above_the_horizon_is_the_games():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(INSTALL_SKY.replace("sys.argv[1]", repr(root)))
    assert not got["errors"], got["errors"]
    top, lower = got["game"]
    assert top != got["ours"][0]  # the game's sky, not the view's
    assert top[2] > top[0] + 30  # blue
    assert lower[0] + lower[1] + lower[2] > top[0] + top[1] + top[2]  # paler towards the horizon


def test_a_fog_map_cut_short_reads_its_whole_rows_and_one_without_a_texel_is_no_fog():
    assert ga.fog_layers(bytes(15 * 8)).shape == (2, 3, 3, 4)  # 15 texels: 3 x 3 and a rest (rounded: 4 x 4)
    air = ga.CAtmosphere.__new__(ga.CAtmosphere)
    air.m_fog = None
    air.set_fog(bytes(5), 64.0)
    assert air._m_fog_layers is None and air.m_fog is None
