"""The ``mod import`` command: import a mod into a new project, optionally build it back and compare the build with
the mod (docs/mod-import.md). ``add_import(sub)`` adds it to a ``mod`` command group's parser; ``register(sub)`` adds
the whole ``mod`` group (the Toolbox's). Needs no window.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ..infra.i18n import tr
from ..modloader.mods import MODINFO_NAMES
from . import carriedpart, projectimport
from . import project as projects
from .settings import Settings, settings_file


def register(sub: argparse._SubParsersAction) -> None:
    group = sub.add_parser("mod", help="mods: import a whole mod into a project (docs/mod-import.md)")
    add_import(group.add_subparsers(dest="mod_command", required=True))


def add_import(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "import",
        help="import a mod (a folder or a .zip) into a new project folder; the mod is never changed",
    )
    p.add_argument("source", type=Path, help="the mod folder (the one with modinfo.json) or a .zip")
    p.add_argument("folder", type=Path, help="the new, empty folder of the project")
    p.add_argument("--copy", action="store_true", help="copy every file (default: hard links where the disk allows)")
    p.add_argument(
        "--no-hash",
        action="store_true",
        help="do not read every file for its SHA-256 (much faster for a very large mod)",
    )
    p.add_argument("--build", action="store_true", help="then build the mod into <folder>/build/<mod id>")
    p.add_argument(
        "--verify",
        action="store_true",
        help="then build, and compare the build with the source (exit 1 if not identical)",
    )
    p.add_argument("--settings", type=Path, default=None, help="the Toolbox's settings file (the own GUID reserve)")
    p.set_defaults(func=cmd_import)


def _say(text: str) -> None:
    if text:
        print(text, file=sys.stderr, flush=True)


def cmd_import(args: argparse.Namespace) -> int:
    path = args.settings or settings_file()
    settings = Settings.load(path)
    report = projectimport.import_mod(
        args.source, args.folder, settings, _say, link=not args.copy, hash_files=not args.no_hash
    )
    settings.save(path)
    print("\n".join(report.lines()))
    print(tr("common.import_took", seconds=f"{report.seconds:.1f}"))
    if not (args.build or args.verify):
        return 0
    proj = projects.Project.open(args.folder)
    started = time.monotonic()
    result = projects.build(proj, [carriedpart.PART], settings, _say)
    print("\n".join(result.lines()))
    print(tr("common.import_built_in", seconds=f"{time.monotonic() - started:.1f}"))
    if not args.verify:
        return 0
    started = time.monotonic()
    source, ignore = _source_folder(args.source, proj)
    diff = carriedpart.compare_trees(source, proj.build_dir, _say, ignore)
    print("\n".join(diff.lines()))
    print(tr("common.import_compared_in", seconds=f"{time.monotonic() - started:.1f}"))
    return 0 if diff.identical else 1


def _source_folder(source: Path, proj: projects.Project) -> tuple[Path, tuple[str, ...]]:
    """What to compare the build with: the mod itself, or for a ``.zip`` (no folder) the files the import kept, the
    ``modinfo`` the build adds then left out of the comparison."""
    if source.is_file():
        return carriedpart.carried_root(proj), MODINFO_NAMES
    return source, ()
