"""Import a whole mod into a new project and build it back (docs/mod-import.md, phase 1). Stdlib only.

``import_mod(source, folder, settings, say)`` reads the mod at ``source`` (a mod folder, or a ``.zip``), sorts every
file (``modclassify``), puts them into the new project ``folder`` as ``mod-files/<game>/<mod path>`` (hard links where
the disk allows, else copies: the source is never changed), records each file's size and SHA-256 and the mod's own
``modinfo`` (``importmeta``), makes the project from the mod's identity and GUID range and returns the
``SImportReport``. ``say`` is called between chunks of files and may raise to stop: the half-made project is then
removed. The build side is ``suite.carriedpart``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import number, tr
from ..modloader.mods import DEPENDENCY_KINDS, LATE, MODINFO_NAMES, localized, parked, read_mod, walk_sizes
from . import appfolders, buildout, guidranges, importchecks, importmeta, importresume, modclassify, projectdeps
from . import project as projects

CHUNK = 1000  # files placed between two chances to stop
MAX_ZIP_BYTES = 16 * 1024**3
ADD_TYPES = frozenset({"add", "addnextsibling", "addafter", "addprevsibling", "addbefore", "append", "prepend"})
GAME_OF_ANNO = {8: "anno117"}  # ``Anno`` in a modinfo; a mod that says none is Anno 1800's
STAGING = ".import-unzip"  # a ``.zip`` is unpacked here (inside the project) and the files linked from it
Say = Callable[[str], None]


class CImportRefused(ValueError):
    """The mod cannot be imported; the message says why in a sentence."""


@dataclass
class SImportReport:
    """What the import did; ``lines()`` is the text for the window and the command line."""

    folder: Path
    source: str
    mod_id: str = ""
    name: str = ""
    version: str = ""
    game: str = ""
    files: int = 0
    size_bytes: int = 0
    linked: int = 0
    copied: int = 0
    owners: Counter = field(default_factory=Counter)  # app -> files recorded for it, still carried
    taken: Counter = field(default_factory=Counter)  # whole-file app -> files placed in its own folder
    carried: Counter = field(default_factory=Counter)  # extension -> files no app edits
    not_understood: Counter = field(default_factory=Counter)  # extension -> files the sort has no kind for
    nested: list = field(default_factory=list)  # (path, ModID, version) of the mods inside the mod
    guid_range: tuple[int, int] = (0, 0)
    guid_added: int = 0  # the assets the mod adds, whose numbers gave the range
    notes: list = field(default_factory=list)
    resumed: int = 0  # files a resumed import found placed already
    seconds: float = 0.0

    def lines(self) -> list[str]:
        report_lines = [
            tr(
                "common.import_summary",
                name=self.name or self.mod_id,
                version=self.version,
                game=self.game,
                files=number(self.files),
                size=_size(self.size_bytes),
                folder=self.folder,
            ),
            tr("common.import_placed", linked=number(self.linked), copied=number(self.copied)),
        ]
        if self.taken:
            report_lines.append(tr("common.import_taken", list=_counts(self.taken)))
        if self.owners:
            report_lines.append(tr("common.import_owners", list=_counts(self.owners)))
        if self.carried:
            report_lines.append(tr("common.import_carried", list=_counts(self.carried)))
        if self.not_understood:
            report_lines.append(tr("common.import_not_understood", list=_counts(self.not_understood)))
        if self.nested:
            report_lines.append(
                tr(
                    "common.import_nested",
                    list=", ".join(f"{mod_id} {version}".strip() for _p, mod_id, version in self.nested),
                )
            )
        range_start, range_end = self.guid_range
        if self.guid_added:
            report_lines.append(
                tr(
                    "common.import_guid_detected",
                    lo=number(range_start),
                    hi=number(range_end),
                    n=number(self.guid_added),
                )
            )
        else:
            report_lines.append(tr("common.import_guid_none", lo=number(range_start), hi=number(range_end)))
        if self.resumed:
            report_lines.append(tr("common.import_resumed", n=number(self.resumed)))
        return [*report_lines, *self.notes]


def _counts(counter: Counter) -> str:
    return ", ".join(f"{key} {number(count)}" for key, count in counter.most_common())


def _size(size_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024 or unit == "GB":
            return f"{number(size_bytes, 0 if unit == 'B' else 1)} {unit}"
        size_bytes /= 1024
    return ""


@dataclass
class _SScan:
    files: list = field(default_factory=list)  # (mod path, size) of every file but the top modinfo
    nested: list = field(default_factory=list)  # folders below the root that hold a modinfo
    notes: list = field(default_factory=list)


def _scan(root: Path) -> _SScan:
    """One walk: every file, the nested mods' folders, the folders that are links and the names differing in case."""
    scan = _SScan()
    for here, base, dirs, names in walk_sizes(root):
        prefix = "" if base == "." else f"{base}/"
        for dir_name in dirs:
            if (here / dir_name).is_symlink():
                scan.notes.append(tr("common.import_symlink", path=f"{prefix}{dir_name}"))
        if (
            base != "."
            and any(file_name in MODINFO_NAMES for file_name, _s in names)
            and not any(map(parked, base.split("/")))
        ):
            scan.nested.append(base)
        for name, size in names:
            if base != "." or name.lower() not in MODINFO_NAMES:  # Windows finds ``ModInfo.json`` too
                scan.files.append((f"{prefix}{name}", size))
    return scan


def _case_clashes(folder: Path, scan: _SScan) -> list[str]:
    """Report the paths that differ only in case; where the project's disk cannot hold both (Windows, the usual
    macOS) the later one is left out of the import, else both are kept as written."""
    clashes = importchecks.case_clashes(scan.files)
    if not clashes:
        return []
    probe = folder / ".case-probe"
    probe.write_bytes(b"")
    insensitive = (folder / ".CASE-PROBE").exists()
    probe.unlink()
    if insensitive:
        left = {later for _first, later in clashes}
        scan.files[:] = [entry for entry in scan.files if entry[0] not in left]
    return importchecks.case_clash_lines(clashes, insensitive)


def _unpack(archive: Path, into: Path, say: Say) -> Path:
    """The mod of a ``.zip`` unpacked into ``into``; returns its root (the archive's root, or the one folder around it)."""
    try:
        with zipfile.ZipFile(archive) as zip_file:
            infos = zip_file.infolist()
            if sum(entry.file_size for entry in infos) > MAX_ZIP_BYTES:
                raise CImportRefused(tr("common.import_zip_too_big", name=archive.name, size=MAX_ZIP_BYTES >> 30))
            if any(entry.flag_bits & 0x1 for entry in infos):
                raise CImportRefused(tr("common.import_zip_encrypted", name=archive.name))
            into.mkdir(parents=True, exist_ok=True)
            base = into.resolve()
            for zip_info in infos:
                target = (into / zip_info.filename).resolve()
                if (
                    zip_info.filename.startswith(("/", "\\"))
                    or ":" in zip_info.filename.split("/")[0]
                    or (base not in (target, *target.parents))
                ):
                    raise CImportRefused(tr("common.import_zip_outside", name=archive.name, file=zip_info.filename))
            for zip_info in infos:
                say("")
                zip_file.extract(zip_info, into)
    except zipfile.BadZipFile as error:
        raise CImportRefused(tr("common.import_zip_unreadable", name=archive.name, why=error)) from error
    roots = (
        [into]
        if any((into / modinfo_name).is_file() for modinfo_name in MODINFO_NAMES)
        else [
            candidate
            for candidate in sorted(into.iterdir())
            if candidate.is_dir() and any((candidate / modinfo_name).is_file() for modinfo_name in MODINFO_NAMES)
        ]
    )
    if len(roots) != 1:
        raise CImportRefused(tr("common.import_zip_mods", name=archive.name, n=len(roots)))
    return roots[0]


def _identity(root: Path):
    """``(Mod, modinfo file name, its bytes, game)`` of the mod at ``root``, else ``CImportRefused``."""
    name = next((file_name for file_name in MODINFO_NAMES if (root / file_name).is_file()), None)
    if name is None:
        raise CImportRefused(tr("common.import_no_modinfo", name=root.name))
    mod, why = read_mod(root)
    if mod is None:
        raise CImportRefused(tr("common.import_not_a_mod", why=why))
    anno = mod.m_info.get("Anno")
    if anno is not None and (isinstance(anno, bool) or anno not in GAME_OF_ANNO):
        raise CImportRefused(tr("common.import_other_game", name=root.name, anno=anno))
    return mod, name, (root / name).read_bytes(), GAME_OF_ANNO.get(anno, "anno1800")


@dataclass
class SOptions:
    """How an import runs: ``use_links`` hard-links (else copies), ``is_hashed`` reads every file for its SHA-256,
    ``is_resumable`` keeps the half-made project when ``say`` stops it (``importresume``), ``context`` is what the checks
    look at, ``is_resuming`` continues a cut-short import (``folder_existed``: its folder was there before)."""

    use_links: bool = True
    is_hashed: bool = True
    is_resumable: bool = False
    context: importchecks.CImportContext | None = None
    is_resuming: bool = False
    folder_existed: bool = False


class _CPlacer:
    """Puts one file into the project and hashes it: a hard link where the disk allows, else a copy."""

    def __init__(self, root: Path, folder_of: Callable[[str], str], options: SOptions) -> None:
        self.m_root, self.m_folder_of, self.m_is_hashed = str(root), folder_of, options.is_hashed
        self.m_is_resuming, self.m_placer = options.is_resuming, buildout.CPlacer(options.use_links)

    def __call__(self, item: tuple[str, int]) -> tuple[str, int, str, bool]:
        rel, size_bytes = item
        source_path, target = os.path.join(self.m_root, rel), os.path.join(self.m_folder_of(rel), rel)
        if self.m_is_resuming and os.path.lexists(target):
            buildout.remove(target)  # cut short in the middle of this file: only the project's name goes
        linked = self.m_placer.put(source_path, target)
        digest = ""  # not hashed: the manifest keeps size only (``import_mod(is_hashed=False)``)
        if self.m_is_hashed:
            with open(target, "rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
        return rel, size_bytes, digest, linked


def _placed_already(target: str, size_bytes: int, known: tuple[int, str] | None) -> int:
    """For a file a cut-short import listed whole: 1 hard link, 2 copy; 0 when it must be placed again."""
    try:
        file_stat = os.stat(target)
    except OSError:
        return 0
    return (
        0
        if known is None or known[0] != size_bytes or file_stat.st_size != size_bytes
        else (1 if file_stat.st_nlink > 1 else 2)
    )


def _keep_resumed(project_dir: Path, files: list, folder_of: Callable[[str], str]) -> dict[str, int]:
    """The files a cut-short import placed whole and listed (``rel -> _placed_already``); the manifest is cut down to
    exactly those lines, so each file has one line when the import ends."""
    listed, kept = importresume.valid_manifest(project_dir), {}
    lines = []
    for rel, size in files:
        how = _placed_already(os.path.join(folder_of(rel), rel), size, listed.get(rel))
        if how:
            kept[rel] = how
            lines.append(json.dumps([rel, size, listed[rel][1]], ensure_ascii=False) + "\n")
    (project_dir / importmeta.FILES_FILE).write_text("".join(lines), encoding="utf-8", newline="\n")
    return kept


def _place_all(
    root: Path, project_dir: Path, game: str, files: list, options: SOptions, say: Say, report: SImportReport
) -> None:
    """Every file into its owner's folder of the project (``mod-files/<game>``, or the whole-file app's) and one manifest
    line each, a chunk at a time; a resumed import keeps the files its manifest lists whole."""
    (project_dir / importmeta.MOD_FILES / game).mkdir(parents=True, exist_ok=True)
    nested = tuple(nested_path for nested_path, _i, _v in report.nested)
    by_owner = {
        owner: str(appfolders.root_for(project_dir, game, owner))
        for owner in (*appfolders.FOLDERS, modclassify.CARRIED)
    }

    def folder_of(rel: str) -> str:
        return by_owner[appfolders.owner_of(game, rel, nested)]

    placer, made, total = _CPlacer(root, folder_of, options), set(), len(files)
    kept = _keep_resumed(project_dir, files, folder_of) if options.is_resuming else {}
    with (
        ThreadPoolExecutor(buildout.WORKERS) as pool,
        (project_dir / importmeta.FILES_FILE).open(
            "a" if options.is_resuming else "w", encoding="utf-8", newline="\n"
        ) as manifest,
    ):
        for start in range(0, total, CHUNK):
            chunk = files[start : start + CHUNK]
            todo = []
            for rel, size_bytes in chunk:
                how = kept.get(rel, 0)
                if how:
                    report.resumed += 1
                    report.linked += how == 1
                    report.copied += how == 2
                else:
                    todo.append((rel, size_bytes))
            groups: dict[str, list[str]] = {}
            for rel, _size in todo:
                groups.setdefault(folder_of(rel), []).append(rel)
            for folder, rels in groups.items():
                buildout.make_parents(folder, rels, made)
            for rel, size_bytes, digest, linked in pool.map(placer, todo):
                manifest.write(json.dumps([rel, size_bytes, digest], ensure_ascii=False) + "\n")
                report.linked += linked
                report.copied += not linked
            manifest.flush()
            done = start + len(chunk)
            say(
                tr("common.import_progress", done=number(done), total=number(total))
                if done // buildout.REPORT != start // buildout.REPORT
                else ""
            )


def _sort(game: str, files: list, report: SImportReport) -> None:
    """Count what the whole-file apps take (``taken``), what the other apps are to take later (``owners``) and what
    is carried, by extension."""
    nested = tuple(path for path, _id, _version in report.nested)
    for rel, size_bytes in files:
        kind = modclassify.classify(game, rel)
        report.files += 1
        report.size_bytes += size_bytes
        extension = modclassify.extension(rel)
        if appfolders.owner_of(game, rel, nested) != modclassify.CARRIED:
            report.taken[kind.owner] += 1
        elif not kind.understood:
            report.not_understood[extension] += 1
        elif kind.owner == modclassify.CARRIED:
            report.carried[extension] += 1
        else:
            report.owners[kind.owner] += 1


def added_guids(path: Path) -> list[int]:
    """The GUIDs of the assets a ModOps file adds: an ``<Asset>`` of a top-level or ``add`` payload (not a ``merge``
    or ``replace`` of an existing one), by its ``Values/Standard/GUID``."""
    found: list[int] = []
    stack: list[tuple[str, bool]] = []  # (tag, whether what is below adds)
    for event, element in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            parent_adds = stack[-1][1] if stack else True
            if element.tag == "ModOp":
                kind = (
                    element.get("Type")
                    or next(
                        (
                            attribute_name
                            for attribute_name in ("Add", "Append", "Prepend")
                            if element.get(attribute_name)
                        ),
                        "",
                    )
                ).lower()
                stack.append((element.tag, kind in ADD_TYPES))
            else:
                stack.append((element.tag, parent_adds))
            continue
        tags = [tag for tag, _a in stack]
        if element.tag == "GUID" and tags[-4:] == ["Asset", "Values", "Standard", "GUID"] and stack[-4][1]:
            try:
                found.append(int((element.text or "").strip()))
            except ValueError:
                pass
        stack.pop()
        if element.tag in ("Asset", "ModOp"):
            element.clear()
    return found


def _guid_range(root: Path, game: str, files: list, notes: list[str]) -> tuple[tuple[int, int], int]:
    """The block range that holds the GUIDs the mod adds, and how many: ``((0, 0), 0)`` for a mod that adds none."""
    guids: list[int] = []
    for rel, _size in files:
        if modclassify.classify(game, rel).owner == modclassify.ASSETS:
            try:
                guids += added_guids(root / rel)
            except (ET.ParseError, OSError) as error:
                notes.append(tr("common.import_guid_unread", file=rel, error=error))
    guids = [guid for guid in guids if 0 < guid <= guidranges.MAX_GUID]
    if not guids:
        return (0, 0), 0
    block = guidranges.BLOCK
    return (min(guids) // block * block, min(max(guids) // block * block + block - 1, guidranges.MAX_GUID)), len(guids)


def _dependencies(mod, notes: list[str]) -> dict[str, list[str]]:
    """The mod's dependency kinds as the project can hold them (an id it cannot hold stays in the kept modinfo)."""
    found = {
        "Require": mod.m_require, "Optional": mod.m_optional, "LoadAfter": mod.m_load_after,
        "Incompatible": mod.m_incompatible, "Deprecate": mod.m_deprecate,
    }  # fmt: skip
    kept: dict[str, list[str]] = {}
    for kind in DEPENDENCY_KINDS:
        for dep in found[kind]:
            if (projectdeps.ID_FORMAT.match(dep) or (kind == "LoadAfter" and dep == LATE)) and dep != mod.m_mod_id:
                kept.setdefault(kind, []).append(dep)
            else:
                notes.append(tr("common.import_dependency_dropped", kind=kind, id=dep))
    return projectdeps.clean(kept)


def _project_for(mod, game: str, folder: Path, notes: list[str]) -> projects.CProject:
    """The project the mod's identity makes (not saved yet)."""
    mod_id = mod.m_mod_id
    if not projectdeps.ID_FORMAT.match(mod_id):
        mod_id = projects._slug(mod_id) or projects._slug(folder.name) or "imported-mod"
        notes.append(tr("common.import_id_changed", old=mod.m_mod_id, new=mod_id))
    return projects.CProject(
        folder,
        mod_id=mod_id,
        name=mod.m_name or mod.m_folder.name,
        version=mod.m_version or "0.1.0",
        game=game,
        category=mod.m_category or "Modding",
        description=localized(mod.m_info.get("Description")),
        dependencies=_dependencies(mod, notes),
    )


def _nested_mods(root: Path, folders: list[str], notes: list[str]) -> list[tuple[str, str, str]]:
    nested = []
    for rel in folders:
        mod, why = read_mod(root / rel)
        if mod is None:
            notes.append(tr("common.import_nested_unreadable", path=rel, why=why))
        else:
            nested.append((rel, mod.m_mod_id, mod.m_version))
    return nested


def _check_target(source: Path, folder: Path) -> None:
    """The new project's folder: nothing in it yet, and not inside the mod (a ``modinfo`` of its own there would be a
    nested mod to the loader) or around it."""
    here, there = folder.resolve(), source.resolve()
    if there == here or there in here.parents or here in there.parents:
        raise CImportRefused(tr("common.import_folder_overlaps", folder=folder, source=source))
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise CImportRefused(tr("common.import_folder_not_empty", folder=folder))


def import_mod(
    source: Path,
    folder: Path,
    settings=None,
    say: Say = lambda _t: None,
    use_links: bool = True,
    is_hashed: bool = True,
    context: importchecks.CImportContext | None = None,
    is_resumable: bool = False,
) -> SImportReport:
    """Import the mod at ``source`` (a mod folder, or a ``.zip``) as a new project in ``folder``. ``settings`` gives
    the own GUID reserve for a mod that adds no asset (and notes the project); ``say`` is told the steps and may raise
    to stop; ``use_links`` False copies every file; ``is_hashed`` False leaves the SHA-256 out (a mod of tens of GB is
    read once less; the manifest keeps the sizes); ``context`` is what the report's checks look at; ``is_resumable``:
    a stop keeps the half-made project for ``importresume.resume``, else it is removed. Raises ``CImportRefused`` with
    a sentence; nothing stays after a failure."""
    source, folder = Path(source), Path(folder)
    if not source.exists():
        raise CImportRefused(tr("common.import_source_missing", path=source))
    _check_target(source, folder)
    options = SOptions(use_links, is_hashed, is_resumable, context, folder_existed=folder.is_dir())
    return run(source, folder, settings, say, options)


def run(source: Path, folder: Path, settings, say: Say, options: SOptions) -> SImportReport:
    """The import into ``folder`` (new, or cut short before and ``options.is_resuming``): the journal is written first and
    goes with a normal end; a failure removes the project, a stop does too unless ``options.is_resumable``."""
    started = time.monotonic()
    registry = options.context.m_registry if options.context else None
    folder.mkdir(parents=True, exist_ok=True)
    importresume.write_journal(
        folder, importresume.SJournal(str(source), options.use_links, options.is_hashed, options.folder_existed)
    )
    importresume.register(registry, folder)
    stopped: list[bool] = []

    def talk(text: str) -> None:
        try:
            say(text)
        except BaseException:
            stopped.append(True)
            raise

    try:
        report = _import(source, folder, settings, talk, options)
    except BaseException:
        if stopped and options.is_resumable:
            importresume.write_journal(
                folder,
                importresume.SJournal(
                    str(source), options.use_links, options.is_hashed, options.folder_existed, importresume.STOPPED
                ),
            )
        else:
            clear(folder, remove=not options.folder_existed)
            importresume.unregister(registry, folder)
        raise
    importresume.finish(folder, registry)
    report.seconds = time.monotonic() - started
    return report


def clear(folder: Path, remove: bool) -> None:
    """Take out what a failed import made (the folder was empty or new): links go, the source files stay. What cannot
    be removed stays without a word here: the caller's error is the one to show."""

    def read_only(_func, path, _exc) -> None:
        try:
            buildout.remove(path)  # a read-only link (a mod cloned with git has them); the folder goes after it
        except OSError:
            pass

    for child in folder.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, onexc=read_only)
            else:
                buildout.remove(child)
        except OSError:
            pass
    if remove:
        try:
            folder.rmdir()
        except OSError:
            pass


def _import(source: Path, folder: Path, settings, say: Say, options: SOptions) -> SImportReport:
    say(tr("common.import_reading", name=source.name))
    staging = folder / STAGING
    root = _unpack(source, staging, say) if source.is_file() else source
    mod, modinfo_name, modinfo_bytes, game = _identity(root)
    report = SImportReport(folder, str(source), mod.m_mod_id, mod.m_name, mod.m_version, game)
    scan = _scan(root)
    report.notes += scan.notes
    report.nested = _nested_mods(root, scan.nested, report.notes)
    report.notes += _case_clashes(folder, scan)
    _sort(game, scan.files, report)
    project = _project_for(mod, game, folder, report.notes)
    report.mod_id = project.mod_id
    _place_all(root, folder, game, scan.files, options, say, report)
    checks = importchecks.run_checks(root, game, scan.files, options.context or importchecks.CImportContext(), say)
    report.notes += checks  # after the files, so a resume runs them once
    originals = folder / importmeta.MOD_FILES
    originals.mkdir(parents=True, exist_ok=True)
    (originals / importmeta.MODINFO_ORIGINAL).write_bytes(modinfo_bytes)
    say(tr("common.import_writing_project"))
    report.guid_range, report.guid_added = _guid_range(root, game, scan.files, report.notes)
    _set_range(project, report, settings)
    project.save()
    if settings is not None:
        projects.remember(settings, folder)
    importmeta.write_meta(folder, _meta(source, project, modinfo_name, report))
    shutil.rmtree(staging, ignore_errors=True)
    return report


def _set_range(project: projects.CProject, report: SImportReport, settings) -> None:
    """The project's GUID range: the block range of the mod's own GUIDs (recorded as own), or the usual new one."""
    if report.guid_added:
        project.guid_range, project.guid_choice = report.guid_range, guidranges.OWN
    elif settings is not None:
        project.choose_range(guidranges.OWN, "", project.guid_range, settings)
    report.guid_range = project.guid_range
    for problem in guidranges.check(project.game, project.guid_range):
        report.notes.append(tr("common.guid_range_problem", problem=problem.text))


def _meta(source: Path, project: projects.CProject, modinfo_name: str, report: SImportReport) -> importmeta.SMeta:
    return importmeta.SMeta(
        source=str(source),
        game=project.game,
        modinfo=modinfo_name,
        identity=importmeta.identity_of(project),
        nested=[{"path": path, "mod_id": mod_id, "version": version} for path, mod_id, version in report.nested],
        files=report.files,
        size=report.size_bytes,
        linked=report.linked,
        copied=report.copied,
        owners=dict(report.owners),
        taken=dict(report.taken),
        carried=dict(report.carried),
        not_understood=dict(report.not_understood),
        guid={"added": report.guid_added, "range": list(report.guid_range), "detected": bool(report.guid_added)},
    )
