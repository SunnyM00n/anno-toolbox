"""Where the steps of several mods meet: the same value changed twice, a block replaced under a changed value, an asset
removed and used, an asset added twice, a file carried twice, an asset several mods change in different values.

``find_conflicts(steps)`` takes the ``ModEffect``s of a run in load order and returns the ``Conflict``s, the last mod of
each one the winner and what put it last (``decided``). The kinds and their levels are in ``docs/mod-diff.md``
"Conflicts".
"""

from __future__ import annotations

from .effect import ModEffect, guid_key
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
    Conflict,
    Target,
)


def find_conflicts(steps: list[ModEffect]) -> list[Conflict]:
    """Every conflict of the run's steps (``Effect.steps``), files after targets, each part in file and key order."""
    if len(steps) < 2:  # one mod meets nobody
        return []
    users: dict[tuple[str, str], list[tuple[ModEffect, Target | None]]] = {}
    for step in steps:
        for ident, target in step.targets.items():
            users.setdefault(ident, []).append((step, target))
        for ident in step.writes.keys() - step.targets.keys():  # set the value that was there: a user, no target
            users.setdefault(ident, []).append((step, None))
    out: list[Conflict] = []
    for ident in sorted(users, key=lambda i: (i[0], guid_key(i[1]))):
        if len(users[ident]) > 1:
            out += _of_target(ident, users[ident])
    return out + _of_files(steps)


def decided_by(losers: list[ModEffect], winner: ModEffect) -> str:
    """What made ``winner`` come after ``losers``: the late phase, a ``LoadAfter``, or the loader's default order."""
    mod = winner.mod
    if mod is None:
        return ORDER
    if mod.late and any(not (s.mod and s.mod.late) for s in losers):
        return LATE_PHASE
    if any(s.mod_id in mod.load_after for s in losers):
        return LOAD_AFTER
    return NAME_ORDER


def _make(kind: str, level: str, target: Target, path: str, who: list[ModEffect], values: list) -> Conflict:
    return Conflict(
        kind, level, target.file, target.key, target.name, path,
        tuple(s.mod_id for s in who), tuple(values), who[-1].mod_id, decided_by(who[:-1], who[-1]),
    )  # fmt: skip


def _of_target(ident: tuple[str, str], users: list[tuple[ModEffect, Target | None]]) -> list[Conflict]:
    real = [(s, t) for s, t in users if t is not None]  # none: every mod only set the value that was there
    name = next((t.name for _s, t in reversed(real) if t.name), "")
    head = Target(ident[0], ident[1], real[-1][1].kind if real else CHANGED, name)
    out: list[Conflict] = []
    added = [(s, t) for s, t in real if t.kind == ADDED]
    if len(added) > 1:
        out.append(_make(DUPLICATE, CONFLICT, head, "", [s for s, _t in added], [ADDED] * len(added)))
    removed = [(s, t) for s, t in real if t.kind == REMOVED]
    if removed and len(removed) < len(users):
        who = [s for s, _t in users]
        out.append(_make(GONE, CONFLICT, head, "", who, [_what(t) for _s, t in users]))
    if len(users) > 1 and (not real or any(t.kind == CHANGED for _s, t in real)):
        out += _of_values(head, users)
    return out


def _what(target: Target | None) -> str:
    if target is None:
        return SET_VALUE
    return target.kind if target.kind in (ADDED, REMOVED) else f"{len(target.changes)} {VALUES}"


def _paths(step: ModEffect, target: Target | None, ident: tuple[str, str]) -> dict[str, str | None]:
    """The value paths one mod's step reached on a target and what it left there: what it changed (its value after),
    and what it set to the value that was there."""
    out = dict(step.writes.get(ident, {}))
    if target is not None and target.kind == CHANGED:
        out.update({c.path: c.after for c in target.changes})
    return out


def _of_values(head: Target, users: list[tuple[ModEffect, Target | None]]) -> list[Conflict]:
    by_path: dict[str, list[tuple[ModEffect, str | None]]] = {}
    for step, target in users:
        for path, value in _paths(step, target, head.ident).items():
            by_path.setdefault(path, []).append((step, value))
    changing = [(s, t) for s, t in users if t is not None and t.kind == CHANGED]
    out = []
    for path, who in by_path.items():
        if len({s.mod_id for s, _a in who}) > 1:
            values = [a for _s, a in who]
            level = CONFLICT if len(set(values)) > 1 else OVERLAP  # the same value twice: nothing is lost
            out.append(_make(VALUE, level, head, path, [s for s, _a in who], values))
    for outer in by_path:  # a block replaced or removed above a value another mod changed
        for inner in by_path:
            if outer != inner and _above(outer, inner):
                mine, theirs = by_path[outer], by_path[inner]
                who = [*mine, *theirs]
                if len({s.mod_id for s, _a in who}) > 1:
                    order = sorted(who, key=lambda pair: _position(users, pair[0]))
                    out.append(_make(BLOCK, CONFLICT, head, outer, [s for s, _a in order], [a for _s, a in order]))
    if not out and len(changing) > 1:
        mods = [s for s, _t in changing]
        counts = [f"{len(t.changes)} {VALUES}" for _s, t in changing]
        out.append(_make(ASSET, OVERLAP, head, "", mods, counts))
    return out


def _above(outer: str, inner: str) -> bool:
    """Whether ``outer`` is a block that holds ``inner`` (``Costs`` and ``Costs/Item[0]/Amount``, ``Costs[+]``)."""
    return inner.startswith((outer + "/", outer + "["))


def _position(users: list[tuple[ModEffect, Target | None]], step: ModEffect) -> int:
    return next(k for k, (s, _t) in enumerate(users) if s is step)


def _of_files(steps: list[ModEffect]) -> list[Conflict]:
    carried: dict[str, list[tuple[ModEffect, int, str]]] = {}
    for step in steps:
        for change in step.files:
            carried.setdefault(change.path, []).append((step, change.size, change.digest))
    out = []
    for path in sorted(carried):
        who = carried[path]
        if len(who) > 1:
            steps_ = [s for s, _n, _d in who]
            same = len({d for _s, _n, d in who}) == 1 and bool(who[0][2])  # one content: nothing is lost
            out.append(
                Conflict(FILE, OVERLAP if same else CONFLICT, path, "", "", "", tuple(s.mod_id for s in steps_),
                         tuple(f"{n} {BYTES}" for _s, n, _d in who), steps_[-1].mod_id, decided_by(steps_[:-1], steps_[-1]))
            )  # fmt: skip
    return out
