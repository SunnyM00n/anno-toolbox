"""The engine's structured buffers as raw buffers (``render.rawbuffers``): a structure's packing, reads, a member's
assignment and an atomic on a member, as the shadow analysis (``shadow.fx``) uses them. Text only, no compiler."""

from __future__ import annotations

from annotoolbox.render import gameshader as gs
from annotoolbox.render import rawbuffers

SHADOW = """
struct ShadowZBounds
{
    uint  MinZVS;
    uint  MaxZVS;
    uint2 Padding;
    int   MinX[4];
    int   MaxX[4];
};
struct ShadowInformation
{
    float4x4 ShadowMatrix;
    float4x4 ShadowMatrixInv;
    float4   DepthTransform;
    float3   MinLS;
    float    SplitDistance;
};
globallycoherent RWStructuredBuffer<ShadowZBounds> cZBoundsOut;
RWStructuredBuffer<ShadowInformation> cShadowInformationUAV;
StructuredBuffer<ShadowInformation> cShadowInformation;
void Clear() { cZBoundsOut[0].MinX[i] = maxVal; }
void Reduce() { InterlockedMin(cZBoundsOut[0].MinX[i], int(floor(v))); InterlockedMax(cZBoundsOut[0].MaxZVS, asuint(z)); }
float Split(uint p) { return cShadowInformationUAV[p].SplitDistance + asfloat(cZBoundsOut[0].MinZVS); }
float4 Pos(float4 w) { return mul(w, cShadowInformation[k].ShadowMatrix); }
"""


def test_a_structures_packing_has_no_sixteen_byte_rows():
    size, members = rawbuffers.layout(SHADOW, "ShadowZBounds")
    assert size == 48 and members["Padding"].offset == 8 and members["MinX"].offset == 16
    assert members["MaxX"].offset == 32 and members["MaxX"].size == 4 and members["MaxX"].count == 4
    size, members = rawbuffers.layout(SHADOW, "ShadowInformation")
    assert size == 160 and members["MinLS"].offset == 144 and members["SplitDistance"].offset == 156


def test_reads_writes_and_atomics_go_through_the_raw_buffer():
    h = rawbuffers.rewrite(SHADOW, ("cShadowInformation",))
    assert "globallycoherent RWByteAddressBuffer cZBoundsOut;" in h
    assert "RWByteAddressBuffer cShadowInformationUAV;" in h and "RWByteAddressBuffer cShadowInformation;" in h
    assert "StructuredBuffer" not in h
    assert ("{ ShadowZBounds rw_ = cZBoundsOut.Load<ShadowZBounds>((uint)(0) * 48); rw_.MinX[i] = maxVal; "
            "cZBoundsOut.Store<ShadowZBounds>((uint)(0) * 48, rw_); }") in h  # fmt: skip
    assert "cZBoundsOut.InterlockedMin((uint)(0) * 48 + 0 + 16 + (i) * 4, int(floor(v)));" in h
    assert "cZBoundsOut.InterlockedMax((uint)(0) * 48 + 0 + 4, asuint(z));" in h
    assert "cShadowInformationUAV.Load<ShadowInformation>((uint)(p) * 160).SplitDistance" in h
    assert "asfloat(cZBoundsOut.Load<ShadowZBounds>((uint)(0) * 48).MinZVS)" in h
    assert "mul(w, cShadowInformation.Load<ShadowInformation>((uint)(k) * 160).ShadowMatrix)" in h


SKIN = """
StructuredBuffer<float4x3> cCurrBoneMatrixBuffer;
StructuredBuffer<float4x3> cLastBoneMatrixBuffer;
StructuredBuffer<uint4> cModelPerFrameDataBuffer;
StructuredBuffer<float4> cOther;
float4x3 Bone(uint i, StructuredBuffer<float4x3> boneMatrixBuffer) { return boneMatrixBuffer[i] * 0.5; }
float4 Keep(StructuredBuffer<float4> list) { return list[0]; }
float4x3 Rigid(uint4 w) { return cCurrBoneMatrixBuffer[w.z]; }
float4x3 Last(uint i) { return Bone(i, cLastBoneMatrixBuffer); }
uint4 Word(uint i) { return cModelPerFrameDataBuffer[cOffset + i]; }
"""


def test_the_frames_instance_buffers_are_read_raw_a_matrix_by_its_columns_a_parameter_alike():
    """The island view's model shaders read the frame's instance buffers raw (``gameshader.adapt(frame=True)``): a
    ``float4x3`` read as the three columns a structured buffer stores (``-Zpc``), a function parameter of a buffer
    type that only raw buffers are passed to as a raw buffer; a parameter another buffer is passed to stays."""
    h = rawbuffers.rewrite(SKIN, gs.FRAME_INSTANCES)
    loader = rawbuffers.matrix_loader("float4x3")
    assert h.startswith(loader) and h.count("float4x3 rb_float4x3(") == 1
    assert "b.Load<float4>(a + 0), b.Load<float4>(a + 16), b.Load<float4>(a + 32)" in loader
    assert "transpose(float3x4(" in loader
    assert (
        "float4x3 Bone(uint i, RWByteAddressBuffer boneMatrixBuffer) { return rb_float4x3(boneMatrixBuffer, (uint)(i) * 48) * 0.5; }"
        in h
    )
    assert "return rb_float4x3(cCurrBoneMatrixBuffer, (uint)(w.z) * 48);" in h
    assert "cModelPerFrameDataBuffer.Load<uint4>((uint)(cOffset + i) * 16)" in h
    assert "float4 Keep(StructuredBuffer<float4> list)" in h  # cOther is no raw buffer
    plain = gs.adapt(SKIN, "Word").hlsl  # the viewer's: constant-buffer arrays as before
    assert "cbuffer cb_cCurrBoneMatrixBuffer { float4x3 cCurrBoneMatrixBuffer[256]; };" in plain
    assert (
        "rb_float4x3" not in plain
        and "RWByteAddressBuffer cModelPerFrameDataBuffer;" in gs.adapt(SKIN, "Word", frame=True).hlsl
    )


def test_the_adapter_reads_the_frames_raw_buffers_and_leaves_the_rest_constant_buffers():
    h = gs.adapt(SHADOW + "StructuredBuffer<float4> cOther;\n", "Pos").hlsl
    assert "RWByteAddressBuffer cShadowInformation;" in h  # gameshader.RAW_BUFFERS
    assert "cbuffer cb_cOther { float4 cOther[16]; };" in h
