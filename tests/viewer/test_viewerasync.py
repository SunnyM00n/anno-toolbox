"""The viewer builds a model's scene, a bank's tables and a game's folder index off the window's thread: a newer pick
supersedes an older one, so a stale scene or table never lands, and a big table fills in chunks."""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from tests.viewer.viewerfakes import VIEWER_HOUSE_CFG as HOUSE
from tests.viewer.viewerfakes import viewer_game as game  # noqa: F401

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from annotoolbox.exporting import banks
from annotoolbox.suite.background import CBackground, CLatest
from annotoolbox.suite.settings import CSettings
from annotoolbox.viewer import browser, modelscene, soundview
from annotoolbox.viewer.viewerwindow import CViewerWindow, with_index

WALL = "data/base/graphics/walls/wall_diff_0.dds"


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def pump(until, seconds: float = 10.0) -> bool:
    """Run the event loop until ``until()`` holds (the callbacks of the workers arrive on it)."""
    end = time.monotonic() + seconds
    while not until() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.002)
    QApplication.processEvents()
    return until()


def test_only_the_newest_job_calls_back(qt):
    background, gate, got = CBackground(), threading.Event(), []
    latest = CLatest(background)
    latest.run(lambda: gate.wait(5) and "old", got.append)
    latest.run(lambda: "new", got.append)
    assert pump(lambda: got == ["new"])
    gate.set()
    background.join()
    pump(lambda: False, 0.2)
    assert got == ["new"]  # the older job ended, its result was dropped


def test_a_cancelled_job_never_calls_back(qt):
    background, got = CBackground(), []
    latest = CLatest(background)
    latest.run(lambda: "x", got.append)
    latest.cancel()
    background.join()
    pump(lambda: False, 0.2)
    assert got == []


def viewer_window(tmp_path, game):  # noqa: F811
    window = CViewerWindow(CSettings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(game)
    return window


def slow_first_build(monkeypatch):
    """``modelscene.build``: the first call waits for the gate, later calls return at once."""
    real, gate, calls = modelscene.build, threading.Event(), []

    def build(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            gate.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(modelscene, "build", build)
    return gate, calls


def test_a_model_picked_twice_shows_the_scene_of_the_second_pick_only(qt, game, tmp_path, monkeypatch):  # noqa: F811
    gate, calls = slow_first_build(monkeypatch)
    window = viewer_window(tmp_path, game)
    shown = []
    real_show = window._show_model
    window._show_model = lambda scene: (shown.append(scene), real_show(scene))
    window.open_path(HOUSE)
    assert window.m_scene is None  # the call returned before the scene was built
    window.open_path(HOUSE)
    assert pump(lambda: window.m_scene is not None)
    second = window.m_scene
    gate.set()
    window.m_background.join()
    pump(lambda: False, 0.3)
    assert len(calls) == 2 and shown == [second] and window.m_scene is second
    window.close()


def test_a_texture_picked_while_a_model_builds_leaves_the_model_out(qt, game, tmp_path, monkeypatch):  # noqa: F811
    gate, _calls = slow_first_build(monkeypatch)
    window = viewer_window(tmp_path, game)
    window.open_path(HOUSE)
    window.open_path(WALL)
    gate.set()
    window.m_background.join()
    pump(lambda: False, 0.3)
    assert window.m_scene is None and window.m_pages.currentWidget() is window.m_texture
    window.close()


def test_a_game_opened_over_a_building_scene_drops_that_scene(qt, game, tmp_path, monkeypatch):  # noqa: F811
    gate, _calls = slow_first_build(monkeypatch)
    window = viewer_window(tmp_path, game)
    window.open_path(HOUSE)
    window.set_tree(game)
    gate.set()
    window.m_background.join()
    pump(lambda: False, 0.3)
    assert window.m_scene is None
    window.close()


def test_a_lod_picked_while_the_first_build_runs_builds_again_with_it(qt, game, tmp_path, monkeypatch):  # noqa: F811
    gate, _calls = slow_first_build(monkeypatch)
    lods, real = [], modelscene.build
    monkeypatch.setattr(modelscene, "build", lambda *args: lods.append(args[3].lod) or real(*args))
    window = viewer_window(tmp_path, game)
    window.open_path(HOUSE)
    window.m_lod_actions[1].trigger()  # the first scene is not in yet
    gate.set()
    assert pump(lambda: window.m_scene is not None)
    window.m_background.join()
    pump(lambda: False, 0.3)
    assert lods[-1] == 1
    window.close()


def test_a_model_that_cannot_be_built_says_why(qt, game, tmp_path, monkeypatch):  # noqa: F811
    def broken(*_args, **_kwargs):
        raise ValueError("no mesh")

    monkeypatch.setattr(modelscene, "build", broken)
    window = viewer_window(tmp_path, game)
    window.open_path(HOUSE)
    assert pump(lambda: "no mesh" in window.m_message.text())
    assert window.m_pages.currentWidget() is window.m_message
    window.close()


def test_a_scene_that_cannot_be_shown_says_why_when_it_arrives_late(qt, game, tmp_path):  # noqa: F811
    window = viewer_window(tmp_path, game)

    def refuse(_scene):
        raise ValueError("no vertex layout")

    window._show_model = refuse
    window.open_path(HOUSE)
    assert pump(lambda: "no vertex layout" in window.m_message.text())
    assert window.m_pages.currentWidget() is window.m_message
    window.close()


def test_a_folder_opened_while_another_is_read_wins_even_when_the_older_read_ends_last(qt, game, tmp_path, monkeypatch):  # noqa: F811
    from annotoolbox.viewer import viewerwindow

    gate, real = threading.Event(), viewerwindow.with_index

    def slow_first(tree, game_key=None):
        if tree.m_name == "first":
            gate.wait(10)
        return real(tree, game_key)

    monkeypatch.setattr(viewerwindow, "with_index", slow_first)
    for name in ("first", "second"):
        (tmp_path / name / "data").mkdir(parents=True)
        (tmp_path / name / "data" / "a.txt").write_text(name, encoding="utf-8")
    window = viewer_window(tmp_path, game)
    window.open_folder(tmp_path / "first")
    window.open_folder(tmp_path / "second")
    assert pump(lambda: window.m_tree is not None and window.m_tree.m_name == "second")
    gate.set()
    window.m_background.join()
    pump(lambda: False, 0.3)
    assert window.m_tree.m_name == "second"
    window.close()


def test_the_index_built_on_the_worker_is_the_one_the_browser_keeps(qt, game, tmp_path):  # noqa: F811
    tree, index = with_index(game)
    assert tree is game and index == browser.folder_index(game.m_paths)
    window = viewer_window(tmp_path, game)
    window.set_tree(tree, index)
    assert window.m_browser.m_index is index
    window.set_tree(game)  # no index given: the browser builds its own
    assert window.m_browser.m_index == index and window.m_browser.m_index is not index
    window.close()


def fake_tables(rows: int) -> soundview.SBankTables:
    listing = SimpleNamespace(objects={}, version=1, bank_id=2, sounds=[], media={}, events=[])
    found = soundview.SBankTables(listing, banks.SBankInfo())
    found.sound_rows = [[str(row), "", "", "", ""] for row in range(rows)]
    found.sound_clips = [("", b"")] * rows
    return found


def test_a_big_bank_fills_in_chunks_and_a_newer_bank_supersedes_it(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(soundview, "CHUNK_ROWS", 100)
    gate = threading.Event()

    def tables(path, _data, _files):
        if path == "old.bnk":
            gate.wait(10)
        return fake_tables(450 if path == "old.bnk" else 250)

    monkeypatch.setattr(soundview, "bank_tables", tables)
    view = soundview.CSoundView(cache=tmp_path)
    table = view.m_tables["Sounds"]
    view.show_bank("old.bnk", b"", None)
    view.show_bank("new.bnk", b"", None)
    assert table.rowCount() == 0  # nothing built yet
    assert pump(lambda: table.rowCount() == 250)
    assert 0 < len(view.m_clips) == 250 and table.item(249, 0).text() == "249"
    gate.set()
    view._m_latest._m_background.join()
    pump(lambda: False, 0.3)
    assert table.rowCount() == 250 and len(view.m_clips) == 250  # the old bank's 450 rows never came


def test_rows_come_a_chunk_at_a_time_and_a_clip_stops_the_fill(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(soundview, "CHUNK_ROWS", 100)
    monkeypatch.setattr(soundview, "bank_tables", lambda *_args: fake_tables(450))
    view = soundview.CSoundView(cache=tmp_path)
    table = view.m_tables["Sounds"]
    view.show_bank("a.bnk", b"", None)
    assert pump(lambda: table.rowCount() >= 100)
    assert table.rowCount() < 450  # a chunk each turn of the loop, not all at once
    view.show_clip("c.wav", b"")
    pump(lambda: False, 0.3)
    assert table.rowCount() == 0
