"""A set of mods resolved the way the mod loader does it: the nested mods that come with their top mod, the same ModID
once, the ``Deprecate``d mods out, the order, and what the dependency fields report. Stdlib only.

``resolve(on, available, allow_pull=...)`` takes the mods that are switched on (a profile's, or one mod folder the way an
import opens it) and returns a ``SResolution``: ``order`` is what the loader applies, the rest is what its log would say
or a person should be told (``lines()``). ``resolve_profile`` is the same for a profile. The rules, and which are
proven and which inferred: ``docs/engine-modloader.md`` ("Dependencies", "Nested mods").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .mods import CMod, order_mods, switched_on, version_key
from .profilefile import SProfile


@dataclass
class SMissing:
    mod: str
    dep: str
    installed: bool  # found in the mods folders but not switched on


@dataclass
class SResolution:
    order: list[CMod] = field(default_factory=list)
    nested: list[CMod] = field(default_factory=list)  # in ``order``: came with their top mod
    pulled: dict[str, str] = field(default_factory=dict)  # ModID -> the mod that requires it (an import's choice)
    missing: list[SMissing] = field(default_factory=list)  # ``Require``d, not in the set
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
            f"{mod.mod}: is missing a dependency {mod.dep}" + (" (installed, but not on)" if mod.installed else "")
            for mod in self.missing
        ]
        out += [
            f"{first}: is incompatible with {second} (both are on; the loader loads both)"
            for first, second in self.incompatible
        ]
        out += [f"load order cycle: {' -> '.join(cycle)} (the loader's order is undefined)" for cycle in self.cycles]
        out += [
            f"{first}: LoadAfter {second}, a late mod: a normal mod cannot load after a late one"
            for first, second in self.late_ignored
        ]
        return out

    def lines(self) -> list[str]:
        """The summary: which mods came in how, then the problems."""
        by_id = {mod.m_mod_id: mod for mod in self.order}
        out = []
        came = [f"{mod.m_mod_id} (nested in {mod.m_parent.m_mod_id})" for mod in self.nested if mod.m_parent]
        came += [f"{k} (required by {version})" for k, version in self.pulled.items()]
        if came:
            out.append(f"mods that came in with another: {len(came)}")
            out += [f"  {cycle}" for cycle in came]
        out += [f"{old}: replaced by {new}, left out" for new, old in self.deprecated]
        out += [
            f"{i}: two copies, version {version} kept, {left_out} left out" for i, version, left_out in self.duplicates
        ]
        out += [
            f"{mod}: optional {dependency} {'is on' if enabled_mods else 'is not on'}"
            for mod, dependency, enabled_mods in self.optional
            if mod in by_id
        ]
        out += self.problems
        out += self.notes
        return out


def _redirect(mods: list[CMod]) -> dict[str, str]:
    return {old: mod.m_mod_id for mod in mods for old in mod.m_deprecate if old != mod.m_mod_id}


def _with_nested(top: CMod, into: list[CMod], nested: list[CMod]) -> None:
    into.append(top)
    for child in top.nested:
        nested.append(child)
        _with_nested(child, into, nested)


def _dedupe(mods: list[CMod], resolution: SResolution) -> list[CMod]:
    """A ModID once: the higher version stays, the later one when the versions are equal (silently, in the game)."""
    kept: dict[str, CMod] = {}
    for mod in mods:
        old = kept.get(mod.m_mod_id)
        if old is None or version_key(mod.m_version) >= version_key(old.m_version):
            if old is not None:
                resolution.duplicates.append((mod.m_mod_id, mod.m_version or "unversioned", str(old.m_folder)))
            kept[mod.m_mod_id] = mod
        else:
            resolution.duplicates.append((mod.m_mod_id, old.m_version or "unversioned", str(mod.m_folder)))
    return [mod for mod in mods if kept[mod.m_mod_id] is mod]


def _below(mod: CMod, gone: set[int]) -> bool:
    """Whether ``m`` or a mod it sits inside is among ``gone`` (by identity)."""
    while mod is not None:
        if id(mod) in gone:
            return True
        mod = mod.m_parent
    return False


def _unmet(mods: list[CMod], redirect: dict[str, str]) -> list[tuple[str, str]]:
    ids = {mod.m_mod_id for mod in mods}
    return [
        (mod.m_mod_id, dep)
        for mod in mods
        for dep in mod.m_require
        if redirect.get(dep, dep) not in ids and dep != mod.m_mod_id
    ]


def resolve(
    enabled_mods: list[CMod],
    available: list[CMod] | tuple = (),
    *,
    allow_pull: bool = False,
    provided: frozenset[str] = frozenset(),
) -> SResolution:
    """``enabled_mods`` (top mods) with their nested mods, ``Deprecate`` and duplicates applied, ordered and checked.

    ``available``: the mods found in the mods folders but not on (to tell "installed, not on" from "not installed",
    and the ones ``allow_pull`` may add). ``allow_pull``: add every ``Require``d mod that is in ``available`` (with its
    nested mods), as an import of a single mod does, since there is no profile to name them. ``provided``: ids that exist
    though they are no mod (the DLCs a ``Require`` may name)."""
    resolution = SResolution()
    flat: list[CMod] = []
    for top in enabled_mods:
        _with_nested(top, flat, resolution.nested)
    avail = {mod.m_mod_id: mod for mod in available}
    while allow_pull:
        have = {mod.m_mod_id for mod in flat}
        redirect = _redirect(flat)
        want = [
            (who, dep)
            for who, dep in _unmet(flat, redirect)
            if dep in avail and dep not in have and dep not in provided
        ]
        if not want:
            break
        who, dep = want[0]
        resolution.pulled[dep] = who
        _with_nested(avail[dep], flat, resolution.nested)
    flat = _dedupe(flat, resolution)
    redirect = _redirect(flat)
    ids = {mod.m_mod_id for mod in flat}
    gone = {id(mod) for mod in flat if mod.m_mod_id in redirect}
    for old, new in redirect.items():
        if old in ids:
            resolution.deprecated.append((new, old))
    flat = [mod for mod in flat if not _below(mod, gone)]
    kept = {id(mod) for mod in flat}
    resolution.nested = [mod for mod in resolution.nested if id(mod) in kept]
    ids = {mod.m_mod_id for mod in flat}
    for mod in flat:
        for dep in mod.m_require:
            real = redirect.get(dep, dep)
            if real not in ids and real != mod.m_mod_id and real not in provided:
                resolution.missing.append(SMissing(mod.m_mod_id, dep, dep in avail))
        resolution.optional += [(mod.m_mod_id, dep, redirect.get(dep, dep) in ids) for dep in mod.m_optional]
        for other in mod.m_incompatible:
            pair = tuple(sorted((mod.m_mod_id, other)))
            if (
                other in ids
                and other != mod.m_mod_id
                and pair not in {tuple(sorted(known)) for known in resolution.incompatible}
            ):
                resolution.incompatible.append((mod.m_mod_id, other))
    ordering = order_mods(flat, redirect)
    resolution.order, resolution.cycles, resolution.late_ignored = ordering.mods, ordering.cycles, ordering.ignored
    for mod in resolution.order:
        resolution.notes += [f"{mod.m_mod_id}: nested folder not a mod: {why}" for why in mod.nested_notes]
    return resolution


def resolve_profile(
    found: list[CMod], profile: SProfile, new_folders: tuple[Path, ...] = (), provided: frozenset[str] = frozenset()
) -> SResolution:
    """The mods ``profile`` switches on, with what comes with them, as the loader would run them."""
    enabled_mods = switched_on(found, profile, new_folders)
    off = [mod for mod in found if mod not in enabled_mods]
    return resolve(enabled_mods, off, provided=provided)
