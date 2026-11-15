"""The windows the tests left open are deleted before the interpreter shuts down. A full run opens well over a thousand
top-level widgets and keeps them; Qt and PySide then tear them down during the interpreter's own shutdown, where the
process aborts (exit code 127 under Git Bash, 0xC0000409 in the event log) after every test has passed.
``conftest.py`` calls ``release_windows`` once, at the end of the session."""

from __future__ import annotations

import gc
import sys


def release_windows() -> int:
    """Delete every top-level widget that is still alive and let Qt carry the deletions out; returns how many there were
    (0 when no test used the widgets at all)."""
    widgets = sys.modules.get("PySide6.QtWidgets")
    if widgets is None:
        return 0
    from PySide6.QtCore import QCoreApplication, QEvent

    app = widgets.QApplication.instance()
    if app is None:
        return 0
    tops = widgets.QApplication.topLevelWidgets()
    count = len(tops)
    for widget in tops:
        widget.deleteLater()
    del tops
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    return count
