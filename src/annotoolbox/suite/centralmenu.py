"""The Toolbox window's one menu bar: what means the same in every app (Project, Open game, Save, Quit, Settings,
Window, Help) is central; the front app's own menus are merged in while it is in front (docs/suite.md, "The menu bar").

``CCentralMenus(shell)`` builds the menus on the suite window's bar, ``show_page(page)`` takes an app in front. An app
says what is central with ``mark(action_or_menu, slot)`` where it builds its menus (``GAME``, ``QUIT``, ``SETTINGS``
dropped; ``SAVE`` lifted into the central Save); a page may answer ``menu_window()`` and emit ``menus_changed``.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMenu

from ..infra.i18n import tr
from ..sources import gametree
from . import widgets

SLOT = "centralSlot"  # the property that carries a mark
GAME, SAVE, QUIT, SETTINGS = "game", "save", "quit", "settings"  # entries the central menus answer
FILE, WINDOW, HELP = "file", "window", "help"  # the menus an app's menu of that name merges into
DROPPED = (GAME, QUIT, SETTINGS)


def mark(item: QAction | QMenu, slot: str):
    """Say that ``item`` (an action, or a menu by its action) is the app's version of the central entry ``slot``."""
    action = item.menuAction() if isinstance(item, QMenu) else item
    action.setProperty(SLOT, slot)
    return item


def slot_of(action: QAction) -> str:
    return action.property(SLOT) or ""


def plain(text: str) -> str:
    """``text`` without its access key."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&&")


def menu_of(window, key: str) -> QMenu:
    """The central menu ``key`` of a suite window; a window without them (a plain one) has one Help menu for all."""
    central = getattr(window, "m_central", None)
    if central is not None:
        return central.menu_of(key)
    title = tr("islands.help")
    found = next((action.menu() for action in window.menuBar().actions() if action.text() == title), None)
    return found or window.menuBar().addMenu(title)


def menu_window(page) -> QMainWindow | None:
    """The window whose menus stand for the page: what it answers, else the page when it is a window."""
    pick = getattr(page, "menu_window", None)
    if callable(pick):
        return pick()
    return page if isinstance(page, QMainWindow) else None


class CCentralMenus:
    """The menus of one suite window (``shell``) and the front app's merged into them."""

    def __init__(self, shell) -> None:
        self.m_shell = shell
        self.m_bar = shell.menuBar()
        self.m_base: dict[QAction, str] = {}  # a central entry's text without its access key
        self.m_added: list[tuple[object, QAction]] = []  # (menu or bar, action) of the front app, to take away again
        self.m_watched: set[int] = set()  # the pages whose ``menus_changed`` is followed
        self.m_front = None  # the page whose menus are shown
        self.m_menus: dict[str, QMenu] = {}
        self._file()
        self._settings()
        self._window()
        self._help()
        for menu in self.m_menus.values():
            menu.aboutToShow.connect(self.rekey)

    def _make(self, key: str, title: str) -> QMenu:
        menu = self.m_bar.addMenu(plain(title))
        self.m_menus[key] = menu
        return menu

    def _file(self) -> None:
        shell = self.m_shell
        file = self._make(FILE, tr("common.menu.file"))
        file.addAction(tr("central.project"), shell.open_project)
        self.m_games = file.addMenu(plain(tr("common.menu.open_game")))
        group = QActionGroup(self.m_games, exclusive=True)
        self.m_game_actions: dict[str, QAction] = {}
        for key, name in gametree.GAMES.items():
            act = self.m_games.addAction(name, lambda _c=False, k=key: shell.select_game(k))
            act.setCheckable(True)
            group.addAction(act)
            self.m_game_actions[key] = act
        self.m_games.aboutToShow.connect(self.enable_games)
        file.addSeparator()
        self.m_save_slot = file.addAction(tr("central.save"))
        widgets.enable_unless(self.m_save_slot, tr("central.no_document"))
        self.m_file_tail = file.addSeparator()
        file.addAction(tr("islands.quit"), shell.close, QKeySequence("Ctrl+Q"))

    def _settings(self) -> None:
        shell = self.m_shell
        menu = self._make(SETTINGS, tr("central.menu.settings"))
        menu.addAction(tr("common.settings_dots"), shell.edit_settings)
        menu.addAction(tr("central.game_link"), shell.open_link_panel)
        menu.addAction(tr("common.debug_commands"), shell.open_palette)
        menu.addAction(tr("appapi.manage_apps"), shell.open_apps_manager)
        self.m_settings_action = menu.menuAction()

    def _window(self) -> None:
        window = self._make(WINDOW, tr("common.menu.window"))
        self.m_full_screen = window.addAction(tr("central.full_screen"), self.toggle_full_screen, QKeySequence("F11"))
        self.m_full_screen.setCheckable(True)
        apps = window.addMenu(plain(tr("common.apps")))
        for act in self.m_shell.m_switch_actions.values():  # the switcher's own actions: their keys stay theirs
            apps.addAction(act)
        self.m_window_anchor = window.addSeparator()
        self.m_window_anchor.setVisible(False)
        window.aboutToShow.connect(lambda: self.m_full_screen.setChecked(self.m_shell.isFullScreen()))

    def _help(self) -> None:
        help_ = self._make(HELP, tr("islands.help"))
        self.m_help_anchor = help_.addSeparator()  # the front app's entries go above it
        self.m_help_anchor.setVisible(False)

    def toggle_full_screen(self) -> None:
        if self.m_shell.isFullScreen():
            self.m_shell.showNormal()
        else:
            self.m_shell.showFullScreen()

    def menu_of(self, key: str) -> QMenu:
        return self.m_menus[key]

    def enable_games(self) -> None:
        """Open game: the selected game ticked, a game without a folder greyed with the reason."""
        settings = self.m_shell.m_settings
        for key, act in self.m_game_actions.items():
            folder = getattr(settings, key, "")
            act.setChecked(settings.game == key)
            why = "" if folder else tr("central.no_game_folder", game=gametree.GAMES[key])
            widgets.enable_unless(act, why)

    # the front app
    def show_page(self, page) -> None:
        """The menus of the app ``page`` shown in the bar, the menus of the app before it taken away."""
        self.clear()
        self.m_front = page
        changed = getattr(page, "menus_changed", None)
        if changed is not None and id(page) not in self.m_watched:
            self.m_watched.add(id(page))
            changed.connect(lambda: self.m_front is page and self.show_page(page))
        window = menu_window(page)
        if window is not None:
            window.menuBar().hide()  # the one bar is this window's
            self._merge(window)
        self.rekey()

    def clear(self) -> None:
        for holder, action in self.m_added:
            holder.removeAction(action)
        self.m_added.clear()
        self.m_save_slot.setVisible(True)
        self.m_window_anchor.setVisible(False)
        self.m_help_anchor.setVisible(False)

    def _target(self, action: QAction) -> str:
        found = slot_of(action)
        if found:
            return found
        title = plain(action.text())
        titles = {
            plain(tr("common.menu.file")): FILE,
            plain(tr("common.menu.window")): WINDOW,
            plain(tr("islands.help")): HELP,
        }
        return titles.get(title, "")

    def _merge(self, window: QMainWindow) -> None:
        for action in window.menuBar().actions():
            menu = action.menu()
            if menu is None:
                continue
            target = self._target(action)
            if target in self.m_menus and target != SETTINGS:
                self._into(target, menu)
            else:
                self.m_bar.insertAction(self.m_settings_action, action)
                self.m_added.append((self.m_bar, action))

    def _into(self, target: str, menu: QMenu) -> None:
        """The entries of an app's menu in the central menu ``target``: the dropped ones left out, the Save lifted."""
        central = self.m_menus[target]
        entries: list[QAction] = []
        for act in menu.actions():
            slot = slot_of(act)
            if slot == SAVE and self.m_save_slot.isVisible():
                self.m_save_slot.setVisible(False)
                self._add(central, act, self.m_save_slot)
            elif slot in DROPPED or (act.isSeparator() and (not entries or entries[-1].isSeparator())):
                continue
            else:
                entries.append(act)
        while entries and entries[-1].isSeparator():
            entries.pop()
        if not entries:
            return
        before = {FILE: self.m_file_tail, HELP: self.m_help_anchor}.get(target)
        if target == WINDOW:
            self.m_window_anchor.setVisible(True)
        elif target == HELP:
            self.m_help_anchor.setVisible(True)
        for act in entries:
            self._add(central, act, before)

    def _add(self, menu: QMenu, action: QAction, before: QAction | None) -> None:
        menu.insertAction(before, action)
        self.m_added.append((menu, action))

    # access keys
    def rekey(self) -> None:
        """Every central entry and title gets an access key that the front app's entries in the same place do not
        carry (the app's own keys stay): redone at each switch and each time a central menu opens."""
        shown = {action for _holder, action in self.m_added}
        away = widgets.access_letters(action.text() for holder, action in self.m_added if holder is self.m_bar)
        titles = [self.m_menus[k].menuAction() for k in self.m_menus]
        for act, text in zip(
            titles, widgets.access_keys([plain(action.text()) for action in titles], away), strict=True
        ):
            act.setText(text)
        for menu in (*self.m_menus.values(), self.m_games):
            mine = [
                action
                for action in menu.actions()
                if action not in shown and not action.isSeparator() and action.isVisible()
            ]
            for action in mine:
                action.setText(self.m_base.setdefault(action, plain(action.text())))
            entries = [action for action in menu.actions() if not action.isSeparator()]
            widgets.assign_access_keys(mine, [entries])
