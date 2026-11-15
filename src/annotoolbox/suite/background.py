"""Work off the window's thread for every app of the suite: ``Background.run(work, callback)`` runs ``work`` on a
thread and calls ``callback`` with its result (or the exception it raised) back on the window's thread.
``long_lived()`` wraps work that builds what the window keeps (an opened island).
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

    def join(self, timeout: float = 10.0) -> None:
        """Wait for the running work (a test; the callbacks still need the event loop)."""
        for thread in list(self._threads):
            thread.join(timeout)
