"""Anno 1404's models by its own shaders: the ambient set's light and atmosphere, the scattering constants as the
engine's setter makes them, the Granny vertices in the layouts ``model.fx`` takes (with tangent frames for a normal
map), the material's switches, the constants, the shadow's and the post's; with the installed game (``ANNO1404_DIR``)
a printing house compiles and draws on Direct3D 11 unlike our editor look, with its shadow and the game's sky."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.paths import REPO_ROOT
from annotoolbox.render import frame1404
from annotoolbox.viewer import fxcpass1404

AMBIENT = """<AmbientMap><Settings><South><DiffuseColor><r>1</r><g>1</g><b>1</b><a>1</a></DiffuseColor></South>
<North><DiffuseColor><r>0.5</r><g>0.25</g><b>1</b><a>1</a></DiffuseColor><DiffuseColorPower>2</DiffuseColorPower>
<AmbientColor><r>0.2</r><g>0.4</g><b>0.6</b><a>1</a></AmbientColor><AmbientColorPower>0.5</AmbientColorPower>
<MoodColor><r>1</r><g>0.5</g><b>0.25</b><a>0</a></MoodColor><MoodColorPower>1</MoodColorPower>
<SunColorPower>10</SunColorPower><Atmosphere><Density>2E+25</Density><Turbidity>2</Turbidity><g>-0.2</g>
<ScatterStart>0.3</ScatterStart><SunColor><r>1</r><g>0.5</g><b>0.5</b><a>1</a></SunColor></Atmosphere>
</North></Settings></AmbientMap>"""


def test_the_ambient_sets_light_is_colour_times_power():
    sun = frame1404.read_sun(AMBIENT)
    assert sun.diffuse == pytest.approx((1.0, 0.5, 2.0)) and sun.ambient == pytest.approx((0.1, 0.2, 0.3))
    assert (sun.density, sun.turbidity, sun.g, sun.scatter_start) == (2e25, 2.0, -0.2, 0.3)
    assert sun.sun_color == pytest.approx((10.0, 5.0, 5.0)) and sun.mood == (1.0, 0.5, 0.25, 0.0)
    assert frame1404.read_sun("") == frame1404.SSun1404((0.0, 1.0, 0.0))  # no file: the defaults


def test_the_scattering_constants_follow_the_engines_setter():
    from annotoolbox.render import gameshader as gs

    sun = frame1404.read_sun(AMBIENT)
    cam = gs.direct_camera((64, 64), (0.0, 5.0, 10.0), (0.0, 0.0, 0.0), 0.8, 1.0, 101.0)
    v = frame1404.scattering_values(sun, cam)
    assert v["cBetaRay"][2] == pytest.approx(5.538964e20 / 2e25)  # Rayleigh: the numerator over the density
    assert v["cBetaMie"][0] == pytest.approx(0.00570717407 * 2 - 0.00567752216)  # Mie: linear in the turbidity
    assert v["cBetaDashRay"][0] == pytest.approx(v["cBetaRay"][0] * 3 / (16 * math.pi))
    assert v["cReciBetaRayMie"][1] == pytest.approx(1 / (v["cBetaRay"][1] + v["cBetaMie"][1]))
    assert v["cHGParameters"] == pytest.approx([0.96, 1.04, -0.4])
    assert v["cDepthSca"] == pytest.approx([31.0, 101.0, 1 / 70.0])  # ScatterStart of the depth range
    assert v["cOpticalDepths"][1] == 75000.0  # the buffer's initial values, which a plain compile leaves to us


def test_1404s_shadow_and_post_take_2070s_with_its_matrices():
    from annotoolbox.render import frame2070, post2070

    corners = np.array([[x, y, z] for x in (-1, 1) for y in (0, 2) for z in (-1, 1)], float)
    ours = frame2070.shadow_values((0.3, -1.0, 0.2), corners, frame1404.as_is)
    turned = frame2070.shadow_values((0.3, -1.0, 0.2), corners)
    assert np.allclose(ours["cMtxShadowVP0"], np.asarray(turned["cMtxShadowVP0"]).T)  # 1404's go in as ours
    assert [s[0] for s in post2070.STEPS_1404] == ["colorize"]  # the default sequence without glow or heat


def test_granny_vertices_take_the_layout_of_their_material():
    verts = np.array([[0, 0, 0, 0, 1, 0, 0, 0], [1, 0, 0, 0, 1, 0, 1, 0], [0, 0, 1, 0, 1, 0, 0, 1]], "f4")
    plain = frame1404.granny_mesh(verts, [0, 1, 2])
    assert plain.m_stride == 32 and [e[0] for e in plain.m_elements] == [0, 1, 4]
    tangent = frame1404.granny_mesh(verts, [0, 1, 2], tangents=True)
    assert tangent.m_stride == 56 and [e[0] for e in tangent.m_elements] == [0, 1, 2, 3, 4]
    rows = np.frombuffer(tangent.m_data, "f4").reshape(3, 14)
    assert rows[0, 6:9] == pytest.approx([-1, 0, 0])  # the tangent (growing u along +x), stored negated
    assert rows[0, 12:14] == pytest.approx([0, 0])
    assert frame1404.material_defines("d.dds") == (("DIFFUSE_ENABLED", "1"),)
    assert ("NORMAL_ENABLED", "1") in frame1404.material_defines("d.dds", "n.dds")


def test_an_intact_object_keeps_its_colour():
    values = frame1404.light_values(frame1404.SSun1404((0.0, 1.0, 0.0)))
    assert values["cObjectDamage"][1] == 1.0  # AddDirt multiplies the colour by y
    assert values["cLgtDirection"][:3] == pytest.approx([0, -1, 0])  # the direction the light travels


def test_1404s_water_sheets_measure_their_depth_in_a_left_handed_view_and_find_the_ground_behind():
    """shader-compile.md 12 "Model water": shader index 1 of the model table is water; the sheet's ``cMtxView[2]``
    gives the view depth ahead as a positive number (ours looks down -z: the depth behind a sheet came out negative),
    ``cMtxViewProjInvOrigin`` takes a pixel's depth back to the point relative to the camera; ``cTimer`` the clock."""
    from types import SimpleNamespace as N

    from annotoolbox.render import gameshader as gs

    assert frame1404.is_water({"Owner": "MODEL", "ShaderIndex": 1})
    assert not frame1404.is_water({"Owner": "MODEL", "ShaderIndex": 6}) and not frame1404.is_water({"Owner": "X"})
    cam = gs.direct_camera((100, 100), (3.0, 4.0, 10.0), (3.0, 0.0, 0.0), 0.8, 1.0, 100.0)
    sheet = frame1404.CWater1404(N(fallbacks={}, is_mirrored=False))
    assert (
        sheet.m_refracts and sheet.m_water and not frame1404.CModel1404(N(fallbacks={}, is_mirrored=False)).m_refracts
    )
    values = sheet.frame_values(cam, frame1404.SSun1404((0.0, 1.0, 0.0), seconds=7.0))
    assert values["cTimer"] == [7.0, 0.0, 7.0, 7.0]
    view = values["cMtxView"]  # as the shader reads it: row 2 of the transposed matrix is our column 2
    near, far = np.array([3.0, 0.0, 0.0, 1.0]), np.array([3.0, 0.0, -10.0, 1.0])
    assert near @ view[:, 2] > 0 and far @ view[:, 2] > near @ view[:, 2]  # ahead and deeper
    clip = far @ cam.view @ cam.projection
    back = np.array([*(clip[:2] / clip[3]), clip[2] / clip[3], 1.0]) @ values["cMtxViewProjInvOrigin"]
    assert back[:3] / back[3] + cam.eye == pytest.approx(far[:3])


def test_1404s_water_takes_the_highest_qualitys_caustics_scrolled_by_the_engines_speeds():
    """shader-compile.md 12 "Model water": the caustics at the highest water quality, the engine's one map at the
    highest texture quality, ``cWaterCaustic`` 0.03 and 0.02 a second, the last half a period on, as the engine sets
    them: the pass draws in the game's axes (shader-compile.md 11 "The game's own axes")."""
    assert ("WATER_CAUSTICS_ENABLED", "1") in frame1404.WATER_DEFINES
    assert frame1404.CAUSTICS.endswith("water/caustics_0.dds")
    assert frame1404.caustic_offsets(0.0) == pytest.approx([0.0, 0.0, 0.0, 0.5])
    assert frame1404.caustic_offsets(10.0) == pytest.approx([0.3, 0.3, 0.2, 0.7])
    assert frame1404.caustic_offsets(50.0) == pytest.approx([0.5, 0.5, 0.0, 0.5])  # wrapped at 1


def test_1404s_frame_draws_its_water_sheets_after_the_rest_over_the_scenes_copy(monkeypatch):
    """The sheets cast no shadow and draw last, with the copies of the colour and depth as their refraction."""
    from types import SimpleNamespace as N

    order, casters = [], []
    frame = frame1404.CFrame1404.__new__(frame1404.CFrame1404)
    frame.m_ctx, frame.m_sky, frame.m_color, frame.m_depth, frame.m_fbo = (
        N(m_flip_offscreen=False),
        None,
        None,
        None,
        None,
    )
    frame.m_sea = None
    frame._targets = lambda size: None
    frame._shadow = lambda models, sun, on: casters.extend(models)
    frame.m_composite = N(draw=lambda *a, **k: order.append("composite"))
    frame._water = lambda size, camera, sun, water, shadow: order.append(("water", water))
    monkeypatch.setattr(frame1404, "draw_models", lambda ctx, fbo, models, *a, **k: order.append(("models", models)))
    house, sheet = N(m_refracts=False), N(m_refracts=True)
    frame.render(None, (8, 8), [sheet, house], None, None)
    assert casters == [house] and order == [("models", [house]), ("water", [sheet]), "composite"]


def test_the_sea_draws_last_over_the_scenes_copies_with_the_frames_shadow():
    """The old engines' sea under a model (1404's and 1701's, Ocean waves): after the models, the sky and the sheets,
    refracting copies of the colour and depth, with the frame's sun shadow; nothing without one."""
    from types import SimpleNamespace as N

    got = []
    frame = frame1404.CFrame1404.__new__(frame1404.CFrame1404)
    frame.m_ctx, frame.m_color, frame.m_depth, frame.m_fbo, frame.m_shadow = None, "colour", "depth", "fbo", "shadow"
    frame.m_copies = N(take=lambda color, depth, size: (f"copy of {color}", f"copy of {depth}"))
    frame.m_sea = None
    frame._after((8, 8), None, None)
    frame.m_sea = lambda *args: got.append(args)
    frame._after((8, 8), None, None)
    assert got == [("fbo", (8, 8), "copy of colour", "copy of depth", "shadow")]


def test_1404s_sea_is_offered_in_the_viewer_off_until_turned_on():
    from annotoolbox.render import nativelook, ocean1404

    assert nativelook.GAMES["anno1404"].features["waves"] == nativelook.YES
    assert not nativelook.part_on("anno1404", {}, "waves") and nativelook.part_on("anno1404", {"waves": True}, "waves")
    assert set(ocean1404.STAGES) == {"grid", "water", "shadowed"}
    assert ocean1404.OPEN_SEA[3] == 0  # the stand-in depth map marks no land: nothing is clipped


PRINTING_HOUSE = """
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
tree = gametree.open_game("anno1404", Path(%r))
scene = modelscene.build(tree, "data/graphics/buildings/factory/north/printing_house/n_printing_house.cfg", "anno1404")
out = {}
for name, look, off in (("editor", "editor", ()), ("shaders", "shaders", ()), ("flat", "shaders", ("shadows",))):
    state = SViewState(look=look, particles=False, switches={k: False for k in off})
    img = rhi_picture(CModelFiles(tree), scene, state, (200, 150), "d3d11", 600)
    out[name] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(200)] for y in range(150)], float).tolist()
print(json.dumps(out))
"""


def test_the_installed_printing_house_draws_by_the_games_own_shaders():
    root = os.environ.get("ANNO1404_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 1404 (ANNO1404_DIR) on Windows")
    from annotoolbox.infra import fxc
    from annotoolbox.models import shader2070
    from annotoolbox.sources import gametree

    tree = gametree.open_game("anno1404", Path(root))
    try:
        compiler = fxc.CFxc.find(None, library="d3dcompiler_43")
    except Exception as e:  # no compiler on this machine
        pytest.skip(str(e))
    shaders = shader2070.CSource2070(tree, compiler, shader2070.GLOBALS_1404)
    got = shaders.effect_pass(frame1404.FX, frame1404.material_defines("d", "n"), frame1404.TECHNIQUE)
    assert [i.semantic for i in got.vertex_stage.inputs] == [
        "SV_Position",
        "NORMAL0",
        "TANGENT0",
        "BINORMAL0",
        "TEXCOORD0",
    ]
    repo = REPO_ROOT
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": str(repo / "src")}
    done = subprocess.run([sys.executable, "-c", PRINTING_HOUSE % root], cwd=repo, env=env, capture_output=True,
                          text=True, timeout=900)  # fmt: skip
    if done.returncode != 0 and "Direct3D" in done.stderr:
        pytest.skip("no Direct3D 11 device")
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    import json

    got = {k: np.asarray(v) for k, v in json.loads(done.stdout.splitlines()[-1]).items()}
    assert got["shaders"].std() > 5.0  # the house drawn
    assert np.abs(got["shaders"] - got["editor"]).mean() > 0.5  # by other shaders than the editor look's
    assert np.abs(got["shaders"] - got["flat"]).max() > 30  # the house shadows itself
    top = got["shaders"][:10].reshape(-1, 3).mean(axis=0)
    assert top[2] > top[0] + 10  # the game's sky above: its scattering is blue


def test_a_granny_skin_moves_the_meshs_positions_and_turns_its_normals_and_tangent_frame():
    """The old engine's model shaders read no bones (Granny poses on the CPU): the game's shader gets the posed
    vertices, the tangent frame turned with the normal; a new pose uploads once (shader-compile.md 12)."""
    from annotoolbox.models.rdmmesh import SMeshData

    verts = np.array([[0, 0, 0, 0, 0, 1, 0, 0], [1, 0, 0, 0, 0, 1, 1, 0], [0, 1, 0, 0, 0, 1, 0, 1]], np.float32)
    skin = SMeshData("m", verts, {"a": np.arange(3, dtype=np.uint32)})
    skin.joints, skin.weights = np.zeros((3, 1), np.int32), np.ones((3, 1), np.float32)
    turn = np.identity(4)
    turn[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]  # 90 degrees about z
    turn[:3, 3] = (5, 0, 0)
    for tangents, uvs in ((False, 1), (True, 1), (True, 2), (False, 2)):  # 2: 1701's two uv sets, read by elements
        mesh = frame1404.granny_mesh(verts, [0, 1, 2], tangents=tangents, uvs=uvs)
        rows = np.frombuffer(frame1404.posed_vertices(mesh, skin, turn[None]), "<f4").reshape(3, -1)
        assert rows[1, :3] == pytest.approx((5, 1, 0)) and rows[2, :3] == pytest.approx((4, 0, 0))
        assert rows[0, 3:6] == pytest.approx((0, 0, 1))  # a normal along the turn's axis stays
        if tangents:
            rest = np.frombuffer(mesh.m_data, "<f4").reshape(3, -1)
            assert rows[0, 6:9] == pytest.approx(turn[:3, :3] @ rest[0, 6:9], abs=1e-6)
        assert rows[0, -2:] == pytest.approx((0, 0))  # the uvs kept
    model = type("M", (), {"m_draw_call": type("D", (), {"m_mesh": mesh})()})()
    item = {"skinned": skin, "matrices": turn[None], "pose": ("clip", 0.5)}
    fxcpass1404.follow_skin(item, model)
    assert mesh.m_version == 1 and mesh.m_rest is not None
    fxcpass1404.follow_skin(item, model)
    assert mesh.m_version == 1  # the same pose: nothing uploads again
