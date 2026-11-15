"""``formats/fxeffect`` and ``formats/fxlink``: 117's effect dialect turned into plain HLSL, on synthetic files (the
includes, the dialect's macros, the techniques, what one shader reaches and in which order, the uniform-argument
entries, the resources at their engine bindings). Compiling needs the game's compiler: ``test_shadercheck``."""

from __future__ import annotations

import re

import pytest

from annotoolbox.formats import fxeffect, fxlink

FILES = {
    "data/shaders/fx/demo.fx": b'#include "../include/common.fxh"\n#include "missing.fxh"\nfloat4 Main() { return 1; }\n',
    "data/shaders/include/common.fxh": b"#define COMMON 1\n",
    "data/shaders/fx/flat.fx": b'#include "common.fxh"\n',
}


def read(path: str) -> bytes | None:
    return FILES.get(path)


def test_includes_are_inlined_under_line_markers_and_a_missing_one_errors_only_where_taken():
    text = fxeffect.expand_includes(read, "data/shaders/fx/demo.fx")
    assert '#line 1 "data/shaders/include/common.fxh"' in text and "#define COMMON 1" in text
    assert "#error include missing.fxh not found" in text  # left to the preprocessor's branch
    assert '#line 3 "data/shaders/fx/demo.fx"' in text  # back in the including file, on its next line
    flat = fxeffect.expand_includes(read, "data/shaders/fx/flat.fx")
    assert "#define COMMON 1" in flat  # not beside the file: found in the include folder, as the game finds it
    assert fxeffect.includes_of(read, "data/shaders/fx/demo.fx") >= {"data/shaders/include/common.fxh"}


def test_pastes_with_a_macro_name_go_through_a_second_macro_and_a_trailing_paste_is_dropped():
    files = {"a.fx": b"#define GET(c) x##NAME[c].c##Index\n#define TEX(t) c##t##\nfloat y;\n"}
    text = fxeffect.expand_includes(files.get, "a.fx")
    assert "#define GET(c) FX_CAT(FX_CAT(x, NAME)[c].c, Index)" not in text  # only the paste itself is wrapped
    assert "FX_CAT(x, NAME)" in text and "FX_CAT(c, Index)" in text
    assert re.search(r"#define TEX\(t\) FX_CAT\(c, t\)\s*$", text, re.M)
    assert "#define FX_CAT(a, b) FX_CAT_(a, b)" in fxeffect.prelude()


def test_the_prelude_marks_the_table_words_joins_semantics_and_declares_the_engines_samplers():
    p = fxeffect.prelude()
    assert "#define bindless __fx_bindless" in p and "#define root_constant __fx_root_constant" in p
    assert "#define TEXCOORD(index) TEXCOORD##index" in p
    assert "SamplerState cLinearClampSampler : register(s3);" in p
    assert "SamplerComparisonState cLinearClampCmpSampler : register(s11);" in p


def test_the_engines_intrinsics_header_comes_before_every_effect():
    files = {"data/shaders/include/intrinsics_dx.fxh": b"bool equal(float a, float b) { return a == b; }"}
    files["data/shaders/fx/x.fx"] = b"float4 F() { return 1; }"
    text = fxeffect.effect_source(files.get, "data/shaders/fx/x.fx", [], [])
    assert text.index("bool equal(float a") < text.index("float4 F()")


PREPROCESSED = """#line 1 "data/shaders/fx/demo.fx"
struct VSOut { float4 Position : SV_Position; [nointerpolation] uint Id : TEXCOORD3; };
Texture2D cTex : register(t11);
Texture2D cBindlessTextures[];
StructuredBuffer<float4> cBuf : register(t4, space2);
float4 Helper(float x) { return Later(x) * cTex.Load(int3(0, 0, 0)); }
float4 Later(float x, float y = 2.0) { return x * y; }
void Debug() { DebugPrint("hello"); }
float Broken() { return undefined.0; }
void vsMain(in float3 p : POSITION0, out VSOut o, uniform bool mirrored) { o.Position = float4(mirrored ? -p : p, 1); o.Id = 0; }
float4 psMain(VSOut i) : SV_Target0
{
@("#ifndef DISABLE_X")
    return Helper(1);
@("#endif")
}
[InputPrimitive(point)]
[OutputPrimitive(triangle)]
[MaxOutputVertices(4)]
void gsMain(VSOut input, out VSOut output) { APPEND_VERTEX(output, input[0]); }
renderstateblock sbPlain { CullMode = NONE; };
MaterialRef Demo;
technique Main
{
    PassthroughDefines = DISABLE_X, OTHER;
    RenderTarget = RTT_DEFAULT;
    pass p0 { SetStateBlock(sbPlain); SetVertexShader(vsMain(true)); SetPixelShader(psMain()); }
}
technique Shadow { pass p0 { SetVertexShader(vsMain(false)); SetGeometryShader(gsMain()); } }
"""


def test_split_takes_the_techniques_out_and_turns_the_dialect_into_hlsl():
    effect = fxeffect.split_effect(PREPROCESSED)
    assert [t.name for t in effect.techniques] == ["Main", "Shadow"]
    main = effect.techniques[0]
    assert main.passthrough == ("DISABLE_X", "OTHER")
    assert [(c.profile, c.function, c.args) for c in main.calls] == [
        ("vs_6_2", "vsMain", ("true",)),
        ("ps_6_2", "psMain", ()),
    ]
    hlsl = effect.hlsl
    assert "technique" not in hlsl and "renderstateblock" not in hlsl and "MaterialRef" not in hlsl
    assert hlsl.count("\n") == PREPROCESSED.count("\n")  # removed parts keep their lines
    assert "\n#ifndef DISABLE_X\n" in hlsl and "@(" not in hlsl  # deferred to the compiler's own preprocessor
    assert "nointerpolation uint Id" in hlsl
    assert re.search(
        r"DebugPrint\(PackDebugString\(uint4\(0x6c6c6568u, 0x0000006fu, 0x00000000u, 0x00000000u\)\)\)", hlsl
    )
    assert "[maxvertexcount(4)] void gsMain(point VSOut input[1], inout TriangleStream<VSOut> output)" in hlsl
    shadow = [c for _t, c in fxeffect.shader_calls(effect) if c.stage == "gs"]
    assert shadow and shadow[0].function == "gsMain"


def test_a_shader_gets_what_it_reaches_with_every_function_declared_first():
    effect = fxeffect.split_effect(PREPROCESSED)
    program = fxlink.Program(effect.hlsl)
    tech = effect.techniques[0]
    text, entry = program.entry_source(tech.calls[1], tech.passthrough)
    assert entry == "psMain"
    assert text.startswith("#define DISABLE_X\n#define OTHER\n")
    assert "Broken" not in text and "Debug()" not in text  # unreached: the game never compiles it
    assert "Texture2D cTex" in text and "struct VSOut" in text
    assert "register(t11)" not in text  # no bindings given: the compiler places them
    proto = text.index("float4 Later(float x, float y = 2.0);")  # the default stays on the declaration
    assert proto < text.index("float4 Helper(float x) {")  # Helper calls Later, defined after it in the source
    assert "float4 Later(float x, float y) {" in text  # and leaves the definition
    assert '#line 6 "data/shaders/fx/demo.fx"' in text  # each piece back at its file and line


def test_uniform_arguments_get_an_entry_of_their_own():
    effect = fxeffect.split_effect(PREPROCESSED)
    program = fxlink.Program(effect.hlsl)
    text, entry = program.entry_source(effect.techniques[0].calls[0])
    assert entry == "vsMain"  # the entry keeps the function's name, as the game's does: the function is renamed
    assert "void vsMain(in float3 p : POSITION0, out VSOut o)" in text
    assert "vsMain__true(p, o, true);" in text and "void vsMain__true(in float3 p" in text
    unused = fxeffect.ShaderCall("ps", "6_2", "psMain", ("CAMERA_MAIN",))  # arguments for no uniform: as it is
    assert program.entry_source(unused)[1] == "psMain"
    with pytest.raises(fxeffect.EffectError):
        program.entry_source(fxeffect.ShaderCall("ps", "6_2", "nowhere", ("1",)))


def test_a_function_or_structure_defined_twice_counts_once():
    hlsl = "struct S { float a; };\nstruct S { float a; };\nfloat F() { return 1; }\nfloat F() { return 1; }\nfloat4 E() : SV_Target { S s; s.a = F(); return s.a; }\n"
    text, _entry = fxlink.Program(hlsl).entry_source(fxeffect.ShaderCall("ps", "6_2", "E"))
    assert text.count("struct S {") == 1 and text.count("float F() {") == 1


BOUND = """#line 1 "data/shaders/include/constants.fxh"
__fx_shared __fx_global_table StructuredBuffer<float4> cCameraBuffer : register(t4);
__fx_shared __fx_global_table Texture2D cTerrainHeightTex : register(t11);
SamplerState cLinearWrapSampler : register(s4);
SamplerState cLinearClampSampler : register(s3);
Texture2D cZed;
Texture2D cAlpha;
__fx_root_constant cbuffer cbModel { uint cIndex; };
float4 psMain() : SV_Target0 { return cZed.Sample(cLinearWrapSampler, 0) + cAlpha.Sample(cLinearClampSampler, 0)
    + cTerrainHeightTex.Load(int3(0, 0, 0)) + cCameraBuffer[cIndex]; }
"""


def test_a_pass_binds_where_the_engine_does_and_declares_its_resources_in_its_order():
    program = fxlink.Program(BOUND)
    assert [r.name for r in program.resources({"psMain"})] == [
        "cCameraBuffer", "cTerrainHeightTex", "cZed", "cAlpha", "cbModel",
    ]  # fmt: skip
    binds = program.bindings({"psMain"})
    assert binds["cCameraBuffer"] == "register(t59, space0)" and binds["cTerrainHeightTex"] == "register(t17, space0)"
    assert binds["cAlpha"] == "register(t0, space1)" and binds["cZed"] == "register(t1, space1)"
    assert binds["cbModel"] == "register(b0, space1024)"
    text, _entry = program.entry_source(fxeffect.ShaderCall("ps", "6_2", "psMain"), (), binds)
    assert "__fx_" not in text and "cZed : register(t1, space1);" in text
    order = [text.index(n + " ") for n in ("cbModel", "cTerrainHeightTex", "cCameraBuffer", "cAlpha", "cZed")]
    assert order == sorted(order)  # root constants, then each table by register
    assert text.index("cLinearClampSampler :") < text.index("cLinearWrapSampler :")  # the samplers by name


def test_a_techniques_state_block_is_kept_with_its_states():
    text = """
float4 psGlass() : SV_Target { return 1; }
renderstateblock sbModelGlass
{
    CullMode = CCW;
    IndependentBlendEnable = TRUE;
    AlphaBlendEnable[0] = TRUE; SrcBlend[0] = ONE; DstBlend[0] = INV_SRC_ALPHA;
    ZWriteEnable = FALSE;
}
technique Transparent
{
    pass p0
    {
        SetStateBlock(sbModelGlass);
        SetVertexShader(vsGlass(CAMERA_JITTERED));
        SetPixelShader(psGlass(CAMERA_JITTERED));
    }
}
"""
    e = fxeffect.split_effect(text)
    t = e.techniques[0]
    assert t.state == "sbModelGlass" and "renderstateblock" not in e.hlsl
    states = e.state_of(t)
    assert (
        states["srcblend[0]"] == "ONE"
        and states["dstblend[0]"] == "INV_SRC_ALPHA"
        and states["zwriteenable"] == "FALSE"
    )


def test_matching_and_block_end_find_the_close_of_a_bracket_and_a_block():
    text = "a { b { c } d } ; e [ x [ y ] ] z"
    assert text[fxeffect.matching(text, text.index("["))] == "]"
    assert fxeffect.matching(text, text.index("["), "[]") == text.rindex("]")
    assert fxeffect.matching(text, 2, "{}") == text.rindex("}")
    assert fxeffect.block_end(text, 0) == text.index(";") + 1  # the ';' after a block belongs to it
    with pytest.raises(fxeffect.EffectError):
        fxeffect.block_end("a { b", 0)


def test_a_block_assigning_a_state_twice_keeps_the_first_and_lower_cases_the_names():
    got = fxeffect.assignments("CullMode = NONE; AlphaBlendEnable[0] = TRUE;\n#else\nCullMode = BACK; Other = 1.5;")
    assert got == {"cullmode": "NONE", "alphablendenable[0]": "TRUE", "other": "1.5"}
    effect = fxeffect.split_effect("renderstateblock sb { CullMode = CCW; CullMode = NONE; };\n")
    assert effect.blocks == {"sb": {"cullmode": "CCW"}}
