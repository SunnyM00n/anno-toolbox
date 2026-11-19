"""A mod put into a mods folder and taken out again.

``install_path`` takes a mod folder, a folder of mods or a ``.zip`` and copies each mod it holds into the mods folder
(an older copy of the same ModID is replaced: the old one goes to the recycle bin only after the new one is in place);
``remove_mod`` takes a mod folder out, to the recycle bin. A failure is an ``InstallError`` with a sentence. Nothing
is written outside the mods folder; a folder that is half done starts with ``.`` so the loader never sees it.
An error ``progress`` raises stops the install between two steps, the mods placed until then in ``placed``.
``docs/mod-manager.md`` "Install and remove". Stdlib only.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import tr
from ..modloader.mods import MODINFO_NAMES, Mod, parked, read_mod
from ..modloader.modswap import STAGING, InstallError, Trash, check_folder_name, place_folder, recycle

MAX_ZIP_BYTES = 16 * 1024**3
SEARCH_DEPTH = 2  # how far below a folder or zip a mod's ``modinfo`` is looked for
Progress = Callable[[str], None]  # a step's sentence, "" for a mere look whether to go on; it may raise to stop


@dataclass
class Installed:
    mod_id: str
    folder: Path
    replaced: bool = False
    notes: list[str] = field(default_factory=list)
    notice: str = ""  # what the running game needs for it (the live link)


def find_mod_folders(source: Path) -> list[Path]:
    """The mod folders at ``source``: the folder itself when it holds a ``modinfo``, else the folders below it that
    do (one level, then two: a zip often has one folder around the mod), parked ones left out."""
    source = Path(source)
    if any((source / n).is_file() for n in MODINFO_NAMES):
        return [source]
    level = [source]
    for _ in range(SEARCH_DEPTH):
        level = [d for p in level for d in sorted(p.iterdir()) if d.is_dir() and not parked(d.name)]
        found = [d for d in level if any((d / n).is_file() for n in MODINFO_NAMES)]
        if found:
            return found
    return []


def install_path(
    source: Path,
    mods_dir: Path,
    anno: int | None,
    trash: Trash = recycle,
    progress: Progress | None = None,
    placed: list[Installed] | None = None,
) -> list[Installed]:
    """Install every mod at ``source`` (a folder, a folder of mods or a ``.zip``) into ``mods_dir``. ``anno``: the
    game's number in ``modinfo`` (117: 8); a mod that says another is refused with the loader's words. ``placed``:
    the list the result is built in, so a caller that is stopped or fails knows what is in."""
    source, mods_dir = Path(source), Path(mods_dir)
    job = _Job(trash, progress or (lambda _text: None), [] if placed is None else placed)
    if source.is_file() and source.suffix.lower() == ".zip":
        return _install_zip(source, mods_dir, anno, job)
    if not source.is_dir():
        raise InstallError(tr("mods.neither_folder_nor_zip", path=source))
    if source.parent.resolve() == mods_dir.resolve() or source.resolve() == mods_dir.resolve():
        raise InstallError(tr("mods.mod_mods_folder_already"))
    if source.resolve() in mods_dir.resolve().parents:
        raise InstallError(tr("mods.mods_folder_inside_pick_own"))
    plans = _plan_all([(f, f.name, True) for f in _folders(source)], mods_dir, anno)
    return _place_all(plans, mods_dir, job, move=False)


def _folders(source: Path) -> list[Path]:
    found = find_mod_folders(source)
    if not found:
        raise InstallError(tr("mods.no_modinfo_json_folders_just", name=source.name))
    return found


def _install_zip(path: Path, mods_dir: Path, anno: int | None, job: _Job) -> list[Installed]:
    mods_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix=STAGING + "zip-", dir=mods_dir) as tmp:
        root = Path(tmp) / "unpacked"
        job.progress(tr("mods.unpacking_name", name=path.name))
        _extract(path, root, job.progress)
        found = _folders(root)
        plans = _plan_all([(f, path.stem if f == root else f.name, f != root) for f in found], mods_dir, anno)
        return _place_all(plans, mods_dir, job, move=True)


def _extract(path: Path, into: Path, progress: Progress = lambda _text: None) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
                raise InstallError(
                    tr(
                        "mods.unpacks_more_gib_not_installed",
                        name=path.name,
                        size=MAX_ZIP_BYTES >> 30,
                    )
                )
            base = into.resolve()
            for info in infos:
                target = (into / info.filename).resolve()
                if (
                    info.filename.startswith(("/", "\\"))
                    or ":" in info.filename.split("/")[0]
                    or base not in (target, *target.parents)
                ):
                    raise InstallError(
                        tr(
                            "mods.holds_file_outside_own_folder",
                            name=path.name,
                            file=info.filename,
                        )
                    )
            for info in infos:
                progress("")
                zf.extract(info, into)
    except zipfile.BadZipFile as exc:
        raise InstallError(tr("mods.not_readable_zip", name=path.name, reason=exc)) from exc
    except OSError as exc:
        raise InstallError(tr("mods.could_not_unpacked", name=path.name, reason=exc.strerror or exc)) from exc


@dataclass
class _Job:
    """What one install carries through its steps."""

    trash: Trash
    progress: Progress
    placed: list[Installed]


@dataclass
class _Plan:
    """One mod that is going to be installed: checked, nothing done yet."""

    folder: Path
    mod: Mod
    dest: Path
    replaced: bool = False
    notes: list[str] = field(default_factory=list)


def _plan_one(folder: Path, name: str, mods_dir: Path, anno: int | None, own_name: bool) -> _Plan:
    """Check one mod folder for ``mods_dir``: it reads, its folder name there is a single safe name (its ModID when the
    folder's own name is not usable or the folder is the whole zip), and it does not overwrite another mod."""
    mod, why = read_mod(folder, anno)
    if mod is None:
        raise InstallError(tr("mods.name_not_mod_loader_takes", name=folder.name, why=why))
    name = name if own_name and name and not parked(name) else mod.mod_id
    check_folder_name(name)
    dest = mods_dir / name
    if os.path.abspath(dest.parent) != os.path.abspath(mods_dir):
        raise InstallError(tr("mods.leave_mods_folder_not_installed", name=name))
    plan = _Plan(folder, mod, dest)
    if os.path.lexists(dest):
        old, _ = read_mod(dest, anno)
        if old is None or old.mod_id != mod.mod_id:
            raise InstallError(
                tr(
                    "mods.folder_mods_holds_another_mod",
                    name=name,
                    mod=old.mod_id if old else tr("common.no_mod"),
                )
            )
        plan.replaced = True
    if mods_dir.is_dir():
        for other in sorted(p for p in mods_dir.iterdir() if p.is_dir() and p != dest and not parked(p.name)):
            twin, _ = read_mod(other, anno)
            if twin is not None and twin.mod_id == mod.mod_id:
                plan.notes.append(
                    tr(
                        "mods.folder_same_modid_loader_keeps",
                        name=other.name,
                    )
                )
    return plan


def _plan_all(items: list[tuple[Path, str, bool]], mods_dir: Path, anno: int | None) -> list[_Plan]:
    """Check every mod of one install before any is placed: one bad mod, or two landing on the same folder, and
    nothing is installed. The message names every problem."""
    plans: list[_Plan] = []
    problems: list[str] = []
    for folder, name, own_name in items:
        try:
            plans.append(_plan_one(folder, name, mods_dir, anno, own_name))
        except InstallError as exc:
            problems.append(str(exc))
    seen: dict[str, Path] = {}
    for plan in plans:
        key = plan.dest.name.lower()
        if key in seen:
            problems.append(
                tr(
                    "mods.both_go_folder",
                    first=plan.folder.name,
                    second=seen[key].name,
                    folder=plan.dest.name,
                )
            )
        seen[key] = plan.folder
    if problems:
        raise InstallError("; ".join(problems) + (": " + tr("mods.nothing_installed") if len(items) > 1 else ""))
    return plans


def _place_all(plans: list[_Plan], mods_dir: Path, job: _Job, move: bool) -> list[Installed]:
    mods_dir.mkdir(parents=True, exist_ok=True)
    done = job.placed
    for plan in plans:
        job.progress(tr("mods.installing_name", name=plan.mod.mod_id))
        try:
            done.append(_place(plan, mods_dir, job, move))
        except InstallError as exc:
            if done:
                raise InstallError(
                    tr(
                        "mods.reason_already_installed_names",
                        reason=exc,
                        names=", ".join(d.mod_id for d in done),
                    )
                ) from exc
            raise
    return done


def _place(plan: _Plan, mods_dir: Path, job: _Job, move: bool) -> Installed:
    """Copy (or move) the folder into place through a staging folder in ``mods_dir`` (``modswap.place_folder``)."""

    def look(_folder: str, _names: list[str]) -> tuple[()]:
        job.progress("")  # once per folder copied: where a stop ends the copy, the staging folder removed
        return ()

    result = Installed(plan.mod.mod_id, plan.dest, plan.replaced, list(plan.notes))
    result.notes += place_folder(plan.folder, plan.dest, job.trash, move=move, ignore=look)
    return result


def remove_mod(folder: Path, roots: list[Path], trash: Trash = recycle) -> str:
    """Take the mod folder ``folder`` out of its mods folder (one of ``roots``) into the recycle bin; returns where it
    went. A path that is no direct subfolder of a root is refused. A folder that is a junction or symlink is taken out
    as the link: the folder it points to is not touched."""
    folder = Path(os.path.abspath(folder))
    if not any(_same(folder.parent, r) for r in roots) or not folder.is_dir():
        raise InstallError(tr("mods.not_mod_folder_mods_removed", path=folder))
    try:
        where = trash(folder)
    except OSError as exc:
        raise InstallError(tr("mods.could_not_removed", name=folder.name, reason=exc.strerror or exc)) from exc
    return str(where) if where else tr("mods.recycle_bin")


def _same(a: Path, b: Path) -> bool:
    """Two folders that are one (compared through links: it is the parent, not the mod, that may be a link here)."""
    return a.resolve() == Path(b).resolve()
