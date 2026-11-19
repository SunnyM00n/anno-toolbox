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
_active: dict[int, str] = {}  # the compiles in flight: token -> label
_batch = (0, 0)  # (done, total) of the part that runs
_pause = threading.Event()  # set: no new job of the start-up stage starts
_listeners: list[Callable[[Snapshot], None]] = []
_next = 0


@dataclass(frozen=True)
class Snapshot:
    """``active``: the labels of the compiles in flight, oldest first; ``done`` of ``total``: the running part."""

    active: tuple[str, ...] = ()
    done: int = 0
    total: int = 0
    paused: bool = False

    @property
    def busy(self) -> bool:
        return bool(self.active) or self.total > 0


def snapshot() -> Snapshot:
    with _lock:
        return Snapshot(tuple(_active.values()), *_batch, _pause.is_set())


def on_change(listener: Callable[[Snapshot], None]) -> Callable[[], None]:
    """``listener(snapshot)`` after every change, on the thread that made it; returns the function that removes it."""
    with _lock:
        _listeners.append(listener)

    def remove() -> None:
        with _lock:
            if listener in _listeners:
                _listeners.remove(listener)

    return remove


def _changed() -> None:
    with _lock:
        now, listeners = Snapshot(tuple(_active.values()), *_batch, _pause.is_set()), list(_listeners)
    for listener in listeners:
        try:
            listener(now)
        except Exception:  # a display must never fail a compile
            pass


@contextmanager
def compiling(label: str):
    """One compile in flight while the block runs (any thread)."""
    global _next
    with _lock:
        _next += 1
        token = _next
        _active[token] = label
    _changed()
    try:
        yield
    finally:
        with _lock:
            _active.pop(token, None)
        _changed()


def batch(done: int, total: int) -> None:
    """The running part's progress; ``batch(0, 0)`` when it ended."""
    global _batch
    with _lock:
        _batch = (done, total)
        if not total:
            _pause.clear()  # a pause belongs to the part it was made for
    _changed()


def paused() -> bool:
    return _pause.is_set()


def pause(flag: bool = True) -> None:
    """Hold back (or let go of) the jobs not yet started; a running one ends, a view's own compile is not held."""
    if flag:
        _pause.set()
    else:
        _pause.clear()
    _changed()
