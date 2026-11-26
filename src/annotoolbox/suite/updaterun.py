"""The update's doing, in the launcher window (``splash``): checking, downloading, installing, and a failure.

``begin(release)`` is what "Download and install" starts, after the main window closed the usual way: the launcher
window shows the step, the download's percent and, on a failure, the error with the log's path and a button that starts
the Toolbox again. The work runs on a thread of its own (``updates.stage``, then ``updates.apply``, which starts the
installer's updater silently; this program then ends so the updater can swap the files and start the new version,
whose own start-up screen says the update is done). The wording of each state is ``lines``,
a plain function. ``docs/installer.md`` "Updates".
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from ..infra.i18n import tr
from ..infra.tools import NO_WINDOW
from . import install, splash, updates
from .updates import SRelease

log = logging.getLogger(__name__)
CHECK, DOWNLOAD, APPLY, FAILED = "check", "download", "apply", "failed"
_g_current: CUpdateRun | None = None  # the run that is going (nothing else holds it)


@dataclass(frozen=True)
class SStep:
    state: str
    percent: int = -1  # the download's, -1 while it is not known
    error: str = ""  # FAILED: what the updater said
    applying: bool = False  # FAILED: it was the install that failed, not the check or the download
    log: str = ""  # FAILED: the log file's path, "" when none could be written


def lines(step: SStep, version: str) -> tuple[str, str, str]:
    """The launcher window's three lines (the step, its detail, a note) for one state."""
    if step.state == CHECK:
        return tr("common.update_checking", version=version), "", ""
    if step.state == DOWNLOAD:
        text = tr("common.downloading_version_percent", version=version, percent=step.percent)
        return text if step.percent >= 0 else tr("common.downloading_version", version=version), "", ""
    if step.state == APPLY:
        return tr("common.update_applying", version=version), tr("common.update_applying_restart"), ""
    if step.applying:
        failure = tr("common.update_could_not_started", error=step.error)
    else:
        failure = tr("common.download_failed_error", error=step.error)
    where = tr("common.update_log_at", path=step.log) if step.log else tr("common.update_no_log")
    return tr("common.update_failed"), failure.splitlines()[0] if failure else "", where


def fraction(step: SStep) -> float | None:
    """The bar: the download's share, full while the install runs, none otherwise."""
    if step.state == DOWNLOAD and step.percent >= 0:
        return step.percent / 100
    return 1.0 if step.state == APPLY else None


def relaunch_command(argv: list[str], executable: str, appimage: Path | None) -> list[str]:
    """The command that starts the program again as it was started (the AppImage file on Linux, else the program)."""
    return [str(appimage) if appimage else executable, *argv[1:]]


def keep_log(exc: BaseException) -> str:
    """The failure into the error logs (``toolbox.write_error_log``); its path, "" when it could not be written."""
    from .toolbox import write_error_log

    try:
        path = write_error_log(None, type(exc), exc, exc.__traceback__)
    except OSError:  # a full disk must not hide the failure window
        return ""
    return str(path) if path else ""


def leave_program() -> None:
    """End the program at once, as the updater waits for it to exit before it swaps the files."""
    logging.shutdown()
    os._exit(0)


def start_again(command: list[str] | None = None) -> None:
    """Start a normal run of the program, apart from this one (a failed update leaves the person without a window)."""
    command = command or relaunch_command(sys.argv, sys.executable, install.appimage())
    extra = {"start_new_session": True} if sys.platform != "win32" else NO_WINDOW
    try:
        subprocess.Popen(command, close_fds=True, **extra)
    except OSError as exc:
        log.warning("could not start the program again: %s", exc)  # i18n: ok


class CUpdateRun(QObject):
    """One update: the thread's steps come to the main thread as ``stepped`` and are drawn on the launcher window."""

    stepped = Signal(object)
    left = Signal()

    def __init__(
        self,
        release: SRelease,
        net: updates.SNet = updates.NET,
        mgr=None,
        keep: Callable[[BaseException], str] = keep_log,
        leave: Callable[[], None] = leave_program,
    ) -> None:
        super().__init__()
        self.m_release, self.m_net, self.m_mgr, self.m_keep, self.m_leave = release, net, mgr, keep, leave
        self.m_step = SStep(CHECK)
        self.m_thread: threading.Thread | None = None  # a test waits for it
        self.stepped.connect(self._show)
        self.left.connect(self._leave)

    def start(self) -> None:
        splash.show()
        splash.hold()
        self._show(self.m_step)
        self.m_thread = threading.Thread(target=self._work, name="update-run", daemon=True)
        self.m_thread.start()

    def _tell(self, step: SStep) -> None:
        try:
            self.stepped.emit(step)
        except RuntimeError:  # the object is gone: no one to tell
            log.info("the update's step came after its window closed: %s", step.state)

    def _work(self) -> None:
        applying = False
        try:
            mgr = self.m_mgr or updates.manager()
            staged = updates.stage(
                self.m_release, mgr, self.m_net, lambda percent: self._tell(SStep(DOWNLOAD, int(percent)))
            )
            applying = True
            self._tell(SStep(APPLY))
            updates.apply(staged, mgr)  # the silent updater now waits for this program to exit
            self.left.emit()
        except Exception as exc:  # the launcher window always hears back
            log.exception("the update failed")
            error = str(exc) or type(exc).__name__
            self._tell(SStep(FAILED, error=error, applying=applying, log=self.m_keep(exc)))

    def _leave(self) -> None:
        self.m_leave()  # the launcher window stays up until the program is gone

    def _show(self, step: SStep) -> None:
        self.m_step = step
        text, more, line = lines(step, self.m_release.version)
        splash.status(text)
        splash.detail(more)
        splash.note(line)
        splash.progress(fraction(step))
        if step.state == FAILED:
            splash.offer([(tr("common.update_start_toolbox"), self._start_again), (tr("common.close"), self._end)])

    def _start_again(self) -> None:
        start_again()
        self._end()

    @staticmethod
    def _end() -> None:
        global _g_current
        _g_current = None
        splash.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()


def begin(release: SRelease, net: updates.SNet = updates.NET, mgr=None) -> CUpdateRun:
    """Open the launcher window on the update and start it; the run is kept until it ends."""
    global _g_current
    app = QApplication.instance()
    if app is not None:
        app.setQuitOnLastWindowClosed(False)  # the main window is closed already: the launcher window is the program
    _g_current = CUpdateRun(release, net, mgr)
    _g_current.start()
    return _g_current
