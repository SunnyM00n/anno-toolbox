"""The start-up screen: it shows before the apps load, names the step, and goes away when the first window shows or an
error stops the start (offscreen; what it looks like is only seen on a screen)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow

from annotoolbox.infra import i18n
from annotoolbox.suite import splash, toolbox


@pytest.fixture
def qt():
    app = QApplication.instance() or QApplication([])
    yield app
    splash.close()
    i18n.use("en")


def test_the_splash_shows_names_the_step_and_closes(qt):
    assert splash.show()
    assert splash._g_screen is not None and splash._g_screen.isVisible()
    splash.status("Loading the apps...")
    assert splash._g_screen.m_step == "Loading the apps..."
    splash.close()
    assert splash._g_screen is None
    splash.close()  # again, and with none open
    splash.status("nothing open")


def test_the_first_main_window_ends_it(qt):
    splash.show()
    window = QMainWindow()
    window.show()
    qt.processEvents()
    qt.processEvents()
    assert splash._g_screen is None
    window.close()


def test_a_dialog_does_not_end_it(qt):
    from PySide6.QtWidgets import QDialog

    splash.show()
    box = QDialog()
    box.show()
    qt.processEvents()
    assert splash._g_screen is not None
    box.close()


def test_the_language_of_the_settings_is_read_without_loading_them(qt, tmp_path, monkeypatch):
    (tmp_path / splash.SETTINGS_FILE).write_text('{"language": "de"}', encoding="utf-8")
    monkeypatch.setattr(splash, "config_dir", lambda: tmp_path)
    monkeypatch.delenv(i18n.ENV_VAR, raising=False)
    splash._language()
    assert i18n.current() == "de"
    i18n.use("en")
    (tmp_path / splash.SETTINGS_FILE).write_bytes(
        b'\xef\xbb\xbf{"language": "de"}'
    )  # a byte order mark, as Settings takes it
    splash._language()
    assert i18n.current() == "de"
    (tmp_path / splash.SETTINGS_FILE).write_text("not json", encoding="utf-8")
    splash._language()
    assert i18n.current() == "en"


def test_a_failed_start_closes_the_splash(qt, monkeypatch):
    def fail(_args):
        assert splash._g_screen is not None  # it was up while the start ran
        raise RuntimeError("no game")

    monkeypatch.setattr(toolbox, "start_window", fail)
    with pytest.raises(RuntimeError):
        toolbox.run_window([])
    assert splash._g_screen is None


def test_a_smoke_start_has_no_splash(qt, monkeypatch):
    seen = []
    monkeypatch.setattr(toolbox, "start_window", lambda _args: seen.append(splash._g_screen) or 0)
    assert toolbox.run_window(["--app", "islands", "--smoke"]) == 0
    assert seen == [None]
