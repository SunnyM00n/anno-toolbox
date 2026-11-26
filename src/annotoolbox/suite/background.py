"""Work off the window's thread for every app of the suite: ``CBackground.run(work, callback)`` runs ``work`` on a
thread and calls ``callback`` with its result (or the exception it raised) back on the window's thread.
``CLatest(background)`` is the same for work of which only the newest result counts (a file picked while the one before
is still built). ``long_lived()`` wraps work that builds what the window keeps (an opened island). A job that takes long gets a
``CJobTalk``: its progress on the window's thread, and a stop that ends it at its next report (``CStopped``).
"""

from __future__ import annotations

import gc
import threading
import time
from contextlib import contextmanager

from PySide6.QtCore import QObject, Signal

_g_building = 0
_building_lock = threading.Lock()


@contextmanager
def long_lived():
    """Work that builds what the window keeps: the cycle collector waits while it runs, and what was built is left
    out of later collections. Each full collection over a continental island's millions of objects held the
    interpreter, and so the window, for up to 0.45 s (unloaded). ``release_long_lived`` hands it back."""
    global _g_building
    with _building_lock:
        _g_building += 1
        gc.disable()
    try:
        yield
    finally:
        with _building_lock:
            _g_building -= 1
            if not _g_building:
                gc.freeze()
                gc.enable()


def release_long_lived() -> None:
    """What ``long_lived`` kept out of the collections goes back in (an island closed: its cycles can go)."""
    gc.unfreeze()


class CStopped(Exception):
    """The person stopped the job: ``CJobTalk.say`` raises it on the job's thread."""


class CJobTalk(QObject):
    """What a long job and its window say to each other. The job calls ``say(text)`` from its thread: the text
    reaches the window's thread as ``said``, and once ``stop()`` was called the call raises ``CStopped`` instead, so
    every report is a place where the job can end. The job must leave nothing half done there."""

    said = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._m_stop = threading.Event()

    def say(self, text: str = "") -> None:
        if self._m_stop.is_set():
            raise CStopped
        if text:
            try:
                self.said.emit(text)
            except RuntimeError:  # the window closed while the job ran
                pass

    def stop(self) -> None:
        self._m_stop.set()

    @property
    def stopped(self) -> bool:
        return self._m_stop.is_set()


class CLatest:
    """Work of which only the newest result counts: a file picked while the one before is still being built. Every
    ``run`` supersedes the earlier ones, whose callbacks never come; ``cancel`` supersedes all of them. The work of a
    superseded job still runs to its end (a thread cannot be stopped), its result is dropped."""

    def __init__(self, background: CBackground) -> None:
        self._m_background = background
        self._m_serial = 0

    def run(self, work, callback, is_waiting: bool = False) -> None:
        """``background.run(work, callback)``, unless a newer ``run`` or ``cancel`` came first."""
        self._m_serial += 1
        serial = self._m_serial
        self._m_background.run(work, lambda result: serial == self._m_serial and callback(result), is_waiting)

    def cancel(self) -> None:
        """Whatever runs now is dropped when it ends."""
        self._m_serial += 1

    def is_current(self, serial: int) -> bool:
        """Whether ``serial`` (the ``serial`` property read when a job began) is still the newest job."""
        return serial == self._m_serial

    @property
    def serial(self) -> int:
        return self._m_serial


class CBackground(QObject):
    """Runs work off the window's thread and hands the result back on it."""

    done = Signal(object, object)  # callback, result (an exception when it failed)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.done.connect(lambda callback, result: callback(result))
        self._m_threads: list[threading.Thread] = []

    def run(self, work, callback, is_waiting: bool = False) -> None:
        """``work()`` off the window's thread, then ``callback(result)`` on it; the result is the exception when
        ``work`` raised. ``is_waiting``: run it here and call back at once (a test, or a caller that must have the result)."""

        def go():
            try:
                result = work()
            except Exception as exc:  # the window says why
                result = exc
            try:
                self.done.emit(callback, result)
            except RuntimeError:  # the window closed while it ran (a panel's paint): nobody to hand it to
                pass

        if is_waiting:
            go()
        else:
            thread = threading.Thread(target=go, daemon=True)
            self._m_threads = [earlier for earlier in self._m_threads if earlier.is_alive()] + [thread]
            thread.start()

    def busy(self) -> bool:
        """Whether work still runs (its callback is lost when the owner is deleted before it ends)."""
        return any(thread.is_alive() for thread in self._m_threads)

    def join(self, timeout_s: float = 10.0) -> None:
        """Wait for the running work, at most ``timeout_s`` seconds in all (a test; the callbacks still need the event
        loop)."""
        end = time.monotonic() + timeout_s
        for thread in list(self._m_threads):
            thread.join(max(0.0, end - time.monotonic()))
