"""The 2205 UI editor's share of a project's mod (``suite.project.Part``): the files its edit file changes in the
game's Flash UI, by game path, for a project on Anno 2205 (the archive delivery packs them with the sessions).

``PART`` is what ``suite.apps.parts`` lists. docs/tool-suite.md, section 18.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..infra.i18n import N_
from ..othergames import installs, ui2205
from ..suite.project import Part, Project
from . import uiproject

GAME = "anno2205"


def edit_file(project: Project) -> Path:
    return project.ui_edits / f"{GAME}.json"


def has_work(project: Project) -> bool:
    if project.game != GAME or not edit_file(project).is_file():
        return False
    edits = uiproject.Project.load(edit_file(project))
    return bool(any(edits.edits.values()) or edits.files or edits.icons)


def render(project: Project, settings, log: Callable[[str], None]) -> dict[str, bytes]:
    edits = uiproject.Project.load(edit_file(project))
    ui = ui2205.UiFiles(installs.GAMES[GAME].tree(Path(settings.anno2205)))
    changed = uiproject.changes(edits, ui)
    log(f"2205 UI: {len(changed)} files changed")
    return changed


PART = Part("ui2205", N_("common.ui_2205"), has_work, render)
