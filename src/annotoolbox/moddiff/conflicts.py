"""Where the steps of several mods meet: the same value changed twice, a block replaced under a changed value, an asset
removed and used, an asset added twice, a file carried twice, an asset several mods change in different values.

``find_conflicts(steps)`` takes the ``SModEffect``s of a run in load order and returns the ``SConflict``s, the last mod of
each one the winner and what put it last (``decided``). The kinds and their levels are in ``docs/mod-diff.md``
"Conflicts".
"""

from __future__ import annotations

from .effect import SModEffect, guid_key
from .model import (
    ADDED,
    ASSET,
    BLOCK,
    BYTES,
    CHANGED,
    CONFLICT,
    DUPLICATE,
    FILE,
    GONE,
    LATE_PHASE,
    LOAD_AFTER,
    NAME_ORDER,
    ORDER,
    OVERLAP,
    REMOVED,
    SET_VALUE,
    VALUE,
    VALUES,
    CTarget,
    SConflict,
)


def find_conflicts(steps: list[SModEffect]) -> list[SConflict]:
    """Every conflict of the run's steps (``Effect.steps``), files after targets, each part in file and key order."""
    if len(steps) < 2:  # one mod meets nobody
        return []
    users: dict[tuple[str, str], list[tuple[SModEffect, CTarget | None]]] = {}
    for step in steps:
        for ident, target in step.targets.items():
            users.setdefault(ident, []).append((step, target))
        for ident in step.writes.keys() - step.targets.keys():  # set the value that was there: a user, no target
            users.setdefault(ident, []).append((step, None))
    out: list[SConflict] = []
    for ident in sorted(users, key=lambda i: (i[0], guid_key(i[1]))):
        if len(users[ident]) > 1:
            out += _of_target(ident, users[ident])
    return out + _of_files(steps)


def decided_by(losers: list[SModEffect], winner: SModEffect) -> str:
    """What made ``winner`` come after ``losers``: the late phase, a ``LoadAfter``, or the loader's default order."""
    mod = winner.mod
    if mod is None:
        return ORDER
    if mod.late and any(not (mod_effect.mod and mod_effect.mod.late) for mod_effect in losers):
        return LATE_PHASE
    if any(mod_effect.mod_id in mod.m_load_after for mod_effect in losers):
        return LOAD_AFTER
    return NAME_ORDER


def _make(kind: str, level: str, target: CTarget, path: str, who: list[SModEffect], values: list) -> SConflict:
    return SConflict(
        kind, level, target.m_file, target.m_key, target.m_name, path,
        tuple(mod_effect.mod_id for mod_effect in who), tuple(values), who[-1].mod_id, decided_by(who[:-1], who[-1]),
    )  # fmt: skip


def _of_target(ident: tuple[str, str], users: list[tuple[SModEffect, CTarget | None]]) -> list[SConflict]:
    real = [
        (mod_effect, user_target) for mod_effect, user_target in users if user_target is not None
    ]  # none: every mod only set the value that was there
    name = next((user_target.m_name for _mod_effect, user_target in reversed(real) if user_target.m_name), "")
    head = CTarget(ident[0], ident[1], real[-1][1].m_kind if real else CHANGED, name)
    out: list[SConflict] = []
    added = [(mod_effect, user_target) for mod_effect, user_target in real if user_target.m_kind == ADDED]
    if len(added) > 1:
        out.append(
            _make(
                DUPLICATE, CONFLICT, head, "", [mod_effect for mod_effect, _user_target in added], [ADDED] * len(added)
            )
        )
    removed = [(mod_effect, user_target) for mod_effect, user_target in real if user_target.m_kind == REMOVED]
    if removed and len(removed) < len(users):
        who = [mod_effect for mod_effect, _user_target in users]
        out.append(_make(GONE, CONFLICT, head, "", who, [_what(user_target) for _mod_effect, user_target in users]))
    if len(users) > 1 and (not real or any(user_target.m_kind == CHANGED for _mod_effect, user_target in real)):
        out += _of_values(head, users)
    return out


def _what(target: CTarget | None) -> str:
    if target is None:
        return SET_VALUE
    return target.m_kind if target.m_kind in (ADDED, REMOVED) else f"{len(target.m_changes)} {VALUES}"


def _paths(step: SModEffect, target: CTarget | None, ident: tuple[str, str]) -> dict[str, str | None]:
    """The value paths one mod's step reached on a target and what it left there: what it changed (its value after),
    and what it set to the value that was there."""
    out = dict(step.writes.get(ident, {}))
    if target is not None and target.m_kind == CHANGED:
        out.update({change.path: change.after for change in target.m_changes})
    return out


def _of_values(head: CTarget, users: list[tuple[SModEffect, CTarget | None]]) -> list[SConflict]:
    by_path: dict[str, list[tuple[SModEffect, str | None]]] = {}
    for step, target in users:
        for path, value in _paths(step, target, head.ident).items():
            by_path.setdefault(path, []).append((step, value))
    changing = [
        (mod_effect, user_target)
        for mod_effect, user_target in users
        if user_target is not None and user_target.m_kind == CHANGED
    ]
    out = []
    for path, who in by_path.items():
        if len({mod_effect.mod_id for mod_effect, _value in who}) > 1:
            values = [after_value for _mod_effect, after_value in who]
            level = CONFLICT if len(set(values)) > 1 else OVERLAP  # the same value twice: nothing is lost
            out.append(_make(VALUE, level, head, path, [mod_effect for mod_effect, _value in who], values))
    for outer in by_path:  # a block replaced or removed above a value another mod changed
        for inner in by_path:
            if outer != inner and _above(outer, inner):
                mine, theirs = by_path[outer], by_path[inner]
                who = [*mine, *theirs]
                if len({mod_effect.mod_id for mod_effect, _value in who}) > 1:
                    order = sorted(who, key=lambda pair: _position(users, pair[0]))
                    out.append(
                        _make(
                            BLOCK,
                            CONFLICT,
                            head,
                            outer,
                            [mod_effect for mod_effect, _value in order],
                            [after_value for _mod_effect, after_value in order],
                        )
                    )
    if not out and len(changing) > 1:
        mods = [mod_effect for mod_effect, _user_target in changing]
        counts = [f"{len(user_target.m_changes)} {VALUES}" for _mod_effect, user_target in changing]
        out.append(_make(ASSET, OVERLAP, head, "", mods, counts))
    return out


def _above(outer: str, inner: str) -> bool:
    """Whether ``outer`` is a block that holds ``inner`` (``Costs`` and ``Costs/Item[0]/Amount``, ``Costs[+]``)."""
    return inner.startswith((outer + "/", outer + "["))


def _position(users: list[tuple[SModEffect, CTarget | None]], step: SModEffect) -> int:
    return next(k for k, (mod_effect, _user_target) in enumerate(users) if mod_effect is step)


def _of_files(steps: list[SModEffect]) -> list[SConflict]:
    carried: dict[str, list[tuple[SModEffect, int, str]]] = {}
    for step in steps:
        for change in step.files:
            carried.setdefault(change.path, []).append((step, change.size_bytes, change.digest))
    out = []
    for path in sorted(carried):
        who = carried[path]
        if len(who) > 1:
            steps_ = [mod_effect for mod_effect, _size_bytes, _digest in who]
            same = len({digest for _mod_effect, _size_bytes, digest in who}) == 1 and bool(
                who[0][2]
            )  # one content: nothing is lost
            out.append(
                SConflict(FILE, OVERLAP if same else CONFLICT, path, "", "", "", tuple(mod_effect.mod_id for mod_effect in steps_),
                         tuple(f"{size_bytes} {BYTES}" for _mod_effect, size_bytes, _digest in who), steps_[-1].mod_id, decided_by(steps_[:-1], steps_[-1]))
            )  # fmt: skip
    return out
