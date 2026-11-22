"""Apps other people add to the Toolbox (docs/app-api.md): a fake entry point, an apps folder of manifests, a failing app
isolated, a too-new API refused, a program started with its placeholders (the process start is mocked)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel
from tests.sources.sourcesfakes import asset_game

from annotoolbox.suite import appapi, appplugins, appprograms, project, shell
from annotoolbox.suite.settings import CSettings


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    s = CSettings(
        anno117=str(asset_game(tmp_path / "117")),
        anno2070=str(tmp_path / "2070"),
        workspace=str(tmp_path / "ws"),
        mods=str(tmp_path / "mods"),
        game="anno117",
    )
    path = tmp_path / "settings.json"
    s.save(path)
    return s, path


def label_page(context):
    return QLabel(f"hello {context.key}")


def declared(key="demo", **more):
    return appapi.SExternalApp(key=key, title=key.title(), make=label_page, api_version=appapi.API_VERSION, **more)


class FakePoint:
    """What ``importlib.metadata`` hands over: a name and a ``load``."""

    def __init__(self, name, value):
        self.name, self.value = name, value

    def load(self):
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


def write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8", newline="\n")
    return path


def test_an_entry_point_becomes_an_app_and_a_broken_one_is_only_itself_failed(tmp_path):
    points = [
        FakePoint("good", lambda: declared("good", games=("anno117",))),
        FakePoint("broken-import", ImportError("no module named thing")),
        FakePoint("broken-create", lambda: 1 / 0),
        FakePoint("exits", SystemExit(3)),
        FakePoint("wrong", lambda: "not an app"),
        FakePoint("taken", lambda: declared("islands")),
    ]
    found = appplugins.discover(taken={"islands"}, folder=tmp_path / "none", entry_points=lambda: points)
    by = {f.id: f for f in found}
    assert by["entry:good"].status == appplugins.OK
    assert "ImportError" in by["entry:broken-import"].error
    assert "ZeroDivisionError" in by["entry:broken-create"].error
    assert by["entry:exits"].status == appplugins.FAILED and by["entry:wrong"].status == appplugins.FAILED
    assert by["entry:taken"].status == appplugins.FAILED and "islands" in by["entry:taken"].error
    specs = {s.key: s for s in appplugins.specs(found)}
    assert specs["good"].game is not None and specs["good"].game.games() == ("anno117",)
    assert len(specs) == 6  # the failed ones are listed too, with their reason as the summary
    with pytest.raises(RuntimeError, match="ImportError"):
        specs["broken-import"].make(None)


def test_the_window_lists_added_apps_and_a_failed_page_shows_its_error(qt, settings, tmp_path):
    points = [FakePoint("good", lambda: declared("good")), FakePoint("bad", ValueError("boom"))]
    found = appplugins.discover(folder=tmp_path / "none", entry_points=lambda: points)
    window = shell.CSuiteWindow(appplugins.specs(found), *settings)
    assert [a.text() for a in window.m_switcher.actions() if a.text()][:2] == ["Good", "bad"]
    assert window.show_app("good").text() == "hello good"
    failed = window.show_app("bad")
    assert failed.objectName() == shell.FAILED and "boom" in failed.text()
    window.close()


def test_a_too_new_or_too_old_api_is_refused_with_a_reason_before_any_code_runs(tmp_path):
    ran = tmp_path / "ran.txt"
    code = f'open({str(ran)!r}, "w").write("x")\ndef register():\n    raise SystemExit\n'
    write(tmp_path / "apps" / "future" / "app.py", code)
    write(tmp_path / "apps" / "future" / "app.json", {"api_version": appapi.API_VERSION + 1, "entry": "app.py"})
    write(tmp_path / "apps" / "ancient" / "app.json", {"api_version": 0, "command": ["x"]})
    write(tmp_path / "apps" / "silent.json", {"command": ["x"]})
    found = {f.id: f for f in appplugins.discover(folder=tmp_path / "apps", entry_points=lambda: [])}
    assert found["folder:future"].status == appplugins.REFUSED and "newer" in found["folder:future"].error
    assert found["folder:ancient"].status == appplugins.REFUSED
    assert found["file:silent"].status == appplugins.REFUSED and "api_version" in found["file:silent"].error
    assert not ran.exists()  # a refused app's code was not run
    late = appplugins.discover(
        entry_points=lambda: [FakePoint("late", lambda: declared("late"))], folder=tmp_path / "x"
    )
    assert late[0].status == appplugins.OK
    newer = appapi.SExternalApp("new", "New", label_page, appapi.API_VERSION + 1)
    assert appplugins.discover(folder=tmp_path / "x", entry_points=lambda: [FakePoint("n", newer)])[0].status == (
        appplugins.REFUSED
    )


def test_an_apps_folder_holds_python_apps_and_programs_and_disabled_ones_do_not_load(tmp_path):
    folder = tmp_path / "apps"
    write(folder / "py" / "app.json", {"api_version": 1, "entry": "app.py:make_app"})
    write(
        folder / "py" / "app.py",
        "import helper\nfrom annotoolbox.suite.appapi import SExternalApp\n"
        "def make_app():\n    return SExternalApp('folder-app', 'Folder app', helper.page, 1)\n",
    )
    write(folder / "py" / "helper.py", "def page(context):\n    return None\n")
    write(folder / "exe" / "app.json", {"api_version": 1, "name": "Tool", "command": ["tool.exe", "{file}"]})
    write(folder / "single.json", {"api_version": 1, "command": "viewer --open {file}", "file_types": ["dds"]})
    write(folder / "off" / "app.json", {"api_version": 1, "entry": "app.py"})
    write(folder / "off" / "app.py", "raise SystemExit('loaded although disabled')")
    write(folder / "broken" / "app.json", "{not json")
    appplugins.set_disabled("folder:off", True, folder)
    found = {f.id: f for f in appplugins.discover(folder=folder, entry_points=lambda: [])}
    assert found["folder:py"].status == appplugins.OK and found["folder:py"].key == "folder-app"
    assert found["folder:exe"].kind == appplugins.PROGRAM and found["folder:exe"].title == "Tool"
    assert found["file:single"].program.file_types == (".dds",)
    assert found["folder:off"].status == appplugins.DISABLED
    assert found["folder:broken"].status == appplugins.FAILED
    assert [s.key for s in appplugins.specs(list(found.values()))].count("off") == 0
    appplugins.set_disabled("folder:off", False, folder)
    assert appplugins.disabled(folder) == set()


def test_a_python_apps_context_gives_the_settings_the_project_and_the_game(settings, tmp_path):
    s, path = settings
    context = appapi.CAppContext("demo", s, path)
    assert context.project_folder == project.folder_of(s) and context.mods_dir == Path(s.mods)
    assert context.game_dir("anno117") == Path(s.anno117) and context.game_dir("nonsense") is None
    context.state["seen"] = 1
    context.save_settings()
    assert CSettings.load(path).apps["demo"] == {"seen": 1}
    with pytest.raises(ValueError, match="No folder"):
        context.open_game("anno1800")
    context.say("no window: nothing happens")


def test_an_apps_project_part_joins_the_build_and_a_failing_one_names_its_app(settings, tmp_path):
    s, path = settings
    good = appapi.SAppPart(lambda _p: True, lambda _p, _c, _log: {"data/demo/x.txt": b"1"})
    bad = appapi.SAppPart(lambda _p: True, lambda _p, _c, _log: 1 / 0)
    escape = appapi.SAppPart(lambda _p: True, lambda _p, _c, _log: {"../x": b"1"})
    points = [
        FakePoint(k, lambda k=k, p=p: declared(k, part=p)) for k, p in (("good", good), ("bad", bad), ("esc", escape))
    ]
    parts = {
        p.key: p for p in appplugins.parts(appplugins.discover(folder=tmp_path / "n", entry_points=lambda: points))
    }
    mod = project.CProject(tmp_path / "ws")
    assert parts["good"].has_work(mod) and parts["good"].render(mod, s, print) == {"data/demo/x.txt": b"1"}
    with pytest.raises(RuntimeError, match="Bad.*ZeroDivisionError"):
        parts["bad"].render(mod, s, print)
    with pytest.raises(RuntimeError, match="not a path inside the mod"):
        parts["esc"].render(mod, s, print)


def test_a_part_whose_has_work_raises_is_a_value_error_the_project_panel_catches(settings, tmp_path):
    broken = appapi.SAppPart(lambda _p: {}["x"], lambda _p, _c, _log: {})
    points = [FakePoint("broken", lambda: declared("broken", part=broken))]
    (part,) = appplugins.parts(appplugins.discover(folder=tmp_path / "n", entry_points=lambda: points))
    with pytest.raises(ValueError, match="Broken.*KeyError"):
        part.has_work(project.CProject(tmp_path / "ws"))


def test_closing_the_toolbox_asks_the_windows_of_its_own_about_unsaved_work(qt, settings, tmp_path):
    from PySide6.QtWidgets import QWidget

    class FakeHoldingWindow(QWidget):
        agree = False

        def closeEvent(self, event):
            event.accept() if self.agree else event.ignore()

    points = [FakePoint("page", lambda: declared("page"))]
    win = shell.CSuiteWindow(
        appplugins.specs(appplugins.discover(folder=tmp_path / "n", entry_points=lambda: points)), *settings
    )
    held = win._own_window("holding", FakeHoldingWindow)
    assert win.close() is False and win.m_own_windows["holding"] is held and held.isVisible()
    held.agree = True
    assert win.close() is True


def test_an_apps_window_without_background_jobs_can_be_opened_again_after_it_was_closed(qt, settings, tmp_path):
    from PySide6.QtWidgets import QWidget

    points = [FakePoint("page", lambda: declared("page"))]
    win = shell.CSuiteWindow(
        appplugins.specs(appplugins.discover(folder=tmp_path / "n", entry_points=lambda: points)), *settings
    )
    first = win._own_window("plain", QWidget)
    first.close()
    second = win._own_window("plain", QWidget)
    assert second is not first and second.isVisible()


def test_a_program_is_started_with_its_placeholders_filled(settings, tmp_path, monkeypatch):
    s, _ = settings
    started = []
    monkeypatch.setattr(
        appprograms.subprocess, "Popen", lambda args, **kw: started.append((args, kw)) or SimpleNamespace(pid=1)
    )
    program = appprograms.from_manifest(
        {
            "api_version": 1,
            "name": "Tool",
            "command": ["tool", "--game-dir", "{game_dir}", "--project={project}", "{file}", "--mods", "{mods_dir}"],
            "file_types": [".xml"],
            "games": ["anno2070", "anno117"],
            "requires": ["game_dir"],
            "unknown_key": 5,
        },
        tmp_path,
        "tool",
    )
    assert program.extra == {"unknown_key": 5}
    appprograms.launch(s, program, str(tmp_path / "a.xml"))
    args, kw = started[0]
    assert args == ["tool", "--game-dir", s.anno117, f"--project={project.folder_of(s)}", str(tmp_path / "a.xml"),
                    "--mods", s.mods]  # fmt: skip
    assert kw["env"]["ANNO_TOOLBOX_GAME_DIR"] == s.anno117 and kw["env"]["ANNO_TOOLBOX_FILE"].endswith("a.xml")
    appprograms.launch(s, program)  # no file: its argument is left out
    assert str(tmp_path / "a.xml") not in started[1][0] and len(started[1][0]) == 6
    s.game = ""
    s.anno117 = ""  # the first game of its list that has a folder, 2070's, then
    assert appprograms.command_line(program, appprograms.values_for(s, program))[2] == s.anno2070
    assert program.opens("B.XML") and not program.opens("b.dds")
    assert appprograms.for_file([program], "x.xml") == [program]


def test_a_program_that_needs_a_value_it_lacks_is_not_started_and_says_which(settings, tmp_path, monkeypatch):
    s, _ = settings
    s.anno117 = s.anno2070 = ""
    s.game = ""
    started = []
    monkeypatch.setattr(appprograms.subprocess, "Popen", lambda *a, **k: started.append(a))
    program = appprograms.from_manifest(
        {"api_version": 1, "command": ["t", "{game_dir}"], "requires": ["game_dir"], "games": ["anno117"]},
        tmp_path,
        "tool",
    )
    with pytest.raises(ValueError, match="game_dir"):
        appprograms.launch(s, program)
    assert started == []
    for bad in (
        {"api_version": 1, "command": ["t", "{nonsense}"]},
        {"api_version": 1, "command": []},
        {"api_version": 1, "command": ["t"], "key": "Bad Key"},
        {"api_version": 99, "command": ["t"]},
        [],
    ):
        with pytest.raises(ValueError):
            appprograms.from_manifest(bad, tmp_path, "tool")


def test_a_program_has_a_page_in_the_window_that_starts_it(qt, settings, tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(appprograms.subprocess, "Popen", lambda args, **kw: started.append(args) or SimpleNamespace())
    write(tmp_path / "apps" / "prog.json", {"api_version": 1, "name": "Prog", "command": ["prog", "{file}"]})
    found = appplugins.discover(folder=tmp_path / "apps", entry_points=lambda: [])
    window = shell.CSuiteWindow(appplugins.specs(found), *settings)
    page = window.show_app("prog")
    assert (
        page.windowTitle() == "Prog"
        and page.run(str(tmp_path / "f.txt"))
        and started == [["prog", str(tmp_path / "f.txt")]]
    )
    window.close()


def test_the_manage_window_lists_added_apps_and_switches_one_off(qt, settings, tmp_path, monkeypatch):
    from annotoolbox.suite import appmanage

    monkeypatch.setenv(appplugins.APPS_ENV, str(tmp_path / "apps"))
    write(tmp_path / "apps" / "prog.json", {"api_version": 1, "command": ["prog"]})
    write(tmp_path / "apps" / "quux.json", {"api_version": 7, "command": ["prog"]})
    monkeypatch.setattr(appplugins, "_g_found", appplugins.discover(entry_points=lambda: []))
    window = shell.CSuiteWindow([], *settings)
    dialog = window.open_apps_manager()
    assert isinstance(dialog, appmanage.CManageDialog) and dialog.m_table.rowCount() == 2
    assert dialog.m_table.item(1, 2).text() == "Refused" and "newer" in dialog.m_table.item(1, 4).text()
    dialog.m_table.selectRow(0)
    dialog.toggle_selected()
    assert appplugins.disabled() == {"file:prog"} and dialog.m_table.item(0, 2).text() == "Disabled"
    dialog.toggle_selected()
    assert appplugins.disabled() == set()
    dialog.close()
    window.close()


def test_the_built_in_listing_adds_what_the_folder_holds(tmp_path, monkeypatch):
    from annotoolbox.suite import apps

    monkeypatch.setenv(appplugins.APPS_ENV, str(tmp_path / "apps"))
    write(tmp_path / "apps" / "extra.json", {"api_version": 1, "command": ["prog"]})
    monkeypatch.setattr(appplugins, "_g_found", None)
    try:
        keys = [a.key for a in apps.available()]
        assert keys[-1] == "extra" and "islands" in keys
    finally:
        appplugins.reset()


def test_an_app_with_a_window_of_its_own_opens_it_from_the_switcher_and_a_failing_one_is_said(
    qt, settings, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QMainWindow, QMessageBox

    made = []

    def window(context):
        made.append(context.show_app("page"))
        return QMainWindow(context.shell)

    def broken(_context):
        raise RuntimeError("no window")

    points = [
        FakePoint("tool", lambda: appapi.SExternalApp("tool", "Tool", window, 1, window=True)),
        FakePoint("broke", lambda: appapi.SExternalApp("broke", "Broke", broken, 1, window=True)),
    ]
    specs = appplugins.specs(appplugins.discover(folder=tmp_path / "n", entry_points=lambda: points))
    page = appplugins.specs(
        appplugins.discover(folder=tmp_path / "n", entry_points=lambda: [FakePoint("page", lambda: declared("page"))])
    )
    win = shell.CSuiteWindow([*page, *specs], *settings)
    assert list(win.m_apps) == ["page"] and list(win.m_window_apps) == ["tool", "broke"]
    first = win.open_app_window("tool")
    assert made == [True] and first.isVisible() and win.open_app_window("tool") is first  # one window, made once
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: said.append(a[2]))
    assert win.open_app_window("broke") is None and "no window" in said[0]
    first.close()
    win.close()


def test_a_command_written_as_a_string_is_split_without_the_quotes_that_group_it():
    windows = appprograms.split_command(
        r"""C:\Prog\a.exe "C:\Program Files\Tool\tool.exe" --open "{file}" 'x y'""", False
    )
    assert windows == [r"C:\Prog\a.exe", r"C:\Program Files\Tool\tool.exe", "--open", "{file}", "x y"]
    assert appprograms.split_command('/opt/tool --a "b c"', posix=True) == ["/opt/tool", "--a", "b c"]


def test_a_windows_command_line_loses_its_quote_marks_but_keeps_the_spaces_inside():
    line = r'"C:\Program Files\Tools\app.exe" --open "{file}" plain'
    want = [r"C:\Program Files\Tools\app.exe", "--open", "{file}", "plain"]
    assert appprograms.split_command(line, posix=False) == want
    assert appprograms.split_command("viewer --open 'a b'", posix=True) == ["viewer", "--open", "a b"]


def test_say_without_a_window_goes_to_the_log(settings, caplog):
    s, path = settings
    caplog.set_level("INFO")
    appapi.CAppContext("demo", s, path).say("hello")
    assert "demo: hello" in caplog.text
