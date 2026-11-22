"""1800's screen-space reflections on its model water by the game's shaders (``render.ssrforward``; shader-compile.md 11
"1800's screen-space reflections"): the stencil reads QRhi cannot bind, the last frame's camera, the switch offered
and taken by the quality, the trace pass of a water material; with an installed Anno 1800 (``ANNO1800_DIR``) and
Direct3D 11 the wall fountain's basin reflects its wall."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.models import shaderforward
from annotoolbox.render import frameforward, nativelook, ssrforward
from annotoolbox.render.gameshader import SCamera, direct_camera
from annotoolbox.render.quality import SQuality


def test_the_stencil_reads_become_the_gloss_targets_coverage_and_none_for_the_scene():
    text = ("uint stencil = cScreenReflectStencilMap . Load ( int3 ( input . Position . xy , 0 ) ) . g ;\n"
            "uint s2 = cDefaultMRTStencil.Load(int3(srcPos, 0)).g;")  # fmt: skip
    out = shaderforward.stencil_reads(text)
    assert "cScreenReflectStencilMap" not in out and "cDefaultMRTStencil" not in out
    assert "cScreenReflectNormalMap.Load(int3(input . Position . xy, 0)).g > 0.0 ? 1u : 0u" in out
    assert "uint s2 = 0u;" in out


def test_the_last_frames_camera_and_the_half_size():
    cam = direct_camera((640, 361), (10.0, 8.0, 10.0), (0.0, 0.0, 0.0), 0.8, 0.5, 900.0)
    prev = ssrforward.previous_values(cam)
    assert np.allclose(prev["cMtxViewPrev"] @ prev["cMtxProjPrev"], cam.view @ cam.projection)
    assert np.allclose(prev["cMtxViewProjPrev"], cam.view @ cam.projection)
    assert ssrforward.half((640, 361)) == (320, 180) and ssrforward.half((1, 1)) == (1, 1)
    moved = SCamera(cam.eye, cam.view.copy(), cam.projection, cam.near, cam.far, cam.size_px)
    assert ssrforward.same_camera(cam, moved)
    moved.view[3, 0] += 0.01
    assert not ssrforward.same_camera(cam, moved)


def test_the_switch_is_offered_for_1800_and_follows_the_quality():
    assert nativelook.GAMES["anno1800"].features["ssr"] == nativelook.YES
    assert nativelook.GAMES["anno2205"].features["ssr"] == nativelook.RIVERS_2205
    assert nativelook.part_on("anno1800", {}, "ssr")
    medium = nativelook.with_quality({}, SQuality(ssr=False))  # the game's levels 0 and 1 draw none
    assert not nativelook.part_on("anno1800", medium, "ssr")
    assert frameforward.ENGINE_OFF["cScreenReflectEnabled"] == 0.0  # written each frame: off reads no map


def test_a_water_material_gets_its_trace_and_an_opaque_one_its_pre_pass():
    from annotoolbox.models.fxstage import SState
    from annotoolbox.viewer.forwardpass import CForwardPass1800

    class FakeShaders:
        def prepass(self, values):
            return ("pre",), SState()

        def has_technique(self, values, technique):
            return technique == ssrforward.TECHNIQUE

        def model_pass(self, values, technique):
            return (technique,)

        def model_state(self, values, technique):
            return SState()

    source = type("Source", (), {"m_shaders": FakeShaders()})()
    p = object.__new__(CForwardPass1800)
    water = p._frame_passes(source, {"ShaderID": "7"}, SState(blend=True))
    assert set(water) == {"ssr"} and water["ssr"][0] == (ssrforward.TECHNIQUE,)
    assert set(p._frame_passes(source, {"ShaderID": "8"}, SState())) == {"prepass"}


def test_switched_off_the_part_is_released():
    from annotoolbox.viewer.forwardpass import CForwardPass1800

    released = []
    p = object.__new__(CForwardPass1800)
    p.m_ssr = type("Part", (), {"error": "", "pending": False, "release": lambda self: released.append(1)})()
    p._on = lambda key: key != "ssr"
    traced = type("M", (), {"ssr": object()})()
    assert p._ssr([traced]) is None and p.m_ssr is None and released == [1]


INSTALL = """
import json, sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import CModelFiles
from annotoolbox.viewer.modelrender import SViewState
from annotoolbox.viewer.modelview import rhi_picture
app = QApplication([])
tree = open_game("anno1800", Path(sys.argv[1]))
scene = modelscene.build(tree, "data/graphics/buildings/ornamental/seasonpass_04/ornament_fountain_01.cfg", "anno1800")
out = {}
for name, switches in (("on", {}), ("off", {"ssr": False})):
    img = rhi_picture(CModelFiles(tree), scene, SViewState(look="shaders", particles=False, switches=switches),
                      (200, 150), "d3d11", 600)
    out[name] = [[img.pixelColor(x, y).getRgb()[:3] for x in range(200)] for y in range(150)]
print(json.dumps(out))
"""


def test_the_installed_fountains_basin_reflects_its_wall():
    root = os.environ.get("ANNO1800_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    if "d3d11" not in render_backends():
        pytest.skip("no Direct3D 11 device")
    got = run_child(INSTALL.replace("sys.argv[1]", repr(root)), timeout=900)
    on, off = (np.asarray(got[k], float) for k in ("on", "off"))
    changed = np.abs(on - off).sum(axis=2) > 6
    assert changed.sum() > 100  # the basin's water
    rows, cols = np.nonzero(changed)
    assert rows.min() > 150 * 0.4 and cols.max() < 200 * 0.8  # the basin in front, not the wall or the sky


def test_the_blur_reads_the_half_cameras_pixel_size(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    cam = direct_camera((640, 360), (10.0, 8.0, 10.0), (0.0, 0.0, 0.0), 0.8, 0.5, 900.0)
    sizes = []
    monkeypatch.setattr(
        ssrforward, "screen_pass", lambda ctx, draw, target, inputs, values, size_px: sizes.append(size_px)
    )
    texture = SimpleNamespace(native=object())
    ssr = ssrforward.CSsr1800.__new__(ssrforward.CSsr1800)
    ssr.m_ctx, ssr.m_counter, ssr.m_results = None, 0, 0
    ssr.m_blur_x, ssr.m_blur_y = object(), [object(), object()]
    ssr.m_targets = dict.fromkeys(("color", "gloss", "depth"), texture)
    ssr.m_targets.update({"blur0": (texture, object()), "results": ((texture, object()), (texture, object()))})
    ssr._blur(replace(cam, size_px=(320, 180)), {})
    assert sizes == [(320, 180), (320, 180)]
