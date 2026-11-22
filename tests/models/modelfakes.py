"""Stand-ins for the model files: the config texts of the bakery, trees, billboards, bone links and documents,
particles and mesh effects, and the shader install."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from annotoolbox.models.particles2070 import MODEL as PARTICLES_MODEL
from annotoolbox.models.particles2070 import PARTICLE, parse_efc
from annotoolbox.sources.overlay import CSourceTree


BAKERY_CFG = """<m_Config><m_Files><m_Config><m_FileName>data\\graphics\\props\\tree\\tree.cfg</m_FileName>
<m_ConfigType>FILE</m_ConfigType>
<m_Transformer><m_Config><m_Position.x>5.000000</m_Position.x>
<m_Position.y>0.000000</m_Position.y>
<m_Position.z>0.000000</m_Position.z>
<m_Scale>0.700000</m_Scale>
<m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType>
</m_Config>
</m_Transformer>
</m_Config>
</m_Files>
<m_Models><m_Config><m_FileName>data\\graphics\\buildings\\bakery\\rdm\\bakery_lod0.rdm</m_FileName>
<m_ConfigType>MODEL</m_ConfigType>
<m_Animations><m_Config><m_FileName>data\\graphics\\buildings\\bakery\\anim\\bakery_work.rdm</m_FileName>
<m_ConfigType>ANIMATION</m_ConfigType>
</m_Config>
</m_Animations>
<m_Materials><m_Config><m_Name>xx</m_Name>
<m_ShaderIndex>10</m_ShaderIndex>
<m_DiffuseColor.r>1.000000</m_DiffuseColor.r>
<m_DiffuseColor.g>0.500000</m_DiffuseColor.g>
<m_DiffuseColor.b>0.250000</m_DiffuseColor.b>
<m_DiffuseColor.a>1.000000</m_DiffuseColor.a>
<m_DiffuseTexture>data\\graphics\\buildings\\bakery\\maps\\bakery_diff.png</m_DiffuseTexture>
<m_NormalTexture>data\\graphics\\buildings\\bakery\\maps\\bakery_norm.png</m_NormalTexture>
<m_EnvironmentTexture></m_EnvironmentTexture>
<m_HeightTexture></m_HeightTexture>
<m_MaskTexture>data\\graphics\\buildings\\bakery\\maps\\bakery_mask.png</m_MaskTexture>
<m_RipplesTexture></m_RipplesTexture>
<m_VertexFormat>P4h_N4b_G4b_B4b_T2h</m_VertexFormat>
<m_DiffuseEnabled>1</m_DiffuseEnabled>
<m_NormalEnabled>1</m_NormalEnabled>
<m_EnvironmentEnabled>0</m_EnvironmentEnabled>
<m_ParallaxEnabled>0</m_ParallaxEnabled>
<m_RipplesEnabled>0</m_RipplesEnabled>
<m_NumBonesPerVertex>0</m_NumBonesPerVertex>
<m_ConfigType>MATERIAL</m_ConfigType>
</m_Config>
</m_Materials>
<m_Transformer><m_Config><m_Position.x>1.000000</m_Position.x>
<m_Position.y>2.000000</m_Position.y>
<m_Position.z>3.000000</m_Position.z>
<m_Rotation.x>0.000000</m_Rotation.x>
<m_Rotation.y>0.000000</m_Rotation.y>
<m_Rotation.z>0.000000</m_Rotation.z>
<m_Rotation.w>1.000000</m_Rotation.w>
<m_Scale>1.000000</m_Scale>
<m_Conditions>0</m_Conditions>
<m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType>
</m_Config>
</m_Transformer>
</m_Config>
</m_Models>
<m_Effects><m_Config><m_FileName>data\\graphics\\effects\\particles\\fire_01.efc</m_FileName>
<m_ConfigType>EFFECT</m_ConfigType>
</m_Config>
</m_Effects>
<m_TerrainDecals><m_Config><m_Extend.x>2.000000</m_Extend.x>
<m_Extend.y>1.500000</m_Extend.y>
<m_TexCoords.x>0.000000</m_TexCoords.x>
<m_TexCoords.y>0.250000</m_TexCoords.y>
<m_TexCoords.z>1.000000</m_TexCoords.z>
<m_TexCoords.w>1.000000</m_TexCoords.w>
<m_ConfigType>TERRAINDECAL</m_ConfigType>
<m_Materials><m_Config><m_Name></m_Name>
<m_ShaderIndex>0</m_ShaderIndex>
<m_DiffuseTexture>data\\graphics\\buildings\\bakery\\maps\\bakery_ground.png</m_DiffuseTexture>
<m_ConfigType>MATERIAL</m_ConfigType>
</m_Config>
</m_Materials>
</m_Config>
</m_TerrainDecals>
<m_Sequences><m_Config><m_SequenceID>5000</m_SequenceID>
<m_ConfigType>SEQUENCE</m_ConfigType>
</m_Config>
</m_Sequences>
<m_RenderPropertyFlags>131712</m_RenderPropertyFlags>
<m_Center.x>0.000000</m_Center.x>
<m_Center.y>3.355972</m_Center.y>
<m_Center.z>-0.001022</m_Center.z>
<m_Extent.x>2.000000</m_Extent.x>
<m_Extent.y>3.384575</m_Extent.y>
<m_Extent.z>1.572772</m_Extent.z>
<m_Radius>4.234260</m_Radius>
<m_ConfigType>ROOT</m_ConfigType>
</m_Config>"""


TREE_CFG = """<m_Config><m_Models><m_Config><m_FileName>data\\graphics\\props\\tree\\rdm\\tree.rdm</m_FileName>
<m_ConfigType>MODEL</m_ConfigType>
<m_Materials><m_Config><m_Name>leaves</m_Name>
<m_ShaderIndex>4</m_ShaderIndex>
<m_DiffuseTexture>tools\\bobnet\\data\\default_diff.png</m_DiffuseTexture>
<m_VertexFormat>P4h_N4b_T2h</m_VertexFormat>
<m_ConfigType>MATERIAL</m_ConfigType>
</m_Config>
</m_Materials>
</m_Config>
</m_Models>
<m_Files><m_Config><m_FileName>data\\graphics\\buildings\\bakery\\bakery.cfg</m_FileName>
<m_ConfigType>FILE</m_ConfigType>
</m_Config>
</m_Files>
<m_ConfigType>ROOT</m_ConfigType>
</m_Config>"""


def cluster_cfg(count: int) -> str:
    cards = []
    for i in range(count):
        s = f"{i:x}"  # 2070 numbers them in hex: 0..9, a..f, then 10 for the seventeenth
        cards.append(
            f"<m_BBPosX{s}>{i}.0</m_BBPosX{s}><m_BBPosY{s}>2.0</m_BBPosY{s}><m_BBPosZ{s}>0.5</m_BBPosZ{s}>"
            f"<m_BBSize{s}>1.0</m_BBSize{s}>"
            f"<m_BBTexX{s}>0.0</m_BBTexX{s}><m_BBTexY{s}>0.0</m_BBTexY{s}>"
            f"<m_BBTexZ{s}>0.5</m_BBTexZ{s}><m_BBTexW{s}>0.5</m_BBTexW{s}>"
        )
    return (
        "<m_Config><m_ConfigType>ROOT</m_ConfigType>"
        "<m_InstancingBBClusters><m_Config><m_ConfigType>INSTANCING_BBCLUSTER</m_ConfigType>"
        f"<m_BBClusterSize>{count}</m_BBClusterSize>{''.join(cards)}"
        "<m_ColorCount>2</m_ColorCount>"
        "<m_ColorR0>0.5</m_ColorR0><m_ColorG0>0.6</m_ColorG0><m_ColorB0>0.4</m_ColorB0><m_ColorA0>1.0</m_ColorA0>"
        "<m_ColorR1>0.5</m_ColorR1><m_ColorG1>0.6</m_ColorG1><m_ColorB1>0.4</m_ColorB1><m_ColorA1>1.0</m_ColorA1>"
        "<m_Transformer><m_Config><m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType>"
        "<m_Scale>1.5</m_Scale></m_Config></m_Transformer>"
        "</m_Config></m_InstancingBBClusters></m_Config>"
    )


POS = (
    "<m_Config><m_Position.x>1</m_Position.x><m_Position.y>2</m_Position.y><m_Position.z>3</m_Position.z>"
    "<m_Rotation.w>1</m_Rotation.w><m_Scale>1</m_Scale><m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType></m_Config>"
)


SHOWN = "<m_Config><m_Conditions>{bits}</m_Conditions><m_ConfigType>VISIBILITY_TRANSFORM</m_ConfigType></m_Config>"


BONELINKS_CFG = """<m_Config><m_Center.x>0</m_Center.x><m_Center.y>0</m_Center.y><m_Center.z>0</m_Center.z>
<m_Extent.x>1</m_Extent.x><m_Extent.y>1</m_Extent.y><m_Extent.z>1</m_Extent.z><m_Radius>1</m_Radius>
{body}<m_ConfigType>ROOT</m_ConfigType></m_Config>"""


def bonelinks_light(*transforms):
    return f"<m_Config><m_Type>2</m_Type><m_ConfigType>LIGHT</m_ConfigType><m_Transformer>{POS}{''.join(transforms)}</m_Transformer></m_Config>"


def bonelinks_file(name, *transforms):
    return (
        f"<m_Config><m_FileName>{name}</m_FileName><m_ConfigType>FILE</m_ConfigType>"
        f"<m_Transformer>{POS}{''.join(transforms)}</m_Transformer></m_Config>"
    )


def bonelinks_sequence(sid, looped, conditions=(), tracks=()):
    inner = "".join(
        f"<Track><TrackID>{i}</TrackID><TrackElement><Type>2</Type><m_StartTime>{a}</m_StartTime>"
        f"<m_EndTime>{b}</m_EndTime><m_Condition>{bits}</m_Condition></TrackElement></Track>"
        for i, (a, b, bits) in enumerate(conditions, start=1)
    )
    return (
        f"<m_Config><m_SequenceID>{sid}</m_SequenceID><m_Looped>{looped}</m_Looped><m_Speed>1</m_Speed>{inner}"
        "<m_ConfigType>SEQUENCE</m_ConfigType></m_Config>"
    )


NL = "\r\n"


# 117, 1800 and 2205: ConfigType first, group headings in a material, a tag suffix, Anno's </>
NEW_CFGDOC = NL.join([
    "<Config><ConfigType>MAIN</ConfigType>", "<Files></Files>",
    "<Models><Config><ConfigType>MODEL</ConfigType>", "<Name>house</Name>", "<FileName>data\\gfx\\house.fbx</FileName>",
    "<Materials><Config><ConfigType>MATERIAL</ConfigType>",
    "<Common [ModelCommon]>Common [ModelCommon]</Common [ModelCommon]>",
    "<cModelDiffTex>data/gfx/wall_diff.psd</cModelDiffTex>", "<DIFFUSE_ENABLED>1</DIFFUSE_ENABLED>",
    "<cDiffuseColor.r>1</cDiffuseColor.r>", "</Config>", "</Materials>", "</Config>", "</Models>",
    "<Particles><Config><ConfigType>PARTICLE</ConfigType>", "<DistanceScaleEndScale;>1</>", "</Config>", "</Particles>",
    "<Sequences><Config><ConfigType>SEQUENCE</ConfigType>", "<SequenceID>100691457</SequenceID>", "<Speed>1</Speed>",
    "<Track><TrackID>1</TrackID>", "<TrackElement><Type>0</Type>", "<StartTime>0</StartTime>",
    "<EndTime>2000</EndTime>", "<ModelID>0</ModelID>", "<AnimationID>0</AnimationID>", "</TrackElement>", "</Track>",
    "</Config>", "</Sequences>", "</Config>", "",
]).encode("latin-1")  # fmt: skip


# 2070 and 1404: m_ prefixes, ConfigType last, LF here
OLD_CFGDOC = "\n".join([
    "<m_Config><m_Models><m_Config><m_FileName>data\\graphics\\a_lod0.rdm</m_FileName>",
    "<m_ConfigType>MODEL</m_ConfigType>", "<m_Transformer><m_Config><m_Position.x>0.5</m_Position.x>",
    "<m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType>", "</m_Config>", "</m_Transformer>", "</m_Config>",
    "</m_Models>", "<m_ConfigType>ROOT</m_ConfigType>", "</m_Config>", "",
]).encode()  # fmt: skip


def parse_cfg_xml(text: str) -> ET.Element:
    """117 cfg files carry group tags like ``<Common [ModelCommon]>`` that are
    not well-formed XML; strip them so ElementTree can check the structure."""
    import re

    cleaned = re.sub(r"</?[A-Za-z]+ \[[A-Za-z]+\]>", "", text)
    return ET.fromstring(cleaned)


IDENTITY_MATRIX = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


MESH = "data/graphics/effects/particles/rdm/beam.rdm"


TEXTURE = "data/graphics/effects/particles/maps/fx.png"


def curve(name, lo=0.0, hi=1.0, default=0.0, var=0.0, knots=()):
    """A curve's tags as an .efc stores them: min, max, default, variance and ``(pos, x)`` knots."""
    out = f"<{name}ValueMin>{lo}</{name}ValueMin><{name}ValueMax>{hi}</{name}ValueMax>"
    out += f"<{name}ValueDef>{default}</{name}ValueDef><{name}Varianz>{var}</{name}Varianz>"
    out += f"<{name}Size>{len(knots)}</{name}Size>"
    for i, (pos, x) in enumerate(knots):
        out += f"<{name}KnotPos_{i:x}>{pos}</{name}KnotPos_{i:x}><{name}KnotX_{i:x}>{x}</{name}KnotX_{i:x}>"
    return out


def efc(index=0, kind=PARTICLE, technique=0, loop=1, life=1000, duration=1000, delay=0, extra="", pep=100.0):
    pep_curve = curve("PEP", 0.0, 1000.0, pep)
    return (
        f"<Effect{index}><Texture0>{TEXTURE}</Texture0><Name>e{index}</Name><EffectType>{kind}</EffectType>"
        f"<Technique>{technique}</Technique><Delay>{delay}</Delay><LifeTime>{life}</LifeTime>"
        f"<Duration>{duration}</Duration><Loop>{loop}</Loop><Bound>1</Bound>"
        f"<OffsetX>0</OffsetX><OffsetY>0.5</OffsetY><OffsetZ>0</OffsetZ>{pep_curve}"
        f"<AtlasTilingU>0.5</AtlasTilingU><AtlasTilingV>0.25</AtlasTilingV>"
        f"<AtlasOffsetU>0.25</AtlasOffsetU><AtlasOffsetV>0</AtlasOffsetV><Alignment>0</Alignment>"
        f"{extra}</Effect{index}>"
    )


def mesh_emitter(pep=0.0, loop=1, extra="", technique=1, life=6000, duration=200, model=MESH, scroll=600):
    tags = f"<Model>{model}</Model><TexOffset>{scroll}</TexOffset>{extra}"
    return efc(kind=PARTICLES_MODEL, technique=technique, loop=loop, life=life, duration=duration, pep=pep, extra=tags)


def first(text):
    return parse_efc(text)[0]


PARTICLE_MODEL_CFG = """<m_Config><m_RenderPropertyFlags>0</m_RenderPropertyFlags>
<m_Center.x>0</m_Center.x><m_Center.y>0</m_Center.y><m_Center.z>0</m_Center.z>
<m_Extent.x>1</m_Extent.x><m_Extent.y>1</m_Extent.y><m_Extent.z>1</m_Extent.z><m_Radius>1</m_Radius>
<m_Effects>
{effects}
</m_Effects>
<m_Sequences>{sequences}</m_Sequences>
<m_ConfigType>ROOT</m_ConfigType></m_Config>"""


EFFECT_CFG = """<m_Config><m_FileName>{file}</m_FileName><m_ConfigType>EFFECT</m_ConfigType>
<m_Transformer><m_Config><m_Position.x>1</m_Position.x><m_Position.y>2</m_Position.y><m_Position.z>3</m_Position.z>
<m_Rotation.x>0</m_Rotation.x><m_Rotation.y>0</m_Rotation.y><m_Rotation.z>0</m_Rotation.z><m_Rotation.w>1</m_Rotation.w>
<m_Scale>1</m_Scale><m_Conditions>0</m_Conditions><m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType></m_Config>{more}
</m_Transformer></m_Config>"""


def particle_sequence(sid, looped, elements):
    inner = "".join(
        f"<Track><TrackID>{i + 1}</TrackID><TrackElement><Type>{t}</Type><m_StartTime>{a}</m_StartTime>"
        f"<m_EndTime>{b}</m_EndTime><m_EffectID>{v}</m_EffectID><m_Condition>{v}</m_Condition></TrackElement></Track>"
        for i, (t, a, b, v) in enumerate(elements)
    )
    return (
        f"<m_Config><m_SequenceID>{sid}</m_SequenceID><m_Looped>{looped}</m_Looped><m_Speed>1</m_Speed>{inner}"
        "<m_ConfigType>SEQUENCE</m_ConfigType></m_Config>"
    )


def particle_tree(tmp_path, files):
    root = tmp_path / "src" / "Data1"
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return CSourceTree(tmp_path / "src")


SHADER_EFFECT = """
#line 1 "test.fx"
shared cbuffer cbMeshConstants { float4 cMtxMeshWorld[256]; };
Texture2D cTexture0; SamplerState sTexture0 { Filter = ANISOTROPIC; AddressU = CLAMP; AddressV = CLAMP; MaxAnisotropy = 16; };
struct VSIn { float3 Position : SV_Position; float2 TexCoord : TEXCOORD0; };
struct VSOut { float4 Position : SV_Position; float2 TexCoord : TEXCOORD0; };
void vs_T(in VSIn i, out VSOut o, uniform float Scale = 1,
#line 9
          uniform float Bias = 0)
{ o.Position = float4(i.Position * Scale + Bias, 1); o.TexCoord = i.TexCoord; }
float4 ps_T(in VSOut i) : SV_Target0 { return cTexture0.Sample(sTexture0, i.TexCoord) * cMtxMeshWorld[0]; }
RasterizerState rs { CullMode = NONE; };
DepthStencilState ds { DepthEnable = FALSE; DepthWriteMask = ZERO; };
BlendState bs { BlendEnable[0] = TRUE; SrcBlend = SRC_ALPHA; DestBlend = INV_SRC_ALPHA; };
vertexshader cVS = CompileShader(vs_5_0, vs_T(1, 0));
technique11 Deferred <string format = "P3T2";>
{
    pass p0
    {
        SetRasterizerState(rs);
        SetDepthStencilState(ds, 0);
        SetBlendState(bs, float4(0, 0, 0, 0), 0xffffffff);
        SetVertexShader(cVS);
        SetGeometryShader(NULL);
        SetPixelShader(CompileShader(ps_5_0, ps_T()));
    }
}
"""
