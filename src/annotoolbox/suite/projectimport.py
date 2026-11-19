"""Import a whole mod into a new project and build it back (docs/mod-import.md, phase 1). Stdlib only.

``import_mod(source, folder, settings, say)`` reads the mod at ``source`` (a mod folder, or a ``.zip``), sorts every
file (``modclassify``), puts them into the new project ``folder`` as ``mod-files/<game>/<mod path>`` (hard links where
the disk allows, else copies: the source is never changed), records each file's size and SHA-256 and the mod's own
``modinfo`` (``importmeta``), makes the project from the mod's identity and GUID range and returns the
``ImportReport``. ``say`` is called between chunks of files and may raise to stop: the half-made project is then
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


class ImportRefused(ValueError):
    """The mod cannot be imported; the message says why in a sentence."""


@dataclass
class ImportReport:
    """What the import did; ``lines()`` is the text for the window and the command line."""

    folder: Path
    source: str
    mod_id: str = ""
    name: str = ""
    version: str = ""
    game: str = ""
    files: int = 0
    size: int = 0
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
        out = [
            tr(
                "common.import_summary",
                name=self.name or self.mod_id,
                version=self.version,
                game=self.game,
                files=number(self.files),
                size=_size(self.size),
                folder=self.folder,
            ),
            tr("common.import_placed", linked=number(self.linked), copied=number(self.copied)),
        ]
        if self.taken:
            out.append(tr("common.import_taken", list=_counts(self.taken)))
        if self.owners:
            out.append(tr("common.import_owners", list=_counts(self.owners)))
        if self.carried:
            out.append(tr("common.import_carried", list=_counts(self.carried)))
        if self.not_understood:
            out.append(tr("common.import_not_understood", list=_counts(self.not_understood)))
        if self.nested:
            out.append(tr("common.import_nested", list=", ".join(f"{i} {v}".strip() for _p, i, v in self.nested)))
        lo, hi = self.guid_range
        if self.guid_added:
            out.append(tr("common.import_guid_detected", lo=number(lo), hi=number(hi), n=number(self.guid_added)))
        else:
            out.append(tr("common.import_guid_none", lo=number(lo), hi=number(hi)))
        if self.resumed:
            out.append(tr("common.import_resumed", n=number(self.resumed)))
        return [*out, *self.notes]


def _counts(counter: Counter) -> str:
    return ", ".join(f"{key} {number(n)}" for key, n in counter.most_common())


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{number(n, 0 if unit == 'B' else 1)} {unit}"
        n /= 1024
    return ""


@dataclass
class _Scan:
    files: list = field(default_factory=list)  # (mod path, size) of every file but the top modinfo
    nested: list = field(default_factory=list)  # folders below the root that hold a modinfo
    notes: list = field(default_factory=list)


def _scan(root: Path) -> _Scan:
    """One walk: every file, the nested mods' folders, the folders that are links and the names differing in case."""
    scan = _Scan()
    for here, base, dirs, names in walk_sizes(root):
        prefix = "" if base == "." else f"{base}/"
        for d in dirs:
            if (here / d).is_symlink():
                scan.notes.append(tr("common.import_symlink", path=f"{prefix}{d}"))
        if base != "." and any(n in MODINFO_NAMES for n, _s in names) and not any(map(parked, base.split("/"))):
            scan.nested.append(base)
        for name, size in names:
            if base != "." or name.lower() not in MODINFO_NAMES:  # Windows finds ``ModInfo.json`` too
                scan.files.append((f"{prefix}{name}", size))
    return scan


def _case_clashes(folder: Path, scan: _Scan) -> list[str]:
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
        scan.files[:] = [f for f in scan.files if f[0] not in left]
    return importchecks.case_clash_lines(clashes, insensitive)


def _unpack(archive: Path, into: Path, say: Say) -> Path:
    """The mod of a ``.zip`` unpacked into ``into``; returns its root (the archive's root, or the one folder around it)."""
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
                raise ImportRefused(tr("common.import_zip_too_big", name=archive.name, size=MAX_ZIP_BYTES >> 30))
            if any(i.flag_bits & 0x1 for i in infos):
                raise ImportRefused(tr("common.import_zip_encrypted", name=archive.name))
            into.mkdir(parents=True, exist_ok=True)
            base = into.resolve()
            for info in infos:
                target = (into / info.filename).resolve()
                if (
                    info.filename.startswith(("/", "\\"))
                    or ":" in info.filename.split("/")[0]
                    or (base not in (target, *target.parents))
                ):
                    raise ImportRefused(tr("common.import_zip_outside", name=archive.name, file=info.filename))
            for info in infos:
                say("")
                zf.extract(info, into)
    except zipfile.BadZipFile as exc:
        raise ImportRefused(tr("common.import_zip_unreadable", name=archive.name, why=exc)) from exc
    roots = (
        [into]
        if any((into / n).is_file() for n in MODINFO_NAMES)
        else [d for d in sorted(into.iterdir()) if d.is_dir() and any((d / n).is_file() for n in MODINFO_NAMES)]
    )
    if len(roots) != 1:
        raise ImportRefused(tr("common.import_zip_mods", name=archive.name, n=len(roots)))
    return roots[0]


def _identity(root: Path):
    """``(Mod, modinfo file name, its bytes, game)`` of the mod at ``root``, else ``ImportRefused``."""
    name = next((n for n in MODINFO_NAMES if (root / n).is_file()), None)
    if name is None:
        raise ImportRefused(tr("common.import_no_modinfo", name=root.name))
    mod, why = read_mod(root)
    if mod is None:
        raise ImportRefused(tr("common.import_not_a_mod", why=why))
    anno = mod.info.get("Anno")
    if anno is not None and (isinstance(anno, bool) or anno not in GAME_OF_ANNO):
        raise ImportRefused(tr("common.import_other_game", name=root.name, anno=anno))
    return mod, name, (root / name).read_bytes(), GAME_OF_ANNO.get(anno, "anno1800")


@dataclass
class Options:
    """How an import runs: ``link`` hard-links (else copies), ``hashed`` reads every file for its SHA-256,
    ``resumable`` keeps the half-made project when ``say`` stops it (``importresume``), ``context`` is what the checks
    look at, ``resume`` continues a cut-short import (``existed``: its folder was there before)."""

    link: bool = True
    hashed: bool = True
    resumable: bool = False
    context: importchecks.ImportContext | None = None
    resume: bool = False
    existed: bool = False


class _Placer:
    """Puts one file into the project and hashes it: a hard link where the disk allows, else a copy."""

    def __init__(self, root: Path, dest: Callable[[str], str], options: Options) -> None:
        self.root, self.dest, self.hashed = str(root), dest, options.hashed
        self.again, self.placer = options.resume, buildout.Placer(options.link)

    def __call__(self, item: tuple[str, int]) -> tuple[str, int, str, bool]:
        rel, size = item
        src, target = os.path.join(self.root, rel), os.path.join(self.dest(rel), rel)
        if self.again and os.path.lexists(target):
            buildout.remove(target)  # cut short in the middle of this file: only the project's name goes
        linked = self.placer.put(src, target)
        digest = ""  # not hashed: the manifest keeps size only (``import_mod(hash_files=False)``)
        if self.hashed:
            with open(target, "rb") as fh:
                digest = hashlib.file_digest(fh, "sha256").hexdigest()
        return rel, size, digest, linked


def _placed_already(target: str, size: int, known: tuple[int, str] | None) -> int:
    """For a file a cut-short import listed whole: 1 hard link, 2 copy; 0 when it must be placed again."""
    try:
        st = os.stat(target)
    except OSError:
        return 0
    return 0 if known is None or known[0] != size or st.st_size != size else (1 if st.st_nlink > 1 else 2)


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
    root: Path, project_dir: Path, game: str, files: list, options: Options, say: Say, report: ImportReport
) -> None:
    """Every file into its owner's folder of the project (``mod-files/<game>``, or the whole-file app's) and one manifest
    line each, a chunk at a time; a resumed import keeps the files its manifest lists whole."""
    (project_dir / importmeta.MOD_FILES / game).mkdir(parents=True, exist_ok=True)
    nested = tuple(p for p, _i, _v in report.nested)
    by_owner = {o: str(appfolders.root_for(project_dir, game, o)) for o in (*appfolders.FOLDERS, modclassify.CARRIED)}

    def folder_of(rel: str) -> str:
        return by_owner[appfolders.owner_of(game, rel, nested)]

    placer, made, total = _Placer(root, folder_of, options), set(), len(files)
    kept = _keep_resumed(project_dir, files, folder_of) if options.resume else {}
    with (
        ThreadPoolExecutor(buildout.WORKERS) as pool,
        (project_dir / importmeta.FILES_FILE).open(
            "a" if options.resume else "w", encoding="utf-8", newline="\n"
        ) as manifest,
    ):
        for start in range(0, total, CHUNK):
            chunk = files[start : start + CHUNK]
            todo = []
            for rel, size in chunk:
                how = kept.get(rel, 0)
                if how:
                    report.resumed += 1
                    report.linked += how == 1
                    report.copied += how == 2
                else:
                    todo.append((rel, size))
            groups: dict[str, list[str]] = {}
            for rel, _size in todo:
                groups.setdefault(folder_of(rel), []).append(rel)
            for folder, rels in groups.items():
                buildout.make_parents(folder, rels, made)
            for rel, size, digest, linked in pool.map(placer, todo):
                manifest.write(json.dumps([rel, size, digest], ensure_ascii=False) + "\n")
                report.linked += linked
                report.copied += not linked
            manifest.flush()
            done = start + len(chunk)
            say(
                tr("common.import_progress", done=number(done), total=number(total))
                if done // buildout.REPORT != start // buildout.REPORT
                else ""
            )


def _sort(game: str, files: list, report: ImportReport) -> None:
    """Count what the whole-file apps take (``taken``), what the other apps are to take later (``owners``) and what
    is carried, by extension."""
    nested = tuple(p for p, _i, _v in report.nested)
    for rel, size in files:
        kind = modclassify.classify(game, rel)
        report.files += 1
        report.size += size
        ext = modclassify.extension(rel)
        if appfolders.owner_of(game, rel, nested) != modclassify.CARRIED:
            report.taken[kind.owner] += 1
        elif not kind.understood:
            report.not_understood[ext] += 1
        elif kind.owner == modclassify.CARRIED:
            report.carried[ext] += 1
        else:
            report.owners[kind.owner] += 1


def added_guids(path: Path) -> list[int]:
    """The GUIDs of the assets a ModOps file adds: an ``<Asset>`` of a top-level or ``add`` payload (not a ``merge``
    or ``replace`` of an existing one), by its ``Values/Standard/GUID``."""
    found: list[int] = []
    stack: list[tuple[str, bool]] = []  # (tag, whether what is below adds)
    for event, el in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            parent_adds = stack[-1][1] if stack else True
            if el.tag == "ModOp":
                kind = (el.get("Type") or next((k for k in ("Add", "Append", "Prepend") if el.get(k)), "")).lower()
                stack.append((el.tag, kind in ADD_TYPES))
            else:
                stack.append((el.tag, parent_adds))
            continue
        tags = [t for t, _a in stack]
        if el.tag == "GUID" and tags[-4:] == ["Asset", "Values", "Standard", "GUID"] and stack[-4][1]:
            try:
                found.append(int((el.text or "").strip()))
            except ValueError:
                pass
        stack.pop()
        if el.tag in ("Asset", "ModOp"):
            el.clear()
    return found


def _guid_range(root: Path, game: str, files: list, notes: list[str]) -> tuple[tuple[int, int], int]:
    """The block range that holds the GUIDs the mod adds, and how many: ``((0, 0), 0)`` for a mod that adds none."""
    guids: list[int] = []
    for rel, _size in files:
        if modclassify.classify(game, rel).owner == modclassify.ASSETS:
            try:
                guids += added_guids(root / rel)
            except (ET.ParseError, OSError) as exc:
                notes.append(tr("common.import_guid_unread", file=rel, error=exc))
    guids = [g for g in guids if 0 < g <= guidranges.MAX_GUID]
    if not guids:
        return (0, 0), 0
    block = guidranges.BLOCK
    return (min(guids) // block * block, min(max(guids) // block * block + block - 1, guidranges.MAX_GUID)), len(guids)


def _dependencies(mod, notes: list[str]) -> dict[str, list[str]]:
    """The mod's dependency kinds as the project can hold them (an id it cannot hold stays in the kept modinfo)."""
    found = {
        "Require": mod.require, "Optional": mod.optional, "LoadAfter": mod.load_after,
        "Incompatible": mod.incompatible, "Deprecate": mod.deprecate,
    }  # fmt: skip
    out: dict[str, list[str]] = {}
    for kind in DEPENDENCY_KINDS:
        for dep in found[kind]:
            if (projectdeps.ID_FORMAT.match(dep) or (kind == "LoadAfter" and dep == LATE)) and dep != mod.mod_id:
                out.setdefault(kind, []).append(dep)
            else:
                notes.append(tr("common.import_dependency_dropped", kind=kind, id=dep))
    return projectdeps.clean(out)


def _project_for(mod, game: str, folder: Path, notes: list[str]) -> projects.Project:
    """The project the mod's identity makes (not saved yet)."""
    mod_id = mod.mod_id
    if not projectdeps.ID_FORMAT.match(mod_id):
        mod_id = projects._slug(mod_id) or projects._slug(folder.name) or "imported-mod"
        notes.append(tr("common.import_id_changed", old=mod.mod_id, new=mod_id))
    return projects.Project(
        folder,
        mod_id=mod_id,
        name=mod.name or mod.folder.name,
        version=mod.version or "0.1.0",
        game=game,
        category=mod.category or "Modding",
        description=localized(mod.info.get("Description")),
        dependencies=_dependencies(mod, notes),
    )


def _nested_mods(root: Path, folders: list[str], notes: list[str]) -> list[tuple[str, str, str]]:
    out = []
    for rel in folders:
        mod, why = read_mod(root / rel)
        if mod is None:
            notes.append(tr("common.import_nested_unreadable", path=rel, why=why))
        else:
            out.append((rel, mod.mod_id, mod.version))
    return out


def _check_target(source: Path, folder: Path) -> None:
    """The new project's folder: nothing in it yet, and not inside the mod (a ``modinfo`` of its own there would be a
    nested mod to the loader) or around it."""
    here, there = folder.resolve(), source.resolve()
    if there == here or there in here.parents or here in there.parents:
        raise ImportRefused(tr("common.import_folder_overlaps", folder=folder, source=source))
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ImportRefused(tr("common.import_folder_not_empty", folder=folder))


def import_mod(
    source: Path,
    folder: Path,
    settings=None,
    say: Say = lambda _t: None,
    link: bool = True,
    hash_files: bool = True,
    context: importchecks.ImportContext | None = None,
    resumable: bool = False,
) -> ImportReport:
    """Import the mod at ``source`` (a mod folder, or a ``.zip``) as a new project in ``folder``. ``settings`` gives
    the own GUID reserve for a mod that adds no asset (and notes the project); ``say`` is told the steps and may raise
    to stop; ``link`` False copies every file; ``hash_files`` False leaves the SHA-256 out (a mod of tens of GB is
    read once less; the manifest keeps the sizes); ``context`` is what the report's checks look at; ``resumable``:
    a stop keeps the half-made project for ``importresume.resume``, else it is removed. Raises ``ImportRefused`` with
    a sentence; nothing stays after a failure."""
    source, folder = Path(source), Path(folder)
    if not source.exists():
        raise ImportRefused(tr("common.import_source_missing", path=source))
    _check_target(source, folder)
    options = Options(link, hash_files, resumable, context, existed=folder.is_dir())
    return run(source, folder, settings, say, options)


def run(source: Path, folder: Path, settings, say: Say, options: Options) -> ImportReport:
    """The import into ``folder`` (new, or cut short before and ``options.resume``): the journal is written first and
    goes with a normal end; a failure removes the project, a stop does too unless ``options.resumable``."""
    started = time.monotonic()
    registry = options.context.registry if options.context else None
    folder.mkdir(parents=True, exist_ok=True)
    importresume.write_journal(folder, importresume.Journal(str(source), options.link, options.hashed, options.existed))
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
        if stopped and options.resumable:
            importresume.write_journal(
                folder,
                importresume.Journal(str(source), options.link, options.hashed, options.existed, importresume.STOPPED),
            )
        else:
            clear(folder, remove=not options.existed)
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


def _import(source: Path, folder: Path, settings, say: Say, options: Options) -> ImportReport:
    say(tr("common.import_reading", name=source.name))
    staging = folder / STAGING
    root = _unpack(source, staging, say) if source.is_file() else source
    mod, modinfo_name, modinfo_bytes, game = _identity(root)
    report = ImportReport(folder, str(source), mod.mod_id, mod.name, mod.version, game)
    scan = _scan(root)
    report.notes += scan.notes
    report.nested = _nested_mods(root, scan.nested, report.notes)
    report.notes += _case_clashes(folder, scan)
    _sort(game, scan.files, report)
    project = _project_for(mod, game, folder, report.notes)
    report.mod_id = project.mod_id
    _place_all(root, folder, game, scan.files, options, say, report)
    checks = importchecks.run_checks(root, game, scan.files, options.context or importchecks.ImportContext(), say)
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


def _set_range(project: projects.Project, report: ImportReport, settings) -> None:
    """The project's GUID range: the block range of the mod's own GUIDs (recorded as own), or the usual new one."""
    if report.guid_added:
        project.guid_range, project.guid_choice = report.guid_range, guidranges.OWN
    elif settings is not None:
        project.choose_range(guidranges.OWN, "", project.guid_range, settings)
    report.guid_range = project.guid_range
    for problem in guidranges.check(project.game, project.guid_range):
        report.notes.append(tr("common.guid_range_problem", problem=problem.text))


def _meta(source: Path, project: projects.Project, modinfo_name: str, report: ImportReport) -> importmeta.Meta:
    return importmeta.Meta(
        source=str(source),
        game=project.game,
        modinfo=modinfo_name,
        identity=importmeta.identity_of(project),
        nested=[{"path": p, "mod_id": i, "version": v} for p, i, v in report.nested],
        files=report.files,
        size=report.size,
        linked=report.linked,
        copied=report.copied,
        owners=dict(report.owners),
        taken=dict(report.taken),
        carried=dict(report.carried),
        not_understood=dict(report.not_understood),
        guid={"added": report.guid_added, "range": list(report.guid_range), "detected": bool(report.guid_added)},
    )
