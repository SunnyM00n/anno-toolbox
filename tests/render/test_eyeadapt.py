"""117's eye adaptation by the game's shaders (``render.eyeadapt``): the engine's values into ``cbHistogram``, its
buffers as raw buffers; with an installed Anno 117 (``ANNO117_DIR``) and Direct3D 12 the exposure read back stays at
the minimum by day and rises at night, between the engine's limits."""

from __future__ import annotations

import struct

import pytest

pytest.importorskip("PySide6")

from annotoolbox.infra.dxc import SConstantBuffer, SVariable
from annotoolbox.render import eyeadapt, gameshader, rawbuffers

EYE = """
RWStructuredBuffer<uint> cMergedHistogramOut;
StructuredBuffer<uint> cMergedHistogramIn;
RWStructuredBuffer<float> cExposureOut;
StructuredBuffer<float> cExposureBuffer;
void Merge(uint i, uint count) { InterlockedAdd(cMergedHistogramOut[i], count); }
void Out(float e) { float f = cExposureOut[0] / e; cExposureOut[0] = f; }
float Tone(float3 c) { return c.r * cExposureBuffer[0] + cMergedHistogramIn[3]; }
"""


def test_the_engines_values_go_into_the_histograms_constants():
    names = [("cImageOffset", "uint", 2), ("cImageSize", "uint", 2), ("cNumTiles", "uint", 2)]
    names += [(n, "float", 1) for n in eyeadapt.CONSTANTS] + [("cProgressive", "int", 1), ("cRealTimer", "float", 4)]
    variables, at = [], 0
    for name, kind, n in names:
        variables.append(SVariable(name, at, 4 * n, kind, rows=1, columns=n))
        at += 4 * n
    c = gameshader.CConstants(SConstantBuffer("cb", 0x60, variables))

    def read(name, fmt="f"):
        v = next(v for v in variables if v.name == name)
        return struct.unpack_from("<" + fmt * v.columns, c.m_data, v.offset)

    eyeadapt.fill(c, (100, 70), None)  # the first frame: no easing
    assert read("cNumTiles", "I") == (4, 3) and read("cImageSize", "I") == (100, 70)
    assert read("cHistogramMin") == (-12.0,) and read("cHistogramRange") == (16.0,)
    assert read("cMaxExposure") == (12.0,) and read("cExposureTarget") == pytest.approx((0.18,))
    assert read("cProgressive", "i") == (0,)
    eyeadapt.fill(c, (100, 70), 0.25)  # a later frame eases with its time step
    assert read("cProgressive", "i") == (1,) and read("cRealTimer")[1] == 0.25
    eyeadapt.fill(c, (100, 70), 3.0)  # after a pause: at the target at once
    assert read("cProgressive", "i") == (0,)


def test_the_exposure_and_histograms_are_raw_buffers():
    out = rawbuffers.rewrite(EYE, gameshader.RAW_BUFFERS)
    assert "StructuredBuffer" not in out.replace("RWByteAddressBuffer", "")
    assert "cMergedHistogramOut.InterlockedAdd((uint)(i) * 4 + 0, count)" in out
    assert "cExposureBuffer.Load<float>((uint)(0) * 4)" in out
    assert "cMergedHistogramIn.Load<uint>((uint)(3) * 4)" in out
    assert "cExposureOut.Store<float>" in out


def test_overloads_by_a_textures_element_type_get_one_bindless_copy():
    text = (  # as the linker hands a pass's functions: declared, then defined with the brace on its own line
        "float  TexelFetch(Texture2D<float > tex, int2 p);\nfloat4 TexelFetch(Texture2D<float4> tex, int2 p);\n"
        "float  TexelFetch(Texture2D<float > tex, int2 p)\n{ return tex.Load(int3(p, 0)); }\n"
        "float4 TexelFetch(Texture2D<float4> tex, int2 p)\n{ return tex.Load(int3(p, 0)); }\n"
    )
    out = gameshader.adapt(text, "Main").hlsl
    assert out.count("TexelFetch(BindlessTex tex, int2 p);") == 1  # HLSL cannot overload by return type alone
    assert out.count("TexelFetch(BindlessTex tex, int2 p)\n{") == 1
    assert out.count("TexelFetch(Texture2D<float4> tex, int2 p)") == 2


INSTALL_EYE = """
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
out = {}
for night in (False, True):
    view = CRhiModelView("d3d12")
    view.m_state = SViewState(look="shaders", particles=False, night=night)
    view.show_scene(scene, CModelFiles(tree))
    start = time.time()
    while time.time() - start < 600:
        view.picture(160, 120)
        r = view.m_renderer
        if r is not None and r.m_game_pass.m_eye is not None and not r.busy() and time.time() - start > 2:
            break
        time.sleep(0.03)
    eye = view.m_renderer.m_game_pass.m_eye
    eye.m_read_back = True
    view.picture(160, 120)
    view.picture(160, 120)
    out["night" if night else "day"] = np.frombuffer(bytes(eye.m_readback.data), np.float32).tolist()
    out.setdefault("errors", []).extend(list(view.m_ctx.m_errors))
    view.release()
print(json.dumps(out))
"""


def test_the_games_eye_adaptation_brightens_the_night_only():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.render.renderfakes import render_backends, run_child

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(INSTALL_EYE.replace("sys.argv[1]", repr(root)))
    assert not got["errors"], got["errors"]
    for exposure in (got["day"], got["night"]):
        assert 1.0 <= exposure[0] <= eyeadapt.MAX_EXPOSURE  # within the engine's limits
        # [6] and [7]: 0.18 over the scene's mean luminance, clamped below at 1 and above at the maximum
        assert exposure[6] >= 1.0 and 0.0 < exposure[7] <= eyeadapt.MAX_EXPOSURE
        assert (
            exposure[6] == pytest.approx(1.0)
            or exposure[7] == pytest.approx(eyeadapt.MAX_EXPOSURE)
            or (exposure[6] == pytest.approx(exposure[7]))
        )
    assert got["day"][0] == pytest.approx(1.0)  # a lit day scene sits at the minimum exposure
