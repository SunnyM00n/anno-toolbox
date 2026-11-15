"""Lint of what ties a mod to other mods: its dependencies, the order they ask for, the mods nested inside it.

``lint_dependencies(folder, folders, profile)`` returns findings M01-M04 (``docs/engine-modloader.md``
"Dependencies" and "Nested mods"): a required mod that is not there, a load order circle, an incompatible pair that is
on together, a nested mod the loader would not pick up, a ``LoadAfter`` on a late mod. Stdlib only.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..modloader.mods import GAME_NUMBER, MODINFO_NAMES, Mod, find_mods, parked, read_mod, switched_on
from ..modloader.profilefile import ProfileText
from ..modloader.resolve import Resolution, resolve
from .modlint import Finding

ANNO_117 = GAME_NUMBER["anno117"]  # ``Anno`` of 117's ``modinfo``


def hidden_nested(folder: Path) -> list[Finding]:
    """A ``modinfo`` below a folder whose name starts with ``-`` or ``.``: the loader does not take it as a mod."""
    out = []
    for top, dirs, names in os.walk(folder):
        dirs.sort()
        rel = Path(top).relative_to(folder).as_posix()
        if rel != "." and any(n in MODINFO_NAMES for n in names) and any(map(parked, rel.split("/"))):
            out.append(
                Finding(
                    "M04",
                    rel,
                    "a modinfo inside a folder starting with '-' or '.': the loader skips it, it is no nested mod",
                    unverified=True,
                )
            )
            dirs.clear()
    return out


def resolution_findings(
    res: Resolution, ours: set[str] | None = None, *, profile: bool = True, installed: bool = True, notes: bool = False
) -> list[Finding]:
    """The loader's resolution as findings: M01 a missing ``Require``, M02 a ``LoadAfter`` circle, M03 an incompatible
    pair, M05 a ``LoadAfter`` on a late mod; with ``notes`` the harmless ones too (level info): M06 a ``Deprecate``d
    mod, M07 two copies, M08 an ``Optional`` that is not on, M09 the resolution's own notes. ``ours``: only what
    concerns these ModIDs (None: all). ``profile`` / ``installed``: whether a profile was given and whether mods
    folders were searched, which decide if "installed but not on" and "not found" can be said."""
    mine = (lambda *ids: True) if ours is None else (lambda *ids: bool(set(ids) & ours))
    out = []
    for miss in res.missing:
        if mine(miss.mod) and (profile or not miss.installed) and (installed or profile):
            state = "installed but not on in the profile" if miss.installed else "not found"
            out.append(
                Finding(
                    "M01",
                    miss.mod,
                    f"requires {miss.dep}, which is {state}: the loader logs it and loads the mod anyway",
                )
            )
    for cycle in res.cycles:
        out.append(Finding("M02", cycle[0], f"LoadAfter circle {' -> '.join(cycle)}: the loader's order is undefined"))
    for a, b in res.incompatible:
        if mine(a, b):
            out.append(
                Finding("M03", a, f"is incompatible with {b}, and both are on: the loader logs it and loads both")
            )
    for a, b in res.late_ignored:
        if mine(a):
            out.append(
                Finding(
                    "M05", a, f"LoadAfter {b}, a late mod: a normal mod cannot load after a late one", unverified=True
                )
            )
    if notes:
        out += _note_findings(res)
    return out


def _note_findings(res: Resolution) -> list[Finding]:
    out = [
        Finding("M06", old, f"is left out: {new} replaces it (Deprecate)", level="info") for new, old in res.deprecated
    ]
    out += [
        Finding(
            "M07",
            i,
            f"two copies are on, version {v} stays, {f} is left out, no line in the loader's log",
            level="info",
        )
        for i, v, f in res.duplicates
    ]
    out += [
        Finding("M08", m, f"optional {d} is not on: the content it would enable is missing", level="info")
        for m, d, on in res.optional
        if not on
    ]
    return out + [Finding("M09", "", n, level="info") for n in res.notes]


def _findings(mod: Mod, res: Resolution, profile: bool, installed: bool) -> list[Finding]:
    ours = {mod.mod_id} | {m.mod_id for m in res.nested if m.top is mod}
    out = [
        Finding("M04", mod.folder.name, f"nested folder is no mod: {why}", unverified=True) for why in mod.nested_notes
    ]
    for m in res.order:
        if m.top is mod:
            out += [
                Finding("M04", m.folder.name, f"nested folder is no mod: {w}", unverified=True)
                for w in m.nested_notes
                if m is not mod
            ]
    return out + resolution_findings(res, ours, profile=profile, installed=installed)


def lint_dependencies(
    folder: Path, folders: list[Path] | None = None, profile: Path | None = None
) -> tuple[list[Finding], list[str]]:
    """The findings and the notes (checks that could not run). ``folders``: the mods folders the dependencies are
    looked for in; ``profile``: a profile file, whose mods are on instead of the linted mod alone."""
    mod, _ = read_mod(folder, ANNO_117)
    if mod is None:
        return [], []
    notes: list[str] = []
    found = find_mods(list(folders or []), ANNO_117)[0]
    here = Path(folder).resolve()
    others = [m for m in found if m.folder.resolve() != here]
    if folders is None:
        notes.append("dependencies not checked against installed mods (give the mods folder)")
    if profile is not None:
        prof = ProfileText.from_bytes(Path(profile).read_bytes()).to_profile(Path(profile).stem)
        on = [m for m in switched_on([mod, *others], prof, tuple(folders or ())) if m.top is m]
        if mod not in on:
            on.append(mod)
            notes.append(f"the profile does not switch {mod.mod_id} on: linted as if it were")
        res = resolve(on, [m for m in others if m not in on])
    else:
        res = resolve([mod], others, pull=True)
    out = [*hidden_nested(Path(folder))]
    out += _findings(mod, res, profile is not None, folders is not None)
    return out, notes
