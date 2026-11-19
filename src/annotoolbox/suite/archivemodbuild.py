"""The build side of an imported archive mod (docs/mod-import.md, section 16): the project's files written back as the
archive of the same version and layout, and the comparison of a built archive with the one it came from. Stdlib only.

``routed(base)`` is the ``project.Delivery`` of an older game: a project that came from an archive is packed by
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
from .carriedpart import TreeDiff

SCRATCH = ".archive-build"  # in the project: where the archive is written before the build takes it
Log = Callable[[str], None]


def is_archive_project(project: projects.Project) -> bool:
    return archiverecord.read_record(project.folder) is not None


def package(files: dict, project: projects.Project, _settings, log: Log) -> dict[str, bytes]:
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


def install(project: projects.Project, _settings, _log: Log) -> str:
    raise RuntimeError(tr("common.archive_mod_install_by_hand", folder=project.build_dir))


def routed(base: projects.Delivery | None) -> projects.Delivery:
    """``base`` (how the game takes a project of islands) with the archive mods in front: the files are handed on as
    paths (``lazy``) and read only for a project that is not an archive mod."""

    def pack(files: dict, project: projects.Project, settings, log: Log) -> dict[str, bytes]:
        if is_archive_project(project):
            return package(files, project, settings, log)
        if base is None:
            raise ValueError(tr("common.building_not_available_edition", game=project.game))
        return base.package({p: buildout.read(d) for p, d in files.items()}, project, settings, log)

    def put(project: projects.Project, settings, log: Log) -> str:
        if is_archive_project(project):
            return install(project, settings, log)
        if base is None:
            raise ValueError(tr("common.installing_not_available_edition", game=project.game))
        return base.install(project, settings, log)

    return projects.Delivery(pack, put, lazy=True)


def deliveries(base: Mapping[str, projects.Delivery] | None = None) -> dict[str, projects.Delivery]:
    """Every older game's delivery with the archive mods routed in front of ``base`` (the games' island deliveries);
    without it only the archive mods build."""
    return {game: routed((base or {}).get(game)) for game in projects.ARCHIVE_GAMES}


def _members(path: Path) -> dict[str, Callable[[], bytes]]:
    """The files of an archive or of a folder by lower-case path, each a function that reads it."""
    if Path(path).is_dir():
        found = {}
        for here, _dirs, names in Path(path).walk():
            found.update({normalize((here / n).relative_to(path)): (here / n).read_bytes for n in names})
        return found
    archive = rda.RdaArchive(path)
    return {normalize(p): (lambda e=e: archive.read_entry(e)) for p, e in archive.entries.items()}


def compare_members(a: Path, b: Path, say: Callable[[str], None] = lambda _t: None) -> TreeDiff:
    """Compare the members of ``a`` and ``b`` (each an archive, or a folder of files): the same paths and the same
    bytes. ``TreeDiff.missing`` are the members of ``a`` that ``b`` lacks, ``extra`` the other way round."""
    left, right = _members(Path(a)), _members(Path(b))
    diff = TreeDiff(missing=sorted(left.keys() - right.keys()), extra=sorted(right.keys() - left.keys()))
    for path in sorted(left.keys() & right.keys()):
        if left[path]() == right[path]():
            diff.same += 1
        else:
            diff.different.append(path)
        say("")
    return diff


def same_bytes(a: Path, b: Path) -> bool:
    """Whether two files are the same bytes (streamed)."""
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return hashlib.file_digest(fa, "sha256").hexdigest() == hashlib.file_digest(fb, "sha256").hexdigest()
