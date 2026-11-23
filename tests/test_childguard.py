"""Test children end with their test run (tests/childguard.py)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from tests import childguard

ROOT = Path(__file__).resolve().parents[1]


def _child(code: str, env: dict[str, str]) -> subprocess.Popen:
    body = f"import sys; sys.path.insert(0, {str(ROOT)!r}); from tests import childguard; childguard.watch()\n{code}"
    return subprocess.Popen([sys.executable, "-c", body], env={**os.environ, **env})


def test_a_child_whose_parent_is_gone_ends_itself():
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    child = _child("import time; time.sleep(60)", childguard.child_env(60, parent_pid=gone.pid))
    assert child.wait(timeout=20) == childguard.EXIT_PARENT_GONE


def test_a_child_past_its_deadline_ends_itself():
    child = _child("import time; time.sleep(60)", childguard.child_env(1))
    assert child.wait(timeout=20) == childguard.EXIT_DEADLINE


def test_a_child_with_time_and_a_parent_runs_on():
    child = _child("pass", childguard.child_env(60))
    assert child.wait(timeout=20) == 0


def test_the_prelude_starts_the_watch():
    code = childguard.child_prelude() + "import time; time.sleep(60)"
    start = time.time()
    done = subprocess.run([sys.executable, "-c", code], env={**os.environ, **childguard.child_env(1)}, timeout=30)
    assert done.returncode == childguard.EXIT_DEADLINE and time.time() - start < 25


def _alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = k32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    gone = k32.WaitForSingleObject(handle, 0) == 0
    k32.CloseHandle(handle)
    return not gone


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows's")
def test_a_grandchild_dies_when_the_process_in_the_job_ends(tmp_path):
    script = textwrap.dedent(
        f"""
        import subprocess, sys
        sys.path.insert(0, {str(ROOT)!r})
        from tests import childguard
        assert childguard.kill_children_with_me()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        print(child.pid, flush=True)
        """
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    pid = int(done.stdout.split()[-1])
    deadline = time.time() + 10
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    alive = _alive(pid)
    if alive:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    assert not alive
