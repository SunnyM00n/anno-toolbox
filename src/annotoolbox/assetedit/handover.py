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
from ..modloader.mods import GAME_NUMBER, MODINFO_NAMES, Mod, Variables, find_mods, read_mod
from ..modloader.resolve import resolve
from ..suite import appfolders, carriedpart, importmeta
from ..suite import project as projects
from ..textedit.editfile import FOLDER as TEXT_EDITS
from . import modimport
from .assetgame import Game
from .changeset import ChangeSet
from .projectpart import change_set_path

RECORD = "handover.json"  # in the project folder: what was taken (absent: nothing was)
STORE = "handover"  # the originals' folder: ``handover/<game>/<mod path>``, and the change set that was there before
STAGE = ".handover-stage"  # the mod's ModOps, copied here for the loader's reading (never stays)
BEFORE = "change-set.before.json"
SUPPORTED = ("anno117", "anno1800")
INCLUDE_SUFFIX = ".include.xml"
INCLUDE = re.compile(rb"""<Include\b[^>]*?\bFile\s*=\s*["']([^"']*)["']""")
SHOWN = 12


class HandoverRefused(ValueError):
    """The hand-over cannot be made or undone; the message says why in a sentence."""


@dataclass
class Plan:
    """What a hand-over would do, read and not done: the files it takes (as the mod spells them), the include files that
    go with them, the patch files that stay carried, and the lines of the lossy report."""

    taken: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    imported: modimport.Imported | None = None

    def lines(self) -> list[str]:
        r = self.imported
        out = [tr("assets.handover_takes", n=len(self.taken) + len(self.includes), list=names(self.taken))]
        if r is not None:
            out.append(
                tr(
                    "assets.handover_counts",
                    added=number(len(r.added)),
                    changed=number(len(r.changed)),
                    removed=number(len(r.removed)),
                    texts=number(sum(r.texts.values())),
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
class Record:
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


def record_path(project: projects.Project) -> Path:
    return project.folder / RECORD


def store_root(project: projects.Project) -> Path:
    return project.folder / STORE / project.game


def text_edits_path(project: projects.Project) -> Path:
    return project.folder / TEXT_EDITS / f"{project.game}.json"


def mods_folders(settings, game: str) -> list[Path]:
    """The folders that may hold the mods an imported mod requires: the game's mods folder, when set."""
    folder = settings.mods if game == "anno117" else getattr(settings, "mods1800", "") if game == "anno1800" else ""
    return [Path(folder)] if folder else []


def state(project: projects.Project) -> Record | None:
    """The hand-over the project holds, None when none was taken."""
    try:
        data = json.loads(record_path(project).read_text(encoding="utf-8"))
        return Record(**{k: v for k, v in data.items() if k in Record.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return None


def can_take(project: projects.Project) -> bool:
    """Whether the Project window offers the hand-over: an imported 117 or 1800 project with none taken yet."""
    return (
        project.game in SUPPORTED
        and importmeta.read_meta(project.folder) is not None
        and carriedpart.carried_root(project).is_dir()
        and state(project) is None
        and appfolders.layered_record(project) is None
    )


def edited_since(project: projects.Project) -> list[str]:
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
def patch_files(project: projects.Project, layout, files: dict[str, int]) -> tuple[list[str], list[str]]:
    """``(taken, kept)``: the carried files the change set writes itself (the asset file's patch, each language's text
    patch) and the other patch files of the mod, which no form takes."""
    taken, kept = [], []
    head, _, tail = (layout.texts or "").partition("{language}")
    for rel in sorted(files):
        low = rel.lower()
        if low == layout.patch_target.lower() or (
            layout.texts and low.startswith(head.lower()) and low.endswith(tail.lower()) and "/" not in low[len(head) :]
        ):
            if low[len(head) : len(low) - len(tail)] != "metadata":
                taken.append(rel)
                continue
        if low.endswith(".xml") and not low.endswith(INCLUDE_SUFFIX):
            kept.append(rel)
    return taken, kept


def _closure(root: Path, by_low: dict[str, str], start: list[str]) -> set[str]:
    """Every file the files ``start`` ``Include`` (and those include in turn), as the carried folder spells them."""
    seen: set[str] = set()
    todo = list(start)
    while todo:
        rel = todo.pop()
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        for ref in INCLUDE.findall(data):
            text = ref.decode("utf-8", "replace")
            joined = text.lstrip("/") if text.startswith("/") else posixpath.join(posixpath.dirname(rel), text)
            found = by_low.get(posixpath.normpath(joined).lower())
            if found and found not in seen:
                seen.add(found)
                todo.append(found)
    return seen


def _stage(project: projects.Project, meta: importmeta.Meta, root: Path, files: dict[str, int], into: Path) -> Path:
    """A mod folder the loader can read, made of the carried ModOps (every ``.xml``) and every ``modinfo`` (the nested
    mods' too), with the mod's own ``modinfo`` from the import: copies, so nothing of the project is touched."""
    mod = into / "mod"
    mod.mkdir(parents=True)
    (mod / meta.modinfo).write_bytes((project.folder / importmeta.MOD_FILES / importmeta.MODINFO_ORIGINAL).read_bytes())
    for rel in files:
        name = rel.rsplit("/", 1)[-1].lower()
        if rel.lower().endswith(".xml") or name in MODINFO_NAMES:
            target = mod / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / rel, target)
    return mod


def _static_losses(root: Path, plan: Plan, includes: list[str], mod) -> None:
    """What the net change set cannot keep that the files show on their face (comments, conditions, includes, options)."""
    for rel in plan.taken:
        text = (root / rel).read_bytes().decode("utf-8", "replace")
        for needle, key in (
            ("<!--", N_("assets.handover_lost_comments")),
            ("Condition=", N_("assets.handover_lost_conditions")),
            ("Skip=", N_("assets.handover_lost_skip")),
            ("MaxRepeat=", N_("assets.handover_lost_skip")),
        ):
            if n := text.count(needle):
                plan.lost.append(tr(key, n=number(n), file=rel))
    if includes:
        plan.lost.append(tr("assets.handover_lost_includes", list=names(includes)))
    if isinstance(mod.info.get("Options"), dict) and mod.info["Options"]:
        plan.lost.append(tr("assets.handover_lost_options"))


def read_carried(
    project: projects.Project,
    game: Game,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
) -> tuple[modimport.Imported, Mod]:
    """The carried ModOps files read as the import reads a mod (a copy of them staged for the loader, removed again),
    applied over the installed ``game`` with the mods the mod requires found in ``search``: the import's result and
    the mod. Raises ``HandoverRefused`` when the loader would not read the mod."""
    meta = importmeta.read_meta(project.folder)
    if meta is None:
        raise HandoverRefused(tr("assets.handover_not_imported"))
    root = carriedpart.carried_root(project)
    stage = project.folder / STAGE
    shutil.rmtree(stage, ignore_errors=True)
    try:
        mod_folder = _stage(project, meta, root, carriedpart.files_of(root), stage)
        mod, why = read_mod(mod_folder, GAME_NUMBER.get(project.game))
        if mod is None:
            raise HandoverRefused(why)
        others = [m for m in find_mods(search or [], mod.anno)[0] if m.mod_id != mod.mod_id]
        res = resolve([mod], others, pull=True)
        return modimport.import_mods(game, res.order, Variables(res.order, {}), say, resolution=res, primary=mod), mod
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def plan(
    project: projects.Project,
    game: Game,
    say: Callable[[str], None] = lambda _t: None,
    search: list[Path] | None = None,
) -> Plan:
    """What handing the project's patch files over would do and lose, read from the carried files and the installed
    ``game`` (nothing is changed). ``search``: the mods folders that hold the mods this one requires. Raises
    ``HandoverRefused``."""
    meta = importmeta.read_meta(project.folder)
    if meta is None:
        raise HandoverRefused(tr("assets.handover_not_imported"))
    if project.game not in SUPPORTED or game.layout.key != project.game:
        raise HandoverRefused(tr("assets.handover_game", game=project.game))
    if state(project) is not None:
        raise HandoverRefused(tr("assets.handover_already"))
    if appfolders.layered_record(project) is not None:
        raise HandoverRefused(tr("assets.handover_layered"))
    held = ChangeSet.load(change_set_path(project)) if change_set_path(project).is_file() else None
    if held is not None and (held.assets or held.new or held.removed or held.texts):
        raise HandoverRefused(tr("assets.handover_has_work", n=len(held.assets) + len(held.new) + len(held.removed)))
    root = carriedpart.carried_root(project)
    files = carriedpart.files_of(root)
    out = Plan()
    out.taken, out.kept = patch_files(project, game.layout, files)
    if not out.taken:
        raise HandoverRefused(tr("assets.handover_nothing", path=game.layout.patch_target))
    by_low = {rel.lower(): rel for rel in files}
    mine = _closure(root, by_low, out.taken)
    elsewhere = _closure(root, by_low, [r for r in files if r.lower().endswith(".xml") and r not in out.taken])
    out.includes = sorted(r for r in mine if r.lower().endswith(INCLUDE_SUFFIX) and r not in elsewhere)
    shared = sorted(mine & elsewhere)
    out.imported, mod = read_carried(project, game, say, search)
    _static_losses(root, out, out.includes, mod)
    r = out.imported
    out.lost.insert(0, tr("assets.handover_lost_layout"))
    out.lost += [e.line() for e in r.conditional]
    out.lost += r.lost
    out.lost += [tr("assets.handover_dropped", line=e.line()) for e in r.dropped]
    if shared:
        out.lost.append(tr("assets.handover_include_shared", list=names(shared)))
    silent = [
        rel for rel in out.taken if _language_of(game.layout, rel) and _language_of(game.layout, rel) not in r.texts
    ]
    out.lost += [tr("assets.handover_text_dropped", file=rel) for rel in silent]
    out.lost.append(tr("assets.handover_roundtrip"))
    return out


def _language_of(layout, rel: str) -> str:
    head, _, tail = (layout.texts or "").partition("{language}")
    low = rel.lower()
    if layout.texts and low.startswith(head.lower()) and low.endswith(tail.lower()):
        return low[len(head) : len(low) - len(tail)]
    return ""


# taking and undoing
def _move(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)


def take(project: projects.Project, made: Plan) -> Record:
    """Hand the files of ``made`` over: the change set into the project (the project's identity and GUID range on it),
    the files into the undo store. Raises ``HandoverRefused`` for a project that changed since ``plan``; a failure
    moves everything back."""
    if made.imported is None or state(project) is not None:
        raise HandoverRefused(tr("assets.handover_already"))
    if appfolders.layered_record(project) is not None:
        raise HandoverRefused(tr("assets.handover_layered"))
    root, store = carriedpart.carried_root(project), store_root(project)
    changes = made.imported.changes
    changes.carry = []  # the carried part supplies every file the change set does not write
    changes.mod_id, changes.mod_name, changes.version = project.mod_id, project.name, project.version
    changes.guid_range, changes.guid_choice, changes.guid_source = (
        tuple(project.guid_range), project.guid_choice, project.guid_source,
    )  # fmt: skip
    target = change_set_path(project)
    record = Record(project.game, [*made.taken, *made.includes], had_change_set=target.is_file())
    if record.had_change_set:
        _move(target, project.folder / STORE / BEFORE)
    moved: list[str] = []
    try:
        for rel in record.files:
            if not (root / rel).is_file():
                raise HandoverRefused(tr("assets.handover_file_gone", file=rel))
            _move(root / rel, store / rel)
            moved.append(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        changes.save(target)
        record.change_set_sha, record.texts_sha = _sha(target), _sha(text_edits_path(project))
        write_atomic(record_path(project), (json.dumps(asdict(record), indent=1) + "\n").encode("utf-8"))
    except BaseException:
        for rel in moved:
            _move(store / rel, root / rel)
        target.unlink(missing_ok=True)
        if record.had_change_set:
            _move(project.folder / STORE / BEFORE, target)
        raise
    return record


def undo(project: projects.Project, discard: bool = False) -> int:
    """Put the handed-over files back into ``mod-files`` and remove the change set the hand-over made: the build is
    byte-identical to the mod again. Edits made in the apps since are lost, so ``HandoverRefused`` names them unless
    ``discard``. Returns the number of files back."""
    record = state(project)
    if record is None:
        raise HandoverRefused(tr("assets.handover_none"))
    changed = edited_since(project)
    if changed and not discard:
        raise HandoverRefused(tr("assets.handover_edited", what=", ".join(changed)))
    root, store = carriedpart.carried_root(project), store_root(project)
    for rel in record.files:
        if (root / rel).exists() and (store / rel).is_file():
            raise HandoverRefused(tr("assets.handover_in_the_way", file=rel))
    target = change_set_path(project)
    target.unlink(missing_ok=True)
    before = project.folder / STORE / BEFORE
    if before.is_file():
        _move(before, target)
    for rel in record.files:
        if (store / rel).is_file():
            _move(store / rel, root / rel)
    record_path(project).unlink(missing_ok=True)
    shutil.rmtree(project.folder / STORE, ignore_errors=True)
    return len(record.files)
