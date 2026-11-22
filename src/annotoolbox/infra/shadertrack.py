"""What the game look's shader compiles are doing right now, for a progress display. No Qt: the compile sites report
from any thread, a listener (the Toolbox's status bar, ``suite.shaderbar``) is called on the reporting thread and
hands the state over to its own thread.

``compiling(label)`` wraps one compile; ``batch(done, total)`` is a part's progress (the start-up stage's jobs,
``render.shaderstart.run``; ``batch(0, 0)`` ends it); ``pause(True/False)`` holds back the jobs not yet started (``paused()``: the stage's
gate asks); ``snapshot()`` is the state now; ``on_change(listener)`` adds a listener and returns what removes it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

_lock = threading.Lock()
_g_active: dict[int, str] = {}  # the compiles in flight: token -> label
_g_batch = (0, 0)  # (done, total) of the part that runs
_g_pause = threading.Event()  # set: no new job of the start-up stage starts
_g_listeners: list[Callable[[SSnapshot], None]] = []
_g_next = 0


@dataclass(frozen=True)
class SSnapshot:
    """``active``: the labels of the compiles in flight, oldest first; ``done`` of ``total``: the running part."""

    active: tuple[str, ...] = ()
    done: int = 0
    total: int = 0
    paused: bool = False

    @property
    def busy(self) -> bool:
        return bool(self.active) or self.total > 0


def snapshot() -> SSnapshot:
    with _lock:
        return SSnapshot(tuple(_g_active.values()), *_g_batch, _g_pause.is_set())


def on_change(listener: Callable[[SSnapshot], None]) -> Callable[[], None]:
    """``listener(snapshot)`` after every change, on the thread that made it; returns the function that removes it."""
    with _lock:
        _g_listeners.append(listener)

    def remove() -> None:
        with _lock:
            if listener in _g_listeners:
                _g_listeners.remove(listener)

    return remove


def _changed() -> None:
    with _lock:
        now, listeners = SSnapshot(tuple(_g_active.values()), *_g_batch, _g_pause.is_set()), list(_g_listeners)
    for listener in listeners:
        try:
            listener(now)
        except Exception:  # a display must never fail a compile
            pass


@contextmanager
def compiling(label: str):
    """One compile in flight while the block runs (any thread)."""
    global _g_next
    with _lock:
        _g_next += 1
        token = _g_next
        _g_active[token] = label
    _changed()
    try:
        yield
    finally:
        with _lock:
            _g_active.pop(token, None)
        _changed()


def batch(done: int, total: int) -> None:
    """The running part's progress; ``batch(0, 0)`` when it ended."""
    global _g_batch
    with _lock:
        _g_batch = (done, total)
        if not total:
            _g_pause.clear()  # a pause belongs to the part it was made for
    _changed()


def paused() -> bool:
    return _g_pause.is_set()


def pause(flag: bool = True) -> None:
    """Hold back (or let go of) the jobs not yet started; a running one ends, a view's own compile is not held."""
    if flag:
        _g_pause.set()
    else:
        _g_pause.clear()
    _changed()
