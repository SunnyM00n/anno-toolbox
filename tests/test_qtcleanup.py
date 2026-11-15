"""The leftover windows of a test session are deleted at its end (tests/qtcleanup.py). Run in a process of its own: the
function deletes every open window, which the session's other tests may still hold."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

SCRIPT = """
from PySide6.QtWidgets import QApplication, QWidget
from tests import qtcleanup

app = QApplication([])
open_windows = [QWidget() for _ in range(3)]
print(qtcleanup.release_windows(), len(QApplication.topLevelWidgets()), qtcleanup.release_windows())
"""


def test_every_open_top_level_widget_is_deleted_and_counted():
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(root)}
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, env=env, cwd=root, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["3", "0", "0"]  # three were open, none is left, a second call finds nothing
