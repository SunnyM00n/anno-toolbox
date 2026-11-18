"""The legacy shader compiler (FXC) loaded at run time: compile with defines and includes read through a callback,
reflect, disassemble; and a DXBC shader drawn as it is on QRhi's Direct3D 11 backend (in a child process, as the
other QRhi frames in ``test_rhi``). Skipped without a ``d3dcompiler_47.dll`` or a Direct3D 11 device."""

from __future__ import annotations

import os
import sys

import pytest

from annotoolbox.infra import fxc

SOURCE = """
#include "head.fxh"
cbuffer cbFrame { float4x4 cMtx; float4 cTint; };
Texture2D cTex; SamplerState cTexSampler;
float4 vsMain(float3 p : POSITION, float2 uv : TEXCOORD0) : SV_Position
{
    return mul(float4(p, 1), cMtx) * SCALE + cTex.SampleLevel(cTexSampler, uv, 0) * cTint;
}
"""


def _fxc() -> fxc.Fxc:
    if sys.platform != "win32":
        pytest.skip("FXC is a Windows DLL")
    try:
        return fxc.Fxc.find()
    except fxc.FxcMissing as e:
        pytest.skip(str(e))


def test_fxc_compiles_with_includes_and_defines_and_reflects():
    f = _fxc()
    asked = []

    def include(name: str, parent: str) -> bytes | None:
        asked.append((name, parent))
        return b"static const float SCALE = BOOST;\n" if name == "head.fxh" else None

    done = f.compile(SOURCE, "vsMain", "vs_5_0", [("BOOST", "2")], include=include)
    assert done.ok, done.messages
    assert asked == [("head.fxh", "")]
    r = f.reflect(done.object)
    assert r.model == "5_0"
    assert {(b.name, b.kind) for b in r.bindings} == {("cbFrame", "cbuffer"), ("cTex", "texture"),
                                                       ("cTexSampler", "sampler")}  # fmt: skip
    cb = r.cbuffers[0]
    assert [(v.name, v.offset, v.type) for v in cb.variables] == [("cMtx", 0, "float4x4"), ("cTint", 64, "float4")]
    assert [s for s, _i, _r in r.inputs] == ["POSITION", "TEXCOORD"]
    assert "vs_5_0" in f.disassemble(done.object)
    missing = f.compile(SOURCE, "vsMain", "vs_5_0", [("BOOST", "2")], include=lambda n, p: None)
    assert not missing.ok and "head.fxh" in missing.messages
    text, _ = f.preprocess(SOURCE, [("BOOST", "3")], include=include)
    assert "SCALE = 3" in text


def test_a_reader_that_raises_fails_the_include_with_its_message_instead_of_crashing_the_compiler():
    f = _fxc()

    def broken(name: str, parent: str) -> bytes | None:
        raise OSError("archive entry unreadable")

    done = f.compile(SOURCE, "vsMain", "vs_5_0", [("BOOST", "2")], include=broken)
    assert not done.ok and "archive entry unreadable" in done.messages
    text, messages = f.preprocess(SOURCE, [("BOOST", "3")], include=broken)
    assert text == "" and "archive entry unreadable" in messages


def test_the_games_flag_word_compiles():
    f = _fxc()
    done = f.compile("float4 ps() : SV_Target { return 1; }", "ps", "ps_4_0", flags=fxc.GAME_FLAGS)
    assert done.ok, done.messages
    assert not f.compile("float4 ps() : SV_Target { return 1; }", "ps", "ps_5_0", flags=fxc.NO_PRESHADER).ok
    assert fxc.profile_model("ps_4_1") == 41 and fxc.profile_model("cs_5_0") == 50


DXBC_FRAME = """
import json
from PySide6.QtGui import QColor, QRhiBuffer, QRhiGraphicsPipeline, QRhiShaderResourceBinding as B, QRhiShaderStage
from PySide6.QtGui import QRhiVertexInputAttribute, QRhiVertexInputBinding, QRhiVertexInputLayout, QShader
from PySide6.QtGui import QRhiDepthStencilClearValue, QRhiViewport
from PySide6.QtWidgets import QApplication
from annotoolbox.infra import fxc
from annotoolbox.render import gamedraw, rhiview
app = QApplication([])
f = fxc.Fxc.find()
VS = "float4 vs(float2 p : TEXCOORD0) : SV_Position { return float4(p, 0, 1); }"
PS = "cbuffer cbTint { float4 cTint; }; float4 ps() : SV_Target { return cTint; }"
vs, ps = f.compile(VS, "vs", "vs_5_0"), f.compile(PS, "ps", "ps_5_0")
cb_reg = f.reflect(ps.object).bindings[0].register

class View(rhiview.RhiView):
    def draw(self, ctx, screen, size):
        rhi = ctx.rhi
        def run(cb, batch):
            rt, rp, sig = ctx.render_target(screen, clear=True)
            vbuf = rhi.newBuffer(QRhiBuffer.Type.Immutable, QRhiBuffer.UsageFlag.VertexBuffer, 24)
            vbuf.create()
            import struct
            batch.uploadStaticBuffer(vbuf, struct.pack("6f", -1, -1, 1, -1, -1, 1))
            ubuf = rhi.newBuffer(QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer, 256)
            ubuf.create()
            batch.updateDynamicBuffer(ubuf, 0, struct.pack("4f", 0, 1, 0, 1))
            srb = rhi.newShaderResourceBindings()
            srb.setBindings([B.uniformBuffer(0, B.StageFlag.FragmentStage, ubuf)])
            srb.create()
            pipe = rhi.newGraphicsPipeline()
            pipe.setShaderStages([
                QRhiShaderStage(QRhiShaderStage.Type.Vertex, gamedraw.native_shader(QShader.Stage.VertexStage, vs.object, "vs", {})),
                QRhiShaderStage(QRhiShaderStage.Type.Fragment, gamedraw.native_shader(QShader.Stage.FragmentStage, ps.object, "ps", {0: (cb_reg, -1)}))])
            layout = QRhiVertexInputLayout()
            layout.setBindings([QRhiVertexInputBinding(8)])
            layout.setAttributes([QRhiVertexInputAttribute(0, 0, QRhiVertexInputAttribute.Format.Float2, 0)])
            pipe.setVertexInputLayout(layout)
            pipe.setShaderResourceBindings(srb)
            pipe.setRenderPassDescriptor(rp)
            pipe.setSampleCount(max(1, sig[2]))
            self.made = pipe.create()
            cb.beginPass(rt, QColor(0, 0, 255), QRhiDepthStencilClearValue(1.0, 0), batch)
            if self.made:
                cb.setGraphicsPipeline(pipe)
                cb.setViewport(QRhiViewport(0, 0, *size))
                cb.setShaderResources(srb)
                cb.setVertexInput(0, [(vbuf, 0)])
                cb.draw(3)
            cb.endPass()
            ctx._doomed += [vbuf, ubuf, srb, pipe]
        ctx.native_pass(run)

v = View("d3d11")
img = v.picture(32, 32)
corners = {"filled": img.pixelColor(3, 16).getRgb()[:3], "clear": img.pixelColor(29, 6).getRgb()[:3]}
print(json.dumps({"failed": v.failed, "made": getattr(v, "made", None), "dxil": gamedraw.is_dxil(vs.object), **corners}))
"""


def test_a_dxbc_shader_draws_on_direct3d_11_as_it_is():
    _fxc()
    from tests.test_rhi import _backends, _child

    if "d3d11" not in _backends():
        pytest.skip("no Direct3D 11 device")
    got = _child(DXBC_FRAME)
    assert not got["failed"] and got["made"] and not got["dxil"], got
    assert got["filled"][1] > 200 and got["filled"][0] < 40, got  # the green triangle (the left of it)
    assert got["clear"][2] > 200, got


def _pretend_loader(monkeypatch, loads):
    """``Fxc(path)`` without the library: it loads when ``loads(path)``, else says it could not (a wrong architecture)."""

    def init(self, path):
        if not loads(path):
            raise fxc.FxcMissing(f"{path}: not a valid Win32 application")
        self.path = path

    monkeypatch.setattr(fxc.Fxc, "__init__", init)


def test_off_windows_the_compiler_says_it_is_not_available(monkeypatch):
    monkeypatch.setattr(fxc.os, "name", "posix")
    with pytest.raises(fxc.FxcMissing, match="Windows DLL"):
        fxc.Fxc(fxc.DLL)


def test_a_copy_that_does_not_load_leaves_the_next_one_to_try(tmp_path, monkeypatch):
    game = tmp_path / "game"
    (game / "Bin" / "Win64").mkdir(parents=True)
    (game / "Bin" / "Win64" / fxc.DLL).write_bytes(b"32-bit")
    system = tmp_path / "system" / "System32"
    system.mkdir(parents=True)
    (system / fxc.DLL).write_bytes(b"64-bit")
    monkeypatch.setenv("SystemRoot", str(system.parent))
    monkeypatch.delenv(fxc.FXC_ENV, raising=False)
    _pretend_loader(monkeypatch, lambda path: path.read_bytes() == b"64-bit")
    assert fxc.Fxc.find(game).path == system / fxc.DLL


def test_when_no_copy_loads_the_message_says_why_for_each(tmp_path, monkeypatch):
    (tmp_path / "System32").mkdir()
    (tmp_path / "System32" / fxc.DLL).write_bytes(b"x")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.delenv(fxc.FXC_ENV, raising=False)
    _pretend_loader(monkeypatch, lambda path: False)
    with pytest.raises(fxc.FxcMissing, match="not a valid Win32"):
        fxc.Fxc.find()


def test_the_variable_may_name_several_folders(tmp_path, monkeypatch):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / fxc.DLL).write_bytes(d.encode())
    monkeypatch.setenv(fxc.FXC_ENV, f"{tmp_path / 'nowhere'}{os.pathsep}{tmp_path / 'b'}{os.pathsep}{tmp_path / 'a'}")
    _pretend_loader(monkeypatch, lambda path: True)
    assert fxc.Fxc.find().path == tmp_path / "b" / fxc.DLL


def test_the_missing_compilers_reason_is_in_the_interface_language_and_english_for_a_log(tmp_path, monkeypatch):
    from annotoolbox.infra import dxc, i18n

    (tmp_path / "System32").mkdir()
    (tmp_path / "System32" / fxc.DLL).write_bytes(b"x")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.delenv(fxc.FXC_ENV, raising=False)
    _pretend_loader(monkeypatch, lambda path: False)
    i18n.use("de")
    with pytest.raises(fxc.FxcMissing) as caught:
        fxc.Fxc.find()
    shown, english = str(caught.value), caught.value.english
    assert shown.startswith(f"keine {fxc.DLL} gefunden: d3dcompiler_47.dll: Bin/Win64 des Spiels")
    assert english.startswith(f"no {fxc.DLL} found: d3dcompiler_47.dll: the game's own Bin/Win64")
    assert shown.endswith("not a valid Win32 application") and english.endswith("not a valid Win32 application")
    monkeypatch.setattr(dxc.os, "name", "posix")
    with pytest.raises(dxc.DxcMissing) as caught:
        dxc.Dxc(tmp_path)
    assert str(caught.value).startswith("DXC ist hier eine Windows-DLL: dxcompiler.dll und dxil.dll")
    assert caught.value.english.startswith("DXC is a Windows DLL here: dxcompiler.dll and dxil.dll: the Anno 117")
    assert dxc.DxcMissing("no DXC found").english == "no DXC found" and not i18n.missing()
