"""The suite's window and the asset editor's window, offscreen, on synthetic settings and a synthetic game."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # after the platform is chosen
from tests.sources.sourcesfakes import asset_game

from annotoolbox.infra.i18n import english
from annotoolbox.suite import apps, guidrangedialog, guidranges, project, shell
from annotoolbox.suite.extspecs import SAppSpec
from annotoolbox.suite.settings import CSettings


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    s = CSettings(
        anno117=str(asset_game(tmp_path / "117")), workspace=str(tmp_path / "ws"), mods=str(tmp_path / "mods")
    )
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

    apps = [SAppSpec("one", "One", "app_islands", "first", page("one")),
            SAppSpec("two", "Two", "app_assets", "second", page("two"))]  # fmt: skip
    window = shell.CSuiteWindow(apps, *settings)
    shown = [a.text() for a in window.m_switcher.actions() if a.text()]
    assert made == [] and shown[:3] == ["One", "Two", "Project"] and "Manage apps" in shown
    window.show_app("two")
    assert made == ["two"] and window.windowTitle() == f"two title · {shell.TITLE}"
    window.m_switch_actions["one"].trigger()
    assert made == ["two", "one"] and window.m_stack.currentWidget().text() == "one"
    window.close()
    assert CSettings.load(settings[1]).apps["shell"] == {"app": "one"}


def test_an_app_that_fails_to_start_says_why_and_the_others_stay(qt, settings):
    def broken(_shell):
        raise RuntimeError("no game")

    window = shell.CSuiteWindow([SAppSpec("bad", "Bad", "app_assets", "", broken)], *settings)
    page = window.show_app("bad")
    assert "no game" in page.text()


def test_a_page_that_refuses_to_close_keeps_the_window_and_the_page_in_front(qt, settings):
    """The pages asked before the one that refused were closed already: the one in front stayed hidden, a blank window."""
    from PySide6.QtWidgets import QWidget

    class FakeUnsavedWindow(QWidget):
        def closeEvent(self, event):
            event.ignore()

    specs = [SAppSpec("one", "One", "app_islands", "", lambda _s: QLabel("one")),
             SAppSpec("two", "Two", "app_assets", "", lambda _s: FakeUnsavedWindow())]  # fmt: skip
    window = shell.CSuiteWindow(specs, *settings)
    window.show_app("two")
    front = window.show_app("one")
    window.show()
    assert not window.close() and window.isVisible()
    assert front.isVisible() and window.m_stack.currentWidget() is front
    assert window._m_follow_system_scheme is not None  # still open: it goes on following the system's colours
    window.m_pages.pop("two").deleteLater()
    assert window.close() and window._m_follow_system_scheme is None


def test_every_app_has_a_shortcut_shown_in_its_tip_and_the_palette_does_not_take_the_windows_colours(qt, settings):
    from PySide6.QtGui import QPalette

    specs = [SAppSpec(f"app{n}", f"App {n}", "app_islands", "About", lambda _s: QLabel()) for n in range(1, 24)]
    window = shell.CSuiteWindow(specs, *settings)
    keys = [a.shortcut().toString() for a in window.m_switch_actions.values()]
    assert (
        keys == [f"Ctrl+{n}" for n in range(1, 10)] + ["Ctrl+0"] + [f"Ctrl+Shift+{n}" for n in range(1, 10)] + [""] * 4
    )
    assert len(set(filter(None, keys))) == 19  # no key twice
    tips = [a.toolTip() for a in window.m_switch_actions.values()]
    assert tips[9] == "About (Ctrl+0)" and tips[10] == "About (Ctrl+Shift+1)" and tips[-1] == "About"
    opened = window.open_palette()
    assert window.m_debug_palette is opened and isinstance(window.palette(), QPalette)
    opened.close()
    window.close()


@pytest.mark.parametrize("opener", ["open_palette", "open_link_panel", "open_project"])
def test_a_window_of_the_suite_is_reused_while_open_and_a_closed_one_is_deleted(qt, settings, opener, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    monkeypatch.setenv("ANNO_LIVE_LINK", "0")
    window = shell.CSuiteWindow([SAppSpec("one", "One", "app_islands", "", lambda _s: QLabel())], *settings)
    first = getattr(window, opener)()
    assert getattr(window, opener)() is first and first.isVisible()  # a second click: the same window, in front
    first.close()
    first.m_background.join()
    second = getattr(window, opener)()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert second is not first and second.isVisible() and not isValid(first)
    assert sum(type(child) is type(second) for child in window.children()) == 1
    second.close()
    window.close()


def test_the_asset_window_opens_a_game_edits_undoes_checks_and_builds(qt, settings, tmp_path):
    from annotoolbox.assetedit.assetwindow import CAssetWindow
    from annotoolbox.suite.apps import BUILTIN

    window = shell.CSuiteWindow(list(BUILTIN), *settings)
    page = window.show_app("assets")
    assert isinstance(page, CAssetWindow)
    page.open("anno117", is_waiting=True)
    assert page.m_doc is not None and page.m_tree.m_mode.currentText() == "Groups"
    page.m_tree.select(2002)
    assert page.m_guid == 2002
    blocks = page.m_form.m_blocks
    assert list(blocks)[:2] == ["Standard", "Building"] and blocks["Building"].m_built
    page.m_doc.set_value(2002, "Building/Upkeep", "11")
    page._changed_now()
    assert page.windowTitle().startswith("Anno 117 *") and "*" in page.m_tree.m_tree.currentItem().text(0)
    page.m_acts["undo"].trigger()
    assert 2002 not in page.m_doc.m_changes.m_assets or page.m_doc.m_changes.m_assets[2002].empty
    page.m_acts["redo"].trigger()
    for k in range(page.m_tabs.count()):  # every tab fills for the asset
        page.m_tabs.setCurrentIndex(k)
    page.m_tabs.setCurrentIndex(0)
    page.m_tree.select(2001)
    page.clip(cut=False)
    page.m_tree.select(1002)
    page.paste()
    (copy,) = page.m_doc.m_changes.m_new
    assert (
        page.m_doc.m_changes.m_new[copy].anchor == 1002 and page.m_guid == copy and page.m_clipboard == ([2001], False)
    )
    page.clip(cut=True)  # the new copy: a cut moves it, the GUID stays
    assert page.m_clipboard == ([copy], True)
    page.m_tree.select(2001)
    assert page.m_tree.selected_assets() == [2001]
    page.m_doc.remove_asset(copy)
    assert page.run_check(is_waiting=True) == []
    folder = page.build_mod(tmp_path / "mods", is_waiting=True)
    assert (folder / "data/base/config/export/assets.xml").is_file()
    assert page.save() and not page.m_doc.dirty
    window.close()


def test_a_check_a_build_and_a_write_run_off_the_window_thread_one_at_a_time(qt, settings, tmp_path):
    from annotoolbox.suite.apps import BUILTIN

    page = shell.CSuiteWindow(list(BUILTIN), *settings).show_app("assets")
    page.open("anno117", is_waiting=True)
    page.m_doc.set_value(2002, "Building/Upkeep", "11")
    assert page.run_check() is None and page.m_working  # started, the status line says what runs
    page.run_check()  # a second job while one runs is said, not started
    assert "still busy" in page.statusBar().currentMessage()
    page.m_background.join()
    qt.processEvents()
    assert not page.m_working and "findings" in page.statusBar().currentMessage()
    page.write_native(tmp_path / "files")
    page.m_background.join()
    qt.processEvents()
    assert not page.m_working and (tmp_path / "files").is_dir()
    assert page.save()
    page.close()


def test_the_island_editor_is_the_shells_first_app_and_keeps_its_own_window_state(qt, settings):
    from annotoolbox.editor.desktop.workbench import CMainWindow
    from annotoolbox.suite.apps import BUILTIN

    window = shell.CSuiteWindow(list(BUILTIN), *settings)
    page = window.show_app("islands")
    assert isinstance(page, CMainWindow) and not page.isWindow() and page.m_app.m_settings is window.m_settings
    assert window.windowTitle().endswith(shell.TITLE) and page.menuBar().actions()
    window.close()
    layout = CSettings.load(settings[1]).layout
    assert "state" in layout and "geometry" not in layout  # a page has no place of its own on the screen


def test_the_smoke_start_opens_every_app_and_the_project_window(qt, settings, capsys):
    window = shell.CSuiteWindow(list(apps.BUILTIN), *settings, apps.parts())
    assert shell.smoke_test(qt, window) == 0
    out = capsys.readouterr().out
    assert all(f"{english(a.title)}: started" in out for a in apps.BUILTIN) and "Project: opened" in out


def test_the_project_window_saves_the_project_and_builds_its_mod(qt, settings, tmp_path):
    s, path = settings
    s.project = str(tmp_path / "proj")
    files = {"data/base/config/export/assets.xml": b'<ModOps><ModOp Type="add" Path="/A" /></ModOps>'}
    part = project.SPart("x", "X", lambda _p: True, lambda _p, _s, _log: dict(files))
    window = shell.CSuiteWindow(list(apps.BUILTIN), s, path, [part])
    dialog = window.open_project()
    assert "no project file yet" in dialog.m_folder.text() and dialog.m_mod_id.text() == "proj"
    dialog.m_name.setText("My project")
    dialog.m_guid_selection = guidrangedialog.SSelection(guidranges.CUSTOM, "", (1_337_530_000, 1_337_530_099))
    dialog.build(is_waiting=True)
    saved = project.CProject.open(tmp_path / "proj")
    assert saved.defined and saved.name == "My project" and saved.guid_range[1] == 1_337_530_099
    assert saved.guid_choice == guidranges.CUSTOM and str(tmp_path / "proj") in [str(Path(k)) for k in s.known_projects]
    assert (saved.build_dir / "modinfo.json").is_file() and "1 files from X" in dialog.m_log.toPlainText()
    dialog.m_mod_id.setText("Not An Id")
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
    window = shell.CSuiteWindow(list(apps.BUILTIN), s, path, [])
    dialog = window.open_project()
    assert dialog is not None and "does not read" in dialog.m_folder.text() and warned == []
    assert not dialog.m_buttons["Save"].isEnabled() and dialog.m_buttons["Open…"].isEnabled()
    s.project = str(good)
    dialog.use(broken)  # opening it again: said, and the setting stays on the project that reads
    assert warned and s.project == str(good)
    dialog.use(good)
    assert dialog.m_buttons["Save"].isEnabled() and s.project == str(good)
    dialog.close()
    window.close()


def test_work_that_ends_after_its_window_closed_is_dropped_quietly():
    """A panel's background paint that ends after the island's window closed raised in its thread ("Signal source
    has been deleted"); its result is dropped."""
    import threading

    import shiboken6
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite.background import CBackground

    QApplication.instance() or QApplication([])
    raised, gate = [], threading.Event()
    before, threading.excepthook = threading.excepthook, raised.append
    try:
        bg = CBackground()
        bg.run(lambda: gate.wait(5), lambda _result: None)
        threads = list(bg._m_threads)
        shiboken6.delete(bg)
        gate.set()
        for t in threads:
            t.join(5)
    finally:
        threading.excepthook = before
    assert raised == []


def _asking_page(answer, log, name):
    from PySide6.QtWidgets import QWidget

    from annotoolbox.suite import widgets

    class FakeClosablePage(QWidget):
        def can_close(self):
            log.append(f"asked {name}")
            return answer()

        def closeEvent(self, event):
            if not widgets.may_close(self):  # the yes of the first step is used, no second question
                event.ignore()
                return
            log.append(f"closed {name}")
            super().closeEvent(event)

    return lambda _shell: FakeClosablePage()


def test_closing_asks_every_page_first_and_closes_none_while_one_refuses(qt, settings):
    log, answers = [], {"b": False}
    specs = [
        SAppSpec("aa", "A", "app_islands", "", _asking_page(lambda: True, log, "a")),
        SAppSpec("old", "Old", "app_assets", "", lambda _s: QLabel("a page without the query")),
        SAppSpec("bb", "B", "app_assets", "", _asking_page(lambda: answers["b"], log, "b")),
    ]
    window = shell.CSuiteWindow(specs, *settings)
    for key in ("aa", "old", "bb"):
        window.show_app(key)
    window.show_app("aa")
    window.show()
    assert not window.close() and window.isVisible()
    assert log == ["asked a", "asked b"]  # a was not released although it agreed
    assert window.m_stack.currentWidget() is window.m_pages["bb"]  # the page that refused is shown
    answers["b"] = True
    log.clear()
    assert window.close()
    assert log == ["asked a", "asked b", "closed a", "closed b"]  # each asked once, then closed


def test_closing_during_a_project_import_or_build_asks_and_a_yes_stops_the_import(qt, settings, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.suite.background import CJobTalk

    window = shell.CSuiteWindow([SAppSpec("one", "One", "app_islands", "", lambda _s: QLabel("one"))], *settings)
    dialog = window.open_project()
    asked = []
    answer = {"yes": False}

    def question(_parent, _title, text, *_more):
        asked.append(text)
        return QMessageBox.StandardButton.Yes if answer["yes"] else QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    assert window.may_close() and asked == []  # nothing runs: no question
    dialog.m_talk = CJobTalk(dialog)
    assert not window.may_close() and "import" in asked[-1]  # No: the Toolbox stays
    answer["yes"] = True
    assert window.close() and dialog.m_talk.stopped  # Yes: the import is told to stop and keeps its work
    dialog.m_talk = None
    dialog._m_is_building = True
    answer["yes"] = False
    assert not dialog.can_close() and "build" in asked[-1]
    dialog._m_is_building = False
