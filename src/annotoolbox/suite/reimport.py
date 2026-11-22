"""Import a mod again over its project (docs/mod-import.md, section 14): a three-way compare by manifest.

The three sides are the mod as the import saw it (the manifest's size and SHA-256), the project's file now (the person's
work in an app), and the mod now. ``plan`` reads and changes nothing; ``apply`` takes the new mod's files where the
person changed nothing, leaves the person's files where the mod did not change, and for a file both changed asks for a
choice (``KEEP`` mine, ``TAKE`` the mod's), never overwriting an edit unasked. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..infra.i18n import N_, number, tr
from . import appfolders, buildout, importmeta, modclassify
from . import project as projects
from . import projectimport as pi

KEEP = "keep"
TAKE = "take"
HANDOVER_RECORD = (
    "handover.json"  # the Assets hand-over (docs/mod-import.md section 11) holds files outside the folders
)
SHOWN = 8
Say = Callable[[str], None]


@dataclass
class SConflict:
    """A file both sides changed (``removed``: the mod deleted a file the person changed)."""

    rel: str
    removed: bool = False


@dataclass
class CReimportPlan:
    """What a re-import would do. ``update``: the mod changed, the person did not (taken); ``mine``: the person changed
    it and the mod did not (kept); ``added``/``removed``: the mod's new and deleted files; ``conflicts``: both changed."""

    m_folder: Path
    m_source: Path
    m_root: Path
    m_game: str
    m_unchanged: int = 0
    m_mine: list = field(default_factory=list)
    m_update: list = field(default_factory=list)
    m_added: list = field(default_factory=list)
    m_removed: list = field(default_factory=list)
    m_conflicts: list = field(default_factory=list)
    m_identity: dict = field(default_factory=dict)  # modinfo fields the mod changed (the person did not)
    m_notes: list = field(default_factory=list)
    m_new_files: dict = field(default_factory=dict)  # rel -> (size, sha256 or "") of the mod now
    m_staging: Path | None = None
    m_modinfo: bytes = b""

    def lines(self) -> list[str]:
        out = [
            tr(
                "common.reimport_summary",
                unchanged=number(self.m_unchanged),
                mine=number(len(self.m_mine)),
                update=number(len(self.m_update)),
                added=number(len(self.m_added)),
                removed=number(len(self.m_removed)),
                conflicts=number(len(self.m_conflicts)),
            )
        ]
        for key, rows in (
            (N_("common.reimport_mine"), self.m_mine),
            (
                N_("common.reimport_conflicts"),
                [conflict.rel + (" (removed)" if conflict.removed else "") for conflict in self.m_conflicts],
            ),
        ):
            if rows:
                more = f" ... {number(len(rows) - SHOWN)}" if len(rows) > SHOWN else ""
                out.append(tr(key, list=", ".join(rows[:SHOWN]) + more))
        if self.m_identity:
            out.append(tr("common.reimport_identity", fields=", ".join(sorted(self.m_identity))))
        return [*out, *self.m_notes]

    def dispose(self) -> None:
        """Remove what the plan unpacked (a ``.zip`` mod)."""
        if self.m_staging is not None:
            shutil.rmtree(self.m_staging, ignore_errors=True)
            self.m_staging = None


def _digest(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _where(project_dir: Path, game: str, rel: str, nested: tuple[str, ...]) -> list[Path]:
    """The project's places for mod path ``rel``: the owner's folder, then the carried one (an older import)."""
    first = appfolders.root_for(project_dir, game, appfolders.owner_of(game, rel, nested)) / rel
    carried = appfolders.root_for(project_dir, game, modclassify.CARRIED) / rel
    return [first] if first == carried else [first, carried]


def _current(places: list[Path]) -> Path | None:
    return next((place for place in places if place.is_file()), None)


def _is_original(path: Path, size_bytes: int, digest: str) -> bool:
    """Whether the project's file still holds what the import gave (the size, and the SHA-256 when the import kept one)."""
    try:
        if path.stat().st_size != size_bytes:
            return False
        return not digest or _digest(str(path)) == digest
    except OSError:
        return False


def plan(folder: Path | str, source: Path | str, say: Say = lambda _t: None) -> CReimportPlan:
    """Compare the mod at ``source`` (a folder or a ``.zip``; normally the one the project came from) with the
    project at ``folder``; changes nothing. Raises ``CImportRefused`` (a sentence) when the project cannot take it."""
    folder, source = Path(folder), Path(source)
    meta = importmeta.read_meta(folder)
    if meta is None:
        raise pi.CImportRefused(tr("common.reimport_not_imported", folder=folder))
    if (folder / HANDOVER_RECORD).exists() or (folder / appfolders.LAYERED_RECORD).exists():
        raise pi.CImportRefused(tr("common.reimport_handed_over"))
    if not source.exists():
        raise pi.CImportRefused(tr("common.import_source_missing", path=source))
    project = projects.CProject.open(folder)
    staging = folder / pi.STAGING
    try:
        root = pi._unpack(source, staging, say) if source.is_file() else source  # a refused zip leaves nothing
        mod, _name, modinfo_bytes, game = pi._identity(root)
        if game != project.game:
            raise pi.CImportRefused(tr("common.reimport_other_game", game=game, project=project.game))
        made = CReimportPlan(folder, source, root, game, m_staging=staging if source.is_file() else None)
        made.m_modinfo = modinfo_bytes
        made.m_identity = _changed_identity(project, meta, mod)
        _compare(made, project, meta, say)
        return made
    except BaseException:
        if source.is_file():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _changed_identity(project: projects.CProject, meta: importmeta.SMeta, mod) -> dict:
    """The identity fields the mod changed since the import and the person did not edit (the person's edits stay)."""
    now = {"mod_id": mod.m_mod_id, "version": mod.m_version, "name": mod.m_name or mod.m_folder.name}
    return {
        key: value
        for key, value in now.items()
        if value and value != meta.identity.get(key) and getattr(project, key) == meta.identity.get(key)
    }


def _compare(made: CReimportPlan, project: projects.CProject, meta: importmeta.SMeta, say: Say) -> None:
    scan = pi._scan(made.m_root)
    nested = tuple(nested_entry["path"] for nested_entry in meta.nested)
    old = {rel: (size, digest) for rel, size, digest in importmeta.read_manifest(made.m_folder)}
    low_old = {rel.lower(): rel for rel in old}
    if any(not digest for _size, digest in old.values()):
        made.m_notes.append(tr("common.reimport_no_hashes"))
    new = dict(scan.files)
    seen: set[str] = set()

    def look(item: tuple[str, int]) -> tuple[str, int, str, str]:
        rel, size = item
        before = old.get(low_old.get(rel.lower(), ""))
        places = _where(made.m_folder, made.m_game, rel, nested)
        mine = _current(places)
        source_path = os.path.join(made.m_root, rel)
        if before is not None and before[0] == size and mine is not None:
            try:
                if os.path.samestat(
                    os.stat(source_path), os.stat(mine)
                ):  # one file on the disk: the project holds the mod's
                    return rel, size, before[1], "same"
            except OSError:
                pass
        digest = _digest(source_path)
        if before is not None and before[0] == size and (not before[1] or before[1] == digest):
            edited = mine is not None and not _is_original(mine, size, before[1])
            return rel, size, before[1] or digest, "mine" if edited else "unchanged"
        if mine is None:
            return rel, size, digest, "added" if before is None else "update"
        if mine.stat().st_size == size and _digest(str(mine)) == digest:
            return rel, size, digest, "same"
        if before is None or _is_original(mine, before[0], before[1]):
            return rel, size, digest, "update" if before else "conflict"
        return rel, size, digest, "conflict"

    items = list(new.items())
    with ThreadPoolExecutor(buildout.WORKERS) as pool:
        for start in range(0, len(items), pi.CHUNK):
            for rel, size, digest, how in pool.map(look, items[start : start + pi.CHUNK]):
                seen.add(rel.lower())
                made.m_new_files[rel] = (size, digest)
                _sort_one(made, rel, how)
            say("")
    for key, rel in low_old.items():
        if key not in seen:
            mine = _current(_where(made.m_folder, made.m_game, rel, nested))
            if mine is None:
                continue
            if _is_original(mine, *old[rel]):
                made.m_removed.append(rel)
            else:
                made.m_conflicts.append(SConflict(rel, removed=True))


def _sort_one(made: CReimportPlan, rel: str, how: str) -> None:
    if how in ("same", "unchanged"):
        made.m_unchanged += 1
    elif how == "mine":
        made.m_mine.append(rel)
    elif how == "update":
        made.m_update.append(rel)
    elif how == "added":
        made.m_added.append(rel)
    else:
        made.m_conflicts.append(SConflict(rel))


def apply(made: CReimportPlan, choices: dict[str, str] | None = None, say: Say = lambda _t: None) -> list[str]:
    """Carry the plan out; ``choices`` maps a conflict's path to ``KEEP`` or ``TAKE`` (``KEEP`` when absent). Returns the
    report's lines. A stop (``say`` raising) leaves the manifest as it was, so the same plan can be made and applied
    again; the project's own edits are never overwritten except by a ``TAKE``."""
    choices = choices or {}
    project = projects.CProject.open(made.m_folder)
    meta = importmeta.read_meta(made.m_folder)
    nested = tuple(nested_entry["path"] for nested_entry in meta.nested)
    placer, took = buildout.CPlacer(True), 0
    todo = [
        *made.m_update,
        *made.m_added,
        *(conflict.rel for conflict in made.m_conflicts if choices.get(conflict.rel) == TAKE and not conflict.removed),
    ]
    for rel in todo:
        places = _where(made.m_folder, made.m_game, rel, nested)
        target = places[0]
        for old in places:
            if old.is_file() or old.is_symlink():
                buildout.remove(old)
        target.parent.mkdir(parents=True, exist_ok=True)
        placer.put(os.path.join(made.m_root, rel), str(target))
        took += 1
        say("")
    for rel in [
        *made.m_removed,
        *(conflict.rel for conflict in made.m_conflicts if conflict.removed and choices.get(conflict.rel) == TAKE),
    ]:
        for old in _where(made.m_folder, made.m_game, rel, nested):
            if old.is_file():
                buildout.remove(old)
    kept = sum(1 for conflict in made.m_conflicts if choices.get(conflict.rel) != TAKE) + len(made.m_mine)
    _write_state(made, project, meta)
    made.dispose()
    return [tr("common.reimport_done", took=number(took), removed=number(len(made.m_removed)), kept=number(kept))]


def _write_state(made: CReimportPlan, project: projects.CProject, meta: importmeta.SMeta) -> None:
    """The mod's new state is the manifest (so the next compare is against it), its modinfo the kept original, and the
    identity fields it changed (that the person had not) the project's."""
    lines = (json.dumps([rel, *made.m_new_files[rel]], ensure_ascii=False) + "\n" for rel in sorted(made.m_new_files))
    write_atomic(made.m_folder / importmeta.FILES_FILE, "".join(lines).encode("utf-8"))
    original = made.m_folder / importmeta.MOD_FILES / importmeta.MODINFO_ORIGINAL
    write_atomic(original, made.m_modinfo)
    for key, value in made.m_identity.items():
        setattr(project, key, value)
        meta.identity[key] = value
    scan_files = list(made.m_new_files.items())
    meta.files, meta.size = len(scan_files), sum(size for size, _d in made.m_new_files.values())
    importmeta.write_meta(made.m_folder, meta)
    project.save()


def choose_all(made: CReimportPlan, choice: str) -> dict[str, str]:
    """The same choice for every conflict."""
    return {conflict.rel: choice for conflict in made.m_conflicts}
