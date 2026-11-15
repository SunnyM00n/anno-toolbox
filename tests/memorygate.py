"""A test starts only when the machine has commit to spare: below ``ANNO_TEST_MIN_FREE_MB`` (default 3000) of free
commit the gate waits, up to ``ANNO_TEST_MEMORY_WAIT`` seconds (default 120), for other processes to give some back.
On Windows a read, a file write or a graphics driver that cannot get memory fails as ``Errno 22`` or ends the worker
(CONTRIBUTING "Parallel runs"); elsewhere the gate is open. ``conftest.py`` waits at it before every test."""

from __future__ import annotations

import ctypes
import os
import sys
import time

DEFAULT_MB = 3000
DEFAULT_WAIT = 120.0


class _Status(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("load", ctypes.c_ulong),
        ("total_phys", ctypes.c_ulonglong),
        ("avail_phys", ctypes.c_ulonglong),
        ("total_commit", ctypes.c_ulonglong),
        ("avail_commit", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("avail_virtual", ctypes.c_ulonglong),
        ("avail_extended", ctypes.c_ulonglong),
    ]


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def free_commit_mb() -> float | None:
    """The machine's free commit (what new allocations can still take), None where it is not known."""
    if sys.platform != "win32":
        return None
    status = _Status()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status.avail_commit / 2**20


def wait(free=free_commit_mb, sleep=time.sleep, clock=time.monotonic) -> float:
    """Wait until the free commit reaches the floor or the wait runs out; returns the seconds waited."""
    floor, limit = _number("ANNO_TEST_MIN_FREE_MB", DEFAULT_MB), _number("ANNO_TEST_MEMORY_WAIT", DEFAULT_WAIT)
    start = clock()
    while (mb := free()) is not None and mb < floor and clock() - start < limit:
        sleep(1.0)
    return clock() - start
