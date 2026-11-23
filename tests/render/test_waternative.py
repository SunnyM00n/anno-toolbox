"""The water and the transparent pass on the native backend (render/waternative): a technique's vertex shader picked
by the bindings it shares with the pixel shader, the pass's native runs recorded by their owners in order, a QRhi
texture a pass writes moved into unordered access and back; with the game and the module: the sea's, the rivers'
and the coast waves' shipped shaders bound into pipelines on Windows' software rasteriser, and an island's sea drawn
natively in the island view."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from tests.render.renderfakes import native_modules

from annotoolbox.infra.dxc import SBinding
from annotoolbox.models.shadercache import SCachedShader
from annotoolbox.render import gamesea, renderstates, terrainnative, waternative


class FakeNatives:
    """Shaders by entry and their reflections, as a variant of the cache holds them."""

    def __init__(self, shaders: dict, reflections: dict, calls: tuple) -> None:
        self._shaders, self._reflections = shaders, reflections
        self._techniques = {"T": SimpleNamespace(calls=calls)}

    def variant(self, stem, vf="", switches=()):
        return (stem, switches)

    def techniques(self, stem):
        return self._techniques

    def shaders(self, v):
        return self._shaders

    def call(self, v, entry, args=""):
        return next(s for s in self._shaders.values() if s.name == entry)

    def reflection(self, s):
        return self._reflections[s.bytecode]


def _reflection(*bindings) -> SimpleNamespace:
    return SimpleNamespace(bindings=[SBinding(n, "texture", r, 1, 1, dimension=4) for n, r in bindings], cbuffers=[])


def test_the_vertex_shader_is_the_one_that_binds_as_the_pixel_shader_does():
    shaders = {"vsRiver": SCachedShader("vsRiver", (), b"lava"), "vsRiver#1": SCachedShader("vsRiver", (), b"river"),
               "psRiver": SCachedShader("psRiver", (), b"ps")}  # fmt: skip
    reflections = {b"lava": _reflection(("cWavesElevation", 8)), b"river": _reflection(("cRiverGradientsRO", 9)),
                   b"ps": _reflection(("cPhysicalWavesMap0", 8), ("cRiverGradientsRO", 9))}  # fmt: skip
    natives = FakeNatives(shaders, reflections, (("vs", "vsRiver", ""), ("ps", "psRiver", "")))
    vs, ps = waternative.pass_shaders(natives, "water_inland_water", (), "T", ("vsRiver", "psRiver"))
    assert vs.bytecode == b"river" and ps.bytecode == b"ps"  # the first one binds t8 to another map
    natives._techniques["T"] = SimpleNamespace(calls=(("vs", "vsRiver", ""),))
    with pytest.raises(waternative.CNativeError):
        waternative.pass_shaders(natives, "water_inland_water", (), "T", ("vsRiver", "psRiver"))


class FakeOwner:
    def __init__(self, fails: bool = False) -> None:
        self.m_error, self.fails, self.calls = "", fails, []

    def record(self, command_buffer, *args) -> None:
        self.calls.append(args)
        if self.fails:
            raise RuntimeError("refused")


def test_a_runs_draws_go_to_their_owners_in_order_and_a_failing_owner_stops():
    water, objects = FakeOwner(), FakeOwner(fails=True)
    frame = waternative.SFrameWater(water, objects)
    sea, lake = SimpleNamespace(name="sea"), SimpleNamespace(name="lake")
    runs = frame.runs([lake, sea])
    assert all(r.m_native for r in runs) and [r.m_model for r in runs] == [lake, sea]
    part = SimpleNamespace(m_native=True)
    frame.record("cb", [*runs, part, part], "camera", [], {}, (4, 4))
    assert water.calls == [([lake, sea], "camera", [], {}, (4, 4))]
    assert objects.calls[0][:2] == ("transparent", [part, part]) and objects.m_error == "RuntimeError: refused"
    frame.record("cb", [part], "camera", [], {}, (4, 4))
    assert len(objects.calls) == 1  # off until its owner says so and drops it
    assert waternative.SFrameWater(None, objects).runs([sea]) == [sea]  # no native water: the current path's


def test_a_texture_a_pass_writes_goes_into_unordered_access_and_back():
    bridge = object.__new__(terrainnative.CBridge)
    bridge.m_reads, bridge.m_writes = {1: terrainnative.SHADER_READ, 2: 0x400}, {3: terrainnative.SHADER_READ}
    before, after = bridge.before(), bridge.after()
    assert (2, 0x400, terrainnative.SHADER_READ) in before and (3, terrainnative.SHADER_READ, 0x8) in before
    assert {(r, b, a) for r, a, b in before} == set(after) and len(before) == 2


def test_the_seas_draw_finds_its_maps_before_the_frames():
    model = object.__new__(gamesea.CSeaModel)
    model.m_lent, model.m_own = {"cOceanFoamMap": "waves"}, {"cTerrainSDF": "own"}
    shared = {"cOceanFoamMap": "frame", "cColorBufferCopy": "copy"}
    assert [model.lookup(n, shared) for n in ("cOceanFoamMap", "cTerrainSDF", "cColorBufferCopy", "x")] == [
        "waves", "own", "copy", None]  # fmt: skip


@pytest.mark.xdist_group("island_view")
def test_the_water_binds_the_shipped_shaders_on_the_software_rasteriser():
    from tests.render.renderfakes import native_module

    natives = native_modules()
    native_module()
    from annotoolbox.render import nativegpu

    waternative.shaders(natives)  # every technique the current path draws, both variants
    gpu = nativegpu.own_device(adapter=-2)
    water = waternative.CNativeWater(gpu, natives)
    calls = natives.techniques(waternative.SEA)["PatchInner"]
    state = renderstates.pass_state(calls.state)
    state = renderstates.SPassState(state.depth_test, state.depth_write, state.blends, writes=state.writes,
                                   stencil=renderstates.stencil_of(calls.state))  # fmt: skip
    program = SimpleNamespace(
        m_vertex_stage=SimpleNamespace(entry="vsOcean"), m_pixel_stage=SimpleNamespace(entry="psOcean")
    )
    sea = SimpleNamespace(m_program=program, m_switches=waternative.PHYSICAL, m_state=state, mesh=gamesea.grid(5))
    tech = water.technique(sea)
    assert water.technique(sea) is tech and tech.state["stencil"] and tech.state["stencil_func"] == "not_equal"
    pipeline, constant = water.pipeline(tech, sea.mesh)
    river = SimpleNamespace(m_program=SimpleNamespace(m_vertex_stage=SimpleNamespace(entry="vsRiver"),
                                                      m_pixel_stage=SimpleNamespace(entry="psRiver")),
                            m_switches=waternative.EGYPT, m_state=renderstates.SPassState(depth_write=False))  # fmt: skip
    _p, _c = water.pipeline(water.technique(river), gamesea.grid(3))  # inputs the mesh lacks read a constant
    assert pipeline is not None and constant.size >= 16
    shader = natives.call(natives.variant(waternative.WAVES), waternative.WAVES_ENTRY)
    waves = water._make((shader,), ("cs",))
    assert {i.kind for spec in waves.plan.layouts for i in spec.items} >= {"rwtexture", "texture", "cbuffer"}
    assert gpu.compute_pipeline(waves.shaders[0], waves.layouts).compute
    water.release()
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
cams.pop("close")
pictures, numbers = nc.one_way(session, app.m_editor, cams, (320, 180), 1, 600.0, "water")
print(json.dumps({"nodes": numbers["nodes"], "said": numbers["said"], "failed": numbers["failed"]}))
"""


def test_an_islands_sea_draws_natively_in_the_island_view():
    native_modules()
    from tests.render.renderfakes import native_module, render_backends, run_child

    native_module()
    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(DRAW)
    assert not got["failed"] and not got["said"], got
    assert got["nodes"]["whole"]["water_native"] > 0, got  # the sea by the shipped ocean shader
