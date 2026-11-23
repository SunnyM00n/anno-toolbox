"""The settings window (``suite/settingsdialog.py``): its pages, and that each value comes back as it was set."""

from __future__ import annotations

from annotoolbox.infra.i18n import tr
from annotoolbox.render import rhiview
from annotoolbox.suite import settingsdialog
from annotoolbox.suite.extspecs import SSettingsRows
from annotoolbox.suite.settings import CSettings


def make(tmp_path, **fields):
    return settingsdialog.CSettingsDialog(CSettings(workspace=str(tmp_path), **fields))


def tab_names(dialog) -> list[str]:
    return [dialog.m_tabs.tabText(k) for k in range(dialog.m_tabs.count())]


def test_the_pages_are_general_games_and_rendering(qt, tmp_path):
    dialog = make(tmp_path)
    assert tab_names(dialog) == [tr("settings.page.general"), tr("settings.page.games"), tr("settings.page.rendering")]


def test_values_round_trip_the_general_and_rendering_settings(qt, tmp_path):
    dialog = make(tmp_path, check_updates=False, hardware=False, native_shaders=False, theme="dark", renderer="")
    values = dialog.values()
    assert values["check_updates"] is False and values["hardware"] is False and values["theme"] == "dark"
    assert values["native_shaders"] is False and values["renderer"] == ""
    dialog.m_check_updates.setChecked(True)
    dialog.m_hardware.setChecked(True)
    assert dialog.values()["check_updates"] is True and dialog.values()["hardware"] is True


def test_the_renderer_lists_only_what_the_machine_offers(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(rhiview, "offered", lambda: ["gl", "vulkan"])
    dialog = make(tmp_path)
    assert [dialog.m_renderer.itemData(k) for k in range(dialog.m_renderer.count())] == ["", "gl", "vulkan"]
    dialog.m_renderer.setCurrentIndex(dialog.m_renderer.findData("vulkan"))
    assert dialog.values()["renderer"] == "vulkan"


def test_a_renderer_the_machine_lacks_is_kept_not_dropped(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(rhiview, "offered", lambda: ["gl"])
    assert make(tmp_path, renderer="d3d12").values()["renderer"] == "d3d12"


def test_the_apps_rows_make_a_page_and_keep_the_other_switches(qt, tmp_path):
    rows = SSettingsRows(
        (("snap_places", "islands.menu.snap_places", {"off": "Off", "cell": "Cell"}),),
        (("view_switches", {"shadows": ("islands.perf_shadows", "islands.tip.perf_shadows")}),),
    )
    settings = CSettings(workspace=str(tmp_path), view_switches={"waves": False, "shadows": True})
    dialog = settingsdialog.CSettingsDialog(settings, rows=(rows,))
    assert tab_names(dialog)[-1] == tr("settings.page.island_editor")
    dialog.m_switches["view_switches"]["shadows"].setChecked(False)
    values = dialog.values()
    assert values["view_switches"] == {"waves": False, "shadows": False} and values["snap_places"] == "off"


def test_the_games_limit_still_applies(qt, tmp_path):
    dialog = settingsdialog.CSettingsDialog(CSettings(workspace=str(tmp_path)), games=("anno117",))
    assert set(dialog.m_edits) == {"anno117", "workspace", "mods1800"}
