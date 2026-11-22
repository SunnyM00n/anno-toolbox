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
from tests.modmgr.modmgrfakes import make_mod, make_mods_folder

from annotoolbox.modmgr import window as mw
from annotoolbox.suite import livelink
from annotoolbox.suite.settings import CSettings


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
    s = CSettings(anno117="", mods=str(mods), workspace=str(tmp_path / "ws"), mods1800=str(tmp_path / "m1800"))
    box = {"running": False}
    w = mw.CModManagerWindow(
        s, None, is_synchronous=True, lister=lambda: {"anno117.exe"} if box["running"] else set(), trash=bin_
    )
    w.box, w.mods = box, mods
    yield w
    w.close()


def settle(qt):
    """Let the queued signals run: a check box's change is handled after its own signal has finished."""
    for _ in range(3):
        qt.processEvents()


def rows(w):
    return {w.m_tree.topLevelItem(n).text(1): w.m_tree.topLevelItem(n) for n in range(w.m_tree.topLevelItemCount())}


def test_the_tree_lists_the_mods_with_their_state_and_nested_mods(win):
    items = rows(win)
    assert list(items) == ["alpha", "beta", "gamma"] and win.m_profile == "default"
    assert (
        items["alpha"].checkState(0) == Qt.CheckState.Checked
        and items["gamma"].checkState(0) == Qt.CheckState.Unchecked
    )
    assert items["beta"].childCount() == 1 and items["beta"].child(0).text(1) == "beta-inner"
    assert items["beta"].child(0).text(5) == "with beta" and not (
        items["beta"].child(0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    )
    assert [win.m_order.topLevelItem(n).text(1) for n in range(win.m_order.topLevelItemCount())] == [
        "alpha",
        "beta",
        "beta-inner",
    ]
    assert "(active)" in win.m_profile_box.currentText() and win.m_activate_action.isEnabled() is False


def test_a_check_box_writes_the_profile_and_the_problems_follow(win, qt):
    rows(win)["gamma"].setCheckState(0, Qt.CheckState.Checked)
    settle(qt)
    text = (win.mods / "profiles" / "default.txt").read_text()
    assert text.splitlines()[-1] == "gamma"
    assert "M03" in [
        win.m_issues.topLevelItem(n).text(2) for n in range(win.m_issues.topLevelItemCount())
    ]  # alpha and gamma
    assert win.m_tabs.tabText(1) == "Problems (1)"
    rows(win)["alpha"].setCheckState(0, Qt.CheckState.Unchecked)
    settle(qt)
    assert "# alpha" in (win.mods / "profiles" / "default.txt").read_text().splitlines()


def test_the_enable_new_mods_box_and_the_profile_buttons(win, monkeypatch):
    win.m_new_mods.setChecked(False)
    assert (win.mods / "profiles" / "default.txt").read_text().startswith("# EnableNewMods")
    win.m_new_mods.setChecked(True)
    assert (win.mods / "profiles" / "default.txt").read_text().startswith("EnableNewMods")
    names = iter(["second", "second copy", "third"])
    monkeypatch.setattr(mw.QInputDialog, "getText", lambda *a, **k: (next(names), True))
    win.new_profile()
    assert win.m_profile == "second" and (win.mods / "profiles" / "second.txt").is_file()
    win.copy_profile()
    assert win.m_profile == "second copy"
    win.rename_profile()
    assert win.m_profile == "third" and not (win.mods / "profiles" / "second copy.txt").exists()
    win.make_active()
    assert win.m_manager.active() == "third" and "(active)" in win.m_profile_box.currentText()
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.deactivate()
    assert win.m_manager.active() == ""
    win.m_profile_box.setCurrentIndex(win.m_profile_box.findData("second"))
    win.delete_profile()
    assert not (win.mods / "profiles" / "second.txt").exists()


def test_a_profile_error_is_said_not_raised(win, monkeypatch):
    said = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda _w, _t, text: said.append(text))
    monkeypatch.setattr(mw.QInputDialog, "getText", lambda *a, **k: ("default", True))
    win.new_profile()
    assert said and "exists already" in said[0]


def test_while_the_game_runs_the_banner_shows_and_a_change_asks_first(win, monkeypatch, qt):
    assert win.m_guard.isHidden()
    win.box["running"] = True
    win.update_guard()
    assert not win.m_guard.isHidden() and "next start" in win.m_guard.text()
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
    win.m_tree.setCurrentItem(rows(win)["alpha"])
    html = win.m_detail.toHtml()
    assert (
        "the alpha mod" in html
        and "No matching node" in html
        and "unchanged" in html
        and "listed in the profile" in html
    )
    assert "1 in the log" in rows(win)["alpha"].text(6) and win.m_logview.topLevelItem(0).childCount() == 1


def test_install_and_remove_go_through_the_manager(win, tmp_path, monkeypatch, qt):
    src = make_mod(tmp_path / "src", "fresh")
    win._install(src)
    deadline = time.time() + 10
    while win.checking and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    assert "fresh" in rows(win) and (win.mods / "fresh").is_dir()
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.m_tree.setCurrentItem(rows(win)["fresh"])
    win.remove_selected()
    assert "fresh" not in rows(win) and not (win.mods / "fresh").exists()
    win.m_tree.setCurrentItem(rows(win)["beta"].child(0))
    win.remove_selected()  # a nested mod: the whole mod goes
    assert "beta" not in rows(win)


def test_the_deep_check_runs_in_the_background_and_fills_the_problems(win, qt):
    win.deep_check()
    deadline = time.time() + 20
    while win.checking and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    assert not win.checking and win.m_check_action.isEnabled() and win.m_stop_button.isHidden()


def test_the_deep_check_says_where_it_is_and_stop_ends_it_before_the_next_mod(win, qt, monkeypatch):
    import threading

    from annotoolbox.infra.i18n import tr

    release, reached = threading.Event(), []

    def check(_profile, say):
        say("at alpha")
        assert release.wait(30)
        say("at beta")  # the stop ends it here
        reached.append("beta")
        return [mw.SIssue(mw.PROBLEM, "beta", "op:x", "never reported")]

    monkeypatch.setattr(win.m_manager, "deep_check", check)
    win.m_is_synchronous = False
    win.deep_check()
    deadline = time.time() + 10
    while win.statusBar().currentMessage() != "at alpha" and time.time() < deadline:
        qt.processEvents()
    assert win.statusBar().currentMessage() == "at alpha" and not win.m_stop_button.isHidden()
    win.m_stop_button.click()
    assert win.statusBar().currentMessage() == tr("mods.stopping") and not win.m_stop_button.isEnabled()
    release.set()
    wait_for(win, qt, "checking")
    assert not reached and win.m_deep == [] and win.m_stop_button.isHidden() and win.m_check_action.isEnabled()
    assert win.statusBar().currentMessage() == tr("mods.check_stopped")


def test_the_deep_checks_findings_go_when_the_profile_changes(win, qt, monkeypatch):
    found = [mw.SIssue(mw.PROBLEM, "alpha", "op:x", "matches nothing")]
    monkeypatch.setattr(win.m_manager, "deep_check", lambda _profile, _say: list(found))

    def checked() -> bool:
        win.deep_check()
        return win.m_deep == found and found[0] in win.m_plan.issues

    assert checked()
    win._mod_toggled("gamma", True)  # the profile's text changed: the findings are of the one before
    assert win.m_deep == [] and found[0] not in win.m_plan.issues
    assert checked()
    win._profile_chosen()  # another profile picked
    assert win.m_deep == [] and found[0] not in win.m_plan.issues
    assert checked()
    win.rescan()  # the mods folder read again
    assert win.m_deep == []
    stamp = win.m_deep_stamp
    win._mod_toggled("gamma", False)
    win._checked(stamp, list(found))  # a check that ends after the profile changed under it
    assert win.m_deep == [] and "again" in win.statusBar().currentMessage()


def test_an_install_says_its_steps_and_stop_keeps_what_is_in(win, qt, tmp_path, monkeypatch):
    import threading

    from annotoolbox.infra.i18n import tr

    pack = tmp_path / "pack"
    make_mod(pack, "one")
    make_mod(pack, "two")
    release, copied, real = threading.Event(), threading.Event(), mw.install.place_folder

    def slow(*args, **kwargs):
        notes = real(*args, **kwargs)
        copied.set()
        assert release.wait(30)  # the first mod is in; the stop comes before the second
        return notes

    monkeypatch.setattr(mw.install, "place_folder", slow)
    win.m_is_synchronous = False
    win._install(pack)
    deadline = time.time() + 10
    while "one" not in win.statusBar().currentMessage() and time.time() < deadline:
        qt.processEvents()
    assert win.statusBar().currentMessage() == tr("mods.installing_name", name="one") and copied.wait(30)
    win.stop_job()
    release.set()
    wait_for(win, qt, "checking")
    assert "one" in rows(win) and "two" not in rows(win) and win.m_stop_button.isHidden()
    assert win.statusBar().currentMessage() == tr("mods.install_stopped_installed", names="one")
    assert sorted(p.name for p in win.mods.iterdir() if p.name.startswith(".")) == []  # no staging folder left


def test_a_reload_without_the_link_says_how_to_do_it_by_hand(win, monkeypatch):
    shown = []
    monkeypatch.setattr(mw.QMessageBox, "information", lambda _w, _t, text: shown.append(text))
    win.reload_game()
    assert shown and "Shift+F3" in shown[0] and "active profile (default)" in shown[0]


def test_switching_the_game_and_remembering_it(win):
    make_mod(Path(win.m_settings.mods1800), "old")
    win.m_game_box.setCurrentIndex(win.m_game_box.findData("anno1800"))
    assert win.m_manager.m_game == "anno1800" and win.m_tree.topLevelItemCount() == 1
    win.close()
    assert win.m_settings.apps["mods"]["game"] == "anno1800"


def test_the_guard_and_the_reload_never_wait_on_the_window_thread(win, monkeypatch, qt):
    import threading

    seen = []
    win.m_is_synchronous = False
    win.m_manager.m_lister = lambda: seen.append(threading.get_ident()) or {"anno117.exe"}
    win.update_guard()
    deadline = time.time() + 10
    while win._m_guarding and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)
    assert seen and threading.get_ident() not in seen and not win.m_guard.isHidden()
    win.update_guard()
    win.update_guard()  # a look already on its way is not doubled
    assert win._m_guarding
    win.m_background.join()
    qt.processEvents()
    ran, shown = [], []
    monkeypatch.setattr(
        mw.gameguard,
        "reload_in_game",
        lambda: ran.append(threading.get_ident()) or livelink.SReply(livelink.NOT_CONNECTED, "no link"),
    )
    monkeypatch.setattr(mw.QMessageBox, "information", lambda _w, _t, text: shown.append(text))
    win.reload_game()
    win.m_background.join()
    qt.processEvents()
    assert ran and threading.get_ident() not in ran and shown and "active profile" in shown[0]


def wait_for(win, qt, flag: str, seconds: float = 10) -> None:
    deadline = time.time() + seconds
    while getattr(win, flag) and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.01)


def built_project(tmp_path: Path, win):
    from annotoolbox.suite import project

    proj = project.CProject.open(tmp_path / "built")  # no project.json: the ModID is the folder's name
    make_mod(proj.build_dir.parent, "built", "built")
    win.m_settings.project = str(proj.folder)
    return proj


def test_the_projects_build_is_installed_off_the_window_thread(win, tmp_path, qt, monkeypatch):
    import threading

    built_project(tmp_path, win)
    seen, said = [], []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda _w, _t, text: said.append(text))  # never a modal box
    real = mw.project.install
    monkeypatch.setattr(mw.project, "install", lambda *a, **k: seen.append(threading.get_ident()) or real(*a, **k))
    win.m_is_synchronous = False
    win.install_project()
    assert win.checking  # the window is back at once, the job is on its way
    wait_for(win, qt, "checking")
    win.m_background.join()
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
    win.m_manager.m_trash = lambda path: seen.append(threading.get_ident()) or bin_(path)
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.m_is_synchronous = False
    win.m_tree.setCurrentItem(rows(win)["gamma"])
    win.remove_selected()
    assert win.checking
    wait_for(win, qt, "checking")
    win.m_background.join()
    qt.processEvents()
    assert seen and threading.get_ident() not in seen and "gamma" not in rows(win) and not win.checking


def test_a_profile_that_cannot_be_made_active_is_not_reported_as_active(win, monkeypatch):
    from annotoolbox.modmgr.profiles import CProfileError

    def refuse(_name):
        raise CProfileError("the file is locked")

    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(win.m_manager.m_store, "set_active", refuse)
    win.make_active()
    assert win.statusBar().currentMessage() == "the file is locked"


def test_the_active_profile_opens_whatever_case_profile_txt_spells_it_in(win):
    (win.mods / "profiles" / "aaa.txt").write_text("DisplayName aaa", encoding="utf-8")
    (win.mods / "profile.txt").write_text("ActiveProfile Default", encoding="utf-8")
    win.open_game("anno117")
    assert win.m_profile == "default" and "(active)" in win.m_profile_box.currentText()


def test_selecting_a_mod_reads_no_file_again(win, monkeypatch):
    read = []
    monkeypatch.setattr(win.m_manager, "log", lambda: read.append("log"))
    monkeypatch.setattr(win.m_manager, "read", lambda name: read.append("profile"))
    for mod_id in ("alpha", "beta", "gamma"):
        win.m_tree.setCurrentItem(rows(win)[mod_id])
        assert mod_id in win.m_detail.toPlainText()
    assert read == []  # a refresh read them; a click only shows


def test_the_selected_mod_is_imported_as_a_project_and_the_mod_stays(win, tmp_path, monkeypatch, qt):
    from annotoolbox.suite import project

    shown = []
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    mod = win.mods / "alpha"
    before = sorted((p.relative_to(mod).as_posix(), p.read_bytes()) for p in mod.rglob("*") if p.is_file())
    win.m_tree.setCurrentItem(rows(win)["alpha"])
    win.import_selected(tmp_path / "alpha-project")
    settle(qt)
    assert project.CProject.open(tmp_path / "alpha-project").mod_id == "alpha" and "alpha" in shown[0]
    assert win.m_settings.project == str(tmp_path / "alpha-project") and not win.checking
    assert before == sorted((p.relative_to(mod).as_posix(), p.read_bytes()) for p in mod.rglob("*") if p.is_file())


def test_a_profile_file_that_cannot_be_written_is_said_not_raised(win, qt, monkeypatch):
    said = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda _w, _t, text: said.append(text))

    def locked(profile, mod_id, on):
        raise PermissionError("default.txt is held open")

    monkeypatch.setattr(win.m_manager, "set_mod", locked)
    rows(win)["gamma"].setCheckState(0, Qt.CheckState.Checked)
    settle(qt)
    assert said == ["default.txt is held open"]
    assert rows(win)["gamma"].checkState(0) == Qt.CheckState.Unchecked  # the tree shows the file as it is
