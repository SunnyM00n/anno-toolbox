"""A test that hangs fails fast: after ``seconds`` the guard prints the stacks of every thread to the real stderr and
ends the process (exit code 3), where a plain run would block until somebody notices. ``arm`` returns the function that
disarms it. ``ANNO_TEST_TIMEOUT`` (seconds, default 300) sets the limit of one test; the guard is armed for every test
by ``conftest.py``."""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
from collections.abc import Callable

DEFAULT_SECONDS = 300
EXIT_CODE = 3


def seconds() -> float:
    try:
        return float(os.environ.get("ANNO_TEST_TIMEOUT", DEFAULT_SECONDS))
    except ValueError:
        return float(DEFAULT_SECONDS)


def arm(name: str, limit: float, before_exit: Callable[[], None] | None = None) -> Callable[[], None]:
    """Watch the test ``name``: past ``limit`` seconds, ``before_exit`` (say, give the terminal back from the output
    capture) runs, the stacks are printed and the process ends."""
    done = threading.Event()

    def watch() -> None:
        if done.wait(limit):
            return
        if before_exit is not None:
            before_exit()
        out = sys.__stderr__
        out.write(f"\n\nHANG: {name} ran over {limit:g} s (ANNO_TEST_TIMEOUT); the stacks of every thread:\n")
        out.flush()
        faulthandler.dump_traceback(file=out, all_threads=True)
        out.flush()
        os._exit(EXIT_CODE)

    threading.Thread(target=watch, name="hang-guard", daemon=True).start()
    return done.set
