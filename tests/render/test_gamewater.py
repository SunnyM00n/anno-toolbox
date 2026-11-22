"""117's water by the game's shader (``render.gamewater``) and the ground in the game's G-buffer
(``render.gameground``): the ground's square as the model shader reads it; with an installed Anno 117
(``ANNO117_DIR``) and Direct3D 12 a fountain's water draws by ``model_water`` with the scene's copies, not black."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import gameground, gamewater


def test_the_ground_square_faces_up_as_the_model_shader_unpacks_it():
    m = gameground.mesh()
    pos = m.positions()
    assert np.allclose(pos[:, 1], 0) and pos[:, 0].min() == -1 and pos[:, 2].max() == 1
    raw = np.frombuffer(m.m_data, np.uint8).reshape(m.m_count, m.m_stride)
    normal = raw[:, 8:11] / 255.0 * 2 - 1  # UnBias: 2 b - 1
    assert np.allclose(normal, (0, 1, 0), atol=0.01)
    a, b, c = pos[m.m_indices[:3]]
    assert abs(np.cross(b - a, c - a)[1]) > 0  # a real triangle, not a degenerate one


def test_the_waters_constants_and_maps():
    assert gamewater.CONSTANTS["cBand3PatchDims"] > 0  # the caustics divide by it
    assert set(gamewater.MAPS) == {"cWaterFoamMap", "cNoiseTex2"}
    assert set(gamewater.STAGES) == {"depth"} and gamewater.STAGES["depth"][2] == "csDepthCopyToViewZ"


INSTALL_WATER = """
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
for key, switches in (("ours", {"water": False}), ("game", {})):
    img = rhi_picture(CModelFiles(tree), scene, SViewState(look="shaders", particles=False, switches=switches), (240, 180), "d3d12", wait_s=600)
    out[key] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(240)] for y in range(180)], float)
changed = np.abs(out["game"] - out["ours"]).sum(-1) > 30
print(json.dumps({"changed": int(changed.sum()), "mean": float(out["game"][changed].mean()) if changed.any() else 0}))
"""


def test_a_fountains_water_draws_by_the_games_shader():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(INSTALL_WATER.replace("sys.argv[1]", repr(root)))
    assert got["changed"] > 300, got  # the basins' water is the game's, not ours
    assert got["mean"] > 30, got  # it refracts the basin's floor and reflects the sky: not the black of no copies
