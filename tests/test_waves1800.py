"""1800's small ocean waves by the game's compute shader (``render.waves1800``; shader-compile.md 11 "1800's small ocean
waves"): the engine's wave time and defines, the switch offered and on, the material's define taken; with an
installed Anno 1800 (``ANNO1800_DIR``) and Direct3D 11 a fountain's water moves with the view's clock and lies still
with the switch off."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.models import shaderforward
from annotoolbox.render import frameforward, nativelook, waves1800


def test_the_wave_time_starts_at_5_and_runs_a_tenth_as_fast_as_the_clock():
    assert waves1800.wave_time(0) == 5.0 and waves1800.wave_time(10) == pytest.approx(6.0)
    defines = dict(waves1800.DEFINES)
    assert defines["FFT_MAP_SIZE"] == "512.0" and defines["FFT_GRADIENTS_THREAD_GROUPS"] == "16"
    assert waves1800.GRID_LEN == 0.125


def test_the_switch_is_offered_off_until_turned_on_and_off_the_water_lies_still():
    # off as the other games' Ocean waves: on, 1800's sea takes the place of the view's ground (ForwardPass1800.SEA)
    assert not nativelook.part_on("anno1800", {}, "waves") and nativelook.part_on("anno1800", {"waves": True}, "waves")
    assert frameforward.FALLBACKS[waves1800.MAP] == (0, 0, 0, 0)  # no gradient: the flat normal
    defines = dict(shaderforward.material_defines({waves1800.SWITCH: "1"}, "model_water", "anno1800"))
    assert waves1800.SWITCH in defines


def test_the_volumes_are_read_signed_as_half_floats(monkeypatch):
    class Dds:
        def __init__(self, raw):
            self.raw = raw

        def slices(self, mip):
            return 2

        def level_float(self, mip, face, z):
            return np.full((4, 4, 4), -1.5 - z, np.float32)

    monkeypatch.setattr(waves1800.ddsdecode, "DdsFile", Dds)
    got = waves1800.read_volumes(type("Files", (), {"read": lambda self, path: path})())
    xz, y = got["cSmallWaveDisplacementMapXZ"], got["cSmallWaveDisplacementMapY"]
    assert len(xz) == 2 and xz[0].shape == (4, 4, 4) and y[1].shape == (4, 4, 1)
    assert xz[0].dtype == np.float16 and float(y[1][0, 0, 0]) == -2.5  # below 0 kept


def test_switched_off_or_unasked_the_volumes_are_released():
    from annotoolbox.viewer.forwardpass import ForwardPass1800

    released = []
    p = object.__new__(ForwardPass1800)
    p.waves = type("Part", (), {"error": "", "release": lambda self: released.append(1)})()
    p.r = type("R", (), {"files": type("F", (), {"files": None})()})()
    p._on = lambda key: True
    plain = type("M", (), {"water": True, "fields": {}})()
    assert p._small_waves([plain], 1.0) is None and p.waves is None and released == [1]


INSTALL = """
import json, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import rhi_picture
app = QApplication([])
tree = open_game("anno1800", Path(sys.argv[1]))
scene = modelscene.build(tree, "data/graphics/buildings/ornamental/seasonpass_04/ornament_fountain_01.cfg", "anno1800")
out = {}
for name, t, switches in (("t0", 0.0, {"waves": True}), ("t3", 3.0, {"waves": True}), ("off", 0.0, {})):
    state = ViewState(look="shaders", particles=False, time=t, switches=switches, ground=False)  # no sea: no ground
    img = rhi_picture(ModelFiles(tree), scene, state,
                      (200, 150), "d3d11", 600)
    out[name] = [[img.pixelColor(x, y).getRgb()[:3] for x in range(200)] for y in range(150)]
print(json.dumps(out))
"""


def test_the_installed_fountains_water_moves_with_the_clock_and_lies_still_switched_off():
    root = os.environ.get("ANNO1800_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if "d3d11" not in _backends():
        pytest.skip("no Direct3D 11 device")
    got = _child(INSTALL.replace("sys.argv[1]", repr(root)), timeout=900)
    t0, t3, off = (np.asarray(got[k], float) for k in ("t0", "t3", "off"))
    moved, still = np.abs(t3 - t0).sum(axis=2), np.abs(t0 - off).sum(axis=2)
    assert (moved > 6).sum() > 15  # the waves move on
    assert (still > 6).sum() > 15  # and they are not the still water's flat normal
