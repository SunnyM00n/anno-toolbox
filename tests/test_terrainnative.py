"""The terrain on the native backend (render/terrainnative): the frame's camera buffer laid out as the shipped shader
reads it, a shipped shader found by its technique's arguments, QRhi's resources moved into shader reads and back,
and with the game and the module: every technique's shipped shaders bound into pipelines on Windows' software
rasteriser, and an island's terrain drawn both ways in the island view."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.infra.dxc import ConstantBuffer, Variable
from annotoolbox.models import shadercache, shadernative
from annotoolbox.render import gameshader as gs
from annotoolbox.render import terrainnative


def test_the_camera_buffer_is_three_records_written_by_the_frames_paths():
    members = [Variable("CamPosition", 0, 0, "float4", rows=1, columns=4),
               Variable("MtxView", 16, 0, "float4x4", rows=4, columns=4)]  # fmt: skip
    element = ConstantBuffer("cCameraBuffer", 80, [Variable("$Element", 0, 80, "CameraData", members=members)])
    cb = terrainnative.camera_buffer(element)
    assert cb.size == 80 * terrainnative.CAMERAS
    c = gs.Constants(cb)
    c.set("cCameraBuffer[2].CamPosition", [1.0, 2.0, 3.0, 1.0])
    assert np.frombuffer(bytes(c.data[160:176]), np.float32).tolist() == [1.0, 2.0, 3.0, 1.0]


def test_a_shipped_shader_is_found_by_its_techniques_arguments():
    shaders = [shadercache.CachedShader("vsTerrain", ("2true",), b"plain"),
               shadercache.CachedShader("vsTerrain", ("2false",), b"detailed")]  # fmt: skip
    ns = object.__new__(shadernative.NativeShaders)
    ns.shaders = lambda v: shadernative._by_function(shaders)
    v = shadernative.Variant("terrain")
    assert ns.call(v, "vsTerrain", "2false").bytecode == b"detailed"
    with pytest.raises(KeyError):
        ns.call(v, "vsTerrain", "1false")
    for stages in terrainnative.TECHNIQUES.values():  # as the cache stores them: no spaces
        assert all(" " not in args for _f, args in stages.values())


def test_qrhis_resources_go_into_shader_reads_and_back():
    bridge = object.__new__(terrainnative.Bridge)
    bridge.reads = {10: 0x8, 11: terrainnative.SHADER_READ, 12: 0x400}
    assert bridge.before() == [(10, 0x8, 0xC0), (12, 0x400, 0xC0)]
    assert bridge.after() == [(10, 0xC0, 0x8), (12, 0xC0, 0x400)]


def _natives():
    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from annotoolbox.infra import dxc
    from annotoolbox.sources.anno117 import Anno117Files

    try:
        compiler = dxc.Dxc.find(anno117=root)
    except Exception as exc:
        pytest.skip(f"the game's compiler: {exc}")
    return shadernative.NativeShaders(Anno117Files(root), compiler)


def test_every_technique_binds_the_games_shipped_shaders_on_the_software_rasteriser():
    from tests.test_nativegpu import _module

    natives = _natives()
    _module()
    from annotoolbox.render import nativegpu

    found = terrainnative.shaders(natives)
    assert natives.shipped(natives.variant(terrainnative.EFFECT))
    assert set(found) == set(terrainnative.TECHNIQUES)
    gpu = nativegpu.own_device(adapter=-2)
    terrain = terrainnative.NativeTerrain(gpu, natives)
    detailed = terrain.techniques["TerrainDeferredDetailed"]
    names = {i.slot: i.name for spec in detailed.plan.layouts if spec.space == 1 for i in spec.items}
    assert names[3] == "cBakedHeightTexArray" and names[4] == "cBakedNormalTexArray"  # the technique's own layout
    assert detailed.tessellated and terrain.techniques["TerrainShadow"].plan.push is not None
    terrain.release()
    assert gpu.errors == 0, gpu.messages()


DRAW = """
import json, os, sys
sys.path.insert(0, "scripts")
import native_compare as nc
from annotoolbox.editor import editorapp
from annotoolbox.editor.editsession import EditSession
app = editorapp.App()
app.open("anno117", nc.island_path(app.files(), "roman_island_small_01"))
session = EditSession(json.loads(app.editor.island_json()), app.editor.edits())
session.finish_loading()
cams = nc.cameras(session)
cams.pop("whole")
pictures, numbers = nc.one_way(session, app.editor, cams, (320, 180), 1, 600.0)
print(json.dumps({"nodes": numbers["nodes"], "said": numbers["said"], "failed": numbers["failed"]}))
"""


def test_an_islands_terrain_draws_natively_in_the_island_view():
    _natives()
    from tests.test_nativegpu import _module
    from tests.test_rhi import _backends, _child

    _module()
    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(DRAW)
    assert not got["failed"] and not got["said"], got
    assert got["nodes"]["close"]["terrain_native"] == 1 and got["nodes"]["close"]["terrain_nodes"] > 0, got
    # its pages and its ground's bake by the shipped shaders on the module too (pagesnative, bakenative)
    assert got["nodes"]["close"]["pages_native"] == 1 and got["nodes"]["close"]["bake_native"] == 1, got
