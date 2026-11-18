"""Work off the window's thread for every app of the suite: ``Background.run(work, callback)`` runs ``work`` on a
thread and calls ``callback`` with its result (or the exception it raised) back on the window's thread.
``long_lived()`` wraps work that builds what the window keeps (an opened island). A job that takes long gets a
``JobTalk``: its progress on the window's thread, and a stop that ends it at its next report (``Stopped``).
"""

from __future__ import annotations

import gc
import threading
from contextlib import contextmanager

from PySide6.QtCore import QObject, Signal

_building = 0
_building_lock = threading.Lock()


@contextmanager
def long_lived():
    """Work that builds what the window keeps: the cycle collector waits while it runs, and what was built is left
    out of later collections. Each full collection over a continental island's millions of objects held the
    interpreter, and so the window, for up to 0.45 s (unloaded). ``release_long_lived`` hands it back."""
    global _building
    with _building_lock:
        _building += 1
        gc.disable()
    try:
        yield
    finally:
        with _building_lock:
            _building -= 1
            if not _building:
                gc.freeze()
                gc.enable()


def release_long_lived() -> None:
    """What ``long_lived`` kept out of the collections goes back in (an island closed: its cycles can go)."""
    gc.unfreeze()


class Stopped(Exception):
    """The person stopped the job: ``JobTalk.say`` raises it on the job's thread."""


class JobTalk(QObject):
    """What a long job and its window say to each other. The job calls ``say(text)`` from its thread: the text
    reaches the window's thread as ``said``, and once ``stop()`` was called the call raises ``Stopped`` instead, so
    every report is a place where the job can end. The job must leave nothing half done there."""

    said = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop = threading.Event()

    def say(self, text: str = "") -> None:
        if self._stop.is_set():
            raise Stopped
        if text:
            try:
                self.said.emit(text)
            except RuntimeError:  # the window closed while the job ran
                pass

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()


class Background(QObject):
    """Runs work off the window's thread and hands the result back on it."""

    done = Signal(object, object)  # callback, result (an exception when it failed)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.done.connect(lambda cb, result: cb(result))
        self._threads: list[threading.Thread] = []

    def run(self, work, callback, wait: bool = False) -> None:
        """``work()`` off the window's thread, then ``callback(result)`` on it; the result is the exception when
        ``work`` raised. ``wait``: run it here and call back at once (a test, or a caller that must have the result)."""

        def go():
            try:
                result = work()
            except Exception as exc:  # the window says why
                result = exc
            try:
                self.done.emit(callback, result)
            except RuntimeError:  # the window closed while it ran (a panel's paint): nobody to hand it to
                pass

        if wait:
            go()
        else:
            thread = threading.Thread(target=go, daemon=True)
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
            thread.start()

    def busy(self) -> bool:
        """Whether work still runs (its callback is lost when the owner is deleted before it ends)."""
        return any(t.is_alive() for t in self._threads)

    def join(self, timeout: float = 10.0) -> None:
        """Wait for the running work (a test; the callbacks still need the event loop)."""
        for thread in list(self._threads):
            thread.join(timeout)
