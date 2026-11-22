"""Lint of what ties a mod to other mods: its dependencies, the order they ask for, the mods nested inside it.

``lint_dependencies(folder, folders, profile)`` returns findings M01-M04 (``docs/engine-modloader.md``
"Dependencies" and "Nested mods"): a required mod that is not there, a load order circle, an incompatible pair that is
on together, a nested mod the loader would not pick up, a ``LoadAfter`` on a late mod. Stdlib only.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..modloader.mods import GAME_NUMBER, MODINFO_NAMES, CMod, find_mods, parked, read_mod, switched_on
from ..modloader.profilefile import CProfileText
from ..modloader.resolve import SResolution, resolve
from .modlint import SFinding

ANNO_117 = GAME_NUMBER["anno117"]  # ``Anno`` of 117's ``modinfo``


def hidden_nested(folder: Path) -> list[SFinding]:
    """A ``modinfo`` below a folder whose name starts with ``-`` or ``.``: the loader does not take it as a mod."""
    out = []
    for top, subfolders, names in os.walk(folder):
        subfolders.sort()
        relative_path = Path(top).relative_to(folder).as_posix()
        if (
            relative_path != "."
            and any(name in MODINFO_NAMES for name in names)
            and any(map(parked, relative_path.split("/")))
        ):
            out.append(
                SFinding(
                    "M04",
                    relative_path,
                    "a modinfo inside a folder starting with '-' or '.': the loader skips it, it is no nested mod",
                    unverified=True,
                )
            )
            subfolders.clear()
    return out


def resolution_findings(
    resolution: SResolution,
    ours: set[str] | None = None,
    *,
    profile: bool = True,
    installed: bool = True,
    notes: bool = False,
) -> list[SFinding]:
    """The loader's resolution as findings: M01 a missing ``Require``, M02 a ``LoadAfter`` circle, M03 an incompatible
    pair, M05 a ``LoadAfter`` on a late mod; with ``notes`` the harmless ones too (level info): M06 a ``Deprecate``d
    mod, M07 two copies, M08 an ``Optional`` that is not on, M09 the resolution's own notes. ``ours``: only what
    concerns these ModIDs (None: all). ``profile`` / ``installed``: whether a profile was given and whether mods
    folders were searched, which decide if "installed but not on" and "not found" can be said."""
    mine = (lambda *ids: True) if ours is None else (lambda *ids: bool(set(ids) & ours))
    out = []
    for miss in resolution.missing:
        if mine(miss.mod) and (profile or not miss.installed) and (installed or profile):
            state = "installed but not on in the profile" if miss.installed else "not found"
            out.append(
                SFinding(
                    "M01",
                    miss.mod,
                    f"requires {miss.dep}, which is {state}: the loader logs it and loads the mod anyway",
                )
            )
    for cycle in resolution.cycles:
        out.append(SFinding("M02", cycle[0], f"LoadAfter circle {' -> '.join(cycle)}: the loader's order is undefined"))
    for first, second in resolution.incompatible:
        if mine(first, second):
            out.append(
                SFinding(
                    "M03", first, f"is incompatible with {second}, and both are on: the loader logs it and loads both"
                )
            )
    for first, second in resolution.late_ignored:
        if mine(first):
            out.append(
                SFinding(
                    "M05",
                    first,
                    f"LoadAfter {second}, a late mod: a normal mod cannot load after a late one",
                    unverified=True,
                )
            )
    if notes:
        out += _note_findings(resolution)
    return out


def _note_findings(resolution: SResolution) -> list[SFinding]:
    out = [
        SFinding("M06", old, f"is left out: {new} replaces it (Deprecate)", level="info")
        for new, old in resolution.deprecated
    ]
    out += [
        SFinding(
            "M07",
            i,
            f"two copies are on, version {version} stays, {left_out} is left out, no line in the loader's log",
            level="info",
        )
        for i, version, left_out in resolution.duplicates
    ]
    out += [
        SFinding(
            "M08", mod_id, f"optional {dependency} is not on: the content it would enable is missing", level="info"
        )
        for mod_id, dependency, is_on in resolution.optional
        if not is_on
    ]
    return out + [SFinding("M09", "", note, level="info") for note in resolution.notes]


def _findings(mod: CMod, resolution: SResolution, profile: bool, installed: bool) -> list[SFinding]:
    ours = {mod.m_mod_id} | {nested_mod.m_mod_id for nested_mod in resolution.nested if nested_mod.top is mod}
    out = [
        SFinding("M04", mod.m_folder.name, f"nested folder is no mod: {why}", unverified=True)
        for why in mod.nested_notes
    ]
    for nested_mod in resolution.order:
        if nested_mod.top is mod:
            out += [
                SFinding("M04", nested_mod.m_folder.name, f"nested folder is no mod: {note}", unverified=True)
                for note in nested_mod.nested_notes
                if nested_mod is not mod
            ]
    return out + resolution_findings(resolution, ours, profile=profile, installed=installed)


def lint_dependencies(
    folder: Path, folders: list[Path] | None = None, profile: Path | None = None
) -> tuple[list[SFinding], list[str]]:
    """The findings and the notes (checks that could not run). ``folders``: the mods folders the dependencies are
    looked for in; ``profile``: a profile file, whose mods are on instead of the linted mod alone."""
    mod, _ = read_mod(folder, ANNO_117)
    if mod is None:
        return [], []
    notes: list[str] = []
    found = find_mods(list(folders or []), ANNO_117)[0]
    here = Path(folder).resolve()
    others = [candidate for candidate in found if candidate.m_folder.resolve() != here]
    if folders is None:
        notes.append("dependencies not checked against installed mods (give the mods folder)")
    if profile is not None:
        loaded_profile = CProfileText.from_bytes(Path(profile).read_bytes()).to_profile(Path(profile).stem)
        switched_on_mods = [
            candidate
            for candidate in switched_on([mod, *others], loaded_profile, tuple(folders or ()))
            if candidate.top is candidate
        ]
        if mod not in switched_on_mods:
            switched_on_mods.append(mod)
            notes.append(f"the profile does not switch {mod.m_mod_id} on: linted as if it were")
        resolution = resolve(switched_on_mods, [candidate for candidate in others if candidate not in switched_on_mods])
    else:
        resolution = resolve([mod], others, allow_pull=True)
    out = [*hidden_nested(Path(folder))]
    out += _findings(mod, resolution, profile is not None, folders is not None)
    return out, notes
