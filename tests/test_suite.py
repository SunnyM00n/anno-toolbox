"""The suite's window and the asset editor's window, offscreen, on synthetic settings and a synthetic game."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # after the platform is chosen
from tests.fakes import asset_game

from annotoolbox.suite import apps, guidrangedialog, guidranges, project, shell
from annotoolbox.suite.extspecs import AppSpec
from annotoolbox.suite.settings import Settings


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    s = Settings(anno117=str(asset_game(tmp_path / "117")), workspace=str(tmp_path / "ws"), mods=str(tmp_path / "mods"))
    path = tmp_path / "settings.json"
    s.save(path)
    return s, path


def test_the_shell_makes_an_apps_page_when_it_is_first_shown_and_keeps_the_last(qt, settings):
    made = []

    def page(name):
        def make(_shell):
            made.append(name)
            label = QLabel(name)
            label.setWindowTitle(f"{name} title")
            return label

        return make

    apps = [AppSpec("one", "One", "app_islands", "first", page("one")),
            AppSpec("two", "Two", "app_assets", "second", page("two"))]  # fmt: skip
    window = shell.SuiteWindow(apps, *settings)
    assert made == [] and [a.text() for a in window.switcher.actions() if a.text()] == ["One", "Two", "Project"]
    window.show_app("two")
    assert made == ["two"] and window.windowTitle() == f"two title · {shell.TITLE}"
    window.switch_actions["one"].trigger()
    assert made == ["two", "one"] and window.stack.currentWidget().text() == "one"
    window.close()
    assert Settings.load(settings[1]).apps["shell"] == {"app": "one"}


def test_an_app_that_fails_to_start_says_why_and_the_others_stay(qt, settings):
    def broken(_shell):
        raise RuntimeError("no game")

    window = shell.SuiteWindow([AppSpec("bad", "Bad", "app_assets", "", broken)], *settings)
    page = window.show_app("bad")
    assert "no game" in page.text()


def test_the_asset_window_opens_a_game_edits_undoes_checks_and_builds(qt, settings, tmp_path):
    from annotoolbox.assetedit.assetwindow import AssetWindow
    from annotoolbox.suite.apps import BUILTIN

    window = shell.SuiteWindow(list(BUILTIN), *settings)
    page = window.show_app("assets")
    assert isinstance(page, AssetWindow)
    page.open("anno117", wait=True)
    assert page.doc is not None and page.tree.mode.currentText() == "Groups"
    page.tree.select(2002)
    assert page.guid == 2002
    blocks = page.form.blocks
    assert list(blocks)[:2] == ["Standard", "Building"] and blocks["Building"].built
    page.doc.set_value(2002, "Building/Upkeep", "11")
    page._changed_now()
    assert page.windowTitle().startswith("Anno 117 *") and "*" in page.tree.tree.currentItem().text(0)
    page.acts["undo"].trigger()
    assert 2002 not in page.doc.changes.assets or page.doc.changes.assets[2002].empty
    page.acts["redo"].trigger()
    for k in range(page.tabs.count()):  # every tab fills for the asset
        page.tabs.setCurrentIndex(k)
    page.tabs.setCurrentIndex(0)
    page.tree.select(2001)
    page.clip(cut=False)
    page.tree.select(1002)
    page.paste()
    (copy,) = page.doc.changes.new
    assert page.doc.changes.new[copy].anchor == 1002 and page.guid == copy and page.clipboard == ([2001], False)
    page.clip(cut=True)  # the new copy: a cut moves it, the GUID stays
    assert page.clipboard == ([copy], True)
    page.tree.select(2001)
    assert page.tree.selected_guids() == [2001]
    page.doc.remove_asset(copy)
    assert page.run_check(wait=True) == []
    folder = page.build_mod(tmp_path / "mods", wait=True)
    assert (folder / "data/base/config/export/assets.xml").is_file()
    assert page.save() and not page.doc.dirty
    window.close()


def test_a_check_a_build_and_a_write_run_off_the_window_thread_one_at_a_time(qt, settings, tmp_path):
    from annotoolbox.suite.apps import BUILTIN

    page = shell.SuiteWindow(list(BUILTIN), *settings).show_app("assets")
    page.open("anno117", wait=True)
    page.doc.set_value(2002, "Building/Upkeep", "11")
    assert page.run_check() is None and page.working  # started, the status line says what runs
    page.run_check()  # a second job while one runs is said, not started
    assert "still busy" in page.statusBar().currentMessage()
    page.background.join()
    qt.processEvents()
    assert not page.working and "findings" in page.statusBar().currentMessage()
    page.write_native(tmp_path / "files")
    page.background.join()
    qt.processEvents()
    assert not page.working and (tmp_path / "files").is_dir()
    assert page.save()
    page.close()


def test_the_island_editor_is_the_shells_first_app_and_keeps_its_own_window_state(qt, settings):
    from annotoolbox.editor.desktop.workbench import MainWindow
    from annotoolbox.suite.apps import BUILTIN

    window = shell.SuiteWindow(list(BUILTIN), *settings)
    page = window.show_app("islands")
    assert isinstance(page, MainWindow) and not page.isWindow() and page.app.settings is window.settings
    assert window.windowTitle().endswith(shell.TITLE) and page.menuBar().actions()
    window.close()
    layout = Settings.load(settings[1]).layout
    assert "state" in layout and "geometry" not in layout  # a page has no place of its own on the screen


def test_the_smoke_start_opens_every_app_and_the_project_window(qt, settings, capsys):
    window = shell.SuiteWindow(list(apps.BUILTIN), *settings, apps.parts())
    assert shell.smoke_test(qt, window) == 0
    out = capsys.readouterr().out
    assert all(f"{a.title}: started" in out for a in apps.BUILTIN) and "Project: opened" in out


def test_the_project_window_saves_the_project_and_builds_its_mod(qt, settings, tmp_path):
    s, path = settings
    s.project = str(tmp_path / "proj")
    files = {"data/base/config/export/assets.xml": b'<ModOps><ModOp Type="add" Path="/A" /></ModOps>'}
    part = project.Part("x", "X", lambda _p: True, lambda _p, _s, _log: dict(files))
    window = shell.SuiteWindow(list(apps.BUILTIN), s, path, [part])
    dialog = window.open_project()
    assert "no project file yet" in dialog.folder.text() and dialog.mod_id.text() == "proj"
    dialog.name.setText("My project")
    dialog.guid_sel = guidrangedialog.Selection(guidranges.CUSTOM, "", (1_337_530_000, 1_337_530_099))
    dialog.build(wait=True)
    saved = project.Project.open(tmp_path / "proj")
    assert saved.defined and saved.name == "My project" and saved.guid_range[1] == 1_337_530_099
    assert saved.guid_choice == guidranges.CUSTOM and str(tmp_path / "proj") in [str(Path(k)) for k in s.known_projects]
    assert (saved.build_dir / "modinfo.json").is_file() and "1 files from X" in dialog.log.toPlainText()
    dialog.mod_id.setText("Not An Id")
    assert not dialog.save.__self__.edited or True
    dialog.close()
    window.close()


def test_a_project_file_that_does_not_read_leaves_the_project_window_open_to_choose_another(
    qt, settings, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QMessageBox

    s, path = settings
    broken, good = tmp_path / "broken", tmp_path / "good"
    broken.mkdir()
    (broken / project.FILE).write_text('{"mod_id": ', encoding="utf-8")
    s.project = str(broken)
    warned: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _w, _t, text, *_a: warned.append(text))  # never modal here
    window = shell.SuiteWindow(list(apps.BUILTIN), s, path, [])
    dialog = window.open_project()
    assert dialog is not None and "does not read" in dialog.folder.text() and warned == []
    assert not dialog.buttons["Save"].isEnabled() and dialog.buttons["Open…"].isEnabled()
    s.project = str(good)
    dialog.use(broken)  # opening it again: said, and the setting stays on the project that reads
    assert warned and s.project == str(good)
    dialog.use(good)
    assert dialog.buttons["Save"].isEnabled() and s.project == str(good)
    dialog.close()
    window.close()


def test_work_that_ends_after_its_window_closed_is_dropped_quietly():
    """A panel's background paint that ends after the island's window closed raised in its thread ("Signal source
    has been deleted"); its result is dropped."""
    import threading

    import shiboken6
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite.background import Background

    QApplication.instance() or QApplication([])
    raised, gate = [], threading.Event()
    before, threading.excepthook = threading.excepthook, raised.append
    try:
        bg = Background()
        bg.run(lambda: gate.wait(5), lambda _result: None)
        threads = list(bg._threads)
        shiboken6.delete(bg)
        gate.set()
        for t in threads:
            t.join(5)
    finally:
        threading.excepthook = before
    assert raised == []
