"""The hand-over of an imported mod's patch files to the Assets and Texts apps (docs/mod-import.md, "Phase 2 as
built"). No Qt. ``plan`` reads the carried ModOps and texts as the import does (a net change set against the
installed game) and lists what that loses; ``take`` moves the files out of ``mod-files`` into the undo store
(``handover/``) and writes the change set, so the build writes ModOps from it; ``undo`` puts the files back. Only on
request: a project without a hand-over builds byte-identical to the mod.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..infra.i18n import N_, number, tr
from ..modloader.mods import GAME_NUMBER, MODINFO_NAMES, CMod, CVariables, find_mods, read_mod
from ..modloader.resolve import resolve
from ..suite import appfolders, carriedpart, importmeta
from ..suite import project as projects
from ..textedit.editfile import FOLDER as TEXT_EDITS
from . import modimport
from .assetgame import CGame
from .changeset import CChangeSet
from .projectpart import change_set_path

RECORD = "handover.json"  # in the project folder: what was taken (absent: nothing was)
STORE = "handover"  # the originals' folder: ``handover/<game>/<mod path>``, and the change set that was there before
STAGE = ".handover-stage"  # the mod's ModOps, copied here for the loader's reading (never stays)
BEFORE = "change-set.before.json"
SUPPORTED = ("anno117", "anno1800")
INCLUDE_SUFFIX = ".include.xml"
INCLUDE = re.compile(rb"""<Include\b[^>]*?\bFile\s*=\s*["']([^"']*)["']""")
SHOWN = 12


class CHandoverRefused(ValueError):
    """The hand-over cannot be made or undone; the message says why in a sentence."""


@dataclass
class SPlan:
    """What a hand-over would do, read and not done: the files it takes (as the mod spells them), the include files that
    go with them, the patch files that stay carried, and the lines of the lossy report."""

    taken: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    imported: modimport.SImported | None = None

    def lines(self) -> list[str]:
        imported = self.imported
        out = [tr("assets.handover_takes", n=len(self.taken) + len(self.includes), list=names(self.taken))]
        if imported is not None:
            out.append(
                tr(
                    "assets.handover_counts",
                    added=number(len(imported.added)),
                    changed=number(len(imported.changed)),
                    removed=number(len(imported.removed)),
                    texts=number(sum(imported.texts.values())),
                )
            )
        if self.kept:
            out.append(tr("assets.handover_leaves", list=names(self.kept)))
        out.append(tr("assets.handover_lost_head", n=len(self.lost)))
        out += [f"  {row}" for row in self.lost[: SHOWN * 2]]
        if len(self.lost) > SHOWN * 2:
            out.append("  " + tr("assets.and_n_more", n=len(self.lost) - SHOWN * 2))
        return out


@dataclass
class SRecord:
    """``handover.json``: the files moved into the store, and the hashes the undo compares to see edits made since."""

    game: str = "anno117"
    files: list = field(default_factory=list)  # mod paths, as the mod spells them
    change_set_sha: str = ""
    texts_sha: str = ""  # of the Texts app's own edit file when the hand-over was made ("" absent)
    had_change_set: bool = False  # an empty change set file was there: kept as ``BEFORE``
    format: int = 1


def names(paths: list[str]) -> str:
    shown = ", ".join(paths[:SHOWN])
    return shown + (f", ... {number(len(paths) - SHOWN)}" if len(paths) > SHOWN else "")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def record_path(project: projects.CProject) -> Path:
    return project.folder / RECORD


def store_root(project: projects.CProject) -> Path:
    return project.folder / STORE / project.game


def text_edits_path(project: projects.CProject) -> Path:
    return project.folder / TEXT_EDITS / f"{project.game}.json"


def mods_folders(settings, game: str) -> list[Path]:
    """The folders that may hold the mods an imported mod requires: the game's mods folder, when set."""
    folder = settings.mods if game == "anno117" else getattr(settings, "mods1800", "") if game == "anno1800" else ""
    return [Path(folder)] if folder else []


def state(project: projects.CProject) -> SRecord | None:
    """The hand-over the project holds, None when none was taken."""
    try:
        data = json.loads(record_path(project).read_text(encoding="utf-8"))
        return SRecord(**{k: value for k, value in data.items() if k in SRecord.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return None


def can_take(project: projects.CProject) -> bool:
    """Whether the Project window offers the hand-over: an imported 117 or 1800 project with none taken yet."""
    return (
        project.game in SUPPORTED
        and importmeta.read_meta(project.folder) is not None
        and carriedpart.carried_root(project).is_dir()
        and state(project) is None
        and appfolders.layered_record(project) is None
    )


def edited_since(project: projects.CProject) -> list[str]:
    """What the apps changed in the handed-over work after the hand-over (the words for a question), empty when
    nothing: the change set's file and the Texts app's own edits against the hashes taken then."""
    record = state(project)
    if record is None:
        return []
    out = []
    if _sha(change_set_path(project)) != record.change_set_sha:
        out.append(tr("assets.handover_edited_assets"))
    if _sha(text_edits_path(project)) != record.texts_sha:
        out.append(tr("assets.handover_edited_texts"))
    return out


# reading the carried files
def patch_files(project: projects.CProject, layout, files: dict[str, int]) -> tuple[list[str], list[str]]:
    """``(taken, kept)``: the carried files the change set writes itself (the asset file's patch, each language's text
    patch) and the other patch files of the mod, which no form takes."""
    taken, kept = [], []
    head, _, tail = (layout.texts or "").partition("{language}")
    for relative_path in sorted(files):
        low = relative_path.lower()
        if low == layout.patch_target.lower() or (
            layout.texts and low.startswith(head.lower()) and low.endswith(tail.lower()) and "/" not in low[len(head) :]
        ):
            if low[len(head) : len(low) - len(tail)] != "metadata":
                taken.append(relative_path)
                continue
        if low.endswith(".xml") and not low.endswith(INCLUDE_SUFFIX):
            kept.append(relative_path)
    return taken, kept


def _closure(root: Path, by_low: dict[str, str], start: list[str]) -> set[str]:
    """Every file the files ``start`` ``Include`` (and those include in turn), as the carried folder spells them."""
    seen: set[str] = set()
    todo = list(start)
    while todo:
        relative_path = todo.pop()
        try:
            data = (root / relative_path).read_bytes()
        except OSError:
            continue
        for ref in INCLUDE.findall(data):
            text = ref.decode("utf-8", "replace")
            joined = (
                text.lstrip("/") if text.startswith("/") else posixpath.join(posixpath.dirname(relative_path), text)
            )
            found = by_low.get(posixpath.normpath(joined).lower())
            if found and found not in seen:
                seen.add(found)
                todo.append(found)
    return seen


def _stage(project: projects.CProject, meta: importmeta.SMeta, root: Path, files: dict[str, int], into: Path) -> Path:
    """A mod folder the loader can read, made of the carried ModOps (every ``.xml``) and every ``modinfo`` (the nested
    mods' too), with the mod's own ``modinfo`` from the import: copies, so nothing of the project is touched."""
    mod = into / "mod"
    mod.mkdir(parents=True)
    (mod / meta.modinfo).write_bytes((project.folder / importmeta.MOD_FILES / importmeta.MODINFO_ORIGINAL).read_bytes())
    for relative_path in files:
        name = relative_path.rsplit("/", 1)[-1].lower()
        if relative_path.lower().endswith(".xml") or name in MODINFO_NAMES:
            target = mod / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative_path, target)
    return mod


def _static_losses(root: Path, plan: SPlan, includes: list[str], mod) -> None:
    """What the net change set cannot keep that the files show on their face (comments, conditions, includes, options)."""
    for relative_path in plan.taken:
        text = (root / relative_path).read_bytes().decode("utf-8", "replace")
        for needle, key in (
            ("<!--", N_("assets.handover_lost_comments")),
            ("Condition=", N_("assets.handover_lost_conditions")),
            ("Skip=", N_("assets.handover_lost_skip")),
            ("MaxRepeat=", N_("assets.handover_lost_skip")),
        ):
            if count := text.count(needle):
                plan.lost.append(tr(key, n=number(count), file=relative_path))
    if includes:
        plan.lost.append(tr("assets.handover_lost_includes", list=names(includes)))
    if isinstance(mod.m_info.get("Options"), dict) and mod.m_info["Options"]:
        plan.lost.append(tr("assets.handover_lost_options"))


def read_carried(
    project: projects.CProject,
    game: CGame,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
) -> tuple[modimport.SImported, CMod]:
    """The carried ModOps files read as the import reads a mod (a copy of them staged for the loader, removed again),
    applied over the installed ``game`` with the mods the mod requires found in ``search``: the import's result and
    the mod. Raises ``CHandoverRefused`` when the loader would not read the mod."""
    meta = importmeta.read_meta(project.folder)
    if meta is None:
        raise CHandoverRefused(tr("assets.handover_not_imported"))
    root = carriedpart.carried_root(project)
    stage = project.folder / STAGE
    shutil.rmtree(stage, ignore_errors=True)
    try:
        mod_folder = _stage(project, meta, root, carriedpart.files_of(root), stage)
        mod, why = read_mod(mod_folder, GAME_NUMBER.get(project.game))
        if mod is None:
            raise CHandoverRefused(why)
        others = [
            candidate for candidate in find_mods(search or [], mod.m_anno)[0] if candidate.m_mod_id != mod.m_mod_id
        ]
        resolution = resolve([mod], others, allow_pull=True)
        return modimport.import_mods(
            game, resolution.order, CVariables(resolution.order, {}), say, resolution=resolution, primary=mod
        ), mod
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def plan(
    project: projects.CProject,
    game: CGame,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
) -> SPlan:
    """What handing the project's patch files over would do and lose, read from the carried files and the installed
    ``game`` (nothing is changed). ``search``: the mods folders that hold the mods this one requires. Raises
    ``CHandoverRefused``."""
    meta = importmeta.read_meta(project.folder)
    if meta is None:
        raise CHandoverRefused(tr("assets.handover_not_imported"))
    if project.game not in SUPPORTED or game.m_layout.key != project.game:
        raise CHandoverRefused(tr("assets.handover_game", game=project.game))
    if state(project) is not None:
        raise CHandoverRefused(tr("assets.handover_already"))
    if appfolders.layered_record(project) is not None:
        raise CHandoverRefused(tr("assets.handover_layered"))
    held = CChangeSet.load(change_set_path(project)) if change_set_path(project).is_file() else None
    if held is not None and (held.m_assets or held.m_new or held.m_removed or held.m_texts):
        raise CHandoverRefused(
            tr("assets.handover_has_work", n=len(held.m_assets) + len(held.m_new) + len(held.m_removed))
        )
    root = carriedpart.carried_root(project)
    files = carriedpart.files_of(root)
    out = SPlan()
    out.taken, out.kept = patch_files(project, game.m_layout, files)
    if not out.taken:
        raise CHandoverRefused(tr("assets.handover_nothing", path=game.m_layout.patch_target))
    by_low = {relative_path.lower(): relative_path for relative_path in files}
    mine = _closure(root, by_low, out.taken)
    elsewhere = _closure(
        root,
        by_low,
        [
            relative_path
            for relative_path in files
            if relative_path.lower().endswith(".xml") and relative_path not in out.taken
        ],
    )
    out.includes = sorted(
        relative_path
        for relative_path in mine
        if relative_path.lower().endswith(INCLUDE_SUFFIX) and relative_path not in elsewhere
    )
    shared = sorted(mine & elsewhere)
    out.imported, mod = read_carried(project, game, say, search)
    _static_losses(root, out, out.includes, mod)
    imported = out.imported
    out.lost.insert(0, tr("assets.handover_lost_layout"))
    out.lost += [event.line() for event in imported.conditional]
    out.lost += imported.lost
    out.lost += [tr("assets.handover_dropped", line=event.line()) for event in imported.dropped]
    if shared:
        out.lost.append(tr("assets.handover_include_shared", list=names(shared)))
    silent = [
        relative_path
        for relative_path in out.taken
        if _language_of(game.m_layout, relative_path)
        and _language_of(game.m_layout, relative_path) not in imported.texts
    ]
    out.lost += [tr("assets.handover_text_dropped", file=relative_path) for relative_path in silent]
    out.lost.append(tr("assets.handover_roundtrip"))
    return out


def _language_of(layout, relative_path: str) -> str:
    head, _, tail = (layout.texts or "").partition("{language}")
    low = relative_path.lower()
    if layout.texts and low.startswith(head.lower()) and low.endswith(tail.lower()):
        return low[len(head) : len(low) - len(tail)]
    return ""


# taking and undoing
def _move(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def take(project: projects.CProject, made: SPlan) -> SRecord:
    """Hand the files of ``made`` over: the change set into the project (the project's identity and GUID range on it),
    the files into the undo store. Raises ``CHandoverRefused`` for a project that changed since ``plan``; a failure
    moves everything back."""
    if made.imported is None or state(project) is not None:
        raise CHandoverRefused(tr("assets.handover_already"))
    if appfolders.layered_record(project) is not None:
        raise CHandoverRefused(tr("assets.handover_layered"))
    root, store = carriedpart.carried_root(project), store_root(project)
    changes = made.imported.changes
    changes.m_carry = []  # the carried part supplies every file the change set does not write
    changes.m_mod_id, changes.m_mod_name, changes.m_version = project.mod_id, project.name, project.version
    changes.m_guid_range, changes.m_guid_choice, changes.m_guid_source = (
        tuple(project.guid_range), project.guid_choice, project.guid_source,
    )  # fmt: skip
    target = change_set_path(project)
    record = SRecord(project.game, [*made.taken, *made.includes], had_change_set=target.is_file())
    if record.had_change_set:
        _move(target, project.folder / STORE / BEFORE)
    moved: list[str] = []
    try:
        for relative_path in record.files:
            if not (root / relative_path).is_file():
                raise CHandoverRefused(tr("assets.handover_file_gone", file=relative_path))
            _move(root / relative_path, store / relative_path)
            moved.append(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        changes.save(target)
        record.change_set_sha, record.texts_sha = _sha(target), _sha(text_edits_path(project))
        write_atomic(record_path(project), (json.dumps(asdict(record), indent=1) + "\n").encode("utf-8"))
    except BaseException:
        for relative_path in moved:
            _move(store / relative_path, root / relative_path)
        target.unlink(missing_ok=True)
        if record.had_change_set:
            _move(project.folder / STORE / BEFORE, target)
        raise
    return record


def undo(project: projects.CProject, discard: bool = False) -> int:
    """Put the handed-over files back into ``mod-files`` and remove the change set the hand-over made: the build is
    byte-identical to the mod again. Edits made in the apps since are lost, so ``CHandoverRefused`` names them unless
    ``discard``. Returns the number of files back."""
    record = state(project)
    if record is None:
        raise CHandoverRefused(tr("assets.handover_none"))
    changed = edited_since(project)
    if changed and not discard:
        raise CHandoverRefused(tr("assets.handover_edited", what=", ".join(changed)))
    root, store = carriedpart.carried_root(project), store_root(project)
    for relative_path in record.files:
        if (root / relative_path).exists() and (store / relative_path).is_file():
            raise CHandoverRefused(tr("assets.handover_in_the_way", file=relative_path))
    target = change_set_path(project)
    target.unlink(missing_ok=True)
    before = project.folder / STORE / BEFORE
    if before.is_file():
        _move(before, target)
    for relative_path in record.files:
        if (store / relative_path).is_file():
            _move(store / relative_path, root / relative_path)
    record_path(project).unlink(missing_ok=True)
    shutil.rmtree(project.folder / STORE, ignore_errors=True)
    return len(record.files)
