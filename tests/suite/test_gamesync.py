"""The suite's one game for every app, offscreen (docs/suite.md "One game for every app"): a pick in one app sends
the others there, unsaved work is asked about once, Cancel stops the whole switch, the old game's files are let go,
and an app that cannot show the game stays as it is and says so."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget  # after the platform is chosen
from tests.mapedit.mapeditfakes import STEM
from tests.mapedit.mapeditfakes import mapedit_game as map_game
from tests.objbuild.objbuildfakes import HOUSE_CFG as HOUSE
from tests.objbuild.objbuildfakes import objbuild_game as object_game  # noqa: F401
from tests.particleedit.particleeditfakes import RDP_PATH, tree117  # noqa: F401
from tests.particleedit.particleeditfakes import particle_game as game  # noqa: F401

from annotoolbox.sources import gametree
from annotoolbox.suite import apps, gamesync, shell
from annotoolbox.suite.extspecs import SAppSpec
from annotoolbox.suite.gamesync import SAppGame
from annotoolbox.suite.settings import CSettings

A, B, C = "anno117", "anno1800", "anno2070"


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


class FakeApp(QMainWindow):
    """An app as the selection sees it: a game, unsaved work, a leave question with a set answer."""

    def __init__(self) -> None:
        super().__init__()
        self.key, self.dirty, self.answer, self.asked, self.opened = "", False, True, 0, []

    def may_leave(self) -> bool:
        if not self.dirty:
            return True
        self.asked += 1
        return self.answer

    def open_game(self, key: str) -> None:
        if not gamesync.may_switch(self, key, self.may_leave):
            return
        self.key, self.dirty = key, False  # the file of the game before is let go
        self.opened.append(key)


def fake_spec(name: str, games=(A, B), needs_folder: bool = False) -> SAppSpec:
    def make(suite):
        page = FakeApp()
        last = suite.m_settings.apps.get(name, {}).get("game")
        if last:
            page.open_game(last)
        return page

    side = SAppGame(
        name,
        lambda: games,
        lambda page: page.key,
        lambda page, key: page.open_game(key),
        may_leave=lambda page, _key: page.may_leave(),
        needs_folder=needs_folder,
    )
    return SAppSpec(name, name, "app_assets", "", make, game=side)


def suite_of(tmp_path, *specs, **folders) -> shell.CSuiteWindow:
    settings = CSettings(workspace=str(tmp_path / "ws"), mods=str(tmp_path / "mods"), **folders)
    return shell.CSuiteWindow(list(specs), settings, tmp_path / "settings.json")


def test_a_game_picked_in_one_app_sends_the_others_there_and_a_later_app_starts_on_it(qt, tmp_path):
    window = suite_of(tmp_path, fake_spec("one"), fake_spec("two"), fake_spec("late"))
    window.m_settings.apps["late"] = {"game": A, "file": "data/a.cfg"}
    one, two = window.show_app("one"), window.show_app("two")
    heard = []
    window.game_changed.connect(heard.append)
    one.open_game(B)
    assert (one.key, two.key, window.m_settings.game, heard) == (B, B, B, [B])
    assert two.opened == [B] and one.opened == [B]  # each opened it once
    late = window.show_app("late")
    assert late.key == B and window.m_settings.apps["late"] == {"game": B}  # the file was the other game's
    assert window.select_game(A) and {one.key, two.key, late.key} == {A}
    window.save_settings()
    assert CSettings.load(tmp_path / "settings.json").game == A


def test_an_app_with_unsaved_work_asks_once_and_a_cancel_stops_the_whole_switch(qt, tmp_path):
    window = suite_of(tmp_path, fake_spec("one"), fake_spec("two"), fake_spec("three"))
    one, two, three = (window.show_app(k) for k in ("one", "two", "three"))
    one.open_game(A)
    two.dirty = three.dirty = True
    three.answer = False  # Cancel in the third app
    one.open_game(B)
    assert (two.asked, three.asked) == (1, 1)
    assert (one.key, two.key, three.key, window.m_settings.game) == (A, A, A, A)
    assert two.dirty and three.dirty  # nothing was closed anywhere, not in the app that had agreed either
    three.answer = True  # Save or Discard
    one.open_game(B)
    assert (two.asked, three.asked) == (2, 2)  # asked again for this switch, and once only
    assert (one.key, two.key, three.key, window.m_settings.game) == (B, B, B, B)
    assert not two.dirty
    one.dirty, one.answer = True, False  # the app the game is picked in cancels its own question
    two.dirty = True
    one.open_game(A)
    assert (one.key, two.key, two.asked, window.m_settings.game) == (B, B, 2, B)
    assert not window.select_game(A) and window.m_settings.game == B  # the window's own switch is cancelled alike


def test_an_app_that_cannot_show_the_game_stays_as_it_is_and_says_so(qt, tmp_path):
    (tmp_path / "117").mkdir()
    specs = (fake_spec("all", (A, B, C)), fake_spec("few", (A,)), fake_spec("installed", (A, B), needs_folder=True))
    window = suite_of(tmp_path, *specs, anno117=str(tmp_path / "117"), anno1800="")
    every, few, installed = (window.show_app(k) for k in ("all", "few", "installed"))
    every.open_game(A)
    assert (few.key, installed.key) == (A, A)
    few.dirty = True  # it is not asked: it leaves nothing
    every.open_game(B)
    assert (every.key, few.key, installed.key, window.m_settings.game) == (B, A, A, B)
    assert few.asked == 0 and few.dirty
    assert "Anno 1800" in few.statusBar().currentMessage() and "does not open" in few.statusBar().currentMessage()
    assert "No folder" in installed.statusBar().currentMessage()
    few.open_game(C)  # a game the picking app cannot show itself is no selection: the app says why it does not open
    assert window.m_settings.game == B and every.key == B
    window.m_settings.apps["few"] = {"game": A}
    window.m_pages.pop("few").deleteLater()
    assert window.show_app("few").key == A  # made again, it starts on its own last game


def test_an_app_alone_only_asks_its_own_question(qt):
    page = FakeApp()
    page.open_game(A)
    page.dirty, page.answer = True, False
    page.open_game(B)
    assert (page.key, page.asked) == (A, 1)


def settle(*pages) -> None:
    for page in pages:
        page.m_background.join()
    QApplication.processEvents()


def test_the_viewer_and_the_object_builder_let_the_old_games_files_go(qt, object_game, tmp_path, monkeypatch):  # noqa: F811
    (tmp_path / "1800" / "data").mkdir(parents=True)
    (tmp_path / "1800" / "data" / "a.txt").write_text("a", encoding="utf-8")
    other = gametree.loose_tree(tmp_path / "1800", "Anno 1800")
    other.m_game = B
    trees = {A: object_game, B: other}
    monkeypatch.setattr(gametree, "open_game", lambda key, _folder, _index: trees[key])
    specs = [app for app in apps.available() if app.key in ("viewer", "objects")]
    window = suite_of(tmp_path, *specs, anno117=str(tmp_path / "117"), anno1800=str(tmp_path / "1800"))
    viewer, objects = window.show_app("viewer"), window.show_app("objects")
    viewer.m_is_waiting = True
    viewer.open_game(A, HOUSE, is_waiting=True)
    settle(objects)
    assert objects.m_tree is object_game and window.m_settings.game == A
    assert objects.open_path(HOUSE) and viewer.m_scene is not None
    assert viewer.m_sequences.m_choice.count() > 1 and viewer.m_checks._m_scene is viewer.m_scene
    objects.confirm_discard = lambda: False  # the leave question, answered Cancel
    viewer.open_game(B, is_waiting=True)
    assert viewer.m_tree is object_game and objects.m_object_doc is not None and window.m_settings.game == A
    del objects.confirm_discard  # nothing unsaved: not asked
    viewer.open_game(B, is_waiting=True)
    settle(objects)
    assert (viewer.m_tree, objects.m_tree, window.m_settings.game) == (other, other, B)
    assert (viewer.m_scene, viewer.m_current) == (None, "") and viewer.m_parts.topLevelItemCount() == 0
    assert viewer.m_sequences.m_choice.count() == 1 and viewer.m_checks._m_scene is None  # only the rest pose
    assert viewer.m_props.rowCount() == 0
    assert objects.m_object_doc is None and objects.m_scene is None and objects.m_objects.topLevelItemCount() == 0
    assert window.m_settings.apps["objects"]["game"] == B
    window.close()


def test_the_map_editor_and_the_particle_editor_close_what_was_open(qt, tree117, tmp_path, monkeypatch):  # noqa: F811
    from annotoolbox.mapedit.mapwindow import CMapWindow
    from annotoolbox.particleedit.particlewindow import CParticleWindow

    maps_tree = map_game(tmp_path / "maps")
    settings = CSettings(workspace=str(tmp_path / "ws"), anno117=str(tmp_path))
    monkeypatch.setattr(gametree, "open_game", lambda _key, _folder, _index: maps_tree)
    maps = CMapWindow(settings)
    maps.open_game(A, STEM + ".a7tinfo", is_waiting=True)
    assert maps.m_doc is not None and maps.m_view.m_doc is maps.m_doc
    maps._may_leave = lambda: False  # unkept changes, Cancel
    maps.open_game(A, is_waiting=True)
    assert maps.m_doc is not None
    del maps._may_leave
    maps.open_game(A, is_waiting=True)
    assert maps.m_doc is None and maps.m_view.m_doc is None and maps.m_panel.m_findings.count() == 0
    maps.close()
    monkeypatch.setattr(gametree, "open_game", lambda _key, _folder, _index: tree117)
    particles = CParticleWindow(settings, None, is_synchronous=True)
    particles.open_game(A, RDP_PATH)
    assert particles.m_effect is not None and particles.m_emitters.topLevelItemCount() > 0
    particles.open_game(A)
    assert particles.m_effect is None and particles.m_emitters.topLevelItemCount() == 0 and particles.m_scene is None
    particles.close()


def test_the_islands_panel_turns_to_the_game_and_a_tab_turned_there_picks_it(qt, tmp_path):
    for name in ("117", "1800"):
        (tmp_path / name).mkdir()
    islands = next(app for app in apps.available() if app.key == "islands")
    window = suite_of(
        tmp_path, islands, fake_spec("other"), anno117=str(tmp_path / "117"), anno1800=str(tmp_path / "1800")
    )
    page, other = window.show_app("islands"), window.show_app("other")
    page.m_tabs.blockSignals(True)  # the lists of two games, as ``reload`` fills them
    for key in (A, B):
        tab = QWidget()
        tab.m_key = key
        page.m_tabs.addTab(tab, key)
    page.m_tabs.setCurrentIndex(page.m_tabs.count() - 2)
    page.m_tabs.blockSignals(False)
    assert page.shown_game() == A
    other.open_game(B)
    assert page.shown_game() == B and window.m_settings.game == B
    page.show_game(A)  # a tab turned by hand
    assert other.key == A and window.m_settings.game == A
    other.dirty, other.answer = True, False
    page.show_game(B)
    assert (page.shown_game(), other.key, window.m_settings.game) == (
        B,
        A,
        A,
    )  # the lists stay turned, nothing switched
    page.reload()  # refilling the lists picks no game
    assert (other.key, window.m_settings.game) == (A, A)
    other.dirty = False
    window.close()
