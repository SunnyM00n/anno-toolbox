"""A model's constant buffers drawn by 117's own shaders (``render.gamedraw.CGameModel``): each buffer filled again
only when an input it reads changed (the camera, the place, the pose, the pass's fill, the owner's values, its raw
bytes), and the members the fills write are those the buffers' layouts are tested against."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")
from annotoolbox.infra.dxc import SConstantBuffer, SVariable
from annotoolbox.render import gamedraw, gameframe, gamepool, gameshadow
from annotoolbox.render import gameshader as gs


def _camera_data() -> SVariable:
    members = [SVariable("MtxView", 0, 0, "float4x4", 4, 4), SVariable("CamPosition", 64, 0, "float4", 1, 4)]
    return SVariable("cCameraBuffer", 0, 80 * gs.CAMERAS, "CameraData", 0, 0, gs.CAMERAS, members=members)


def _layouts() -> dict[str, SConstantBuffer]:
    """A buffer per input: the camera's, the place's, the shadow pass's, the owner's values, the raw bytes', and
    one that reads none of them (the material's)."""
    variables = {
        "cbFrame": [_camera_data(), SVariable("cRenderBufferSize", 240, 8, "float2", 1, 2)],
        "cbInstance": [SVariable("cCurrBoneMatrixBuffer", 0, 48 * 4, "float4x3", 4, 3, 4)],
        "cbShadow": [
            SVariable("cUsedShadowPartitions", 0, 4, "uint", 1, 1),
            SVariable("cShadowMaxDistance", 4, 4, "float", 1, 1),
            SVariable("cLgtDirection", 16, 16, "float4", 1, 4),
        ],  # fmt: skip
        "cbLook": [SVariable("cLook", 0, 16, "float4", 1, 4)],
        "cbRaw": [SVariable("cRows", 0, 64, "float4", 1, 4, 4)],
        "cbMaterial": [SVariable("cTint", 0, 16, "float4", 1, 4)],
    }
    return {
        name: SConstantBuffer(name, max(16, sum(v.size for v in found)), found) for name, found in variables.items()
    }


class FakeBatch:
    """A resource update batch that keeps the dynamic buffer writes."""

    def __init__(self) -> None:
        self.m_updates: list = []

    def updateDynamicBuffer(self, buffer, offset, data) -> None:
        self.m_updates.append(buffer)


def _model() -> gamedraw.CGameModel:
    """A model whose resources stand made: its constant buffers by ``_layouts``, a buffer stand-in each."""
    program = SimpleNamespace(m_cbuffers=_layouts(), m_pipelines={})
    model = gamedraw.CGameModel(None, program, None, gamedraw.SMaterial({}, {}))
    model.m_keep, model.m_pipeline, model._m_uploaded = [object()], object(), None
    model.m_constants = {name: gs.CConstants(layout) for name, layout in program.m_cbuffers.items()}
    model.m_buffers = {name: name for name in model.m_constants}
    return model


def _camera(eye=(0.0, 5.0, 10.0)) -> gs.SCamera:
    return gs.direct_camera((64, 32), eye, (0.0, 0.0, 0.0), 1.0, 0.5, 100.0)


def _filled(model: gamedraw.CGameModel, camera: gs.SCamera, fill=None) -> set[str]:
    """The buffers ``prepare`` filled (``fill_instance`` runs once per buffer filled)."""
    seen: list = []
    instance = gs.fill_instance
    gs.fill_instance = lambda constants, *args, **kwargs: (seen.append(constants), instance(constants, *args, **kwargs))
    try:
        model.prepare(None, (1, 1), FakeBatch(), camera, fill)
    finally:
        gs.fill_instance = instance
    return {name for name, constants in model.m_constants.items() if any(c is constants for c in seen)}


def _shadow_fill(cascades: gameshadow.SCascades):
    """The shadow pass's fill as ``CShadowMap.render`` keys it."""

    def fill(constants: gs.CConstants) -> None:
        gameshadow.fill_casters(constants, cascades)

    fill.key, fill.names = (gameshadow.cascade_key(cascades), 1, 0, None, None), gameshadow.CASTER_NAMES
    return fill


def _cascades(sun=(0.3, 1.0, 0.2)) -> gameshadow.SCascades:
    return gameshadow.fit(_camera(), ((-2.0, 0.0, -2.0), (2.0, 3.0, 2.0)), sun)


def test_a_buffer_is_filled_once_and_again_only_when_an_input_it_reads_changed():
    model = _model()
    all_buffers = set(model.m_constants)
    assert _filled(model, _camera()) == all_buffers  # the first frame fills every buffer
    assert _filled(model, _camera()) == set()  # a still camera, made anew each frame: nothing
    assert _filled(model, _camera(eye=(1.0, 5.0, 10.0))) == {"cbFrame"}
    model.m_world = np.diag([2.0, 1.0, 1.0, 1.0])
    assert _filled(model, _camera(eye=(1.0, 5.0, 10.0))) == {"cbInstance"}
    model.m_palette = np.stack([np.identity(4), np.identity(4)])
    assert _filled(model, _camera(eye=(1.0, 5.0, 10.0))) == {"cbInstance"}  # a skin's pose
    model.m_palette = model.m_palette.copy()
    model.m_palette[1, 0, 3] = 1.0
    assert _filled(model, _camera(eye=(1.0, 5.0, 10.0))) == {"cbInstance"}  # the pose changed in place
    model.m_values["cLook"] = [1.0, 2.0, 3.0, 4.0]
    assert _filled(model, _camera(eye=(1.0, 5.0, 10.0))) == {"cbLook"}
    model.m_values["cLook"][0] = 5.0  # changed in place
    assert _filled(model, _camera(eye=(1.0, 5.0, 10.0))) == {"cbLook"}
    model.m_raw["cbRaw"] = bytes(range(64))
    assert _filled(model, _camera(eye=(1.0, 5.0, 10.0))) == {"cbRaw"}
    assert _filled(model, _camera(eye=(1.0, 5.0, 10.0))) == set()


def test_the_shadow_pass_refills_the_buffers_its_fill_writes_when_the_cascades_or_the_light_change():
    model = _model()
    camera = _camera()
    assert _filled(model, camera, _shadow_fill(_cascades())) == set(model.m_constants)
    assert _filled(model, camera, _shadow_fill(_cascades())) == set()  # the same cascades, a new fill
    assert _filled(model, camera, _shadow_fill(_cascades(sun=(-0.4, 1.0, 0.2)))) == {"cbShadow"}  # the light
    moved = gameshadow.fit(_camera(eye=(3.0, 5.0, 9.0)), ((-2.0, 0.0, -2.0), (2.0, 5.0, 2.0)), (-0.4, 1.0, 0.2))
    assert _filled(model, camera, _shadow_fill(moved)) == {"cbShadow"}
    used = gameshadow.SCascades(moved.cascades, moved.light, moved.max_distance_m, used=2)
    assert _filled(model, camera, _shadow_fill(used)) == {"cbShadow"}  # the quality's cascades
    assert np.frombuffer(bytes(model.m_constants["cbShadow"].m_data[:4]), np.uint32)[0] == 2


def test_another_pass_a_fill_without_a_key_and_a_model_made_again_fill_every_buffer():
    model = _model()
    camera = _camera()
    _filled(model, camera)

    def unkeyed(constants: gs.CConstants) -> None:
        constants.set("cTint", [1.0, 0.0, 0.0, 1.0])

    assert _filled(model, camera, unkeyed) == set(model.m_constants)  # what it reads is not known: every time
    assert _filled(model, camera, unkeyed) == set(model.m_constants)

    def keyed(constants: gs.CConstants) -> None:
        constants.set("cTint", [0.0, 1.0, 0.0, 1.0])

    keyed.key = ("a pass",)  # no names: it may write any member
    assert _filled(model, camera, keyed) == set(model.m_constants)
    assert _filled(model, camera, keyed) == set()
    keyed.key = ("another pass",)
    assert _filled(model, camera, keyed) == set(model.m_constants)
    assert np.frombuffer(bytes(model.m_constants["cbMaterial"].m_data[:16]), np.float32).tolist() == [0, 1, 0, 1]
    model.m_constants = {name: gs.CConstants(c.m_reflection) for name, c in model.m_constants.items()}
    assert _filled(model, camera, keyed) == set(model.m_constants)  # made again (``_resources``): new buffers


def _written(write, layout: SConstantBuffer) -> set[str]:
    """The top-level members ``write(c)`` touches in ``layout``: the bytes it changes from zeros or from ones."""
    low, high = gs.CConstants(layout), gs.CConstants(layout)
    high.m_data[:] = b"\xff" * len(high.m_data)
    write(low)
    write(high)
    changed = np.flatnonzero(
        (np.frombuffer(low.m_data, np.uint8) != 0) | (np.frombuffer(high.m_data, np.uint8) != 0xFF)
    )
    spans = {v.name: range(v.offset, v.offset + gs.size_of(v)) for v in layout.variables}
    outside = {"<between members>"} if any(all(k not in span for span in spans.values()) for k in changed) else set()
    return {name for name, span in spans.items() if any(k in span for k in changed)} | outside


def _layout(variables: list[SVariable]) -> SConstantBuffer:
    """``variables`` one after another (each at a 256-byte row), then a member no fill writes."""
    offset_bytes = 0
    for variable in (variables := [*variables, SVariable("cOther", 0, 16, "float4", 1, 4)]):
        variable.offset = offset_bytes
        offset_bytes += (variable.size + 255) // 256 * 256
    return SConstantBuffer("cbAll", offset_bytes, variables)


def _arrays(names) -> list[SVariable]:
    """A float4 array of 16 for each name."""
    return [SVariable(name, 0, 256, "float4", 1, 4, 16) for name in sorted(names)]


def test_the_fills_write_only_the_members_their_names_say():
    frame = _layout([*_arrays(gs.FRAME_NAMES - {"cCameraBuffer"}), _camera_data()])
    assert _written(lambda c: gs.fill_frame(c, _camera()), frame) == gs.FRAME_NAMES
    record = [SVariable("Data", 16, 0, "uint3", 1, 3), SVariable("LastIndex", 28, 0, "uint", 1, 1)]
    instance = _layout([SVariable("cModelPerFrameDataBuffer", 0, 256, "uint4", 1, 4, 16),
                        SVariable("cModelInstanceDataBuffer", 0, 512, "ModelInstanceData", 0, 0, 16, members=record),
                        SVariable("cCurrBoneMatrixBuffer", 0, 768, "float4x3", 4, 3, 16),
                        SVariable("cLastBoneMatrixBuffer", 0, 768, "float4x3", 4, 3, 16)])  # fmt: skip
    palette = np.stack([np.identity(4)] * 3)
    assert _written(lambda c: gs.fill_instance(c, np.identity(4), palette), instance) == gs.INSTANCE_NAMES
    casters = _layout(_arrays(gameshadow.CASTER_NAMES))
    assert _written(lambda c: gameshadow.fill_casters(c, _cascades()), casters) == gameshadow.CASTER_NAMES
    ground = gameframe.SGround(512.0, {}, {"cGritScale": [1.0, 2.0, 0.0, 0.0]}).fill()
    assert ground.names == {"cGritScale", "cTerrainSize", "cTerrainHeightTexIsValid"}
    assert _written(ground, _layout(_arrays(ground.names))) == ground.names


def test_the_shadow_pass_hands_its_models_a_fill_keyed_by_the_cascades_and_named_by_what_it_writes(monkeypatch):
    seen = []
    model = SimpleNamespace(prepare=lambda render_pass, signature, batch, camera, fill, shared: seen.append(fill))
    monkeypatch.setattr(gameshadow, "draw_models", lambda ctx, fbo, casters, prepare, draw, **kwargs: [
        prepare(caster, None, (0, 1), FakeBatch()) for caster in casters])  # fmt: skip
    shadow = gameshadow.CShadowMap.__new__(gameshadow.CShadowMap)
    shadow.m_ctx, shadow.m_size_px, shadow.m_fbo = None, 64, None
    cascades = gameshadow.SCascades(_cascades().cascades, _cascades().light, 50.0, gpu=True)
    ground = gameframe.SGround(512.0, {}, {"cGritScale": [1.0, 2.0, 0.0, 0.0]})
    shadow.render([model], _camera(), cascades, None)
    shadow.render([model], _camera(), cascades, None, ground)
    assert seen[0].names == gameshadow.CASTER_NAMES and seen[0].key[0] == gameshadow.cascade_key(cascades)
    assert seen[1].names == gameshadow.CASTER_NAMES | ground.fill().names
    seen.clear()
    for words in ([1, 2], [1, 2], [1, 3]):  # a caster's own culled words: keyed by what they hold, not by the object
        model.m_words = np.asarray(words, np.uint32)
        shadow.render([model], _camera(), cascades, None)
    assert seen[0].key == seen[1].key != seen[2].key


class SlotBuffer:
    """A dynamic buffer as QRhi keeps it: one copy per frame in flight, each update queued for every copy and
    applied when that copy's frame comes round."""

    SLOTS = 2

    def __init__(self, size: int) -> None:
        self.m_copies = [bytearray(size) for _ in range(self.SLOTS)]
        self.m_pending: list[list] = [[] for _ in range(self.SLOTS)]

    def create(self) -> bool:
        return True

    def destroy(self) -> None:
        pass

    def write(self, offset: int, data: bytes) -> None:
        for queue in self.m_pending:
            queue.append((offset, bytes(data)))

    def frame(self, number: int) -> bytearray:
        """The copy the frame ``number`` reads, after the updates queued for it."""
        slot = number % self.SLOTS
        for offset, data in self.m_pending[slot]:
            self.m_copies[slot][offset : offset + len(data)] = data
        self.m_pending[slot] = []
        return self.m_copies[slot]


class SlotBatch:
    def updateDynamicBuffer(self, buffer, offset, data) -> None:
        buffer.write(offset, data)


class FakeRhi:
    def __init__(self) -> None:
        self.m_made: list[SlotBuffer] = []

    def ubufAligned(self, size: int) -> int:
        return (size + 255) // 256 * 256

    def newBuffer(self, kind, usage, size) -> SlotBuffer:
        self.m_made.append(SlotBuffer(size))
        return self.m_made[-1]


def _pooled_model() -> tuple[gamedraw.CGameModel, FakeRhi]:
    """A model whose constant buffers are carved out of one buffer (``_uniform``), as ``_cbuffer`` makes them."""
    rhi = FakeRhi()
    program = SimpleNamespace(m_cbuffers=_layouts(), m_pipelines={})
    model = gamedraw.CGameModel(rhi, program, None, gamedraw.SMaterial({}, {}))
    model.m_keep, model.m_pipeline, model._m_uploaded = [object()], object(), None
    for name, layout in program.m_cbuffers.items():
        constants = model.m_constants[name] = gs.CConstants(layout)
        model.m_buffers[name] = model._uniform(name, len(constants.m_data))
    return model, rhi


def test_a_models_constant_buffers_share_one_buffer_at_aligned_offsets():
    model, rhi = _pooled_model()
    assert len(rhi.m_made) == 1 and len(model.m_constants) == 6
    assert len({id(buffer) for buffer in model.m_buffers.values()}) == 1
    ranges = sorted(model.m_ranges.values())
    assert all(offset % 256 == 0 and size % 256 == 0 for offset, size in ranges)
    assert all(a[0] + a[1] <= b[0] for a, b in zip(ranges, ranges[1:], strict=False))  # none overlaps
    assert model.m_keep[-1] is rhi.m_made[0]  # released with the model
    pool = gamepool.CUniformPool(rhi, [], 512)
    assert [pool.take(300)[1:] for _ in range(3)] == [(0, 512), (0, 512), (0, 512)]  # a chunk each: 512 apiece
    assert len(rhi.m_made) == 4


def test_a_range_sent_once_is_right_in_every_frame_slot():
    model, _rhi = _pooled_model()
    batch, buffer = SlotBatch(), next(iter(model.m_buffers.values()))
    expected: dict[str, bytes] = {}

    def draw(number: int, camera: gs.SCamera) -> None:
        model.prepare(None, (1, 1), batch, camera)
        for name, constants in model.m_constants.items():
            expected[name] = bytes(constants.m_data)
        copy = buffer.frame(number)
        for name, data in expected.items():
            offset = model.m_ranges[name][0]
            assert bytes(copy[offset : offset + len(data)]) == data, (number, name)

    eyes = [(0.0, 5.0, 10.0)] * 3 + [(1.0, 5.0, 10.0)] * 4 + [(2.0, 5.0, 10.0)] * 2
    for number, eye in enumerate(eyes):  # a change, then frames that send nothing, in each of the two slots
        draw(number, _camera(eye))
    model.m_world = np.diag([2.0, 1.0, 1.0, 1.0])
    for number in range(len(eyes), len(eyes) + 4):
        draw(number, _camera(eyes[-1]))
