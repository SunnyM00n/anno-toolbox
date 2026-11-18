"""The renderer's background loads: ``Loader`` asks the editor for a piece of data once (the ground, coverage,
game heights, water, the grade), on a worker thread, and answers None until it is in. No Qt, no GL.
"""

from __future__ import annotations

import os
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

# the island view's workers: its game shaders compile and read side by side, a compiler each (shaderview), the
# cores less two for the window and the models (island-117-view.md "Speed", part 14) [ours]
VIEW_WORKERS = max(2, min(6, (os.cpu_count() or 4) - 2))


class Loader:
    """Data the view asks the editor for once (ground, coverage, game heights, water, grade), each loaded in
    the background; ``get`` answers None until it is in."""

    def __init__(self, workers: int = 2) -> None:
        self.pool = ThreadPoolExecutor(workers, thread_name_prefix="view-data")
        self.futures: dict = {}
        self.changed = threading.Event()
        _live.add(self)

    def get(self, key: str, work):
        f = self.futures.get(key)
        if f is None:
            f = self.futures[key] = self.pool.submit(self._run, work)
        if not f.done():
            return None
        return f.result()

    def _run(self, work):
        try:
            return work()
        except Exception as exc:  # the switch stays off, the reason is shown
            return exc
        finally:
            self.changed.set()

    def forget(self, key) -> None:
        """Drop what was loaded under ``key``: the next ``get`` loads it again."""
        self.futures.pop(key, None)

    def forget_done(self, key) -> bool:
        """``forget`` once it is loaded: False while it still runs (left to finish: asked again, it would run
        twice)."""
        f = self.futures.get(key)
        if f is not None and not f.done():
            return False
        self.futures.pop(key, None)
        return True

    def pending(self) -> int:
        return sum(1 for f in self.futures.values() if not f.done())

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


_live: weakref.WeakSet[Loader] = weakref.WeakSet()  # every view's loader (``loading``)


def loading() -> bool:
    """Whether any view's loader has work in flight (an island being opened): the start-up stage's background part
    starts no new job meanwhile (``shaderstart.Later``)."""
    return any(loader.pending() for loader in list(_live))
