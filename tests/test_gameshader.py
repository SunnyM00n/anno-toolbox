"""The game-shader path: the engine's HLSL bound the way QRhi binds (structured buffers, the bindless table, vertex
semantics), constant buffers written by member path, material defaults read from a description, and (with an
installed Anno 117 in ``ANNO117_DIR``) a model drawn by the game's shaders on Direct3D 12."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest

from annotoolbox.render import gameshader as gs

ENGINE = """
struct CameraData { float4x4 MtxView; float4 CamPosition; };
struct VSIn
{
    float3 Position : SV_Position;
    float4 Normal : NORMAL0;
    float2 TexCoord : TEXCOORD0;
};
StructuredBuffer<CameraData> cCameraBuffer : register(t59, space0);
Texture2D cBindlessTextures[] : register(t0, space1);
cbuffer cbModel : register(b1) { uint cModelPerFrameDataOffset; };
float Lod(in Texture2D tex, in SamplerState s, in float2 uv);
float4 Pick(StructuredBuffer<CameraData> cams, uint i) { return cams[i].CamPosition; }
float Lod(in Texture2D tex, in SamplerState s, in float2 uv)
{
    return tex.CalculateLevelOfDetailUnclamped(s, uv);
}
void vsMain(in VSIn input, uint id : SV_VertexID, out float4 pos : SV_Position)
{
    Texture2D t = cBindlessTextures[id + 1];
    pos = Pick(cCameraBuffer, 0) + cBindlessTextures[0].SampleLevel(cLinearWrapSampler, input.TexCoord, 0);
}
"""


def test_the_engines_bindings_become_qrhis():
    out = gs.adapt(ENGINE, "vsMain", vertex=True)
    h = out.hlsl
    assert "space" not in h and "register(b1)" not in h
    assert "cbuffer cb_cCameraBuffer { CameraData cCameraBuffer[3]; };" in h
    assert "CameraData cams[3]" in h  # a parameter takes the buffer's array
    assert "BindlessTex t = BindlessAt(id + 1);" in h and "BindlessAt(0).SampleLevel(" in h
    assert "cBindlessTextures" not in h and "Texture2DArray cBindless5;" in h
    assert h.count("float Lod(in BindlessTex tex") == 2  # a copy of the prototype and of the definition
    assert [(i.name, i.semantic, i.location) for i in out.inputs] == [
        ("Position", "SV_Position", 0), ("Normal", "NORMAL0", 1), ("TexCoord", "TEXCOORD0", 2)
    ]  # fmt: skip
    assert "float3 Position : TEXCOORD0" in h and "uint id : SV_VertexID" in h


@dataclass
class Var:
    name: str
    offset: int
    size: int
    type: str = ""
    elements: int = 0
    members: list = field(default_factory=list)


@dataclass
class Buffer:
    name: str
    size: int
    variables: list


def test_constants_are_written_by_member_path():
    camera = Var("cCameraBuffer", 0, 240, "CameraData", 3, [Var("MtxView", 0, 64, "float4x4"),
                                                           Var("CamPosition", 64, 16, "float4")])  # fmt: skip
    c = gs.Constants(Buffer("cb", 256, [camera, Var("cFlags", 240, 4, "uint")]))
    m = np.arange(16, dtype=float).reshape(4, 4)
    assert c.set("cCameraBuffer[1].MtxView", m) and c.set("cCameraBuffer[2].CamPosition", [1, 2, 3, 4])
    assert c.set("cFlags", 0xFFFFFFFF) and not c.set("cMissing", 1)
    stored = np.frombuffer(bytes(c.data[80:144]), "f4").reshape(4, 4)
    assert np.array_equal(stored, m.T)  # column-major, as the engine's MUL(v, M) reads it
    assert np.frombuffer(bytes(c.data[224:240]), "f4").tolist() == [1, 2, 3, 4]
    assert np.frombuffer(bytes(c.data[240:244]), "u4")[0] == 0xFFFFFFFF


def test_a_material_descriptions_defaults_and_the_configs_values():
    pytest.importorskip("PySide6")  # the module hands its result to the QRhi draw
    from annotoolbox.models import shaderview

    fxm = """material ModelCommon {
    color cDiffuseColor { name = "x", default = color(1.0), }
    float2 cTexTiling { default = float2(2.0, 3.0), }
    bool cTexFlipU { default = true, }
    Texture2D cModelDiffTex { default = "data/a.png", }
    float cOpacity { default = 0.5, } }"""
    defaults = shaderview.material_defaults(fxm)
    assert defaults == {"cDiffuseColor": [1.0] * 4, "cTexTiling": [2.0, 3.0], "cTexFlipU": 1, "cOpacity": 0.5}
    values = {"cDiffuseColor.r": "0.5", "cOpacity": "1"}
    assert shaderview.config_value("cDiffuseColor", defaults["cDiffuseColor"], values) == [0.5, 1.0, 1.0, 1.0]
    assert shaderview.config_value("cOpacity", 0.5, values) == 1.0


GAME_FRAME = """
import sys
sys.argv = ["rhi_spike.py", "--anno117", %r, "--shot", %r, "--size", "160x120"]
sys.path.insert(0, "scripts")
import rhi_spike
sys.exit(rhi_spike.main())
"""


def test_a_model_is_drawn_by_the_games_own_shaders(tmp_path):
    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / "scripts" / "rhi_spike.py").is_file():
        pytest.skip("the spike script is not in this tree")
    shot = tmp_path / "barrel.png"
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": str(repo / "src")}
    done = subprocess.run([sys.executable, "-c", GAME_FRAME % (root, str(shot))], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=600)  # fmt: skip
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    from PIL import Image

    px = np.asarray(Image.open(shot).convert("RGB"), int)
    assert (np.abs(px - px[0, 0]).sum(axis=2) > 30).mean() > 0.05  # the model covers part of the view


def test_a_meshs_stored_elements_feed_the_inputs_by_semantic():
    pytest.importorskip("PySide6")
    from annotoolbox.render import gamedraw

    # P4h at 0, N4b at 8, T2h at 12: 16 bytes a vertex, two vertices
    pos = np.array([[1, 2, 3, 1], [4, 5, 6, 1]], "<f2").tobytes()
    rows = [pos[k * 8 : k * 8 + 8] + bytes([128, 255, 128, 0]) + np.array([0.5, 0.25], "<f2").tobytes() for k in (0, 1)]
    mesh = gamedraw.Mesh(b"".join(rows), 16, ((0, 6, 4, 0), (1, 5, 1, 8), (4, 6, 2, 12)), np.arange(3), 2)
    assert mesh.positions().tolist() == [[1, 2, 3], [4, 5, 6]]
    inputs = [gs.Input("Position", "float3", "SV_Position", 0), gs.Input("Normal", "float4", "NORMAL0", 1),
              gs.Input("Tangent", "float4", "TANGENT0", 2), gs.Input("TexCoord", "float2", "TEXCOORD0", 3),
              gs.Input("Color", "float4", "COLOR0", 4)]  # fmt: skip
    fill, attrs = gamedraw.vertex_input(mesh, inputs)
    got = [(a.binding(), a.location(), a.offset()) for a in attrs]
    assert got == [(0, 0, 0), (0, 1, 8), (1, 2, 0), (0, 3, 12), (1, 4, 4)]  # what the mesh lacks reads a constant
    assert fill == bytes([0, 0, 0, 0, 255, 255, 255, 255]) * 2  # a tangent of zero, a white colour


def test_the_place_and_a_skins_joints_become_the_engines_bone_rows():
    bones = Var("cCurrBoneMatrixBuffer", 0, 48 * 4, "float4x3", 4)
    c = gs.Constants(Buffer("cb", 48 * 4, [bones]))
    world = np.identity(4)
    world[:3, 3] = (5, 6, 7)
    joint = np.identity(4)
    joint[0, 3] = 1.0
    gs.fill_instance(c, world, np.stack([np.identity(4), joint]))
    second = np.frombuffer(bytes(c.data[48:96]), "f4").reshape(3, 4)  # the columns of the 4 x 3 row matrix
    assert second[0].tolist() == [1, 0, 0, 6] and second[1].tolist() == [0, 1, 0, 6]  # x moved by 5 + 1
    assert np.frombuffer(bytes(c.data[0:48]), "f4").reshape(3, 4)[0, 3] == 5


def test_the_reversed_projection_puts_the_near_plane_at_one():
    m = gs.perspective(1.0, 1.5, 0.5, 100.0, reverse=True)
    for z, depth in ((-0.5, 1.0), (-100.0, 0.0)):
        clip = np.array([0, 0, z, 1.0]) @ m
        assert clip[2] / clip[3] == pytest.approx(depth)
    std = gs.perspective(1.0, 1.5, 0.5, 100.0)
    clip = np.array([0, 0, -7.0, 1.0])
    assert (clip @ std)[2] / (clip @ std)[3] == pytest.approx(1 - (clip @ m)[2] / (clip @ m)[3])


def test_declared_textures_keep_their_kind():
    hlsl = "Texture2D<uint>  cIndex;\nTextureCube cSky;\nRWTexture2D<float3> cOut;\nTexture2DArray cAreas;\n"
    assert gs.declared_types(hlsl) == {"cIndex": "Texture2D<uint>", "cSky": "TextureCube",
                                       "cOut": "RWTexture2D<float3>", "cAreas": "Texture2DArray"}  # fmt: skip


def test_the_gbuffer_technique_and_a_materials_first_values():
    pytest.importorskip("PySide6")
    from annotoolbox.formats.fxeffect import Effect, ShaderCall, Technique
    from annotoolbox.models import shaderview

    vs, ps = ShaderCall("vs", "6_2", "vsModel"), ShaderCall("ps", "6_2", "psModelDeferred")
    effect = Effect("", [Technique("ModelDepthPrepass", (), [vs]), Technique("ModelDeferred", ("X",), [vs, ps])])
    tech, got_vs, got_ps = shaderview.gbuffer_calls(effect)
    assert tech.name == "ModelDeferred" and (got_vs, got_ps) == (vs, ps)
    with pytest.raises(shaderview.NoGBufferPass):
        shaderview.gbuffer_calls(Effect("", [Technique("CutOut", (), [vs, ps])]))
    assert shaderview.first_values([("A", "1"), ("B", "2"), ("A", "3")]) == {"A": "1", "B": "2"}
    assert shaderview.effect_of(8) == "model_pbr" and shaderview.effect_of(999) is None


def test_the_sky_cube_is_the_sky_lights_radiance():
    pytest.importorskip("PySide6")
    from annotoolbox.render import gamelight

    sh = [(1.0, 1.0, 1.0), (0.5, 0.5, 0.5)] + [(0.0, 0.0, 0.0)] * 7  # brighter up (c1 is y)
    levels = gamelight.sky_cube(sh, 8)
    assert len(levels) == 4 and all(len(faces) == 6 for faces in levels)
    up, down = levels[0][2].astype(float).mean(), levels[0][3].astype(float).mean()
    assert up > down
    flat = gamelight.sh_radiance([(2.0, 2.0, 2.0)] + [(0.0, 0.0, 0.0)] * 8, np.array([[0.0, 1.0, 0.0]]))
    assert flat[0].tolist() == pytest.approx([0.56419] * 3, abs=1e-4)


VIEWER_SHADERS = """
import json
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources import gametree
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import rhi_picture
app = QApplication([])
tree = gametree.open_game("anno117", Path(%r))
scene = modelscene.build(tree, "data/base/graphics/library/props/storage/barrels/barrel_01.cfg", "anno117")
out = {}
for look in ("editor", "shaders"):
    img = rhi_picture(ModelFiles(tree), scene, ViewState(look=look, particles=False), (160, 120), "d3d12", 600)
    out[look] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(160)] for y in range(120)], float).tolist()
print(json.dumps(out))
"""


def test_the_viewer_draws_a_config_by_the_games_own_shaders():
    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": str(repo / "src")}
    done = subprocess.run([sys.executable, "-c", VIEWER_SHADERS % root], cwd=repo, env=env, capture_output=True,
                          text=True, timeout=900)  # fmt: skip
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    import json

    got = {k: np.asarray(v) for k, v in json.loads(done.stdout.splitlines()[-1]).items()}
    assert got["shaders"].std() > 5.0  # the barrel drawn
    assert np.abs(got["shaders"] - got["editor"]).mean() > 0.5  # by other shaders than our plain look's


def test_without_a_shader_compiler_the_look_says_where_to_get_one(monkeypatch):
    pytest.importorskip("PySide6")
    import time
    from types import SimpleNamespace

    from annotoolbox.infra import dxc
    from annotoolbox.viewer import gamepass

    def missing(anno117=None):
        raise dxc.DxcMissing("no DXC found")

    monkeypatch.setattr(dxc.Dxc, "find", missing)
    rhi = SimpleNamespace(backendName=lambda: "D3D12")
    renderer = SimpleNamespace(ctx=SimpleNamespace(rhi=rhi, native_pass=None),
                               files=SimpleNamespace(files=SimpleNamespace(game="anno117", folder=None)))  # fmt: skip
    gp = gamepass.GamePass(renderer)
    for _ in range(200):
        why = gp.why_not()
        if why:
            break
        time.sleep(0.01)
    assert "shader compiler" in why and dxc.WHERE_TO_GET in why
    renderer.files.files.game = "anno1800"
    assert "Anno 117" in gp.why_not()
    gp.release()


def test_a_variants_text_is_the_effects_with_its_defines_in_front():
    pytest.importorskip("PySide6")
    from annotoolbox.formats import fxeffect
    from annotoolbox.models import shaderview

    files = {"data/shaders/fx/a.fx": b'#include "b.fxh"\nfloat4 ps() : SV_Target { return X; }\n',
             "data/shaders/fx/b.fxh": b"#define X 1\n"}  # fmt: skip
    source = object.__new__(shaderview.ShaderSource)
    source._bodies, source.descriptions = {}, []
    source._read = files.get
    for defines in ([("A", "1")], [("B", ""), ("C", "2")]):
        want = fxeffect.effect_source(files.get, "data/shaders/fx/a.fx", defines, [])
        assert source.source("data/shaders/fx/a.fx", defines) == want
    assert len(source._bodies) == 1  # the includes read once


def test_a_write_stays_inside_its_member_and_a_wider_matrix_gives_its_block():
    @dataclass
    class Row(Var):
        row_major: bool = False

    c = gs.Constants(Buffer("cb", 128, [Var("cSun", 0, 12, "float3"), Var("cPower", 12, 4, "float"),
                                        Var("cBones", 16, 48, "float4x3"), Row("cRows", 64, 60, "float4x3", row_major=True)]))  # fmt: skip
    assert c.set("cPower", 5) and c.set("cSun", [1, 2, 3, 0])  # four values into a float3
    assert np.frombuffer(bytes(c.data[0:16]), "f4").tolist() == [1, 2, 3, 5]
    m = np.arange(16, dtype=float).reshape(4, 4)
    assert c.set("cBones", m)  # a 4x4 into a float4x3: its first three columns
    assert np.frombuffer(bytes(c.data[16:64]), "f4").reshape(3, 4).tolist() == m[:, :3].T.tolist()
    assert c.set("cRows", m)  # a row_major member is packed row by row
    rows = np.frombuffer(bytes(c.data[64:124]) + bytes(4), "f4").reshape(4, 4)
    assert rows[:, :3].tolist() == m[:, :3].tolist()


def test_the_forward_pass_releases_its_frame_and_light_textures():
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.viewer import forwardpass

    class Owned:
        released = 0

        def release(self):
            self.released += 1

    frame, clusters = Owned(), Owned()
    ctx = SimpleNamespace(_doomed=[])
    fp = forwardpass.ForwardPass(
        SimpleNamespace(ctx=ctx, files=SimpleNamespace(files=SimpleNamespace(game="anno2205")))
    )
    fp.frame, fp.clusters = frame, clusters
    fp.release()
    assert (frame.released, clusters.released) == (1, 1) and fp.frame is None and fp.clusters is None


def test_a_pass_stays_busy_while_a_group_waits_for_its_shaders():
    """The install's source read but a group's compile not asked for yet: the view keeps drawing, and an offscreen
    picture waits, instead of stopping on our look."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.viewer import forwardpass

    part = SimpleNamespace(banner=None, node="Files/0")
    renderer = SimpleNamespace(ctx=None, files=SimpleNamespace(files=SimpleNamespace(game="anno2205")),
                               scene=SimpleNamespace(parts=[part]))  # fmt: skip
    fp = forwardpass.ForwardPass(renderer)
    fp._model = lambda k, part, item: None  # still compiling
    _ready, _rest, waiting = fp._split([(part, {"key": 0}, None)])
    assert waiting == 1 and fp.busy() == 1  # the loaders are idle: the waiting group alone keeps it busy
    fp.release_scene()
    assert fp.busy() == 0


def test_the_games_record_names_the_pass_that_draws_it():
    from types import SimpleNamespace

    from annotoolbox.viewer import forwardpass, fxcpass, fxcpass1404, fxcpass1701, gamepass

    def renderer(game):
        return SimpleNamespace(files=SimpleNamespace(files=SimpleNamespace(game=game)))

    assert type(gamepass.make_pass(renderer("anno2070"))) is fxcpass.FxcPass
    assert type(gamepass.make_pass(renderer("anno117"))) is gamepass.GamePass
    assert type(gamepass.make_pass(renderer("anno2205"))) is forwardpass.ForwardPass
    assert type(gamepass.make_pass(renderer("anno1404"))) is fxcpass1404.FxcPass1404
    assert type(gamepass.make_pass(renderer("anno1701"))) is fxcpass1701.FxcPass1701
    assert type(gamepass.make_pass(renderer("anno1503"))) is gamepass.GamePass  # none of its own: 117's says why not


@dataclass
class Typed(Var):
    rows: int = 0
    columns: int = 0


def test_a_structures_members_are_written_although_the_reflection_gives_them_no_size():
    """The reflection gives a structure's members no size (only a buffer's variables have one): a write into a
    member must still land, sized by the packing rules."""
    light = Typed("cLightBuffer", 0, 192, "LightData", members=[
        Typed("LgtDiffuse", 0, 0, "float3", rows=1, columns=3), Typed("Range", 12, 0, "float", rows=1, columns=1),
        Typed("SH", 16, 0, "float4", elements=9, rows=1, columns=4), Typed("Mtx", 160, 0, "float4x3", rows=4, columns=3),
    ])  # fmt: skip
    c = gs.Constants(Buffer("cb", 256, [light]))
    assert c.set("cLightBuffer.LgtDiffuse", [1, 2, 3, 9]) and c.set("cLightBuffer.Range", 4)
    assert np.frombuffer(bytes(c.data[0:16]), "f4").tolist() == [1, 2, 3, 4]  # the float3 ends before Range
    assert c.set_rows("cLightBuffer.SH", [[k, k, k, 1] for k in range(9)])
    assert np.frombuffer(bytes(c.data[16 + 16 * 8 : 16 + 16 * 9]), "f4").tolist() == [8, 8, 8, 1]
    m = np.arange(16, dtype=float).reshape(4, 4)
    assert c.set("cLightBuffer.Mtx", m)  # three columns of four
    assert np.frombuffer(bytes(c.data[160:208]), "f4").reshape(3, 4).tolist() == m[:, :3].T.tolist()
    assert gs.size_of(light.members[2]) == 144 and gs.size_of(light.members[3]) == 48


class _Sampler:
    def __init__(self, args) -> None:
        self.args, self.compare = args, None

    def setAddressW(self, mode) -> None:
        pass

    def setTextureCompareOp(self, op) -> None:
        self.compare = op

    def create(self) -> None:
        pass


class _Rhi:
    def newSampler(self, *args) -> _Sampler:
        return _Sampler(args)


def test_117s_comparison_samplers_pass_where_the_reference_is_the_greater():
    from PySide6.QtGui import QRhiSampler

    from annotoolbox.models import fxstage
    from annotoolbox.render import gamedraw

    greater, less = QRhiSampler.CompareOp.GreaterOrEqual, QRhiSampler.CompareOp.LessOrEqual
    # the fixed samplers of 117's shaders, whoever binds them (the transparent models, the compute stages, the shading)
    for name in ("cLinearClampCmpSampler", "sPointClampCmp"):
        assert gamedraw.fixed_sampler(_Rhi(), name).compare == greater
    assert gamedraw.fixed_sampler(_Rhi(), "cLinearClampSampler").compare is None
    model = gamedraw.GameModel.__new__(gamedraw.GameModel)
    model.rhi, model.keep = _Rhi(), []
    assert model._sampler("cLinearClampCmpSampler").compare == greater and len(model.keep) == 1
    # the older games keep the plain default: an effect's own comparison sampler tests the other way
    desc = fxstage.Sampler("COMPARISON_MIN_MAG_MIP_LINEAR", compare=True)
    assert gamedraw.sampler_of(_Rhi(), desc).compare == less


def test_a_fixed_sampler_takes_its_address_mode_from_its_name_else_from_the_callers_default():
    from PySide6.QtGui import QRhiSampler

    from annotoolbox.models import fxstage
    from annotoolbox.render import gamedraw

    clamp, wrap = QRhiSampler.AddressMode.ClampToEdge, QRhiSampler.AddressMode.Repeat
    assert fxstage.sampler_named("cLinearWrapSampler", "CLAMP").address_u == "WRAP"
    assert fxstage.sampler_named("cLinearSampler").address_u == "WRAP"
    assert fxstage.sampler_named("cLinearSampler", "CLAMP").address_u == "CLAMP"
    assert gamedraw.fixed_sampler(_Rhi(), "cLinearWrapSampler", "CLAMP").args[3] == wrap
    assert gamedraw.fixed_sampler(_Rhi(), "cLinearClampSampler", "CLAMP").args[3] == clamp


def test_a_block_read_for_its_states_keeps_the_first_assignment_in_lower_case():
    from annotoolbox.models import fxstage

    hlsl = "BlendState Alpha { BlendEnable[0] = TRUE; SrcBlend = SRC_ALPHA;\n#ifdef OTHER\nSrcBlend = ONE;\n#endif\n};"
    assert fxstage.read_states(hlsl) == {"Alpha": {"blendenable[0]": "TRUE", "srcblend": "SRC_ALPHA"}}


def test_a_blend_factor_is_read_in_any_case_with_dest_for_dst():
    from PySide6.QtGui import QRhiGraphicsPipeline

    from annotoolbox.render import renderstates

    f = QRhiGraphicsPipeline.BlendFactor
    assert renderstates.blend_factor("SRC_ALPHA") == f.SrcAlpha == renderstates.blend_factor("src_alpha")
    assert renderstates.blend_factor("dest_alpha") == f.DstAlpha == renderstates.blend_factor("DST_ALPHA")
    assert renderstates.blend_factor("inv_dest_color") == f.OneMinusDstColor
    assert (
        renderstates.blend_factor("inv_src_color") == f.OneMinusSrcColor
    )  # the older table lacked both inverse colours
    assert renderstates.blend_factor("nonsense") == f.One and renderstates.blend_factor("nonsense", "ZERO") == f.Zero
    state = renderstates.pass_state(
        {"AlphaBlendEnable": "true", "SrcBlend[0]": "src_alpha", "DstBlend[0]": "x"}, targets=1
    )
    assert state.blends[0][0] == f.SrcAlpha


def test_the_shader_texts_brackets_come_from_the_effect_reader():
    from annotoolbox.formats import fxeffect
    from annotoolbox.render import rawbuffers

    assert gs.fxeffect is fxeffect and rawbuffers.fxeffect is fxeffect
    assert rawbuffers.structured_size("struct S { float4 a; uint b; };", "S") == 20
