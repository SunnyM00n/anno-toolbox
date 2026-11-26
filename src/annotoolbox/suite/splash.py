"""The start-up screen: the program's name, its version and what is loading, until the first window shows.

``show()`` is the first thing the windowed program does after it knows Qt is installed, before the heavy imports (the
apps, the game trees), so the person sees something at once. ``status(text)`` names the next step, ``detail(text)``
fills the line under it, ``close()`` ends it; it also ends when the first main window is shown, and
``toolbox.run_window`` closes it on an error. ``updaterun`` uses it for the update: ``hold``, ``progress``, ``note``,
``offer`` (buttons). ``show(updated=...)`` adds "Updated to version X." after an update. Nothing here imports another module of the suite or a game library.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QSplashScreen

from .. import __version__
from ..infra import i18n
from ..infra.i18n import tr
from ..paths import config_dir

NAME = "Anno Toolbox"  # the program's name (the window titles use it, ``shell.TITLE``)
SETTINGS_FILE = "editor-app.json"  # the suite's settings file; only its language is read here
SIZE = (520, 270)
STEP_Y, MORE_Y, NOTE_Y, BAR_Y = 150, 178, 202, 138
PANEL, EDGE, TEXT, DIM, ACCENT = "#1f2329", "#3a424d", "#ececec", "#9aa4b1", "#4aa3df"

_g_screen: _CScreen | None = None
_g_watch: _CFirstWindow | None = None


class _CFirstWindow(QObject):
    """Closes the splash when the first main window is shown (every way the program starts a window gets this)."""

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(watched, QMainWindow) and watched.isWindow():
            QTimer.singleShot(0, close)
        return False


class _CScreen(QSplashScreen):
    """The picture with the step being loaded written at its lower edge."""

    m_step = ""
    m_more = ""  # the step's own info line (``detail``)
    m_line = ""  # a third line (``note``)
    m_fraction: float | None = None  # a bar under the version, 0..1 (``progress``)
    m_buttons: list[QPushButton] = []

    def drawContents(self, painter) -> None:
        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(36, STEP_Y, SIZE[0] - 60, 26, int(Qt.AlignmentFlag.AlignVCenter), self.m_step)
        painter.setPen(QColor(DIM))
        painter.setFont(QFont("Segoe UI", 9))
        if self.m_more:
            painter.drawText(36, MORE_Y, SIZE[0] - 60, 22, int(Qt.AlignmentFlag.AlignVCenter), self.m_more)
        if self.m_line:
            painter.drawText(36, NOTE_Y, SIZE[0] - 60, 22, int(Qt.AlignmentFlag.AlignVCenter), self.m_line)
        if self.m_fraction is not None:
            width = SIZE[0] - 72
            painter.fillRect(36, BAR_Y, width, 5, QColor(EDGE))
            painter.fillRect(36, BAR_Y, round(width * min(max(self.m_fraction, 0.0), 1.0)), 5, QColor(ACCENT))

    def mousePressEvent(self, event) -> None:
        """A splash hides on a click by default; this one ends only through ``close``."""
        event.ignore()


def _language() -> None:
    """The interface language of the settings, read without loading them (the full load looks for the games)."""
    try:
        text = (config_dir() / SETTINGS_FILE).read_text(encoding="utf-8-sig")  # as ``Settings`` reads it
        code = json.loads(text).get("language")
    except (OSError, ValueError, AttributeError):
        code = None
    i18n.use(code if isinstance(code, str) else None)


def _picture(updated: str = "") -> QPixmap:
    pixmap = QPixmap(*SIZE)
    pixmap.fill(QColor(PANEL))
    painter = QPainter(pixmap)
    painter.setPen(QColor(EDGE))
    painter.drawRect(0, 0, SIZE[0] - 1, SIZE[1] - 1)
    painter.fillRect(0, 0, 6, SIZE[1], QColor(ACCENT))
    painter.setPen(QColor(TEXT))
    painter.setFont(QFont("Segoe UI", 28, QFont.Weight.DemiBold))
    painter.drawText(34, 40, SIZE[0] - 60, 60, int(Qt.AlignmentFlag.AlignVCenter), NAME)
    painter.setPen(QColor(DIM))
    painter.setFont(QFont("Segoe UI", 10))
    painter.drawText(
        36, 100, SIZE[0] - 60, 24, int(Qt.AlignmentFlag.AlignVCenter), tr("common.version_number", version=__version__)
    )
    if updated:
        painter.setPen(QColor(ACCENT))
        done = tr("common.update_done", version=updated)
        painter.drawText(36, 118, SIZE[0] - 60, 24, int(Qt.AlignmentFlag.AlignVCenter), done)
    painter.end()
    return pixmap


def show(updated: str = "") -> bool:
    """Open the splash (and the application object it needs); False, with no splash, where Qt has no screen to show
    it on. ``updated`` is the version the program was just updated to, "" for a normal start. Call once, first, after
    PySide6 imported."""
    global _g_screen, _g_watch
    if _g_screen is not None:
        return True
    try:
        application = QApplication.instance() or QApplication([sys.argv[0]])
        _language()
        _g_screen = _CScreen(_picture(updated), Qt.WindowType.WindowStaysOnTopHint)
        _g_watch = _CFirstWindow()
        application.installEventFilter(_g_watch)
        _g_screen.show()
        status(tr("common.starting"))
    except Exception:  # a splash is a courtesy: the program starts without it
        _g_screen = None
        return False
    return True


def status(text: str) -> None:
    """Name what is loading now (drawn at once, though the main thread is about to be busy). It clears the lines, the
    bar and the buttons of the step before. No splash: nothing."""
    if _g_screen is None:
        return
    _g_screen.m_step, _g_screen.m_more, _g_screen.m_line, _g_screen.m_fraction = text, "", "", None
    _clear_buttons()
    _redraw()


def detail(text: str) -> None:
    """The line under the step: what of it is being done now (drawn at once). No splash: nothing."""
    if _g_screen is None:
        return
    _g_screen.m_more = text
    _redraw()


def note(text: str) -> None:
    """A third line, under ``detail``. No splash: nothing."""
    if _g_screen is None:
        return
    _g_screen.m_line = text
    _redraw()


def progress(fraction: float | None) -> None:
    """The bar, 0..1; None takes it away. No splash: nothing."""
    if _g_screen is None:
        return
    _g_screen.m_fraction = fraction
    _redraw()


def offer(choices: list[tuple[str, Callable[[], None]]]) -> None:
    """Buttons at the lower edge, one per ``(label, action)``; the next ``status`` takes them away. No splash: nothing."""
    if _g_screen is None:
        return
    _clear_buttons()
    x = SIZE[0] - 24
    for label, action in reversed(choices):
        button = QPushButton(label, _g_screen)
        button.adjustSize()
        x -= button.width() + 8
        button.move(x, SIZE[1] - 40)
        button.clicked.connect(lambda _c=False, run=action: run())
        button.show()
        _g_screen.m_buttons.append(button)
    _redraw()


def hold() -> None:
    """Keep the splash open when other windows show (the update's doing has no first main window to wait for)."""
    global _g_watch
    watch, _g_watch = _g_watch, None
    app = QApplication.instance()
    if watch is not None and app is not None:
        app.removeEventFilter(watch)


def _clear_buttons() -> None:
    for button in _g_screen.m_buttons:
        button.deleteLater()
    _g_screen.m_buttons = []


def _redraw() -> None:
    _g_screen.repaint()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def close() -> None:
    """End the splash; safe to call again, and with none open."""
    global _g_screen, _g_watch
    screen, watch = _g_screen, _g_watch
    _g_screen = _g_watch = None
    app = QApplication.instance()
    if watch is not None and app is not None:
        app.removeEventFilter(watch)
    if screen is not None:
        screen.close()
        screen.deleteLater()
