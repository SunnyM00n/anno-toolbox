"""The pages and the bake on the native backend (``render.pagesnative``, ``bakenative``, ``nativecompute``): their
binding plans, the state handed over between QRhi and the module, the page queue they share with the current path;
with the module and an installed Anno 117 (``ANNO117_DIR``) both chains run on Windows' software rasteriser."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from tests.render.renderfakes import FakeBinding, FakeHandover, FakeReflection

from annotoolbox.infra.dxc import SConstantBuffer, SVariable
from annotoolbox.render import gameshader as gs
from annotoolbox.render import nativecompute, nativegpu, nativelayout, terrainpages
from annotoolbox.render.terrainnodes import SNode


def test_the_bakes_splat_maps_are_a_bindless_range_and_its_buffers_volatile():
    vs = FakeReflection([FakeBinding("cbTerrainBaking", "cbuffer", 0, space=1)])
    ps = FakeReflection([FakeBinding("cbPerFrame", "cbuffer", 0), FakeBinding("cTerrainHeightTex", "texture", 17),
            FakeBinding("cTrnBakeSplatTextures", "texture", 0, count=96, space=1), FakeBinding("cbTerrainBaking", "cbuffer", 0, space=2),
            FakeBinding("cSplatsBuffer", "structured", 6, space=2)])  # fmt: skip
    plan = nativelayout.SBindingPlan.of([vs, ps], bindless=("cTrnBakeSplatTextures",), volatile=True)
    assert [(s.space, s.bindless) for s in plan.layouts] == [(0, False), (1, False), (1, True), (2, False)]
    assert plan.layouts[0].tuples() == [("texture", 17, 1), ("volatile", 0, 1)]
    assert plan.layouts[1].tuples() == [("volatile", 0, 1)] and plan.layouts[2].tuples() == [("texture", 0, 0)]
    assert plan.index(1, 0) == 2
    # without the switches the array stays a bounded one and the buffers static (the terrain's nodes)
    plain = nativelayout.SBindingPlan.of([vs, ps])
    assert ("texture", 0, 96) in plain.layouts[1].tuples() and ("cbuffer", 0, 1) in plain.layouts[0].tuples()


def test_a_structured_buffer_of_records_is_written_by_path():
    members = [SVariable("MatID", 0, 4, "uint", rows=1, columns=1),
               SVariable("Repetition", 4, 4, "float", rows=1, columns=1)]  # fmt: skip
    element = SConstantBuffer("cSplatsBuffer", 32, [SVariable("$Element", 0, 32, "TerrainBakeSplat", members=members)])
    c = gs.CConstants(nativecompute.records(element, 32))
    assert len(c.m_data) == 32 * 32
    c.set("cSplatsBuffer[3].Repetition", 2.5)
    assert np.frombuffer(bytes(c.m_data[100:104]), np.float32)[0] == 2.5


def test_the_page_queue_lets_the_longest_unused_go_never_a_wanted_one():
    q = terrainpages.CPageQueue(64.0, {})
    gone = []
    q._let_go = lambda ctx, pair: gone.append(pair)
    for i in range(terrainpages.KEEP + 3):
        q.m_ready[(0, i, 0)] = (i, i)
        q._m_made[(0, i, 0)] = 0
    q.m_wanted = {(0, 0, 0)}
    q._evict(None)
    assert len(q.m_ready) == terrainpages.KEEP and (0, 0, 0) in q.m_ready and gone == [(1, 1), (2, 2), (3, 3)]
    assert q.m_wanted == set()
    assert set(q._m_made) == set(q.m_ready)  # a page let go keeps no mark of when it was made
    parts = terrainpages.SPageParts(np.zeros((66, 66), np.uint16), [])
    q.want((0, 9, 9), parts)
    assert q.busy() and q.m_queue[(0, 9, 9)] is parts


class FakeTexture:
    def __init__(self, state: int) -> None:
        self.state, self.layout = state, None

    def setNativeLayout(self, layout: int) -> None:
        self.layout = layout


def test_the_states_qrhi_left_are_taken_back_ahead_of_the_modules_list_and_told_after(monkeypatch):
    read = FakeTexture(0x80)  # QRhi read it in a pixel shader last frame
    fresh = FakeTexture(nativecompute.SHADER_READ)
    monkeypatch.setattr(nativecompute, "pointer", id)
    module = SimpleNamespace(qrhi_texture=lambda p: (p + 1, read.state if p == id(read) else fresh.state))
    h = object.__new__(nativecompute.CHandover)
    h.m_module, h.m_given, h._m_moved = module, {id(read): (read, "a"), id(fresh): (fresh, "b")}, []
    recorded = []
    lst = SimpleNamespace(transitions=recorded.append)
    h.take_back(lst, [read, fresh, read, None, FakeTexture(0x80)])  # one not given here is QRhi's own: left alone
    assert recorded == [[(id(read) + 1, 0x80, nativecompute.SHADER_READ)]]
    h.handed_back()
    assert read.layout == fresh.layout == nativecompute.SHADER_READ and h._m_moved == []


def test_a_splat_map_goes_up_as_stored_with_its_rows_pitch():
    from annotoolbox.render import bakenative

    info = SimpleNamespace(format="DXT1", faces=1, width_px=8, height_px=8, mips=1, srgb=True, depth=1)
    dds = SimpleNamespace(m_info=info, stored=lambda level: bytes(32))
    s = bakenative.CStored(dds)
    assert s.m_format == "BC1_UNORM_SRGB" and s.pitch(0) == 16 and s.m_levels[0] == bytes(32)
    missing = bakenative.CStored(bakenative.CMissingMap(2))
    assert (
        missing.m_format == "RGBA8_UNORM"
        and missing.pitch(0) == 16
        and missing.m_levels[0][:4] == bytes([0, 0, 0, 255])
    )


def _game():
    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    if not nativegpu.available():
        pytest.skip(f"the native module: {nativegpu.reason()}")
    from annotoolbox.infra import dxc
    from annotoolbox.models import shadernative
    from annotoolbox.sources.anno117 import CAnno117Files

    compiler = dxc.CDxc.find(anno117=root)
    return compiler, shadernative.CNativeShaders(CAnno117Files(root), compiler)


def test_the_shipped_page_chain_and_the_bake_run_on_the_software_rasteriser():
    from annotoolbox.models import shaderdefines
    from annotoolbox.render import bakenative, gamedraw, pagesnative, terrainbake, terraindraw

    compiler, natives = _game()
    device = nativegpu.own_device(adapter=-2)
    island = terraindraw.island_values(16.0)
    pages = pagesnative.CNativePages(device, natives, FakeHandover(), 16.0, island)
    heights = np.full((33, 33), 2.0)
    pages.set_coarse(heights, 0.5)
    deltas = [(np.zeros((n, n), np.uint8), 0.01, 4) for n in (132, 264, 528)]
    pages.want((0, 0, 0), terrainpages.SPageParts(np.full((66, 66), 30000, np.uint16), deltas))
    pages.run(None)
    assert not pages.m_error, pages.m_error
    height, normal = pages.m_ready[(0, 0, 0)]
    assert (height.format, height.width, normal.format, normal.width) == ("BC4_UNORM", 528, "RGBA8_UNORM", 512)

    done = compiler.compile(terrainbake.FLIP_HLSL, shaderdefines.compile_args(terrainbake.FLIP_ENTRY, "cs_6_2"))
    flip = gamedraw.SStage(done.object, terrainbake.FLIP_ENTRY, compiler.reflect(done.object))
    ground = terrainbake.SGroundSource([terrainbake.SSplat(0)], np.zeros((32, 32), np.uint32),
                                      np.full((64, 64), 31, np.uint32), 16.0)  # fmt: skip
    bake = bakenative.CNativeBake(device, natives, FakeHandover(), ground, flip, slices=2, size_texels=64)
    node = SNode(4, 0.0, 0.0)
    mesh = terrainbake.bake_mesh(heights, 0.5, node, 33, terraindraw.vertex_normals(heights, 0.5))
    values = terrainbake.bake_values(node, ground, island, None, 64)
    bake.run(None, [(bake.model(mesh, values, {}), 1)])
    assert not bake.m_error, bake.m_error
    staging = device.staging(64, 64, "RGBA16_FLOAT")
    lst = device.command_list()
    lst.open()
    lst.copy_to_staging(staging, bake.m_targets["albedo"])
    lst.execute()
    albedo = np.frombuffer(device.read(staging), np.float16).reshape(64, 64, 4)
    assert albedo[..., 3].max() > 0  # the grey stand-in laid down over the node
    assert device.errors == 0, device.messages()
    pages.release()
    bake.release()


def test_a_bake_job_is_read_by_its_field_names():
    from unittest.mock import MagicMock, patch

    from annotoolbox.render import bakenative, gamedraw

    bake = bakenative.CNativeBake.__new__(bakenative.CNativeBake)
    bake.m_device, bake.m_list, bake.m_draw, bake.m_pool = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    bake.m_draw.pipeline_for.return_value = (object(), b"")
    bake.m_draw.write.return_value = {}
    bake.m_draw.sets.return_value = []
    bake.m_handover = FakeHandover()
    bake.m_maps, bake.m_targets = {}, {}
    bake.m_size_texels, bake.m_splat_records = 64, None
    bake.m_splats = bake.m_table = bake.m_camera = bake.m_framebuffer = bake.m_white = MagicMock()
    mesh = gamedraw.CMesh(b"\0" * 32, 16, (), np.arange(3, dtype="<u4"), 2)
    job = bake.model(mesh, {"cTrnBakedTexelSizeWS": 1.0}, {})
    with (
        patch.object(bakenative.gs, "CConstants", MagicMock()),
        patch.object(bakenative.CNativeBake, "_derive") as derive,
    ):
        bake._bake(job, 3)
    derive.assert_called_once_with(1.0, 3)
