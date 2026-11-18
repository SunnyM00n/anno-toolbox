"""One game for every app of the suite: the selected game is ``Settings.game``, the window says when it changes
(``SuiteWindow.game_changed``) and every app that is started follows through its ``AppGame`` (``AppSpec.game``).

``may_switch(window, key, ask)`` is what an app calls before it opens a game: alone it only asks its own leave
question; as a page of the suite's window it also asks the other apps and, when nobody cancelled, switches them.
``GameSync`` is the window's side of it. The rules: docs/suite.md, "One game for every app".
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..infra.i18n import tr
from ..sources import gametree


def _yes(_page, _key: str) -> bool:
    return True


def _remember(state: dict, game: str) -> None:
    """An app's remembered state set to ``game``; the file it had open last belonged to the game before."""
    if state.get("game") != game:
        state.pop("file", None)
        state["game"] = game


def _say(page, text: str) -> None:
    page.statusBar().showMessage(text)


@dataclass(frozen=True)
class AppGame:
    """How the suite's game selection reaches one app.

    ``state``: the app's key in ``Settings.apps``; ``games()``: the games it can show; ``current(page)``: the one it
    is on ("" for none); ``open(page, key)``: let the old game's file go and open ``key`` (nothing is asked: that was
    done); ``may_leave(page, key)``: its leave question before ``key`` is opened, False when the person cancelled.
    """

    state: str
    games: Callable[[], Collection[str]]
    current: Callable[[Any], str]
    open: Callable[[Any, str], None]
    may_leave: Callable[[Any, str], bool] = _yes
    needs_folder: bool = True  # the app reads the game's install: without its folder it cannot follow
    remember: Callable[[dict, str], None] = _remember  # what the app starts on, written before its page is made
    say: Callable[[Any, str], None] = _say  # a line for the app's status bar


def may_switch(window, key: str, ask: Callable[[], bool] = lambda: True) -> bool:
    """Whether ``window`` may open the game ``key`` now; ``ask`` is its own leave question (unsaved work). In the
    suite's window a True also means every other app agreed and was sent to ``key``."""
    found = _page_of(window)
    if found is None:  # the app alone, or its page while it is being made
        return ask()
    sync, app = found
    return sync.picked(app, key, ask)


def _page_of(window) -> tuple[GameSync, str] | None:
    """The suite window's selection and the app whose page holds ``window``; None outside a suite window."""
    chain, node = [], window
    while node is not None:
        sync = getattr(node, "game_sync", None)
        if isinstance(sync, GameSync):
            app = next((k for k, page in sync.shell.app_pages() if any(page is held for held in chain)), None)
            return (sync, app) if app is not None else None
        chain.append(node)
        node = node.parent()
    return None


class GameSync:
    """The selection of one suite window (``shell``): asks the apps, records the game and sends them to it."""

    def __init__(self, shell) -> None:
        self.shell = shell
        self.switching = False  # the apps are being sent to the selected game: none of them asks or picks
        self._leader = ""  # the app whose own pick is being followed (it opens the game itself)
        shell.game_changed.connect(self.follow)

    def side(self, app: str) -> AppGame | None:
        return getattr(self.shell.apps[app], "game", None)

    def cannot(self, side: AppGame, key: str) -> str:
        """Why an app cannot show the game ``key``, as its status line says it; "" when it can."""
        title = gametree.GAMES.get(key, key)
        if key not in side.games():
            return tr("common.game_sync_not_shown", game=title)
        folder = getattr(self.shell.settings, key, "")
        if side.needs_folder and not (folder and Path(folder).is_dir()):
            return tr("common.game_sync_no_folder", game=title)
        return ""

    def starting(self, app: str) -> None:
        """Before an app's page is made: it starts on the selected game when it can show it, else on its own last."""
        side, key = self.side(app), self.shell.settings.game
        if side is not None and key and not self.cannot(side, key):
            side.remember(self.shell.settings.apps.setdefault(side.state, {}), key)

    def followers(self, key: str, but: str = "") -> list[tuple[AppGame, Any]]:
        """The started apps that would have to leave their game for ``key``."""
        out = []
        for app, page in self.shell.app_pages():
            side = self.side(app)
            if side is not None and app != but and not self.cannot(side, key) and side.current(page) != key:
                out.append((side, page))
        return out

    def picked(self, app: str, key: str, ask: Callable[[], bool]) -> bool:
        """The app ``app`` is about to open ``key`` (its own leave question: ``ask``). False cancels the whole
        switch: nothing was closed anywhere."""
        if self.switching:
            return True
        side = self.side(app)
        if side is None or self.cannot(side, key):  # the app says itself why it does not open: nobody follows
            return ask()
        if not ask() or not self.agreed(key, but=app):
            return False
        self.select(key, leader=app)
        return True

    def agreed(self, key: str, but: str = "") -> bool:
        """Every app with unsaved work asked (Save / Discard / Cancel); False at the first Cancel."""
        return all(side.may_leave(page, key) for side, page in self.followers(key, but))

    def choose(self, key: str) -> bool:
        """Select ``key`` for every app (``SuiteWindow.select_game``); False when an app's question was cancelled."""
        if not self.agreed(key):
            return False
        self.select(key)
        return True

    def select(self, key: str, leader: str = "") -> None:
        self.shell.settings.game = key
        self._leader = leader
        try:
            self.shell.game_changed.emit(key)
        finally:
            self._leader = ""

    def follow(self, key: str) -> None:
        """The window's ``game_changed``: every started app opens ``key``, or says why it stays as it is."""
        self.switching = True
        try:
            for app, page in self.shell.app_pages():
                side = self.side(app)
                if side is None or app == self._leader:
                    continue
                why = self.cannot(side, key)
                if why:
                    side.say(page, why)
                elif side.current(page) != key:
                    side.open(page, key)
        finally:
            self.switching = False
