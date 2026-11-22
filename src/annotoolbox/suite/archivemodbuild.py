"""The build side of an imported archive mod (docs/mod-import.md, section 16): the project's files written back as the
archive of the same version and layout, and the comparison of a built archive with the one it came from. Stdlib only.

``routed(base)`` is the ``project.SDelivery`` of an older game: a project that came from an archive is packed by
``archiverecord.pack`` (into ``<build>/<archive name>``), any other project by ``base``, the island delivery.
``compare_members(a, b)`` is the round-trip test for these games: the same members and the same bytes in each.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from ..formats import rda
from ..infra.i18n import tr
from ..sources.overlay import normalize
from . import archiverecord, buildout
from . import project as projects
from .carriedpart import STreeDiff

SCRATCH = ".archive-build"  # in the project: where the archive is written before the build takes it
Log = Callable[[str], None]


def is_archive_project(project: projects.CProject) -> bool:
    return archiverecord.read_record(project.folder) is not None


def package(files: dict, project: projects.CProject, _settings, log: Log) -> dict[str, bytes]:
    """The archive of the project: ``{archive name: bytes}``; ``files`` are the parts' merged files (paths or bytes)."""
    record = archiverecord.read_record(project.folder)
    if record is None:
        raise ValueError(tr("common.archive_mod_no_record", folder=project.folder))
    original = archiverecord.original_of(project.folder, record)
    if original is None:
        log(tr("common.archive_mod_original_missing", name=record.archive))
    scratch = project.folder / SCRATCH
    shutil.rmtree(scratch, ignore_errors=True)
    try:
        dest, again = archiverecord.pack(project.folder, record, files, scratch / record.archive, original)
        data = dest.read_bytes()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    log(tr("common.archive_mod_packed", name=record.archive, files=len(files), again=again))
    return {record.archive: data}


def install(project: projects.CProject, _settings, _log: Log) -> str:
    raise RuntimeError(tr("common.archive_mod_install_by_hand", folder=project.build_dir))


def routed(base: projects.SDelivery | None) -> projects.SDelivery:
    """``base`` (how the game takes a project of islands) with the archive mods in front: the files are handed on as
    paths (``lazy``) and read only for a project that is not an archive mod."""

    def pack(files: dict, project: projects.CProject, settings, log: Log) -> dict[str, bytes]:
        if is_archive_project(project):
            return package(files, project, settings, log)
        if base is None:
            raise ValueError(tr("common.building_not_available_edition", game=project.game))
        return base.package({path: buildout.read(source) for path, source in files.items()}, project, settings, log)

    def put(project: projects.CProject, settings, log: Log) -> str:
        if is_archive_project(project):
            return install(project, settings, log)
        if base is None:
            raise ValueError(tr("common.installing_not_available_edition", game=project.game))
        return base.install(project, settings, log)

    return projects.SDelivery(pack, put, lazy=True)


def deliveries(base: Mapping[str, projects.SDelivery] | None = None) -> dict[str, projects.SDelivery]:
    """Every older game's delivery with the archive mods routed in front of ``base`` (the games' island deliveries);
    without it only the archive mods build."""
    return {game: routed((base or {}).get(game)) for game in projects.ARCHIVE_GAMES}


def _members(path: Path) -> dict[str, Callable[[], bytes]]:
    """The files of an archive or of a folder by lower-case path, each a function that reads it."""
    if Path(path).is_dir():
        found = {}
        for here, _dirs, names in Path(path).walk():
            found.update({normalize((here / name).relative_to(path)): (here / name).read_bytes for name in names})
        return found
    archive = rda.CRdaArchive(path)
    return {
        normalize(member_path): (lambda entry=entry: archive.read_entry(entry))
        for member_path, entry in archive.entries.items()
    }


def compare_members(first_path: Path, second_path: Path, say: Callable[[str], None] = lambda _t: None) -> STreeDiff:
    """Compare the members of ``first_path`` and ``second_path`` (each an archive, or a folder of files): the same paths and the same
    bytes. ``STreeDiff.missing`` are the members of ``first_path`` that ``second_path`` lacks, ``extra`` the other way round."""
    left, right = _members(Path(first_path)), _members(Path(second_path))
    diff = STreeDiff(missing=sorted(left.keys() - right.keys()), extra=sorted(right.keys() - left.keys()))
    for path in sorted(left.keys() & right.keys()):
        if left[path]() == right[path]():
            diff.same += 1
        else:
            diff.different.append(path)
        say("")
    return diff


def same_bytes(first_path: Path, second_path: Path) -> bool:
    """Whether two files are the same bytes (streamed)."""
    with open(first_path, "rb") as first_file, open(second_path, "rb") as second_file:
        return (
            hashlib.file_digest(first_file, "sha256").hexdigest()
            == hashlib.file_digest(second_file, "sha256").hexdigest()
        )
