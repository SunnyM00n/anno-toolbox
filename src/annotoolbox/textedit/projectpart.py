"""The Texts app's share of a project's mod (``suite.project.SPart``): the project's ``text-edits/<game>.json`` as the
game's text files (``render``). ``PART`` is what ``suite.apps.parts`` lists. docs/text-editor.md, "How a mod adds or
overrides a text".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..assetedit import assetgame
from ..assetedit.gamefiles import LAYOUTS, open_files
from ..infra.i18n import N_
from ..suite.project import CProject, SPart
from . import merged
from . import render as render_texts
from .editfile import FOLDER, CEditFile


def edits_path(project: CProject, game: str | None = None) -> Path:
    return project.folder / FOLDER / f"{game or project.game}.json"


def change_set_path(project: CProject) -> Path:
    return project.asset_edits / f"{project.game}.json"


def project_edits(project: CProject) -> tuple[CEditFile, list[merged.SConflict]]:
    """The Texts app's edits and the asset editor's texts as one set (this part is the project's only writer of texts)."""
    return merged.project_edits(project, edits_path(project), change_set_path(project))


def has_work(project: CProject) -> bool:
    return project_edits(project)[0].count() > 0


def render(project: CProject, settings, log: Callable[[str], None]) -> dict[str, bytes]:
    edits, conflicts = project_edits(project)
    for conflict in conflicts:
        log(conflict.sentence())
    files = open_files(project.game, Path(getattr(settings, project.game)), assetgame.cache_dir(settings.workspace))
    out = render_texts.render(files, LAYOUTS[project.game], edits, project.mod_id, log)
    log(f"texts: {edits.count()} texts in {len(out)} files")
    return out


PART = SPart("texts", N_("ui.texts"), has_work, render)
