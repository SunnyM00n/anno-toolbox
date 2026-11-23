"""The terrain on the native backend (render/terrainnative): the frame's camera buffer laid out as the shipped shader
reads it, a shipped shader found by its technique's arguments, QRhi's resources moved into shader reads and back,
and with the game and the module: every technique's shipped shaders bound into pipelines on Windows' software
rasteriser, and an island's terrain drawn both ways in the island view."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from tests.render.renderfakes import native_modules

from annotoolbox.infra.dxc import SConstantBuffer, SVariable
from annotoolbox.models import shadercache, shadernative
from annotoolbox.render import gameshader as gs
from annotoolbox.render import terrainnative


def test_the_camera_buffer_is_three_records_written_by_the_frames_paths():
    members = [SVariable("CamPosition", 0, 0, "float4", rows=1, columns=4),
               SVariable("MtxView", 16, 0, "float4x4", rows=4, columns=4)]  # fmt: skip
    element = SConstantBuffer("cCameraBuffer", 80, [SVariable("$Element", 0, 80, "CameraData", members=members)])
    cb = terrainnative.camera_buffer(element)
    assert cb.size == 80 * terrainnative.CAMERAS
    c = gs.CConstants(cb)
    c.set("cCameraBuffer[2].CamPosition", [1.0, 2.0, 3.0, 1.0])
    assert np.frombuffer(bytes(c.m_data[160:176]), np.float32).tolist() == [1.0, 2.0, 3.0, 1.0]


def test_a_shipped_shader_is_found_by_its_techniques_arguments():
    shaders = [shadercache.SCachedShader("vsTerrain", ("2true",), b"plain"),
               shadercache.SCachedShader("vsTerrain", ("2false",), b"detailed")]  # fmt: skip
    ns = object.__new__(shadernative.CNativeShaders)
    ns.shaders = lambda v, only="": shadernative._by_function(shaders)
    v = shadernative.SVariant("terrain")
    assert ns.call(v, "vsTerrain", "2false").bytecode == b"detailed"
    with pytest.raises(KeyError):
        ns.call(v, "vsTerrain", "1false")
    for stages in terrainnative.TECHNIQUES.values():  # as the cache stores them: no spaces
        assert all(" " not in args for _f, args in stages.values())


def test_qrhis_resources_go_into_shader_reads_and_back():
    bridge = object.__new__(terrainnative.CBridge)
    bridge.m_reads, bridge.m_writes = {10: 0x8, 11: terrainnative.SHADER_READ, 12: 0x400}, {}
    assert bridge.before() == [(10, 0x8, 0xC0), (12, 0x400, 0xC0)]
    assert bridge.after() == [(10, 0xC0, 0x8), (12, 0xC0, 0x400)]


def test_a_recording_that_raises_still_moves_qrhis_resources_back(monkeypatch):
    """``CBridge.send``: the module's list first, then the draws between the moves there and back; a draw that raises
    leaves no resource in another state than QRhi tracks (the rest of QRhi's frame is recorded from those)."""
    calls = []
    bridge = object.__new__(terrainnative.CBridge)
    bridge.m_reads, bridge.m_writes = {10: 0x8}, {}
    bridge.m_module = type("M", (), {"transitions": lambda self, lst, items: calls.append((lst, list(items)))})()
    monkeypatch.setattr(terrainnative.nativegpu, "command_list", lambda command_buffer: "qrhi")
    own = type("L", (), {"execute": lambda self: calls.append("sent")})()

    def record(lst) -> None:
        calls.append(("drawn", lst))
        raise RuntimeError("a draw that did not bind")

    with pytest.raises(RuntimeError):
        bridge.send(own, None, record)
    assert calls == ["sent", ("qrhi", [(10, 0x8, 0xC0)]), ("drawn", "qrhi"), ("qrhi", [(10, 0xC0, 0x8)])]
    assert bridge.m_reads == {} and bridge.m_writes == {}


@pytest.mark.xdist_group("island_view")
def test_every_technique_binds_the_games_shipped_shaders_on_the_software_rasteriser():
    from tests.render.renderfakes import native_module

    natives = native_modules()
    native_module()
    from annotoolbox.render import nativegpu

    found = terrainnative.shaders(natives)
    assert natives.shipped(natives.variant(terrainnative.EFFECT))
    assert set(found) == set(terrainnative.TECHNIQUES)
    gpu = nativegpu.own_device(adapter=-2)
    terrain = terrainnative.CNativeTerrain(gpu, natives)
    detailed = terrain.m_techniques["TerrainDeferredDetailed"]
    names = {i.slot: i.name for spec in detailed.plan.layouts if spec.space == 1 for i in spec.items}
    assert names[3] == "cBakedHeightTexArray" and names[4] == "cBakedNormalTexArray"  # the technique's own layout
    assert detailed.tessellated and terrain.m_techniques["TerrainShadow"].plan.push is not None
    terrain.release()
    assert gpu.errors == 0, gpu.messages()


DRAW = """
import json, os, sys
sys.path.insert(0, "scripts")
import native_compare as nc
from annotoolbox.editor import editorapp
from annotoolbox.editor.editsession import CEditSession
app = editorapp.CApp()
app.open("anno117", nc.island_path(app.files(), "roman_island_small_01"))
session = CEditSession(json.loads(app.m_editor.island_json()), app.m_editor.edits())
session.finish_loading()
cams = nc.cameras(session)
cams.pop("whole")
pictures, numbers = nc.one_way(session, app.m_editor, cams, (320, 180), 1, 600.0)
print(json.dumps({"nodes": numbers["nodes"], "said": numbers["said"], "failed": numbers["failed"]}))
"""


def test_an_islands_terrain_draws_natively_in_the_island_view():
    native_modules()
    from tests.render.renderfakes import native_module, render_backends, run_child

    native_module()
    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(DRAW)
    assert not got["failed"] and not got["said"], got
    assert got["nodes"]["close"]["terrain_native"] == 1 and got["nodes"]["close"]["terrain_nodes"] > 0, got
    # its pages and its ground's bake by the shipped shaders on the module too (pagesnative, bakenative)
    assert got["nodes"]["close"]["pages_native"] == 1 and got["nodes"]["close"]["bake_native"] == 1, got
