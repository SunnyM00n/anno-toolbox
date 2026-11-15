"""117's post chain by the game's shaders (``render.gamepost``): the full-screen triangle maps the screen onto
Direct3D's texture coordinates; with an installed Anno 117 (``ANNO117_DIR``) and Direct3D 12 the grade and tone map
draw the residence (not one flat colour) and differ from our finish."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.render import gamepost


def test_the_triangle_covers_the_screen_with_direct3ds_coordinates():
    pos = np.array(gamepost.TRIANGLE["float3"], float)[:, :2]
    uv = np.array(gamepost.TRIANGLE["float2"], float)
    # the affine map clip -> uv the triangle sets up: u = (x + 1) / 2, v = (1 - y) / 2 (row 0 at the top)
    m = np.linalg.lstsq(np.c_[pos, np.ones(3)], uv, rcond=None)[0]
    for x, y in ((-1, -1), (1, 1), (0, 0)):
        assert np.allclose(np.r_[x, y, 1] @ m, ((x + 1) / 2, (1 - y) / 2))
    assert gamepost.CONSTANTS["cExposure"] == 0.55 and gamepost.CONSTANTS["cViewportCropFactor"] == (1.0, 1.0)


INSTALL_POST = """
import json, sys
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import rhi_picture
app = QApplication([])
tree = open_game("anno117", Path(sys.argv[1]))
cfg = "data/base/graphics/roman/buildings/residences/residence_tier01_estate06/residence_tier01_estate06.cfg"
scene = modelscene.build(tree, cfg, "anno117")
out = {}
for key, switches in (("ours", {"post": False}), ("game", {})):
    img = rhi_picture(ModelFiles(tree), scene, ViewState(look="shaders", particles=False, switches=switches), (240, 180), "d3d12", wait=600)
    out[key] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(240)] for y in range(180)], float)
print(json.dumps({"std": float(out["game"].std()), "diff": float(np.abs(out["game"] - out["ours"]).mean())}))
"""


def test_the_games_grade_and_tone_map_draw_the_view():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(INSTALL_POST.replace("sys.argv[1]", repr(root)))
    assert got["std"] > 20 and 0.5 < got["diff"] < 30, got  # a picture, close to our port of the same curve
