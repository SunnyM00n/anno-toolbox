"""Every module of the package imports where ``ctypes`` and Qt have no Windows-only names (Linux, macOS): a Windows
library is loaded, its function types are made and Qt's Direct3D classes are imported where they are used, never at
import. Run in a child process with those names hidden, so the check runs on Windows too."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import annotoolbox as package

WINDOWS_ONLY = ("WINFUNCTYPE", "WinDLL", "windll", "OleDLL", "oledll", "HRESULT", "WinError", "FormatError",
                "GetLastError", "get_last_error", "set_last_error")  # fmt: skip
QT_WINDOWS_ONLY = ("QRhiD3D11InitParams", "QRhiD3D12InitParams")  # Qt's Direct3D backends: its Windows builds

SCRIPT = """
import ctypes, importlib, json, pkgutil, sys
for name in {names!r}:
    if hasattr(ctypes, name):
        delattr(ctypes, name)
try:
    from PySide6 import QtGui
except ImportError:
    QtGui = None
for name in {qt_names!r}:
    if QtGui is not None and hasattr(QtGui, name):
        delattr(QtGui, name)
import {pkg} as package
failed = {{}}
for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
    if info.name.endswith(".__main__"):  # runs the command line
        continue
    try:
        importlib.import_module(info.name)
    except ModuleNotFoundError as exc:
        if (exc.name or "").startswith(package.__name__):
            failed[info.name] = f"ModuleNotFoundError: {{exc}}"
        # else an optional package (PySide6, numpy ...) is not installed: not this check's concern
    except BaseException as exc:
        failed[info.name] = f"{{type(exc).__name__}}: {{exc}}"
print(json.dumps(failed))
"""


def test_every_module_imports_without_the_windows_only_ctypes_names():
    src = Path(package.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONDONTWRITEBYTECODE": "1", "QT_QPA_PLATFORM": "offscreen"}
    code = SCRIPT.format(names=WINDOWS_ONLY, qt_names=QT_WINDOWS_ONLY, pkg=package.__name__)
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=src, timeout=600, check=False
    )
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
    assert done.returncode == 0 and lines, done.stdout[-3000:] + done.stderr[-3000:]
    assert json.loads(lines[-1]) == {}
