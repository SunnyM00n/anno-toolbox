"""The island editor's Performance switches (island-editor-view "Performance switches"): each reaches the options the
renderer reads and makes the view skip its part, the ribbon group sits beside Layers and the choice is kept."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.editor.editorfakes import desktop_window, open_fake_island

from annotoolbox.editor.desktop import desktopeditor, desktopribbon, desktopsea
from annotoolbox.editor.desktop.desktopgame1800 import CGameIsland1800
from annotoolbox.editor.desktop.desktoprender import CRenderer, SViewOptions
from annotoolbox.render.gameextras import SSkyParts
from annotoolbox.suite.settings import CSettings

SWITCHES = desktopeditor.PERFORMANCE
OFF = desktopeditor.PERFORMANCE_OFF


class FakeRenderer:
    """What ``CRenderer._water_clock`` reads of the renderer: its start and the held moment."""

    def __init__(self) -> None:
        self._m_start = 0.0
        self._m_held_s = None


class FakeMirrorTarget:
    """A framebuffer of the sea's mirror: counts clears and uses."""

    def __init__(self, log: list, name: str) -> None:
        self.log, self.name = log, name

    def use(self) -> None:
        self.log.append(f"use {self.name}")

    def clear(self, *colour, depth: float = 1.0) -> None:
        self.log.append(f"clear {self.name}")


class FakeCompositePass:
    """The gamma composite of the mirror: records a draw."""

    def __init__(self, log: list) -> None:
        self.log = log

    def draw(self, *args, **kwargs) -> None:
        self.log.append("gamma draw")


def test_every_switch_is_on_by_default_and_a_held_clock_is_either_the_still_picture_or_the_water_motion_off():
    options = SViewOptions()
    assert all(getattr(options, key) is (key not in OFF) for key in SWITCHES) and not options.is_clock_held
    # the cache's picture differs until the game's own blend is known; the clusters change it and gained nothing
    assert OFF == {"cloud_cache", "distance_clusters"}
    options.waves = False
    assert options.is_clock_held
    options.waves, options.particle_time = True, 3.0
    assert options.is_clock_held
    assert options.sky_parts == SSkyParts(True, True, True, False)  # accumulated over frames as the game does
    assert SViewOptions(clouds=False).sky_parts == SSkyParts(True, False, True, False)
    assert SViewOptions(sky_haze=False).sky_parts == SSkyParts(False, False, True, False)  # no sky, no clouds on it
    parts = SViewOptions(temporal_scattering=False, cloud_cache=True).sky_parts
    assert not parts.is_temporal and parts.is_cache_kept


def test_the_water_clock_is_held_from_the_moment_the_water_motion_goes_off_until_it_is_on_again(monkeypatch):
    from annotoolbox.editor.desktop import desktoprender

    now = iter([10.0, 12.0, 15.0, 16.0, 20.0])
    monkeypatch.setattr(desktoprender.time, "monotonic", lambda: next(now))
    renderer, options = FakeRenderer(), SViewOptions()
    clock = lambda: CRenderer._water_clock(renderer, options)  # noqa: E731
    assert clock() == 10.0  # live
    options.waves = False
    assert [clock(), clock(), clock()] == [12.0, 12.0, 12.0]  # held at the first frame without waves
    options.waves = True
    assert clock() == 15.0 and renderer._m_held_s is None
    options.particle_time = 5.0
    assert clock() == 5.0  # a still picture's moment wins


def _mirror():
    log: list[str] = []
    own = {
        "fbo": FakeMirrorTarget(log, "mirror"),
        "gamma_fbo": FakeMirrorTarget(log, "gamma"),
        "color": object(),
        "gamma": SimpleNamespace(native="gamma map"),
    }
    sea = SimpleNamespace(
        m_renderer=SimpleNamespace(m_ctx=SimpleNamespace(viewport=None)),
        m_empty_mirror=None,
        _mirror_targets=lambda: own,
        _pass=lambda name, fragment: FakeCompositePass(log),
    )
    targets = {"msaa": FakeMirrorTarget(log, "msaa"), "size": (64, 64)}
    return sea, targets, log


def test_the_reflections_switch_draws_no_mirror_and_clears_the_map_once():
    sea, targets, log = _mirror()
    options = SimpleNamespace(reflections=False)
    for _frame in range(3):
        assert desktopsea.CIslandSea._reflection(sea, None, options, targets) == "gamma map"
    assert log.count("clear mirror") == 1 and log.count("gamma draw") == 1  # no scene pass, one clear for all frames


def test_the_ambient_occlusion_switch_leaves_the_pass_out_and_lets_go_of_the_pre_pass():
    dropped = []
    ssao = SimpleNamespace(m_error="")
    island = SimpleNamespace(
        m_quality=None, m_ssao=ssao, m_frame=SimpleNamespace(drop_prepass=lambda: dropped.append(True))
    )
    assert CGameIsland1800._ambient(island, SViewOptions()) is ssao and not dropped
    assert CGameIsland1800._ambient(island, SViewOptions(ambient_occlusion=False)) is None and dropped


def test_the_ribbon_has_a_performance_tab_and_leaves_out_what_a_game_does_not_have():
    tabs = dict(desktopribbon.TABS)  # a tab of their own: on Home they left half its buttons without a key
    group = [
        item.id for item in dict(tabs[desktopribbon.N_("islands.performance")])[desktopribbon.N_("islands.performance")]
    ]
    assert group == [f"perf_{key}" for key in SWITCHES]
    only_2070 = {item.id for item in desktopribbon.items(desktopribbon.tabs_for(("anno2070",), set()))}
    assert {"perf_shadows", "perf_waves", "perf_reflections"} <= only_2070
    assert not {"perf_sky_haze", "perf_clouds", "perf_temporal_scattering", "perf_cloud_cache",
                "perf_ambient_occlusion"} & only_2070  # fmt: skip
    only_1800 = {item.id for item in desktopribbon.items(desktopribbon.tabs_for(("anno1800",), set()))}
    assert "perf_ambient_occlusion" in only_1800 and "perf_reflections" not in only_1800


def test_a_switch_reaches_the_view_and_the_settings_and_the_next_island_opens_with_it(tmp_path):
    qt, main = desktop_window(tmp_path)
    doc = open_fake_island(main)
    options = main.m_view.m_opts
    assert all(main.m_actions[f"perf_{key}"].isChecked() is (key not in OFF) for key in SWITCHES) and all(
        getattr(options, key) is (key not in OFF) for key in SWITCHES
    )
    for key in SWITCHES:
        action = main.m_actions[f"perf_{key}"]
        if action.isEnabled() and key not in OFF:
            action.trigger()
        else:  # greyed (the island has nothing of that kind: ``performance_needs``) or off already: the call alone
            doc.set_performance(key, False)
        assert getattr(options, key) is False and not action.isChecked()
    assert main.m_app.m_settings.view_switches == dict.fromkeys(SWITCHES, False)
    assert CSettings.load(main.m_app.m_settings_path).view_switches == dict.fromkeys(SWITCHES, False)  # on disk
    main.m_actions["perf_waves"].trigger()
    assert options.waves is True and main.m_app.m_settings.view_switches["waves"] is True
    main.m_look_actions["shadows"].trigger()  # View > Rendering's entry is the same switch
    assert options.shadows is True and main.m_actions["perf_shadows"].isChecked()
    for key in SWITCHES:
        setattr(options, key, True)
    doc.apply_performance()  # a new island's view takes what the settings keep
    assert [getattr(options, key) for key in SWITCHES] == [True, True, *[False] * (len(SWITCHES) - 2)]  # shadows, waves
    assert [getattr(options, key) for key in SWITCHES] == [True, True] + [False] * (len(SWITCHES) - 2)
    main.close()


def test_a_switch_saved_in_the_settings_window_reaches_the_open_island(tmp_path):
    qt, main = desktop_window(tmp_path)
    open_fake_island(main)
    main.m_app.set_settings({"view_switches": {"shadows": False}})  # what the settings window saves
    main.settings_changed()
    assert main.m_view.m_opts.shadows is False and not main.m_actions["perf_shadows"].isChecked()
    main.close()


def test_a_switch_is_greyed_where_the_island_has_nothing_to_skip(tmp_path):
    qt, main = desktop_window(tmp_path)
    doc = open_fake_island(main)
    needs = doc.performance_needs()
    assert needs == {"sea_mirror": False, "game_sky": False, "game_occlusion": False}  # an island of no such game
    assert main.m_actions["perf_shadows"].isEnabled() and not main.m_actions["perf_reflections"].isEnabled()
    main.close()
