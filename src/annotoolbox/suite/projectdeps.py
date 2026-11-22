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
                "common.dependency_kinds_not",
                kinds=", ".join(DEPENDENCY_KINDS),
                unknown=", ".join(unknown),
            )
        )
    for kind, ids in clean(dependencies).items():
        for dependency_id in ids:
            if not (ID_FORMAT.match(dependency_id) or (kind == "LoadAfter" and dependency_id == LATE)):
                raise ValueError(tr("common.no_modid_lower_case_letters", kind=kind, id=repr(dependency_id)))
            if dependency_id == mod_id:
                raise ValueError(tr("common.mod_cannot_name_itself", kind=kind, id=dependency_id))
    if not all(isinstance(entry, str) and entry.strip() for entry in bundle or []):
        raise ValueError(tr("common.bundle_lists_mod_folders"))


def modinfo_fields(dependencies: dict) -> tuple[dict[str, list[str]], tuple[str, ...]]:
    """(the ``Dependencies`` kinds but ``LoadAfter``, ``LoadAfter``) as ``mod.modxml.modinfo`` takes them."""
    deps = clean(dependencies)
    after = tuple(deps.pop("LoadAfter", ()))
    return deps, after


def bundle_folders(project_folder: Path, bundle: list[str]) -> list[Path]:
    """The folders of the bundle; a relative one is relative to the project's folder."""
    return [(Path(project_folder) / folder) if not Path(folder).is_absolute() else Path(folder) for folder in bundle]


def copy_bundle(project_folder: Path, bundle: list[str], out: Path) -> list[str]:
    """Copy each chosen mod folder into ``out`` as a nested mod (``out/<folder name>``, modinfo at its root: the
    loader finds a mod folder at any depth). Returns the sentences for a folder that is no mod or cannot go in."""
    problems: list[str] = []
    for folder in bundle_folders(project_folder, bundle):
        bundled_mod, why = read_mod(folder)
        if bundled_mod is None:
            problems.append(tr("common.bundle_loader_not_take_mod", folder=folder.name, why=why))
            continue
        target = Path(out) / folder.name
        if target.exists():
            problems.append(tr("common.bundle_another_file_mod_build", folder=folder.name))
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
    seen: dict[str, str] = {mod_id: tr("common.the_project")}
    installed = (
        {nested_mod.m_mod_id: nested_mod for nested_mod in find_mods([mods_dir], GAME_NUMBER.get(game))[0]}
        if mods_dir
        else {}
    )
    stack = list(built.nested)
    while stack:
        nested_mod = stack.pop(0)
        stack = [*stack, *nested_mod.nested]
        if nested_mod.m_mod_id in seen:
            out_problems.append(
                tr(
                    "common.bundle_modid_also_loader_keeps",
                    folder=nested_mod.m_folder.name,
                    id=nested_mod.m_mod_id,
                    other=seen[nested_mod.m_mod_id],
                )
            )
        seen[nested_mod.m_mod_id] = tr("common.bundle_folder", folder=nested_mod.m_folder.name)
        old = installed.get(nested_mod.m_mod_id)
        if old is not None and old.m_version != nested_mod.m_version:
            out_problems.append(
                tr(
                    "common.bundle_also_installed_loader_keeps",
                    folder=nested_mod.m_folder.name,
                    id=nested_mod.m_mod_id,
                    version=nested_mod.m_version,
                    installed=old.m_version,
                )
            )
    return out_problems
