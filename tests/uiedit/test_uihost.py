"""The UI app holds the Phoenix editor and the Flash editor as two pages of one window, and one project part."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow

from annotoolbox.suite import buildout
from annotoolbox.suite.settings import CSettings
from annotoolbox.uiedit import projectpart, uihost


class FakeWindow(QMainWindow):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name, self.opened = name, []
        self.setWindowTitle(name)

    def open_game(self, game: str, *_rest) -> None:
        self.opened.append(game)


@pytest.fixture
def host():
    QApplication.instance() or QApplication([])
    made: dict[str, FakeWindow] = {}

    def maker(kind: str):
        return lambda: made.setdefault(kind, FakeWindow(kind))

    window = uihost.CUiHost(CSettings(), {uihost.PHOENIX: maker(uihost.PHOENIX), uihost.FLASH: maker(uihost.FLASH)})
    window.made = made
    return window


def test_a_game_opens_in_the_page_of_its_kind(host):
    host.open_game("anno1800")
    assert host.m_current == uihost.PHOENIX and host.made[uihost.PHOENIX].opened == ["anno1800"]
    assert uihost.FLASH not in host.made  # a page is made when first shown
    host.open_game("anno2205")
    assert host.m_current == uihost.FLASH and host.windowTitle() == uihost.FLASH
    assert host.m_stack.currentWidget() is host.made[uihost.FLASH]
    assert host.m_buttons[uihost.FLASH].isChecked()


def test_the_page_last_open_comes_back(host):
    host.open_game("anno2205")
    assert host.settings.apps["ui"]["kind"] == uihost.FLASH
    host.m_pages.clear()
    host.show_last()
    assert host.m_current == uihost.FLASH
    host.settings.apps["ui"] = {"kind": uihost.PHOENIX, "game": "anno117"}
    host.show_last()
    assert host.m_current == uihost.PHOENIX and host.made[uihost.PHOENIX].opened[-1] == "anno117"


def test_closing_asks_every_page(host):
    host.show_kind(uihost.PHOENIX)
    host.show_kind(uihost.FLASH)
    host.made[uihost.FLASH].closeEvent = lambda event: event.ignore()
    assert host.close() is False


def test_one_part_covers_both_editors(tmp_path):
    from annotoolbox.flashui import projectpart as flash
    from annotoolbox.suite.project import CProject

    assert projectpart.UI_PART.key == "ui"
    flash_project = CProject(tmp_path / "flash", mod_id="mine", game="anno2205")
    assert not projectpart.UI_PART.has_work(flash_project)
    flash.edit_file(flash_project).parent.mkdir(parents=True)
    flash.edit_file(flash_project).write_text(
        '{"format": 1, "game": "anno2205", "files": {"data/ui/x.png": "x"}}', "utf-8"
    )
    assert projectpart.UI_PART.has_work(flash_project)
    phoenix_project = CProject(tmp_path / "phoenix", mod_id="mine", game="anno117")
    assert not projectpart.UI_PART.has_work(phoenix_project)
    projectpart.save_file(phoenix_project, "anno117", "data/ui/a.xml", b"a")
    assert projectpart.UI_PART.has_work(phoenix_project)
    assert buildout.read_all(projectpart.UI_PART.render(phoenix_project, None, lambda _m: None)) == {
        "data/ui/a.xml": b"a"
    }


def test_a_page_that_refuses_to_close_leaves_the_page_in_front_shown(host):
    host.open_game("anno1800")
    host.open_game("anno2205")
    host.show_kind(uihost.PHOENIX)
    host.made[uihost.FLASH].closeEvent = lambda event: event.ignore()  # unsaved work
    assert host.close() is False
    assert not host.made[uihost.PHOENIX].isHidden()


def test_the_suite_reads_the_pages_and_the_open_game_of_the_ui_host(host):
    from types import SimpleNamespace

    from annotoolbox.suite import apps

    page = host.show_kind(uihost.PHOENIX)
    page.game = SimpleNamespace(m_key="anno117", may_leave=None)
    page.may_leave = lambda: False
    assert apps._ui_may_leave(host, "anno117") is True  # that game is open already
    assert apps._ui_may_leave(host, "anno1800") is False  # leaving it asks the page
    apps._ui_open(host, "anno117")
    assert page.opened == []  # the page is on the game: nothing to open
    apps._ui_open(host, "anno1800")
    assert page.opened == ["anno1800"]
