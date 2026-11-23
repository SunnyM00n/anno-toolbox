"""117's ocean wave simulation by the game's shaders (``render.gameocean``): the ambient's two simulations and the
engine's band table into the spectrum's rows, the radix-2 passes the effect names without arguments; with an installed
Anno 117 (``ANNO117_DIR``) and Direct3D 12 a fountain's water moves with the waves' time."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from annotoolbox.formats import fxeffect
from annotoolbox.render import gameocean

AMBIENT = """<Ambient><Water><CoastalWaveFetch>16000</>
<CoastalWaveWindSpeed>1</>
<CoastalWaveChoppiness>1</>
<CoastalWaveDirectionality>0.441</>
<CoastalWaveOceanDepth>100</>
<OffshoreWaveFetch>120000</>
<OffshoreWaveWindSpeed>3</>
<OffshoreWaveChoppiness>1</>
<OffshoreWaveDirectionality>0.657</>
<OffshoreWaveOceanDepth>100</>
<WaveSeed>35</>
</>
</>"""


def test_the_ambients_simulations_and_the_engines_bands_make_eight_slices():
    water = gameocean.read_water(AMBIENT)
    assert water["seed"] == 35 and water["simulations"]["Offshore"]["Fetch"] == 120000.0
    rows = gameocean.spectrum_rows(water)
    assert len(rows) == 8  # coastal bands 0-3, offshore bands 0-3
    assert [r["PatchDim"] for r in rows[:4]] == [2521.0, 1201.0, 211.0, 31.0]
    assert rows[3]["MinKLength"] == 1.19 and rows[3]["MaxKLength"] > 1e38  # the smallest band: unbounded
    assert rows[5]["WindSpeed"] == 3.0 and rows[5]["Directionality"] == 0.657
    assert gameocean.CONSTANTS["cBand3PatchDims"] == 31.0  # what a model water's small waves are scaled by


def test_a_shader_named_without_arguments_is_a_call():
    text = """technique OceanIDFTRadix2Vertical
{
    pass p0
    {
        SetComputeShader(csFFTRadix2Vertical);
    }
};
technique BlurScattering
{
    pass passHorz { SetComputeShader(csBlurScattering(int2(1, 0))); }
    pass passVert { SetComputeShader(csBlurScattering(int2(0, 1))); }
};
"""
    calls = [(t.name, c.function, c.args) for t, c in fxeffect.shader_calls(fxeffect.split_effect(text))]
    assert ("OceanIDFTRadix2Vertical", "csFFTRadix2Vertical", ()) in calls
    blur = [c for c in calls if c[0] == "BlurScattering"]
    assert [c[2] for c in blur] == [("int2(1, 0)",), ("int2(0, 1)",)]  # the second pass is "#1" to the compile


INSTALL_WAVES = """
import json, sys
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import CModelFiles
from annotoolbox.viewer.modelrender import SViewState
from annotoolbox.viewer.modelview import rhi_picture
app = QApplication([])
tree = open_game("anno117", Path(sys.argv[1]))
cfg = "data/base/graphics/roman/buildings/ornaments/cultural/ornament_2x2_fountain_01.cfg"
scene = modelscene.build(tree, cfg, "anno117")
out = {}
for key, switches, t in (("flat", {"waves": False}, 0.0), ("t0", {}, 0.0), ("t3", {}, 3.0)):
    state = SViewState(look="shaders", particles=False, switches=switches, time=t)
    img = rhi_picture(CModelFiles(tree), scene, state, (240, 180), "d3d12", wait_s=600)
    out[key] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(240)] for y in range(180)], float)
print(json.dumps({"waves": int((np.abs(out["t0"] - out["flat"]).sum(-1) > 6).sum()),
                  "time": int((np.abs(out["t3"] - out["t0"]).sum(-1) > 0).sum())}))
"""


@pytest.mark.time_limit(900)  # compiles the game's shaders: minutes under a full run's load
def test_the_ocean_simulation_moves_a_fountains_water():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    # three pictures, the first compiling the game's shaders: the child's default five minutes ran out under load
    got = run_child(INSTALL_WAVES.replace("sys.argv[1]", repr(root)), timeout=880)
    assert got["waves"] > 100, got  # the waves' normals and caustics against the flat surface
    assert got["time"] > 100, got  # and they move with the time
