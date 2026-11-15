"""The memory gate (``tests/memorygate.py``): a test waits while the machine is short of commit, never forever."""

from __future__ import annotations

import os

from tests import memorygate


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_the_gate_opens_when_memory_comes_back_and_at_its_limit(monkeypatch):
    monkeypatch.setenv("ANNO_TEST_MIN_FREE_MB", "1000")
    monkeypatch.setenv("ANNO_TEST_MEMORY_WAIT", "30")
    clock = _Clock()
    readings = iter([200.0, 500.0, 1500.0])
    assert memorygate.wait(lambda: next(readings), clock.sleep, clock) == 2.0
    clock.now = 0.0
    assert memorygate.wait(lambda: 10.0, clock.sleep, clock) == 30.0  # short of memory all along: it goes on
    assert memorygate.wait(lambda: None, clock.sleep, clock) == 0.0  # not known: open


def test_the_machine_reports_its_free_commit():
    found = memorygate.free_commit_mb()
    assert (found is None) == (os.name != "nt") and (found is None or found > 0)
