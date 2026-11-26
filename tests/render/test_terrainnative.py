"""The terrain on the native backend (render/terrainnative): the frame's camera buffer laid out as the shipped shader
reads it, a shipped shader found by its technique's arguments, QRhi's resources moved into shader reads and back,
and with the game and the module: every technique's shipped shaders bound into pipelines on Windows' software
rasteriser, and an island's terrain drawn both ways in the island view."""

from __future__ import annotations

import gc
import weakref

import numpy as np
import pytest

pytest.importorskip("PySide6")

from tests.render.renderfakes import native_modules

from annotoolbox.infra.dxc import SConstantBuffer, SVariable
from annotoolbox.models import shadercache, shadernative
from annotoolbox.render import gameshader as gs
from annotoolbox.render import terrainnative
from annotoolbox.render.nativelayout import SItem, SLayoutSpec


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


def test_a_qrhi_texture_is_wrapped_once_whichever_python_object_hands_it(monkeypatch):
    """PySide hands a new Python object for the same QRhi texture: the bridge keys a wrap by the Direct3D resource
    and its view, so the same wrap (and the binding sets made with it) serve every frame; keyed by the object's id, a
    new wrap and new sets came every frame, and the sets kept filled the sampler heap until the driver crashed."""
    from PySide6.QtGui import QRhiTexture

    class FakeQRhiTexture:
        def __init__(self, pointer: int, texture_format=QRhiTexture.Format.RGBA8) -> None:
            self.pointer, self.m_format = pointer, texture_format

        def format(self):
            return self.m_format

        def flags(self):
            return QRhiTexture.Flag(0)

        def arraySize(self) -> int:
            return 0

    wraps = []
    bridge = object.__new__(terrainnative.CBridge)
    bridge.m_held, bridge.m_frame, bridge.m_reads, bridge.m_writes = {}, 0, {}, {}
    bridge.m_module = type("M", (), {"qrhi_texture": lambda self, pointer: (pointer * 16, 0),
                                     "qrhi_buffer": lambda self, pointer: pointer * 16})()  # fmt: skip
    bridge.m_device = type("D", (), {"wrap_texture": lambda self, *args: wraps.append(args) or object(),
                                     "wrap_buffer": lambda self, *args: wraps.append(args) or object()})()  # fmt: skip
    monkeypatch.setattr(
        terrainnative, "shiboken6", type("S", (), {"getCppPointer": staticmethod(lambda o: (o.pointer,))})
    )
    handed = [FakeQRhiTexture(7) for _ in range(4)]  # alive together: four Python objects of one texture
    first = bridge.texture(handed[0])[0]
    assert all(bridge.texture(other)[0] is first for other in handed[1:]) and len(wraps) == 1
    assert bridge.texture(FakeQRhiTexture(8))[0] is not first  # another texture: its own wrap
    assert bridge.texture(FakeQRhiTexture(7, QRhiTexture.Format.R8))[0] is not first  # another view of it: its own
    assert bridge.buffer(FakeQRhiTexture(9), 16)[0] is not bridge.buffer(FakeQRhiTexture(9), 32)[0]
    assert len(wraps) == 5


def test_a_wrap_lapses_only_over_frames_that_used_the_bridge():
    """A kept frame skips the passes: frames that asked for no wrap leave the wraps (and the sets made with them)
    alone, so a standing view makes no sets again; four frames that used the bridge without a wrap let it go."""

    class FakeWrap:
        released = False

        def release(self) -> None:
            self.released = True

    bridge = object.__new__(terrainnative.CBridge)
    kept, other = FakeWrap(), FakeWrap()
    bridge.m_held = {("texture", 1, "RGBA8_UNORM", ""): [kept, 0], ("texture", 2, "RGBA8_UNORM", ""): [other, 0]}
    bridge.m_frame, bridge.m_is_used = 0, False
    for _ in range(3 * terrainnative.KEEP_FRAMES):  # a standing view: no pass asked for a wrap
        assert bridge.end_frame() == []
    assert not kept.released and bridge.m_frame == 0
    gone = []
    for _ in range(terrainnative.KEEP_FRAMES + 1):  # the passes run again and ask for the other one only
        bridge.m_held[("texture", 2, "RGBA8_UNORM", "")][1], bridge.m_is_used = bridge.m_frame, True
        gone += bridge.end_frame()
    assert gone == [kept] and kept.released and not other.released


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


def test_a_kept_set_holds_what_it_binds_so_a_successor_at_its_address_is_not_taken_for_it(monkeypatch):
    """A frame's shared set is kept while it binds the same resources: its key holds them, so a wrap let go cannot
    die and hand its address (its id) to a new wrap of another texture, which the old set would then stand for."""

    class FakeWrap:
        pass

    class FakeSet:
        def release(self) -> None:
            pass

    made = []
    monkeypatch.setattr(terrainnative.nativegpu, "binding_set", lambda *args: made.append(len(args)) or FakeSet())
    terrain = object.__new__(terrainnative.CNativeTerrain)
    terrain.m_device, terrain.m_sets, terrain.m_samplers = None, {}, {"T": {}}
    spec = SLayoutSpec(0, [SItem("texture", 0, 1, "cMap")])
    tech = type("FakeTechnique", (), {"name": "T", "plan": type("P", (), {"layouts": [spec]})(), "layouts": [None]})()
    wrap = FakeWrap()
    first = terrain._shared_set(tech, 0, {"cMap": wrap})
    gone = weakref.ref(wrap)
    del wrap
    gc.collect()
    assert gone() is not None  # held by the kept set's key
    assert terrain._shared_set(tech, 0, {"cMap": FakeWrap()}) is not first and len(made) == 2


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
