"""The guard that ends a hanging test with the stacks of every thread (tests/hangguard.py)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from tests import hangguard

ROOT = Path(__file__).resolve().parents[1]


def test_a_test_that_hangs_ends_the_process_with_the_stacks(tmp_path):
    script = tmp_path / "hang.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys, threading
            sys.path.insert(0, ROOT)
            from tests import hangguard
            hangguard.arm("a stuck test", 0.5)
            threading.Event().wait(30)
            """
        ).replace("ROOT", repr(str(ROOT))),
        encoding="utf-8",
        newline="\n",
    )
    run = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert run.returncode == hangguard.EXIT_CODE
    assert "HANG: a stuck test ran over 0.5 s" in run.stderr and "hang.py" in run.stderr


def test_a_disarmed_guard_leaves_the_process_alone():
    hangguard.arm("quick", 0.2)()


def test_the_limit_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("ANNO_TEST_TIMEOUT", "12.5")
    assert hangguard.seconds() == 12.5
    monkeypatch.setenv("ANNO_TEST_TIMEOUT", "soon")
    assert hangguard.seconds() == hangguard.DEFAULT_SECONDS
