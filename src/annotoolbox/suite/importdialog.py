"""The window side of importing a mod as a project (docs/mod-import.md, section 3.1), shared by the Project window and
the Mods app: the two folder questions, the import run off the window's thread with its progress and stop, and the
report. ``suite.projectimport`` does the work.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from . import archivemod, importchecks, importresume, projectimport, reimport
from .background import Background, JobTalk, Stopped
from .settings import settings_file

MAX_ROWS = 500  # conflicts listed in the question; the rest take the choice made for "all"


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


def ask_archive(parent: QWidget, loose_files: bool = False) -> tuple[Path, str | None] | None:
    """An older game's archive mod (a ``.rda`` file, or with ``loose_files`` a folder of its files) and the game it is
    for: asked only when the archive's version leaves more than one (the file is read for that)."""
    if loose_files:
        name = QFileDialog.getExistingDirectory(parent, tr("common.archive_mod_pick_folder"))
    else:
        name, _ = QFileDialog.getOpenFileName(parent, tr("common.archive_mod_pick"), "", tr("common.archive_mod_files"))
    if not name:
        return None
    source = Path(name)
    games = list(archivemod.GAME_VERSIONS) if loose_files else archivemod.guess_game(source)
    if len(games) <= 1 and not loose_files:
        return source, (games[0] if games else None)  # a file that is no archive: the import says so
    game, ok = QInputDialog.getItem(
        parent, tr("common.project"), tr("common.archive_mod_pick_game", name=source.name), games, 0, False
    )
    return (source, game) if ok else None


def start_archive(
    talk: JobTalk,
    background: Background,
    source: Path,
    game: str | None,
    folder: Path,
    settings,
    done: Callable[[projectimport.ImportReport | Exception], None],
    wait: bool = False,
) -> None:
    """Import the archive mod ``source`` as a project in ``folder`` off the window's thread (``talk.stop()`` removes
    the half-made project, ``done`` gets ``Stopped``)."""
    background.run(lambda: archivemod.import_archive_mod(source, folder, game, settings, talk.say), done, wait)


def start(
    talk: JobTalk,
    background: Background,
    source: Path,
    folder: Path,
    settings,
    done: Callable[[projectimport.ImportReport | Exception], None],
    wait: bool = False,
    settings_path: Path | None = None,
) -> None:
    """Import ``source`` as a project in ``folder`` off the window's thread. ``talk``'s ``said`` carries the progress;
    ``talk.stop()`` ends it: the half-made project stays and can be resumed (``ask_pending``), and ``done`` gets
    ``Stopped``."""
    context = importchecks.context_from_settings(settings, registry_of(settings_path))
    background.run(
        lambda: projectimport.import_mod(source, folder, settings, talk.say, context=context, resumable=True),
        done,
        wait,
    )


def registry_of(settings_path: Path | None = None) -> Path:
    """The file that lists the half-made imports: beside the Toolbox's settings."""
    return (settings_path or settings_file()).parent / importresume.PENDING


def start_resume(talk: JobTalk, background: Background, folder: Path, settings, done, wait=False, path=None) -> None:
    """Continue the half-made import in ``folder`` off the window's thread (a stop keeps it again)."""
    context = importchecks.context_from_settings(settings, registry_of(path))
    background.run(lambda: importresume.resume(folder, settings, talk.say, context), done, wait)


def ask_pending(parent: QWidget, found: list[importresume.Pending]) -> tuple[str, importresume.Pending] | None:
    """Offer the first half-made import: ``("resume" | "remove", it)``, or None for later."""
    if not found:
        return None
    one = found[0]
    box = QMessageBox(parent)
    box.setWindowTitle(tr("common.project"))
    box.setText(tr("common.import_pending_question"))
    box.setInformativeText(one.line())
    resume = box.addButton(tr("common.import_resume"), QMessageBox.ButtonRole.AcceptRole)
    remove = box.addButton(tr("common.import_remove"), QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(tr("common.import_later"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    return ("resume", one) if clicked is resume else ("remove", one) if clicked is remove else None


def ask_conflicts(parent: QWidget, made: reimport.ReimportPlan) -> dict[str, str] | None:
    """The three-way question of a re-import: the plan's lines, and per file both sides changed a choice, mine or the
    mod's (mine is the default); None when the person cancels."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("common.reimport_title"))
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(chr(10).join(made.lines())))
    rows = made.conflicts[:MAX_ROWS]
    table = QTableWidget(len(rows), 2, dialog)
    table.setHorizontalHeaderLabels([tr("common.reimport_file"), tr("common.reimport_choice")])
    boxes = []
    for row, conflict in enumerate(rows):
        table.setItem(row, 0, _item(conflict.rel + (" (removed)" if conflict.removed else "")))
        box = QComboBox()
        box.addItem(tr("common.reimport_keep_mine"), reimport.KEEP)
        box.addItem(tr("common.reimport_take_mod"), reimport.TAKE)
        table.setCellWidget(row, 1, box)
        boxes.append(box)
    table.horizontalHeader().setStretchLastSection(True)
    table.setColumnWidth(0, 460)
    if rows:
        layout.addWidget(table)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if not dialog.exec():
        return None
    return {c.rel: box.currentData() for c, box in zip(rows, boxes, strict=True)}


def _item(text: str):
    from PySide6.QtWidgets import QTableWidgetItem

    return QTableWidgetItem(text)


def start_reimport_plan(talk: JobTalk, background: Background, folder: Path, source: Path, done, wait=False) -> None:
    background.run(lambda: reimport.plan(folder, source, talk.say), done, wait)


def start_reimport_apply(talk: JobTalk, background, made, choices, done, wait=False) -> None:
    background.run(lambda: reimport.apply(made, choices, talk.say), done, wait)


def explain(result: projectimport.ImportReport | Exception, folder: Path | None = None) -> list[str]:
    """The lines to show for how an import ended; ``folder``: the project's, to tell a kept stop from a removed one."""
    if isinstance(result, Stopped):
        kept = folder is not None and importresume.read_journal(folder) is not None
        return [tr(N_("common.import_stopped_kept") if kept else N_("common.import_stopped"))]
    if isinstance(result, Exception):
        return [tr("common.import_failed", error=result)]
    return result.lines()
