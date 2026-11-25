"""The objects' shadow casters culled on the GPU (``render.shadowcullnative``): the cull objects a frame gathers for
``model_culler.fx``, the draws placed for it, the indirect record after the culling's dispatches, the shadow pass's
split between culled and CPU-culled casters, and the module's refusals of indirect draws it cannot record."""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest
from tests.render.renderfakes import native_module

from annotoolbox.render import gamedraw, gameinstances, nativedraws, nativegpu, objectsnative, shadowcullnative
from annotoolbox.render.instancestore import ALPHA, SPlaces


def _mesh(points) -> gamedraw.CMesh:
    vertices = np.asarray(points, np.float32)
    return gamedraw.CMesh(vertices.tobytes(), 12, ((0, 2, 3, 0),), np.arange(6, dtype=np.uint32), len(vertices))


def test_a_culled_draw_names_its_records_box_draw_and_where_its_words_start():
    frame = gameinstances.CFrameInstances(None)  # no device: only what it gathers
    places = SPlaces.of(np.repeat(np.identity(4, np.float32)[None], 5, axis=0))
    box = (np.array([-1.0, 0.0, -2.0]), np.array([1.0, 3.0, 2.0]))
    first_slot, first = frame.place_culled(places, None, 0, "mesh a", box, 36)
    again = frame.place_culled(places, None, 0, "mesh a", box, 36)
    second_slot, second = frame.place_culled(places, np.array([1, 3]), 2, "mesh b", box, 12)
    assert again == (first_slot, first) and (first, second) == (0, 1)  # a mesh of a level placed once a frame
    got = frame.gathered()
    rows = np.frombuffer(got["cull"], np.uint32).reshape(-1, gameinstances.CULL_WORDS)
    arguments = np.frombuffer(got["cull_args"], np.uint32).reshape(-1, gameinstances.CULL_ARGS_WORDS)
    assert len(rows) == 5 + 2 and arguments[:, 0].tolist() == [36, 12]
    assert (rows[:5, 0] == ALPHA + np.arange(5)).all() and (rows[:, 12] == 1).all()  # its record, one entity
    assert rows[5:, 7].tolist() == [1, 1] and rows[5:, 11].tolist() == [5 * 4] * 2  # its draw, its words' start
    assert rows[6, 2] - rows[5, 2] == 2 * 2  # a skin: its first bone two joints a drawn instance on
    assert rows[0, 4:7].view(np.float32).tolist() == [-1.0, 0.0, -2.0] and rows[0, 8:11].view(np.float32)[1] == 3.0
    slots = np.frombuffer(got["slots"], np.uint32).reshape(-1, gameinstances.SLOT // 4)
    assert slots[first_slot // gameinstances.SLOT, 0] == 0 and slots[second_slot // gameinstances.SLOT, 0] == 20
    assert frame.m_cull_outputs == 7 * gameinstances.CULL_CASCADES


def test_a_meshs_box_is_kept_once_its_bytes_are_gone_and_an_unknown_box_culls_on_the_cpu():
    mesh = _mesh([[0, 0, 0], [2, 1, -1], [1, 4, 3]])
    draws = nativedraws.CLevelDraws([("shadow", mesh)], [0])
    other = nativedraws.CLevelDraws([("shadow", _mesh([[0, 0, 0]]))], [0])
    mesh.box()
    mesh.m_data = b""  # on the device now (``CNativePart.mesh``)
    frame = gameinstances.CFrameInstances(None)
    drawn = SimpleNamespace(places=SPlaces.of(np.identity(4, np.float32)[None]), local=None)
    group = nativedraws.CCulledGroup.of("part", draws, drawn, frame)
    assert group is not None and group.m_args == [0] and len(group) == 1
    assert [corner.tolist() for corner in draws.culled()[0][2]] == [[0, 0, -1], [2, 4, 3]]
    other.m_pairs[0][1].m_data = b""  # gone before its box was known
    assert nativedraws.CCulledGroup.of("part", other, drawn, frame) is None


class FakeBuffer:
    def __init__(self, size: int, usage: str = "") -> None:
        self.size, self.usage, self.native = size, usage, id(self)

    def release(self) -> None:
        pass


class FakeDevice:
    def buffer(self, size, usage="", stride=0, name=""):
        return FakeBuffer(size, usage)


class FakeList:
    def __init__(self) -> None:
        self.m_written: list = []

    def write_buffer(self, buffer, data: bytes, offset: int = 0) -> None:
        self.m_written.append((buffer, len(data)))


def test_the_devices_instance_buffers_take_the_cull_objects_and_say_how_many():
    frame = gameinstances.CFrameInstances(None)
    places = SPlaces.of(np.repeat(np.identity(4, np.float32)[None], 3, axis=0))
    frame.place_culled(places, None, 0, "mesh", (np.zeros(3), np.ones(3)), 6)
    buffers, command_list = nativedraws.CInstanceBuffers(FakeDevice()), FakeList()
    buffers.send(command_list, frame)
    assert buffers.m_cull_counts == (3, 1, 3 * gameinstances.CULL_CASCADES)
    named = buffers.cull_named()
    assert (
        named["cInputObjects"] is buffers.m_buffers["cull"]
        and named["cInstanceDataBuffer"] is buffers.m_buffers["records"]
    )
    assert (buffers.m_buffers["cull"], 3 * 64) in command_list.m_written


class FakeModule:
    INDIRECT_STRIDE = 20

    def __init__(self) -> None:
        self.m_calls: list = []

    def transitions(self, list_ptr, items) -> None:
        self.m_calls.append(("transitions", items[0][1:]))

    def record_indirect(self, device, list_ptr, items, args, args_offsets, offsets, viewport) -> None:
        self.m_calls.append(("draws", list(args_offsets), list(offsets)))


def _cull(module: FakeModule, fail: bool = False) -> shadowcullnative.CShadowCull:
    cull = shadowcullnative.CShadowCull.__new__(shadowcullnative.CShadowCull)
    cull.m_device, cull.m_module, cull.m_args, cull.m_error = None, module, FakeBuffer(40, "indirect"), ""
    cull.m_messages, cull.m_foreign, cull._m_frame = [], [], ("named", "buffers", (2, 2, 8))

    def dispatch(list_ptr) -> None:
        if fail:
            raise RuntimeError("the culling did not bind")
        module.m_calls.append(("cull", list_ptr))

    cull._cull = dispatch
    return cull


def test_the_casters_are_drawn_by_their_arguments_after_the_culling_and_a_failed_culling_draws_none():
    module = FakeModule()
    cull = _cull(module)
    cull.record(7, ["a", "b"], [0, 1], [256, 512], (0.0, 0.0, 8.0, 8.0))
    indirect = (shadowcullnative.SHADER_READ, shadowcullnative.INDIRECT)
    assert module.m_calls == [("cull", 7), ("transitions", indirect), ("draws", [0, 20], [256, 512]),
                              ("transitions", indirect[::-1])]  # fmt: skip
    assert cull._m_frame is None  # once a frame: the next record asks for ``prepare`` again
    with pytest.raises(RuntimeError, match="not prepared"):
        cull.record(7, ["a"], [0], [0], (0.0, 0.0, 8.0, 8.0))
    module, failing = FakeModule(), _cull(FakeModule(), fail=True)
    failing.m_module = module
    failing.record(7, ["a"], [0], [0], (0.0, 0.0, 8.0, 8.0))
    assert module.m_calls == [] and "did not bind" in failing.m_error  # nothing drawn: its arguments are stale


def test_the_shadow_pass_records_the_culled_casters_apart_once_the_culling_is_prepared():
    recorded: list = []
    prepared: list = []
    cull = SimpleNamespace(prepare=lambda *args: prepared.append(args[3]) or True)
    objects = SimpleNamespace(m_cull=cull, m_error="", m_bridge=None,
                              _constant=lambda key, data, usage, stride: ("information", len(data)),
                              record=lambda *args: recorded.append((args[1], len(args[2]), args[-1])))  # fmt: skip
    culled = nativedraws.CCulledGroup("part", nativedraws.CLevelDraws([], []), [], [])
    plain = nativedraws.CNativeGroup("part", nativedraws.CLevelDraws([], []), [], 1)
    draws = objectsnative.SFrameDraws(objects, [], [culled, plain, culled], {}, None)
    cascades = objectsnative.gameshadow.fit(_camera(), (np.zeros(3), np.full(3, 10.0)), np.array([0.3, 1.0, 0.2]))
    draws.shadow("command buffer", _camera(), cascades, (None, False), 64)
    assert [(name, count) for name, count, _last in recorded] == [("shadow", 1), ("culled", 2)]
    assert prepared and prepared[0][0] == "information"
    objects.m_cull = None  # the culling let go: the culled groups are not drawn by stale arguments
    recorded.clear()
    draws.shadow("command buffer", _camera(), cascades, (None, False), 64)
    assert [name for name, _count, _last in recorded] == ["shadow"]


def _camera():
    from annotoolbox.render import gameshader

    view = gameshader.look_at(np.array([5.0, 20.0, -10.0]), np.array([5.0, 0.0, 5.0]))
    projection = gameshader.perspective(0.8, 1.5, 0.5, 200.0, is_reversed=True)
    return gameshader.SCamera(np.array([5.0, 20.0, -10.0]), view, projection, 0.5, 200.0, (64, 64), 200.0)


def test_the_module_refuses_indirect_draws_it_cannot_record():
    module = native_module()
    if not hasattr(module, "record_indirect"):
        pytest.skip("a module built before the indirect draws")
    gpu = nativegpu.own_device(adapter=-2)
    command_list = gpu.command_list()
    command_list.open()
    args, plain = gpu.buffer(40, "indirect,raw,uav"), gpu.buffer(40, "raw,uav")
    vertices, indices = gpu.buffer(64, "vertex"), gpu.buffer(64, "index")
    layout = gpu.layout([("rwraw", 0, 1)])
    stand_in = gpu.binding_set(layout, [("rwraw", 0, plain)])
    pipeline = _pipeline(gpu, layout)
    indexed = module.draw_item(pipeline, [stand_in], 3, [vertices], indices)
    unindexed = module.draw_item(pipeline, [stand_in], 3, [vertices])
    viewport = (0.0, 0.0, 4.0, 4.0)
    with pytest.raises(RuntimeError, match="indexed draws only"):
        module.record_indirect(gpu, command_list.native, [unindexed], args, [0], [0], viewport)
    with pytest.raises(RuntimeError, match="past the buffer's end"):
        module.record_indirect(gpu, command_list.native, [indexed], args, [24], [0], viewport)
    with pytest.raises(RuntimeError, match="past the buffer's end"):  # its end wraps past 2^64: still past
        module.record_indirect(gpu, command_list.native, [indexed], args, [(1 << 64) - 4], [0], viewport)
    with pytest.raises(RuntimeError, match="not an indirect buffer"):
        module.record_indirect(gpu, command_list.native, [indexed], plain, [0], [0], viewport)
    with pytest.raises(RuntimeError, match="one argument offset"):
        module.record_indirect(gpu, command_list.native, [indexed], args, [], [0], viewport)
    command_list.close()


def _pipeline(gpu, layout):
    """A compute pipeline standing in for a draw's (the refusals come before anything is bound)."""
    from annotoolbox.infra import dxc

    try:
        compiler = dxc.CDxc.find(os.environ.get("ANNO117_DIR"))
    except dxc.CDxcMissing as exc:
        pytest.skip(f"no shader compiler: {exc}")
    done = compiler.compile("RWByteAddressBuffer o : register(u0); [numthreads(1,1,1)] void main() { o.Store(0, 1); }",
                            ["-T", "cs_6_2", "-E", "main"])  # fmt: skip
    if not done.ok:
        pytest.skip(f"the compiler: {done.messages[:200]}")
    return gpu.compute_pipeline(gpu.shader("cs", done.object, "main"), [layout])
