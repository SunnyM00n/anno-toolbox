"""The native backend's Direct3D 12 layer (render/nativelayout, nativegpu; native/d3d12): the binding plan from a
pass's reflections, the vertex input from a shader's signature, the module's loading, and with the module built the
layer itself on Windows' software rasteriser and the game's shipped tone map drawn on both routes of step 0."""

from __future__ import annotations

import os
import struct
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from tests.render.renderfakes import FakeBinding, FakeReflection, native_module

from annotoolbox.infra.dxc import SConstantBuffer
from annotoolbox.render import nativegpu, nativelayout


def test_the_plan_lays_a_pass_out_by_space_with_its_bindless_ranges_and_root_constants():
    vs = FakeReflection([FakeBinding("cbPerFrame", "cbuffer", 0), FakeBinding("cModelInstanceDataBuffer", "structured", 55),
            FakeBinding("cbModel", "cbuffer", 1, space=1024), FakeBinding("cbDrawCallIndex", "cbuffer", 2, space=1024)],
           [SConstantBuffer("cbModel", 192), SConstantBuffer("cbDrawCallIndex", 16)])  # fmt: skip
    ps = FakeReflection([FakeBinding("cbPerFrame", "cbuffer", 0), FakeBinding("cBlueNoiseTex", "texture", 14), FakeBinding("cLinearClampCmpSampler", "sampler", 11),
            FakeBinding("cTextureAtlas", "texture", 0, space=1), FakeBinding("cTextureFeedback", "uav_rwbyteaddress", 2, space=1),
            FakeBinding("cBindlessTextures", "texture", 3, count=0, space=1)])  # fmt: skip
    plan = nativelayout.SBindingPlan.of([vs, ps])
    assert [(s.space, s.bindless) for s in plan.layouts] == [(0, False), (1, False), (1, True), (1024, False)]
    assert plan.layouts[0].tuples() == [
        ("cbuffer", 0, 1),
        ("sampler", 11, 1),
        ("structured", 55, 1),
        ("texture", 14, 1),
    ]
    assert plan.layouts[1].tuples() == [("rwraw", 2, 1), ("texture", 0, 1)]
    assert plan.layouts[2].tuples() == [("texture", 3, 0)]  # the unbounded range: a table of its own
    # the draw's index becomes the push constants; the model's 192 bytes stay a root constant buffer
    assert plan.push.name == "cbDrawCallIndex" and plan.layouts[3].tuples() == [("volatile", 1, 1), ("push", 2, 16)]
    assert plan.samplers == {(0, 11): "cLinearClampCmpSampler"}
    assert plan.index(1, 3) == 2 and plan.index(1024) == 3


def test_too_many_layouts_are_refused():
    many = FakeReflection([FakeBinding(f"t{s}", "texture", 0, space=s) for s in range(9)])
    with pytest.raises(ValueError, match="binding layouts"):
        nativelayout.SBindingPlan.of([many])


def test_the_engines_samplers_follow_their_names():
    cmp = nativelayout.sampler_state("cLinearClampCmpSampler")
    assert cmp["compare"] == "greater_equal" and cmp["address"] == "clamp" and cmp["filter"] == "linear"
    assert nativelayout.sampler_state("cPointWrapSampler")["filter"] == "point_mip_linear"
    assert nativelayout.sampler_state("cAnisoWrapSampler")["filter"] == "aniso"


def _container(elements: list[tuple[str, int, int, int, int]]) -> bytes:
    """A DXBC container with an ``ISG1`` part of (semantic, index, register, component type, mask)."""
    head, names = bytearray(struct.pack("<II", len(elements), 8)), bytearray()
    table = 8 + 32 * len(elements)
    rows = bytearray()
    for name, index, reg, comp, mask in elements:
        rows += struct.pack("<6IB3x", 0, table + len(names), index, 0, comp, reg, mask)
        rows += bytes(4)  # the row's minimum precision
        names += name.encode() + b"\0"
    part = bytes(head + rows + names)
    body = b"ISG1" + struct.pack("<I", len(part)) + part
    return b"DXBC" + bytes(16) + struct.pack("<III", 1, 36 + len(body), 1) + struct.pack("<I", 36) + body


def test_the_vertex_input_is_read_from_the_shaders_signature_and_laid_over_the_mesh():
    inputs = nativelayout.signature(_container([("POSITION", 0, 0, 3, 7), ("TEXCOORD", 1, 1, 3, 3),
                                                ("COLOR", 0, 2, 3, 15), ("SV_VertexID", 0, 3, 1, 1)]))  # fmt: skip
    assert [(i.semantic, i.index, i.width) for i in inputs] == [("POSITION", 0, 3), ("TEXCOORD", 1, 2), ("COLOR", 0, 4),
                                                                 ("SV_VertexID", 0, 1)]  # fmt: skip
    # the mesh: a half-float position, two texture coordinates; no colour
    mesh = [(0, 6, 4, 0), (4, 6, 2, 8), (4, 6, 2, 12)]
    elements, fill = nativelayout.input_elements(mesh, inputs, 16)
    assert elements[0] == ("POSITION", 0, "RGBA16_FLOAT", 0, 0, 16, False)
    assert elements[1] == ("TEXCOORD", 1, "RG16_FLOAT", 0, 12, 16, False)  # the second one, by its index
    assert elements[2] == ("COLOR", 0, "RGBA8_UNORM", nativelayout.FILL_SLOT, 0, 0, False) and fill == bytes([255] * 4)
    floats, stride = nativelayout.float_elements(inputs)
    assert stride == 36 and floats[1] == ("TEXCOORD", 1, "RG32_FLOAT", 0, 12, 36, False)


def test_without_the_module_the_view_keeps_its_own_look(monkeypatch):
    monkeypatch.setattr(nativegpu, "_g_state", {})
    monkeypatch.setenv(nativegpu.ENV, "0")
    assert nativegpu.load() is None and "switched off" in nativegpu.reason()


def test_a_view_that_lets_its_device_go_lets_the_native_hold_on_it_go(monkeypatch):
    """``CRhiView.releaseResources`` detaches NVRHI from a Direct3D 12 device (it would keep the device alive after
    QRhi's goes, with everything released late on it), its pipeline library written first; another backend's
    handles are not read."""
    pytest.importorskip("PySide6")
    from annotoolbox.render import rhiview

    waited = []
    gpu = type("G", (), {"wait_idle": lambda self: waited.append(True), "adapter_id": "1002-73bf-00000000-c1",
                         "pipeline_stats": {"changed": True}, "pipeline_library": lambda self: b"NGPL"})()  # fmt: skip
    monkeypatch.setattr(nativegpu, "_g_state", {"attached": {7: gpu}, "samplers": {(id(gpu), "linear"): object()}})
    monkeypatch.setattr(nativegpu, "qrhi_handles", lambda rhi: (7, 8))
    released = []
    for backend in ("Vulkan", "D3D12"):
        rhi = type("Rhi", (), {"backendName": lambda self, b=backend: b})()
        ctx = type("Ctx", (), {"m_rhi": rhi, "release": lambda self: released.append(True)})()
        view = SimpleNamespace(m_failed="", _m_device=None, m_ctx=ctx, released=lambda ctx: None)
        rhiview.CRhiView.releaseResources(view)
        assert bool(waited) == (backend == "D3D12") and len(released) == 1 + (backend == "D3D12")
    assert nativegpu._g_state["attached"] == {} and nativegpu._g_state["samplers"] == {}
    assert nativegpu.library_path(gpu).read_bytes() == b"NGPL"  # what the device made, for the next start


def test_the_layer_runs_on_the_software_rasteriser():
    module = native_module()
    gpu = nativegpu.own_device(adapter=-2)
    assert gpu.shader_model >= 0x62 and gpu.binding_tier >= 2
    target = gpu.texture(8, 4, "RGBA8_UNORM", usage="target")
    sampler = gpu.sampler(compare="greater_equal")
    layout = gpu.layout([("texture", 0, 1), ("texture", 3, 1), ("sampler", 11, 1), ("cbuffer", 0, 1)])
    cb = gpu.buffer(256, "constant")
    s = gpu.binding_set(layout, [("texture", 0, target), ("texture", 3, None), ("sampler", 11, sampler),
                                 ("cbuffer", 0, cb)])  # fmt: skip
    assert s is not None
    staging = gpu.staging(8, 4, "RGBA8_UNORM")
    cl = gpu.command_list()
    cl.open()
    cl.write_buffer(cb, bytes(range(16)))
    cl.clear_color(target, 0.0, 1.0, 0.0, 1.0)
    cl.copy_to_staging(staging, target)
    cl.execute()
    px = gpu.read(staging)
    assert len(px) == 8 * 4 * 4 and px[:4] == bytes([0, 255, 0, 255])
    assert gpu.errors == 0, gpu.messages()
    with pytest.raises(RuntimeError, match="unknown format"):
        gpu.texture(4, 4, "NOT_A_FORMAT")
    assert "BC7_UNORM_SRGB" in module.formats()


def test_a_foreign_list_refuses_a_released_pipeline_and_a_copy_past_a_buffers_end():
    """What the native steps record on QRhi's list raises instead of reading freed or foreign memory: a kept draw
    whose pipeline was let go, a copy past either buffer's end."""
    module = native_module()
    gpu = nativegpu.own_device(adapter=-2)
    if not hasattr(module.CommandList, "native"):
        pytest.skip("a module built before the foreign list's checks")
    small, large = gpu.buffer(256, "structured", stride=16), gpu.buffer(1024, "structured", stride=16)
    cl = gpu.command_list()
    cl.open()
    with pytest.raises(RuntimeError, match="past a buffer's end"):
        module.copy_buffer(cl.native, small.native, 0, large.native, 0, 512)
    module.copy_buffer(cl.native, large.native, 0, small.native, 0, 256)
    cs = gpu.shader("cs", _compute_dxil(), "main")
    layout = gpu.layout([("rwstructured", 0, 1)])
    pipe = gpu.compute_pipeline(cs, [layout])
    s = gpu.binding_set(layout, [("rwstructured", 0, gpu.buffer(64, "structured,uav", stride=16))])
    vertices = gpu.buffer(64, "vertex")
    item = module.draw_item(pipe, [s], 3, [vertices])
    pipe.release()
    with pytest.raises(RuntimeError, match="released pipeline"):
        module.record_draws(gpu, cl.native, [item], [1], [0], (0.0, 0.0, 4.0, 4.0))
    cl.close()


def _compute_dxil() -> bytes:
    from annotoolbox.infra import dxc

    try:
        compiler = dxc.CDxc.find(os.environ.get("ANNO117_DIR"))
    except dxc.CDxcMissing as exc:
        pytest.skip(f"no shader compiler: {exc}")
    hlsl = "RWStructuredBuffer<float4> o : register(u0); [numthreads(1,1,1)] void main() { o[0] = 1; }"
    done = compiler.compile(hlsl, ["-T", "cs_6_2", "-E", "main"])
    if not done.ok:
        pytest.skip(f"the compiler: {done.messages[:200]}")
    return done.object


def test_the_kept_sets_and_buffers_hold_what_they_were_made_with():
    """``CSetCache``: a set per layout and resources, the least used let go past its size; ``CKeptBuffers``: a buffer
    written only when its bytes change, made again when they outgrow it, let go when unused."""
    released, written = [], []

    class FakeRes:
        def __init__(self, size=0):
            self.size = size

        def release(self):
            released.append(self)

    class FakeDev:
        def buffer(self, size, usage, stride=0, name=""):
            return FakeRes(size)

    class FakeLst:
        def write_buffer(self, buf, data, offset=0):
            written.append((buf, data))

    sets = nativegpu.CSetCache(2)
    layout, a, b, c = object(), object(), object(), object()
    first = sets.get(layout, (a,), FakeRes)
    assert sets.get(layout, (a,), FakeRes) is first and len(sets) == 1
    sets.get(layout, (b,), FakeRes)
    sets.get(layout, (a,), FakeRes)  # used again: the newest
    sets.get(layout, (c,), FakeRes)
    assert len(sets) == 2 and released and released[-1] is not first  # (b,) went, (a,) stays
    kept, lst = nativegpu.CKeptBuffers(FakeDev()), FakeLst()
    one = kept.get(lst, "k", bytes(16), frame=1)
    assert one.size == 256 and kept.get(lst, "k", bytes(16), frame=2) is one and len(written) == 1
    two = kept.get(lst, "k", bytes(300), frame=3)
    assert two is not one and two.size == 512 and one in released and len(written) == 2
    assert kept.let_go(before=3) == [] and two not in released
    assert kept.let_go(before=4) == [two] and two in released and not kept.m_held


def test_a_set_kept_with_a_resource_its_owner_let_go_goes_with_it():
    """``CSetCache.let_go``: the sets made with a resource let go (a bridge's wrap unused for some frames, a kept
    buffer) are let go at once. Kept, they were never asked for again (the next wrap has another id) and held their
    sampler descriptors until the device's 2048 were used up and the island view crashed in a binding set."""
    released = []

    class FakeSet:
        def release(self):
            released.append(self)

    sets = nativegpu.CSetCache(512)
    layout, wrap, other = object(), object(), object()
    with_wrap, without = sets.get(layout, (wrap, None), FakeSet), sets.get(layout, (other, None), FakeSet)
    sets.let_go([])
    assert released == [] and len(sets) == 2
    sets.let_go([wrap])
    assert released == [with_wrap] and len(sets) == 1 and sets.get(layout, (other, None), FakeSet) is without
    keys = []
    sets.get(layout, (object(),), FakeSet, keys)
    assert not sets.touch([*keys, (id(layout), id(wrap), id(None))])  # a kept list naming the gone set makes anew


TONEMAP = """
import json, os, sys
import numpy as np
sys.path.insert(0, "scripts")
from PySide6.QtWidgets import QApplication
app = QApplication([])
import native_probe as probe
shipped = probe.shipped_tonemap(os.environ["ANNO117_DIR"])
out = {}
View = probe._view_class()
for mode in ("a", "b"):
    view = View(shipped, mode)
    img = None
    for _ in range(3):
        img = view.picture(160, 90)
    px = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(0, 160, 8)] for y in range(0, 90, 6)], float)
    out[mode] = {"std": float(px.std()), "corner": list(px[-1, 0]), "failed": view.m_failed}
print(json.dumps(out))
"""


def test_the_games_shipped_tone_map_draws_on_both_routes_of_step_zero():
    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    native_module()
    from tests.render.renderfakes import render_backends, run_child

    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(TONEMAP)
    for mode in ("a", "b"):
        assert not got[mode]["failed"] and got[mode]["std"] > 30, got  # the tone-mapped gradient, not a clear
    # route A drew into QRhi's target: the same picture as route B's copy through the CPU
    assert all(abs(x - y) < 8 for x, y in zip(got["a"]["corner"], got["b"]["corner"], strict=True)), got


def test_released_resources_and_bad_arguments_raise_instead_of_crashing_the_process():
    """A resource let go and then used, an adapter that does not exist and a framebuffer without targets are errors
    Python can catch, not access violations."""
    module = native_module()
    if not hasattr(module.Buffer, "native"):
        pytest.skip("a module built before the released-resource checks")
    gpu = nativegpu.own_device(adapter=-2)
    buf, tex = gpu.buffer(64, "structured", stride=16), gpu.texture(4, 4, "RGBA8_UNORM", usage="target")
    cl = gpu.command_list()
    cl.open()
    buf.release()
    tex.release()
    with pytest.raises(RuntimeError, match="released"):
        _ = buf.native
    with pytest.raises(RuntimeError, match="released"):
        _ = buf.gpu_address
    with pytest.raises(RuntimeError, match="released"):
        _ = tex.native
    with pytest.raises(RuntimeError, match="released"):
        cl.write_buffer(buf, bytes(4))
    with pytest.raises(RuntimeError, match="released"):
        gpu.framebuffer([tex])
    layout = gpu.layout([("texture", 0, 1), ("structured", 1, 1), ("cbuffer", 0, 1), ("sampler", 0, 1)])
    sampler, cb = gpu.sampler(), gpu.buffer(256, "constant")
    rest = [("cbuffer", 0, cb), ("sampler", 0, sampler)]
    for texture, structured in ((tex, None), (None, buf)):  # NVRHI would write the view through a null handle
        with pytest.raises(RuntimeError, match="released resource"):
            gpu.binding_set(layout, [("texture", 0, texture), ("structured", 1, structured), *rest])
    assert gpu.binding_set(layout, [("texture", 0, None), ("structured", 1, None), *rest]) is not None
    cb.release()
    with pytest.raises(RuntimeError, match="released resource"):
        gpu.binding_set(layout, [("texture", 0, None), ("structured", 1, None), *rest])
    layout.release()
    with pytest.raises(RuntimeError, match="released layout"):
        gpu.binding_set(layout, [])
    with pytest.raises(RuntimeError, match="needs a colour or a depth"):
        gpu.framebuffer([])
    with pytest.raises(RuntimeError, match="unknown address mode"):
        gpu.sampler(address="bogus")
    cl.close()
    with pytest.raises(RuntimeError, match="no adapter"):
        module.Device.create(False, 99)


def test_a_binding_set_the_full_sampler_heap_cannot_hold_raises_and_one_fits_again_once_sets_are_let_go():
    """The shader-visible sampler heap holds 2048 descriptors at most: a set past them is refused (NVRHI wrote its
    samplers through an invalid index and the driver crashed the island view), and the descriptors of sets let go
    come back after the frames that may still read them."""
    native_module()
    gpu = nativegpu.own_device(adapter=-2)
    sampler = gpu.sampler()
    layout = gpu.layout([("sampler", slot, 1) for slot in range(16)])
    items = [("sampler", slot, sampler) for slot in range(16)]
    made = []
    with pytest.raises(RuntimeError, match="sampler descriptor heap is full"):
        for _ in range(2048 // 16 + 1):
            made.append(gpu.binding_set(layout, items))
    assert 0 < len(made) <= 2048 // 16
    for kept in made:
        kept.release()
    for _ in range(gpu.keep_frames + 1):
        gpu.end_frame()
    assert gpu.binding_set(layout, items) is not None


class FakePoolDevice:
    """A device of ``CBufferPool``: its buffers by number, the frames ``end_frame`` counted."""

    keep_frames = 3

    def __init__(self) -> None:
        self.frame, self.made, self.released = 0, [], []

    def buffer(self, size, usage, stride=0, name=""):
        made = SimpleNamespace(size=size, usage=usage, release=lambda: self.released.append(made))
        self.made.append(made)
        return made


class FakeWrites:
    def __init__(self) -> None:
        self.writes = []

    def write_buffer(self, buffer, data, offset=0):
        self.writes.append((buffer, len(data), offset))


def test_a_pool_places_pieces_in_one_buffer_and_reuses_a_freed_one_after_the_frames_in_flight():
    """``CBufferPool``: pieces 256-byte aligned in one block, a freed piece reused only once the frames that may still
    read it are done, joined to its free neighbours; a piece over a quarter of a block gets a buffer of its own,
    let go late."""
    device, writes = FakePoolDevice(), FakeWrites()
    pool = nativegpu.CBufferPool(device, "vertex,index", "meshes", block_bytes=4096)
    a, b, c = (pool.place(writes, bytes(size)) for size in (100, 300, 16))
    assert len(device.made) == 1 and [a.offset, b.offset, c.offset] == [0, 256, 768]
    assert writes.writes == [(device.made[0], 100, 0), (device.made[0], 300, 256), (device.made[0], 16, 768)]
    pool.free(a)
    pool.free(b)
    assert pool.place(writes, bytes(16)).offset == 1024  # freed this frame: a frame in flight may read them
    device.frame = 4
    reused = pool.place(writes, bytes(500))
    assert reused.offset == 0 and reused.buffer is device.made[0]  # both freed pieces joined into one range
    large = pool.place(writes, bytes(2000))
    assert large.offset == 0 and large.buffer is device.made[1] and large.block < 0
    pool.free(large)
    assert device.released == [device.made[1]]
    for _ in range(4):
        pool.place(writes, bytes(1024))  # the block is full: a second one
    assert len(device.made) == 3
    pool.release()
    assert device.made[0] in device.released and device.made[2] in device.released


def test_a_piece_given_back_after_its_pool_was_released_is_not_handed_out_again():
    """An owner that lets its pieces go after the pool's own release (the distance clusters after their objects):
    their ranges named blocks that are gone, and a block made since would have handed them out a second time."""
    device, writes = FakePoolDevice(), FakeWrites()
    pool = nativegpu.CBufferPool(device, "vertex,index", "meshes", block_bytes=4096)
    old, large = pool.place(writes, bytes(256)), pool.place(writes, bytes(2000))
    pool.release()
    kept = pool.place(writes, bytes(256))  # a block made after the release: its first range
    pool.free(old)
    pool.free(large)  # its buffer released with the pool: not a second time
    device.frame = 10
    assert pool.place(writes, bytes(256)).offset != kept.offset
    assert device.released.count(device.made[1]) == 1


def test_a_pooled_constant_piece_is_bound_by_its_offset_and_size():
    """``binding_set`` hands the module a pool's piece of a constant buffer as its buffer with its offset and size."""
    piece = nativegpu.SPlaced("buffer", 512, 0, 256)
    spec = nativelayout.SLayoutSpec(2, [nativelayout.SItem("cbuffer", 0, 1, "cbTerrainNode")])
    got = []
    device = SimpleNamespace(binding_set=lambda layout, items: got.append(items))
    nativegpu.binding_set(device, "layout", spec, {"cbTerrainNode": piece}, {})
    assert got == [[("cbuffer", 0, "buffer", {"offset": 512, "range": 256})]]


class FakeLibraryDevice:
    """A device's pipeline library as the module keeps it: its bytes, what was opened, whether it changed."""

    adapter_id = "10de-2702-00000000-a1"

    def __init__(self, changed: bool = True, pending: int = 0) -> None:
        self.opened, self.changed, self.pending = [], changed, pending

    @property
    def pipeline_stats(self):
        return {"changed": self.changed, "pending": self.pending}

    def pipeline_library(self):
        self.changed = False
        return b"NGPL library"

    def open_pipeline_library(self, data):
        self.opened.append(data)
        return True


def test_the_pipeline_library_is_kept_per_adapter_and_written_once_the_workers_rest(monkeypatch):
    """The library's file is one per adapter in the cache; it is written only when pipelines were stored since and no
    worker has been busy, at most every ``SAVE_AFTER_S``; a later device opens it from there."""
    monkeypatch.setattr(nativegpu, "_g_state", {})
    device = FakeLibraryDevice()
    assert not nativegpu.open_pipelines(device)  # none yet
    assert nativegpu.library_path(device).parent.name == nativegpu.PIPELINES_DIR
    busy = FakeLibraryDevice(pending=2)
    nativegpu.keep_pipelines(busy, 100.0)
    assert busy.changed and not nativegpu.library_path(busy).exists()  # its workers still make pipelines
    nativegpu.keep_pipelines(device, 100.0)
    for thread in threading.enumerate():
        if thread.name == "pipeline-library":
            thread.join(10)
    assert nativegpu.library_path(device).read_bytes() == b"NGPL library" and not device.changed
    device.changed = True
    nativegpu.keep_pipelines(device, 100.0 + nativegpu.SAVE_AFTER_S / 2)
    assert device.changed  # not again so soon
    later = FakeLibraryDevice(changed=False)
    assert nativegpu.open_pipelines(later) and later.opened == [b"NGPL library"]
    assert not nativegpu.save_pipelines(later)  # nothing stored since: the file stays as it is


def test_a_pipeline_made_in_the_background_is_drawn_once_in_and_a_later_start_loads_it_from_the_library():
    """A pipeline asked for in the background is ready once a worker made it and is stored in the device's library;
    the library's bytes open on a new device (not those of another file format or driver: it starts empty), whose
    same pipeline is then loaded, not made."""
    native_module()
    gpu = nativegpu.own_device(adapter=-2)
    cs = gpu.shader("cs", _compute_dxil(), "main")
    layout = gpu.layout([("rwstructured", 0, 1)])
    pipe = gpu.compute_pipeline(cs, [layout], background=True)
    for _ in range(5000):
        if pipe.ready or pipe.error:
            break
        time.sleep(0.002)
    assert pipe.ready and not pipe.error
    stats = gpu.pipeline_stats
    assert stats["made"] == 1 and stats["stored"] == 1 and stats["changed"] and not stats["pending"]
    data = gpu.pipeline_library()
    assert data[:4] == b"NGPL" and not gpu.pipeline_stats["changed"]
    again = nativegpu.own_device(adapter=-2)
    assert not again.open_pipeline_library(data[:4] + b"\x02" + data[5:])  # another format of the file
    assert not again.open_pipeline_library(data.replace(gpu.driver_version.encode(), b"0.0.0.0"))  # another driver
    assert again.open_pipeline_library(data) and not again.open_pipeline_library(data)  # once, before any pipeline
    made = again.compute_pipeline(again.shader("cs", _compute_dxil(), "main"), [again.layout([("rwstructured", 0, 1)])])
    stats = again.pipeline_stats
    assert made.ready and stats["loaded"] == 1 and stats["made"] == 0 and stats["opened"]
