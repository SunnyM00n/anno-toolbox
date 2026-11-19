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
class Conflict:
    """A file both sides changed (``removed``: the mod deleted a file the person changed)."""

    rel: str
    removed: bool = False


@dataclass
class ReimportPlan:
    """What a re-import would do. ``update``: the mod changed, the person did not (taken); ``mine``: the person changed
    it and the mod did not (kept); ``added``/``removed``: the mod's new and deleted files; ``conflicts``: both changed."""

    folder: Path
    source: Path
    root: Path
    game: str
    unchanged: int = 0
    mine: list = field(default_factory=list)
    update: list = field(default_factory=list)
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    identity: dict = field(default_factory=dict)  # modinfo fields the mod changed (the person did not)
    notes: list = field(default_factory=list)
    new_files: dict = field(default_factory=dict)  # rel -> (size, sha256 or "") of the mod now
    staging: Path | None = None
    modinfo: bytes = b""

    def lines(self) -> list[str]:
        out = [
            tr(
                "common.reimport_summary",
                unchanged=number(self.unchanged),
                mine=number(len(self.mine)),
                update=number(len(self.update)),
                added=number(len(self.added)),
                removed=number(len(self.removed)),
                conflicts=number(len(self.conflicts)),
            )
        ]
        for key, rows in (
            (N_("common.reimport_mine"), self.mine),
            (N_("common.reimport_conflicts"), [c.rel + (" (removed)" if c.removed else "") for c in self.conflicts]),
        ):
            if rows:
                more = f" ... {number(len(rows) - SHOWN)}" if len(rows) > SHOWN else ""
                out.append(tr(key, list=", ".join(rows[:SHOWN]) + more))
        if self.identity:
            out.append(tr("common.reimport_identity", fields=", ".join(sorted(self.identity))))
        return [*out, *self.notes]

    def dispose(self) -> None:
        """Remove what the plan unpacked (a ``.zip`` mod)."""
        if self.staging is not None:
            shutil.rmtree(self.staging, ignore_errors=True)
            self.staging = None


def _digest(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _where(project_dir: Path, game: str, rel: str, nested: tuple[str, ...]) -> list[Path]:
    """The project's places for mod path ``rel``: the owner's folder, then the carried one (an older import)."""
    first = appfolders.root_for(project_dir, game, appfolders.owner_of(game, rel, nested)) / rel
    carried = appfolders.root_for(project_dir, game, modclassify.CARRIED) / rel
    return [first] if first == carried else [first, carried]


def _current(places: list[Path]) -> Path | None:
    return next((p for p in places if p.is_file()), None)


def _is_original(path: Path, size: int, digest: str) -> bool:
    """Whether the project's file still holds what the import gave (the size, and the SHA-256 when the import kept one)."""
    try:
        if path.stat().st_size != size:
            return False
        return not digest or _digest(str(path)) == digest
    except OSError:
        return False


def plan(folder: Path | str, source: Path | str, say: Say = lambda _t: None) -> ReimportPlan:
    """Compare the mod at ``source`` (a folder or a ``.zip``; normally the one the project came from) with the
    project at ``folder``; changes nothing. Raises ``ImportRefused`` (a sentence) when the project cannot take it."""
    folder, source = Path(folder), Path(source)
    meta = importmeta.read_meta(folder)
    if meta is None:
        raise pi.ImportRefused(tr("common.reimport_not_imported", folder=folder))
    if (folder / HANDOVER_RECORD).exists() or (folder / appfolders.LAYERED_RECORD).exists():
        raise pi.ImportRefused(tr("common.reimport_handed_over"))
    if not source.exists():
        raise pi.ImportRefused(tr("common.import_source_missing", path=source))
    project = projects.Project.open(folder)
    staging = folder / pi.STAGING
    root = pi._unpack(source, staging, say) if source.is_file() else source
    try:
        mod, _name, modinfo_bytes, game = pi._identity(root)
        if game != project.game:
            raise pi.ImportRefused(tr("common.reimport_other_game", game=game, project=project.game))
        made = ReimportPlan(folder, source, root, game, staging=staging if source.is_file() else None)
        made.modinfo = modinfo_bytes
        made.identity = _changed_identity(project, meta, mod)
        _compare(made, project, meta, say)
        return made
    except BaseException:
        if source.is_file():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _changed_identity(project: projects.Project, meta: importmeta.Meta, mod) -> dict:
    """The identity fields the mod changed since the import and the person did not edit (the person's edits stay)."""
    now = {"mod_id": mod.mod_id, "version": mod.version, "name": mod.name or mod.folder.name}
    return {
        key: value
        for key, value in now.items()
        if value and value != meta.identity.get(key) and getattr(project, key) == meta.identity.get(key)
    }


def _compare(made: ReimportPlan, project: projects.Project, meta: importmeta.Meta, say: Say) -> None:
    scan = pi._scan(made.root)
    nested = tuple(n["path"] for n in meta.nested)
    old = {rel: (size, digest) for rel, size, digest in importmeta.read_manifest(made.folder)}
    low_old = {rel.lower(): rel for rel in old}
    if any(not digest for _size, digest in old.values()):
        made.notes.append(tr("common.reimport_no_hashes"))
    new = dict(scan.files)
    seen: set[str] = set()

    def look(item: tuple[str, int]) -> tuple[str, int, str, str]:
        rel, size = item
        before = old.get(low_old.get(rel.lower(), ""))
        places = _where(made.folder, made.game, rel, nested)
        mine = _current(places)
        src = os.path.join(made.root, rel)
        if before is not None and before[0] == size and mine is not None:
            try:
                if os.path.samestat(os.stat(src), os.stat(mine)):  # one file on the disk: the project holds the mod's
                    return rel, size, before[1], "same"
            except OSError:
                pass
        digest = _digest(src)
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
                made.new_files[rel] = (size, digest)
                _sort_one(made, rel, how)
            say("")
    for key, rel in low_old.items():
        if key not in seen:
            mine = _current(_where(made.folder, made.game, rel, nested))
            if mine is None:
                continue
            if _is_original(mine, *old[rel]):
                made.removed.append(rel)
            else:
                made.conflicts.append(Conflict(rel, removed=True))


def _sort_one(made: ReimportPlan, rel: str, how: str) -> None:
    if how in ("same", "unchanged"):
        made.unchanged += 1
    elif how == "mine":
        made.mine.append(rel)
    elif how == "update":
        made.update.append(rel)
    elif how == "added":
        made.added.append(rel)
    else:
        made.conflicts.append(Conflict(rel))


def apply(made: ReimportPlan, choices: dict[str, str] | None = None, say: Say = lambda _t: None) -> list[str]:
    """Carry the plan out; ``choices`` maps a conflict's path to ``KEEP`` or ``TAKE`` (``KEEP`` when absent). Returns the
    report's lines. A stop (``say`` raising) leaves the manifest as it was, so the same plan can be made and applied
    again; the project's own edits are never overwritten except by a ``TAKE``."""
    choices = choices or {}
    project = projects.Project.open(made.folder)
    meta = importmeta.read_meta(made.folder)
    nested = tuple(n["path"] for n in meta.nested)
    placer, took = buildout.Placer(True), 0
    todo = [*made.update, *made.added, *(c.rel for c in made.conflicts if choices.get(c.rel) == TAKE and not c.removed)]
    for rel in todo:
        places = _where(made.folder, made.game, rel, nested)
        target = places[0]
        for old in places:
            if old.is_file() or old.is_symlink():
                buildout.remove(old)
        target.parent.mkdir(parents=True, exist_ok=True)
        placer.put(os.path.join(made.root, rel), str(target))
        took += 1
        say("")
    for rel in [*made.removed, *(c.rel for c in made.conflicts if c.removed and choices.get(c.rel) == TAKE)]:
        for old in _where(made.folder, made.game, rel, nested):
            if old.is_file():
                buildout.remove(old)
    kept = sum(1 for c in made.conflicts if choices.get(c.rel) != TAKE) + len(made.mine)
    _write_state(made, project, meta)
    made.dispose()
    return [tr("common.reimport_done", took=number(took), removed=number(len(made.removed)), kept=number(kept))]


def _write_state(made: ReimportPlan, project: projects.Project, meta: importmeta.Meta) -> None:
    """The mod's new state is the manifest (so the next compare is against it), its modinfo the kept original, and the
    identity fields it changed (that the person had not) the project's."""
    lines = (json.dumps([rel, *made.new_files[rel]], ensure_ascii=False) + "\n" for rel in sorted(made.new_files))
    write_atomic(made.folder / importmeta.FILES_FILE, "".join(lines).encode("utf-8"))
    original = made.folder / importmeta.MOD_FILES / importmeta.MODINFO_ORIGINAL
    write_atomic(original, made.modinfo)
    for key, value in made.identity.items():
        setattr(project, key, value)
        meta.identity[key] = value
    scan_files = list(made.new_files.items())
    meta.files, meta.size = len(scan_files), sum(size for size, _d in made.new_files.values())
    importmeta.write_meta(made.folder, meta)
    project.save()


def choose_all(made: ReimportPlan, choice: str) -> dict[str, str]:
    """The same choice for every conflict."""
    return {c.rel: choice for c in made.conflicts}
