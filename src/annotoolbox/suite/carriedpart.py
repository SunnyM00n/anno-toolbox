"""The build side of an imported mod: the files no app edits (``mod-files``) as the first part of the build, and the
check that a build equals the mod it came from (docs/mod-import.md, sections 3.2 and 3.3). Stdlib only.

``PART`` gives the project's ``mod-files/<game>`` to the build as paths (``buildout.FileRef``), never as bytes, so a
mod of half a million files is not held in memory. ``compare_trees(a, b)`` is the round-trip test: the same set of
paths (compared without regard to case, the spelling of ``a`` kept) and the same bytes in every file.
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
from .buildout import WORKERS, FileRef, same

CHUNK = 2000


def carried_root(project: projects.Project) -> Path:
    return project.folder / importmeta.MOD_FILES / project.game


def files_of(root: Path) -> dict[str, int]:
    """Mod path -> size of every file below ``root`` (one walk; a half-written ``.part`` file is none)."""
    found: dict[str, int] = {}
    for _here, base, _dirs, names in walk_sizes(root):
        prefix = "" if base == "." else f"{base}/"
        found.update({f"{prefix}{n}": size for n, size in names if not n.endswith(projects.PART_SUFFIX)})
    return found


def _has_work(project: projects.Project) -> bool:
    return (
        project.game in projects.ALL_GAMES
        and carried_root(project).is_dir()
        and importmeta.read_meta(project.folder) is not None
    )


def _render(project: projects.Project, _settings, log: Callable[[str], None]) -> dict[str, FileRef]:
    root = carried_root(project)
    held = appfolders.held_paths(project)  # a whole-file app's file is written by the app's part, once
    files = {rel: FileRef(os.path.join(root, rel)) for rel in files_of(root) if rel.lower() not in held}
    log(tr("common.carried_files_n", n=number(len(files))))
    return files


PART = projects.Part("carried", N_("common.carried_files"), _has_work, _render)


@dataclass
class TreeDiff:
    """How two mod folders differ; ``same`` the files that are equal, ``shared`` of them the ones that are one file on
    the disk (hard-linked: equal by construction)."""

    missing: list = field(default_factory=list)  # in ``a``, not in ``b``
    extra: list = field(default_factory=list)  # in ``b``, not in ``a``
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
    a: Path, b: Path, say: Callable[[str], None] = lambda _t: None, ignore: tuple[str, ...] = ()
) -> TreeDiff:
    """Compare mod folder ``a`` with ``b`` file by file; ``say`` is called between chunks and may raise to stop;
    ``ignore``: paths of ``b`` that do not count as extra."""
    left = files_of(Path(a))
    right = {rel.lower(): (rel, size) for rel, size in files_of(Path(b)).items()}
    diff = TreeDiff()
    pairs: list[tuple[str, str]] = []
    for rel, size in left.items():
        other = right.pop(rel.lower(), None)
        if other is None:
            diff.missing.append(rel)
        elif other[1] != size:
            diff.different.append(rel)
        else:
            pairs.append((rel, other[0]))
    ignored = {i.lower() for i in ignore}
    diff.extra = sorted(rel for key, (rel, _size) in right.items() if key not in ignored)

    def one(pair: tuple[str, str]) -> tuple[bool, bool]:
        pa, pb = os.path.join(a, pair[0]), os.path.join(b, pair[1])
        try:
            shared = os.path.samestat(os.stat(pa), os.stat(pb))
        except OSError:
            return False, False
        return shared or same(FileRef(pa), FileRef(pb)), shared

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
