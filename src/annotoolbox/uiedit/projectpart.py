"""The UI editor's share of a project's mod (``suite.project.SPart``): the changed and new UI files of the project's
game, kept whole under ``ui-edits/<game>/<game path>``, and the check that every file they name exists.

``UI_PART`` is what ``suite.apps.parts`` lists (``PART`` and the Flash editor's ``flashui.projectpart``);
``save_file`` and ``EDITS.saved_files`` are the window's; ``check`` runs the reference check (``mod.phxrefs``) on
what the project holds. docs/tool-suite.md section 20, "Writing".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..formats import phoenix
from ..infra.i18n import N_
from ..mod import phxrefs
from ..suite.project import UI_EDITS, CProject, SEditsFolder, SPart

EDITS = SEditsFolder(UI_EDITS, ("anno117", "anno1800", "anno1404", "anno1701"))
PART = EDITS.part("ui", N_("common.ui"), "UI")


def _has_work(project: CProject) -> bool:
    from ..flashui import projectpart as flash

    return PART.has_work(project) or flash.PART.has_work(project)


def _render(project: CProject, settings, log: Callable[[str], None]) -> dict[str, bytes]:
    from ..flashui import projectpart as flash

    files: dict[str, bytes] = {}
    for part in (flash.PART, PART):  # each holds work for its own games only
        if part.has_work(project):
            files |= part.render(project, settings, log)
    return files


UI_PART = SPart(
    "ui", N_("common.ui"), _has_work, _render
)  # the UI app's: the Phoenix files and the Flash movies' (``flashui``)


def sidecar(project: CProject, game: str, name: str) -> Path:
    """A file of the editor's own beside the UI files (``names``, ``samples``)."""
    return project.folder / UI_EDITS / f"{game}-{name}.json"


def save_file(project: CProject, game: str, path: str, data: bytes) -> Path:
    """Keep a changed or new UI file in the project (whole or not at all)."""
    return EDITS.save_files(project, game, {path: data})[0]


def check(project: CProject, game: str, has_base: Callable[[str], bool], schema: dict | None = None) -> list[str]:
    """Every reference of the project's UI files that neither the project nor the game has (as the loader lint's
    U01 says it after a build)."""
    root = EDITS.folder(project, game)
    if not (root / phoenix.GENERATED).is_dir():
        return []
    return [f"{where}: {what}" for where, what in phxrefs.missing_refs(root, has_base, schema)]
