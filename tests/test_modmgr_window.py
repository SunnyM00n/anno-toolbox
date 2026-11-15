"""The mod manager's window, offscreen, on a synthetic mods folder (the real one is never touched)."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from tests.modmgrfakes import make_mod, make_mods_folder

from annotoolbox.modmgr import window as mw
from annotoolbox.suite import livelink
from annotoolbox.suite.settings import Settings


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def bin_(path):
    shutil.rmtree(path)
    return "the test bin"


@pytest.fixture
def win(qt, tmp_path):
    livelink.reset()
    mods = make_mods_folder(tmp_path)
    s = Settings(anno117="", mods=str(mods), workspace=str(tmp_path / "ws"), mods1800=str(tmp_path / "m1800"))
    box = {"running": False}
    w = mw.ModManagerWindow(s, None, sync=True, lister=lambda: {"anno117.exe"} if box["running"] else set(), trash=bin_)
    w.box, w.mods = box, mods
    yield w
    w.close()


def settle(qt):
    """Let the queued signals run: a check box's change is handled after its own signal has finished."""
    for _ in range(3):
        qt.processEvents()


def rows(w):
    return {w.tree.topLevelItem(n).text(1): w.tree.topLevelItem(n) for n in range(w.tree.topLevelItemCount())}


def test_the_tree_lists_the_mods_with_their_state_and_nested_mods(win):
    items = rows(win)
    assert list(items) == ["alpha", "beta", "gamma"] and win.profile == "default"
    assert (
        items["alpha"].checkState(0) == Qt.CheckState.Checked
        and items["gamma"].checkState(0) == Qt.CheckState.Unchecked
    )
    assert items["beta"].childCount() == 1 and items["beta"].child(0).text(1) == "beta-inner"
    assert items["beta"].child(0).text(5) == "with beta" and not (
        items["beta"].child(0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    )
    assert [win.order.topLevelItem(n).text(1) for n in range(win.order.topLevelItemCount())] == [
        "alpha",
        "beta",
        "beta-inner",
    ]
    assert "(active)" in win.profile_box.currentText() and win.activate_action.isEnabled() is False


def test_a_check_box_writes_the_profile_and_the_problems_follow(win, qt):
    rows(win)["gamma"].setCheckState(0, Qt.CheckState.Checked)
    settle(qt)
    text = (win.mods / "profiles" / "default.txt").read_text()
    assert text.splitlines()[-1] == "gamma"
    assert "M03" in [
        win.issues.topLevelItem(n).text(2) for n in range(win.issues.topLevelItemCount())
    ]  # alpha and gamma
    assert win.tabs.tabText(1) == "Problems (1)"
    rows(win)["alpha"].setCheckState(0, Qt.CheckState.Unchecked)
    settle(qt)
    assert "# alpha" in (win.mods / "profiles" / "default.txt").read_text().splitlines()


def test_the_enable_new_mods_box_and_the_profile_buttons(win, monkeypatch):
    win.new_mods.setChecked(False)
    assert (win.mods / "profiles" / "default.txt").read_text().startswith("# EnableNewMods")
    win.new_mods.setChecked(True)
    assert (win.mods / "profiles" / "default.txt").read_text().startswith("EnableNewMods")
    names = iter(["second", "second copy", "third"])
    monkeypatch.setattr(mw.QInputDialog, "getText", lambda *a, **k: (next(names), True))
    win.new_profile()
    assert win.profile == "second" and (win.mods / "profiles" / "second.txt").is_file()
    win.copy_profile()
    assert win.profile == "second copy"
    win.rename_profile()
    assert win.profile == "third" and not (win.mods / "profiles" / "second copy.txt").exists()
    win.make_active()
    assert win.manager.active() == "third" and "(active)" in win.profile_box.currentText()
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.deactivate()
    assert win.manager.active() == ""
    win.profile_box.setCurrentIndex(win.profile_box.findData("second"))
    win.delete_profile()
    assert not (win.mods / "profiles" / "second.txt").exists()


def test_a_profile_error_is_said_not_raised(win, monkeypatch):
    said = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda _w, _t, text: said.append(text))
    monkeypatch.setattr(mw.QInputDialog, "getText", lambda *a, **k: ("default", True))
    win.new_profile()
    assert said and "exists already" in said[0]


def test_while_the_game_runs_the_banner_shows_and_a_change_asks_first(win, monkeypatch, qt):
    assert win.guard.isHidden()
    win.box["running"] = True
    win.update_guard()
    assert not win.guard.isHidden() and "next start" in win.guard.text()
    asked = []
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    before = (win.mods / "profiles" / "default.txt").read_text()
    rows(win)["gamma"].setCheckState(0, Qt.CheckState.Checked)
    settle(qt)
    assert asked and (win.mods / "profiles" / "default.txt").read_text() == before  # refused: nothing written
    assert rows(win)["gamma"].checkState(0) == Qt.CheckState.Unchecked  # and the box is back
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    rows(win)["gamma"].setCheckState(0, Qt.CheckState.Checked)
    settle(qt)
    assert (win.mods / "profiles" / "default.txt").read_text().splitlines()[-1] == "gamma"


def test_the_details_show_the_modinfo_and_the_last_starts_lines(win):
    (win.mods / "mod-loader.log").write_text(
        '[2026-11-09 10:00:00.100] [info] Load "[Tools] Alpha" alpha@1.0.0 (from "x/alpha")\n'
        '[2026-11-09 10:00:01.100] [info] alpha: No matching node for Path "/X" (assets.xml:4)\n'
        "[2026-11-09 10:00:02.100] [info] Patching completed in 2s\n",
        encoding="utf-8",
    )
    win.rescan()
    win.tree.setCurrentItem(rows(win)["alpha"])
    html = win.detail.toHtml()
    assert (
        "the alpha mod" in html
        and "No matching node" in html
        and "unchanged" in html
        and "listed in the profile" in html
    )
    assert "1 in the log" in rows(win)["alpha"].text(6) and win.logview.topLevelItem(0).childCount() == 1


def test_install_and_remove_go_through_the_manager(win, tmp_path, monkeypatch, qt):
    src = make_mod(tmp_path / "src", "fresh")
    win._install(src)
    deadline = time.time() + 10
    while win.checking and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    assert "fresh" in rows(win) and (win.mods / "fresh").is_dir()
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.tree.setCurrentItem(rows(win)["fresh"])
    win.remove_selected()
    assert "fresh" not in rows(win) and not (win.mods / "fresh").exists()
    win.tree.setCurrentItem(rows(win)["beta"].child(0))
    win.remove_selected()  # a nested mod: the whole mod goes
    assert "beta" not in rows(win)


def test_the_deep_check_runs_in_the_background_and_fills_the_problems(win, qt):
    win.deep_check()
    deadline = time.time() + 20
    while win.checking and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    assert not win.checking and win.check_action.isEnabled()
    assert ("anno117", "default") in win.deep


def test_a_reload_without_the_link_says_how_to_do_it_by_hand(win, monkeypatch):
    shown = []
    monkeypatch.setattr(mw.QMessageBox, "information", lambda _w, _t, text: shown.append(text))
    win.reload_game()
    assert shown and "Shift+F3" in shown[0] and "active profile (default)" in shown[0]


def test_switching_the_game_and_remembering_it(win):
    make_mod(Path(win.settings.mods1800), "old")
    win.game_box.setCurrentIndex(win.game_box.findData("anno1800"))
    assert win.manager.game == "anno1800" and win.tree.topLevelItemCount() == 1
    win.close()
    assert win.settings.apps["mods"]["game"] == "anno1800"


def test_the_guard_and_the_reload_never_wait_on_the_window_thread(win, monkeypatch, qt):
    import threading

    seen = []
    win.sync = False
    win.manager.lister = lambda: seen.append(threading.get_ident()) or {"anno117.exe"}
    win.update_guard()
    deadline = time.time() + 10
    while win._guarding and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    assert seen and threading.get_ident() not in seen and not win.guard.isHidden()
    win.update_guard()
    win.update_guard()  # a look already on its way is not doubled
    assert win._guarding
    win.background.join()
    qt.processEvents()
    ran, shown = [], []
    monkeypatch.setattr(
        mw.gameguard,
        "reload_in_game",
        lambda: ran.append(threading.get_ident()) or livelink.Reply(livelink.NOT_CONNECTED, "no link"),
    )
    monkeypatch.setattr(mw.QMessageBox, "information", lambda _w, _t, text: shown.append(text))
    win.reload_game()
    win.background.join()
    qt.processEvents()
    assert ran and threading.get_ident() not in ran and shown and "active profile" in shown[0]


def wait_for(win, qt, flag: str, seconds: float = 10) -> None:
    deadline = time.time() + seconds
    while getattr(win, flag) and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)


def built_project(tmp_path: Path, win):
    from annotoolbox.suite import project

    proj = project.Project.open(tmp_path / "built")  # no project.json: the ModID is the folder's name
    make_mod(proj.build_dir.parent, "built", "built")
    win.settings.project = str(proj.folder)
    return proj


def test_the_projects_build_is_installed_off_the_window_thread(win, tmp_path, qt, monkeypatch):
    import threading

    built_project(tmp_path, win)
    seen, said = [], []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda _w, _t, text: said.append(text))  # never a modal box
    real = mw.project.install
    monkeypatch.setattr(mw.project, "install", lambda *a, **k: seen.append(threading.get_ident()) or real(*a, **k))
    win.sync = False
    win.install_project()
    assert win.checking  # the window is back at once, the job is on its way
    wait_for(win, qt, "checking")
    win.background.join()
    qt.processEvents()
    assert not said, said
    assert seen and threading.get_ident() not in seen and not win.checking
    assert (win.mods / "built" / "modinfo.json").is_file() and "built" in rows(win)
    assert "installed the project's build" in win.statusBar().currentMessage()


def test_a_failed_project_install_is_said_and_frees_the_window(win, tmp_path, qt, monkeypatch):
    built_project(tmp_path, win)
    (win.mods / "built").mkdir()  # a folder of that name that holds no mod of this project
    said = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda _w, _t, text: said.append(text))
    win.install_project()
    qt.processEvents()
    assert said and "another mod" in said[0] and not win.checking


def test_a_project_for_another_game_is_refused_before_any_work(win, tmp_path, monkeypatch):
    proj = built_project(tmp_path, win)
    proj.game = "anno1800"
    monkeypatch.setattr(mw.project, "current", lambda _s: proj)
    said = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda _w, _t, text: said.append(text))
    win.install_project()
    assert said and "anno1800" in said[0] and not win.checking and not (win.mods / "built").exists()


def test_removing_a_mod_moves_it_off_the_window_thread(win, qt, monkeypatch):
    import threading

    seen = []
    win.manager.trash = lambda path: seen.append(threading.get_ident()) or bin_(path)
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.sync = False
    win.tree.setCurrentItem(rows(win)["gamma"])
    win.remove_selected()
    assert win.checking
    wait_for(win, qt, "checking")
    win.background.join()
    qt.processEvents()
    assert seen and threading.get_ident() not in seen and "gamma" not in rows(win) and not win.checking
