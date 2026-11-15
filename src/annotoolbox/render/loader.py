"""The renderer's background loads: ``Loader`` asks the editor for a piece of data once (the ground, coverage,
game heights, water, the grade), on a worker thread, and answers None until it is in. No Qt, no GL.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor


class Loader:
    """Data the view asks the editor for once (ground, coverage, game heights, water, grade), each loaded in
    the background; ``get`` answers None until it is in."""

    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(2, thread_name_prefix="view-data")
        self.futures: dict = {}
        self.changed = threading.Event()

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
