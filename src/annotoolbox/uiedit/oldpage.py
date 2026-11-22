"""The UI app's page for the old interface (2070, 1404, 1701): a game box over ``guiold.view.COldGuiPage``.

The files are read only; the page is read in the background and replaced whenever another game is chosen. Its
``open_game`` is what the app's host and the suite's game choice call (docs/ui/old-gui.md, "Where this is in the code").
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from ..guiold.view import COldGuiPage
from ..infra.i18n import tr
from ..sources import gametree
from ..suite import gameopen, gamesync
from ..suite.background import CBackground

APP = "ui"  # its remembered state shares the UI app's key in the settings
OLD_GAMES = ("anno2070", "anno1404", "anno1701")  # the games of the old interface


class COldWindow(QMainWindow):
    def __init__(self, settings) -> None:
        super().__init__()
        self.settings = settings
        self.m_key: str | None = None  # the game open
        self.m_page: COldGuiPage | None = None
        self.m_background = CBackground(self)
        self.m_game_box = QComboBox()
        for key in OLD_GAMES:
            self.m_game_box.addItem(gametree.GAMES.get(key, key), key)
        self.m_game_box.setCurrentIndex(-1)
        self.m_game_box.activated.connect(lambda _i: self.open_game(self.m_game_box.currentData()))
        self.m_stack = QStackedWidget()
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("ui.old_game")))
        top.addWidget(self.m_game_box)
        top.addStretch(1)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 4, 6, 0)
        layout.addLayout(top)
        layout.addWidget(self.m_stack, 1)
        self.setCentralWidget(body)
        self.setWindowTitle(tr("ui.old_interface"))
        self.statusBar().showMessage(tr("ui.old_interface_pick_game"))

    @property
    def game(self):
        """The game open, as the Phoenix page holds it (the suite reads ``.key``); None while none is."""
        return self if self.m_key else None

    def may_leave(self) -> bool:
        return True  # read only: nothing to save

    def open_game(self, key: str, is_waiting: bool = False) -> None:
        """Open a game's old interface in the background; the suite's other apps follow it."""
        if key not in OLD_GAMES or not gamesync.may_switch(self, key):
            return
        gameopen.open_installed(
            self,
            self.settings,
            key,
            self.m_background,
            then=lambda tree: self._opened(key, tree),
            is_waiting=is_waiting,
        )

    def _opened(self, key: str, tree) -> None:
        title = gametree.GAMES.get(key, key)
        if isinstance(tree, Exception):
            self.statusBar().showMessage(tr("common.game_not_open_error", game=title, error=tree))
            return
        if self.m_page is not None:
            self.m_stack.removeWidget(self.m_page)
            self.m_page.deleteLater()  # taken off the stack only, it would stay for the window's life
        self.m_key, self.m_page = key, COldGuiPage(tree)
        self.m_stack.addWidget(self.m_page)
        self.m_stack.setCurrentWidget(self.m_page)
        self.m_game_box.setCurrentIndex(self.m_game_box.findData(key))
        self.settings.apps.setdefault(APP, {})["old"] = key
        self.setWindowTitle(f"{title} - {tr('ui.old_interface')}")
        self.statusBar().showMessage(tr("ui.old_interface_read_only", game=title))
