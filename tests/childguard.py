"""Test children that cannot outlive their test run: ``watch()`` in the child ends it when its parent is gone or its
deadline passed (``child_env`` and ``child_prelude`` are the runner's side); ``kill_children_with_me()`` puts a Windows
test process in a kill-on-close job object, so every subprocess dies with it (docs/maintainers.md, "Tests")."""

from __future__ import annotations

import os
import sys
import threading
import time

ENV_PARENT = "ANNO_CHILD_PARENT_PID"
ENV_DEADLINE = "ANNO_CHILD_DEADLINE"
EXIT_PARENT_GONE = 4
EXIT_DEADLINE = 5
MARGIN = 10.0

_g_job: list = []  # the job handle stays open for the process's lifetime


def child_env(seconds: float, parent_pid: int | None = None) -> dict[str, str]:
    """The variables a child's ``watch`` reads: the parent to follow and the clock time to end at."""
    return {
        ENV_PARENT: str(os.getpid() if parent_pid is None else parent_pid),
        ENV_DEADLINE: repr(time.time() + seconds),
    }


def child_prelude() -> str:
    """The code a runner puts in front of a child's own: it imports this module and starts ``watch``."""
    return f"import sys; sys.path.insert(0, {os.path.dirname(os.path.dirname(__file__))!r}); from tests import childguard; childguard.watch()\n"


def _parent_wait(pid: int, seconds: float) -> bool:
    """Wait up to ``seconds`` for process ``pid`` to end; True when it is gone."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return True
        try:
            return k32.WaitForSingleObject(handle, max(0, int(seconds * 1000))) == 0
        finally:
            k32.CloseHandle(handle)
    time.sleep(min(seconds, 1.0))
    return os.getppid() != pid


def watch() -> None:
    """End this process (``os._exit``) when the parent named in the environment is gone or the deadline passed; does
    nothing without the variables."""
    try:
        parent = int(os.environ[ENV_PARENT])
        deadline = float(os.environ[ENV_DEADLINE])
    except (KeyError, ValueError):
        return

    def run() -> None:
        while True:
            left = deadline - time.time()
            if left <= 0:
                os._exit(EXIT_DEADLINE)
            if _parent_wait(parent, min(left, 1.0)):
                os._exit(EXIT_PARENT_GONE)

    threading.Thread(target=run, name="child-guard", daemon=True).start()


def kill_children_with_me() -> bool:
    """Put this process into a new job object that kills its members when the last handle closes (the process's
    end); every child started afterwards is a member. True when it worked; silently False elsewhere or when
    Windows refuses."""
    if sys.platform != "win32" or _g_job:
        return bool(_g_job)
    try:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Counters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ro", "wo", "oo", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", Counters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not ok or not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            k32.CloseHandle(job)
            return False
        _g_job.append(job)
        return True
    except (OSError, AttributeError, ValueError):
        return False
