"""Anno 2070's frame parts beyond the lighting: the light volumes and their rows as the light renderer feeds them, the
shadow's fitted constants, the sky dome, the post sequence's steps, the effect states and vertex inputs the older
effects write, model water's colour and depth, the cloth's vertices and variant; with the installed game
(``ANNO2070_DIR``) the parts' techniques compile, a casino by the game's own shaders on Direct3D 11 gains its lights,
its shadow, its sky and its bloom, a fountain its water and a flagpole its banners."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.models import cfg2070, fxstage, shader2070
from annotoolbox.render import cloth2070, frame2070, lights2070, post2070, sky2070
from annotoolbox.render import gameshader as gs
from annotoolbox.render.lightsforward import Lamp


def test_the_point_lights_hemisphere_encloses_the_unit_sphere_and_faces_the_camera():
    verts, index = lights2070.hemisphere()
    assert verts.shape == (35, 3) and len(index) % 3 == 0
    assert verts[0].tolist() == pytest.approx([0, 0, lights2070.RING_SCALE])  # the apex towards the camera
    tri = verts[index.reshape(-1, 3)]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    keep = np.linalg.norm(normals, axis=1) > 1e-6
    n = normals[keep] / np.linalg.norm(normals[keep], axis=1)[:, None]
    planes = np.einsum("ij,ij->i", n, tri[keep, 0])
    assert np.all(
        np.abs(planes) >= 0.98
    )  # every face within 2 % of the radius: the engine's polygon all but encloses it


def test_a_strip_becomes_a_list_with_one_winding_and_no_degenerate_triangles():
    assert lights2070.strip_to_list([0, 1, 2, 3, 3, 4]).tolist() == [0, 1, 2, 2, 1, 3]


def test_the_spot_cone_has_its_sides_then_its_base():
    verts, index, sides = lights2070.cone()
    assert verts.shape == (11, 3) and verts[10].tolist() == [0, 0, 0]
    assert sides == 30 and len(index) == 54 and set(index[:sides].reshape(-1, 3)[:, 0]) == {10}
    assert np.allclose(verts[:10, 1], 1.0)


def test_the_engines_rule_picks_sprites_meshes_and_the_spots_whose_base_shows():
    eye = (0.0, 0.0, 0.0)
    small = Lamp((0, 0, -20), 1.0, (1, 1, 1))
    near = Lamp((0, 0, -3), 2.0, (1, 1, 1))
    far = Lamp((0, 0, -20), 2.0, (1, 1, 1))
    towards = Lamp((0, 0, -20), 3.0, (1, 1, 1), axis=(0, 0, 1), cos_inner=0.9, cos_outer=0.8)
    away = Lamp((0, 0, -20), 3.0, (1, 1, 1), axis=(0, 0, -1), cos_inner=0.9, cos_outer=0.8)
    got = lights2070.split([small, near, far, towards, away], eye, 1.0)
    assert got.sprites == [small, near] and got.points == [far]
    assert got.spots == [towards, away] and got.bases == [towards]  # the camera looks at the end of the first


def test_the_rows_are_laid_out_as_the_light_renderer_writes_them():
    point = Lamp((1, 2, 3), 4.0, (0.5, 0.6, 0.7), 2.0, volume=0.1)
    assert lights2070.point_rows([point])[0].tolist() == pytest.approx([1, 2, 3, 4, 0.5, 0.6, 0.7, 2, 0.1])
    outer, inner = math.cos(0.5), math.cos(0.3)
    spot = Lamp((1, 2, 3), 4.0, (0.5, 0.6, 0.7), 2.0, (0, -1, 0), inner, outer, 0.2)
    row = lights2070.spot_rows([spot])[0]
    assert row[3:6].tolist() == pytest.approx([0, -1, 0])  # the frame's second row is the axis
    assert row[9:12].tolist() == pytest.approx([1, 2, 3])
    assert row[12:16].tolist() == pytest.approx([4, 4 * math.tan(0.5), 2, 0.1])  # the volume halved
    assert row[16:18].tolist() == pytest.approx([outer, 1 / (inner - outer)])
    sprites = lights2070.sprite_rows([point] * 100)
    assert len(sprites) == lights2070.MAX_SPRITES * 3 and sprites[1] == pytest.approx([0.5, 0.6, 0.7, 2.0])


def test_an_instanced_mesh_carries_each_row_on_every_vertex_of_its_copy():
    verts, index = np.zeros((3, 3), "f4"), np.array([0, 1, 2], np.uint32)
    rows = np.array([[1, 2], [3, 4]], "f4")
    mesh = lights2070.instanced(verts, index, rows, (1, 1))
    assert mesh.count == 6 and mesh.stride == 20 and mesh.indices.tolist() == [0, 1, 2, 3, 4, 5]
    data = np.frombuffer(mesh.data, "f4").reshape(6, 5)
    assert data[:, 3].tolist() == [1, 1, 1, 3, 3, 3]
    assert lights2070.sprite_mesh(2).count == 8 and lights2070.sprite_mesh(2).indices.max() == 7


def test_the_shadow_values_measure_the_distance_to_the_light_from_0_to_1():
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (0, 2) for z in (-1, 1)], float)
    v = frame2070.shadow_values((0.3, -1.0, 0.2), corners)
    plane = np.asarray(v["cVecShadowVPD"])
    depth = corners @ plane[:3] + plane[3]
    assert depth.min() >= 0.0 and depth.max() <= 1.0 and depth[corners[:, 1] == 2].max() < depth.min() + 0.5
    centre = np.asarray(v["cMtxShadowVPP0"]) @ np.array([0, 1, 0, 1.0])  # column vectors, as the shader multiplies
    assert centre[:2] / centre[3] == pytest.approx([0.5, 0.5])


def test_the_sky_dome_is_a_unit_sphere():
    mesh = sky2070.dome(4, 8)
    pts = np.frombuffer(mesh.data, "f4").reshape(-1, 3)
    assert mesh.count == 45 and np.allclose(np.linalg.norm(pts, axis=1), 1.0, atol=1e-6)
    assert len(mesh.indices) == 4 * 8 * 6


def test_the_post_sequence_is_the_default_hdr_one():
    names = [s[0] for s in post2070.STEPS]
    assert names == ["bright", "blur_x", "blur_y", "blur_x_2", "blur_y_2", "combine", "colorize"]
    assert [s[3] for s in post2070.STEPS] == [1, 2, 2, 4, 4, 1, 1]  # the pfx's DIMENSION 0, -2, -4


def test_a_spaced_out_blend_index_is_read_and_parameter_inputs_are_renamed():
    blocks = fxstage.read_states("BlendState b \n{ \n BLENDENABLE [ 0 ] = TRUE ; \n SRCBLEND = ONE ; \n} ;")
    assert fxstage.pass_state(blocks, "", "", "b").blend
    text = "void vs_T ( in float3 p : SV_Position , out float4 o : SV_Position ) ;\nvoid vs_T(float3 p : SV_Position, float2 t : TEXCOORD0, out float4 o : SV_Position) { }"
    hlsl, inputs = fxstage.parameter_inputs(text, "vs_T")
    assert [(i.semantic, i.location) for i in inputs] == [("SV_Position", 0)]  # the declaration's
    assert hlsl.count("p : TEXCOORD0") == 2 and "t : TEXCOORD1" in hlsl and "o : SV_Position" in hlsl


def test_the_scenes_lights_are_fed_as_the_engine_feeds_them():
    from types import SimpleNamespace

    from annotoolbox.viewer import fxcpass

    place = np.diag([2.0, 2.0, 2.0, 1.0])
    spot = SimpleNamespace(range=1.5, falloff=1.0, kind=2, cone=(0.3, 0.5), volume=0.1, color_at=lambda t: (1, 1, 1))
    state = SimpleNamespace(time=0.0, shows=lambda thing: True, light_color=lambda lamp: lamp.color_at(0.0))
    renderer = SimpleNamespace(scene=SimpleNamespace(lights=[spot]), state=state, place=lambda lamp, st: place)
    (lamp,) = fxcpass.lamps_of(renderer, lambda p: np.asarray(p, float))  # in the game's axes, as the pass draws
    assert lamp.range == pytest.approx(3.0) and lamp.axis == pytest.approx(tuple(-place[:3, 1] / 2))
    assert (lamp.cos_inner, lamp.cos_outer) == pytest.approx((math.cos(0.3), math.cos(0.5)))  # half-angles


def test_the_object_colour_follows_the_colour_transform_as_the_engine_applies_it():
    blue = (0.1, 0.2, 0.8)
    assert cfg2070.object_color(None, blue) == (1.0, 1.0, 1.0, 1.0)  # a building without one: white
    assert cfg2070.object_color(None, blue, shown=True) == (*blue, 1.0)  # ShowPlayerColor: the owner's
    water = cfg2070.ColorTransform((0.7, 0.8, 0.9), override=True, apply_always=True, alpha=0.7)
    assert cfg2070.object_color(water, blue) == (0.7, 0.8, 0.9, 0.7)  # replaced, its alpha kept
    flag = cfg2070.ColorTransform((1.0, 0.5, 1.0), multiply=True, apply_always=True)
    assert cfg2070.object_color(flag, blue) == pytest.approx((0.1, 0.1, 0.8, 1.0))  # the owner's colour times it
    skipped = cfg2070.ColorTransform((0.5, 0.5, 0.5))  # not applied to a ShowPlayerColor object
    assert cfg2070.object_color(skipped, blue, shown=True) == (*blue, 1.0)


def test_a_materials_values_carry_its_diffuse_colour_object_colour_and_cloth():
    mat = cfg2070.Material("w", 1, "P4h_N4b_G4b_B4b_T2h", {}, {}, {"diffuse": (1.0, 1.0, 1.0, 0.6)})
    ct = cfg2070.ColorTransform((0.2, 0.3, 0.4), multiply=True, alpha=0.5)
    values = dict(shader2070.material_values(mat, lambda name: name, ct))
    assert values["DiffuseColor"] == (1.0, 1.0, 1.0, 0.6) and values["ObjectColor"] == (0.2, 0.3, 0.4, 0.5)
    assert values["ObjectPlayer"] == 1 and "ClothRipple" not in values
    cloth = cfg2070.Cloth("c.rdm", [mat], None, double_sided=True, ripple=(1.0, 0.7, 0.5))
    values = dict(shader2070.material_values(mat, lambda name: name, None, cloth))
    assert values["ClothRipple"] == (1.0, 0.7, 0.5) and values["DoubleSided"] == 1 and values["ObjectPlayer"] == 0
    from annotoolbox.viewer import fxcpass
    from annotoolbox.viewer.ownpass import PLAYER_COLOR

    tinted = fxcpass.object_rgba({"ObjectColor": (1.0, 1.0, 1.0, 0.5), "ObjectPlayer": 1})
    assert tinted == pytest.approx((*PLAYER_COLOR, 0.5))


def test_foam_icons_and_faded_objects_go_to_the_blended_pass_and_water_keeps_its_step():
    """shader-compile.md 10, "Blended pass" (frame-2070.md "Which pass"): the blended-only shaders and an object
    drawn faded take ``DefaultBlend`` after the sky, cast no shadow and are taken out by Transparent pass."""
    from annotoolbox.viewer import fxcpass

    plain = {"Owner": "MODEL", "ShaderIndex": 0}
    assert not fxcpass.is_blended(plain)
    assert all(fxcpass.is_blended({"Owner": "MODEL", "ShaderIndex": k}) for k in (3, 7, 9))  # foam fall, icons
    assert fxcpass.is_blended({**plain, "ObjectColor": (1.0, 1.0, 1.0, 0.4)})  # faded
    assert not fxcpass.is_blended({"Owner": "MODEL", "ShaderIndex": 1, "ObjectColor": (0.5, 0.6, 0.7, 0.3)})  # water
    from annotoolbox.render import nativelook

    assert nativelook.GAMES["anno2070"].features["transparent"] == nativelook.YES


def test_the_blended_pass_without_a_shadow_reads_every_point_lit(monkeypatch):
    """Foam falls and icons draw by shadow method 0 whatever the frame has: with the shadow switched off (or no
    caster) their buffer was left zero and ``Shadow2ObjTrapezoidal`` divided 0 by 0 (read in the source). Now the
    map's middle at depth 0 of an empty map (cleared to the far depth): lit."""
    from types import SimpleNamespace

    from annotoolbox.render import shadowforward

    cleared = []
    monkeypatch.setattr(shadowforward, "ShadowMap", lambda ctx: SimpleNamespace(
        native="map", render=lambda casters, values, fill: cleared.append(list(casters))))  # fmt: skip
    frame = frame2070.Frame2070.__new__(frame2070.Frame2070)
    frame.ctx, frame.shadow_map = None, None
    values, maps = frame._unshadowed()
    assert maps == {"cShadowMap": "map"} and cleared == [[]]
    shader = np.asarray(values["cMtxShadowVPP0"])  # column vectors: the shader's mul(M, p)
    x, y, _z, w = shader @ np.array([3.0, -2.0, 7.0, 1.0])
    assert (x / w, y / w) == (0.5, 0.5) and not any(values["cVecShadowVPD"])  # the middle, at depth 0


def test_the_engines_timers_run_with_the_views_time():
    """shader-compile.md 10: ``cTimer`` (in-game, wind and real time) is the view's time, so model water's ripples,
    foam falls and icons move as an animation plays; a still view keeps them where they were (0)."""
    got = {}
    buffer = type("C", (), {"set": lambda self, name, value: got.__setitem__(name, value)})()
    cam = gs.direct_camera((64, 64), (0.0, 0.0, 10.0), (0.0, 0.0, 0.0), 0.8, 1.0, 100.0)
    frame2070.fill_camera({"cbCommon": buffer}, cam, 12.5)
    assert got["cTimer"] == [12.5, 0.0, 12.5, 12.5]
    frame2070.fill_camera({"cbCommon": buffer}, cam)
    assert got["cTimer"] == [0.0, 0.0, 0.0, 0.0]


def test_model_water_measures_the_depth_behind_it_ahead_of_the_camera():
    cam = gs.direct_camera((64, 64), (0.0, 0.0, 10.0), (0.0, 0.0, 0.0), 0.8, 1.0, 100.0)
    values = frame2070.water_values(cam, (1, 1, 1, 0.6))
    view = np.asarray(values["cMtxView"])  # column vectors, as the shader multiplies
    near, far = ((view @ np.array([0.0, 0.0, z, 1.0]))[2] for z in (5.0, 0.0))
    assert 0 < near < far  # the engine's view space: ahead is +z, the scene behind the sheet deeper
    assert values["cMatDiffuse"] == [1, 1, 1, 0.6]
    rows, _n = frame2070.object_rows(np.identity(4), None, 0, (0.7, 0.8, 0.9, 0.7))
    assert rows[0] == [0.7, 0.8, 0.9, 0.7]  # water keeps its alpha: the depth to its full colour


def test_a_cloths_vertices_are_packed_as_the_cloth_manager_feeds_them():
    assert cloth2070.packed(np.array([[1.0, 0.0, 0.0]])).tolist() == [[128, 128, 255, 255]]  # read back as .bgra
    quad = np.array([[0, 0, 0, 0, 0, 0, 0, 1], [1, 0, 0, 0, 0, 0, 1, 1], [1, 1, 0, 0, 0, 0, 1, 0],
                     [0, 1, 0, 0, 0, 0, 0, 0]], np.float32)  # fmt: skip
    mesh = cloth2070.cloth_mesh(quad, [0, 1, 2, 0, 2, 3])
    assert mesh.stride == cloth2070.STRIDE and mesh.count == 4 and mesh.positions().tolist()[2] == [1, 1, 0]
    raw = np.frombuffer(mesh.data, np.uint8).reshape(4, cloth2070.STRIDE)
    assert raw[0, 12:16].tolist() == [255, 128, 128, 255]  # the quad's normal +z, bytes z y x
    tangent = np.frombuffer(raw[0, 24:40].tobytes(), "<u4")
    assert tangent.tolist() == [128, 128, 0, 255]  # -dP/du stored: -x
    assert cloth2070.defines({"Switch.diffuse": 1, "Switch.ripples": 1}) == (("DIFFUSE_ENABLED", "1"),
                                                                              ("RIPPLES_ENABLED", "1"))  # fmt: skip
    assert cloth2070.techniques({"DoubleSided": 1}) == ("ClothDoubleSided", "Obj2ShadowDoubleSided")
    assert cloth2070.effect({"ShaderIndex": 2}) is None


def test_a_2070_configs_cloth_is_a_part_of_the_viewers_scene(tmp_path):
    from annotoolbox.sources import gametree
    from annotoolbox.viewer import modelscene

    cfg = """<m_Config><m_Clothes><m_Config><m_FileName>data\\\\f\\\\flag.rdm</m_FileName>
<m_IsDoubleSided>1</m_IsDoubleSided><m_RippleSize>2.0</m_RippleSize><m_ConfigType>CLOTH</m_ConfigType>
<m_Materials><m_Config><m_Name>cloth</m_Name><m_ShaderIndex>0</m_ShaderIndex><m_ConfigType>MATERIAL</m_ConfigType>
</m_Config></m_Materials></m_Config></m_Clothes></m_Config>"""
    for rel, data in {"data/f/flag.cfg": cfg.encode(), "data/f/flag.rdm": b""}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    scene = modelscene.build(gametree.loose_tree(tmp_path), "data/f/flag.cfg", "anno2070")
    (part,) = scene.parts
    assert part.node == "Clothes/0" and dict(part.default.values)["ClothRipple"][0] == 2.0


def _install() -> str:
    root = os.environ.get("ANNO2070_DIR")
    if not root:
        pytest.skip("needs an installed Anno 2070 (ANNO2070_DIR)")
    return root


def test_the_installed_games_frame_parts_compile():
    from annotoolbox.infra import fxc
    from annotoolbox.models import shader2070
    from annotoolbox.sources import gametree

    tree = gametree.open_game("anno2070", Path(_install()))
    try:
        compiler = fxc.Fxc.find(getattr(tree, "folder", None), library="d3dcompiler_43")
    except fxc.FxcMissing as e:
        pytest.skip(str(e))
    shaders = shader2070.Source2070(tree, compiler)
    shadowed = shaders.effect_pass(frame2070.FX, (), frame2070.SHADOWED)
    assert shadowed.ps.samplers["sShadowMap"].compare  # the hardware comparison of the game's configuration
    lights = {t: shaders.effect_pass(lights2070.FX, (), t) for t in lights2070.TECHNIQUES.values()}
    assert all(p.state.blend and (p.state.source, p.state.dest) == ("one", "one") for p in lights.values())
    sky = shaders.effect_pass(sky2070.FX, (), sky2070.TECHNIQUE)
    assert [i.semantic for i in sky.vs.inputs] == ["SV_Position"]  # its inputs are parameters
    assert set(post2070.compile_steps(shaders)) == {s[0] for s in post2070.STEPS}
    assert len(sky2070.read_volume(tree)) > 1
    from annotoolbox.viewer import fxcpass

    for index, technique in ((0, "DefaultBlend"), (3, "DefaultBlendStandardShadow"), (7, "DefaultBlendStandardShadow")):
        values = {"Owner": "MODEL", "ShaderIndex": index, "VertexFormat": "P4h_N4b_G4b_B4b_T2h"}
        blend = fxcpass.blend_pass(shaders, values)  # a faded model, a foam fall, an icon
        assert blend.technique == technique and blend.state.blend and blend.state.dest == "inv_src_alpha"


PICTURES = """
import json, sys
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources import gametree
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import rhi_picture
app = QApplication([])
root, shots = sys.argv[1], json.loads(sys.argv[2])
tree = gametree.open_game("anno2070", Path(root))
out = {}
for name, cfg, off in shots:
    state = ViewState(look="shaders", particles=False, switches={k: False for k in off})
    img = rhi_picture(ModelFiles(tree), modelscene.build(tree, cfg, "anno2070"), state, (240, 180), "d3d11", 600)
    out[name] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(240)] for y in range(180)], float).tolist()
print(json.dumps(out))
"""
BUILDINGS = "data/graphics/buildings/"
CASINO = BUILDINGS + "tycoons/public/casino_tycoons/casino_tycoons.cfg"
FOUNTAIN = "data/graphics/dlc_01/buildings/ecos/ornaments/eden_line_fountain/eden line fountain.cfg"
FLAGPOLE = "data/graphics/dlc_03/buildings/ecos/ornaments/eden_line_flagpole/eden_line_flagpole.cfg"


def _pictures(shots: list) -> dict:
    """``shots`` (name, cfg, switches off) drawn by the game's shaders on Direct3D 11, 240 x 180, as rgb arrays."""
    root = _install()
    if sys.platform != "win32":
        pytest.skip("Direct3D 11 needs Windows")
    import json

    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": str(repo / "src")}
    done = subprocess.run([sys.executable, "-c", PICTURES, root, json.dumps(shots)], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=900)  # fmt: skip
    if done.returncode != 0 and "Direct3D" in done.stderr:
        pytest.skip("no Direct3D 11 device")
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return {k: np.asarray(v) for k, v in json.loads(done.stdout.splitlines()[-1]).items()}


def test_the_installed_casino_gains_its_lights_shadow_sky_and_bloom():
    got = _pictures([("none", CASINO, ("shadows", "atmosphere", "lights", "post")), ("all", CASINO, ())])
    assert got["none"].std() > 5.0 and got["all"].std() > 5.0
    assert np.abs(got["all"] - got["none"]).mean() > 1.0  # the parts change the picture
    top = got["all"][:20].reshape(-1, 3).mean(axis=0)
    assert top[2] > top[0]  # the game's sky above: blue


def test_the_installed_fountain_gains_its_water_and_the_flagpole_its_banners():
    got = _pictures([("water", FOUNTAIN, ("post",)), ("ours", FOUNTAIN, ("post", "water")),
                     ("flag", FLAGPOLE, ("post", "atmosphere"))])  # fmt: skip
    assert np.abs(got["water"] - got["ours"]).max() > 40  # the game's water, not our look's sheet
    flag = got["flag"].reshape(-1, 3)
    blue = (flag[:, 2] > flag[:, 0] + 40) & (flag[:, 2] > flag[:, 1] + 40)
    assert blue.sum() > 200  # the banners in the owner's colour (our look's player blue) over the grey ground
