"""The sky, the moving cloud shadows and the post chain of the island look by 1800's and 2205's own shaders
(``editor.desktop.desktopfinish1800``): the clouds' drift, what is read from the install and what a missing file
leaves, the parts that do not compile said once, the view's own light in the game's units under the chain.
docs/rendering/island-1800-2205-view.md section 14."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from annotoolbox.editor.desktop import desktopfinish1800 as fin
from annotoolbox.render import ambientforward, postforward


def test_the_clouds_drift_with_the_settings_speed_both_layers_the_same_distance_over_the_ground():
    light = ambientforward.Light(clouds=(2.0, 1.0, 1.025, 0.2))  # 1800's moderate_01: scale 2, speed 1
    assert fin.cloud_deltas(light, 0.0) == [0.0, 0.0, 0.0, 0.0]
    d = fin.cloud_deltas(light, 10.0)
    far = 10.0 * fin.CLOUD_WIND  # world units moved
    assert d[0] == pytest.approx(far * 0.01 * 2.0) and d[2] == pytest.approx(far * 0.015 * 2.0)  # the shader's scales
    assert d[1] == d[3] == 0.0  # along the wind's x
    assert fin.cloud_deltas(light, 10.0, (0.0, 1.0))[1] == pytest.approx(d[0])


def test_the_plants_wind_is_the_settings_strength_on_the_views_clock_and_its_integral():
    assert fin.read_wind("<Info><WindStrength>1.111000</>\n<Water>") == pytest.approx(1.111)
    assert fin.read_wind("<Info/>") == fin.WIND_STRENGTH
    v = fin.wind_values(2.0, 10.0, (0.0, 1.0))
    assert v["cTimer"][0] == v["cTimer"][2] == 10.0 and v["cWindStrength"] == 2.0
    assert v["cWindDirection"] == [0.0, 1.0] and v["cAccumulatedWind"] == [0.0, 20.0]  # += dir x strength x dt


def test_the_exposure_settles_once_its_change_is_no_longer_seen():
    assert fin.settling(False, True, 1.0, 1.0)  # a read-back on its way
    assert fin.settling(True, False, 1.0, 1.01)
    assert not fin.settling(True, False, 1.0, 1.0005)  # the moving clouds and plants: never exactly equal
    assert not fin.settling(False, False, 1.0, 1.0)


def test_the_install_is_read_once_and_a_file_that_does_not_read_is_left_out():
    light = ambientforward.Light(skybox="sky.dds", lut="grade.png")
    files = SimpleNamespace(read=lambda path: (_ for _ in ()).throw(KeyError(path)))
    got = fin.read_parts(files, light, "anno1800")
    assert got["cubes"] == {"sky.dds": None} and got["maps"] == {} and "lut" not in got
    assert fin.CLOUDS["anno1800"].endswith("cloud_layer_0.dds") and fin.CLOUDS["anno2205"].endswith("sky/clouds.dds")


def test_a_part_that_does_not_compile_is_kept_as_its_reason():
    def fails(*_args):
        raise RuntimeError("X3000")

    source = SimpleNamespace(effect_pass=fails, call_stage=fails)
    got = fin.compile_parts(source, "anno1800")
    assert set(got) == {"sky", "post", "eye"} and all("X3000" in why for why in got.values())
    assert set(fin.compile_parts(source, "anno2205")) == {"sky", "post"}  # 2205 has no eye adaptation


def test_the_views_own_parts_take_the_games_units_only_under_its_chain():
    light = ambientforward.Light()
    finish = object.__new__(fin.IslandFinish)
    finish.game, finish.light, finish.post = "anno1800", light, None
    lt = {"sunDir": (0.0, 1.0, 0.0), "finish": 0, "exposure": 1.0}
    assert finish.view_light(lt) is lt  # no chain: the view's own finish
    finish.post = "chain"
    turned = finish.view_light(lt)
    assert turned["sunDir"] == lt["sunDir"] and turned["exposure"] == 0.6
    stored = postforward.STORED["anno1800"]  # 1800's models store a quarter of the light: the view's parts too
    assert turned["flatAmbient"] == pytest.approx(tuple(c * stored for c in light.ambient))


def test_117s_grade_is_not_read_for_an_1800_or_2205_island():
    """Their looks grade in their own chains; the view's own finish leaves them ungraded instead of reading 117's
    ambient setting (which said "no game grade" on every such island)."""
    from annotoolbox.editor.desktop.desktopframe import FramePasses

    said, asked = [], []
    for game in ("anno1800", "anno2205"):
        view = SimpleNamespace(
            session=SimpleNamespace(island={"game": game}),
            textures={},
            _message=said.append,
            loader=SimpleNamespace(get=lambda *a: asked.append(a)),
            editor=SimpleNamespace(grade_lut=None),
        )
        assert FramePasses._lut(view) is None
    assert said == [] and asked == []
    view = SimpleNamespace(
        session=SimpleNamespace(island={"game": "anno117"}),
        textures={},
        _message=said.append,
        loader=SimpleNamespace(get=lambda *a: ValueError("no setting")),
        editor=SimpleNamespace(grade_lut=None),
    )
    assert FramePasses._lut(view) is None and len(said) == 1  # 117's island still reads it
