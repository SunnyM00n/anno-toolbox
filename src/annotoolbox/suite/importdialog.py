"""The window side of importing a mod as a project (docs/mod-import.md, section 3.1), shared by the Project window and
the Mods app: the two folder questions, the import run off the window's thread with its progress and stop, and the
report. ``suite.projectimport`` does the work.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from ..infra.i18n import tr
from . import projectimport
from .background import Background, JobTalk, Stopped


def ask_source(parent: QWidget, zip_file: bool = False) -> Path | None:
    """The mod to import: its folder, or a ``.zip`` file."""
    if zip_file:
        name, _ = QFileDialog.getOpenFileName(parent, tr("common.import_pick_zip"), "", tr("mods.zip_files"))
    else:
        name = QFileDialog.getExistingDirectory(parent, tr("common.import_pick_mod"))
    return Path(name) if name else None


def ask_target(parent: QWidget, source: Path) -> Path | None:
    """The new, empty folder of the project; says why when the chosen one cannot take it."""
    name = QFileDialog.getExistingDirectory(parent, tr("common.import_pick_project", name=source.stem))
    if not name:
        return None
    folder = Path(name)
    if any(folder.iterdir()):
        QMessageBox.warning(parent, tr("common.project"), tr("common.import_folder_not_empty", folder=folder))
        return None
    return folder


def start(
    talk: JobTalk,
    background: Background,
    source: Path,
    folder: Path,
    settings,
    done: Callable[[projectimport.ImportReport | Exception], None],
    wait: bool = False,
) -> None:
    """Import ``source`` as a project in ``folder`` off the window's thread. ``talk``'s ``said`` carries the progress;
    ``talk.stop()`` ends it (the half-made project is removed) and ``done`` then gets ``Stopped``."""
    background.run(lambda: projectimport.import_mod(source, folder, settings, talk.say), done, wait)


def explain(result: projectimport.ImportReport | Exception) -> list[str]:
    """The lines to show for how an import ended."""
    if isinstance(result, Stopped):
        return [tr("common.import_stopped")]
    if isinstance(result, Exception):
        return [tr("common.import_failed", error=result)]
    return result.lines()
