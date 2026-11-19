"""The UI app: one page for each kind of interface file, a switcher over them.

``UiHost(settings, makers)`` holds the editor of the Phoenix files (Anno 117, 1800, 1404, 1701; ``uiwindow``), the
editor of 2205's Flash movies (``flashui.uiwindow``) and the viewer of the old interface (2070, 1404, 1701;
``oldpage``) as pages of one window, the way the island editor holds every game; a page is made (its modules imported)
the first time it is shown, and a kind without a maker has no button. ``open_game`` is the entry other code
calls: the game's own kind of page comes to the front. Both editors stay as they are; docs/ui-editor.md.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..suite import gamesync
from .uiframe import APP, GAMES

PHOENIX, FLASH, OLD = "phoenix", "flash", "old"
FLASH_GAME = "anno2205"
OLD_GAME = "anno2070"  # the old interface's own game; 1404 and 1701 also have Phoenix files, so they stay there
KINDS = (  # (key, label, tooltip) of the switcher's buttons
    (PHOENIX, N_("ui.anno_117_1800_1404_1701"), N_("ui.interface_files_anno_117_1800")),
    (FLASH, N_("ui.anno_2205"), N_("ui.anno_2205s_flash_interface_movies")),
    (OLD, N_("ui.old_interface_tab"), N_("ui.old_interface_tab_tip")),
)  # fmt: skip


def kind_of(game: str) -> str:
    """The kind of editor a game's interface files need."""
    return FLASH if game == FLASH_GAME else OLD if game == OLD_GAME else PHOENIX


class UiHost(QWidget):
    """The switcher on top, the open kind's editor below it."""

    def __init__(self, settings, makers: dict[str, Callable[[], QMainWindow]]) -> None:
        super().__init__()
        self.settings = settings
        self.makers = makers
        self.pages: dict[str, QMainWindow] = {}
        self.current: str | None = None
        self.stack = QStackedWidget()
        self.buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self, exclusive=True)
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.addWidget(QLabel(tr("ui.interface_files")))
        for key, label, tip in (k for k in KINDS if k[0] in makers):
            button = QPushButton(tr(label), checkable=True, toolTip=tr(tip))
            button.clicked.connect(lambda _on=False, k=key: self.pick_kind(k))
            group.addButton(button)
            bar.addWidget(button)
            self.buttons[key] = button
        bar.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(self.stack, 1)
        self.setWindowTitle(tr("ui.ui_editor"))

    def page_of(self, kind: str) -> QMainWindow:
        """The editor of a kind, made when it is not yet; it is held as a page of this window."""
        if kind not in self.pages:
            page = self.makers[kind]()
            page.setWindowFlags(Qt.WindowType.Widget)
            page.windowTitleChanged.connect(lambda _t, k=kind: self._title(k))
            self.pages[kind] = page
            self.stack.addWidget(page)
        return self.pages[kind]

    def game(self) -> str:
        """The game of the page in front; "" while that page has none open."""
        if self.current == FLASH:
            return FLASH_GAME
        if self.current == OLD:
            return getattr(self.pages.get(OLD), "key", None) or ""
        open_game = getattr(self.pages.get(PHOENIX), "game", None)
        return open_game.key if open_game is not None else ""

    def pick_kind(self, kind: str) -> None:
        """A click on the switcher: the page comes to the front and the suite's other apps follow its game; a
        question cancelled in one of them leaves the page in front where it is."""
        open_game = getattr(self.pages.get(PHOENIX), "game", None)
        if kind == FLASH:
            game = FLASH_GAME
        elif kind == OLD:
            game = getattr(self.pages.get(OLD), "key", None) or ""
        else:
            game = open_game.key if open_game is not None else ""
        if game and not gamesync.may_switch(self, game):
            if self.current:
                self.buttons[self.current].setChecked(True)
            return
        self.show_kind(kind)

    def show_kind(self, kind: str) -> QMainWindow:
        page = self.page_of(kind)
        self.current = kind
        self.stack.setCurrentWidget(page)
        self.buttons[kind].setChecked(True)
        self.settings.apps.setdefault(APP, {})["kind"] = kind
        self._title(kind)
        return page

    def open_game(self, game: str, *opened) -> None:
        """Open an installed game in the editor of its kind (``opened``: what that editor takes after the game)."""
        page = self.show_kind(kind_of(game))
        if kind_of(game) == PHOENIX and game in GAMES or kind_of(game) == OLD:
            page.open_game(game, *opened)

    def show_last(self) -> None:
        """The kind and game open last: the Phoenix editor on its game, else the Flash one."""
        last = self.settings.apps.get(APP, {})
        if last.get("kind") == FLASH:
            self.show_kind(FLASH)
            return
        if last.get("kind") == OLD and OLD in self.makers:
            self.show_kind(OLD).open_game(last.get("old") or OLD_GAME)
            return
        page = self.show_kind(PHOENIX)
        if last.get("game") in GAMES:
            page.open_game(last["game"])

    def _title(self, kind: str) -> None:
        if kind == self.current:
            self.setWindowTitle(self.pages[kind].windowTitle())

    def close(self) -> bool:
        """Close every page (each asks about its unsaved work); False when one stays open."""
        for page in self.pages.values():
            if not page.close():
                return False
        return super().close()
