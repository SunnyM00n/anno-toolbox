"""What the import says about the mod before it is carried (docs/mod-import.md, section 14): conditions on other mods,
a build stamp of another game version, the mod loader's lint of the original, a mod id the Mods app already holds, and
names that differ only in case. Every check reads; none changes the mod. Stdlib only.

``CImportContext`` carries what the checks look at (the install, the mods folders, the profile); a check whose input is
missing says so in one line and is skipped. ``run_checks`` gives the report's lines.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import N_, number, tr
from ..modloader.mods import PROFILE_FILE, VARIABLE, find_mods, read_mod
from ..modloader.profilefile import DEFAULT, CActiveFile, CProfileText

SHOWN = 5  # lines of one kind a report lists
CONDITION = re.compile(rb"""\bCondition\s*=\s*(?:"([^"]*)"|'([^']*)')""")
STAMP = re.compile(r"Generated for the game build: ([^.\s]+(?:\.[a-z0-9]+)?)", re.IGNORECASE)
PROFILES = "profiles"  # the folder of a mods folder that holds the profiles
ANNO_OF_GAME = {"anno117": 8}


@dataclass
class CImportContext:
    """The inputs of the checks. ``install``: the game's install folder (its archives give the build and the files a
    patch targets), ``mods_folders``: where mods live (duplicates, dependencies), ``profile``: the Mods app's active
    profile, ``registry``: the file that lists half-made imports (``importresume``). ``build`` and ``has_base`` may be
    set directly; else they are read from ``install`` when a check asks."""

    m_install: Path | None = None
    m_cache: Path | None = None
    m_mods_folders: list = field(default_factory=list)
    m_profile: CProfileText | None = None
    m_registry: Path | None = None
    m_build: str = ""
    m_has_base: Callable[[str], bool] | None = None
    _m_files: object = None

    def _archives(self):
        if self._m_files is None and self.m_install:
            from ..sources.anno117 import CAnno117Files
            from ..sources.overlay import cache_base

            try:
                self._m_files = CAnno117Files(self.m_install, cache_dir=self.m_cache or cache_base())
            except OSError:
                self._m_files = False
        return self._m_files or None

    def installed_build(self) -> str:
        if self.m_build or not self._archives():
            return self.m_build
        from ..sources.anno117 import game_build

        return game_build(self._archives())

    def base_lookup(self) -> Callable[[str], bool] | None:
        archives = self._archives()
        return self.m_has_base or (archives.exists if archives else None)


def context_from_settings(settings, registry: Path | None = None) -> CImportContext:
    """The context a window or the command line gives the import: the settings' 117 install, the 117 mods folder (the
    standard place when the setting is empty) and the profile it names as active; nothing is read until a check asks."""
    from . import project as projects

    mods = projects.mods_folder_of("anno117", settings)
    profile = _active_profile(mods) if mods is not None else None
    install = Path(settings.anno117) if getattr(settings, "anno117", "") else None
    return CImportContext(install, None, [mods] if mods else [], profile, registry)


def _active_profile(mods: Path) -> CProfileText | None:
    """The profile the mods folder's ``profile.txt`` names (``default`` without one), None when it does not read."""
    try:
        active = mods / PROFILE_FILE
        name = CActiveFile.from_bytes(active.read_bytes()).name if active.is_file() else DEFAULT
        return CProfileText.from_bytes((mods / PROFILES / f"{name}.txt").read_bytes()) if name else None
    except (OSError, ValueError):
        return None


def case_clashes(files: list) -> list[tuple[str, str]]:
    """``(first, later)`` for each path that differs from an earlier one only in case (Windows sees one file)."""
    seen: dict[str, str] = {}
    return [(seen[low], rel) for rel, _size in files if seen.setdefault(low := rel.lower(), rel) != rel]


def case_clash_lines(clashes: list[tuple[str, str]], left_out: bool) -> list[str]:
    if not clashes:
        return []
    shown = "; ".join(f"{first} / {second}" for first, second in clashes[:SHOWN]) + (
        " ..." if len(clashes) > SHOWN else ""
    )
    count_text = number(len(clashes))
    if left_out:
        return [tr("common.import_case_clash_left_out", n=count_text, list=shown)]
    return [tr("common.import_case_clash_kept", n=count_text, list=shown)]


@dataclass
class SConditions:
    """The ``Condition`` attributes of the mod's ModOps files: ``on_mods`` (expression -> files) asks about other
    mods, ``own`` only about the mod's own options or the game."""

    on_mods: dict = field(default_factory=dict)
    mods: set = field(default_factory=set)
    own: Counter = field(default_factory=Counter)  # file -> conditions of its own
    total: int = 0


def read_conditions(root: Path, rels: list[str], owner: str) -> SConditions:
    """Every ``Condition`` of the ``.xml`` files ``rels`` below ``root``, read line by line (a patch file may be
    hundreds of MB); ``owner`` is the mod's own id (a ``#owner`` is no other mod)."""
    found = SConditions()
    for rel in rels:
        try:
            with (root / rel).open("rb") as fh:
                for line in fh:
                    if b"Condition" in line:
                        _count(found, rel, line, owner)
        except OSError:
            continue
    return found


def _count(found: SConditions, rel: str, line: bytes, owner: str) -> None:
    for match in CONDITION.finditer(line):
        expr = (match.group(1) or match.group(2)).decode("utf-8", "replace")
        found.total += 1
        others = {
            name.split(".")[0] for _neg, _sign, name in VARIABLE.findall(_unquoted(expr)) if name.split(".")[0] != owner
        }
        others = {name for name in others if "." in name or _is_mod_variable(expr, name)}
        if others:
            found.mods |= others
            found.on_mods.setdefault(expr, []).append(rel)
        else:
            found.own[rel] += 1


def _unquoted(expr: str) -> str:
    return "".join(part for k, part in enumerate(re.split(r"('[^']*'|\"[^\"]*\")", expr)) if not k % 2)


def _is_mod_variable(expr: str, name: str) -> bool:
    """A ``#name`` asks about a mod; a bare ``$name`` is the mod's own option."""
    return f"#{name}" in expr


def condition_lines(found: SConditions, mod, ctx: CImportContext) -> list[str]:
    """The conditions report: how many, which ones ask about other mods, and why the files stay as they are."""
    if not found.total:
        return []
    out = [tr("common.import_conditions", n=number(found.total), own=number(sum(found.own.values())))]
    if not found.on_mods:
        return out
    known = (
        {found_mod.m_mod_id: found_mod for found_mod in find_mods([Path(folder) for folder in ctx.m_mods_folders])[0]}
        if ctx.m_mods_folders
        else {}
    )
    wanted = {*mod.m_require, *mod.m_optional, *mod.m_load_after}
    for other in sorted(found.mods):
        state = (
            tr("common.import_condition_dependency") if other in wanted else tr("common.import_condition_no_dependency")
        )
        where = tr("common.import_condition_installed") if other in known else tr("common.import_condition_missing")
        out.append(tr("common.import_condition_mod", id=other, state=state, where=where))
    for expr, rels in list(found.on_mods.items())[:SHOWN]:
        out.append(tr("common.import_condition_expr", expr=expr, n=number(len(rels)), file=sorted(set(rels))[0]))
    out.append(tr("common.import_conditions_kept"))
    return out


def stamp_lines(mod, ctx: CImportContext) -> list[str]:
    """The game build the mod says it was generated for (the converter's mods write the line), against the installed
    one: a warning when they differ, a note when the install is not known."""
    from ..modloader.mods import localized

    found = STAMP.search(localized(mod.m_info.get("Description")))
    if not found:
        return []
    stamped = found.group(1)
    installed = ctx.installed_build()
    if not installed:
        return [tr("common.import_stamp_unknown", stamped=stamped)]
    if stamped.lower() == installed.lower():
        return [tr("common.import_stamp_same", stamped=stamped)]
    return [tr("common.import_stamp_other", stamped=stamped, installed=installed)]


def lint_lines(root: Path, game: str, ctx: CImportContext) -> list[str]:
    """The mod loader's lint of the original (``mod lint``): the summary and the first findings of each rule."""
    if game != "anno117":
        return [tr("common.import_lint_game", game=game)]
    from ..mod import modlint

    base = ctx.base_lookup()
    result = modlint.lint_mod(root, has_base=base, folders=list(ctx.m_mods_folders) or None)
    summary, *rest = result.lines(per_code=3)
    out = [tr("common.import_lint", summary=summary)]
    out += [f"  {line}" for line in rest[: 12 * 4]]
    if base is None:
        out.append(tr("common.import_lint_no_install"))
    return out


def duplicate_lines(mod, root: Path, ctx: CImportContext) -> list[str]:
    """The mod's id as the Mods app has it: other folders with the same id, and the profile's line for it (the
    mod's own folder, when the import starts from the mods folder, is not a duplicate)."""
    out: list[str] = []
    here = Path(root).resolve()
    if ctx.m_mods_folders:
        others = [
            found_mod
            for found_mod in find_mods([Path(folder) for folder in ctx.m_mods_folders], ANNO_OF_GAME.get("anno117"))[0]
            if found_mod.m_mod_id == mod.m_mod_id and found_mod.m_folder.resolve() != here
        ]
        out += [
            tr(
                "common.import_duplicate_folder",
                id=mod.m_mod_id,
                folder=found_mod.m_folder,
                version=found_mod.m_version,
            )
            for found_mod in others
        ]
    listed = ctx.m_profile.state(mod.m_mod_id) if ctx.m_profile is not None else None
    if listed is not None and not _inside(here, ctx.m_mods_folders):
        text = N_("common.import_duplicate_profile_on") if listed else N_("common.import_duplicate_profile_off")
        out.append(tr(text, id=mod.m_mod_id))
    return out


def _inside(here: Path, folders: list) -> bool:
    return any(Path(folder).resolve() in (here, *here.parents) for folder in folders)


def run_checks(root: Path, game: str, files: list, ctx: CImportContext, say: Callable[[str], None]) -> list[str]:
    """The report lines of every check, in the order: stamp, duplicates, conditions, lint. ``say`` is told which one
    runs (the lint of a large mod takes a while)."""
    mod, _why = read_mod(root)
    if mod is None:
        return []
    xml = [rel for rel, _size in files if rel.lower().endswith(".xml")]
    say(tr("common.import_checking_conditions"))
    out = stamp_lines(mod, ctx) + duplicate_lines(mod, root, ctx)
    out += condition_lines(read_conditions(root, xml, mod.m_mod_id), mod, ctx)
    say(tr("common.import_checking_lint"))
    return out + lint_lines(root, game, ctx)
