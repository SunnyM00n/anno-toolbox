"""1800's ambient occlusion by the game's compute shaders (``render.ssaoforward``; shader-compile.md 11 "1800's ambient
occlusion"): the sliced targets as QRhi binds them, the engine's constants, the switch the game leaves on; with an
installed Anno 1800 (``ANNO1800_DIR``) and Direct3D 11 the occlusion darkens the residence's corners, not its
edges."""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.models import shaderforward
from annotoolbox.render import gamecompute, nativelook, ssaoforward

SPLIT = """cOutQuarterSizeViewDepthTex0 [ uint3 ( dtID . xy , 0 ) ] = Z0 . w ;
cOutQuarterSizeViewDepthTex7 [ uint3 ( dtID . xy , 0 ) ] = Z1 . y ;
cOutQuarterSizeAOTex [ uint3 ( dtID . xy , 0 ) ] = 1.0 ;
RWTexture2DArray < float > cOutQuarterSizeViewDepthTex0 ;
RWTexture2DArray < float > cOutQuarterSizeViewDepthTex5 ;
RWTexture2D < float2 > cOutAOZTex ;
cOutAOZTex [ dtID . xy ] = float2 ( ao , depthVS ) ;"""


def test_the_one_slice_views_become_one_array_written_at_the_slice_index():
    out = shaderforward.slice_targets(SPLIT)
    assert "cOutQuarterSizeViewDepthTex[uint3(dtID . xy, (uint)cSliceIndex + 0)] = Z0 . w" in out
    assert "cOutQuarterSizeViewDepthTex[uint3(dtID . xy, (uint)cSliceIndex + 7)] = Z1 . y" in out
    assert "cOutQuarterSizeAOTex[uint3(dtID . xy, (uint)cSliceIndex + 0)] = 1.0" in out
    assert out.count("RWTexture2DArray<float> cOutQuarterSizeViewDepthTex;") == 1 and "Tex5" not in out
    assert "RWTexture2D<float4> cOutAOZTex" in out and "= float4(float2 ( ao , depthVS )" in out


def test_the_constants_are_the_engines_arithmetic_on_its_settings():
    from annotoolbox.render import gameshader as gs

    cam = gs.direct_camera((1600, 900), (0, 5, 10), (0, 0, 0), 0.8, 0.5, 200.0)
    v = ssaoforward.global_values((1600, 900), cam)
    p11 = 1 / math.tan(0.4)
    assert v["cInvQuarterRes"] == [4 / 1600, 4 / 900] and v["cUVToView1"][1] == pytest.approx(1 / p11)
    assert v["cLinearizeDepth0"] == pytest.approx(1 / 200 - 1 / 0.5) and v["cLinearizeDepth1"] == 2.0
    assert v["cNegInvRadius2"] == pytest.approx(-4.0) and v["cRadiusToScreen"] == pytest.approx(0.25 * 900 * p11)
    assert (v["cNDotVBias"], v["cPowerExponent"], v["cBlurSharpnessFar"], v["cBlurSharpnessNear"]) == (
        0.2,
        2.0,
        12,
        120,
    )
    assert v["cSmallScaleAOFac"] == pytest.approx(2.5) and v["cLargeScaleAOFac"] == pytest.approx(0.75)
    jitters = [ssaoforward.jitter(s) for s in range(ssaoforward.SLICES)]
    assert all(math.hypot(j[0], j[1]) == pytest.approx(1.0) and 0 < j[2] < 1 for j in jitters)
    assert len({round(j[2], 6) for j in jitters}) == ssaoforward.SLICES  # every slice starts elsewhere
    assert ssaoforward.quarter((1601, 2)) == (401, 1)


def test_the_switch_is_offered_and_on_as_the_game_has_it():
    assert nativelook.part_on("anno1800", {}, "ssao") and not nativelook.part_on("anno1800", {"ssao": False}, "ssao")
    from annotoolbox.render.quality import Quality

    assert nativelook.with_quality({}, Quality(ssao=False))["ssao"] is False  # the rendering quality's switch


def test_switched_off_the_occlusion_releases_its_targets_and_the_pre_passs():
    """Audit of 2026-11-14: switched off, the occlusion's full-size targets and the pre-pass's stayed allocated."""
    from annotoolbox.viewer.forwardpass import ForwardPass1800

    released = []

    class Part:
        error = ""

        def release(self):
            released.append("ssao")

    class Frame:
        def drop_prepass(self):
            released.append("prepass")

    p = object.__new__(ForwardPass1800)
    p.ssao, p.frame, p._on = Part(), Frame(), lambda key: False
    assert p._ssao() is None and p.ssao is None and released == ["prepass", "ssao"]


def test_a_compute_stage_that_only_loads_textures_gets_a_sampler_on_direct3d_11():
    """QRhi's Direct3D 11 binds a stage's textures only next to a sampler: a compute shader that only loaded read
    zeros (1800's eye adaptation and occlusion until the audit of 2026-11-14)."""
    from PySide6.QtGui import QRhi

    d3d11 = type("Rhi", (), {"backend": lambda self: QRhi.Implementation.D3D11})()
    d3d12 = type("Rhi", (), {"backend": lambda self: QRhi.Implementation.D3D12})()
    assert gamecompute.lone_textures_unbound(d3d11) and not gamecompute.lone_textures_unbound(d3d12)


INSTALL = """
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
tree = open_game("anno1800", Path(sys.argv[1]))
scene = modelscene.build(tree, "data/graphics/buildings/residence/residence_tier01_01/residence_tier01_01.cfg", "anno1800")
out = {}
for name, switches in (("on", {}), ("off", {"ssao": False})):
    img = rhi_picture(ModelFiles(tree), scene, ViewState(look="shaders", particles=False, switches=switches),
                      (240, 180), "d3d11", 600)
    out[name] = [[img.pixelColor(x, y).getRgb()[:3] for x in range(240)] for y in range(180)]
print(json.dumps(out))
"""


def test_the_installed_residences_occlusion_darkens_some_of_it_and_brightens_none():
    root = os.environ.get("ANNO1800_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 1800 (ANNO1800_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if "d3d11" not in _backends():
        pytest.skip("no Direct3D 11 device")
    got = _child(INSTALL.replace("sys.argv[1]", repr(root)), timeout=900)
    on, off = (np.asarray(got[k], float).sum(axis=2) for k in ("on", "off"))
    darker = off - on
    assert darker.max() > 30 and darker.min() >= -3  # occlusion only takes light away
    assert 0.01 < (darker > 3).mean() < 0.5  # some of the picture, not all of it
