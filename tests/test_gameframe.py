"""117's frame on QRhi without a GPU: the sun's cascades fitted as the engine splits them and written by name, the
casters' per-cascade instances, the clustered point lights (cells, index list, light data), the large structured
buffers read raw; with an installed Anno 117 (``ANNO117_DIR``) and Direct3D 12 the residence's shadow and lights
change the picture (in a child process)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pytest

from annotoolbox.render import gameshader as gs
from annotoolbox.render import gameshadow, lightclusters

LEFT = np.diag([1.0, 1.0, -1.0, 1.0])


def camera(eye=(0.0, 10.0, 30.0), target=(0.0, 0.0, 0.0), size=(320, 200)) -> gs.Camera:
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    proj = LEFT @ gs.perspective(math.radians(45), size[0] / size[1], 0.5, 500.0, reverse=True)
    return gs.Camera(eye, gs.look_at(eye, target) @ LEFT, proj, 0.5, 500.0, size)


def test_the_cascades_split_the_scenes_depth_logarithmically_and_hold_it():
    cam = camera()
    box = (np.array([-5.0, 0.0, -5.0]), np.array([5.0, 6.0, 5.0]))
    c = gameshadow.fit(cam, box, (0.4, 0.8, 0.3))
    splits = [k.split for k in c.cascades]
    assert len(splits) == 4 and splits == sorted(splits)
    near, far = splits[0] ** 2 / splits[1], splits[-1]  # minZ x (maxZ/minZ)^((i+1)/n)
    assert np.allclose(splits, [near * (far / near) ** ((i + 1) / 4) for i in range(4)])
    assert np.allclose(c.light, -np.array([0.4, 0.8, 0.3]) / np.linalg.norm([0.4, 0.8, 0.3]))
    corners = gameshadow._corners(*box)
    for k in c.cascades:  # every caster lies inside the depth range, the light's projection is orthographic
        d = corners @ k.depth_axis
        assert k.depth_range[0] < d.min() and d.max() < k.depth_range[1]
        clip = np.c_[corners, np.ones(8)] @ k.matrix
        assert clip[:, 3].min() == pytest.approx(1.0)


@dataclass
class Var:
    name: str
    offset: int
    size: int
    type: str = ""
    rows: int = 0
    columns: int = 0
    elements: int = 0
    members: list = field(default_factory=list)


@dataclass
class Buffer:
    name: str
    size: int
    variables: list


def test_the_cascades_go_into_the_raw_information_and_the_casters_constants():
    c = gs.Constants(Buffer("x", 512, [Var("cUsedShadowPartitions", 0, 4, "uint"),
                                       Var("cModelGPUCulledInstanceData", 16, 64, "uint4", elements=4)]))  # fmt: skip
    cas = gameshadow.fit(camera(), (np.full(3, -2.0), np.full(3, 2.0)), (0.0, 1.0, 0.5))
    gameshadow.fill_casters(c, cas)
    words = np.frombuffer(bytes(c.data), np.uint32)
    assert words[0] == 4
    per = words[4:20].reshape(4, 4)[:, 0]
    assert [(w >> 30) & 3 for w in per] == [0, 1, 2, 3] and all((w >> 22) & 0xF == 15 for w in per)
    gameshadow.fill(c, None)
    assert np.frombuffer(bytes(c.data), np.uint32)[0] == 0
    raw = gameshadow.info_bytes(cas)  # the structured buffer's packing: 208 bytes a cascade
    assert len(raw) == 4 * gameshadow.INFO_BYTES
    floats = np.frombuffer(raw, np.float32)
    second = floats[208 * 2 // 4 :]
    assert second[156 // 4] == pytest.approx(cas.cascades[2].split)
    assert np.allclose(second[:16].reshape(4, 4), cas.cascades[2].matrix.T)  # by columns
    assert np.allclose(second[192 // 4 : 208 // 4], [0.5, 0.5, 0.0, 0.5])  # the third quarter of the map


def test_the_suns_constants_are_written_once_for_every_pass():
    names = ("cSunDirection", "cLgtDirection", "cLgtDirectionUncapped", "cDayNight", "cTimeOfDay")
    c = gs.Constants(Buffer("x", 256, [Var(n, 16 * i, 16, "float4") for i, n in enumerate(names)]))
    sun = (0.0, 3.0, 4.0)
    assert np.allclose(gs.light_travel(sun), (0.0, -0.6, -0.8)) and np.allclose(gs.light_travel((0, 0, 0)), 0)
    assert np.allclose(gameshadow.light_basis(sun)[2], gs.light_travel(sun))
    gs.fill_sun(c, sun, night=True)
    rows = np.frombuffer(bytes(c.data), np.float32).reshape(-1, 4)
    for row in range(3):
        assert np.allclose(rows[row][:3], (0.0, -0.6, -0.8))
    assert rows[3].tolist() == [0, 1, 0, 1] and rows[4].tolist() == [0.0, 0, 0, 1]
    gs.fill_sun(c, sun, night=False)
    rows = np.frombuffer(bytes(c.data), np.float32).reshape(-1, 4)
    assert rows[3].tolist() == [1, 0, 1, 0] and rows[4].tolist() == [0.5, 1, 0.5, 0]


def test_point_lights_go_into_every_cell_their_sphere_touches():
    cam = camera(eye=(0.0, 0.0, -10.0), target=(0.0, 0.0, 0.0))
    near = lightclusters.PointLight((0.0, 0.0, 0.0), 2.0, (1.0, 0.5, 0.25))
    behind = lightclusters.PointLight((0.0, 0.0, -30.0), 2.0, (1.0, 1.0, 1.0))
    c = lightclusters.build([near, behind], cam.view, cam.proj, cam.far)
    assert c.count == 2 and np.allclose(c.points[:12], [0, 0, 0, 2, 1, 0.5, 0.25, 0, 0, 0, 0, 0])
    cells = c.cells.reshape(-1, 2)
    used = np.nonzero(cells[:, 1])[0]
    assert len(used) > 0 and set(cells[used, 1]) == {1}  # only the near light, the other is behind the camera
    xs, ys, zs = used % 32, (used // 32) % 16, used // (32 * 16)
    assert xs.min() <= 15 <= xs.max() and ys.min() <= 7 <= ys.max() and set(zs) == {0}  # around the middle, ahead
    starts = cells[used, 0]
    assert sorted(starts) == list(range(len(used))) and np.all(c.indices.ravel()[starts] == 0)
    assert lightclusters.slice_of(10, 500) == 0 and lightclusters.slice_of(500, 500) == 15
    assert lightclusters.slice_of(20 + 480 / 4, 500) == 9  # sqrt(1/4) x 16 + 1


@dataclass
class Lamp:
    """A config's LIGHT, as the viewer's scene record holds it (``lightclusters.Lamp``)."""

    kind: int
    range: float
    cone: tuple = (0.5, 0.8)
    size: tuple = (1.0, 1.0)
    falloff: float = 2.0
    double_sided: bool = False
    no_fog: bool = False


def test_spot_and_area_lights_follow_the_point_lights_in_each_cell():
    cam = camera(eye=(0.0, 0.0, -10.0), target=(0.0, 0.0, 0.0))
    place = np.identity(4)
    place[:3, 3] = (0.0, 0.0, 0.0)
    place[:3, :3] = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]  # local -y onto world +z: the spot shines away from the eye
    point = lightclusters.from_config(Lamp(0, 2.0), place, (1, 1, 1))
    spot = lightclusters.from_config(
        Lamp(2, 3.0, cone=(math.radians(40), math.radians(60)), falloff=1.5), place, (2, 2, 2)
    )
    quad = lightclusters.from_config(Lamp(5, 2.5, size=(2.0, 4.0), double_sided=True), place, (3, 3, 3))
    line = lightclusters.from_config(Lamp(8, 1.0, size=(6.0, 0.2)), place, (1, 1, 1))
    assert lightclusters.from_config(Lamp(9, 1.0), place, (1, 1, 1)) is None  # no reading of that type
    assert isinstance(spot, lightclusters.SpotLight) and np.allclose(spot.direction, (0, 0, 1))
    assert spot.cos_inner == pytest.approx(math.cos(math.radians(20)))
    assert quad.shape == 1 and np.allclose(quad.axis1, (1, 0, 0)) and np.allclose(quad.axis2, (0, -2, 0))
    assert quad.flags == 2 and line.shape == 5 and line.axis2 == (0.2, 0.0, 0.0) and np.allclose(line.axis1, (3, 0, 0))
    c = lightclusters.build([quad, spot, point, line], cam.view, cam.proj, cam.far)
    cells = c.cells.reshape(-1, 2)
    used = np.nonzero(cells[:, 1])[0]
    counts = cells[used, 1]
    assert set((counts >> 11) & 0x3FF) == {1}  # the spot reaches furthest: in every cell used
    assert set(counts & 0x7FF) == {0, 1} and set(counts >> 21) == {0, 1, 2}
    every = used[counts == (1 | 1 << 11 | 2 << 21)]  # around the lights' place: all four
    assert len(every) > 0
    first = cells[every[0], 0]
    assert list(c.indices.ravel()[first : first + 4]) == [0, 0, 0, 1]  # point 0, spot 0, areas 0 and 1
    s = c.spots[:32]
    assert np.allclose(s[:4], [0, 0, 0, 1.5]) and np.allclose(s[4:8], [2, 2, 2, math.cos(math.radians(30))])
    assert np.allclose(s[8:11], [0, 0, -3])  # towards the light, its length the range
    assert s[11] == pytest.approx(1 / (math.cos(math.radians(20)) - math.cos(math.radians(30))), rel=1e-5)
    a = c.areas[:20]
    assert (
        np.allclose(a[:4], [0, 0, 0, 1]) and np.allclose(a[4:8], [1, 0, 0, 2]) and np.allclose(a[8:12], [0, -2, 0, 2.5])
    )
    assert len(c.spots) == len(c.areas) == 4096 * 4


def test_large_structured_buffers_are_read_raw():
    text = """
StructuredBuffer<uint2> cLightClusterBuffer : register(t13, space0);
StructuredBuffer<float4> cPointLights;
uint2 Cell(float i) { return cLightClusterBuffer[i + 0.5]; }
float3 Pos(uint k) { return cPointLights[k * 3 + 0].xyz + cPointLights[k * 3 + 1].xyz; }
"""
    h = gs.adapt(text, "Cell").hlsl
    assert "RWByteAddressBuffer cLightClusterBuffer;" in h and "RWByteAddressBuffer cPointLights;" in h
    assert "cLightClusterBuffer.Load<uint2>((uint)(i + 0.5) * 8)" in h
    assert "cPointLights.Load<float4>((uint)(k * 3 + 1) * 16).xyz" in h
    assert "StructuredBuffer" not in h


INSTALL_FRAME = """
import json, sys
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import rhi_picture
app = QApplication([])
tree = open_game("anno117", Path(sys.argv[1]))
cfg = "data/base/graphics/roman/buildings/residences/residence_tier01_estate06/residence_tier01_estate06.cfg"
scene = modelscene.build(tree, cfg, "anno117")
out = {}
for key, night, switches in (("all", True, {}), ("no_lights", True, {"lights": False}), ("day", False, {}),
                             ("no_shadow", False, {"shadows": False})):
    state = ViewState(look="shaders", particles=False, night=night, switches=switches)
    img = rhi_picture(ModelFiles(tree), scene, state, (240, 180), "d3d12", wait=600)
    out[key] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(240)] for y in range(180)], float)
print(json.dumps({"shadow": float(np.abs(out["day"] - out["no_shadow"]).mean()),
                  "lights": float(np.abs(out["all"] - out["no_lights"]).mean()), "std": float(out["all"].std())}))
"""


def test_the_residences_shadow_and_lights_draw_by_the_games_shaders():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(INSTALL_FRAME.replace("sys.argv[1]", repr(root)))
    # the sun's shadow by day (at night the game's sky passes light by the moon), the lamps at night
    assert got["std"] > 5 and got["shadow"] > 0.2 and got["lights"] > 0.05, got


INSTALL_FIT = """
import json, sys, time
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.render import gameshadow
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import RhiModelView
app = QApplication([])
tree = open_game("anno117", Path(sys.argv[1]))
cfg = "data/base/graphics/roman/buildings/residences/residence_tier01_estate06/residence_tier01_estate06.cfg"
scene = modelscene.build(tree, cfg, "anno117")
view = RhiModelView("d3d12")
view.state = ViewState(look="shaders", particles=False, ground=False)  # the model's depth alone (no ground plane)
view.show_scene(scene, ModelFiles(tree))
for i in range(4000):
    view.picture(320, 240)
    r = view.renderer
    if r is not None and r.box is not None and not r.busy() and r.game_pass.fit is not None and i > 5:
        break
    time.sleep(0.05)
view.frame_model()
view.picture(320, 240)
gp = view.renderer.game_pass
seen = {}
render = gp.frame.render
def spy(*a, **k):
    seen["camera"], seen["box"] = a[3], k["passes"].box
    return render(*a, **k)
gp.frame.render = spy
gp.fit.read_back = True
view.picture(320, 240)
view.picture(320, 240)
bounds, info = (bytes(r.data) for r in gp.fit.readbacks)
z = np.frombuffer(bounds[:8], np.float32)
corners = gameshadow._corners(*seen["box"])
zs = (np.c_[corners, np.ones(8)] @ seen["camera"].view)[:, 2]
cells = np.frombuffer(info, np.float32).reshape(4, 52)
print(json.dumps({"z": z.tolist(), "box_z": [float(zs.min()), float(zs.max())], "splits": cells[:, 39].tolist(),
                  "min": cells[:, 36:39].tolist(), "max": cells[:, 40:43].tolist(),
                  "matrix": cells[0, :16].tolist(), "errors": list(view.renderer.messages) + list(view.ctx.errors)}))
"""


def test_the_games_analysis_fits_the_cascades_to_the_depth_drawn():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(INSTALL_FIT.replace("sys.argv[1]", repr(root)))
    assert not got["errors"], got["errors"]
    near, far = got["z"]
    lo, hi = got["box_z"]
    assert lo - 0.01 <= near < far <= hi + 0.01  # the drawn pixels lie inside the model's bounds
    near = max(near, 0.5)  # csCalculateSplits' clamp, then minZ x (maxZ / minZ)^((i + 1) / 4)
    assert got["splits"] == pytest.approx([near * (far / near) ** ((i + 1) / 4) for i in range(4)], rel=1e-4)
    mins, maxs = np.array(got["min"]), np.array(got["max"])
    assert np.all(mins < maxs) and np.allclose(mins, np.floor(mins))  # whole units, as the atomics keep them
    m = np.array(got["matrix"]).reshape(4, 4).T  # stored by columns
    width = maxs[0, 0] - mins[0, 0]
    assert m[0, 0] ** 2 + m[1, 0] ** 2 + m[2, 0] ** 2 == pytest.approx((2 / width) ** 2, rel=1e-4)  # the ortho x scale


def test_a_state_block_becomes_the_pipelines_blend_and_depth():
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QRhiGraphicsPipeline

    from annotoolbox.render import renderstates

    bf = QRhiGraphicsPipeline.BlendFactor
    premult = {"CullMode": "CCW", "AlphaBlendEnable[0]": "TRUE", "SrcBlend[0]": "ONE", "DstBlend[0]": "INV_SRC_ALPHA",
               "SrcBlendAlpha[0]": "INV_DST_ALPHA", "DstBlendAlpha[0]": "ONE", "AlphaBlendEnable[1]": "TRUE",
               "SrcBlend[1]": "INV_DST_COLOR", "DstBlend[1]": "ONE"}  # fmt: skip
    s = renderstates.pass_state(premult)
    assert s.depth_test and s.depth_write  # no depth state named: the engine's default, tested and written
    assert s.blends[0] == (bf.One, bf.OneMinusSrcAlpha, bf.OneMinusDstAlpha, bf.One)
    assert s.blends[1] == (bf.OneMinusDstColor, bf.One, bf.OneMinusDstColor, bf.One)
    water = renderstates.pass_state({"ZWriteEnable": "FALSE"})
    assert not water.depth_write and water.blends == (None, None) and water.cull == "none"


INSTALL_TRANSPARENT = """
import json, sys, time
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.sources.gametree import open_game
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ViewState
from annotoolbox.viewer.modelview import RhiModelView, rhi_picture
app = QApplication([])
tree = open_game("anno117", Path(sys.argv[1]))
cfg = "data/base/graphics/portrait/celtic/resident_tier01_celtic/resident_tier01_celtic.cfg"
scene = modelscene.build(tree, cfg, "anno117")
out = {}
for key, look, switches in (("game", "editor", {}), ("shaders", "shaders", {}), ("off", "shaders", {"transparent": False})):
    img = rhi_picture(ModelFiles(tree), scene, ViewState(look=look, particles=False, switches=switches), (200, 150), "d3d12", wait=600)
    out[key] = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(200)] for y in range(150)], float)
view = RhiModelView("d3d12")
view.state = ViewState(look="shaders", particles=False)
view.show_scene(scene, ModelFiles(tree))
for i in range(600):
    view.picture(200, 150)
    gp = view.renderer.game_pass if view.renderer is not None else None
    if gp is not None and len(gp.models) >= 9 and not view.renderer.busy():
        break
    time.sleep(0.05)
background = out["shaders"][0, 0]
print(json.dumps({"transparent": len(gp.transparent), "messages": list(view.renderer.messages),
                  "models": len(gp.models), "groups": len(view.renderer.items[0]),
                  "covered": float((np.abs(out["shaders"] - background).sum(2) > 30).mean()),
                  "covered_game": float((np.abs(out["game"] - background).sum(2) > 30).mean()),
                  "hair": float((np.abs(out["shaders"] - out["off"]).sum(2) > 0).mean())}))
"""


def test_a_portraits_skin_draws_in_its_rest_pose_and_its_hair_in_the_transparent_pass():
    import os
    import sys

    root = os.environ.get("ANNO117_DIR")
    if not root or sys.platform != "win32":
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR) on Windows")
    from tests.test_rhi import _backends, _child

    if "d3d12" not in _backends():
        pytest.skip("no Direct3D 12 device")
    got = _child(INSTALL_TRANSPARENT.replace("sys.argv[1]", repr(root)))
    assert got["transparent"] >= 1 and not got["messages"], got
    assert got["covered"] > 0.5 * got["covered_game"], got  # the skin is not collapsed (rest pose: identity joints)
    assert got["hair"] > 0, got  # the hair drawn by its transparent technique, not by our look
    assert got["models"] == got["groups"] == 9, got  # a model per mesh and material: the shirt's two meshes both


def test_a_group_is_its_part_its_mesh_and_its_material():
    pytest.importorskip("PySide6")
    from annotoolbox.viewer.ownpass import OwnPass

    # a portrait's shirt material is in two meshes of its file: two groups, two models (frame-117-viewer.md 9)
    assert OwnPass._key(None, 0, {"key": 1, "mesh": 1}) != OwnPass._key(None, 0, {"key": 1, "mesh": 5})
    assert OwnPass._key(None, 2, {"key": 3}) == (2, 0, 3)


def test_the_hand_written_shadow_information_offsets_match_the_games_structure():
    import os
    from pathlib import Path

    from annotoolbox.render import rawbuffers
    from annotoolbox.sources.gametree import open_game

    root = os.environ.get("ANNO117_DIR")
    if not root:
        pytest.skip("needs an installed Anno 117 (ANNO117_DIR)")
    text = open_game("anno117", Path(root)).read("data/shaders/include/constants_shadow.fxh").decode("utf-8", "replace")
    size, members = rawbuffers.layout(text, "ShadowInformation")
    assert size == gameshadow.INFO_BYTES
    assert {name: m.offset for name, m in members.items() if name in gameshadow.INFO} == gameshadow.INFO
