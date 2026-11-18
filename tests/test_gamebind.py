"""What the game-shader passes share on QRhi: the resources of a shader (``render.gamebind``: uniform buffers, stand-ins,
the binding set rebuilt when the inputs change, the constants written) and the compute helpers (``render.gamecompute``:
``record``, ``groups``, ``ComputeChain``). The device is Qt's null backend: it makes every resource, draws nothing."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QRhi, QRhiNullInitParams, QRhiSampler, QRhiShaderResourceBinding
from tests.test_gameframe import Buffer, Var

from annotoolbox.render import gamebind, gamecompute, gamedraw
from annotoolbox.render import gameshader as gs

B = QRhiShaderResourceBinding


@pytest.fixture(scope="module")
def rhi():
    device = QRhi.create(QRhi.Implementation.Null, QRhiNullInitParams())
    if device is None:
        pytest.skip("no null QRhi device")
    return device


class Batch:
    """Records what a pass would upload."""

    def __init__(self) -> None:
        self.buffers, self.textures, self.dynamic = [], [], []

    def uploadStaticBuffer(self, buf, data) -> None:
        self.buffers.append((buf, bytes(data)))

    def uploadTexture(self, tex, desc) -> None:
        self.textures.append((tex, [data for _layer, data in desc.entries]))

    def updateDynamicBuffer(self, buf, offset, data) -> None:
        self.dynamic.append((buf, offset, bytes(data)))


class Description:
    """Stands in for QRhi's upload description, which PySide cannot read back."""

    def __init__(self) -> None:
        self.entries = []

    def setEntries(self, entries) -> None:
        self.entries = list(entries)


@pytest.fixture(autouse=True)
def readable_uploads(monkeypatch):
    monkeypatch.setattr(gamedraw, "QRhiTextureUploadDescription", Description)
    monkeypatch.setattr(gamedraw, "QRhiTextureUploadEntry", lambda layer, level, data: (layer, data))
    monkeypatch.setattr(gamedraw, "QRhiTextureSubresourceUploadDescription", bytes)


def constants() -> dict[str, gs.Constants]:
    return {"cb": gs.Constants(Buffer("cb", 100, [Var("cValue", 0, 4, "float")]))}


NUMBERS = {("cb", "cbuffer"): 0, ("cLinearClampCmpSampler", "sampler"): 1, ("cTex", "texture"): 2,
           ("cCube", "texture"): 3, ("cRaw", "byteaddress"): 4, ("cOut", "uav_rwtyped"): 5}  # fmt: skip
TYPES = {"cTex": "Texture2D<float4>", "cCube": "TextureCube", "cOut": "RWTexture2D<float4>"}


def resources(rhi, **options) -> gamebind.Resources:
    return gamebind.Resources(rhi, NUMBERS, lambda _key: B.StageFlag.ComputeStage, constants(),
                              lambda name: TYPES.get(name, ""), **options)  # fmt: skip


def test_a_constant_buffer_gets_one_uniform_buffer_of_whole_256_byte_blocks(rhi):
    res = resources(rhi)
    buf = res.uniform("cb")
    assert res.uniform("cb") is buf and buf.size() == 256 and res.keep == [buf]
    assert gamedraw.uniform_buffer(rhi, 257).size() == 512 and gamedraw.uniform_buffer(rhi, 0).size() == 256


def test_a_resource_the_frame_lacks_is_a_zeroed_stand_in_of_its_kind_made_once(rhi):
    res, batch = resources(rhi), Batch()
    assert res.rebind(batch, {}) is True
    made = dict(res.made)
    assert set(made) == {"cLinearClampCmpSampler", "cTex", "cCube", "cRaw", "cOut"}
    assert made["cLinearClampCmpSampler"].textureCompareOp() == QRhiSampler.CompareOp.GreaterOrEqual
    assert {len(entries) for _tex, entries in batch.textures} == {1, 6}  # a texture, a cube's six faces
    assert all(set(data) == {0} for _tex, entries in batch.textures for data in entries)  # zero, not undefined
    assert batch.buffers == [(made["cRaw"], bytes(4096))]
    assert made["cOut"] not in [t for t, _ in batch.textures]  # a storage texture is the shader's to write
    res.invalidate()
    res.rebind(batch, {})
    assert res.made == made  # the same stand-ins, not new ones


def test_the_frames_input_beats_the_passes_own_answer_which_beats_the_default(rhi):
    seen = []
    mine = rhi.newBuffer(gamedraw.QRhiBuffer.Type.Static, gamedraw.QRhiBuffer.UsageFlag.StorageBuffer, 64)
    mine.create()

    def hook(batch, name, kind):
        seen.append((name, kind))
        return mine if name == "cRaw" else None

    res = resources(rhi, stand_in=hook)
    given = rhi.newTexture(gamedraw.F.RGBA16F, gamedraw.QSize(1, 1), 1, gamedraw.QRhiTexture.Flag(0))
    given.create()
    assert res.resource(Batch(), "cTex", "texture", {"cTex": given}) is given and not seen
    assert res.resource(Batch(), "cRaw", "byteaddress", {}) is mine and seen == [("cRaw", "byteaddress")]
    assert res.resource(Batch(), "cCube", "texture", {}) is res.made["cCube"] and "cRaw" not in res.made


def test_the_binding_set_is_made_again_only_when_the_inputs_change_and_the_old_one_dies_later(rhi):
    res, batch = resources(rhi), Batch()
    a = rhi.newTexture(gamedraw.F.RGBA16F, gamedraw.QSize(1, 1), 1, gamedraw.QRhiTexture.Flag(0))
    a.create()
    assert res.rebind(batch, {"cTex": a}) and not res.rebind(batch, {"cTex": a})
    first = res.srb
    assert res.rebind(batch, {}) and res.srb is not first and res._retired == [first]
    res.invalidate()
    assert res.rebind(batch, {}) and len(res._retired) == 1 and res._retired[0] is not first  # the first is gone


def test_the_constants_are_filled_and_uploaded_into_their_buffer(rhi):
    res, batch = resources(rhi), Batch()
    res.write(batch, lambda c: c.set("cValue", 2.5))
    [(buf, offset, data)] = batch.dynamic
    assert buf is res.uniform("cb") and offset == 0 and np.frombuffer(data, np.float32)[0] == 2.5
    res.write(batch)  # no fill: what the constants hold
    assert np.frombuffer(batch.dynamic[1][2], np.float32)[0] == 2.5


def test_release_destroys_what_was_made_and_the_next_use_makes_it_again(rhi):
    res = resources(rhi)
    res.rebind(Batch(), {})
    assert res.keep and res.srb is not None
    res.release()
    assert not res.keep and not res.made and res.srb is None and not res.buffers
    assert res.rebind(Batch(), {}) and res.keep


def test_a_raw_buffer_binds_as_the_pass_says_and_a_storage_texture_as_an_image(rhi):
    bound = []

    def raw(number, stage, res):
        bound.append((number, res))
        return B.bufferLoad(number, stage, res)

    res = resources(rhi, raw=raw)
    out = res._bindings(Batch(), {})
    assert len(out) == len(NUMBERS) and [n for n, _res in bound] == [4] and bound[0][1] is res.made["cRaw"]


def test_groups_cover_a_size_with_at_least_one_group():
    assert (gamecompute.groups(0), gamecompute.groups(1), gamecompute.groups(8), gamecompute.groups(9)) == (1, 1, 1, 2)
    assert gamecompute.groups(70, 16) == 5 and gamecompute.groups(1920, 64) == 30


class Pass:
    def __init__(self) -> None:
        self.calls = []

    def beginComputePass(self, batch) -> None:
        self.calls.append(("begin", batch))

    def endComputePass(self, batch=None) -> None:
        self.calls.append(("end", batch))


class Stage:
    def __init__(self, log: list) -> None:
        self.log, self.error, self.share = log, "", None

    def prepare(self, batch, inputs, fill) -> None:
        self.log.append(("prepare", batch, inputs, fill))

    def dispatch(self, cb, grid) -> None:
        self.log.append(("dispatch", grid))

    def release(self) -> None:
        self.log.append(("release", self.share is None))


def test_a_recorded_pass_runs_its_parts_in_order_and_ends_with_the_after_batch():
    log, cb = [], Pass()
    ctx = type("Ctx", (), {"native_pass": lambda self, fn: fn(cb, "batch")})()
    gamecompute.record(
        ctx, Stage(log), {"in": 1}, "fill", (2, 3, 1), lambda: "after", lambda batch: log.append(("before", batch))
    )
    assert [e[0] for e in log] == ["before", "prepare", "dispatch"] and log[2][1] == (2, 3, 1)
    assert log[1][1:] == ("batch", {"in": 1}, "fill") and cb.calls == [("begin", "batch"), ("end", "after")]
    cb.calls.clear()
    gamecompute.record(ctx, Stage([]), {}, None, (1, 1, 1))
    assert cb.calls == [("begin", "batch"), ("end", None)]


def test_a_chain_reports_the_first_error_and_releases_the_pipelines_owners_last():
    log = []
    chain = gamecompute.ComputeChain()
    own, sharing = Stage(log), Stage(log)
    sharing.share = own
    chain.stages = {"a": own, "b": sharing}
    assert chain.error == ""
    sharing.error = "refused"
    assert chain.error == "refused"
    chain.release()
    assert log == [("release", False), ("release", True)]


def test_an_input_made_anew_rebinds_though_python_gives_the_old_ones_id_again(rhi):
    """A resized target is let go and made again: the new one often lies where the old one lay, with its ``id``. The
    binding set holds what it was made with, so no other object can have that id while it is compared."""
    res, batch = resources(rhi), Batch()

    def texture():
        tex = rhi.newTexture(gamedraw.F.RGBA16F, gamedraw.QSize(1, 1), 1, gamedraw.QRhiTexture.Flag(0))
        tex.create()
        return tex

    for _ in range(16):  # each the only reference: without the hold the next one took its place and its id
        assert res.rebind(batch, {"cTex": texture()})
    held = res._held
    assert len(held) == 1 and not res.rebind(batch, {"cTex": held[0]})
    res.release()
    assert res._held == ()


def test_a_draws_binding_set_knows_its_textures_and_buffers_by_the_objects(rhi):
    """``fxcdraw.FxcDraw`` rebinds when a texture or buffer of a name is another object, not when its id differs."""
    from types import SimpleNamespace

    from annotoolbox.render import fxcdraw

    stage = SimpleNamespace(samplers={}, types={})
    key = ("cTex", "texture")
    program = SimpleNamespace(
        vs=stage, ps=stage, numbers={key: 0}, stages={key: B.StageFlag.FragmentStage}, cbuffers={}
    )
    draw = fxcdraw.FxcDraw(SimpleNamespace(rhi=rhi, later=lambda r: None), program, None, None, None)
    a, b = (rhi.newTexture(gamedraw.F.RGBA8, gamedraw.QSize(1, 1)) for _ in range(2))
    assert len(draw._bindings({"cTex": a}, None)) == 1 and draw._bound["cTex"] is a
    assert not draw._maps_changed({"cTex": a}) and draw._maps_changed({"cTex": b}) and draw._maps_changed({})
    assert fxcdraw.same_objects([a, ("cb", 256)], [a, ("cb", 256)])
    assert not fxcdraw.same_objects([a, ("cb", 256)], [b, ("cb", 256)])
    assert not fxcdraw.same_objects([a, ("cb", 256)], [a, ("cb", 512)]) and not fxcdraw.same_objects([a], [a, a])


def test_a_three_component_srgb_texture_is_srgb_on_the_device_too(rhi):
    """OpenGL's ``SRGB8`` (the island view's ground albedo, a model's map without alpha) was made linear on QRhi:
    its colours read undecoded, too bright against the OpenGL device's."""
    from annotoolbox.render import rhidevice

    ctx = rhidevice.RhiContext(rhi)
    grey = bytes([128, 128, 128]) * 16
    rgb, rgba = ctx.texture((4, 4), 3, grey, internal_format=0x8C41), ctx.texture((4, 4), 4, internal_format=0x8C43)
    plain = ctx.texture((4, 4), 3, grey)
    flag = gamedraw.QRhiTexture.Flag.sRGB
    assert rgb.srgb and rgba.srgb and not plain.srgb
    assert rgb.native.flags() & flag and not plain.native.flags() & flag
    assert rgb.native.format() == gamedraw.F.RGBA8  # padded to four: the only 8-bit sRGB format
    ctx.decode_srgb = True  # QRhi's OpenGL samples sRGB undecoded: decoded on the way in, kept as half floats
    decoded = ctx.texture((4, 4), 3, grey, internal_format=0x8C41)
    assert decoded.dtype == "f2" and not decoded.srgb
    ctx.release()
