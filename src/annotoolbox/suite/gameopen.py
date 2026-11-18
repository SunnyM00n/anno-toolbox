"""Open an installed game in an app's window: ``open_installed`` checks the game's folder from the settings, indexes
its archives off the window's thread (one index per game for the whole suite, ``Settings.index_dir``) and hands the
result to the window. The viewer, the object builder, the sound editor and the UI editor each had their own copy.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ..infra.i18n import tr
from ..sources import gametree
from .background import Background


def open_installed(
    window,
    settings,
    key: str,
    background: Background,
    *,
    then: Callable[[object], None],
    prepare: Callable[[object], object] = lambda tree: tree,
    wait: bool = False,
) -> bool:
    """Open game ``key`` from ``settings``. ``prepare(tree)`` runs on the background thread (lay the project over
    the tree, read the app's own indexes); ``then(result)`` runs on the window's thread with what it returned, or
    with the exception when opening failed. ``wait``: do it all here (tests). False when no folder is set (the
    window has said so)."""
    folder = getattr(settings, key, "")
    title = gametree.GAMES.get(key, key)
    if not folder or not Path(folder).is_dir():
        QMessageBox.information(
            window,
            window.windowTitle(),
            tr("common.no_folder_set_islands_file", title=title),
        )
        return False
    window.statusBar().showMessage(tr("common.opening_archives_indexed_once", title=title))
    index = settings.index_dir(key)
    background.run(lambda: prepare(gametree.open_game(key, Path(folder), index)), then, wait=wait)
    return True
