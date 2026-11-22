"""Anno 1701's models by its own Direct3D 9 shaders: the ``fx_2_0`` dialect and the state-block file read, the pass's
states and samplers, our pixel entry (the game's pixel shader, or the fixed-function stand-in, with the alpha test and
fog of the state block), the material's values, the light and shadow constants; with the installed game
(``ANNO1701_DIR``) the model effects compile with linking signatures and a house draws on Direct3D 11 unlike our
editor look, with its shadow. 1404's player colour: the dye effect binds its mask (``ANNO1404_DIR``)."""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from annotoolbox.formats import fx9effect, fxlink
from annotoolbox.models import shader1701
from annotoolbox.models.shader2070 import CNoPass
from annotoolbox.othergames import grannymodels
from annotoolbox.paths import REPO_ROOT

EFFECT = """
float4 vcLgtAmbient;
texture pcTexDiffuseColor;
sampler samplerTexDiffuseColor = sampler_state {texture = <pcTexDiffuseColor>;};
sampler samplerTexShadowMap : register(s3) = sampler_state {texture = <pcTexShadowMap>; minfilter = linear;};
void VS_Object(in float3 p : POSITION, out float4 oPosition : POSITION, out float4 oDiffuse : COLOR0,
               out float2 oTexCoord0 : TEXCOORD0, out float oFog : FOG, uniform int nRenderMode) { }
void PS_Caster(in float2 iTexCoord0 : TEXCOORD0, out float4 oColor : COLOR, out float oDepth : DEPTH,
               uniform int nRenderMode) { }
technique Object < string VertexDecl = "p3n3t2" ; >
{
    pass p0 < string StateBlock = "Object" ; >
    {
        Texture[0] = <pcTexDiffuseColor>;
        VertexShader = compile vs_1_1 VS_Object ( 0 ) ;
        PixelShader = NULL ;
    }
}
technique ObjectToShadowMap < string VertexDecl = "p3n3t2" ; >
{
    pass p0 < string StateBlock = "Caster" ; >
    {
        VertexShader = compile vs_1_1 VS_Object ( 0 ) ;
        PixelShader = compile ps_2_0 PS_Caster ( 1 ) ;
    }
}
"""
BLOCKS = """
Default
{
    ZENABLE = TRUE
    ZWRITEENABLE = TRUE
    CULLMODE = CCW // the engine's
    ZFUNC = LESSEQUAL
    FOGENABLE = TRUE
    ALPHAFUNC = GREATER
    MINFILTER[N] = ANISOTROPIC
    COLOROP[0] = MODULATE
    COLORARG1[0] = TEXTURE
}
Object
{
    ALPHAREF = 50
    ALPHATESTENABLE = TRUE
    COLORARG2[0] = DIFFUSE
    ALPHAOP[0] = SELECTARG1
    ALPHAARG1[0] = TEXTURE
}
Caster
{
    #include "Object"
    ZWRITEENABLE = FALSE
    ALPHABLENDENABLE = TRUE
    SRCBLEND = SRCALPHA
    DESTBLEND = INVSRCALPHA
    CULLMODE = NONE
    MINFILTER[3] = TSM_FILTER
    ADDRESSU[3] = CLAMP
}
"""


def test_the_dialect_reads_techniques_passes_and_their_state_blocks():
    found = fx9effect.split(EFFECT)
    lit = found.technique("Object")
    assert lit.format == "p3n3t2" and lit.passes[0].state_block == "Object"
    assert lit.passes[0].textures == {0: "pcTexDiffuseColor"}
    assert lit.passes[0].call("vs").function == "VS_Object" and lit.passes[0].call("vs").args == ("0",)
    assert lit.passes[0].call("ps") is None  # PixelShader = NULL: Direct3D 9's fixed-function stage
    caster = found.technique("ObjectToShadowMap").passes[0].call("ps")
    assert (caster.model, caster.function, caster.args) == ("2_0", "PS_Caster", ("1",))
    assert "technique" not in found.hlsl and found.hlsl.count("\n") == EFFECT.count("\n")
    samplers = fx9effect.samplers(found.hlsl)
    assert samplers["samplerTexDiffuseColor"].texture == "pcTexDiffuseColor"
    assert samplers["samplerTexShadowMap"].register == 3 and samplers["samplerTexShadowMap"].states == {
        "MINFILTER": "LINEAR"
    }


def test_a_state_block_changes_the_default_block_and_takes_its_includes():
    blocks = fx9effect.state_blocks(BLOCKS)
    caster = fx9effect.block_states(blocks, "Caster")
    assert caster["ALPHAREF"] == "50" and caster["ZWRITEENABLE"] == "FALSE" and caster["ZFUNC"] == "LESSEQUAL"
    assert fx9effect.block_states(blocks, "Unknown") == fx9effect.block_states(blocks, "Default")
    assert fx9effect.stage_state(caster, "MINFILTER", 3) == "TSM_FILTER"
    assert fx9effect.stage_state(caster, "MINFILTER", 0) == "ANISOTROPIC"  # the [N] value: every stage
    state = shader1701.pass_state(caster)
    assert (state.cull, state.depth_write, state.depth_func, state.blend) == ("none", False, "less_equal", True)
    assert (state.source, state.dest) == ("src_alpha", "inv_src_alpha")
    assert shader1701.pass_state(fx9effect.block_states(blocks, "Object")).cull == "back"  # D3D9's CCW
    shadow = shader1701.sampler_state(fx9effect.SSampler9("pcTexShadowMap", 3), caster)
    assert shadow.filter == "MIN_MAG_MIP_POINT" and shadow.address_u == "CLAMP"  # TSM_FILTER: point, its comment
    own = shader1701.sampler_state(fx9effect.SSampler9("t", None, {"MINFILTER": "LINEAR"}), caster)
    assert own.filter == "MIN_MAG_MIP_LINEAR" and own.address_u == "WRAP"


def test_our_pixel_entry_stands_in_for_the_fixed_function_stage_or_calls_the_game_shader():
    found = fx9effect.split(EFFECT)
    program = fxlink.CProgram(found.hlsl)
    outputs = [p for p in shader1701.params(program, "VS_Object") if p.direction == "out"]
    assert [p.semantic for p in outputs] == ["POSITION0", "COLOR0", "TEXCOORD0", "FOG0"]
    blocks = fx9effect.state_blocks(BLOCKS)
    lit = shader1701.pixel_wrapper(
        outputs, None, [], fx9effect.block_states(blocks, "Object"), "samplerTexDiffuseColor"
    )
    assert "in float4 v0 : SV_Position" in lit and "in float v3 : FOG0" in lit  # the vertex outputs as they are
    assert "c.rgb = (tex2D(samplerTexDiffuseColor, (v2).xy) * saturate(v1)).rgb;" in lit  # MODULATE texture, diffuse
    assert "c.a = (tex2D(samplerTexDiffuseColor, (v2).xy)).a;" in lit  # SELECTARG1 texture
    assert "if (c.a <= 0.196078) discard;" in lit  # ALPHAREF 50, GREATER
    assert "lerp(cFixedFogColor.rgb, c.rgb, saturate(v3))" in lit and "sampler samplerTexDiffuseColor;" in lit
    caster = found.technique("ObjectToShadowMap").passes[0].call("ps")
    inner = shader1701.params(program, "PS_Caster")
    text = shader1701.pixel_wrapper(outputs, caster, inner, fx9effect.block_states(blocks, "Caster"), "")
    assert "PS_Caster(v2, c, d, 1);" in text and "out float oDepth1701 : SV_Depth" in text
    assert "oDepth1701 = d;" in text and "sampler " not in text


def test_a_material_names_its_effect_and_keeps_its_values():
    assert shader1701.model_effect({"Shader": "data\\System\\FxShaders\\mesh\\default.fx"}).endswith("mesh/default.fx")
    for fx in ("data/system/fxshaders/mesh/waterfall.fx", "data/system/fxshaders/gui/icon_mesh.fx", ""):
        with pytest.raises(CNoPass):
            shader1701.model_effect({"Shader": fx})
    assert shader1701.model_effect({"Shader": "data\\system\\fxshaders\\mesh\\water.fx"}).endswith("mesh/water.fx")
    m = ET.fromstring("<m_Material><m_Name>01</m_Name><m_DiffuseColor>-6908266</m_DiffuseColor><m_Opacity>0.5</m_Opacity>"
                      "<m_Shader>data\\system\\fxshaders\\mesh\\unit_dye.fx</m_Shader>"
                      "<m_OpacityTexture>mask.tga</m_OpacityTexture></m_Material>")  # fmt: skip
    values = dict(grannymodels.material_values(m, lambda name: name and "data/" + name))
    assert values["Shader"] == "data/system/fxshaders/mesh/unit_dye.fx" and values["Opacity"] == 0.5
    assert values["Color.diffuse"] == pytest.approx((150 / 255, 150 / 255, 150 / 255, 1.0))  # 0xFF969696
    assert values["Texture.opacity"] == "data/mask.tga" and values["Texture.bump"] == ""
    assert set(shader1701.MAPS.values()) <= {k.split(".")[1] for k in values if k.startswith("Texture.")}


def test_1701s_water_scrolls_by_the_engines_clocks(qt):
    """shader-compile.md 13 "Water": ``water.fx`` scrolls its two bump layers by ``vcGlbTime2.y`` (a twentieth of the
    time), as ``constants.fx`` describes the clocks."""
    from annotoolbox.render import frame1701

    clocks = frame1701.time_values(25.0)
    assert clocks["vcGlbTime"] == [0.0, 0.5, 0.25, 0.5] and clocks["vcGlbTime2"] == [25.0, 1.25, 0.25, 0.025]


def test_1701s_water_sheets_are_switched_by_view_rendering_water(qt):
    """1701 draws a model's water sheet by its own shader, so its record lists Water and the switch takes the sheets
    out (the old engine's pass: the other groups stay)."""
    from types import SimpleNamespace as N

    from annotoolbox.render import frame1701, nativelook
    from annotoolbox.viewer import fxcpass1701

    assert nativelook.GAMES["anno1701"].features["water"] == nativelook.YES
    sheet, house = N(m_water=True, m_paths={}, m_maps={}), N(m_water=False, m_paths={}, m_maps={})
    rendered = []
    p = fxcpass1701.CFxcPass1701.__new__(fxcpass1701.CFxcPass1701)
    p.m_renderer = N(m_ctx=None, m_state=N(time=0.0))
    p.m_sky_stages, p.m_sky_broken = None, True
    p.m_frame = N(render=lambda target, size, found, *a: rendered.append(found), m_backdrop=None)
    p._split = lambda drawn: ([(k, None, {}, np.identity(4), m) for k, m in enumerate(drawn)], [], 0)
    p._ready = lambda: N(m_sun=frame1701.SLight1701((0.0, 1.0, 0.0)))
    camera = N(planes=lambda: (1.0, 100.0), eye=lambda: (0.0, 1.0, 5.0), m_target=(0.0, 0.0, 0.0), m_fov_rad=45.0)
    for switches, drawn in (({}, [sheet, house]), ({"water": False}, [house])):
        p.m_switches = switches
        p.draw([sheet, house], None, (8, 8), camera, N(sun=(0.0, 1.0, 0.0)))
        assert rendered.pop() == drawn


def test_a_structure_with_its_variable_is_one_declaration():
    program = fxlink.CProgram(
        "struct S { float4 a; } s;\nstruct T { float4 b; } t;\nfloat4 x;\nfloat4 f() { return s.a; }\n"
    )
    text = program.source({"f"})
    assert "} s;" in text.replace(" ;", ";") and "struct T" not in text and "float4 x" not in text


@pytest.fixture
def qt():
    pytest.importorskip("PySide6")


def test_1701s_light_and_shadow_constants(qt):
    from annotoolbox.render import frame1701
    from annotoolbox.render import gameshader as gs

    light = frame1701.read_light(
        "<North><AmbientColor><r>0.5</r>\n<g>0.25</g>\n<b>1</b></AmbientColor><FogStart>3200</FogStart></North>",
        "<m_LightInfo><m_Specular><r>0.1</r><g>0.2</g><b>0.3</b></m_Specular></m_LightInfo>",
    )
    assert light.ambient == (0.5, 0.25, 1.0) and light.specular == (0.1, 0.2, 0.3) and light.fog_start == 3200
    cam = gs.direct_camera((64, 64), (0.0, 5.0, 10.0), (0.0, 0.0, 0.0), 0.8, 1.0, 101.0)
    world = np.diag([2.0, 2.0, 2.0, 1.0])
    v = frame1701.light_values(light, cam, world)
    assert np.allclose(v["vcMtxWorldViewProj"], world.T @ cam.view @ cam.projection)  # ours, as 1404's
    assert v["vcLightingDir.vcLgtAmbient"] == [0.5, 0.25, 1.0, 1.0] and v["pcShdColor"][:3] == [0.5, 0.25, 1.0]
    assert v["vcGlbFogSEDC"][:2] == pytest.approx([100.0, 312.5])  # world units over the tile
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (0, 2) for z in (-1, 1)], float)
    cast, receive = frame1701.shadow_values((0.3, -1.0, 0.2), corners)
    assert np.allclose(receive["vcMtxShadowBias"], cast["vcMtxShadowBias"] @ frame1701.frame2070.TEXTURE)
    depth = [float(np.dot([*c, 1.0], cast["vcShdConstants"])) for c in corners]
    assert 0.0 < min(depth) and max(depth) < 1.0  # the casters' depth inside the map's range


def test_1701s_sky_dome_and_cloud_layers_as_its_terrain_draws_them(qt):
    """shader-compile.md 13, "Sky, clouds and post": the engine's half sphere, its own projection, the camera's turn
    without its place, the layers' scroll and turn by the time; the time of day from light.xml."""
    from annotoolbox.render import gameshader as gs
    from annotoolbox.render import sky1701
    from annotoolbox.render.frame1701 import SLight1701, read_light

    mesh = sky1701.dome()
    rows = np.frombuffer(mesh.m_data, "<f4").reshape(mesh.m_count, 5)
    assert mesh.m_count == 121 and len(mesh.m_indices) == 600 and rows[:, 1].min() >= -1e-6
    assert rows[0, 1] == pytest.approx(4.2) and rows[0, 4] == pytest.approx(0.0)  # the zenith: v 0
    assert np.linalg.norm(rows[-1, [0, 2]]) == pytest.approx(4.0) and rows[-1, 4] == pytest.approx(1.0)  # horizon
    light = read_light("", "<m_LightInfo><m_TimeOfDay>18.0</m_TimeOfDay></m_LightInfo>")
    assert light.time_of_day == 18.0
    near = gs.direct_camera((64, 32), (0.0, 5.0, 10.0), (0.0, 0.0, 0.0), 0.8, 1.0, 101.0)
    far = gs.direct_camera((64, 32), (100.0, 55.0, 110.0), (100.0, 50.0, 100.0), 0.8, 1.0, 101.0)
    sky = sky1701.sky_values(light, near)
    assert sky["vcUsrConstant1"][0] == 0.75 and np.allclose(sky["vcMtxWorldViewProj"],
                                                            sky1701.sky_values(light, far)["vcMtxWorldViewProj"])  # fmt: skip
    assert sky1701._projection(near)[1, 1] == pytest.approx(2.54048, abs=1e-4)  # 0.75 rad, whatever the view's
    still = sky1701.cloud_values(SLight1701((0.0, 1.0, 0.0)), near, 0.0, 0)
    centre = np.array([0.0, 0.0, 0.0, 1.0]) @ still["vcMtxWorld"]
    assert centre[:3] == pytest.approx((0.0, 13.0, 0.0))  # above the camera
    assert still["vcMtxTexture0"][2, :2] == pytest.approx((0.5, 0.5)) and still["vcMtxTexture0"][0, 0] == 4.0
    assert np.allclose(still["vcMtxTexture1"][:2, :2], np.identity(2)) and still["pcUsrConstant0"][3] == 0.5
    later = sky1701.cloud_values(SLight1701((0.0, 1.0, 0.0)), near, 100.0, 1)
    assert later["vcMtxTexture0"][2, 0] == pytest.approx(0.5 % 1.0 + 0.5) and later["vcMtxTexture0"][0, 0] == 2.0
    assert later["vcMtxTexture1"][0, 1] == pytest.approx(np.sin(4.0))  # 0.04 rad a second
    edge = np.array([1.0, 0.0, 0.0, 1.0]) @ later["vcMtxWorld"]
    assert np.linalg.norm(edge[[0, 2]]) == pytest.approx(128.0, rel=1e-3)


def test_a_pitched_camera_tilts_the_1701_cloud_layers_without_squashing_them(qt):
    """The layers turn about the camera's right axis laid level: looking down they keep their 256 x 256 (they were
    squashed by the cosine of the pitch, to a line straight down); a level camera's layers are as before."""
    from annotoolbox.render import gameshader as gs
    from annotoolbox.render import sky1701
    from annotoolbox.render.frame1701 import SLight1701

    light = SLight1701((0.0, 1.0, 0.0))
    for eye in ((0.0, 30.0, 1.0), (7.0, 2.0, 3.0), (10.0, 0.0, 3.0)):
        cam = gs.direct_camera((64, 32), eye, (0.0, 0.0, 0.0), 0.8, 1.0, 101.0)
        world = sky1701.cloud_values(light, cam, 0.0, 0)["vcMtxWorld"]
        turn = world[:3, :3] / np.array([[sky1701.CLOUD_SIZE], [1.0], [sky1701.CLOUD_SIZE]])  # the quad's size out
        assert np.allclose(turn @ turn.T, np.identity(3))
        right = np.asarray(cam.view)[:3, 0] * (1, 0, 1)
        assert np.allclose(right @ turn, right)  # the axis it turns about
    level = gs.direct_camera((64, 32), (0.0, 0.0, 10.0), (0.0, 0.0, 0.0), 0.8, 1.0, 101.0)
    c, s = np.cos(sky1701.TILT), np.sin(sky1701.TILT)
    assert np.allclose(sky1701.tilt(level)[:3, :3], [[1, 0, 0], [0, c, s], [0, -s, c]])


def test_the_old_engines_sky_reads_no_clouds_while_its_map_loads_or_with_the_clouds_off(qt, monkeypatch):
    """1404's clouds are the cube's alpha (2070's the volume's): its white stand-in drew a full overcast until the
    cube was in; Clouds off (1404 offers it now) leaves the cube out, the stand-in reads no density."""
    from types import SimpleNamespace

    from annotoolbox.models.fxstage import SState
    from annotoolbox.render import nativelook, sky2070
    from annotoolbox.viewer import fxcpass1404

    monkeypatch.setattr(sky2070, "CProgram", lambda vs, ps: None)
    monkeypatch.setattr(sky2070, "CFxcDraw", lambda *a: SimpleNamespace(m_fallbacks={}))
    dome = sky2070.CSkyDome(None, SimpleNamespace(vertex_stage=None, pixel_stage=None, state=SState()), None)
    assert dome.m_draw_call.m_fallbacks["cTexture0"][3] == 0 and dome.maps() == {}
    assert nativelook.GAMES["anno1404"].features["clouds"] == nativelook.YES
    p = fxcpass1404.CFxcPass1404.__new__(fxcpass1404.CFxcPass1404)
    p.m_switches, p.m_sky_stages, p.m_sky_broken = {"clouds": False}, object(), False
    p._cube = lambda path: "cube"
    frame = SimpleNamespace(m_sky=dome)
    p._sky(frame)
    assert frame.m_sky.m_cube is None
    p.m_switches = {}
    p._sky(frame)
    assert frame.m_sky.m_cube == "cube"


def test_the_old_engines_clouds_turn_with_the_time(qt):
    """-0.01 and -0.005 rad a second about the up axis, Direct3D's ``RotationY`` as the engine builds and uploads it
    (its memory as the shader matrix's columns, as for the sky cube's faces): the passes draw in the game's axes
    (shader-compile.md 11 "The game's own axes")."""
    from annotoolbox.render import sky2070

    assert np.allclose(sky2070.cloud_turns(0.0)["cMtxCloudRotate0"], np.identity(3))
    turned = sky2070.cloud_turns(100.0)
    for k, angle in ((0, -1.0), (1, -0.5)):
        m = turned[f"cMtxCloudRotate{k}"]
        assert m[1, 1] == 1.0 and m[0, 0] == pytest.approx(np.cos(angle)) and m[2, 0] == pytest.approx(-np.sin(angle))
        assert np.allclose(m, sky2070.rotation_y(angle).T)


def test_a_camera_in_the_games_axes_puts_every_point_where_the_views_camera_does():
    """The old engines' passes draw in the game's axes: the eye and the target mirrored back, the mirror in the
    projection; every clip position is the view's to the bit (shader-compile.md 11 "The game's own axes")."""
    from annotoolbox.render import gameshader as gs

    eye, target = (3.0, 7.0, 11.0), (1.0, 0.5, -2.0)
    view = gs.direct_camera((64, 32), eye, target, 0.8, 1.0, 101.0)
    game = gs.direct_camera((64, 32), eye, target, 0.8, 1.0, 101.0, use_game_axes=True)
    assert game.mirror and not view.mirror and game.eye == pytest.approx((-3.0, 7.0, 11.0))
    for p in ((2.0, 1.0, -3.0, 1.0), (-40.0, 0.25, 9.5, 1.0)):  # a point of the view's and the same of the game's
        point = np.array(p)
        assert np.array_equal(
            point @ view.view @ view.projection, (point * (-1, 1, 1, 1)) @ game.view @ game.projection
        )


def test_the_old_engines_draw_in_the_games_own_axes_and_read_their_maps_as_stored(qt, monkeypatch):
    """2070, 1404 and 1701 draw in the game's axes with the view's mirror in the projection (shader-compile.md 11
    "The game's own axes"): their cubes and 2070's noise volume go up as stored (2205's still mirrored), 1701's cloud
    quad as the engine places it; the camera and the sky's own projection carry the mirror, so their faces swap."""
    from types import SimpleNamespace as N

    from annotoolbox.render import gameshader as gs
    from annotoolbox.render import sky1701, sky2070
    from annotoolbox.render.frame1701 import SLight1701
    from annotoolbox.viewer import forwardpass, fxcpass, fxcpass1404, fxcpass1701

    for old in (fxcpass.CFxcPass, fxcpass1404.CFxcPass1404, fxcpass1701.CFxcPass1701):
        assert old.GAME_AXES and not old.MIRROR_CUBES
    assert forwardpass.CForwardPass.MIRROR_CUBES and not forwardpass.CForwardPass.GAME_AXES
    slice_ = np.arange(2 * 3 * 4, dtype=np.uint8).reshape(2, 3, 4)
    fake = N(level=lambda level, face, z: slice_, slices=lambda level: 2)
    monkeypatch.setattr(sky2070.ddsdecode, "CDdsFile", lambda raw: fake)
    got = sky2070.read_volume(N(read=lambda path: b""))
    assert len(got) == 2 and np.array_equal(got[0], slice_) and got[0].flags["C_CONTIGUOUS"]
    cam = gs.direct_camera((64, 32), (0.0, 5.0, 10.0), (0.0, 0.0, 0.0), 0.8, 1.0, 101.0, use_game_axes=True)
    world = sky1701.cloud_values(SLight1701((0.0, 1.0, 0.0)), cam, 0.0, 0)["vcMtxWorld"]
    assert (np.array([1.0, 0.0, 0.0, 1.0]) @ world)[0] > 100  # the quad's +x corner on the game's +x side
    assert sky1701._projection(cam)[0, 0] < 0 < sky1701._projection(replace(cam, mirror=False))[0, 0]
    monkeypatch.setattr(sky1701, "CFxcDraw", lambda *a: N(m_is_mirrored=False))
    monkeypatch.setattr(sky1701, "CProgram", lambda vs, ps: None)
    stages = N(vertex_stage=None, pixel_stage=None, state=None)
    sky = sky1701.CSky1701(None, stages, stages, None)
    assert all(layer.m_is_mirrored for layer in sky.m_layers) and sky.m_dome.m_is_mirrored


def test_a_cube_read_mirrored_and_as_stored_are_two_reads(qt, monkeypatch):
    """The audit of 2026-11-14: a cube's background read was keyed by its file alone, so a pass of the other mirror
    on the same renderer (1800 after 2205) took the faces of the first; the key names the mirror."""
    from types import SimpleNamespace as N

    from annotoolbox.render.loader import CLoader
    from annotoolbox.viewer import forwardpass, ownpass

    loader, made = CLoader(), []
    monkeypatch.setattr(ownpass.fxcdraw, "cube_faces", lambda dds, mirrored: ("faces", mirrored))
    monkeypatch.setattr(ownpass.fxcdraw, "cube_of", lambda ctx, faces: made.append(faces) or faces)
    monkeypatch.setattr(ownpass.ddsdecode, "CDdsFile", lambda raw: N(m_info=N(faces=6)))
    renderer = N(m_loader=loader, m_files=N(m_files=N(read=lambda path: b"")), m_ctx=None)
    try:
        for cls in (forwardpass.CForwardPass, forwardpass.CForwardPass1800):
            p = cls.__new__(cls)
            p.m_renderer, p.m_cubes = renderer, {}
            while p._cube("sky.dds") is None:
                loader.m_futures[next(iter(loader.m_futures))].result()
        assert made == [("faces", True), ("faces", False)]
    finally:
        loader.close()


def test_constant_buffers_of_one_name_but_two_layouts_stay_apart(qt):
    from types import SimpleNamespace as N

    from annotoolbox.render.gamedraw import CProgram

    def stage(*buffers):
        return N(reflection=N(cbuffers=[N(name=n, size=s, variables=[N(name=v, offset=o) for v, o in vs])
                                        for n, s, vs in buffers]))  # fmt: skip

    vs = stage(("$Globals", 64, [("vcMtxWorld", 0)]), ("cbShared", 16, [("a", 0)]))
    ps = stage(("$Globals", 16, [("cFixedFogColor", 0)]), ("cbShared", 16, [("a", 0)]))
    assert CProgram._buffer_names([vs, ps]) == [{"$Globals": "$Globals", "cbShared": "cbShared"},
                                               {"$Globals": "$Globals@1", "cbShared": "cbShared"}]  # fmt: skip


def _installed(var: str, game: str):
    root = os.environ.get(var)
    if not root or sys.platform != "win32":
        pytest.skip(f"needs an installed {game} ({var}) on Windows")
    from annotoolbox.infra import fxc

    try:
        return root, fxc.CFxc.find(None, library="d3dcompiler_43")
    except Exception as e:  # no compiler on this machine
        pytest.skip(str(e))


HOUSE = """
import json
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources import gametree
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import CModelFiles
from annotoolbox.viewer.modelrender import SViewState
from annotoolbox.viewer.modelview import rhi_picture
app = QApplication([])
tree = gametree.open_game("anno1701", Path(%r))
scene = modelscene.build(tree, %r, "anno1701")
out = {}
for name, look, off in (("editor", "editor", ()), ("shaders", "shaders", ()), ("flat", "shaders", ("shadows",)),
                        ("sky", "shaders", ("ground",)), ("nosky", "shaders", ("ground", "atmosphere"))):
    state = SViewState(look=look, particles=False, switches={k: False for k in off}, ground="ground" not in off)
    img = rhi_picture(CModelFiles(tree), scene, state, (200, 150), "d3d11", 600)
    out[name] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(200)] for y in range(150)], float).tolist()
print(json.dumps(out))
"""
HOUSE_MCF = "data/graphics/objects/buildings/cultures/chinese/chinese_houses/chinese_house01/chinese_house_01.mcf"


def test_the_installed_model_effects_compile_with_signatures_that_link():
    root, compiler = _installed("ANNO1701_DIR", "Anno 1701")
    from annotoolbox.sources import gametree

    shaders = shader1701.CSource1701(gametree.open_game("anno1701", Path(root)), compiler)
    for fx in ("default.fx", "normal_spec.fx", "unit_dye.fx", "morph_normal_spec.fx", "normal_env_fakecube.fx"):
        for technique in ("Object", "ShadowMapToObject", "ObjectToShadowMap"):
            got = shaders.model_pass({"Shader": f"data/system/fxshaders/mesh/{fx}"}, technique)
            assert [tuple(o) for o in got.vertex_stage.reflection.outputs] == [
                tuple(i) for i in got.pixel_stage.reflection.inputs
            ]
    lit = shaders.model_pass({"Shader": "data/system/fxshaders/mesh/normal_spec.fx"}, "Object")
    assert [i.semantic for i in lit.vertex_stage.inputs] == ["POSITION", "NORMAL", "TANGENT", "BINORMAL", "TEXCOORD0"]
    assert {b.name for b in lit.pixel_stage.reflection.bindings} >= {"samplerTexDiffuseColor", "samplerTexBump"}
    env = shaders.model_pass({"Shader": "data/system/fxshaders/mesh/normal_env_fakecube.fx"}, "Object")
    assert env.pixel_stage.types["samplerCubeTexReflection"] == "TextureCube"


def test_the_installed_house_draws_by_the_games_own_shaders():
    root, _compiler = _installed("ANNO1701_DIR", "Anno 1701")
    repo = REPO_ROOT
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": str(repo / "src")}
    done = subprocess.run([sys.executable, "-c", HOUSE % (root, HOUSE_MCF)], cwd=repo, env=env, capture_output=True,
                          text=True, timeout=900)  # fmt: skip
    if done.returncode != 0 and "Direct3D" in done.stderr:
        pytest.skip("no Direct3D 11 device")
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    import json

    got = {k: np.asarray(v) for k, v in json.loads(done.stdout.splitlines()[-1]).items()}
    assert got["shaders"].std() > 5.0  # the house drawn
    assert np.abs(got["shaders"] - got["editor"]).mean() > 0.5  # by other shaders than the editor look's
    assert np.abs(got["shaders"] - got["flat"]).max() > 30  # the house shadows itself
    top = slice(0, 15)  # above the house, the view's ground off: the game's sky dome over its fog colour
    assert np.abs(got["sky"][top] - got["nosky"][top]).mean() > 5


def test_the_installed_1404_dye_effect_binds_its_mask_and_the_object_colour():
    root, compiler = _installed("ANNO1404_DIR", "Anno 1404")
    from annotoolbox.models import shader2070
    from annotoolbox.sources import gametree

    pytest.importorskip("PySide6")
    from annotoolbox.render import frame1404

    shaders = shader2070.CSource2070(gametree.open_game("anno1404", Path(root)), compiler, shader2070.GLOBALS_1404)
    got = shaders.effect_pass(frame1404.DYE_FX, frame1404.material_defines("d", ""), frame1404.TECHNIQUE)
    assert "cTexture4" in {b.name for b in got.pixel_stage.reflection.bindings}
    members = {v.name for c in got.pixel_stage.reflection.cbuffers for v in c.variables}
    assert "cObjectColor" in members


def test_the_installed_1404_water_effect_refracts_the_scene_and_reads_the_sky_cube():
    """shader-compile.md 12 "Model water": ``model_water.fx`` with the engine's refraction define reads the copies of
    the scene and its depth, the sky cube and the clock; the basin's sheet is a water material."""
    root, compiler = _installed("ANNO1404_DIR", "Anno 1404")
    from annotoolbox.models import shader2070
    from annotoolbox.sources import gametree

    pytest.importorskip("PySide6")
    from annotoolbox.render import frame1404

    tree = gametree.open_game("anno1404", Path(root))
    shaders = shader2070.CSource2070(tree, compiler, shader2070.GLOBALS_1404)
    values = {"Owner": "MODEL", "ShaderIndex": 1, "VertexFormat": "p3n3g3b3t2"}
    defines = (*shader2070.material_defines(values), *frame1404.WATER_DEFINES)
    for technique in (frame1404.TECHNIQUE, frame1404.SHADOWED):
        got = shaders.effect_pass(frame1404.WATER_FX, defines, technique)
        names = {b.name for b in got.pixel_stage.reflection.bindings}
        assert {"cWaterRefractionMap", "cWaterDepthMap", "tSkyEnv", "cTexture0"} <= names
    members = {v.name for c in got.vertex_stage.reflection.cbuffers for v in c.variables}
    assert {"cTimer", "cMtxView"} <= members
    basin = tree.read("addondata/graphics/buildings/ornament/north/waterbassin/n_waterbassin_small.cfg")
    assert b"<m_ShaderIndex>1</m_ShaderIndex>" in basin
