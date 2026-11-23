"""The suite's one window: an app switcher down the left side and the open app beside it, on the shared settings
and theme. An app is an ``SAppSpec`` whose page is made the first time it is shown (its modules load then), so the
window opens as fast as its first app; every app keeps its own command as well (``desktop`` for the islands).

``CSuiteWindow(apps, settings, path, parts, deliveries)`` is the window, ``run(apps, start)`` the program (``smoke``: every app
opened once and closed, for a check); ``suite.apps`` lists the apps and their parts of a project's mod (``Project``,
under the switcher). Plan: ``docs/tool-suite.md`` section 7, C1 and C4.
"""

from __future__ import annotations

import sys
from collections.abc import Collection
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QWidget,
)

from ..infra.i18n import english, tr
from . import (
    centralmenu,
    gamesync,
    icons,
    linkclient,
    linkhandover,
    linkroute,
    links,
    linuxnotice,
    livelink,
    migrate,
    settingsdialog,
    setuppage,
    shaderbar,
    shadercleanup,
    splash,
    theme,
    updatenotice,
    widgets,
)
from .extspecs import SAppSpec
from .settings import CSettings, settings_file

TITLE = splash.NAME  # the suite's name (2026-11-09)
FAILED = "appFailed"  # the object name of the page shown for an app that could not start
LAST_DIGIT = 9  # the apps that get a Ctrl+digit shortcut


def app_shortcut(position: int) -> str:
    """The key of the n-th app (from 1): Ctrl+1 to Ctrl+9, the tenth Ctrl+0, the next ones Ctrl+Shift+1 to 9; "" past
    those ("Ctrl+10" is no key: Qt made an unknown one of it). ``docs/suite.md`` "Keys of the suite"."""
    if position <= LAST_DIGIT:
        return f"Ctrl+{position}"
    if position == LAST_DIGIT + 1:
        return "Ctrl+0"
    return f"Ctrl+Shift+{position - LAST_DIGIT - 1}" if position <= 2 * LAST_DIGIT + 1 else ""


class CSuiteWindow(QMainWindow):
    """The window: the switcher (a tool bar of the apps) and a stack with one page per app shown so far."""

    link_status = Signal(object)  # the game link's status changed (from any thread)
    link_event = Signal(object, object)  # (handler, payload) of a game event, to run on this thread
    game_changed = Signal(str)  # the game every app is on changed (``gamesync``; suite.md "One game for every app")

    def __init__(
        self, apps: list[SAppSpec], settings: CSettings, settings_path: Path, parts=None, deliveries=None
    ) -> None:
        super().__init__()
        self.m_apps = {app.key: app for app in apps if not app.window}
        self.m_window_apps = {app.key: app for app in apps if app.window}  # apps that open a window of their own
        self.m_settings, self.m_settings_path = settings, settings_path
        self.m_deliveries = dict(deliveries or {})  # how the older games take a project (``suite.project.SDelivery``)
        self.m_parts = list(parts or [])  # the apps' shares of a project's mod (``suite.project.SPart``)
        self.m_pages: dict[str, QWidget] = {}
        self.m_own_windows: dict[str, QWidget] = {}  # the palette, the link panel and Project, while each is kept
        self.m_current: str | None = None
        self.m_stack = QStackedWidget()
        self.setCentralWidget(self.m_stack)
        self.m_switch_actions: dict[str, QAction] = {}
        self._make_switcher([app for app in apps if not app.window])
        self.m_central = centralmenu.CCentralMenus(self)  # the one menu bar; the front app's menus join it
        self.resize(1600, 980)
        self.apply_theme()
        self._m_follow_system_scheme = lambda _s: self.m_settings.theme == "system" and self.apply_theme()
        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self._m_follow_system_scheme)
        self.m_provided = self._provide_links()
        self._attach_game_link()
        self.m_updates = updatenotice.attach(self)  # None in a build without a release page
        self.m_game_sync = gamesync.CGameSync(self)

    def _make_switcher(self, apps: list[SAppSpec]) -> None:
        """The tool bar down the left side: an action per app (a key each, ``app_shortcut``), then Project."""
        self.m_switcher = QToolBar(tr("common.apps"), self, movable=False, floatable=False, objectName="appSwitcher")
        self.m_switcher.setIconSize(QSize(32, 32))
        self.m_switcher.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.m_switcher)
        group = QActionGroup(self, exclusive=True)
        for number, app in enumerate(apps, 1):
            key = app_shortcut(number)
            tooltip = f"{tr(app.summary)} ({key})" if key else tr(app.summary)
            app_action = QAction(icons.icon(app.icon), tr(app.title), self, checkable=True, toolTip=tooltip)
            if key:
                app_action.setShortcut(QKeySequence(key))
            app_action.triggered.connect(lambda _on=False, key=app.key: self.show_app(key))
            group.addAction(app_action)
            self.m_switcher.addAction(app_action)
            self.m_switch_actions[app.key] = app_action
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.m_switcher.addWidget(spacer)
        self.m_project_action = QAction(
            icons.icon("project"),
            tr("common.project"),
            self,
            toolTip=tr("common.tip.open_project_mod_every_app"),
        )
        self.m_project_action.triggered.connect(lambda _c=False: self.open_project())
        self.m_switcher.addAction(self.m_project_action)
        self.m_window_actions: dict[str, QAction] = {}  # the apps that open a window of their own (``SAppSpec.window``)
        for key, app in self.m_window_apps.items():
            action = QAction(icons.icon(app.icon), tr(app.title), self, toolTip=tr(app.summary))
            action.triggered.connect(lambda _c=False, key=key: self.open_app_window(key))
            self.m_switcher.addAction(action)
            self.m_window_actions[key] = action
        self.m_manage_action = QAction(icons.icon("app_external"), tr("appapi.manage_apps"), self)
        self.m_manage_action.setToolTip(tr("appapi.manage_apps_tip"))
        self.m_manage_action.triggered.connect(lambda _c=False: self.open_apps_manager())
        self.m_switcher.addAction(self.m_manage_action)

    def _provide_links(self) -> list[tuple[str, object]]:
        """Every app's links (``SAppSpec.links``) answered by this window: for an action the app's page made and brought
        to the front; a quiet link (a query) gets the page only when it is made already, else None."""
        out = []
        for app in self.m_apps.values():
            for name, handler in app.links.items():
                quiet = name in app.quiet_links

                def follow(*arguments, key=app.key, handler=handler, quiet=quiet):
                    if quiet:
                        return handler(self.m_pages.get(key), *arguments)
                    page = self.show_app(key)
                    return None if page is None or page.objectName() == FAILED else handler(page, *arguments)

                links.provide(name, follow)
                out.append((name, follow))
        return out

    def _attach_game_link(self) -> None:
        """The status indicator of the live link and the game to editor routing (``livelink``, ``linkroute``)."""
        self.m_shader_bar = shaderbar.attach(self)  # the game look's shader compiles, left of the link
        self.m_link_label = QPushButton(flat=True)  # the status; a click opens the game link panel
        self.m_link_label.clicked.connect(lambda _c=False: self.open_link_panel())
        self.statusBar().addPermanentWidget(self.m_link_label)
        commands = QPushButton(tr("common.debug_commands"), flat=True)
        commands.setToolTip(tr("common.tip.games_debug_commands_sent_over"))
        commands.clicked.connect(lambda _c=False: self.open_palette())
        self.statusBar().addPermanentWidget(commands)
        self.link_status.connect(lambda status: self.m_link_label.setText(status.text))
        self.link_event.connect(lambda handler, payload: handler(payload))
        self.m_link_label.setToolTip(tr("common.tip.live_link_running_game_click"))
        self._m_link_removers = [
            livelink.on("status", self.link_status.emit),
            linkroute.attach(lambda handler, payload: self.link_event.emit(handler, payload)),
        ]
        self.m_link_label.setText(livelink.current().status().text)
        if not linkclient.supported():  # the link is Windows only: say so where the status is
            self.m_link_label.setText(tr("common.live_link_windows_only"))
            self.m_link_label.setToolTip(tr("common.live_link_windows_only_why"))
        self._m_link_search = linkclient.autoconnect()  # finds a game that starts later, and one that comes back
        self._m_handover = (
            linkhandover.CWatcher().start() if self._m_link_search else None
        )  # Anno 1800's open in editor

    def open_palette(self):
        """The debug-command palette (``linkpalette``); its history and favourites live in the settings."""
        from .linkpalette import CPaletteWindow

        state = self.m_settings.apps.setdefault("palette", {})
        # a save that fails is said, not raised
        window = self._own_window("palette", lambda: CPaletteWindow(self, state, self.save_settings))
        self.m_debug_palette = window  # not ``palette``: that is the window's own colours (QWidget.palette)
        return window

    def open_link_panel(self):
        """The game link panel (``linkpanel``); returns it."""
        from .linkpanel import CLinkDialog

        self.m_link_panel = self._own_window("link", lambda: CLinkDialog(self))
        return self.m_link_panel

    def _own_window(self, key: str, make, show: bool = True):
        """The window ``key``: the open one brought to the front, else a new one from ``make`` (shown, or left to the
        caller). The closed one before it is deleted (it would stay, hidden, a child of this window) unless its work
        still runs."""
        old = self.m_own_windows.pop(key, None)
        if old is not None and old.isVisible():
            self.m_own_windows[key] = old
            old.raise_()
            old.activateWindow()
            return old
        busy = getattr(getattr(old, "m_background", None), "busy", None)  # an app's own window may run no jobs
        if old is not None and not (busy and busy()):
            old.deleteLater()
        window = self.m_own_windows[key] = make()
        if show:
            window.show()
        return window

    def apply_theme(self) -> None:
        theme.apply(self, self.m_settings.theme)
        icons.set_foreground(theme.colors()["icon"])
        for key, act in self.m_switch_actions.items():
            act.setIcon(icons.icon(self.m_apps[key].icon))
        if hasattr(self, "m_project_action"):
            self.m_project_action.setIcon(icons.icon("project"))
        for key, action in getattr(self, "m_window_actions", {}).items():
            action.setIcon(icons.icon(self.m_window_apps[key].icon))
        if hasattr(self, "m_manage_action"):
            self.m_manage_action.setIcon(icons.icon("app_external"))

    def open_apps_manager(self):
        """The Manage apps window (``appmanage``): the apps other people added; returns it."""
        from .appmanage import CManageDialog

        dialog = self._own_window("apps", lambda: CManageDialog(self), show=False)
        theme.apply(dialog, self.m_settings.theme)
        dialog.show()
        return dialog

    def open_app_window(self, key: str):
        """The window of an app that has one of its own (``SAppSpec.window``), made once and brought to the front;
        returns it, None (the reason said) when the app could not make it."""
        app = self.m_window_apps[key]
        try:
            window = self._own_window(f"app:{key}", lambda: app.make(self), show=False)
        except Exception as exc:  # the app's own code: the Toolbox and the other apps stay usable
            QMessageBox.warning(
                self, TITLE, tr("common.could_not_start", title=tr(app.title), error=f"{type(exc).__name__}: {exc}")
            )
            return None
        theme.apply(window, self.m_settings.theme)  # a window of its own: the palette is not inherited
        window.show()
        window.raise_()
        return window

    def edit_settings(self) -> bool:
        """Settings > Games and folders: the game folders, the workspace, the theme and the language of every app;
        False when cancelled. The island editor, when made, reads its lists again."""
        dialog = settingsdialog.CSettingsDialog(self.m_settings, self)
        if not dialog.exec():
            return False
        for name, value in dialog.values().items():
            if name in CSettings.__dataclass_fields__ and isinstance(value, type(getattr(self.m_settings, name))):
                setattr(self.m_settings, name, value)
        self.save_settings()
        self.apply_theme()
        changed = getattr(self.m_pages.get("islands"), "settings_changed", None)
        if changed is not None:
            changed()
        return True

    def open_project(self):
        """The Project window (``projectpanel``); returns it."""
        from .projectpanel import CProjectDialog

        try:
            dialog = self._own_window("project", lambda: CProjectDialog(self, self.m_parts), show=False)
        except ValueError as exc:  # a project file that does not read
            QMessageBox.warning(self, TITLE, str(exc))
            return None
        theme.apply(dialog, self.m_settings.theme)  # a window of its own: the palette is not inherited
        dialog.show()
        return dialog

    def show_app(self, key: str) -> QWidget | None:
        """Bring the app ``key`` to the front, making its page on its first showing."""
        if self.page_of(key) is None:
            return None
        self.m_current = key
        self.m_stack.setCurrentWidget(self.m_pages[key])
        self.m_central.show_page(self.m_pages[key])
        self.m_switch_actions[key].setChecked(True)
        self._title(key)
        return self.m_pages[key]

    def page_of(self, key: str) -> QWidget | None:
        """The page of the app ``key``, made (behind the current one) when it is not yet."""
        if key not in self.m_apps:
            return None
        if key not in self.m_pages:
            self.m_game_sync.starting(key)  # it starts on the suite's game when it can show it
            try:
                page = self.m_apps[key].make(self)
            except Exception as exc:  # the reason in the window, the other apps stay usable
                shown = f"{type(exc).__name__}: {exc}"
                page = QLabel(tr("common.could_not_start", title=tr(self.m_apps[key].title), error=shown))
                page.setAlignment(Qt.AlignmentFlag.AlignCenter)
                page.setObjectName(FAILED)
            if isinstance(page, QMainWindow):
                page.setWindowFlags(Qt.WindowType.Widget)  # an app's own window, held as a page of this one
            page.windowTitleChanged.connect(lambda _t, k=key: self._title(k))
            self.m_pages[key] = page
            self.m_stack.addWidget(page)
        return self.m_pages[key]

    def app_pages(self) -> list[tuple[str, QWidget]]:
        """The apps that are started: (key, page), without the ones that could not start."""
        return [(key, page) for key, page in self.m_pages.items() if page.objectName() != FAILED]

    def select_game(self, key: str) -> bool:
        """Send every app to the game ``key`` (each asks about its unsaved work first); False when one of the
        questions was cancelled: no app switched."""
        return self.m_game_sync.choose(key)

    def view_file(self, game: str, path: str) -> bool:
        """Show a game file (``game``: ``anno117`` ...) in the Viewer app (``links.VIEW_FILE``); False without one."""
        return links.view_file(game, path)

    def _title(self, key: str) -> None:
        if key == self.m_current:
            own = self.m_pages[key].windowTitle()
            self.setWindowTitle(f"{own} · {TITLE}" if own else f"{tr(self.m_apps[key].title)} · {TITLE}")

    def save_settings(self) -> None:
        self.m_settings.apps["shell"] = {"app": self.m_current}
        try:
            self.m_settings.save(self.m_settings_path)
        except OSError as exc:
            QMessageBox.warning(self, TITLE, tr("common.could_not_save_settings", error=exc))

    def may_close(self) -> bool:
        """The first step of closing: every page and window of its own that has a ``can_close`` is asked (it asks its
        person about unsaved work and a running job, and releases nothing); one that says no keeps everything open and
        is shown. One without the query agrees."""
        for page in [*self.m_pages.values(), *self.m_own_windows.values()]:
            query = getattr(page, "can_close", None)
            if query is not None and not query():
                if page in self.m_pages.values():
                    self.m_stack.setCurrentWidget(page)
                else:
                    page.show()
                    page.raise_()
                return False
        return True

    def closeEvent(self, event) -> None:
        if not self.may_close():
            event.ignore()
            return
        for part in self.m_own_windows.values():  # a job of a window of its own ends at its next step
            getattr(part, "stop_work", lambda: None)()
        for page in self.m_pages.values():  # each app keeps its layout and lets go; the yes it gave is used here
            page.setProperty(widgets.CLOSE_AGREED, hasattr(page, "can_close"))
            is_closed = page.close()
            page.setProperty(widgets.CLOSE_AGREED, False)
            if not is_closed:
                shown = self.m_stack.currentWidget()
                if shown is not None:
                    shown.show()  # a page that agreed before this one refused was hidden by its close
                event.ignore()
                return
        for window in list(self.m_own_windows.values()):  # a window of its own may hold unsaved work (the translation)
            window.setProperty(widgets.CLOSE_AGREED, hasattr(window, "can_close"))
            is_closed = not window.isVisible() or window.close()
            window.setProperty(widgets.CLOSE_AGREED, False)
            if not is_closed:
                window.raise_()
                event.ignore()
                return
        hints = QGuiApplication.styleHints()
        if hints is not None and self._m_follow_system_scheme is not None:
            hints.colorSchemeChanged.disconnect(self._m_follow_system_scheme)
            self._m_follow_system_scheme = None  # a closed window no longer follows the system (nor is closed twice)
        for name, handler in self.m_provided:
            links.withdraw(name, handler)
        for remove in self._m_link_removers:
            remove()
        if self._m_link_search:
            self._m_link_search.stop()
        if self._m_handover:
            self._m_handover.stop()
        self.save_settings()
        super().closeEvent(event)


def first_apps(
    start: str | None, pages: Collection[str], windows: Collection[str], saved: str | None
) -> tuple[str, str | None]:
    """The page the window opens on and the app window opened over it (None: none) for ``--app start``: a page app
    is the page; an app with a window of its own opens over the page used last (else the first)."""
    page = start if start in pages else saved if saved in pages else next(iter(pages))
    return page, start if start in windows else None


def run(
    apps: list[SAppSpec],
    start: str | None = None,
    settings_path: Path | None = None,
    parts=None,
    deliveries=None,
    smoke: bool = False,
) -> int:
    """The program: the window on the app ``start`` (else the one last used, else the first; an app with a window of
    its own opens that over the last used one). ``smoke``: open every app once, say which started, close without
    saving the settings; 1 when one failed, 2 for an unknown ``start``."""
    if start and start not in (keys := [app.key for app in apps]):
        print(f"error: no app {start!r} (the apps: {', '.join(keys)})", file=sys.stderr)
        return 2
    splash.status(tr("common.preparing_graphics"))
    for app in apps:
        if app.prepare is not None:
            app.prepare()
    application = QApplication.instance() or QApplication([sys.argv[0]])
    application.setApplicationName(TITLE)
    application.setStyle("Fusion")  # the native style ignores parts of the dark palette
    path = settings_path or settings_file()
    splash.status(tr("common.reading_settings"))
    settings_existed = path.is_file()  # the setup page asks a person who never had one
    carried = migrate.carried_shortcuts(migrate.CWinRegistry())  # read before the old install's record is removed
    settings = CSettings.load(path)
    from ..render.nativelook import apply_hardware

    apply_hardware(settings.hardware)  # "Hardware acceleration": read when the window makes its 3D device
    from ..render import nativegpu

    nativegpu.want(settings.native_shaders)  # before the start-up stage: it follows the native phases
    later = None
    if not smoke:  # the game look's shaders, once per game build: what an island needs first (render.shaderstart)
        from ..render import shaderstart

        later = shaderstart.on_start(settings.anno117, settings.renderer, settings.hardware, splash.status,
                                     splash.detail)  # fmt: skip
    splash.status(tr("common.opening_window"))
    window = CSuiteWindow(apps, settings, path, parts, deliveries)
    if smoke:
        return smoke_test(application, window)
    saved = window.m_settings.apps.get("shell", {}).get("app")
    first, over = first_apps(start, window.m_apps, window.m_window_apps, saved)
    splash.status(tr("common.starting_app", app=tr(window.m_apps[first].title)))
    window.show_app(first)
    window.show()
    if over is not None:
        window.open_app_window(over)
    window.setup = setuppage.attach(window)
    linuxnotice.attach(window)
    if window.m_updates:
        window.m_updates.check_at_start()
    setuppage.first_start(window, settings_existed, carried)
    if later is not None:  # the model variants, in the background (render.shaderstart.CLater)
        later.start()  # its progress is the shader bar's (``shaderbar``)
        application.aboutToQuit.connect(later.stop)
    window.shader_cleanup = shadercleanup.attach(window, settings.anno117)  # compiles a native phase left unused
    return application.exec()


def smoke_test(application: QApplication, window: CSuiteWindow) -> int:
    """Every app's page made and shown once, the Project window opened; prints one line per app."""
    failed = []
    window.show()
    for key, app in window.m_apps.items():
        page = window.show_app(key)
        application.processEvents()
        started = page is not None and page.objectName() != FAILED
        print(
            f"{english(app.title)}: {'started' if started else page.text() if isinstance(page, QLabel) else 'no page'}"
        )
        if not started:
            failed.append(key)
    dialog = window.open_project()
    print(f"Project: {'opened' if dialog is not None else 'did not open'}")
    if dialog is not None:
        dialog.close()
    for page in window.m_pages.values():
        page.close()
    window.hide()
    window.deleteLater()
    return 1 if failed or dialog is None else 0
