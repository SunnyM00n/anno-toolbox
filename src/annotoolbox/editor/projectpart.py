"""The island editor's share of a project's mod (``suite.project.Part``): every island with an edit file in the
project, for the project's game (117: replaced in place or added as new islands; 1800: replaced in place; 2070, 1404 and 2205: the
edited island files, which ``archivedelivery`` packs into the archives the game loads).

``PART`` is what ``suite.apps.parts`` lists; ``render`` builds the islands as the Build items of the editor do, into a
temporary folder, and hands the files over. docs/tool-suite.md, C4.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from ..infra.i18n import N_, tr
from ..suite.islandguids import ProjectIslandGuids
from ..suite.project import ARCHIVE_GAMES, Part, Project
from . import archivedelivery
from .formats.a7m import edit117 as a7m_edit117
from .formats.a7m import edit1800 as a7m_edit1800

PREFIXES = {
    "anno117": a7m_edit117.EDIT_PREFIX,
    "anno1800": a7m_edit1800.EDIT_PREFIX,
    **archivedelivery.EDIT_PREFIXES,
}


def has_work(project: Project) -> bool:
    prefix = PREFIXES.get(project.game)
    return prefix is not None and any(project.island_edits.glob(f"{prefix}*.json"))


def _files(project: Project, settings):
    """The game's files the islands are built from."""
    if project.game == "anno117":
        from ..islands.islandview import Files
        from ..sources.anno117 import Anno117Files

        return Files(Anno117Files(Path(settings.anno117)))
    from ..othergames import installs
    from .formats.a7m.reader import GameFiles

    return GameFiles(installs.GAMES["anno1800"].tree(Path(settings.anno1800)))


def render(project: Project, settings, log: Callable[[str], None]) -> dict[str, bytes]:
    if project.game in ARCHIVE_GAMES:  # the files by game path; the delivery packs them
        return archivedelivery.island_files(project, settings, log)
    with tempfile.TemporaryDirectory(prefix="islands-") as tmp:
        out = Path(tmp).resolve() / project.mod_id
        args = (_files(project, settings), project.island_edits, out, project.mod_id, project.name, log)
        if project.game == "anno117":  # new islands take their GUIDs from the project's range
            numbering = ProjectIslandGuids(project.island_guids, project.guid_range, a7m_edit117.IslandGuids())
            written = a7m_edit117.build(*args, guids=numbering)
        else:
            written = a7m_edit1800.build(*args)
        log(tr("islands: {n} written", n=len(written)))
        return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


PART = Part("islands", N_("Islands"), has_work, render)
