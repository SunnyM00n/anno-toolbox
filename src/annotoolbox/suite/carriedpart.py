"""The build side of an imported mod: the files no app edits (``mod-files``) as the first part of the build, and the
check that a build equals the mod it came from (docs/mod-import.md, sections 3.2 and 3.3). Stdlib only.

``PART`` gives the project's ``mod-files/<game>`` to the build as paths (``buildout.CFileRef``), never as bytes, so a
mod of half a million files is not held in memory. ``compare_trees(a, b)`` is the round-trip test: the same set of
paths (compared without regard to case, the spelling of the first folder kept) and the same bytes in every file.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import N_, number, tr
from ..modloader.mods import walk_sizes
from . import appfolders, importmeta
from . import project as projects
from .buildout import WORKERS, CFileRef, same

CHUNK = 2000


def carried_root(project: projects.CProject) -> Path:
    return project.folder / importmeta.MOD_FILES / project.game


def files_of(root: Path) -> dict[str, int]:
    """Mod path -> size of every file below ``root`` (one walk; a half-written ``.part`` file is none)."""
    found: dict[str, int] = {}
    for _here, base, _dirs, names in walk_sizes(root):
        prefix = "" if base == "." else f"{base}/"
        found.update({f"{prefix}{name}": size for name, size in names if not name.endswith(projects.PART_SUFFIX)})
    return found


def _has_work(project: projects.CProject) -> bool:
    return (
        project.game in projects.ALL_GAMES
        and carried_root(project).is_dir()
        and importmeta.read_meta(project.folder) is not None
    )


def _render(project: projects.CProject, _settings, log: Callable[[str], None]) -> dict[str, CFileRef]:
    root = carried_root(project)
    held = appfolders.held_paths(project)  # a whole-file app's file is written by the app's part, once
    files = {
        relative_path: CFileRef(os.path.join(root, relative_path))
        for relative_path in files_of(root)
        if relative_path.lower() not in held
    }
    log(tr("common.carried_files_n", n=number(len(files))))
    return files


PART = projects.SPart("carried", N_("common.carried_files"), _has_work, _render)


@dataclass
class STreeDiff:
    """How two mod folders differ; ``same`` the files that are equal, ``shared`` of them the ones that are one file on
    the disk (hard-linked: equal by construction)."""

    missing: list = field(default_factory=list)  # in the first folder, not in the second
    extra: list = field(default_factory=list)  # in the second folder, not in the first
    different: list = field(default_factory=list)  # in both, other bytes
    same: int = 0
    shared: int = 0

    @property
    def identical(self) -> bool:
        return not (self.missing or self.extra or self.different)

    def lines(self, shown: int = 8) -> list[str]:
        out = [tr("common.tree_compared", same=number(self.same), shared=number(self.shared))]
        for text, items in (
            (N_("common.tree_missing"), self.missing),
            (N_("common.tree_extra"), self.extra),
            (N_("common.tree_different"), self.different),
        ):
            if items:
                more = f" ... {number(len(items) - shown)}" if len(items) > shown else ""
                out.append(tr(text, n=number(len(items)), list=", ".join(items[:shown]) + more))
        return out


def compare_trees(
    folder: Path, other_folder: Path, say: Callable[[str], None] = lambda _t: None, ignore: tuple[str, ...] = ()
) -> STreeDiff:
    """Compare mod folder ``folder`` with ``other_folder`` file by file; ``say`` is called between chunks and may
    raise to stop; ``ignore``: paths of ``other_folder`` that do not count as extra."""
    left = files_of(Path(folder))
    right = {
        relative_path.lower(): (relative_path, size) for relative_path, size in files_of(Path(other_folder)).items()
    }
    diff = STreeDiff()
    pairs: list[tuple[str, str]] = []
    for relative_path, size in left.items():
        other = right.pop(relative_path.lower(), None)
        if other is None:
            diff.missing.append(relative_path)
        elif other[1] != size:
            diff.different.append(relative_path)
        else:
            pairs.append((relative_path, other[0]))
    ignored = {i.lower() for i in ignore}
    diff.extra = sorted(relative_path for key, (relative_path, _size) in right.items() if key not in ignored)

    def one(pair: tuple[str, str]) -> tuple[bool, bool]:
        left_path, right_path = os.path.join(folder, pair[0]), os.path.join(other_folder, pair[1])
        try:
            shared = os.path.samestat(os.stat(left_path), os.stat(right_path))
        except OSError:
            return False, False
        return shared or same(CFileRef(left_path), CFileRef(right_path)), shared

    with ThreadPoolExecutor(WORKERS) as pool:
        for start in range(0, len(pairs), CHUNK):
            chunk = pairs[start : start + CHUNK]
            for pair, (equal, shared) in zip(chunk, pool.map(one, chunk), strict=True):
                if equal:
                    diff.same += 1
                    diff.shared += shared
                else:
                    diff.different.append(pair[0])
            say("")
    return diff


def build_parts() -> list[projects.SPart]:
    """The parts a project's build merges: every app's (the files an import gave an app are built from its folder),
    the carried part alone where the apps cannot be imported (no Qt)."""
    try:
        from . import apps

        return apps.parts()
    except ImportError:
        return [PART]


def round_trip(mod: Path, folder: Path, say: Callable[[str], None] = lambda _t: None) -> STreeDiff:
    """The import's round trip over a built mod (``mod build --verify``): ``mod`` imported as a new project in
    ``folder`` (hard links, nothing of ``mod`` changed, no settings touched), built back with ``build_parts``, and the
    build compared with ``mod``. An identical result means the Toolbox opens the mod and loses no file."""
    from .projectimport import import_mod
    from .settings import CSettings

    import_mod(Path(mod), Path(folder), None, say, use_links=True, is_hashed=False)
    project = projects.CProject.open(Path(folder))
    projects.build(project, build_parts(), CSettings(), say)
    return compare_trees(Path(mod), project.build_dir, say)
