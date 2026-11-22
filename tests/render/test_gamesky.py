"""117's sky light by the game's sky passes (``render.gamesky``): the ambient's atmosphere read with the engine's
values for the rest, the light buffer's layout; with an installed Anno 117 (``ANNO117_DIR``) and Direct3D 12 the
passes' sun and sky light read back against the reference model of rendering/lighting.md section 6."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import gamesky

AMBIENT = """<Ambient><Settings><GroundColorAmbient><x>0.2</>
<y>0.25</>
<z>0.3</>
</>
<MoonLight><x>0.379</>
<y>0.433</>
<z>1.097</>
</>
<MoonSkyStrength>0.17</>
<PhysicalAtmosphere><GroundAlbedo><x>0.4</>
<y>0.7</>
<z>0.8</>
</>
<MieStrength>3e-06</>
<RayleighStrength>1e-04</>
<MieG>0.8</>
<RayleighScattering><x>0.058</>
<y>0.135</>
<z>0.331</>
</>
<SunIrradiance><x>1</>
<y>1</>
<z>0.5</>
</>
<SunStrength>15</>
</>
<Exposure>0.55</>
</></>"""


def test_the_ambients_atmosphere_and_the_engines_values():
    a = gamesky.read_atmosphere(AMBIENT)
    assert a["cAtmosphereSunIrradiance"] == (15.0, 15.0, 7.5)  # irradiance times strength
    assert a["cAtmosphereRayleighScattering"] == (0.058, 0.135, 0.331) and a["cAtmosphereRayleighStrength"] == 1e-4
    assert a["cAtmosphereGroundAlbedo"] == (0.4, 0.7, 0.8) and a["cGroundColorAmbient"] == (0.2, 0.25, 0.3)
    assert a["cAtmosphereMieScaleHeight"] == 1200  # not in the file: the default of the Roman ambient
    assert a["cAtmosphereMoonScatterFactor"] == 0.17 and a["cAtmosphereMoonStrength"] == (0.379, 0.433, 1.097)
    assert a["cAtmosphereRadiusBottom"] == 6360000.0  # the engine's [E]
    assert gamesky.block(AMBIENT, "Missing") == ""


def test_only_the_cloudy_sky_is_made_again_when_only_the_clouds_moved():
    from types import SimpleNamespace

    passes = object.__new__(gamesky.CSkyPasses)  # no device: the passes are counted, not recorded
    passes._m_done, passes._m_clouds, passes.m_read_back, passes.m_compiled = (), None, False, {}
    passes.m_transmittance = passes.m_multiscat = passes.m_light = passes.m_sky = passes.m_reflection = None
    calls = []
    passes._pass = lambda ctx, slot, *a: calls.append(slot.key if not slot.clear else "clear sh")
    passes._sky_cube = lambda ctx, tables, sky, clouds=None: calls.append("cloudy cube" if clouds else "clear cube")
    passes._cube_face = lambda ctx, slot, *a: calls.append(slot.key)
    clouds = SimpleNamespace(m_reflected=object(), m_wind=(1.0, 0.0), m_seconds=3.0, maps_ready=lambda: True,
                             inputs=dict)  # fmt: skip
    sky = SimpleNamespace(sun=(0.0, -1.0, 0.2), night=False)
    assert passes.run(None, sky, clouds) and "transmittance" in calls and "clear cube" in calls
    calls.clear()
    clouds.m_seconds = 4.0  # the same step: nothing again
    assert not passes.run(None, sky, clouds) and not calls
    clouds.m_seconds = gamesky.CLOUD_STEP + 1.0
    assert passes.run(None, sky, clouds)
    assert "cloudy cube" in calls and calls.count("sh") == 6 and calls.count("filter") == 6 * gamesky.LEVELS
    assert not {"transmittance", "multiscat", "light", "clear cube", "clear sh"} & set(calls)
    calls.clear()
    passes.run(None, SimpleNamespace(sun=(0.0, -1.0, 0.3), night=False), clouds)  # the sun moved: all again
    assert {"transmittance", "clear cube", "clear sh"} <= set(calls)


def test_the_light_buffer_is_packed_as_the_structured_buffer_holds_it():
    sh = [(k, k + 0.5, k + 0.25) for k in range(9)]
    raw = gamesky.light_bytes(sh, (4.45, 4.05, 3.47))
    assert len(raw) == gamesky.LIGHT_BYTES == 320
    f = np.frombuffer(raw, np.float32)
    assert np.allclose(f[:36].reshape(9, 4)[:, :3], sh) and np.allclose(f[36:72], f[:36])  # clear sky = sky
    assert np.allclose(f[72:75], (4.45, 4.05, 3.47)) and np.allclose(f[76:79], (4.45, 4.05, 3.47))


INSTALL_SKY = """
import json, sys, time
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
view = CRhiModelView("d3d12")
view.m_state = SViewState(look="shaders", particles=False)
view.show_scene(scene, CModelFiles(tree))
start = time.time()
while time.time() - start < 600:
    view.picture(160, 120)
    r = view.m_renderer
    if r is not None and r.m_game_pass.m_sky is not None and not r.busy() and time.time() - start > 2:
        break
    time.sleep(0.03)
sky = view.m_renderer.m_game_pass.m_sky
sky.m_read_back, sky._m_done = True, ()
view.picture(160, 120)
view.picture(160, 120)
d = np.frombuffer(bytes(sky.m_readback.data), np.float32)
print(json.dumps({"sh": d[36:72].reshape(9, 4)[:, :3].tolist(), "clear": d[:36].reshape(9, 4)[:, :3].tolist(),
                  "sun": d[72:75].tolist(), "errors": list(view.m_renderer.m_messages) + list(view.m_ctx.m_errors)}))
"""


def test_the_games_sky_passes_light_as_the_reference_model():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    from annotoolbox.render import gamelook

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(INSTALL_SKY.replace("sys.argv[1]", repr(root)))
    assert not got["errors"], got["errors"]
    # the view's sun at the Roman cap of 58 degrees: the reference model's LgtDiffuse and ambient (lighting.md 6)
    assert got["sun"] == pytest.approx(gamelook.SUN_DIFFUSE, rel=0.02)
    assert np.allclose(got["sh"][0], gamelook.SKY_SH[0], rtol=0.15)
    # the cloudy sky's coefficients take the clouds (AddReflectedClouds): a little less light than the clear sky's
    assert got["sh"] != got["clear"] and np.all(np.asarray(got["sh"][0]) <= np.asarray(got["clear"][0]))
