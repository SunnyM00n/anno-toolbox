"""The UI editor's "Preview settings" panel (``uiedit.previewpanel``): built on a stand-in catalogue, it writes every
change through the store (and so into ``current()``), resets one value or all, keeps the values in the project, takes the
research presets and shows the note that live values win."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from tests.uiedit.uieditfakes import open_scene, settle_window

from annotoolbox.uiedit import previewcatalogue as pc
from annotoolbox.uiedit import previewdefaults as pd
from annotoolbox.uiedit import previewpanel, previewwidgets

CHECKED, UNCHECKED = Qt.CheckState.Checked, Qt.CheckState.Unchecked


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def person(role, g, name, region="Roman") -> pd.SCharacter:
    return pd.SCharacter(role, g, name, g + 1, f"p/{g}.png", g + 2, f"e/{g}.png", 91304, (1, 2, 3), region)


def catalogue() -> pc.SCatalogue:
    cat = pc.SCatalogue(
        characters={
            "emperor": [person("emperor", 31721, "Lucius"), person("emperor", 31637, "Calidus")],
            "rival": [
                person("rival", 31100, "Tarragon"),
                person("rival", 31102, "Athr"),
                person("rival", 31104, "Concordia"),
                person("rival", 31099, "Dorian"),
            ],
            "pirate": [person("pirate", 37495, "Caeso"), person("pirate", 153069, "Kandis", "Egyptian")],
        }
    )
    cat.portraits = [pc.SOption(38962, "Marcia", "p/38962.png"), pc.SOption(5, "Other", "p/5.png")]
    cat.emblems = [pc.SOption(125990, "Sphinx", "e/1.png"), pc.SOption(6, "Eagle", "e/2.png")]
    cat.colours = [pc.SOption(91304, "Lapis", rgb=(38, 97, 156)), pc.SOption(7, "Red", rgb=(200, 0, 0))]
    cat.banner = {"BannerShape": [pc.SOption(92736, "Round"), pc.SOption(8, "Square")]}
    cat.titles = {"prefix": [pc.SOption(9, "Lord")], "suffix": [pc.SOption(10, "the Great")]}
    cat.regions = [pc.SOption(pd.LATIUM, "Latium"), pc.SOption(pd.ALBION, "Albion")]
    cat.tiers = [pc.SOption(1499, "Liberti", "Roman", tier=1), pc.SOption(1496, "Equites", "Roman", tier=2),
                 pc.SOption(1500, "Plebs", "Celtic", tier=1)]  # fmt: skip
    cat.dlcs = [pc.SOption(11, "Nile"), pc.SOption(12, "Albion DLC")]
    cat.unlocks = [pc.SOption(21, "Forum", group="Public"), pc.SOption(22, "Temple", group="Public"),
                   pc.SOption(23, "Mine", group="Production")]  # fmt: skip
    cat.research = [pc.SOption(31, "Tools", group="Economy", tier=1), pc.SOption(32, "Roads", group="Economy", tier=2),
                    pc.SOption(33, "Harvest", group="Economy", tier=3, repeatable=True),
                    pc.SOption(34, "Legion", group="Army", tier=1),
        pc.SOption(35, "Rite", group="Nile", tier=1, dlc=11),
        pc.SOption(36, "Cut", group=pc.UNUSED, tier=1)]  # fmt: skip
    cat.setup = {"SetupStartCredits": [pc.SOption(0, "Low"), pc.SOption(1, "High")],
                 "SetupTaxes": [pc.SOption(0, "Fair"), pc.SOption(2, "Spare")]}  # fmt: skip
    cat.presets = [pc.SOption(41206, "Campaign"), pc.SOption(41207, "Hard")]
    return cat


def panel_for(tmp_path, project: bool = False, note=None, icons=None):
    file = tmp_path / "preview.json" if project else None
    store = pd.CStore({}, "anno117", file)
    return previewpanel.CPreviewPanel(store, catalogue(), icons, note)


def section(panel, cls):
    return next(s for s in panel.m_sections if isinstance(s, cls))


def test_every_tab_is_built_with_tool_tips_and_no_access_key_twice(qt, tmp_path):
    panel = panel_for(tmp_path)
    from annotoolbox.suite import widgets

    assert panel.m_tabs.count() == 7 and not panel.m_loading
    for s in panel.m_sections:
        letters = widgets.access_letters([panel.m_tabs.tabText(i) for i in range(panel.m_tabs.count())]
                                         + [panel.m_keep.text(), panel.m_reset_all.text(), panel.m_reset_played.text()]
                                         + [w.text() for w in s.m_keyed])  # fmt: skip
        assert len(letters) == len(set(letters))
    for button in panel.m_resets:
        assert button.toolTip() and button.accessibleName()
    for control in (panel.m_keep, panel.m_reset_all, panel.m_reset_played):
        assert control.toolTip()
    for s in panel.m_sections:
        assert all(label.buddy() is not None and label.toolTip() for label in s.m_keyed if hasattr(label, "buddy"))


def test_a_change_reaches_the_store_and_current(qt, tmp_path):
    panel = panel_for(tmp_path)
    seen = []
    panel.m_store.m_listeners.append(seen.append)
    player = section(panel, previewpanel.CParticipantSection)
    assert player.m_name.text() == "Marcia" and pd.text_for("PlayerName", panel.m_store.m_defaults) == "Marcia"
    player.m_name.editingFinished.emit()  # the campaign's own name is no change
    assert panel.m_store.m_defaults.participant_name == ""
    player.m_name.setText("Livia")
    player.m_name.editingFinished.emit()
    assert panel.m_store.m_defaults.participant_name == "Livia" and seen[-1].participant().name == "Livia"
    assert pd.current(SimpleNamespace(m_defaults=panel.m_store.m_defaults)).participant().name == "Livia"
    player.m_grids["participant_portrait"][0].picked.emit(5)
    assert panel.m_store.m_defaults.participant_portrait == 5
    player.m_grids["participant_portrait"][0].picked.emit(38962)  # the campaign's own: no change
    assert panel.m_store.m_defaults.participant_portrait == 0
    player.m_parts["banner.BannerShape"].picked.emit(8)
    assert panel.m_store.m_defaults.option("banner.BannerShape") == 8
    player.m_combos["title.prefix"].picked.emit(9)
    assert panel.m_store.m_defaults.option("title.prefix") == 9
    player.m_combos["title.prefix"].picked.emit(0)
    assert "title.prefix" not in panel.m_store.m_defaults.options


def test_characters_platform_values_and_session_write_through(qt, tmp_path):
    panel = panel_for(tmp_path)
    store = panel.m_store
    people = section(panel, previewpanel.CCharactersSection)
    pirates = people.m_lists["pirate"]
    names = [pirates.item(i).text() for i in range(pirates.count())]
    assert names == ["Caeso", "Kandis"]
    pirates.item(0).setCheckState(UNCHECKED)
    assert store.m_defaults.characters == {"pirate": [153069]}
    emperors = people.m_lists["emperor"]
    emperors.item(1).setCheckState(CHECKED)  # one emperor reigns: Lucius goes
    assert emperors.item(0).checkState() == UNCHECKED and store.m_defaults.characters["emperor"] == [31637]
    pirates.item(0).setCheckState(CHECKED)
    panel.reset_names(("characters:pirate",))
    assert "pirate" not in store.m_defaults.characters
    platform = section(panel, previewpanel.CPlatformSection)
    platform.m_inputs.button(2).setChecked(True)
    platform.m_sessions.button(1).setChecked(True)
    assert store.m_defaults.input == "playstation" and store.m_defaults.session == "multi"
    platform.m_dlcs.item(0).setCheckState(UNCHECKED)
    assert store.m_defaults.dlcs == [12]
    platform.m_dlcs.item(0).setCheckState(CHECKED)
    assert store.m_defaults.dlcs is None  # every DLC is the default
    values = section(panel, previewpanel.CValuesSection)
    values.m_numbers["money"].setValue(5000)
    values.m_season.setText("winter")
    values.m_season.editingFinished.emit()
    values.m_language.activated.emit(2)
    assert (store.m_defaults.money, store.m_defaults.season, store.m_defaults.language) == (5000, "winter", "german")
    setup = section(panel, previewpanel.CSetupSection)
    setup.m_combos["setup.SetupTaxes"].picked.emit(0)
    setup.m_combos["setup.SetupTaxes"].picked.emit(2)  # the campaign's own value is no change
    assert "setup.SetupTaxes" not in store.m_defaults.options
    setup.m_combos["setup.SetupStartCredits"].picked.emit(1)
    setup.m_spins["setup.GameSeed"].setValue(7)
    setup.m_region.picked.emit(pd.ALBION)
    assert store.m_defaults.option("setup.SetupStartCredits") == 1 and store.m_defaults.option("setup.GameSeed") == 7
    assert store.m_defaults.region == pd.ALBION
    setup.use_campaign()
    assert store.m_defaults.region == pd.LATIUM and not store.m_defaults.options


def test_progression_tiers_and_the_locked_checklist(qt, tmp_path):
    panel = panel_for(tmp_path)
    store, prog = panel.m_store, section(panel, previewpanel.CProgressionSection)
    assert prog.m_tiers[1496][0].isChecked()  # everything is reached by default
    prog.m_preset.picked.emit("tier:1499")
    assert store.m_defaults.progression == "tier:1499" and not prog.m_tiers[1496][0].isChecked()
    prog.m_preset.picked.emit("start")
    assert store.m_defaults.progression == "start"
    prog.m_preset.picked.emit("all")
    assert store.m_defaults.progression == "all"
    prog.m_tiers[1500][0].setChecked(False)
    assert 1500 not in store.m_defaults.tiers and 1499 in store.m_defaults.tiers
    leaves = {leaf.data(0, Qt.ItemDataRole.UserRole): leaf for leaf in prog._leaves()}
    leaves[22].setCheckState(0, CHECKED)
    assert store.m_defaults.locked == [22] and store.m_defaults.is_locked(22)
    prog.find.setText("mine")
    prog.m_lock_shown.click()
    assert store.m_defaults.locked == [22, 23]
    prog.find.setText("")
    prog.m_unlock_shown.click()
    assert store.m_defaults.locked == []


def test_the_research_tree_and_its_presets(qt, tmp_path):
    panel = panel_for(tmp_path)
    store, research = panel.m_store, section(panel, previewpanel.CResearchSection)
    leaf = {n.data(0, Qt.ItemDataRole.UserRole).guid: n for n in research._leaves()}
    assert all(n.checkState(0) == CHECKED for g, n in leaf.items() if g != 33) and research.m_counts[33].value() == 1
    leaf[31].setCheckState(0, UNCHECKED)
    assert store.m_defaults.options == {"research.31": 0.0} and store.m_defaults.researched(31, 1) == 0.0
    research.m_counts[33].setValue(3)
    assert store.m_defaults.researched(33, 3) == 3.0
    research.m_preset.picked.emit("none")  # a preset drops the nodes set by hand and sets the rest
    assert store.m_defaults.research == "none" and not store.m_defaults.options
    assert all(n.checkState(0) == UNCHECKED for g, n in leaf.items() if g != 33) and research.m_counts[33].value() == 0
    research.m_preset.picked.emit("tier:1")
    assert (
        leaf[31].checkState(0) == CHECKED and leaf[32].checkState(0) == UNCHECKED and leaf[34].checkState(0) == CHECKED
    )
    leaf[32].setCheckState(0, CHECKED)
    assert store.m_defaults.options == {"research.32": 1.0}
    research.m_preset.picked.emit("all")
    assert store.m_defaults.research == "all" and all(n.checkState(0) == CHECKED for g, n in leaf.items() if g != 33)
    research.find.setText("legion")
    assert leaf[34].parent().childCount() == 1 and not leaf[34].isHidden() and leaf[31].isHidden()
    assert "(" in research.m_tree.topLevelItem(0).text(0)


def test_a_reset_of_one_value_and_of_all_and_of_the_play_modes_choices(qt, tmp_path):
    panel = panel_for(tmp_path)
    store = panel.m_store
    assert not panel.m_reset_all.isEnabled() and not panel.m_reset_played.isEnabled()
    store.set("money", 7)
    store.set("goods", 9, play=True)
    store.set("options.banner.BannerShape", 8)
    assert panel.m_reset_all.isEnabled() and panel.m_reset_played.isEnabled()
    money = next(b for b in panel.m_resets if b.m_names == ("money",))
    assert money.isEnabled()
    money.click()
    assert store.m_defaults.money == pd.CAMPAIGN_DEFAULTS.money and store.m_defaults.goods == 9
    assert not money.isEnabled()
    assert section(panel, previewpanel.CValuesSection).m_numbers["goods"].value() == 9  # the panel follows the store
    panel.m_reset_played.click()
    assert store.m_defaults.goods == pd.CAMPAIGN_DEFAULTS.goods and store.m_defaults.options
    panel.m_reset_all.click()
    assert pd.changes(store.m_defaults) == {} and not panel.m_reset_all.isEnabled()
    assert section(panel, previewpanel.CParticipantSection).m_parts["banner.BannerShape"].current_guid() == 92736


def test_keep_in_project(qt, tmp_path):
    panel = panel_for(tmp_path, project=True)
    store = panel.m_store
    assert panel.m_keep.isEnabled() and not panel.m_keep.isChecked()
    store.set("money", 11)
    panel.m_keep.setChecked(True)
    assert store.m_per_project and (tmp_path / "preview.json").is_file()
    store.set("money", 12)
    assert '"money": 12' in (tmp_path / "preview.json").read_text(encoding="utf-8")
    panel.m_keep.setChecked(False)
    assert not store.m_per_project and not (tmp_path / "preview.json").exists()
    without = panel_for(tmp_path)
    assert not without.m_keep.isEnabled() and without.m_keep.toolTip()


def test_loading_the_catalogue_later_and_a_game_without_one(qt, tmp_path):
    store = pd.CStore({}, "anno117")
    panel = previewpanel.CPreviewPanel(store, None, None, None, loading=True)
    assert panel.m_state.text() and panel.m_loading
    assert section(panel, previewpanel.CCharactersSection).m_lists["pirate"].count() == 3  # the campaign's three
    store.set("input", "xbox")
    panel.set_catalogue(catalogue())
    assert not panel.m_loading and not panel.m_state.text()
    assert section(panel, previewpanel.CPlatformSection).m_inputs.button(1).isChecked()
    panel.set_catalogue(None)
    assert panel.m_state.text() and panel.m_tabs.count() == 7
    empty = previewpanel.CPreviewPanel()
    assert empty.m_tabs.count() == 0 and empty.m_state.text() and not empty.m_reset_all.isEnabled()


def test_the_note_shows_while_live_values_win(qt, tmp_path):
    live = [False]
    panel = panel_for(tmp_path, note=lambda: live[0])
    assert panel.m_note.isHidden()
    live[0] = True
    panel.refresh_note()
    assert not panel.m_note.isHidden() and panel.m_note.text()


def test_pictures_are_read_off_the_windows_thread_and_fill_the_grids(qt, tmp_path):
    from annotoolbox.suite.background import CBackground

    asked = []

    def load(path):
        asked.append(path)
        image = QImage(8, 8, QImage.Format.Format_RGBA8888)
        image.fill(0xFF00FF00)
        return image

    background = CBackground()
    icons = previewwidgets.CIcons(background, load)
    panel = panel_for(tmp_path, icons=icons)
    background.join()
    for _ in range(50):
        qt.processEvents()
    assert "p/38962.png" in asked and icons.get("p/38962.png") is not None
    grid = section(panel, previewpanel.CParticipantSection).m_grids["participant_portrait"][0]
    assert not grid.item(0).icon().isNull()


def test_the_panel_is_the_windows_dock_and_follows_its_store(window, qt, tmp_path):
    """In the window: the dock exists, a game's store is shown, a change lays the preview out again."""
    panel = window.preview_panel
    assert "Preview settings" in window.docks and window.docks["Preview settings"].widget() is panel
    assert panel.m_store is window.preview_store and panel.m_tabs.count() == 7
    window.show_dock("Preview settings")
    open_scene(window, next(iter(window.game.m_files.m_entries)))
    settle_window(window)
    section(panel, previewpanel.CPlatformSection).m_inputs.button(1).setChecked(True)
    assert window.m_ctx.m_defaults.input == "xbox"
    settle_window(window)
    assert window.preview_store.m_defaults.input == "xbox"


def test_a_dlcs_research_tree_follows_the_owned_dlcs_and_cut_research_has_its_own_group(qt, tmp_path):
    panel = panel_for(tmp_path)
    research = section(panel, previewpanel.CResearchSection)
    names = [research.m_tree.topLevelItem(i).text(0) for i in range(research.m_tree.topLevelItemCount())]
    assert names[-1].startswith("Research cut") and names.index("Nile (1/1)") > names.index("Economy (3/3)")
    nile = research.m_tree.topLevelItem(names.index("Nile (1/1)"))
    assert not nile.isDisabled()
    platform = section(panel, previewpanel.CPlatformSection)
    platform.m_dlcs.item(0).setCheckState(UNCHECKED)  # the Nile DLC (11) is not owned
    assert nile.isDisabled() and nile.child(0).isDisabled() and "not owned" in nile.text(0)
    platform.m_dlcs.item(0).setCheckState(CHECKED)
    assert not nile.isDisabled() and "not owned" not in nile.text(0)


def test_a_white_icon_goes_on_a_dark_tile(qt):
    import numpy as np

    white = np.zeros((8, 8, 4), dtype=np.uint8)
    white[2:6, 2:6] = 255
    coloured = white.copy()
    coloured[..., :3] = (200, 30, 30)
    assert previewwidgets.is_white(white) and not previewwidgets.is_white(coloured)
    image = QImage(8, 8, QImage.Format.Format_RGBA8888)
    image.fill(0xFFFFFFFF)
    tile = previewwidgets.on_tile(image)
    assert tile.width() == previewwidgets.PICTURE and tile.pixelColor(4, 32).red() < 100  # dark ground, light glyph
    assert tile.pixelColor(32, 32).red() > 200
