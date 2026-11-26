"""The rendering quality's menu entries and Custom window (``suite/qualitydialog.py``): the entries and which is
marked, a preset setting every area, an area set by hand making the quality Custom, and what the settings keep."""

from __future__ import annotations

from PySide6.QtWidgets import QMenu

from annotoolbox.infra.i18n import tr
from annotoolbox.render import quality
from annotoolbox.suite import qualitydialog


def test_the_menu_offers_the_games_settings_its_presets_and_custom(qt):
    chosen = []
    menu = QMenu()
    actions = qualitydialog.add_actions(menu, chosen.append)
    assert list(actions) == [qualitydialog.PLAYER, *quality.PRESETS, quality.CUSTOM]
    actions["High"].trigger()
    assert chosen == ["High"]
    qualitydialog.check_actions(actions, quality.preset("anno117", "High"))
    assert actions["High"].isChecked() and not actions[qualitydialog.PLAYER].isChecked()
    following = quality.resolve("anno117", None, player_text='{"RenderEngine": {"QualitySetting": "VeryHigh"}}')
    qualitydialog.check_actions(actions, following)
    assert actions[qualitydialog.PLAYER].isChecked() and not actions["VeryHigh"].isChecked()
    name = tr("common.quality_very_high")
    assert actions[qualitydialog.PLAYER].text() == tr("common.quality_as_in_game_named", name=name)
    qualitydialog.check_actions(actions, None)  # an older game: nothing to set
    assert not any(action.isEnabled() for action in actions.values())


def test_a_preset_sets_every_area_and_an_area_by_hand_makes_it_custom(qt):
    dialog = qualitydialog.CQualityDialog("anno117", quality.preset("anno117", "Medium"))
    dialog.m_preset.setCurrentIndex(dialog.m_preset.findData("Low"))
    assert dialog.levels() == quality.preset_levels("Low") and dialog.stored() == "Low"
    dialog.m_areas["Shadow"].setCurrentIndex(4)
    assert dialog.m_preset.currentData() == quality.CUSTOM
    stored = dialog.stored()
    assert stored["preset"] == quality.CUSTOM and stored["levels"]["Shadow"] == 4
    assert quality.resolve("anno117", stored).shadow_cascades == 4
    dialog.m_areas["Shadow"].setCurrentIndex(0)  # the levels are Low's again
    assert dialog.m_preset.currentData() == "Low"
    assert dialog.m_areas["Terrain"].count() == quality.AREAS["Terrain"] + 1


def test_the_render_scale_of_ours_makes_a_custom_record(qt):
    dialog = qualitydialog.CQualityDialog("anno117", quality.preset("anno117", "High"))
    dialog.m_scale.setCurrentIndex(dialog.m_scale.findData(0.75))
    stored = dialog.stored()
    assert stored["preset"] == quality.CUSTOM and quality.resolve("anno117", stored).render_scale == 0.75


def test_a_render_scale_off_the_list_stays_when_the_window_is_confirmed(qt):
    """A Toolbox 0.3 Custom record kept any scale (0.8 from Medium): the list fell back to 100 %, and OK dropped it."""
    current = quality.resolve("anno117", {"preset": "custom", "base": "medium", "render_scale": 0.8})
    dialog = qualitydialog.CQualityDialog("anno117", current)
    assert dialog.m_scale.currentData() == 0.8
    assert dialog.stored()["render_scale"] == 0.8
    scales = [dialog.m_scale.itemData(k) for k in range(dialog.m_scale.count())]
    assert scales == sorted(scales, reverse=True) and set(qualitydialog.RENDER_SCALES) < set(scales)


def test_the_game_entry_takes_the_setting_out_and_a_preset_keeps_its_name():
    assert qualitydialog.choose(None, "anno117", qualitydialog.PLAYER, None) == (True, None)
    assert qualitydialog.choose(None, "anno117", "Low", None) == (True, "Low")
    kept = {"anno117": "Low", "anno1800": "High"}
    assert qualitydialog.replace_game(kept, "anno117", None) == {"anno1800": "High"}
    assert qualitydialog.replace_game(None, "anno117", "Medium") == {"anno117": "Medium"}
