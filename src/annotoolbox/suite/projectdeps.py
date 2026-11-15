"""What a project declares about other mods: the dependencies written into its ``modinfo.json`` and the mods it carries
inside itself (nested mods, the loader's layout). Stdlib only.

``Project.dependencies`` maps a kind (``mods.DEPENDENCY_KINDS``) to ModIDs, ``Project.bundle`` lists mod folders. ``modinfo_fields``
gives the ``modinfo`` arguments, ``validate`` the sentences for a declaration the loader would misread,
``copy_bundle`` puts the chosen mods into the build, ``bundle_problems`` checks them. The rules:
``docs/engine-modloader.md`` "Dependencies" and "Nested mods", ``docs/tool-suite.md`` section 18.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..infra.i18n import tr
from ..modloader.mods import DEPENDENCY_KINDS, GAME_NUMBER, LATE, find_mods, read_mod

ID_FORMAT = re.compile(r"^[a-z0-9-]+$")


def clean(dependencies: dict) -> dict[str, list[str]]:
    """The declaration without empty kinds and repeated ids (a kind the loader does not know is dropped)."""
    out: dict[str, list[str]] = {}
    for kind in DEPENDENCY_KINDS:
        ids = [str(i).strip() for i in (dependencies or {}).get(kind) or [] if str(i).strip()]
        if ids:
            out[kind] = list(dict.fromkeys(ids))
    return out


def validate(mod_id: str, dependencies: dict, bundle: list) -> None:
    """Raise ``ValueError`` for a declaration the loader would misread."""
    unknown = sorted(set(dependencies or {}) - set(DEPENDENCY_KINDS))
    if unknown:
        raise ValueError(
            tr(
                "dependency kinds are {kinds}, not {unknown}",
                kinds=", ".join(DEPENDENCY_KINDS),
                unknown=", ".join(unknown),
            )
        )
    for kind, ids in clean(dependencies).items():
        for i in ids:
            if not (ID_FORMAT.match(i) or (kind == "LoadAfter" and i == LATE)):
                raise ValueError(
                    tr("{kind}: {id} is no ModID (lower-case letters, digits and dashes)", kind=kind, id=repr(i))
                )
            if i == mod_id:
                raise ValueError(tr("{kind}: a mod cannot name itself ({id})", kind=kind, id=i))
    if not all(isinstance(b, str) and b.strip() for b in bundle or []):
        raise ValueError(tr("the bundle lists mod folders"))


def modinfo_fields(dependencies: dict) -> tuple[dict[str, list[str]], tuple[str, ...]]:
    """(the ``Dependencies`` kinds but ``LoadAfter``, ``LoadAfter``) as ``mod.modxml.modinfo`` takes them."""
    deps = clean(dependencies)
    after = tuple(deps.pop("LoadAfter", ()))
    return deps, after


def bundle_folders(project_folder: Path, bundle: list[str]) -> list[Path]:
    """The folders of the bundle; a relative one is relative to the project's folder."""
    return [(Path(project_folder) / b) if not Path(b).is_absolute() else Path(b) for b in bundle]


def copy_bundle(project_folder: Path, bundle: list[str], out: Path) -> list[str]:
    """Copy each chosen mod folder into ``out`` as a nested mod (``out/<folder name>``, modinfo at its root: the
    loader finds a mod folder at any depth). Returns the sentences for a folder that is no mod or cannot go in."""
    problems: list[str] = []
    for folder in bundle_folders(project_folder, bundle):
        mod, why = read_mod(folder)
        if mod is None:
            problems.append(
                tr("bundle {folder}: {why}: the loader would not take it as a mod", folder=folder.name, why=why)
            )
            continue
        target = Path(out) / folder.name
        if target.exists():
            problems.append(tr("bundle {folder}: another file or mod of the build has this name", folder=folder.name))
            continue
        shutil.copytree(folder, target)
    return problems


def bundle_problems(mod_id: str, out: Path, mods_dir: Path | None, game: str = "anno117") -> list[str]:
    """What is wrong with the mods nested in the built folder ``out``: a ModID twice (the loader keeps one, without a
    line), the project's own, one the mods folder holds at another version."""
    built, _ = read_mod(out, GAME_NUMBER.get(game))
    if built is None:
        return []
    out_problems: list[str] = []
    seen: dict[str, str] = {mod_id: tr("the project")}
    installed = {m.mod_id: m for m in find_mods([mods_dir], GAME_NUMBER.get(game))[0]} if mods_dir else {}
    stack = list(built.nested)
    while stack:
        m = stack.pop(0)
        stack = [*stack, *m.nested]
        if m.mod_id in seen:
            out_problems.append(
                tr(
                    "bundle {folder}: ModID {id} is also {other}: the loader keeps one, silently",
                    folder=m.folder.name,
                    id=m.mod_id,
                    other=seen[m.mod_id],
                )
            )
        seen[m.mod_id] = tr("bundle {folder}", folder=m.folder.name)
        old = installed.get(m.mod_id)
        if old is not None and old.version != m.version:
            out_problems.append(
                tr(
                    "bundle {folder}: {id} {version} is also installed as {installed}: "
                    "the loader keeps the higher version for both",
                    folder=m.folder.name,
                    id=m.mod_id,
                    version=m.version,
                    installed=old.version,
                )
            )
    return out_problems
