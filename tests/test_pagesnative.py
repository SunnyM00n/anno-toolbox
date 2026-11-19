"""The pages and the bake on the native backend (``render.pagesnative``, ``bakenative``, ``nativecompute``): their
binding plans, the state handed over between QRhi and the module, the page queue they share with the current path;
with the module and an installed Anno 117 (``ANNO117_DIR``) both chains run on Windows' software rasteriser."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import pytest

from annotoolbox.infra.dxc import ConstantBuffer, Variable
from annotoolbox.render import gameshader as gs
from annotoolbox.render import nativecompute, nativegpu, nativelayout, terrainpages
from annotoolbox.render.terrainnodes import Node


@dataclass
class B:
    name: str
    kind: str
    register: int
    count: int = 1
    space: int = 0


@dataclass
class R:
    bindings: list
    cbuffers: list = field(default_factory=list)


def test_the_bakes_splat_maps_are_a_bindless_range_and_its_buffers_volatile():
    vs = R([B("cbTerrainBaking", "cbuffer", 0, space=1)])
    ps = R([B("cbPerFrame", "cbuffer", 0), B("cTerrainHeightTex", "texture", 17),
            B("cTrnBakeSplatTextures", "texture", 0, count=96, space=1), B("cbTerrainBaking", "cbuffer", 0, space=2),
            B("cSplatsBuffer", "structured", 6, space=2)])  # fmt: skip
    plan = nativelayout.BindingPlan.of([vs, ps], bindless=("cTrnBakeSplatTextures",), volatile=True)
    assert [(s.space, s.bindless) for s in plan.layouts] == [(0, False), (1, False), (1, True), (2, False)]
    assert plan.layouts[0].tuples() == [("texture", 17, 1), ("volatile", 0, 1)]
    assert plan.layouts[1].tuples() == [("volatile", 0, 1)] and plan.layouts[2].tuples() == [("texture", 0, 0)]
    assert plan.index(1, 0) == 2
    # without the switches the array stays a bounded one and the buffers static (the terrain's nodes)
    plain = nativelayout.BindingPlan.of([vs, ps])
    assert ("texture", 0, 96) in plain.layouts[1].tuples() and ("cbuffer", 0, 1) in plain.layouts[0].tuples()


def test_a_structured_buffer_of_records_is_written_by_path():
    members = [Variable("MatID", 0, 4, "uint", rows=1, columns=1),
               Variable("Repetition", 4, 4, "float", rows=1, columns=1)]  # fmt: skip
    element = ConstantBuffer("cSplatsBuffer", 32, [Variable("$Element", 0, 32, "TerrainBakeSplat", members=members)])
    c = gs.Constants(nativecompute.records(element, 32))
    assert len(c.data) == 32 * 32
    c.set("cSplatsBuffer[3].Repetition", 2.5)
    assert np.frombuffer(bytes(c.data[100:104]), np.float32)[0] == 2.5


def test_the_page_queue_lets_the_longest_unused_go_never_a_wanted_one():
    q = terrainpages.PageQueue(64.0, {})
    gone = []
    q._let_go = lambda ctx, pair: gone.append(pair)
    for i in range(terrainpages.KEEP + 3):
        q.ready[(0, i, 0)] = (i, i)
    q.wanted = {(0, 0, 0)}
    q._evict(None)
    assert len(q.ready) == terrainpages.KEEP and (0, 0, 0) in q.ready and gone == [(1, 1), (2, 2), (3, 3)]
    assert q.wanted == set()
    parts = terrainpages.PageParts(np.zeros((66, 66), np.uint16), [])
    q.want((0, 9, 9), parts)
    assert q.busy() and q.queue[(0, 9, 9)] is parts


class _Texture:
    def __init__(self, state: int) -> None:
        self.state, self.layout = state, None

    def setNativeLayout(self, layout: int) -> None:
        self.layout = layout


def test_the_states_qrhi_left_are_taken_back_ahead_of_the_modules_list_and_told_after(monkeypatch):
    read = _Texture(0x80)  # QRhi read it in a pixel shader last frame
    fresh = _Texture(nativecompute.SHADER_READ)
    monkeypatch.setattr(nativecompute, "pointer", id)
    module = SimpleNamespace(qrhi_texture=lambda p: (p + 1, read.state if p == id(read) else fresh.state))
    h = object.__new__(nativecompute.Handover)
    h.module, h.given, h._moved = module, {id(read): (read, "a"), id(fresh): (fresh, "b")}, []
    recorded = []
    lst = SimpleNamespace(transitions=recorded.append)
    h.take_back(lst, [read, fresh, read, None, _Texture(0x80)])  # one not given here is QRhi's own: left alone
    assert recorded == [[(id(read) + 1, 0x80, nativecompute.SHADER_READ)]]
    h.handed_back()
    assert read.layout == fresh.layout == nativecompute.SHADER_READ and h._moved == []


def test_a_splat_map_goes_up_as_stored_with_its_rows_pitch():
    from annotoolbox.render import bakenative

    info = SimpleNamespace(format="DXT1", faces=1, width=8, height=8, mips=1, srgb=True, depth=1)
    dds = SimpleNamespace(info=info, stored=lambda level: bytes(32))
    s = bakenative.Stored(dds)
    assert s.format == "BC1_UNORM_SRGB" and s.pitch(0) == 16 and s.levels[0] == bytes(32)
    missing = bakenative.Stored(bakenative.MissingMap(2))
    assert missing.format == "RGBA8_UNORM" and missing.pitch(0) == 16 and missing.levels[0][:4] == bytes([0, 0, 0, 255])


class _Handover:
    """The handover without QRhi: the module's textures as they are."""

    def give(self, tex, fmt, size, layers=0, flags=None):
        return tex

    def native(self, q):
        return q

    def take_back(self, lst, textures) -> None:
        pass

    def handed_back(self) -> None:
        pass

    def let_go(self, ctx, q) -> None:
        q.release()

    def drop(self, q) -> None:
        if q is not None:
            q.release()


def _game():
    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    if not nativegpu.available():
        pytest.skip(f"the native module: {nativegpu.reason()}")
    from annotoolbox.infra import dxc
    from annotoolbox.models import shadernative
    from annotoolbox.sources.anno117 import Anno117Files

    compiler = dxc.Dxc.find(anno117=root)
    return compiler, shadernative.NativeShaders(Anno117Files(root), compiler)


def test_the_shipped_page_chain_and_the_bake_run_on_the_software_rasteriser():
    from annotoolbox.models import shaderdefines
    from annotoolbox.render import bakenative, gamedraw, pagesnative, terrainbake, terraindraw

    compiler, natives = _game()
    device = nativegpu.own_device(adapter=-2)
    island = terraindraw.island_values(16.0)
    pages = pagesnative.NativePages(device, natives, _Handover(), 16.0, island)
    heights = np.full((33, 33), 2.0)
    pages.set_coarse(heights, 0.5)
    deltas = [(np.zeros((n, n), np.uint8), 0.01, 4) for n in (132, 264, 528)]
    pages.want((0, 0, 0), terrainpages.PageParts(np.full((66, 66), 30000, np.uint16), deltas))
    pages.run(None)
    assert not pages.error, pages.error
    height, normal = pages.ready[(0, 0, 0)]
    assert (height.format, height.width, normal.format, normal.width) == ("BC4_UNORM", 528, "RGBA8_UNORM", 512)

    done = compiler.compile(terrainbake.FLIP_HLSL, shaderdefines.compile_args(terrainbake.FLIP_ENTRY, "cs_6_2"))
    flip = gamedraw.Stage(done.object, terrainbake.FLIP_ENTRY, compiler.reflect(done.object))
    ground = terrainbake.GroundSource([terrainbake.Splat(0)], np.zeros((32, 32), np.uint32),
                                      np.full((64, 64), 31, np.uint32), 16.0)  # fmt: skip
    bake = bakenative.NativeBake(device, natives, _Handover(), ground, flip, slices=2, size=64)
    node = Node(4, 0.0, 0.0)
    mesh = terrainbake.bake_mesh(heights, 0.5, node, 33, terraindraw.vertex_normals(heights, 0.5))
    values = terrainbake.bake_values(node, ground, island, None, 64)
    bake.run(None, [(bake.model(mesh, values, {}), 1)])
    assert not bake.error, bake.error
    staging = device.staging(64, 64, "RGBA16_FLOAT")
    lst = device.command_list()
    lst.open()
    lst.copy_to_staging(staging, bake.targets["albedo"])
    lst.execute()
    albedo = np.frombuffer(device.read(staging), np.float16).reshape(64, 64, 4)
    assert albedo[..., 3].max() > 0  # the grey stand-in laid down over the node
    assert device.errors == 0, device.messages()
    pages.release()
    bake.release()
