"""Import an older game's archive mod into a new project (docs/mod-import.md, section 16). Stdlib only.

``import_archive_mod(source, folder, game, ...)`` reads a ``.rda`` archive (1701, 1404, 2070, 2205), or a folder of
loose files by game path, sorts the members like a mod's files (``modclassify``, ``appfolders``: the apps take their
kinds, the rest is carried under ``mod-files``), keeps the archive's layout (``archiverecord``) and, for an archive,
rebuilds it once from the project's files to say whether the bytes come back. The build side is ``archivemodbuild``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import rda, rdalayout
from ..infra.i18n import N_, number, tr
from ..modloader.mods import walk_sizes
from ..sources.overlay import normalize
from . import appfolders, archiverecord, buildout, importmeta
from . import project as projects
from .projectimport import CImportRefused, SImportReport, _check_target, _counts, _size, _sort, clear

CHUNK = 500  # members written between two chances to stop
SHOWN = 6  # left-out paths named in the report
CHECK_FILE = ".identity-check.rda"
GAME_VERSIONS = {
    "anno2070": ("2.0",),
    "anno1404": ("2.0", "2.2"),
    "anno1701": ("1.1", "2.2"),
    "anno2205": ("2.2",),
}
FOLDER_ARCHIVE = {  # [I] the archive a folder of loose files is built into: the version and block flags the game's patch archives use
    "anno2070": ("2.0", rda.FLAG_COMPRESSED | rda.FLAG_ENCRYPTED),
    "anno1404": ("2.0", rda.FLAG_COMPRESSED | rda.FLAG_ENCRYPTED),
    "anno1701": ("1.1", rda.V1_FLAGS_ZLIB),
    "anno2205": ("2.2", rda.FLAG_ENCRYPTED),
}
Say = Callable[[str], None]
DIFFERENCES = {  # what ``rdalayout.differences`` names (and "bytes": none of them, yet the bytes differ)
    rdalayout.ORDER: N_("common.archive_mod_diff_order"),
    rdalayout.BLOCKS: N_("common.archive_mod_diff_blocks"),
    rdalayout.STAMPS: N_("common.archive_mod_diff_stamps"),
    rdalayout.COMPRESSION: N_("common.archive_mod_diff_compression"),
    rdalayout.SHARING: N_("common.archive_mod_diff_sharing"),
    rdalayout.FILES: N_("common.archive_mod_diff_files"),
    "bytes": N_("common.archive_mod_diff_bytes"),
}
REASONS = {
    "reason_path": N_("common.archive_mod_reason_path"),
    "reason_twice": N_("common.archive_mod_reason_twice"),
}


@dataclass
class SArchiveReport(SImportReport):
    """``SImportReport`` plus what is known about the archive; ``lines()`` is the text for the window and the command
    line. ``identical`` is None for a folder of files (no archive to compare the rebuild with)."""

    archive: str = ""
    format: str = ""
    blocks: int = 0
    deleted: int = 0
    default_flags: int = 0
    identical: bool | None = None
    differences: list = field(default_factory=list)
    left_out: list = field(default_factory=list)  # (path, reason key)

    def lines(self) -> list[str]:
        name = self.name or self.mod_id
        out = [tr("common.import_summary", name=name, version=self.version, game=self.game, files=number(self.files),
                  size=_size(self.size_bytes), folder=self.folder)]  # fmt: skip
        if self.identical is None:
            out.append(
                tr("common.archive_mod_folder", archive=self.archive, version=self.format, flags=self.default_flags)
            )
        else:
            out.append(tr("common.archive_mod_layout", archive=self.archive, version=self.format,
                          blocks=number(self.blocks), deleted=number(self.deleted)))  # fmt: skip
            if self.identical:
                out.append(tr("common.archive_mod_identical"))
            else:
                what = ", ".join(tr(DIFFERENCES[difference]) for difference in self.differences)
                out.append(tr("common.archive_mod_differs", what=what))
        if self.left_out:
            shown = ", ".join(f"{path} ({tr(REASONS[reason])})" for path, reason in self.left_out[:SHOWN])
            more = f" ... {number(len(self.left_out) - SHOWN)}" if len(self.left_out) > SHOWN else ""
            out.append(tr("common.archive_mod_left_out", n=number(len(self.left_out)), list=shown + more))
        for text, counter in (
            (N_("common.import_taken"), self.taken),
            (N_("common.import_owners"), self.owners),
            (N_("common.import_carried"), self.carried),
            (N_("common.import_not_understood"), self.not_understood),
        ):
            if counter:
                out.append(tr(text, list=_counts(counter)))
        return [*out, *self.notes]


@dataclass
class _SMember:
    stored: str  # the path as the source has it
    size_bytes: int
    read: Callable[[], bytes]
    path: str = ""  # the project's path: lower case, forward slashes


def guess_game(archive: Path) -> list[str]:
    """The games that read the archive's version: one when it is certain (1701's V1.1, 2205's V2.2), else the candidates
    (V2.0: 1404 and 2070); empty when the file is no archive."""
    try:
        version = rda.CRdaArchive(archive).version.name
    except (rda.CRdaError, OSError):
        return []
    return [game for game, versions in GAME_VERSIONS.items() if version in versions]


def _archive_members(archive: rda.CRdaArchive) -> list[_SMember]:
    return [
        _SMember(entry.path, entry.size, lambda stored=entry: archive.read_entry(stored))
        for entry in archive.entries.values()
    ]


def _folder_members(root: Path) -> list[_SMember]:
    out = []
    for _here, base, _dirs, names in walk_sizes(root):
        prefix = "" if base == "." else f"{base}/"
        out += [
            _SMember(f"{prefix}{name}", size, lambda file=root / f"{prefix}{name}": file.read_bytes())
            for name, size in names
        ]
    return out


def _sift(members: list[_SMember]) -> tuple[list[_SMember], list[tuple[str, str]]]:
    """The members the project takes, in the source's order, and ``(path, reason)`` of those it cannot: a path that
    leaves the folder or that the game cannot find, or one that names the same file as an earlier member."""
    kept, left, seen = [], [], set()
    for member in members:
        member.path = normalize(member.stored)
        parts = member.path.split("/")
        if not member.path or ".." in parts or any(":" in part for part in parts):
            left.append((member.stored, "reason_path"))
        elif member.path in seen:
            left.append((member.stored, "reason_twice"))
        else:
            try:
                rda.archive_path(member.path)
            except rda.CRdaError:
                left.append((member.stored, "reason_path"))
                continue
            seen.add(member.path)
            kept.append(member)
    return kept, left


def _write_members(
    folder: Path, game: str, members: list[_SMember], say: Say, report: SArchiveReport
) -> list[tuple[str, int, str]]:
    """Every member into its owner's folder of the project, as a file; ``(path, size, sha256)`` of each, in order."""
    made: set[str] = set()
    rows = []
    for start in range(0, len(members), CHUNK):
        for member in members[start : start + CHUNK]:
            root = appfolders.root_for(folder, game, appfolders.owner_of(game, member.path))
            target = root / member.path
            buildout.make_parents(str(root), [member.path], made)
            data = member.read()
            target.write_bytes(data)
            rows.append((member.path, len(data), hashlib.sha256(data).hexdigest()))
        say(tr("common.import_progress", done=number(min(start + CHUNK, len(members))), total=number(len(members))))
    report.copied = len(rows)
    return rows


def _project_for(source: Path, game: str, folder: Path) -> projects.CProject:
    stem = source.stem if source.is_file() else source.name
    mod_id = projects._slug(stem) or projects._slug(folder.name) or "archive-mod"
    return projects.CProject(folder, mod_id=mod_id, name=stem, version="0.1.0", game=game, category="Modding")


def _identity(folder: Path, record: archiverecord.SRecord, source: Path, rows: list, report: SArchiveReport) -> None:
    """Rebuild the archive from the files just written and say whether it is the imported one: the same bytes, or
    what differs (``rdalayout.differences`` and bytes that no reader keeps)."""
    files = {}
    for path, _bytes, _digest in rows:
        owner = appfolders.owner_of(report.game, path)
        files[path] = buildout.CFileRef(appfolders.root_for(folder, report.game, owner) / path)
    scratch = folder / CHECK_FILE
    try:
        original = archiverecord.original_of(folder, record)
        archiverecord.pack(folder, record, files, scratch, original)
        with open(source, "rb") as source_file, open(scratch, "rb") as scratch_file:
            same = (
                hashlib.file_digest(source_file, "sha256").hexdigest()
                == hashlib.file_digest(scratch_file, "sha256").hexdigest()
            )
        record.identical = same
        if not same:
            found = rdalayout.differences(
                archiverecord.layout_of(record), rdalayout.read_layout(rda.CRdaArchive(scratch))
            )
            record.differences = (["files"] if record.left_out else []) + [
                difference for difference in found if difference != "files"
            ] or ["bytes"]
    finally:
        scratch.unlink(missing_ok=True)


def _layout_of_archive(archive: rda.CRdaArchive, kept: list[_SMember], notes: dict) -> rdalayout.SLayout:
    """The archive's layout with the members' project paths (``notes``: the ones that differ from the stored path); a
    deleted path keeps its place under its normalized name."""
    keep = {member.stored: member.path for member in kept}
    for stored in archive.deleted:
        keep.setdefault(stored, normalize(stored))
    notes.update({new: old for old, new in keep.items() if new != old})
    return rdalayout.read_layout(archive).mapped(keep.get)


def import_archive_mod(
    source: Path,
    folder: Path,
    game: str | None = None,
    settings=None,
    say: Say = lambda _t: None,
    use_links: bool = True,
) -> SArchiveReport:
    """Import the archive (a ``.rda``) or the folder of files at ``source`` as a new project in ``folder`` for
    ``game`` (when the archive's version leaves one game, it may be left out). The source is never changed. Raises
    ``CImportRefused`` with a sentence; nothing stays after a failure or a stop (``say`` may raise to stop)."""
    source, folder = Path(source), Path(folder)
    if not source.exists():
        raise CImportRefused(tr("common.import_source_missing", path=source))
    _check_target(source, folder)
    existed = folder.is_dir()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        return _import(source, folder, game, settings, say, use_links)
    except BaseException:
        clear(folder, remove=not existed)
        raise


def _import(source: Path, folder: Path, game: str | None, settings, say: Say, use_links: bool) -> SArchiveReport:
    started = time.monotonic()
    say(tr("common.import_reading", name=source.name))
    archive = _open(source) if source.is_file() else None
    game = _game_of(source, archive, game)
    members = _archive_members(archive) if archive else _folder_members(source)
    kept, left = _sift(members)
    project = _project_for(source, game, folder)
    report = SArchiveReport(folder, str(source), project.mod_id, project.name, project.version, game, left_out=left)
    if archive is not None:
        report.archive, report.format = source.name, archive.version.name
        renamed: dict = {}
        layout = _layout_of_archive(archive, kept, renamed)
        report.blocks, report.deleted = len(layout.blocks), len(layout.deleted)
    else:
        report.archive = f"{project.mod_id}.rda"
        version, flags = FOLDER_ARCHIVE[game]
        report.format, report.default_flags, renamed = version, flags, {}
        layout = rdalayout.default_layout(rda.version_named(version), flags)
    rows = _write_members(folder, game, kept, say, report)
    _sort(game, [(path, size) for path, size, _d in rows], report)
    record = archiverecord.SRecord(
        report.archive, layout.to_json(), renamed, left_out=[[path, reason] for path, reason in left[:200]]
    )
    if archive is not None:
        target = archiverecord.original_path(folder, record)
        target.parent.mkdir(parents=True, exist_ok=True)
        buildout.CPlacer(use_links).put(str(source), str(target))
        record.original = True
        _write_manifest(folder, rows)  # the rebuild takes what is still the imported bytes from the archive
        say(tr("common.archive_mod_checking"))
        _identity(folder, record, source, rows, report)
        report.identical, report.differences = record.identical, record.differences
    archiverecord.write_record(folder, record)
    _write_manifest(folder, rows)
    project.save()
    if settings is not None:
        projects.remember(settings, folder)
    importmeta.write_meta(folder, _meta(source, project, report))
    report.seconds = time.monotonic() - started
    return report


def _open(source: Path) -> rda.CRdaArchive:
    try:
        archive = rda.CRdaArchive(source)
        len(archive)  # reads the index
    except (rda.CRdaError, OSError, ValueError) as exc:
        raise CImportRefused(tr("common.archive_mod_not_archive", path=source, error=exc)) from exc
    return archive


def _game_of(source: Path, archive: rda.CRdaArchive | None, game: str | None) -> str:
    """The game the project is for: the one asked for, else the one the archive's version leaves; refuses when the
    archive's version is not one the game reads, or when it leaves two games and none was asked for."""
    if game is not None and game not in GAME_VERSIONS:
        raise CImportRefused(tr("common.archive_mod_unknown_game", game=game, games=", ".join(GAME_VERSIONS)))
    if archive is None:
        if game is None:
            raise CImportRefused(tr("common.archive_mod_folder_needs_game", name=source.name))
        return game
    version = archive.version.name
    candidates = [candidate for candidate, versions in GAME_VERSIONS.items() if version in versions]
    if game is None:
        if len(candidates) != 1:
            raise CImportRefused(tr("common.archive_mod_game_ambiguous", name=source.name, version=version,
                                   games=", ".join(candidates)))  # fmt: skip
        return candidates[0]
    if game not in candidates:
        raise CImportRefused(tr("common.archive_mod_game_version", game=game, version=version))
    return game


def _write_manifest(folder: Path, rows: list[tuple[str, int, str]]) -> None:
    with (folder / importmeta.FILES_FILE).open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(list(row), ensure_ascii=False) + "\n")


def _meta(source: Path, project: projects.CProject, report: SArchiveReport) -> importmeta.SMeta:
    return importmeta.SMeta(
        source=str(source),
        game=project.game,
        modinfo="",
        identity=importmeta.identity_of(project),
        files=report.files,
        size=report.size_bytes,
        linked=0,
        copied=report.copied,
        owners=dict(report.owners),
        taken=dict(report.taken),
        carried=dict(report.carried),
        not_understood=dict(report.not_understood),
    )
