"""117's clouds by the game's environment shaders (``render.gameclouds``): the ambient's and the settings' values,
the engine's constants, a volume read slice by slice; with an installed Anno 117 (``ANNO117_DIR``) and Direct3D 12
the sky looking up holds white clouds that the view without them does not."""

from __future__ import annotations

import struct

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.models import ddsdecode
from annotoolbox.render import gameatmosphere as ga
from annotoolbox.render import gameclouds as gc
from annotoolbox.render import gameshader as gs

AMBIENT = """<Ambient><CloudShadowIntensity>0.8</><CloudShadowIntensityNgt>0</><ShadowCutoffHeight>0</>
<CloudAlbedo><x>1</><y>0.9</><z>0.8</></>
<WeatherMap>data\\base\\graphics\\library\\ambient\\clouds\\cloud_unique\\roman\\roman_01.png</></Ambient>"""
SETTINGS = """<ViewDistance><QualityLevel0><ViewDistance>450.000000</ViewDistance></QualityLevel0>
<QualityLevel1><ViewDistance>750.000000</ViewDistance></QualityLevel1></ViewDistance>"""


def _volume_dds(slices: np.ndarray) -> bytes:
    """A one-level R32F volume (legacy D3DFORMAT 114) of ``slices`` (depth, height, width)."""
    depth, height, width = slices.shape
    head = bytearray(128)
    head[:4] = b"DDS "
    struct.pack_into("<IIIIIII", head, 4, 124, 0x1007 | 0x800000, height, width, width * 4, depth, 1)
    struct.pack_into("<II4s", head, 76, 32, ddsdecode.DDPF_FOURCC, bytes([114, 0, 0, 0]))
    struct.pack_into("<II", head, 108, 0x1000, ddsdecode.CAPS2_VOLUME)
    return bytes(head) + slices.astype("<f4").tobytes()


def test_the_ambient_and_the_settings():
    got = gc.read_ambient(AMBIENT)
    assert got["cCloudShadowIntensityDay"] == 0.8 and got["cCloudShadowIntensityNgt"] == 0.0
    assert got["cCloudAlbedo"] == (1.0, 0.9, 0.8)
    assert got["weather"].endswith("roman/roman_01.png") and "\\" not in got["weather"]
    assert gc.far_plane(SETTINGS) == 750.0  # the top quality level
    assert gc.far_plane("") == gc.FAR_PLANE


def test_the_engines_constants_and_the_camera():
    eye = np.array([3.0, 2.0, -5.0])
    cam = gs.Camera(eye, np.identity(4), np.identity(4), 0.1, 750.0, (64, 64), 27.0)
    got = gc.constants(cam, gc.read_ambient(AMBIENT))
    assert got["cCloudDomeRadius"] == 200000.0 and got["cWeathermapScale"] == 1250.0
    assert got["cCloudLayerHeight"] == 208.0 and got["cCloudCurlIntensity"] == 7.0
    assert got["cFrustumPos"] == [3.0, -5.0]  # the self-shadow cache centred under the camera
    assert got["cSSCUpdateFactor"] == 1.0 and "weather" not in got  # only constants go to the shader
    assert cam.shadow_distance == 27.0  # the cascades keep the view's far plane
    assert gs.Camera(eye, np.identity(4), np.identity(4), 0.1, 30.0, (64, 64)).shadow_distance == 30.0
    # the atmosphere's dome is the engine's too, with or without clouds
    assert ga.CLOUD_LAYER["cCloudDomeRadius"] == gc.DOME_RADIUS and "cTransmittanceMap" in ga.WHITE


def test_the_wind_moves_the_weather_and_the_noise_as_the_engine_adds_them_up():
    still = gc.scroll(0.0)
    assert still["cWeatherScroll"] == [0.0, 0.0] and still["cCloudNoiseScroll"] == [0.0, 0.0, 0.0]
    direction, drift = gc.wind_drift(np.array([0.0]), gc.Wind(angle=0.0, strength=1.0))
    assert np.allclose(direction, [1.0, 0.0]) and np.allclose(drift, [[2.0, 0.0]])  # 2 x direction x strength
    later = gc.scroll(10.0, gc.Wind(angle=np.pi / 2, strength=1.0))
    assert abs(later["cWeatherScroll"][0]) < 1e-6 and 10 * 1.8 / 1250 < later["cWeatherScroll"][1] < 10 * 2.2 / 1250
    noise, curl = later["cCloudNoiseScroll"], later["cCloudCurlScroll"]
    assert noise[1] == pytest.approx(0.75 * 10.0) and curl[1] == pytest.approx(0.1 * 10.0)  # both rise
    assert noise[2] == pytest.approx(0.75 * 10.0 + later["cWeatherScroll"][1] * 1250, rel=1e-6)
    _d, fast = gc.wind_drift(np.linspace(0, 60, 200), gc.Wind(strength=3.0))
    assert np.all(np.linalg.norm(fast, axis=1) <= 2 * gc.MAX_STRENGTH)  # the strength's cap
    cam = gs.Camera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 750.0, (64, 64))
    assert gc.constants(cam, {}, 10.0, gc.Wind(angle=np.pi / 2))["cWeatherScroll"] == later["cWeatherScroll"]


def test_a_volume_slice_by_slice():
    slices = np.arange(2 * 4 * 4, dtype=np.float32).reshape(2, 4, 4)
    dds = ddsdecode.DdsFile(_volume_dds(slices))
    assert dds.info.depth == 2 and dds.slices(0) == 2
    assert np.array_equal(dds.level_float(0, 0, 1)[..., 0], slices[1])
    with pytest.raises(IndexError):
        dds.level_float(0, 0, 2)
    levels = gc._volume(dds, floats=True)
    assert levels[0].shape == (2, 4, 4, 4) and levels[0].dtype == np.float16
    assert float(levels[0][1, 3, 3, 0]) == 31.0


INSTALL_CLOUDS = """
import json, math, sys, time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import RhiModelView
app = QApplication([])
tree = open_game("anno117", Path(sys.argv[1]))
scene = modelscene.build(tree, "data/base/graphics/library/props/storage/barrels/barrel_01.cfg", "anno117")
out = {}
for key, switches in (("off", {"clouds": False}), ("on", {})):
    view = RhiModelView("d3d12")
    view.state = ViewState(look="shaders", particles=False, switches=switches)
    view.show_scene(scene, ModelFiles(tree))
    start = time.monotonic()
    while time.monotonic() - start < 600:
        img = view.picture(160, 120)
        r = view.renderer
        if r is not None and r.box is not None and not r.busy() and len(r.items) == len(scene.parts) and time.monotonic() - start > 2:
            view.frame_model()
            view.camera.pitch = math.radians(-35)  # looking up from a unit above the ground
            view.camera.target[1] = 1.0 + view.camera.distance * math.sin(math.radians(35))
            for _ in range(3):
                img = view.picture(160, 120)
            break
        time.sleep(0.03)
    px = [img.pixelColor(x, y).getRgb()[:3] for x in range(0, 160, 4) for y in range(0, 120, 4)]
    out[key] = sum(1 for r, g, b in px if r > 170 and b - r < 45)  # white: a cloud, not the blue sky
    out.setdefault("errors", []).extend(list(view.ctx.errors))
    sky = r.game_pass.sky if r is not None and r.game_pass is not None else None
    if sky is not None:  # the sky light's buffer read back with the next run of the sky passes
        sky.read_back, sky._done = True, ()
        for _ in range(3):
            view.picture(160, 120)
        raw = bytes(sky.readback.data) if sky.readback is not None else b""
        out["cloudy_light" if key == "on" else "clear_light"] = raw.hex()
    view.release()
print(json.dumps(out))
"""


def test_clouds_in_the_sky_of_the_install():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(INSTALL_CLOUDS.replace("sys.argv[1]", repr(root)))
    assert not got["errors"], got["errors"]
    assert got["off"] == 0  # the clear sky
    assert got["on"] > 20  # the Roman weather map's clouds over the model
    assert got["cloudy_light"] != got["clear_light"]  # the clouds went into the sky's light (AddReflectedClouds)
