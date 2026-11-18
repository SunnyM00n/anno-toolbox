"""The start-up screen: the program's name, its version and what is loading, until the first window shows.

``show()`` is the first thing the windowed program does after it knows Qt is installed, before the heavy imports (the
apps, the game trees), so the person sees something at once. ``status(text)`` names the next step, ``detail(text)``
fills the line under it (a long step's progress: the game shaders' start-up stage), ``close()`` ends it;
it also ends by itself when the first main window is shown, and ``toolbox.run_window`` closes it on an error, so an
error box is never hidden behind it. Nothing here imports another module of the suite or a game library.
"""

from __future__ import annotations

import json
import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QSplashScreen

from .. import __version__
from ..infra import i18n
from ..infra.i18n import tr
from ..paths import config_dir

NAME = "Anno Toolbox"  # the program's name (the window titles use it, ``shell.TITLE``)
SETTINGS_FILE = "editor-app.json"  # the suite's settings file; only its language is read here
SIZE = (520, 220)
PANEL, EDGE, TEXT, DIM, ACCENT = "#1f2329", "#3a424d", "#ececec", "#9aa4b1", "#4aa3df"

_screen: _Screen | None = None
_watch: _FirstWindow | None = None


class _FirstWindow(QObject):
    """Closes the splash when the first main window is shown (every way the program starts a window gets this)."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Show and isinstance(obj, QMainWindow) and obj.isWindow():
            QTimer.singleShot(0, close)
        return False


class _Screen(QSplashScreen):
    """The picture with the step being loaded written at its lower edge."""

    step = ""
    more = ""  # the step's own info line (``detail``)

    def drawContents(self, painter) -> None:
        painter.setPen(QColor(TEXT))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(36, SIZE[1] - 62, SIZE[0] - 60, 30, int(Qt.AlignmentFlag.AlignVCenter), self.step)
        if self.more:
            painter.setPen(QColor(DIM))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(36, SIZE[1] - 38, SIZE[0] - 60, 24, int(Qt.AlignmentFlag.AlignVCenter), self.more)


def _language() -> None:
    """The interface language of the settings, read without loading them (the full load looks for the games)."""
    try:
        text = (config_dir() / SETTINGS_FILE).read_text(encoding="utf-8-sig")  # as ``Settings`` reads it
        code = json.loads(text).get("language")
    except (OSError, ValueError, AttributeError):
        code = None
    i18n.use(code if isinstance(code, str) else None)


def _picture() -> QPixmap:
    pix = QPixmap(*SIZE)
    pix.fill(QColor(PANEL))
    p = QPainter(pix)
    p.setPen(QColor(EDGE))
    p.drawRect(0, 0, SIZE[0] - 1, SIZE[1] - 1)
    p.fillRect(0, 0, 6, SIZE[1], QColor(ACCENT))
    p.setPen(QColor(TEXT))
    p.setFont(QFont("Segoe UI", 28, QFont.Weight.DemiBold))
    p.drawText(34, 40, SIZE[0] - 60, 60, int(Qt.AlignmentFlag.AlignVCenter), NAME)
    p.setPen(QColor(DIM))
    p.setFont(QFont("Segoe UI", 10))
    p.drawText(
        36, 100, SIZE[0] - 60, 24, int(Qt.AlignmentFlag.AlignVCenter), tr("common.version_number", version=__version__)
    )
    p.end()
    return pix


def show() -> bool:
    """Open the splash (and the application object it needs); False, with no splash, where Qt has no screen to show
    it on. Call once, first, after PySide6 imported."""
    global _screen, _watch
    if _screen is not None:
        return True
    try:
        qt = QApplication.instance() or QApplication([sys.argv[0]])
        _language()
        _screen = _Screen(_picture(), Qt.WindowType.WindowStaysOnTopHint)
        _watch = _FirstWindow()
        qt.installEventFilter(_watch)
        _screen.show()
        status(tr("common.starting"))
    except Exception:  # a splash is a courtesy: the program starts without it
        _screen = None
        return False
    return True


def status(text: str) -> None:
    """Name what is loading now (drawn at once, though the main thread is about to be busy). No splash: nothing."""
    if _screen is None:
        return
    _screen.step, _screen.more = text, ""
    _redraw()


def detail(text: str) -> None:
    """The line under the step: what of it is being done now (drawn at once). No splash: nothing."""
    if _screen is None:
        return
    _screen.more = text
    _redraw()


def _redraw() -> None:
    _screen.repaint()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def close() -> None:
    """End the splash; safe to call again, and with none open."""
    global _screen, _watch
    screen, watch = _screen, _watch
    _screen = _watch = None
    app = QApplication.instance()
    if watch is not None and app is not None:
        app.removeEventFilter(watch)
    if screen is not None:
        screen.close()
        screen.deleteLater()
