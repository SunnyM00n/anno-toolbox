"""The suite's one window: an app switcher down the left side and the open app beside it, on the shared settings
and theme. An app is an ``AppSpec`` whose page is made the first time it is shown (its modules load then), so the
window opens as fast as its first app; every app keeps its own command as well (``desktop`` for the islands).

``SuiteWindow(apps, settings, path, parts, deliveries)`` is the window, ``run(apps, start)`` the program (``smoke``: every app
opened once and closed, for a check); ``suite.apps`` lists the apps and their parts of a project's mod (``Project``,
under the switcher). Plan: ``docs/tool-suite.md`` section 7, C1 and C4.
"""

from __future__ import annotations

import sys
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
    gamesync,
    icons,
    linkclient,
    linkhandover,
    linkroute,
    links,
    livelink,
    migrate,
    setuppage,
    shaderbar,
    shadercleanup,
    splash,
    theme,
    updatenotice,
)
from .extspecs import AppSpec
from .settings import Settings, settings_file

TITLE = splash.NAME  # the suite's name (owner, 2026-11-09)
FAILED = "appFailed"  # the object name of the page shown for an app that could not start
LAST_DIGIT = 9  # the apps that get a Ctrl+digit shortcut


def app_shortcut(n: int) -> str:
    """The key of the n-th app (from 1): Ctrl+1 to Ctrl+9, the tenth Ctrl+0, the next ones Ctrl+Shift+1 to 9; "" past
    those ("Ctrl+10" is no key: Qt made an unknown one of it). ``docs/suite.md`` "Keys of the suite"."""
    if n <= LAST_DIGIT:
        return f"Ctrl+{n}"
    if n == LAST_DIGIT + 1:
        return "Ctrl+0"
    return f"Ctrl+Shift+{n - LAST_DIGIT - 1}" if n <= 2 * LAST_DIGIT + 1 else ""


class SuiteWindow(QMainWindow):
    """The window: the switcher (a tool bar of the apps) and a stack with one page per app shown so far."""

    link_status = Signal(object)  # the game link's status changed (from any thread)
    link_event = Signal(object, object)  # (handler, payload) of a game event, to run on this thread
    game_changed = Signal(str)  # the game every app is on changed (``gamesync``; suite.md "One game for every app")

    def __init__(
        self, apps: list[AppSpec], settings: Settings, settings_path: Path, parts=None, deliveries=None
    ) -> None:
        super().__init__()
        self.apps = {a.key: a for a in apps}
        self.settings, self.settings_path = settings, settings_path
        self.deliveries = dict(deliveries or {})  # how the older games take a project (``suite.project.Delivery``)
        self.parts = list(parts or [])  # the apps' shares of a project's mod (``suite.project.Part``)
        self.pages: dict[str, QWidget] = {}
        self.own_windows: dict[str, QWidget] = {}  # the palette, the link panel and Project, while each is kept
        self.current: str | None = None
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.switch_actions: dict[str, QAction] = {}
        self._make_switcher(apps)
        self.resize(1600, 980)
        self.apply_theme()
        self._follow_system_scheme = lambda _s: self.settings.theme == "system" and self.apply_theme()
        hints = QGuiApplication.styleHints()
        if hints is not None:
            hints.colorSchemeChanged.connect(self._follow_system_scheme)
        self.provided = self._provide_links()
        self._attach_game_link()
        self.updates = updatenotice.attach(self)  # None in a build without a release page
        self.game_sync = gamesync.GameSync(self)

    def _make_switcher(self, apps: list[AppSpec]) -> None:
        """The tool bar down the left side: an action per app (a key each, ``app_shortcut``), then Project."""
        self.switcher = QToolBar(tr("common.apps"), self, movable=False, floatable=False, objectName="appSwitcher")
        self.switcher.setIconSize(QSize(32, 32))
        self.switcher.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, self.switcher)
        group = QActionGroup(self, exclusive=True)
        for n, app in enumerate(apps, 1):
            key = app_shortcut(n)
            tip = f"{tr(app.summary)} ({key})" if key else tr(app.summary)
            act = QAction(icons.icon(app.icon), tr(app.title), self, checkable=True, toolTip=tip)
            if key:
                act.setShortcut(QKeySequence(key))
            act.triggered.connect(lambda _on=False, key=app.key: self.show_app(key))
            group.addAction(act)
            self.switcher.addAction(act)
            self.switch_actions[app.key] = act
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.switcher.addWidget(spacer)
        self.project_action = QAction(
            icons.icon("project"),
            tr("common.project"),
            self,
            toolTip=tr("common.tip.open_project_mod_every_app"),
        )
        self.project_action.triggered.connect(lambda _c=False: self.open_project())
        self.switcher.addAction(self.project_action)

    def _provide_links(self) -> list[tuple[str, object]]:
        """Every app's links (``AppSpec.links``) answered by this window: for an action the app's page made and brought
        to the front; a quiet link (a query) gets the page only when it is made already, else None."""
        out = []
        for app in self.apps.values():
            for name, handler in app.links.items():
                quiet = name in app.quiet_links

                def follow(*args, key=app.key, handler=handler, quiet=quiet):
                    if quiet:
                        return handler(self.pages.get(key), *args)
                    page = self.show_app(key)
                    return None if page is None or page.objectName() == FAILED else handler(page, *args)

                links.provide(name, follow)
                out.append((name, follow))
        return out

    def _attach_game_link(self) -> None:
        """The status indicator of the live link and the game to editor routing (``livelink``, ``linkroute``)."""
        self.shader_bar = shaderbar.attach(self)  # the game look's shader compiles, left of the link
        self.link_label = QPushButton(flat=True)  # the status; a click opens the game link panel
        self.link_label.clicked.connect(lambda _c=False: self.open_link_panel())
        self.statusBar().addPermanentWidget(self.link_label)
        commands = QPushButton(tr("common.debug_commands"), flat=True)
        commands.setToolTip(tr("common.tip.games_debug_commands_sent_over"))
        commands.clicked.connect(lambda _c=False: self.open_palette())
        self.statusBar().addPermanentWidget(commands)
        self.link_status.connect(lambda status: self.link_label.setText(status.text))
        self.link_event.connect(lambda handler, payload: handler(payload))
        self.link_label.setToolTip(tr("common.tip.live_link_running_game_click"))
        self._link_removers = [
            livelink.on("status", self.link_status.emit),
            linkroute.attach(lambda handler, payload: self.link_event.emit(handler, payload)),
        ]
        self.link_label.setText(livelink.current().status().text)
        if not linkclient.supported():  # the link is Windows only: say so where the status is
            self.link_label.setText(tr("common.live_link_windows_only"))
            self.link_label.setToolTip(tr("common.live_link_windows_only_why"))
        self._link_search = linkclient.autoconnect()  # finds a game that starts later, and one that comes back
        self._handover = linkhandover.Watcher().start() if self._link_search else None  # Anno 1800's open in editor

    def open_palette(self):
        """The debug-command palette (``linkpalette``); its history and favourites live in the settings."""
        from .linkpalette import PaletteWindow

        state = self.settings.apps.setdefault("palette", {})
        # a save that fails is said, not raised
        window = self._own_window("palette", lambda: PaletteWindow(self, state, self.save_settings))
        self.debug_palette = window  # not ``palette``: that is the window's own colours (QWidget.palette)
        return window

    def open_link_panel(self):
        """The game link panel (``linkpanel``); returns it."""
        from .linkpanel import LinkDialog

        self.link_panel = self._own_window("link", lambda: LinkDialog(self))
        return self.link_panel

    def _own_window(self, key: str, make, show: bool = True):
        """The window ``key``: the open one brought to the front, else a new one from ``make`` (shown, or left to the
        caller). The closed one before it is deleted (it would stay, hidden, a child of this window) unless its work
        still runs."""
        old = self.own_windows.pop(key, None)
        if old is not None and old.isVisible():
            self.own_windows[key] = old
            old.raise_()
            old.activateWindow()
            return old
        if old is not None and not old.background.busy():
            old.deleteLater()
        window = self.own_windows[key] = make()
        if show:
            window.show()
        return window

    def apply_theme(self) -> None:
        theme.apply(self, self.settings.theme)
        icons.set_foreground(theme.colors()["icon"])
        for key, act in self.switch_actions.items():
            act.setIcon(icons.icon(self.apps[key].icon))
        if hasattr(self, "project_action"):
            self.project_action.setIcon(icons.icon("project"))

    def open_project(self):
        """The Project window (``projectpanel``); returns it."""
        from .projectpanel import ProjectDialog

        try:
            dialog = self._own_window("project", lambda: ProjectDialog(self, self.parts), show=False)
        except ValueError as exc:  # a project file that does not read
            QMessageBox.warning(self, TITLE, str(exc))
            return None
        theme.apply(dialog, self.settings.theme)  # a window of its own: the palette is not inherited
        dialog.show()
        return dialog

    def show_app(self, key: str) -> QWidget | None:
        """Bring the app ``key`` to the front, making its page on its first showing."""
        if self.page_of(key) is None:
            return None
        self.current = key
        self.stack.setCurrentWidget(self.pages[key])
        self.switch_actions[key].setChecked(True)
        self._title(key)
        return self.pages[key]

    def page_of(self, key: str) -> QWidget | None:
        """The page of the app ``key``, made (behind the current one) when it is not yet."""
        if key not in self.apps:
            return None
        if key not in self.pages:
            self.game_sync.starting(key)  # it starts on the suite's game when it can show it
            try:
                page = self.apps[key].make(self)
            except Exception as exc:  # the reason in the window, the other apps stay usable
                shown = f"{type(exc).__name__}: {exc}"
                page = QLabel(tr("common.could_not_start", title=tr(self.apps[key].title), error=shown))
                page.setAlignment(Qt.AlignmentFlag.AlignCenter)
                page.setObjectName(FAILED)
            if isinstance(page, QMainWindow):
                page.setWindowFlags(Qt.WindowType.Widget)  # an app's own window, held as a page of this one
            page.windowTitleChanged.connect(lambda _t, k=key: self._title(k))
            self.pages[key] = page
            self.stack.addWidget(page)
        return self.pages[key]

    def app_pages(self) -> list[tuple[str, QWidget]]:
        """The apps that are started: (key, page), without the ones that could not start."""
        return [(key, page) for key, page in self.pages.items() if page.objectName() != FAILED]

    def select_game(self, key: str) -> bool:
        """Send every app to the game ``key`` (each asks about its unsaved work first); False when one of the
        questions was cancelled: no app switched."""
        return self.game_sync.choose(key)

    def view_file(self, game: str, path: str) -> bool:
        """Show a game file (``game``: ``anno117`` ...) in the Viewer app (``links.VIEW_FILE``); False without one."""
        return links.view_file(game, path)

    def _title(self, key: str) -> None:
        if key == self.current:
            own = self.pages[key].windowTitle()
            self.setWindowTitle(f"{own} · {TITLE}" if own else f"{tr(self.apps[key].title)} · {TITLE}")

    def save_settings(self) -> None:
        self.settings.apps["shell"] = {"app": self.current}
        try:
            self.settings.save(self.settings_path)
        except OSError as exc:
            QMessageBox.warning(self, TITLE, tr("common.could_not_save_settings", error=exc))

    def closeEvent(self, event) -> None:
        for page in self.pages.values():  # each app asks about its unsaved work and keeps its layout
            if not page.close():
                shown = self.stack.currentWidget()
                if shown is not None:
                    shown.show()  # a page that agreed before this one refused was hidden by its close
                event.ignore()
                return
        hints = QGuiApplication.styleHints()
        if hints is not None and self._follow_system_scheme is not None:
            hints.colorSchemeChanged.disconnect(self._follow_system_scheme)
            self._follow_system_scheme = None  # a closed window no longer follows the system (nor is closed twice)
        for name, handler in self.provided:
            links.withdraw(name, handler)
        for remove in self._link_removers:
            remove()
        if self._link_search:
            self._link_search.stop()
        if self._handover:
            self._handover.stop()
        self.save_settings()
        super().closeEvent(event)


def run(
    apps: list[AppSpec],
    start: str | None = None,
    settings_path: Path | None = None,
    parts=None,
    deliveries=None,
    smoke: bool = False,
) -> int:
    """The program: the window on the app ``start`` (else the one last used, else the first). ``smoke``: open every
    app once, say which started, close without saving the settings; 1 when one failed, 2 for an unknown ``start``."""
    if start and start not in (keys := [app.key for app in apps]):
        print(f"error: no app {start!r} (the apps: {', '.join(keys)})", file=sys.stderr)
        return 2
    splash.status(tr("common.preparing_graphics"))
    for app in apps:
        if app.prepare is not None:
            app.prepare()
    qt = QApplication.instance() or QApplication([sys.argv[0]])
    qt.setApplicationName(TITLE)
    qt.setStyle("Fusion")  # the native style ignores parts of the dark palette
    path = settings_path or settings_file()
    splash.status(tr("common.reading_settings"))
    settings_existed = path.is_file()  # the setup page asks a person who never had one
    carried = migrate.carried_shortcuts(migrate.WinRegistry())  # read before the old install's record is removed
    settings = Settings.load(path)
    from ..render.nativelook import apply_hardware

    apply_hardware(settings.hardware)  # "Hardware acceleration": read when the window makes its 3D device
    later = None
    if not smoke:  # the game look's shaders, once per game build: what an island needs first (render.shaderstart)
        from ..render import shaderstart

        later = shaderstart.on_start(settings.anno117, settings.renderer, settings.hardware, splash.status,
                                     splash.detail)  # fmt: skip
    splash.status(tr("common.opening_window"))
    window = SuiteWindow(apps, settings, path, parts, deliveries)
    if smoke:
        return smoke_test(qt, window)
    first = start or window.settings.apps.get("shell", {}).get("app")
    first = first if first in window.apps else apps[0].key
    splash.status(tr("common.starting_app", app=tr(window.apps[first].title)))
    window.show_app(first)
    window.show()
    window.setup = setuppage.attach(window)
    if window.updates:
        window.updates.check_at_start()
    setuppage.first_start(window, settings_existed, carried)
    if later is not None:  # the model variants, in the background (render.shaderstart.Later)
        later.start()  # its progress is the shader bar's (``shaderbar``)
        qt.aboutToQuit.connect(later.stop)
    window.shader_cleanup = shadercleanup.attach(window, settings.anno117)  # compiles a native phase left unused
    return qt.exec()


def smoke_test(qt: QApplication, window: SuiteWindow) -> int:
    """Every app's page made and shown once, the Project window opened; prints one line per app."""
    failed = []
    window.show()
    for key, app in window.apps.items():
        page = window.show_app(key)
        qt.processEvents()
        ok = page is not None and page.objectName() != FAILED
        print(f"{english(app.title)}: {'started' if ok else page.text() if isinstance(page, QLabel) else 'no page'}")
        if not ok:
            failed.append(key)
    dialog = window.open_project()
    print(f"Project: {'opened' if dialog is not None else 'did not open'}")
    if dialog is not None:
        dialog.close()
    for page in window.pages.values():
        page.close()
    window.hide()
    window.deleteLater()
    return 1 if failed or dialog is None else 0
