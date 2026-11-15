"""The five questions as reports: a mod against the game, a profile's mods against each other, two versions of a mod, two
mods together, and an archive or a folder of loose files against the game. Each returns a ``model.Report``.

The mods are run by the loader's rules (``effect.run_mods``) in the order ``modloader.resolve`` gives them, with the
nested mods and the mods a mod requires that sit beside it. ``docs/mod-diff.md`` "The five questions".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..assetedit.assetgame import Game
from ..infra.i18n import tr
from ..modloader.engine import WARNING
from ..modloader.mods import Mod, Variables, find_mods, read_options
from ..modloader.resolve import resolve
from .archivediff import compare_members, members_of
from .conflicts import find_conflicts
from .effect import Effect, guid_key, run_mods
from .elementdata import data_changes
from .filechanges import read_source
from .model import ADDED, DIFFERS, ONLY_A, ONLY_B, REMOVED, Change, FileChange, Report, Target

Progress = Callable[[str], None] | None
NOTED = (
    "modcondition",
    "duplicate",
)  # the loader's info lines the report keeps: a condition on another mod, a GUID again


def _report(effect: Effect, title: str, left: str = "", right: str = "") -> Report:
    """A report of a run: the targets and files against the game, the conflicts, what the loader would drop."""
    report = Report(title, effect.game, left, right)
    report.mods = [s.label for s in effect.steps]
    report.targets, report.files = effect.targets, effect.files
    report.conflicts = find_conflicts(effect.steps)
    report.dropped = [e.line() for e in effect.events if e.level == WARNING]
    report.notes = [e.line() for e in effect.events if e.code in NOTED]
    return report


def run_set(game: Game, order: list[Mod], mods_dir: Path | None, progress: Progress = None) -> Effect:
    """``order`` run in this order with the mods' options (``active-options.jsonc`` of ``mods_dir``)."""
    return run_mods(game, order, Variables(order, read_options(mods_dir)), progress)


def resolved(mods: list[Mod], search: list[Path] | None = None, zips: bool = False) -> list[Mod]:
    """``mods`` with the mods nested in them and, for each, the mods it requires that sit in ``search`` (default: the
    mod's neighbours), in the loader's order."""
    roots = search if search is not None else sorted({m.home for m in mods})
    top = {m.folder.resolve() for m in mods}
    others = [m for m in find_mods(roots, mods[0].anno, zips)[0] if m.folder.resolve() not in top]
    return resolve(mods, others, pull=True).order


def _zips(game: Game) -> bool:
    """Whether the game's loader reads ``.zip`` files as mods (1800's does)."""
    return game.layout.key == "anno1800"


def mod_against_game(game: Game, mod: Mod, search: list[Path] | None = None, progress: Progress = None) -> Report:
    """One mod (with what comes with it) against the game."""
    order = resolved([mod], search, _zips(game))
    effect = run_set(game, order, mod.home, progress)
    return _report(effect, tr("{mod} against the game", mod=mod.mod_id), tr("game"), mod.mod_id)


def profile_conflicts(
    game: Game,
    order: list[Mod],
    mods_dir: Path | None,
    profile: str,
    progress: Progress = None,
    notes: list[str] | None = None,
) -> Report:
    """The mods a profile switches on (``order``: ``modmgr.checks.Plan.order``), run as the loader runs them;
    ``notes``: what the caller knows about how the set was made (1800's activations, ``.zip`` mods)."""
    effect = run_set(game, order, mods_dir, progress)
    report = _report(effect, tr("profile {name}", name=profile), tr("game"), profile)
    report.notes = [*(notes or []), *report.notes]
    return report


def two_mods(game: Game, a: Mod, b: Mod, search: list[Path] | None = None, progress: Progress = None) -> Report:
    """``a`` and ``b`` together, in the loader's order (a ``LoadAfter`` decides), against the game and each other."""
    order = resolved([a, b], search, _zips(game))
    effect = run_set(game, order, a.home, progress)
    return _report(effect, tr("{a} and {b}", a=a.mod_id, b=b.mod_id), tr("game"), f"{a.mod_id} + {b.mod_id}")


def archive_effect(game: Game, path: Path, progress: Progress = None) -> Effect:
    """An archive mod or a folder of loose files compared with the game's files, as one step."""
    step = compare_members(game, members_of(path, game), Path(path).name, progress)
    return Effect(game.layout.key, [step], sorted(step.targets.values(), key=lambda t: t.ident), step.files)


def archive_against_game(game: Game, path: Path, progress: Progress = None) -> Report:
    """An archive mod or a folder of loose files against the game's files."""
    return _report(archive_effect(game, path, progress), Path(path).name, tr("game"), Path(path).name)


def two_archives(game: Game, a: Path, b: Path, progress: Progress = None) -> Report:
    """Two archives (or folders of loose files), each against the game's files: what differs between the results."""
    names = (Path(a).name, Path(b).name)
    if names[0] == names[1]:  # the same file name in two folders: the folder tells them apart
        names = (f"{Path(a).parent.name}/{names[0]}", f"{Path(b).parent.name}/{names[1]}")
    return versions_report(archive_effect(game, a, progress), archive_effect(game, b, progress), *names)


def two_versions(game: Game, a: Mod, b: Mod, search: list[Path] | None = None, progress: Progress = None) -> Report:
    """Two versions of a mod, each run alone on the game: what differs between the results."""
    one = run_set(game, resolved([a], search, _zips(game)), a.home, progress)
    two = run_set(game, resolved([b], search, _zips(game)), b.home, progress)
    label_a, label_b = f"{a.mod_id} {a.version}".strip(), f"{b.mod_id} {b.version}".strip()
    return versions_report(one, two, label_a, label_b)


def versions_report(a: Effect, b: Effect, left: str, right: str) -> Report:
    """The difference of two runs: targets in one only, targets that differ, files that differ."""
    report = Report(tr("{a} and {b}", a=left, b=right), a.game, left, right, [left, right])
    report.targets = diff_targets(a.targets, b.targets)
    report.files = diff_files(a.files, b.files)
    report.dropped = [f"{left}: {e.line()}" for e in a.events if e.level == WARNING]
    report.dropped += [f"{right}: {e.line()}" for e in b.events if e.level == WARNING]
    report.notes = [
        f"{side}: {e.line()}" for side, run in ((left, a), (right, b)) for e in run.events if e.code in NOTED
    ]
    return report


def diff_targets(a: list[Target], b: list[Target]) -> list[Target]:
    """Targets by identity: in one run only, or in both with different results (the value in each run)."""
    left, right = {t.ident: t for t in a}, {t.ident: t for t in b}
    out = []
    for ident in sorted(left.keys() | right.keys(), key=lambda i: (i[0], guid_key(i[1]))):
        one, two = left.get(ident), right.get(ident)
        if two is None:
            out.append(_copy(one, ONLY_A))
        elif one is None:
            out.append(_copy(two, ONLY_B))
        elif changes := _differing(one, two):
            out.append(Target(*ident, DIFFERS, two.name or one.name, two.template or one.template, changes))
    return out


def _copy(target: Target, kind: str) -> Target:
    return Target(
        target.file,
        target.key,
        kind,
        target.name,
        target.template,
        list(target.changes),
        list(target.mods),
        target.packed,
    )


def _differing(one: Target, two: Target) -> list[Change]:
    """Where two runs' results for one target differ, as ``path: value in the first -> value in the second``."""
    if one.kind != two.kind:
        return [Change("(kind)", one.kind, two.kind)]
    if one.kind == REMOVED:
        return []
    if one.kind == ADDED:
        return data_changes(one.values(), two.values()) if one.packed != two.packed else []
    first, second = {c.path: c for c in one.changes}, {c.path: c for c in two.changes}
    out = []
    for path in sorted(first.keys() | second.keys()):
        x, y = first.get(path), second.get(path)
        value_a = x.after if x else y.before  # a side that did not change it holds the game's value
        value_b = y.after if y else x.before
        if value_a != value_b:
            out.append(Change(path, value_a, value_b))
    return out


def diff_files(a: list[FileChange], b: list[FileChange]) -> list[FileChange]:
    """Files carried by one run only, or by both with different content."""
    left, right = {f.path: f for f in a}, {f.path: f for f in b}
    out = []
    for path in sorted(left.keys() | right.keys()):
        one, two = left.get(path), right.get(path)
        if two is None:
            out.append(FileChange(path, ONLY_A, one.size, one.mods, one.digest, one.source))
        elif one is None:
            out.append(FileChange(path, ONLY_B, two.size, two.mods, two.digest, two.source))
        elif not _same_content(one, two):
            out.append(FileChange(path, DIFFERS, two.size, [*one.mods, *two.mods], two.digest, two.source))
    return out


def _same_content(one: FileChange, two: FileChange) -> bool:
    if one.size != two.size:
        return False
    if one.digest and two.digest:
        return one.digest == two.digest
    try:
        return read_source(one.source) == read_source(two.source)
    except (OSError, KeyError):
        return True  # unreadable: not told apart
