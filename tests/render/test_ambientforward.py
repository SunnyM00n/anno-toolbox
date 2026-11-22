"""Anno 2205's frame light from its ambient settings: a local setting's variances times its global setting's graphs at
the time of day, and the per-frame members filled with every shader exponent above zero (synthetic files)."""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest
from tests.fakes import FakeFiles

from annotoolbox.render import ambientforward as af

GLOBAL = b"""<GlobalAmbientInfo><Name>Test_Global </>
<AmbientGraph><size>2</><array><i><t>0.0</><c.r>0.0</><c.g>0.0</><c.b>0.0</><c.a>1.0</></>
<i><t>1.0</><c.r>1.0</><c.g>0.5</><c.b>0.25</><c.a>2.0</></></></>
<DiffuseGraph><size>1</><array><i><t>0.5</><c.r>1.0</><c.g>1.0</><c.b>1.0</><c.a>3.0</></></></>
<Atmosphere><HorizonFallOffGraph><size>1</><array><i><t>0.5</><c.r>0.5</><c.g>0.5</><c.b>0.5</><c.a>1.0</></></></></>
<ImageBrightnessGraph><size>1</><array><i><t>0.5</><c.r>0.6</><c.g>0.6</><c.b>0.6</><c.a>1.3</></></></>
<ColorBalanceShadowsGraph><size>1</><array><i><t>0.5</><c.r>0.5</><c.g>1.0</><c.b>0.25</><c.a>2.0</></></></>
</>"""
LOCAL = b"""<AmbientArea><Name>Test</><Info><GlobalName>Test_Global</><TimeEnabled>%s</><Time>0.25</>
<AmbientVariance><r>2.0</><g>2.0</><b>2.0</><a>1.0</></>
<DiffuseVariance><r>1.0</><g>0.5</><b>0.25</><a>1.0</></>
<SecondLightOffset><x>1.5707963</><y>0.0</></>
<Atmosphere><HorizonFallOffVariance>4.0</></>
<GlowBrightness>2.5</><SCAOMinAmbient>0.8</>
<ToneMappingWhite>1.8</><BloomBias>1.5</><ImageBrightnessVariance>2.0</>
<ColorBalanceShadowsVariance><r>1.0</><g>0.5</><b>2.0</><a>1.0</></>
<Sky><SkyBoxPath>data\\sky\\day.dds</><CloudBoxDensePath></><SkyBoxPathNight>data\\sky\\night.dds</></>
<SkyCloudDensity>0.44</><EnvironmentRotation>0.25</></></>"""


class FakeAmbientFiles(FakeFiles):
    def under(self, prefix: str) -> list[str]:
        return self.iter_paths(prefix)


def tree(time_enabled: str) -> FakeAmbientFiles:
    return FakeAmbientFiles(
        {af.AMBIENT_DIR + "global/test.xml": GLOBAL, af.AMBIENT_DIR + "local/test.xml": LOCAL % time_enabled.encode()}
    )


def test_a_graph_is_read_between_its_keys_each_key_times_its_alpha_first():
    import xml.etree.ElementTree as ET

    graph = ET.fromstring("<g><i><t>0</t><c.r>0</c.r></i><i><t>1</t><c.r>2</c.r><c.a>3</c.a></i></g>")
    assert af.graph_at(graph, 0.25)[0] == 1.5 and af.graph_at(graph, 2.0)[0] == 6.0  # 2 x 3 at the second key
    assert af.graph_at(graph, 0.25)[3] == 1.5 and af.graph_at(None, 0.3) == (1.0, 1.0, 1.0, 1.0)


def close(a, b) -> bool:
    return all(math.isclose(x, y, rel_tol=1e-6, abs_tol=1e-9) for x, y in zip(a, b, strict=True))


def test_the_local_variances_scale_the_global_graphs_in_linear_light():
    light = af.read_light(tree("1"), "test", time=0.25)  # at its own time: the variances in full
    assert close(light.ambient, [c**af.GAMMA for c in (0.5 * 2, 0.25 * 2, 0.125 * 2)])  # (graph x alpha) x variance
    assert close(light.diffuse, [c**af.GAMMA for c in (3.0, 1.5, 0.75)])
    assert math.isclose(light.glow, 2.5**af.GAMMA) and light.scao == (0.8, 0.1, 1.0)
    assert light.horizon_falloff == 4.0  # a scalar graph is its keys' alpha, times its variance
    far = af.read_light(tree("1"), "test", time=0.75)  # six hours away: the graph alone
    assert close(far.diffuse, [3.0**af.GAMMA] * 3)
    assert close(af.read_light(tree("0"), "test", time=0.75).diffuse, light.diffuse)  # no time: always in full


def test_the_frame_values_cap_the_sun_and_set_the_fill_lights_by_azimuth_and_elevation():
    light = af.read_light(tree("1"), "test")
    v = af.light_values(light, (1.0, 0.0, 0.0))  # a sun on the horizon is held below it
    assert math.isclose(math.asin(v["cLgtDirection"][1]), af.LIGHT_CAP) and v["cLgtDirectionUncapped"][1] == 0
    x, y, z = v["cAmbientLightDirInv1"]  # a quarter turn about the up axis from the sun, at elevation 0
    assert math.isclose(x, 0.0, abs_tol=1e-6) and math.isclose(z, 1.0, abs_tol=1e-6) and y == 0
    night = af.light_values(light, (0.0, 1.0, 0.0), is_night=True)
    assert night["cLgtDiffuse"] == [c * af.NIGHT for c in light.diffuse] and night["cDayNight"][1] == 1.0
    s = af.scattering_values(light, 10.0, 0.1, 200.0)
    assert min(s["cScatteringSunFalloff"]) > 0 and min(s["cScatteringDistanceFalloff"]) > 0
    assert min(s["cScatteringHorizonFalloff"]) > 0 and s["cScatteringFactor"] > 0
    assert math.isclose(s["cScatteringSunFactor"], 1 / (math.cos(af.SUN_INNER) - math.cos(af.SUN_OUTER)))


def test_the_post_chain_takes_the_local_bloom_and_the_graphs_times_their_variances_as_stored():
    post = af.read_light(tree("0"), "test").post
    # the white point and the bloom as the engine uploads them: pow(x, 2.2) of the setting's
    assert math.isclose(post["cToneMappingWhite"], 1.8**2.2) and math.isclose(post["cBloomBias"], 1.5**2.2)
    assert post["cPostBlur"] == 0.0
    assert math.isclose(post["cImageBrightness"], 1.3 * 2.0)  # a scalar graph: its alpha
    assert close(post["cColorBalanceShadows"], (1.0, 1.0, 1.0))  # (rgb x alpha) x variance, no gamma
    assert post["cColorBalanceMidtones"] == [1.0, 1.0, 1.0] and post["cImageContrast"] == 1.0  # no graph: neutral


def test_the_visible_sky_takes_the_settings_boxes_their_turn_and_visibility():
    """shader-compile.md 11 "The visible sky": 2205's day box, 1800's sparse box (the dense one the same where the
    setting names none), the night box; the turn of the prefiltered cubes in the game's axes; clouds off: no box."""
    pytest.importorskip("PySide6")
    import numpy as np

    from annotoolbox.render import skyforward
    from annotoolbox.render.camera import MIRROR

    light = af.read_light(tree("0"), "test")
    assert (light.skybox, light.skybox_night, light.cloud_dense) == ("data/sky/day.dds", "data/sky/night.dds", "")
    assert light.sky_cloud_density == 0.44
    assert skyforward.box_maps(light, "anno2205") == {
        "cSkyBox": "data/sky/day.dds",
        "cSkyBoxNight": "data/sky/night.dds",
    }
    boxes = skyforward.box_maps(light, "anno1800")
    assert boxes["cCloudBoxSparse"] == boxes["cCloudBoxDense"] == "data/sky/day.dds"
    turn = af.environment_turn(light)  # a quarter turn about the up axis
    assert np.allclose(np.array([1.0, 0, 0, 0]) @ turn, [0, 0, -1, 0])
    v = skyforward.sky_values(light)
    assert np.allclose(v["cMtxSkyRotDay"], af.cube_turn(light)) and v["cSkyCloudDensity"] == 0.44
    assert np.allclose(af.cube_turn(light), MIRROR @ turn)
    off = skyforward.sky_values(light, clouds=False)
    assert off["cSkyBoxVisibility"] == 0.0 and off["cSkyCloudDensity"] == 0.0 and v["cSkyBoxVisibility"] == 1.0


def test_1800s_reflections_read_the_prefiltered_cube_in_the_games_axes_as_its_visible_sky_does():
    """The view's axes are the game's mirrored in x: the models' cube lookups (``MUL(N, cMtxEnvironmentRotation)``)
    were turned without the mirror while the visible sky's box lookup had it, so a reflection showed the sky's
    other side; both now take ``cube_turn`` (shader-compile.md 11 "Sky cubes")."""
    pytest.importorskip("PySide6")
    import numpy as np

    from annotoolbox.render import skyforward
    from annotoolbox.viewer import forwardpass

    light = af.SLight(specular_cubes=("data/noon.dds",), environment_rotation=0.0)
    p = forwardpass.CForwardPass1800.__new__(forwardpass.CForwardPass1800)
    p._map = lambda path, missing=False: None
    p._cube = lambda path: None
    _maps, values = p._frame_maps(light)
    turn = values["cMtxEnvironmentRotation"]
    assert np.allclose(turn, skyforward.sky_values(light)["cMtxSkyRotDay"])
    assert np.allclose(np.array([1.0, 0.0, 0.0]) @ turn[:3, :3], [-1.0, 0.0, 0.0])  # the view's +x is the game's -x


@pytest.mark.parametrize("kind", ["DXT1", "DXT3", "DXT5", "ATI1", "ATI2"])
@pytest.mark.parametrize("width", [8, 4, 2, 1])
def test_a_cube_face_mirrors_left_to_right_in_its_stored_blocks(kind, width):
    """2205's cube lookups carry no turn, so its cubes go up mirrored: each face flipped left to right in its own
    block format, a level narrower than a block by its texels alone (shader-compile.md 11 "Sky cubes")."""
    pytest.importorskip("PySide6")
    import numpy as np

    from annotoolbox.models import ddsdecode
    from annotoolbox.render import fxcdraw

    size = {"DXT1": 8, "ATI1": 8}.get(kind, 16)
    blocks = max(1, width // 4) * 2  # two block rows
    raw = np.random.default_rng(width).integers(0, 256, blocks * size, dtype=np.uint8).tobytes()
    flipped = fxcdraw.mirrored_x(raw, kind, width, 8)
    before = ddsdecode._compressed(kind.encode(), raw, width, 8, 0)
    after = ddsdecode._compressed(kind.encode(), flipped, width, 8, 0)
    assert np.array_equal(after, before[:, ::-1])
    plain = np.arange(2 * 3 * 8, dtype=np.uint8).tobytes()  # half floats: 8 bytes a texel
    assert (
        fxcdraw.mirrored_x(plain, "RGBA16F", 3, 2) == np.frombuffer(plain, np.uint8).reshape(2, 3, 8)[:, ::-1].tobytes()
    )


def test_2205s_cubes_go_up_mirrored_and_its_sky_takes_the_turn_alone():
    """A mirrored cube swaps the +X and -X faces and flips each; 2205's pass mirrors its cubes and its visible sky
    then reads the boxes with the setting's turn, 1800 keeps the mirror in its turn."""
    pytest.importorskip("PySide6")
    from types import SimpleNamespace as N

    import numpy as np

    from annotoolbox.render import fxcdraw, skyforward
    from annotoolbox.viewer import forwardpass

    faces = [np.full((4, 4, 4), face, np.uint8) for face in range(6)]
    faces[0][:, 0] = 99  # the +X face's left column
    dds = N(m_info=N(format="RGBA8", width_px=4, height_px=4, mips=1), size_of=lambda level: (4, 4),
            level=lambda level, face: faces[face])  # fmt: skip
    got = {
        face: np.frombuffer(raw, np.uint8).reshape(4, 4, 4)
        for face, _level, raw in fxcdraw.cube_faces(dds, True).levels
    }
    assert got[1][0, 3, 0] == 99 and got[1][0, 0, 0] == 0 and got[0][0, 0, 0] == 1 and got[2][0, 0, 0] == 2
    assert forwardpass.CForwardPass.MIRROR_CUBES and not forwardpass.CForwardPass1800.MIRROR_CUBES
    light = af.SLight(environment_rotation=0.0)
    assert np.allclose(skyforward.sky_values(light, mirrored=True)["cMtxSkyRotDay"], af.environment_turn(light))
    assert np.allclose(skyforward.sky_values(light)["cMtxSkyRotDay"], af.cube_turn(light))


def test_a_turned_lookup_into_a_mirrored_map_reads_the_games_direction_for_any_turn():
    """The audit of 2026-11-14: 2205's sky took the setting's turn unmirrored over its mirrored boxes, right only for
    its turn 0; a turn into a mirrored map is ``M T M``, for ``mul(T, v)`` and ``mul(v, T)`` alike (shader-compile.md
    11 "Sky cubes")."""
    pytest.importorskip("PySide6")
    import numpy as np

    from annotoolbox.render import fxcdraw, skyforward

    m = np.diag([-1.0, 1.0, 1.0, 1.0])
    light = af.SLight(environment_rotation=0.1)
    game = af.environment_turn(light)
    view = skyforward.sky_values(light, mirrored=True)["cMtxSkyRotDay"]
    for v in ([1.0, 0.2, 0.3, 0.0], [-0.4, 0.5, 0.9, 0.0]):
        v = np.asarray(v)  # a direction in the view's axes; the mirrored map's texel u holds the game's at M u
        assert np.allclose((v @ view) @ m, (v @ m) @ game)  # row vectors: mul(v, T)
        assert np.allclose(m @ (view @ v), game @ (m @ v))  # column vectors: mul(T, v)
    assert np.allclose(fxcdraw.mirrored_turn(game[:3, :3]), view[:3, :3])  # a 3x3 the same way
    assert not np.allclose(view, game)  # the turn the other way


@pytest.mark.skipif(not os.environ.get("ANNO2205_DIR"), reason="needs an installed Anno 2205 (ANNO2205_DIR)")
def test_the_installed_games_temperate_setting_reads():
    from annotoolbox.sources import gametree

    light = af.read_light(gametree.open_game("anno2205", Path(os.environ["ANNO2205_DIR"])))
    assert light.time == af.DAY and min(light.diffuse) > 0.5 and min(light.ambient) > 0
    assert light.glow > 0 and light.horizon_falloff > 1 and light.skybox.endswith(".dds")
