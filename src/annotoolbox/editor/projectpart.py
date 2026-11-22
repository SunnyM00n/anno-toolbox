"""The island editor's share of a project's mod (``suite.project.SPart``): every island with an edit file in the
project, for the project's game (117: replaced in place or added as new islands; 1800: replaced in place; the older
games: the edited files, which ``archivedelivery`` packs). An imported 117 mod's islands lie under
``island-edits/anno117/<game path>`` (``suite.appfolders``) as the base the edit files are made against: an island
with no edit is written as it is, an edited one is built over its base. docs/tool-suite.md, C4; docs/mod-import.md 13.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from ..infra.i18n import N_, tr
from ..islands import islandedits
from ..suite.islandguids import CProjectIslandGuids
from ..suite.project import ARCHIVE_GAMES, CFileRef, CProject, SEditsFolder, SPart
from . import archivedelivery
from .formats.a7m import edit117 as a7m_edit117
from .formats.a7m import edit1800 as a7m_edit1800

PREFIXES = {
    "anno117": a7m_edit117.EDIT_PREFIX,
    "anno1800": a7m_edit1800.EDIT_PREFIX,
    **archivedelivery.EDIT_PREFIXES,
}
BASES = SEditsFolder("island-edits", ("anno117",))  # an imported mod's island files, the base of the edit files
POOL_ASSETS = "data/base/config/export/assets.xml"  # what the island build writes its pool registrations to
EDIT_KEYS = ("name", "source", "target", "registration", "game", "version")  # what an edit file holds besides edits


def base_folder(project: CProject) -> Path:
    """The folder of the imported mod's island files (game paths below it) for the project's game."""
    return BASES.folder(project, project.game)


def has_base(project: CProject) -> bool:
    """Whether an imported mod's island files are in the project (the first one found: no walk of the whole folder)."""
    root = base_folder(project)
    return project.game in BASES.games and root.is_dir() and any(path.is_file() for path in root.rglob("*"))


def edit_files(project: CProject) -> list[Path]:
    prefix = PREFIXES.get(project.game)
    return sorted(project.island_edits.glob(f"{prefix}*.json")) if prefix is not None else []


def has_work(project: CProject) -> bool:
    return bool(edit_files(project)) or has_base(project)


def edits_something(edits: dict) -> bool:
    """Whether an edit file holds an edit: opening an imported island makes none, and the build leaves its bytes alone."""
    return bool(
        edits.get("clear")
        or edits.get("target") == "new"
        or any(value for k, value in edits.items() if k not in EDIT_KEYS)
    )


def _files(project: CProject, settings):
    """The game's files the islands are built from (117: the project's imported islands over them)."""
    if project.game == "anno117":
        from ..islands.islandview import CFiles
        from ..sources.anno117 import CAnno117Files

        root = base_folder(project) if has_base(project) else None
        return CFiles(CAnno117Files(Path(settings.anno117)), root)
    from ..othergames import installs
    from .formats.a7m.reader import CGameFiles

    return CGameFiles(installs.GAMES["anno1800"].tree(Path(settings.anno1800)))


def _built(project: CProject, settings, log: Callable[[str], None]) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix="islands-") as work_dir:
        out = Path(work_dir).resolve() / project.mod_id
        build_arguments = (_files(project, settings), project.island_edits, out, project.mod_id, project.name, log)
        if project.game == "anno117":  # new islands take their GUIDs from the project's range
            numbering = CProjectIslandGuids(project.island_guids, project.guid_range, a7m_edit117.CIslandGuids())
            written = a7m_edit117.build(
                *build_arguments, guids=numbering, wanted=edits_something if has_base(project) else None
            )
        else:
            written = a7m_edit1800.build(*build_arguments)
        log(tr("islands.islands_n_written", n=len(written)))
        return {
            path.relative_to(out).as_posix(): path.read_bytes() for path in sorted(out.rglob("*")) if path.is_file()
        }


def render(project: CProject, settings, log: Callable[[str], None]) -> dict[str, bytes | CFileRef]:
    if project.game in ARCHIVE_GAMES:  # the files by game path; the delivery packs them
        return archivedelivery.island_files(project, settings, log)
    edited = {}
    if any(edits_something(islandedits.load(path)) or not has_base(project) for path in edit_files(project)):
        edited = _built(project, settings, log)  # an imported mod's islands nobody edited are not built again
    if not has_base(project):
        return edited
    pool = edited.get(POOL_ASSETS)
    if pool is not None and b"<ModOp " not in pool:
        del edited[POOL_ASSETS]  # an in-place edit registers nothing: no empty patch to join to the mod's own
    written = {
        path.lower() for path in edited
    }  # an edited island's files replace the base's; the rest is the base as it is
    base = {
        path: CFileRef(saved_file)
        for path, saved_file in BASES.saved_files(project).items()
        if path.lower() not in written
    }
    log(tr("islands.imported_files_n", n=len(base)))
    return {**base, **edited}


PART = SPart("islands", N_("common.islands"), has_work, render)
