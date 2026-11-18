"""The installed program's own side: the events, the move from the old installer, the shortcuts, the setup page's
choices and the uninstall (suite/install.py, installevents.py, migrate.py, shortcuts.py, setupchoices.py, setuppage.py).
Every folder is a temp folder and every registry and process is a stand-in: nothing real is touched."""

import os
import sys
import time
from types import SimpleNamespace

import pytest

from annotoolbox.suite import install, installevents, migrate, setupchoices, shortcuts
from annotoolbox.suite.settings import Settings

CODE = "{12345678-ABCD-4EF0-8123-456789ABCDEF}"


def installed_tree(tmp_path):
    root = tmp_path / "AnnoToolbox"
    (root / "current").mkdir(parents=True)
    (root / "Update.exe").write_bytes(b"u")
    (root / "AnnoToolbox.exe").write_bytes(b"launcher")
    (root / "current" / "AnnoToolbox.exe").write_bytes(b"app")
    return root


# where the installed program is


def test_the_install_root_is_the_folder_above_current(tmp_path):
    root = installed_tree(tmp_path)
    exe = str(root / "current" / "AnnoToolbox.exe")
    assert install.root(exe) == root and install.installed(exe)
    assert install.launcher(exe) == root / "AnnoToolbox.exe"  # the stable one, not the versioned folder's
    assert install.uninstall_argv(root) == [str(root / "Update.exe"), "uninstall"]


def test_a_copied_program_folder_or_a_source_run_is_not_an_install(tmp_path):
    loose = tmp_path / "copy"
    loose.mkdir()
    (loose / "AnnoToolbox.exe").write_bytes(b"x")
    assert install.root(str(loose / "AnnoToolbox.exe")) is None and not install.installed(
        str(loose / "AnnoToolbox.exe")
    )
    assert install.root() is None  # this test run is not frozen


def test_a_root_without_the_updater_is_not_installed(tmp_path):
    root = installed_tree(tmp_path)
    (root / "Update.exe").unlink()
    assert not install.installed(str(root / "current" / "AnnoToolbox.exe"))


def test_the_uninstaller_is_started_as_a_list_from_the_root(tmp_path):
    root, started = installed_tree(tmp_path), []
    install.start_uninstall(root, lambda argv, **kw: started.append((argv, kw)))
    assert started[0][0] == [str(root / "Update.exe"), "uninstall"] and started[0][1]["cwd"] == str(root)


# the events


def test_the_first_start_leaves_a_mark_the_setup_page_clears(tmp_path):
    assert not installevents.first_run_pending(tmp_path)
    installevents.mark_first_run(tmp_path)
    assert installevents.first_run_pending(tmp_path)
    installevents.clear_first_run(tmp_path)
    assert not installevents.first_run_pending(tmp_path)


def test_the_old_updaters_downloads_are_removed_and_the_settings_stay(tmp_path):
    (tmp_path / "updates").mkdir()
    (tmp_path / "updates" / "AnnoToolbox-0.1.2.msi").write_bytes(b"x" * 10)
    (tmp_path / "editor-app.json").write_text("{}", encoding="utf-8")
    installevents.tidy_old_downloads(tmp_path)
    assert not (tmp_path / "updates").exists() and (tmp_path / "editor-app.json").is_file()


class Chain:
    """What the packer's module offers: the builder whose events are recorded."""

    def __init__(self):
        self.events, self.ran = {}, False

    def __getattr__(self, name):
        if not name.startswith("on_"):
            raise AttributeError(name)

        def set_event(callback):
            self.events[name] = callback
            return self

        return set_event

    def run(self):
        self.ran = True


def test_the_events_are_armed_before_the_program_goes_on():
    chain = Chain()
    assert installevents.run(lambda: chain) is True and chain.ran
    assert set(chain.events) == {
        "on_first_run",
        "on_after_install_fast_callback",
        "on_after_update_fast_callback",
        "on_before_uninstall_fast_callback",
    }


def test_nothing_is_armed_outside_the_installed_program():
    assert installevents.run() is False  # not frozen: a source run


def test_an_event_that_fails_never_raises_and_leaves_its_traceback(monkeypatch, tmp_path):
    chain = Chain()
    monkeypatch.setattr(installevents, "local_data_dir", lambda: tmp_path)  # the events' folder: never the real one
    monkeypatch.setattr(installevents, "mark_first_run", lambda: 1 / 0)
    installevents.run(lambda: chain)
    chain.events["on_first_run"]()  # kept, not raised: the event's process has no console to say it on
    (kept,) = (tmp_path / "logs").iterdir()
    assert "ZeroDivisionError" in kept.read_text(encoding="utf-8")
    (tmp_path / "updates").mkdir()
    chain.events["on_after_install_fast_callback"]("0.2.0")
    assert not (tmp_path / "updates").exists() and len(list((tmp_path / "logs").iterdir())) == 1


# the uninstall: the settings stay unless the dialog left a fresh request


def test_the_request_is_honoured_only_while_fresh(tmp_path):
    assert not installevents.delete_requested(tmp_path)
    installevents.request_uninstall(True, tmp_path, now=1000.0)
    assert installevents.delete_requested(tmp_path, now=1100.0)
    assert not installevents.delete_requested(tmp_path, now=1000.0 + installevents.REQUEST_LIFETIME + 1)
    assert not installevents.delete_requested(tmp_path, now=900.0)  # from the future: not ours
    installevents.request_uninstall(False, tmp_path)  # the box was not ticked: no request
    assert not (tmp_path / installevents.UNINSTALL_REQUEST).exists()


def test_an_unreadable_request_deletes_nothing(tmp_path):
    (tmp_path / installevents.UNINSTALL_REQUEST).write_text("not json", encoding="utf-8")
    assert not installevents.delete_requested(tmp_path)


def make_data(tmp_path):
    data = tmp_path / "anno-toolbox"
    (data / "cache").mkdir(parents=True)
    (data / "editor-app.json").write_text("{}", encoding="utf-8")
    (data / "cache" / "a.bin").write_bytes(b"x" * 2048)
    return data


@pytest.mark.parametrize(
    ("request_age", "deleted"),
    [
        pytest.param(None, False, id="no-request"),  # the box was not ticked: the settings and caches stay
        pytest.param(0.0, True, id="fresh-request"),
        pytest.param(3 * installevents.REQUEST_LIFETIME, False, id="stale-request"),  # of a cancelled uninstall
    ],
)
def test_the_uninstall_removes_the_data_only_on_a_fresh_request(tmp_path, request_age, deleted):
    data, spawned = make_data(tmp_path), []
    if request_age is not None:
        installevents.request_uninstall(True, data, now=time.time() - request_age)
    result = installevents.before_uninstall(
        data, shortcuts.Places(tmp_path / "sm", tmp_path / "dt"), tmp_path / "root", spawn=spawned.append
    )
    assert not (data / installevents.UNINSTALL_REQUEST).exists()  # answered either way: it is not asked twice
    if not deleted:
        assert result["data"] is None and spawned == []
        assert (data / "editor-app.json").is_file() and (data / "cache" / "a.bin").is_file()
        return
    # moved aside at once, still whole for the removal that runs apart from the event
    assert not data.exists() and result["data"] == spawned[0] and spawned[0].name.startswith("anno-toolbox.deleting-")
    assert (spawned[0] / "editor-app.json").is_file()


def test_the_data_size_counts_the_files_under_the_folder(tmp_path):
    assert installevents.data_size(make_data(tmp_path)) == 2048 + 2
    assert installevents.data_size(tmp_path / "missing") == 0


# shortcuts (a temp folder stands in for the Start menu and the desktop)


def places(tmp_path):
    return shortcuts.Places(tmp_path / "Programs", tmp_path / "Desktop")


@pytest.mark.skipif(sys.platform != "win32", reason="shell links are Windows')s")
def test_a_shortcut_is_made_read_back_and_removed_only_when_it_points_into_the_install(tmp_path):
    root = installed_tree(tmp_path)
    where = places(tmp_path)
    problems = shortcuts.sync(True, True, root / "AnnoToolbox.exe", where)
    assert problems == [] and shortcuts.present(where) == (True, True)
    assert shortcuts.link_target(shortcuts.link_path(where.desktop)) == root / "AnnoToolbox.exe"
    foreign = shortcuts.link_path(where.start_menu)
    assert shortcuts.remove_ours(where, tmp_path / "elsewhere") == []  # not ours: stays
    assert foreign.is_file()
    assert sorted(shortcuts.remove_ours(where, root)) == sorted([foreign, shortcuts.link_path(where.desktop)])
    assert shortcuts.present(where) == (False, False)


@pytest.mark.skipif(sys.platform != "win32", reason="shell links are Windows'")
def test_a_choice_removes_the_shortcut_that_is_not_wanted(tmp_path):
    root, where = installed_tree(tmp_path), places(tmp_path)
    shortcuts.sync(True, True, root / "AnnoToolbox.exe", where)
    shortcuts.sync(True, False, root / "AnnoToolbox.exe", where)
    assert shortcuts.present(where) == (True, False)


def test_a_shortcut_that_cannot_be_made_is_reported_not_raised(tmp_path):
    def refuse(path, target, description):
        raise OSError("no")

    assert shortcuts.sync(True, False, tmp_path / "x.exe", places(tmp_path), refuse) == ["no"]


def test_a_shortcut_that_cannot_be_removed_is_reported_and_the_other_is_still_done(tmp_path):
    """The removal raised out of the setup page's thread: no shortcut was made and nothing was said."""
    where, made = places(tmp_path), []
    shortcuts.link_path(where.start_menu).mkdir(parents=True)  # stands in for a file that will not be deleted
    problems = shortcuts.sync(False, True, tmp_path / "x.exe", where, lambda p, t, d: made.append(p))
    assert len(problems) == 1 and made == [shortcuts.link_path(where.desktop)]


def test_the_shortcut_is_judged_by_its_target_not_its_name(tmp_path):
    where = places(tmp_path)
    where.desktop.mkdir()
    link = shortcuts.link_path(where.desktop)
    link.write_bytes(b"x")
    assert shortcuts.remove_ours(where, tmp_path / "root", read=lambda p: tmp_path / "other" / "a.exe") == []
    assert shortcuts.remove_ours(where, tmp_path / "root", read=lambda p: tmp_path / "root" / "a.exe") == [link]
    assert not link.exists()


# the move from the old installer


class FakeRegistry:
    def __init__(self, values=None, subkeys=None):
        self.values, self.sub, self.deleted = values or {}, subkeys or {}, []

    def read(self, key, name):
        return self.values.get((key, name))

    def subkeys(self, key):
        return self.sub.get(key, [])

    def delete_tree(self, key):
        self.deleted.append(key)


def test_the_leftover_product_code_must_look_like_one():
    good = FakeRegistry({(migrate.MIGRATION_KEY, "ProductCode"): CODE})
    assert migrate.leftover_code(good) == CODE
    assert migrate.leftover_code(FakeRegistry({(migrate.MIGRATION_KEY, "ProductCode"): "calc.exe /c"})) == ""
    assert migrate.leftover_code(FakeRegistry()) == ""


def test_the_uninstall_command_is_a_list_from_the_system_folder(tmp_path):
    argv = migrate.uninstall_argv(CODE, str(tmp_path))
    assert argv == [str(tmp_path / "System32" / "msiexec.exe"), "/x", CODE, "/qn", "/norestart"]
    with pytest.raises(ValueError):
        migrate.uninstall_argv("{x} /q")


def test_the_leftover_is_removed_quietly_and_its_record_deleted():
    reg, calls = FakeRegistry({(migrate.MIGRATION_KEY, "ProductCode"): CODE}), []
    assert migrate.remove_leftover(reg, run=lambda argv: calls.append(argv) or 0) == "removed"
    assert calls[0][1:3] == ["/x", CODE] and reg.deleted == [migrate.MIGRATION_KEY]


def test_without_a_leftover_nothing_runs():
    assert migrate.remove_leftover(FakeRegistry(), run=lambda argv: 1 / 0) == "none"


def test_a_busy_installer_is_waited_for_then_the_next_start_tries_again():
    waits, codes = [], iter([migrate.BUSY, migrate.BUSY, 0])
    assert migrate.remove_product(CODE, lambda argv: next(codes), waits.append) == "removed" and waits == [5.0, 5.0]
    waits.clear()
    assert migrate.remove_product(CODE, lambda argv: migrate.BUSY, waits.append, tries=3) == "busy" and len(waits) == 2
    reg = FakeRegistry({(migrate.MIGRATION_KEY, "ProductCode"): CODE})
    migrate.remove_leftover(reg, run=lambda argv: migrate.BUSY, sleep=lambda s: None)
    assert reg.deleted == []  # the record stays, so the next start asks again


def test_a_failed_or_absent_product_is_told_apart():
    assert migrate.remove_product(CODE, lambda argv: 1603) == "failed"
    assert migrate.remove_product(CODE, lambda argv: migrate.NOT_INSTALLED) == "removed"  # gone already
    assert migrate.remove_product(CODE, lambda argv: (_ for _ in ()).throw(OSError("x"))) == "failed"


def test_the_shortcut_choice_of_the_old_install_is_carried_over():
    own = FakeRegistry({(migrate.MIGRATION_KEY, "StartMenu"): "#1", (migrate.MIGRATION_KEY, "Desktop"): ""})
    assert migrate.carried_shortcuts(own) == (True, False)  # a start menu entry, no desktop shortcut
    none = FakeRegistry({(migrate.MIGRATION_KEY, "StartMenu"): "", (migrate.MIGRATION_KEY, "Desktop"): ""})
    assert migrate.carried_shortcuts(none) == (False, False)  # the old install had neither: not forced on
    legacy = FakeRegistry({(migrate.LEGACY_KEY, "DesktopShortcut"): "#1"})
    assert migrate.carried_shortcuts(legacy) == (False, True)
    assert migrate.carried_shortcuts(FakeRegistry()) is None  # a fresh install says nothing


def test_old_installs_are_found_in_the_apps_list_by_their_installer_entry():
    uninstall = migrate.UNINSTALL_KEY
    reg = FakeRegistry(
        {
            (f"{uninstall}\\{CODE}", "WindowsInstaller"): "1",
            (f"{uninstall}\\{CODE}", "DisplayName"): "Anno Toolbox",
            (f"{uninstall}\\other", "DisplayName"): "Anno Toolbox",
            (f"{uninstall}\\{{AAAAAAAA-0000-4000-8000-000000000000}}", "DisplayName"): "Something else",
        },
        {uninstall: [CODE, "other", "{AAAAAAAA-0000-4000-8000-000000000000}"]},
    )
    assert migrate.old_products(reg) == [CODE]


# the setup page's choices


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for variable in ("LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(variable, str(tmp_path))
    return tmp_path


def test_the_page_opens_after_the_installers_first_start_and_for_a_person_without_settings():
    s = Settings()
    assert setupchoices.pending(s, mark=True, settings_existed=True)  # an installer's first start, even a migrated one
    assert setupchoices.pending(s, mark=False, settings_existed=False)  # a source run, first time
    assert not setupchoices.pending(s, mark=False, settings_existed=True)  # an existing person after an update
    s.setup_done = True
    assert not setupchoices.pending(s, mark=True, settings_existed=False)  # answered once: never again by itself


def test_the_defaults_are_the_found_games_and_both_shortcuts_ticked(isolated):
    s = Settings(anno117="X:/game", language="de")
    start = setupchoices.defaults(s)
    assert start.start_menu and start.desktop and start.check_updates and start.language == "de"
    assert start.folders["anno117"] == "X:/game"
    assert not setupchoices.defaults(s, (True, False)).desktop  # the old install's choice wins


def test_a_game_folder_is_taken_only_when_it_is_that_games_install(isolated, tmp_path):
    game = tmp_path / "game"
    (game / "Bin" / "Win64").mkdir(parents=True)
    (game / "Bin" / "Win64" / "Anno117.exe").write_bytes(b"x")
    assert setupchoices.folder_problem("anno117", str(game)) == ""
    assert setupchoices.folder_problem("anno117", str(tmp_path / "nothing")) != ""
    assert setupchoices.folder_problem("anno117", "") == ""  # a game is optional


def test_the_answers_go_into_the_settings_and_a_wrong_folder_is_named_and_left(isolated, tmp_path):
    game = tmp_path / "game"
    (game / "Bin" / "Win64").mkdir(parents=True)
    (game / "Bin" / "Win64" / "Anno117.exe").write_bytes(b"x")
    s = Settings(anno2070="keep")
    choices = setupchoices.Choices("de", False, True, False, {"anno117": str(game), "anno2070": str(tmp_path / "bad")})
    problems = setupchoices.apply(s, choices)
    assert len(problems) == 1 and s.anno2070 == "keep" and s.anno117 == str(game)
    assert (s.language, s.check_updates, s.shortcut_start_menu, s.shortcut_desktop, s.setup_done) == (
        "de",
        False,
        True,
        False,
        True,
    )


def test_an_unknown_language_leaves_the_language_as_it_was(isolated):
    s = Settings(language="en")
    setupchoices.apply(s, setupchoices.Choices("zz", True, True, True, {}))
    assert s.language == "en"


def test_the_first_runs_work_removes_old_installs_before_it_makes_the_shortcuts(tmp_path):
    from annotoolbox.suite import setuppage

    order = []
    choices = setupchoices.Choices("en", True, True, True, {}, (CODE,))
    problems = setuppage.carry_out(
        choices, tmp_path / "a.exe", places(tmp_path), remove=lambda code: order.append("remove") or "removed"
    )
    assert order == ["remove"] and problems == [] or sys.platform != "win32"
    bad = setuppage.carry_out(setupchoices.Choices("en", True, False, False, {}, (CODE,)), None, None, lambda c: "busy")
    assert len(bad) == 1  # an old install that could not be removed is reported


def test_the_default_window_of_the_installed_program_is_the_suite_not_the_editor_alone():
    from annotoolbox.suite import toolbox

    plain = toolbox.window_parser().parse_args([])
    assert toolbox.default_app(plain, frozen=True) == "islands"
    assert toolbox.default_app(plain, frozen=False) is None
    assert toolbox.default_app(toolbox.window_parser().parse_args(["--app", "viewer"]), frozen=True) == "viewer"
    assert toolbox.default_app(toolbox.window_parser().parse_args(["a.a7m"]), frozen=True) is None


# the windows


def _window(settings):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMainWindow

    QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.settings = settings
    window.save_settings = lambda: None
    return window


def test_the_setup_page_shows_the_choices_and_hides_the_shortcuts_where_none_can_be_made(isolated):
    from annotoolbox.suite import setuppage

    s = Settings(language="en")
    window = _window(s)
    with_links = setuppage.SetupDialog(window, s, can_shortcut=True)
    without = setuppage.SetupDialog(window, s, can_shortcut=False)
    assert with_links.start_menu.parent() is with_links  # in the page
    assert without.start_menu.parent() is None  # kept as a value, not shown
    assert with_links.choices().start_menu and with_links.choices().desktop
    assert set(with_links.choices().folders) == set(setupchoices.game_keys())
    assert without.layout().indexOf(without.start_menu) == -1


def test_the_setup_page_offers_the_removal_of_old_installs_only_when_there_are_some(isolated):
    from annotoolbox.suite import setuppage

    window = _window(Settings())
    none = setuppage.SetupDialog(window, Settings())
    some = setuppage.SetupDialog(window, Settings(), old=[CODE])
    assert none.choices().remove_old == () and some.choices().remove_old == (CODE,)
    some.remove_old.setChecked(False)
    assert some.choices().remove_old == ()


def test_the_help_menu_has_the_setup_page_and_the_uninstall_only_in_an_installed_program(isolated, monkeypatch):
    from annotoolbox.suite import setuppage

    for installed, expected in ((False, ["Setup…"]), (True, ["Setup…", "Uninstall…"])):
        monkeypatch.setattr(setuppage.install, "installed", lambda installed=installed: installed)
        window = _window(Settings())
        setuppage.attach(window)
        menu = window.menuBar().actions()[0].menu()
        assert [a.text() for a in menu.actions()] == expected


def test_the_games_and_folders_dialog_keeps_a_folder_typed_in_the_setup_page(isolated):
    from annotoolbox.suite import setuppage

    s = Settings(language="en", anno1404="C:/old 1404")

    def games_and_folders():  # the other dialog: saved with another 1800 folder, the rest as it was
        s.anno1800 = "D:/Anno 1800"

    window = _window(s)
    page = setuppage.SetupDialog(window, s, more=games_and_folders)
    page.edits["anno117"].setText("E:/typed 117")
    page.edits["anno1404"].setText("")
    page._more()
    folders = page.choices().folders
    assert (folders["anno117"], folders["anno1404"], folders["anno1800"]) == ("E:/typed 117", "", "D:/Anno 1800")


def test_the_uninstall_dialog_keeps_the_data_unless_the_box_is_ticked(isolated, tmp_path):
    from annotoolbox.suite import setuppage

    window = _window(Settings())
    dialog = setuppage.UninstallDialog(window, tmp_path, 3 * 2**20)
    assert not dialog.delete.isChecked()  # off by default
    assert str(tmp_path) in dialog.where.text() and "3 MB" in dialog.where.text()


def test_the_uninstall_dialog_opens_before_the_data_folder_is_measured(isolated, tmp_path):
    import threading

    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite import setuppage

    measuring, go_on, threads = threading.Event(), threading.Event(), []

    def slow(folder):
        threads.append(threading.current_thread())
        measuring.set()
        go_on.wait(5)
        return 5 * 2**20

    window = _window(Settings())
    dialog = setuppage.UninstallDialog(window, tmp_path, measure=slow)
    assert measuring.wait(5) and threads[0] is not threading.main_thread()
    assert str(tmp_path) in dialog.where.text() and "measuring" in dialog.where.text()
    go_on.set()
    threads[0].join(5)
    QApplication.processEvents()  # the size comes back to the window's thread
    assert "5 MB" in dialog.where.text() and "measuring" not in dialog.where.text()


def test_the_uninstall_leaves_its_request_and_starts_the_updater(isolated, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from annotoolbox.suite import setuppage, toolbox

    root = installed_tree(tmp_path)
    started = []
    monkeypatch.setattr(setuppage.install, "root", lambda exe=None: root)
    monkeypatch.setattr(setuppage.install, "start_uninstall", lambda where: started.append(where))
    monkeypatch.setattr(
        setuppage.UninstallDialog, "exec", lambda self: (self.delete.setChecked(True), QDialog.DialogCode.Accepted)[1]
    )
    window = _window(Settings())
    log = toolbox.start_log(tmp_path / "logs")  # the program's log is open in the data folder
    assert setuppage.uninstall(window) is True and started == [root]
    assert toolbox.start_log(tmp_path / "logs") is not log  # it was closed before the uninstaller started
    toolbox.stop_log()
    assert installevents.delete_requested()  # the data folder of this isolated run, a fresh request


def test_a_failed_start_of_the_uninstaller_withdraws_the_request(isolated, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox

    from annotoolbox.suite import setuppage

    def fail(where):
        raise OSError("no updater")

    monkeypatch.setattr(setuppage.install, "root", lambda exe=None: installed_tree(tmp_path))
    monkeypatch.setattr(setuppage.install, "start_uninstall", fail)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(
        setuppage.UninstallDialog, "exec", lambda self: (self.delete.setChecked(True), QDialog.DialogCode.Accepted)[1]
    )
    assert setuppage.uninstall(_window(Settings())) is False
    assert not installevents.delete_requested()  # a later uninstall from the apps list must keep the data


def test_the_uninstall_does_nothing_in_a_program_that_was_not_installed(isolated):
    from annotoolbox.suite import setuppage

    assert setuppage.uninstall(_window(Settings())) is False


def test_the_settings_of_a_session_that_did_not_know_the_setup_fields_still_load(isolated):
    path = isolated / "editor-app.json"
    path.write_text('{"theme": "dark"}', encoding="utf-8")
    s = Settings.load(path)
    assert s.theme == "dark" and s.setup_done is False and s.shortcut_start_menu and s.shortcut_desktop
    assert isinstance(SimpleNamespace(), object)
