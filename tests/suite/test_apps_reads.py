"""Reads the attribute renames (``m_`` on classes, plain fields on records) had left on the old spelling, in the
suite's game choice and the UI host: each test drives the reader with the real class, or a stand-in carrying the
real class's names."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


@pytest.fixture
def ui_host():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMainWindow

    from annotoolbox.suite.settings import CSettings
    from annotoolbox.uiedit import uihost, uiwindow

    QApplication.instance() or QApplication([])

    class FakeGamePage(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            game = object.__new__(uiwindow._CGame)  # the Phoenix page's open game, as the window holds it
            game.m_key = "anno117"
            self.game, self.opened = game, []

        def open_game(self, key: str, *_rest) -> None:
            self.opened.append(key)

        def may_leave(self) -> bool:
            return False

    page = FakeGamePage()
    host = uihost.CUiHost(CSettings(), {uihost.PHOENIX: lambda: page})
    host.show_kind(uihost.PHOENIX)
    return host, page


def test_the_ui_host_tells_the_suite_the_game_of_its_phoenix_page(ui_host):
    host, _page = ui_host
    assert host.game() == "anno117"


def test_the_suites_game_choice_reaches_the_ui_app_through_the_hosts_pages(ui_host):
    from annotoolbox.suite import apps

    host, page = ui_host
    apps._ui_open(host, "anno117")
    assert page.opened == []  # on that game already
    apps._ui_open(host, "anno1800")
    assert page.opened == ["anno1800"]
    assert apps._ui_may_leave(host, "anno117") is True and apps._ui_may_leave(host, "anno1800") is False
    said = []
    page.statusBar().showMessage = said.append
    apps.APP_GAMES["ui"].say(host, "hello")
    assert said == ["hello"]


def test_the_suite_reads_the_text_apps_game_by_its_key():
    from annotoolbox.suite import apps

    assert apps.APP_GAMES["texts"].current(SimpleNamespace(m_key="anno1800")) == "anno1800"  # as textwindow's page
