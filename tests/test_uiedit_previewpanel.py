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
from tests.test_uiedit_compose import composed  # noqa: F401  # the window's fixture needs it
from tests.test_uiedit_speed import _open, _settle, scene_files, window  # noqa: F401

from annotoolbox.uiedit import previewcatalogue as pc
from annotoolbox.uiedit import previewdefaults as pd
from annotoolbox.uiedit import previewpanel, previewwidgets

CHECKED, UNCHECKED = Qt.CheckState.Checked, Qt.CheckState.Unchecked


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def person(role, g, name, region="Roman") -> pd.Character:
    return pd.Character(role, g, name, g + 1, f"p/{g}.png", g + 2, f"e/{g}.png", 91304, (1, 2, 3), region)


def catalogue() -> pc.Catalogue:
    cat = pc.Catalogue(
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
    cat.portraits = [pc.Option(38962, "Marcia", "p/38962.png"), pc.Option(5, "Other", "p/5.png")]
    cat.emblems = [pc.Option(125990, "Sphinx", "e/1.png"), pc.Option(6, "Eagle", "e/2.png")]
    cat.colours = [pc.Option(91304, "Lapis", rgb=(38, 97, 156)), pc.Option(7, "Red", rgb=(200, 0, 0))]
    cat.banner = {"BannerShape": [pc.Option(92736, "Round"), pc.Option(8, "Square")]}
    cat.titles = {"prefix": [pc.Option(9, "Lord")], "suffix": [pc.Option(10, "the Great")]}
    cat.regions = [pc.Option(pd.LATIUM, "Latium"), pc.Option(pd.ALBION, "Albion")]
    cat.tiers = [pc.Option(1499, "Liberti", "Roman", tier=1), pc.Option(1496, "Equites", "Roman", tier=2),
                 pc.Option(1500, "Plebs", "Celtic", tier=1)]  # fmt: skip
    cat.dlcs = [pc.Option(11, "Nile"), pc.Option(12, "Albion DLC")]
    cat.unlocks = [pc.Option(21, "Forum", group="Public"), pc.Option(22, "Temple", group="Public"),
                   pc.Option(23, "Mine", group="Production")]  # fmt: skip
    cat.research = [pc.Option(31, "Tools", group="Economy", tier=1), pc.Option(32, "Roads", group="Economy", tier=2),
                    pc.Option(33, "Harvest", group="Economy", tier=3, repeatable=True),
                    pc.Option(34, "Legion", group="Army", tier=1),
        pc.Option(35, "Rite", group="Nile", tier=1, dlc=11),
        pc.Option(36, "Cut", group=pc.UNUSED, tier=1)]  # fmt: skip
    cat.setup = {"SetupStartCredits": [pc.Option(0, "Low"), pc.Option(1, "High")],
                 "SetupTaxes": [pc.Option(0, "Fair"), pc.Option(2, "Spare")]}  # fmt: skip
    cat.presets = [pc.Option(41206, "Campaign"), pc.Option(41207, "Hard")]
    return cat


def panel_for(tmp_path, project: bool = False, note=None, icons=None):
    file = tmp_path / "preview.json" if project else None
    store = pd.Store({}, "anno117", file)
    return previewpanel.PreviewPanel(store, catalogue(), icons, note)


def section(panel, cls):
    return next(s for s in panel.sections if isinstance(s, cls))


def test_every_tab_is_built_with_tool_tips_and_no_access_key_twice(qt, tmp_path):
    panel = panel_for(tmp_path)
    from annotoolbox.suite import widgets

    assert panel.tabs.count() == 7 and not panel.loading
    for s in panel.sections:
        letters = widgets.access_letters([panel.tabs.tabText(i) for i in range(panel.tabs.count())]
                                         + [panel.keep.text(), panel.reset_all.text(), panel.reset_played.text()]
                                         + [w.text() for w in s.keyed])  # fmt: skip
        assert len(letters) == len(set(letters))
    for button in panel.resets:
        assert button.toolTip() and button.accessibleName()
    for control in (panel.keep, panel.reset_all, panel.reset_played):
        assert control.toolTip()
    for s in panel.sections:
        assert all(label.buddy() is not None and label.toolTip() for label in s.keyed if hasattr(label, "buddy"))


def test_a_change_reaches_the_store_and_current(qt, tmp_path):
    panel = panel_for(tmp_path)
    seen = []
    panel.store.listeners.append(seen.append)
    player = section(panel, previewpanel.PlayerSection)
    assert player.name.text() == "Marcia" and pd.text_for("PlayerName", panel.store.defaults) == "Marcia"
    player.name.editingFinished.emit()  # the campaign's own name is no change
    assert panel.store.defaults.player_name == ""
    player.name.setText("Livia")
    player.name.editingFinished.emit()
    assert panel.store.defaults.player_name == "Livia" and seen[-1].player().name == "Livia"
    assert pd.current(SimpleNamespace(defaults=panel.store.defaults)).player().name == "Livia"
    player.grids["player_portrait"][0].picked.emit(5)
    assert panel.store.defaults.player_portrait == 5
    player.grids["player_portrait"][0].picked.emit(38962)  # the campaign's own: no change
    assert panel.store.defaults.player_portrait == 0
    player.parts["banner.BannerShape"].picked.emit(8)
    assert panel.store.defaults.option("banner.BannerShape") == 8
    player.combos["title.prefix"].picked.emit(9)
    assert panel.store.defaults.option("title.prefix") == 9
    player.combos["title.prefix"].picked.emit(0)
    assert "title.prefix" not in panel.store.defaults.options


def test_characters_platform_values_and_session_write_through(qt, tmp_path):
    panel = panel_for(tmp_path)
    store = panel.store
    people = section(panel, previewpanel.CharactersSection)
    pirates = people.lists["pirate"]
    names = [pirates.item(i).text() for i in range(pirates.count())]
    assert names == ["Caeso", "Kandis"]
    pirates.item(0).setCheckState(UNCHECKED)
    assert store.defaults.characters == {"pirate": [153069]}
    emperors = people.lists["emperor"]
    emperors.item(1).setCheckState(CHECKED)  # one emperor reigns: Lucius goes
    assert emperors.item(0).checkState() == UNCHECKED and store.defaults.characters["emperor"] == [31637]
    pirates.item(0).setCheckState(CHECKED)
    panel.reset_names(("characters:pirate",))
    assert "pirate" not in store.defaults.characters
    platform = section(panel, previewpanel.PlatformSection)
    platform.inputs.button(2).setChecked(True)
    platform.sessions.button(1).setChecked(True)
    assert store.defaults.input == "playstation" and store.defaults.session == "multi"
    platform.dlcs.item(0).setCheckState(UNCHECKED)
    assert store.defaults.dlcs == [12]
    platform.dlcs.item(0).setCheckState(CHECKED)
    assert store.defaults.dlcs is None  # every DLC is the default
    values = section(panel, previewpanel.ValuesSection)
    values.numbers["money"].setValue(5000)
    values.season.setText("winter")
    values.season.editingFinished.emit()
    values.language.activated.emit(2)
    assert (store.defaults.money, store.defaults.season, store.defaults.language) == (5000, "winter", "german")
    setup = section(panel, previewpanel.SetupSection)
    setup.combos["setup.SetupTaxes"].picked.emit(0)
    setup.combos["setup.SetupTaxes"].picked.emit(2)  # the campaign's own value is no change
    assert "setup.SetupTaxes" not in store.defaults.options
    setup.combos["setup.SetupStartCredits"].picked.emit(1)
    setup.spins["setup.GameSeed"].setValue(7)
    setup.region.picked.emit(pd.ALBION)
    assert store.defaults.option("setup.SetupStartCredits") == 1 and store.defaults.option("setup.GameSeed") == 7
    assert store.defaults.region == pd.ALBION
    setup.use_campaign()
    assert store.defaults.region == pd.LATIUM and not store.defaults.options


def test_progression_tiers_and_the_locked_checklist(qt, tmp_path):
    panel = panel_for(tmp_path)
    store, prog = panel.store, section(panel, previewpanel.ProgressionSection)
    assert prog.tiers[1496][0].isChecked()  # everything is reached by default
    prog.preset.picked.emit("tier:1499")
    assert store.defaults.progression == "tier:1499" and not prog.tiers[1496][0].isChecked()
    prog.preset.picked.emit("start")
    assert store.defaults.progression == "start"
    prog.preset.picked.emit("all")
    assert store.defaults.progression == "all"
    prog.tiers[1500][0].setChecked(False)
    assert 1500 not in store.defaults.tiers and 1499 in store.defaults.tiers
    leaves = {leaf.data(0, Qt.ItemDataRole.UserRole): leaf for leaf in prog._leaves()}
    leaves[22].setCheckState(0, CHECKED)
    assert store.defaults.locked == [22] and store.defaults.is_locked(22)
    prog.find.setText("mine")
    prog.lock_shown.click()
    assert store.defaults.locked == [22, 23]
    prog.find.setText("")
    prog.unlock_shown.click()
    assert store.defaults.locked == []


def test_the_research_tree_and_its_presets(qt, tmp_path):
    panel = panel_for(tmp_path)
    store, research = panel.store, section(panel, previewpanel.ResearchSection)
    leaf = {n.data(0, Qt.ItemDataRole.UserRole).guid: n for n in research._leaves()}
    assert all(n.checkState(0) == CHECKED for g, n in leaf.items() if g != 33) and research.counts[33].value() == 1
    leaf[31].setCheckState(0, UNCHECKED)
    assert store.defaults.options == {"research.31": 0.0} and store.defaults.researched(31, 1) == 0.0
    research.counts[33].setValue(3)
    assert store.defaults.researched(33, 3) == 3.0
    research.preset.picked.emit("none")  # a preset drops the nodes set by hand and sets the rest
    assert store.defaults.research == "none" and not store.defaults.options
    assert all(n.checkState(0) == UNCHECKED for g, n in leaf.items() if g != 33) and research.counts[33].value() == 0
    research.preset.picked.emit("tier:1")
    assert (
        leaf[31].checkState(0) == CHECKED and leaf[32].checkState(0) == UNCHECKED and leaf[34].checkState(0) == CHECKED
    )
    leaf[32].setCheckState(0, CHECKED)
    assert store.defaults.options == {"research.32": 1.0}
    research.preset.picked.emit("all")
    assert store.defaults.research == "all" and all(n.checkState(0) == CHECKED for g, n in leaf.items() if g != 33)
    research.find.setText("legion")
    assert leaf[34].parent().childCount() == 1 and not leaf[34].isHidden() and leaf[31].isHidden()
    assert "(" in research.tree.topLevelItem(0).text(0)


def test_a_reset_of_one_value_and_of_all_and_of_the_play_modes_choices(qt, tmp_path):
    panel = panel_for(tmp_path)
    store = panel.store
    assert not panel.reset_all.isEnabled() and not panel.reset_played.isEnabled()
    store.set("money", 7)
    store.set("goods", 9, play=True)
    store.set("options.banner.BannerShape", 8)
    assert panel.reset_all.isEnabled() and panel.reset_played.isEnabled()
    money = next(b for b in panel.resets if b.names == ("money",))
    assert money.isEnabled()
    money.click()
    assert store.defaults.money == pd.CAMPAIGN_DEFAULTS.money and store.defaults.goods == 9
    assert not money.isEnabled()
    assert section(panel, previewpanel.ValuesSection).numbers["goods"].value() == 9  # the panel follows the store
    panel.reset_played.click()
    assert store.defaults.goods == pd.CAMPAIGN_DEFAULTS.goods and store.defaults.options
    panel.reset_all.click()
    assert pd.changes(store.defaults) == {} and not panel.reset_all.isEnabled()
    assert section(panel, previewpanel.PlayerSection).parts["banner.BannerShape"].current_guid() == 92736


def test_keep_in_project(qt, tmp_path):
    panel = panel_for(tmp_path, project=True)
    store = panel.store
    assert panel.keep.isEnabled() and not panel.keep.isChecked()
    store.set("money", 11)
    panel.keep.setChecked(True)
    assert store.per_project and (tmp_path / "preview.json").is_file()
    store.set("money", 12)
    assert '"money": 12' in (tmp_path / "preview.json").read_text(encoding="utf-8")
    panel.keep.setChecked(False)
    assert not store.per_project and not (tmp_path / "preview.json").exists()
    without = panel_for(tmp_path)
    assert not without.keep.isEnabled() and without.keep.toolTip()


def test_loading_the_catalogue_later_and_a_game_without_one(qt, tmp_path):
    store = pd.Store({}, "anno117")
    panel = previewpanel.PreviewPanel(store, None, None, None, loading=True)
    assert panel.state.text() and panel.loading
    assert section(panel, previewpanel.CharactersSection).lists["pirate"].count() == 3  # the campaign's three
    store.set("input", "xbox")
    panel.set_catalogue(catalogue())
    assert not panel.loading and not panel.state.text()
    assert section(panel, previewpanel.PlatformSection).inputs.button(1).isChecked()
    panel.set_catalogue(None)
    assert panel.state.text() and panel.tabs.count() == 7
    empty = previewpanel.PreviewPanel()
    assert empty.tabs.count() == 0 and empty.state.text() and not empty.reset_all.isEnabled()


def test_the_note_shows_while_live_values_win(qt, tmp_path):
    live = [False]
    panel = panel_for(tmp_path, note=lambda: live[0])
    assert panel.note.isHidden()
    live[0] = True
    panel.refresh_note()
    assert not panel.note.isHidden() and panel.note.text()


def test_pictures_are_read_off_the_windows_thread_and_fill_the_grids(qt, tmp_path):
    from annotoolbox.suite.background import Background

    asked = []

    def load(path):
        asked.append(path)
        image = QImage(8, 8, QImage.Format.Format_RGBA8888)
        image.fill(0xFF00FF00)
        return image

    background = Background()
    icons = previewwidgets.Icons(background, load)
    panel = panel_for(tmp_path, icons=icons)
    background.join()
    for _ in range(50):
        qt.processEvents()
    assert "p/38962.png" in asked and icons.get("p/38962.png") is not None
    grid = section(panel, previewpanel.PlayerSection).grids["player_portrait"][0]
    assert not grid.item(0).icon().isNull()


def test_the_panel_is_the_windows_dock_and_follows_its_store(window, qt, tmp_path):  # noqa: F811
    """In the window: the dock exists, a game's store is shown, a change lays the preview out again."""
    panel = window.preview_panel
    assert "Preview settings" in window.docks and window.docks["Preview settings"].widget() is panel
    assert panel.store is window.preview_store and panel.tabs.count() == 7
    window.show_dock("Preview settings")
    _open(window, next(iter(window.game.files.entries)))
    _settle(window)
    section(panel, previewpanel.PlatformSection).inputs.button(1).setChecked(True)
    assert window.ctx.defaults.input == "xbox"
    _settle(window)
    assert window.preview_store.defaults.input == "xbox"


def test_a_dlcs_research_tree_follows_the_owned_dlcs_and_cut_research_has_its_own_group(qt, tmp_path):
    panel = panel_for(tmp_path)
    research = section(panel, previewpanel.ResearchSection)
    names = [research.tree.topLevelItem(i).text(0) for i in range(research.tree.topLevelItemCount())]
    assert names[-1].startswith("Research cut") and names.index("Nile (1/1)") > names.index("Economy (3/3)")
    nile = research.tree.topLevelItem(names.index("Nile (1/1)"))
    assert not nile.isDisabled()
    platform = section(panel, previewpanel.PlatformSection)
    platform.dlcs.item(0).setCheckState(UNCHECKED)  # the Nile DLC (11) is not owned
    assert nile.isDisabled() and nile.child(0).isDisabled() and "not owned" in nile.text(0)
    platform.dlcs.item(0).setCheckState(CHECKED)
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
