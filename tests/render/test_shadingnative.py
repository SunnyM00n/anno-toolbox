"""Step 5 on the native backend (render/shadingnative, nativeforeign, skynative, atmospherenative): the states a
stretch of work on QRhi's list carries and gives back, the barriers between its writes, the upsample's blend states
from the technique, what the transparent pass reads of the clouds; with the game and the module: every shader of
the step bound into a pipeline on Windows' software rasteriser, and an island lit natively in the island view."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from tests.render.renderfakes import native_modules

from annotoolbox.render import atmospherenative, nativeforeign, shadingnative


class FakeResource:
    def __init__(self, native: int) -> None:
        self.native = native


class FakeModule:
    def __init__(self) -> None:
        self.calls: list = []

    def transitions(self, _list, items) -> None:
        self.calls.append(("transitions", list(items)))

    def uav_barrier(self, _list) -> None:
        self.calls.append(("barrier",))

    def record_dispatch(self, *_args) -> None:
        self.calls.append(("dispatch",))


def _recording() -> nativeforeign.CRecording:
    rec = object.__new__(nativeforeign.CRecording)
    rec.m_module, rec.m_device, rec.m_list, rec.m_bridge = FakeModule(), None, 0, type("B", (), {"reads": {}})()
    rec.m_home, rec.m_now, rec.m_dirty = {}, {}, set()
    return rec


def test_a_stretch_on_qrhis_list_leaves_every_resource_as_it_found_it():
    rec = _recording()
    rec.m_home[1] = 0x4  # QRhi's: a render target
    depth, lit, mine = FakeResource(1), FakeResource(2), FakeResource(3)
    read, write = nativeforeign.SHADER_READ, nativeforeign.UNORDERED
    rec.dispatch(None, [], [(depth, read), (lit, write)], (1, 1, 1))
    rec.dispatch(None, [], [(lit, write), (mine, write)], (1, 1, 1))  # writes what the last one wrote: a barrier
    rec.dispatch(None, [], [(lit, read)], (1, 1, 1))
    rec.finish()
    calls = rec.m_module.calls
    assert calls[0] == ("transitions", [(1, 0x4, read), (2, read, write)])
    assert calls[2] == ("barrier",) and calls[3] == ("transitions", [(3, read, write)])
    assert calls[5] == ("transitions", [(2, write, read)])
    assert calls[-1] == ("transitions", [(1, read, 0x4), (3, write, read)])
    assert rec.m_now == {} and rec.m_dirty == set()


def test_a_stretch_that_raises_still_gives_every_resource_back():
    """QRhi records the rest of its frame from the states it tracks: a stretch cut short by a raise moves back what
    it moved before the raise (``with Recording``)."""
    rec = _recording()
    rec.m_home[1] = 0x4
    with pytest.raises(RuntimeError), rec:
        rec.dispatch(None, [], [(FakeResource(1), nativeforeign.SHADER_READ)], (1, 1, 1))
        raise RuntimeError("a dispatch that did not bind")
    assert rec.m_module.calls[-1] == ("transitions", [(1, nativeforeign.SHADER_READ, 0x4)]) and rec.m_now == {}


def test_the_upsample_blends_as_its_technique_says():
    state = {"cullmode": "NONE", "alphablendenable": "TRUE", "independentblendenable": "TRUE",
             "srcblend[0]": "ONE", "dstblend[0]": "SRC_ALPHA", "srcblendalpha[0]": "ZERO",
             "dstblendalpha[0]": "ONE", "srcblend[1]": "ZERO", "dstblend[1]": "ONE", "colorwriteenable[0]": "0x7",
             "colorwriteenable[1]": "0x0"}  # fmt: skip
    first, second = atmospherenative.blends(state, 2)
    assert first == (True, "one", "src_alpha", "add", "zero", "one", "add", 7)
    assert second == (True, "zero", "one", "add", "zero", "one", "add", 0)
    assert atmospherenative.blends({}, 1) == [(False, "one", "zero", "add", "one", "zero", "add", 15)]


def test_a_whole_texture_is_copied_from_and_into_the_copy_states_and_given_back():
    rec = _recording()
    copied = []
    rec.m_module.copy_texture = lambda _list, destination, source: copied.append((destination, source))
    history, target = FakeResource(5), FakeResource(6)
    rec.dispatch(None, [], [(target, nativeforeign.UNORDERED)], (1, 1, 1))
    rec.copy_texture(history, target)
    rec.finish()
    read = nativeforeign.SHADER_READ
    assert copied == [(5, 6)]
    assert rec.m_module.calls[2] == (
        "transitions", [(5, read, nativeforeign.COPY_DEST), (6, nativeforeign.UNORDERED, nativeforeign.COPY_SOURCE)]
    )  # fmt: skip
    assert rec.m_module.calls[-1] == (
        "transitions", [(6, nativeforeign.COPY_SOURCE, read), (5, nativeforeign.COPY_DEST, read)]
    )  # fmt: skip


class FakeHistoryOwner:
    """What the scattering's history reads of the native shading: the camera record, the device, the module's list."""

    def __init__(self) -> None:
        from annotoolbox.infra.dxc import SConstantBuffer, SVariable

        members = [
            SVariable("MtxViewPrev", 0, 64, "float4x4", 4, 4),
            SVariable("MtxProjPrev", 64, 64, "float4x4", 4, 4),
        ]
        self.m_element = SConstantBuffer("cCameraBuffer", 128, [SVariable("$Element", 0, 128, "CameraData",
                                                                          members=members)])  # fmt: skip
        self.written: list[bytes] = []
        self.m_device = SimpleNamespace(buffer=lambda *_args, **_kwargs: "cameras")
        self.m_common = SimpleNamespace(m_list=SimpleNamespace(write_buffer=lambda _b, data: self.written.append(data)))


def test_the_clouds_and_fog_history_reprojects_from_last_frames_camera_and_settles_in_four_still_frames():
    import numpy as np

    from annotoolbox.render import gameshader as gs

    owner = FakeHistoryOwner()
    history = atmospherenative.CScatteringHistory(owner)
    turned = np.identity(4)
    turned[3, 0] = 5.0
    first = gs.SCamera(np.zeros(3), np.identity(4), np.identity(4), 0.1, 100.0, (8, 8))
    second = gs.SCamera(np.zeros(3), turned, np.identity(4), 0.1, 100.0, (8, 8))
    left = []
    for camera in (first, second, second, second, second, second, second):
        history.step(camera, (4, 4))
        left.append(history.m_settle_left)
    assert left == [4, 4, 3, 2, 1, 0, 0]  # a moved camera starts the count again
    assert history.m_frame_counter == 7  # cFrameCounter turns the marched pixel of each quad
    prev_view = np.frombuffer(owner.written[1][:64], "<f4").reshape(4, 4)
    assert np.allclose(prev_view, np.identity(4))  # the second frame reprojects from the first one's view
    assert history.m_has_stepped
    history.forget()
    assert history.m_settle_left == 0


def test_the_kept_cloud_cache_scrolls_with_the_camera_and_is_made_anew_for_a_new_sun_or_a_jump_in_time():
    import numpy as np

    from annotoolbox.render import skynative

    shifted = []
    sky = object.__new__(skynative.CNativeSky)
    sky.m_kernels = {"shift": SimpleNamespace(dispatch=lambda _list, _named, fill, grid: shifted.append(grid))}
    sky.m_list = SimpleNamespace(uav_barrier=lambda: None)
    sky.m_description = SimpleNamespace(m_is_cache_kept=True, m_seconds=1.0, m_wind="calm")
    sky._m_cache_at = None
    sky._named = lambda _key, named: named
    camera, sun = SimpleNamespace(eye=np.array([1.0, 2.0, 3.0])), SimpleNamespace(sun=(0.0, 1.0, 0.0), night=False)
    assert sky._shift(camera, sun, {}) == 1.0  # nothing kept yet
    assert sky._shift(camera, sun, {}) == skynative.CACHE_BLEND and shifted == [(16, 16, 16)]
    assert sky._shift(camera, SimpleNamespace(sun=(1.0, 0.0, 0.0), night=False), {}) == 1.0  # a new sun
    sky.m_description.m_seconds = 9.0
    assert sky._shift(camera, SimpleNamespace(sun=(1.0, 0.0, 0.0), night=False), {}) == 1.0  # the clock jumped
    sky.m_description.m_is_cache_kept = False
    assert sky._shift(camera, SimpleNamespace(sun=(1.0, 0.0, 0.0), night=False), {}) == 1.0  # the switch is off
    assert len(shifted) == 1


def test_the_transparent_pass_reads_the_clouds_shadow_only_once_it_was_made():
    seen = shadingnative.SCloudsSeen(None, {"cTransmittanceMap": 1, "cCloudBaseNoise": 2})
    assert seen.inputs() == {"cCloudBaseNoise": 2}
    seen.shadowed = True
    assert seen.inputs() == {"cTransmittanceMap": 1, "cCloudBaseNoise": 2}


@pytest.mark.xdist_group("island_view")
def test_every_shader_of_the_step_binds_on_the_software_rasteriser():
    from tests.render.renderfakes import native_module

    natives = native_modules()
    native_module()
    from annotoolbox.render import nativegpu

    gpu = nativegpu.own_device(adapter=-2)
    found = shadingnative.shaders(natives)
    assert {"atm.select", "atm.accumulate", "sky.shift"} <= set(found)  # the shipped variant accumulates over frames
    common = shadingnative.CCommon(gpu, natives)
    draws = ("atm.quad", "atm.down", "atm.up")
    kernels = [common.kernel(s) for key, s in found.items() if key not in draws and not key.startswith("sky.")]
    refl = natives.reflection
    quad = (found["atm.quad"], refl(found["atm.quad"]))
    down = nativeforeign.CForeignKernel.draw(gpu, {"vs": quad, "ps": (found["atm.down"], refl(found["atm.down"]))},
                                            atmospherenative.DOWN_STATE, common.m_stand_ins)  # fmt: skip
    shading = kernels[0]
    resolved = shading.resolve({})  # every binding stood in: zeros, white for the clear-air maps
    assert set(resolved) >= {"cGBuffer0", "cDepth", "cDstColor", "cLightBuffer", "cShadowInformation"}
    assert resolved["cSSSTex"] is common.m_stand_ins.white_texture()
    sets = shading.sets(0, resolved, shading.write(common.m_list, 0, None))
    assert len(sets) == len(shading.m_layouts)
    common.m_list.execute()
    for k in [*kernels, down]:
        k.release()
    common.release()
    assert gpu.errors == 0, gpu.messages()


DRAW = """
import json, sys
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
pictures, numbers = nc.one_way(session, app.m_editor, cams, (320, 180), 1, 600.0, "shading")
print(json.dumps({"nodes": numbers["nodes"], "said": numbers["said"], "failed": numbers["failed"]}))
"""


def test_an_island_is_lit_natively_in_the_island_view():
    native_modules()
    from tests.render.renderfakes import native_module, render_backends, run_child

    native_module()
    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(DRAW)
    assert not got["failed"] and not got["said"], got
    assert got["nodes"]["close"]["shading_native"] == 1, got
