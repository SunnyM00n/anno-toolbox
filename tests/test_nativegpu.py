"""The native backend's Direct3D 12 layer (render/nativelayout, nativegpu; native/d3d12): the binding plan from a
pass's reflections, the vertex input from a shader's signature, the module's loading, and with the module built the
layer itself on Windows' software rasteriser and the game's shipped tone map drawn on both routes of step 0."""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass, field

import pytest

from annotoolbox.render import nativegpu, nativelayout


@dataclass
class B:
    name: str
    kind: str
    register: int
    count: int = 1
    space: int = 0


@dataclass
class C:
    name: str
    size: int


@dataclass
class R:
    bindings: list
    cbuffers: list = field(default_factory=list)


def test_the_plan_lays_a_pass_out_by_space_with_its_bindless_ranges_and_root_constants():
    vs = R([B("cbPerFrame", "cbuffer", 0), B("cModelInstanceDataBuffer", "structured", 55),
            B("cbModel", "cbuffer", 1, space=1024), B("cbDrawCallIndex", "cbuffer", 2, space=1024)],
           [C("cbModel", 192), C("cbDrawCallIndex", 16)])  # fmt: skip
    ps = R([B("cbPerFrame", "cbuffer", 0), B("cBlueNoiseTex", "texture", 14), B("cLinearClampCmpSampler", "sampler", 11),
            B("cTextureAtlas", "texture", 0, space=1), B("cTextureFeedback", "uav_rwbyteaddress", 2, space=1),
            B("cBindlessTextures", "texture", 3, count=0, space=1)])  # fmt: skip
    plan = nativelayout.BindingPlan.of([vs, ps])
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
    many = R([B(f"t{s}", "texture", 0, space=s) for s in range(9)])
    with pytest.raises(ValueError, match="binding layouts"):
        nativelayout.BindingPlan.of([many])


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
    monkeypatch.setattr(nativegpu, "_state", {})
    monkeypatch.setenv(nativegpu.ENV, "0")
    assert nativegpu.load() is None and "switched off" in nativegpu.reason()


def _module():
    monkey = pytest.MonkeyPatch()
    monkey.delenv(nativegpu.ENV, raising=False)
    nativegpu._state.pop("module", None)
    module = nativegpu.load()
    monkey.undo()
    if module is None:
        pytest.skip(f"the native module: {nativegpu.reason()}")
    return module


def test_the_layer_runs_on_the_software_rasteriser():
    module = _module()
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
    out[mode] = {"std": float(px.std()), "corner": list(px[-1, 0]), "failed": view.failed}
print(json.dumps(out))
"""


def test_the_games_shipped_tone_map_draws_on_both_routes_of_step_zero():
    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    _module()
    from tests.test_rhi import _backends, _child

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(TONEMAP)
    for mode in ("a", "b"):
        assert not got[mode]["failed"] and got[mode]["std"] > 30, got  # the tone-mapped gradient, not a clear
    # route A drew into QRhi's target: the same picture as route B's copy through the CPU
    assert all(abs(x - y) < 8 for x, y in zip(got["a"]["corner"], got["b"]["corner"], strict=True)), got
