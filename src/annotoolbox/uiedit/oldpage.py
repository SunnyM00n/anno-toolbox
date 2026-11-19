"""The UI app's page for the old interface (2070, 1404, 1701): a game box over ``guiold.view.OldGuiPage``.

The files are read only; the page is read in the background and replaced whenever another game is chosen. Its
``open_game`` is what the app's host and the suite's game choice call (docs/ui/old-gui.md, "Where this is in the code").
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from ..guiold.view import OldGuiPage
from ..infra.i18n import tr
from ..sources import gametree
from ..suite import gameopen, gamesync
from ..suite.background import Background

APP = "ui"  # its remembered state shares the UI app's key in the settings
OLD_GAMES = ("anno2070", "anno1404", "anno1701")  # the games of the old interface


class OldWindow(QMainWindow):
    def __init__(self, settings) -> None:
        super().__init__()
        self.settings = settings
        self.key: str | None = None  # the game open
        self.page: OldGuiPage | None = None
        self.background = Background(self)
        self.game_box = QComboBox()
        for key in OLD_GAMES:
            self.game_box.addItem(gametree.GAMES.get(key, key), key)
        self.game_box.setCurrentIndex(-1)
        self.game_box.activated.connect(lambda _i: self.open_game(self.game_box.currentData()))
        self.stack = QStackedWidget()
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("ui.old_game")))
        top.addWidget(self.game_box)
        top.addStretch(1)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 4, 6, 0)
        lay.addLayout(top)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(body)
        self.setWindowTitle(tr("ui.old_interface"))
        self.statusBar().showMessage(tr("ui.old_interface_pick_game"))

    @property
    def game(self):
        """The game open, as the Phoenix page holds it (the suite reads ``.key``); None while none is."""
        return self if self.key else None

    def may_leave(self) -> bool:
        return True  # read only: nothing to save

    def open_game(self, key: str, wait: bool = False) -> None:
        """Open a game's old interface in the background; the suite's other apps follow it."""
        if key not in OLD_GAMES or not gamesync.may_switch(self, key):
            return
        gameopen.open_installed(
            self, self.settings, key, self.background, then=lambda tree: self._opened(key, tree), wait=wait
        )

    def _opened(self, key: str, tree) -> None:
        title = gametree.GAMES.get(key, key)
        if isinstance(tree, Exception):
            self.statusBar().showMessage(tr("common.game_not_open_error", game=title, error=tree))
            return
        if self.page is not None:
            self.stack.removeWidget(self.page)
            self.page.deleteLater()  # taken off the stack only, it would stay for the window's life
        self.key, self.page = key, OldGuiPage(tree)
        self.stack.addWidget(self.page)
        self.stack.setCurrentWidget(self.page)
        self.game_box.setCurrentIndex(self.game_box.findData(key))
        self.settings.apps.setdefault(APP, {})["old"] = key
        self.setWindowTitle(f"{title} - {tr('ui.old_interface')}")
        self.statusBar().showMessage(tr("ui.old_interface_read_only", game=title))
