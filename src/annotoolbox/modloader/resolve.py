"""A set of mods resolved the way the mod loader does it: the nested mods that come with their top mod, the same ModID
once, the ``Deprecate``d mods out, the order, and what the dependency fields report. Stdlib only.

``resolve(on, available, pull=...)`` takes the mods that are switched on (a profile's, or one mod folder the way an
import opens it) and returns a ``Resolution``: ``order`` is what the loader applies, the rest is what its log would say
or a person should be told (``lines()``). ``resolve_profile`` is the same for a profile. The rules, and which are
proven and which inferred: ``docs/engine-modloader.md`` ("Dependencies", "Nested mods").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .mods import Mod, order_mods, switched_on, version_key
from .profilefile import Profile


@dataclass
class Missing:
    mod: str
    dep: str
    installed: bool  # found in the mods folders but not switched on


@dataclass
class Resolution:
    order: list[Mod] = field(default_factory=list)
    nested: list[Mod] = field(default_factory=list)  # in ``order``: came with their top mod
    pulled: dict[str, str] = field(default_factory=dict)  # ModID -> the mod that requires it (an import's choice)
    missing: list[Missing] = field(default_factory=list)  # ``Require``d, not in the set
    optional: list[tuple[str, str, bool]] = field(default_factory=list)  # (mod, id, in the set): informational
    incompatible: list[tuple[str, str]] = field(default_factory=list)  # both in the set
    deprecated: list[tuple[str, str]] = field(default_factory=list)  # (replacing, replaced and taken out)
    duplicates: list[tuple[str, str, str]] = field(default_factory=list)  # (ModID, kept version, dropped folder)
    cycles: list[list[str]] = field(default_factory=list)
    late_ignored: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        """What the loader logs or silently gets wrong, one sentence each (not the informational ones)."""
        out = [
            f"{m.mod}: is missing a dependency {m.dep}" + (" (installed, but not on)" if m.installed else "")
            for m in self.missing
        ]
        out += [f"{a}: is incompatible with {b} (both are on; the loader loads both)" for a, b in self.incompatible]
        out += [f"load order cycle: {' -> '.join(c)} (the loader's order is undefined)" for c in self.cycles]
        out += [
            f"{a}: LoadAfter {b}, a late mod: a normal mod cannot load after a late one" for a, b in self.late_ignored
        ]
        return out

    def lines(self) -> list[str]:
        """The summary: which mods came in how, then the problems."""
        by_id = {m.mod_id: m for m in self.order}
        out = []
        came = [f"{m.mod_id} (nested in {m.parent.mod_id})" for m in self.nested if m.parent]
        came += [f"{k} (required by {v})" for k, v in self.pulled.items()]
        if came:
            out.append(f"mods that came in with another: {len(came)}")
            out += [f"  {c}" for c in came]
        out += [f"{old}: replaced by {new}, left out" for new, old in self.deprecated]
        out += [f"{i}: two copies, version {v} kept, {f} left out" for i, v, f in self.duplicates]
        out += [f"{m}: optional {d} {'is on' if on else 'is not on'}" for m, d, on in self.optional if m in by_id]
        out += self.problems
        out += self.notes
        return out


def _redirect(mods: list[Mod]) -> dict[str, str]:
    return {old: m.mod_id for m in mods for old in m.deprecate if old != m.mod_id}


def _with_nested(top: Mod, into: list[Mod], nested: list[Mod]) -> None:
    into.append(top)
    for child in top.nested:
        nested.append(child)
        _with_nested(child, into, nested)


def _dedupe(mods: list[Mod], res: Resolution) -> list[Mod]:
    """A ModID once: the higher version stays, the later one when the versions are equal (silently, in the game)."""
    kept: dict[str, Mod] = {}
    for m in mods:
        old = kept.get(m.mod_id)
        if old is None or version_key(m.version) >= version_key(old.version):
            if old is not None:
                res.duplicates.append((m.mod_id, m.version or "unversioned", str(old.folder)))
            kept[m.mod_id] = m
        else:
            res.duplicates.append((m.mod_id, old.version or "unversioned", str(m.folder)))
    return [m for m in mods if kept[m.mod_id] is m]


def _below(m: Mod, gone: set[int]) -> bool:
    """Whether ``m`` or a mod it sits inside is among ``gone`` (by identity)."""
    while m is not None:
        if id(m) in gone:
            return True
        m = m.parent
    return False


def _unmet(mods: list[Mod], redirect: dict[str, str]) -> list[tuple[str, str]]:
    ids = {m.mod_id for m in mods}
    return [(m.mod_id, dep) for m in mods for dep in m.require if redirect.get(dep, dep) not in ids and dep != m.mod_id]


def resolve(
    on: list[Mod], available: list[Mod] | tuple = (), *, pull: bool = False, provided: frozenset[str] = frozenset()
) -> Resolution:
    """``on`` (top mods) with their nested mods, ``Deprecate`` and duplicates applied, ordered and checked.

    ``available``: the mods found in the mods folders but not on (to tell "installed, not on" from "not installed",
    and the ones ``pull`` may add). ``pull``: add every ``Require``d mod that is in ``available`` (with its nested
    mods), as an import of a single mod does, since there is no profile to name them. ``provided``: ids that exist
    though they are no mod (the DLCs a ``Require`` may name)."""
    res = Resolution()
    flat: list[Mod] = []
    for top in on:
        _with_nested(top, flat, res.nested)
    avail = {m.mod_id: m for m in available}
    while pull:
        have = {m.mod_id for m in flat}
        redirect = _redirect(flat)
        want = [
            (who, dep)
            for who, dep in _unmet(flat, redirect)
            if dep in avail and dep not in have and dep not in provided
        ]
        if not want:
            break
        who, dep = want[0]
        res.pulled[dep] = who
        _with_nested(avail[dep], flat, res.nested)
    flat = _dedupe(flat, res)
    redirect = _redirect(flat)
    ids = {m.mod_id for m in flat}
    gone = {id(m) for m in flat if m.mod_id in redirect}
    for old, new in redirect.items():
        if old in ids:
            res.deprecated.append((new, old))
    flat = [m for m in flat if not _below(m, gone)]
    kept = {id(m) for m in flat}
    res.nested = [m for m in res.nested if id(m) in kept]
    ids = {m.mod_id for m in flat}
    for m in flat:
        for dep in m.require:
            real = redirect.get(dep, dep)
            if real not in ids and real != m.mod_id and real not in provided:
                res.missing.append(Missing(m.mod_id, dep, dep in avail))
        res.optional += [(m.mod_id, dep, redirect.get(dep, dep) in ids) for dep in m.optional]
        for other in m.incompatible:
            pair = tuple(sorted((m.mod_id, other)))
            if other in ids and other != m.mod_id and pair not in {tuple(sorted(p)) for p in res.incompatible}:
                res.incompatible.append((m.mod_id, other))
    ordering = order_mods(flat, redirect)
    res.order, res.cycles, res.late_ignored = ordering.mods, ordering.cycles, ordering.ignored
    for m in res.order:
        res.notes += [f"{m.mod_id}: nested folder not a mod: {why}" for why in m.nested_notes]
    return res


def resolve_profile(
    found: list[Mod], profile: Profile, new_folders: tuple[Path, ...] = (), provided: frozenset[str] = frozenset()
) -> Resolution:
    """The mods ``profile`` switches on, with what comes with them, as the loader would run them."""
    on = switched_on(found, profile, new_folders)
    off = [m for m in found if m not in on]
    return resolve(on, off, provided=provided)
