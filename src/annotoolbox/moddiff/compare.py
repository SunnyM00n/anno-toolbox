"""The five questions as reports: a mod against the game, a profile's mods against each other, two versions of a mod, two
mods together, and an archive or a folder of loose files against the game. Each returns a ``model.SReport``.

The mods are run by the loader's rules (``effect.run_mods``) in the order ``modloader.resolve`` gives them, with the
nested mods and the mods a mod requires that sit beside it. ``docs/mod-diff.md`` "The five questions".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..assetedit.assetgame import CGame
from ..infra.i18n import tr
from ..modloader.engine import WARNING
from ..modloader.mods import CMod, CVariables, find_mods, read_options
from ..modloader.resolve import resolve
from .archivediff import compare_members, members_of
from .conflicts import find_conflicts
from .effect import SEffect, guid_key, run_mods
from .elementdata import data_changes
from .filechanges import read_source
from .model import ADDED, DIFFERS, ONLY_A, ONLY_B, REMOVED, CTarget, SChange, SFileChange, SReport

Progress = Callable[[str], None] | None
NOTED = (
    "modcondition",
    "duplicate",
)  # the loader's info lines the report keeps: a condition on another mod, a GUID again


def _report(effect: SEffect, title: str, left: str = "", right: str = "") -> SReport:
    """A report of a run: the targets and files against the game, the conflicts, what the loader would drop."""
    report = SReport(title, effect.game, left, right)
    report.mods = [step.label for step in effect.steps]
    report.targets, report.files = effect.targets, effect.files
    report.conflicts = find_conflicts(effect.steps)
    report.dropped = [event.line() for event in effect.events if event.level == WARNING]
    report.notes = [event.line() for event in effect.events if event.code in NOTED]
    return report


def run_set(game: CGame, order: list[CMod], mods_dir: Path | None, progress: Progress = None) -> SEffect:
    """``order`` run in this order with the mods' options (``active-options.jsonc`` of ``mods_dir``)."""
    return run_mods(game, order, CVariables(order, read_options(mods_dir)), progress)


def resolved(mods: list[CMod], search: list[Path] | None = None, allow_zips: bool = False) -> list[CMod]:
    """``mods`` with the mods nested in them and, for each, the mods it requires that sit in ``search`` (default: the
    mod's neighbours), in the loader's order."""
    roots = search if search is not None else sorted({mod.home for mod in mods})
    top = {mod.m_folder.resolve() for mod in mods}
    others = [mod for mod in find_mods(roots, mods[0].m_anno, allow_zips)[0] if mod.m_folder.resolve() not in top]
    return resolve(mods, others, allow_pull=True).order


def _zips(game: CGame) -> bool:
    """Whether the game's loader reads ``.zip`` files as mods (1800's does)."""
    return game.m_layout.key == "anno1800"


def mod_against_game(game: CGame, mod: CMod, search: list[Path] | None = None, progress: Progress = None) -> SReport:
    """One mod (with what comes with it) against the game."""
    order = resolved([mod], search, _zips(game))
    effect = run_set(game, order, mod.home, progress)
    return _report(effect, tr("moddiff.mod_against_game_title", mod=mod.m_mod_id), tr("ui.game"), mod.m_mod_id)


def profile_conflicts(
    game: CGame,
    order: list[CMod],
    mods_dir: Path | None,
    profile: str,
    progress: Progress = None,
    notes: list[str] | None = None,
) -> SReport:
    """The mods a profile switches on (``order``: ``modmgr.checks.SPlan.order``), run as the loader runs them;
    ``notes``: what the caller knows about how the set was made (1800's activations, ``.zip`` mods)."""
    effect = run_set(game, order, mods_dir, progress)
    report = _report(effect, tr("mods.profile_name", name=profile), tr("ui.game"), profile)
    report.notes = [*(notes or []), *report.notes]
    return report


def two_mods(
    game: CGame, first_mod: CMod, second_mod: CMod, search: list[Path] | None = None, progress: Progress = None
) -> SReport:
    """``a`` and ``b`` together, in the loader's order (a ``LoadAfter`` decides), against the game and each other."""
    order = resolved([first_mod, second_mod], search, _zips(game))
    effect = run_set(game, order, first_mod.home, progress)
    return _report(
        effect,
        tr("moddiff.a_and_b", a=first_mod.m_mod_id, b=second_mod.m_mod_id),
        tr("ui.game"),
        f"{first_mod.m_mod_id} + {second_mod.m_mod_id}",
    )


def archive_effect(game: CGame, path: Path, progress: Progress = None) -> SEffect:
    """An archive mod or a folder of loose files compared with the game's files, as one step."""
    step = compare_members(game, members_of(path, game), Path(path).name, progress)
    return SEffect(
        game.m_layout.key, [step], sorted(step.targets.values(), key=lambda target: target.ident), step.files
    )


def archive_against_game(game: CGame, path: Path, progress: Progress = None) -> SReport:
    """An archive mod or a folder of loose files against the game's files."""
    return _report(archive_effect(game, path, progress), Path(path).name, tr("ui.game"), Path(path).name)


def two_archives(game: CGame, first_path: Path, second_path: Path, progress: Progress = None) -> SReport:
    """Two archives (or folders of loose files), each against the game's files: what differs between the results."""
    names = (Path(first_path).name, Path(second_path).name)
    if names[0] == names[1]:  # the same file name in two folders: the folder tells them apart
        names = (f"{Path(first_path).parent.name}/{names[0]}", f"{Path(second_path).parent.name}/{names[1]}")
    return versions_report(
        archive_effect(game, first_path, progress), archive_effect(game, second_path, progress), *names
    )


def two_versions(
    game: CGame, first_mod: CMod, second_mod: CMod, search: list[Path] | None = None, progress: Progress = None
) -> SReport:
    """Two versions of a mod, each run alone on the game: what differs between the results."""
    one = run_set(game, resolved([first_mod], search, _zips(game)), first_mod.home, progress)
    two = run_set(game, resolved([second_mod], search, _zips(game)), second_mod.home, progress)
    label_a, label_b = (
        f"{first_mod.m_mod_id} {first_mod.m_version}".strip(),
        f"{second_mod.m_mod_id} {second_mod.m_version}".strip(),
    )
    return versions_report(one, two, label_a, label_b)


def versions_report(first: SEffect, second: SEffect, left: str, right: str) -> SReport:
    """The difference of two runs: targets in one only, targets that differ, files that differ."""
    report = SReport(tr("moddiff.a_and_b", a=left, b=right), first.game, left, right, [left, right])
    report.targets = diff_targets(first.targets, second.targets)
    report.files = diff_files(first.files, second.files)
    report.dropped = [f"{left}: {event.line()}" for event in first.events if event.level == WARNING]
    report.dropped += [f"{right}: {event.line()}" for event in second.events if event.level == WARNING]
    report.notes = [
        f"{side}: {event.line()}"
        for side, run in ((left, first), (right, second))
        for event in run.events
        if event.code in NOTED
    ]
    return report


def diff_targets(first_targets: list[CTarget], second_targets: list[CTarget]) -> list[CTarget]:
    """Targets by identity: in one run only, or in both with different results (the value in each run)."""
    left, right = (
        {target.ident: target for target in first_targets},
        {target.ident: target for target in second_targets},
    )
    out = []
    for ident in sorted(left.keys() | right.keys(), key=lambda i: (i[0], guid_key(i[1]))):
        one, two = left.get(ident), right.get(ident)
        if two is None:
            out.append(_copy(one, ONLY_A))
        elif one is None:
            out.append(_copy(two, ONLY_B))
        elif changes := _differing(one, two):
            out.append(CTarget(*ident, DIFFERS, two.m_name or one.m_name, two.m_template or one.m_template, changes))
    return out


def _copy(target: CTarget, kind: str) -> CTarget:
    return CTarget(
        target.m_file,
        target.m_key,
        kind,
        target.m_name,
        target.m_template,
        list(target.m_changes),
        list(target.m_mods),
        target.m_packed,
    )


def _differing(one: CTarget, two: CTarget) -> list[SChange]:
    """Where two runs' results for one target differ, as ``path: value in the first -> value in the second``."""
    if one.m_kind != two.m_kind:
        return [SChange("(kind)", one.m_kind, two.m_kind)]
    if one.m_kind == REMOVED:
        return []
    if one.m_kind == ADDED and (one.m_packed or two.m_packed):  # an added asset: its values; an added text: its change
        return data_changes(one.values(), two.values()) if one.m_packed != two.m_packed else []
    first, second = {change.path: change for change in one.m_changes}, {change.path: change for change in two.m_changes}
    out = []
    for path in sorted(first.keys() | second.keys()):
        x, y = first.get(path), second.get(path)
        value_a = x.after if x else y.before  # a side that did not change it holds the game's value
        value_b = y.after if y else x.before
        if value_a != value_b:
            out.append(SChange(path, value_a, value_b))
    return out


def diff_files(first_files: list[SFileChange], second_files: list[SFileChange]) -> list[SFileChange]:
    """Files carried by one run only, or by both with different content."""
    left, right = (
        {file_change.path: file_change for file_change in first_files},
        {file_change.path: file_change for file_change in second_files},
    )
    out = []
    for path in sorted(left.keys() | right.keys()):
        one, two = left.get(path), right.get(path)
        if two is None:
            out.append(SFileChange(path, ONLY_A, one.size_bytes, one.mods, one.digest, one.source))
        elif one is None:
            out.append(SFileChange(path, ONLY_B, two.size_bytes, two.mods, two.digest, two.source))
        elif not _same_content(one, two):
            out.append(SFileChange(path, DIFFERS, two.size_bytes, [*one.mods, *two.mods], two.digest, two.source))
    return out


def _same_content(one: SFileChange, two: SFileChange) -> bool:
    if one.size_bytes != two.size_bytes:
        return False
    if one.digest and two.digest:
        return one.digest == two.digest
    try:
        return read_source(one.source) == read_source(two.source)
    except (OSError, KeyError):
        return True  # unreadable: not told apart
