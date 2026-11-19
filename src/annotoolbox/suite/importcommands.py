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
from . import archivemod, archivemodbuild, carriedpart, importchecks, importmeta, importresume, projectimport, reimport
from . import project as projects
from .settings import Settings, settings_file


def register(sub: argparse._SubParsersAction) -> argparse._SubParsersAction:
    """The ``mod`` group with ``import``; returns the group's subparsers for the commands the apps add to it."""
    group = sub.add_parser("mod", help="mods: import a whole mod into a project (docs/mod-import.md)")
    commands = group.add_subparsers(dest="mod_command", required=True)
    add_import(commands)
    add_resume(commands)
    add_reimport(commands)
    return commands


def add_import(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "import",
        help="import a mod (a folder or a .zip, or an older game's archive) into a new project folder; the mod is never changed",
    )
    p.add_argument(
        "source", type=Path, help="the mod folder (the one with modinfo.json), a .zip, or an older game's .rda archive"
    )
    p.add_argument(
        "--game",
        default=None,
        help="an older game's archive mod: the game it is for (anno2070, anno1404, anno1701, anno2205); a folder of "
        "loose files by game path needs it, an archive only when its version leaves two games (V2.0: 1404, 2070)",
    )
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
    if getattr(args, "game", None) or (args.source.is_file() and args.source.suffix.lower() == ".rda"):
        return _import_archive(args, settings, path)
    report = projectimport.import_mod(
        args.source,
        args.folder,
        settings,
        _say,
        link=not args.copy,
        hash_files=not args.no_hash,
        context=importchecks.context_from_settings(settings, path.parent / importresume.PENDING),
    )
    settings.save(path)
    print("\n".join(report.lines()))
    print(tr("common.import_took", seconds=f"{report.seconds:.1f}"))
    if not (args.build or args.verify):
        return 0
    proj = projects.Project.open(args.folder)
    started = time.monotonic()
    result = projects.build(proj, _parts(), settings, _say)
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


def _import_archive(args: argparse.Namespace, settings: Settings, path: Path) -> int:
    """``mod import`` for an older game's archive mod (docs/mod-import.md, section 16): import, and with ``--build`` /
    ``--verify`` build the archive again and compare its members (and its bytes) with the source."""
    try:
        report = archivemod.import_archive_mod(args.source, args.folder, args.game, settings, _say, link=not args.copy)
    except projectimport.ImportRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    settings.save(path)
    print("\n".join(report.lines()))
    print(tr("common.import_took", seconds=f"{report.seconds:.1f}"))
    if not (args.build or args.verify):
        return 0
    proj = projects.Project.open(args.folder)
    result = projects.build(proj, _parts(), settings, _say, _deliveries())
    print("\n".join(result.lines()))
    if not args.verify:
        return 0
    built = proj.build_dir / report.archive
    diff = archivemodbuild.compare_members(args.source, built, _say)
    print("\n".join(diff.lines()))
    if args.source.is_file():
        same = archivemodbuild.same_bytes(args.source, built)
        if same:
            print(tr("common.archive_mod_bytes_same"))
        else:
            print(tr("common.archive_mod_bytes_differ"))
    return 0 if diff.identical else 1


def _deliveries() -> dict[str, projects.Delivery]:
    """How the older games take a project: with the island deliveries where the apps can be imported, else the
    archive mods alone."""
    try:
        from . import apps

        return apps.deliveries()
    except ImportError:
        return archivemodbuild.deliveries()


def _parts() -> list[projects.Part]:
    """The parts the build merges: every app's (the files an import gave an app are built from its folder), the
    carried part alone where the apps cannot be imported (no Qt)."""
    try:
        from . import apps

        return apps.parts()
    except ImportError:
        return [carriedpart.PART]


def _source_folder(source: Path, proj: projects.Project) -> tuple[Path, tuple[str, ...]]:
    """What to compare the build with: the mod itself, or for a ``.zip`` (no folder) the files the import kept, the
    ``modinfo`` the build adds then left out of the comparison."""
    if source.is_file():
        return carriedpart.carried_root(proj), MODINFO_NAMES
    return source, ()


def add_resume(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "resume", help="finish an import that was cut short (the project folder holds import-state.json), or remove it"
    )
    p.add_argument("folder", type=Path, help="the half-made project's folder")
    p.add_argument(
        "--discard", action="store_true", help="remove the half-made project instead (the mod is not touched)"
    )
    p.add_argument("--list", action="store_true", help="list the half-made imports the Toolbox knows of and stop")
    p.add_argument("--settings", type=Path, default=None, help="the Toolbox's settings file")
    p.set_defaults(func=cmd_resume)


def cmd_resume(args: argparse.Namespace) -> int:
    path = args.settings or settings_file()
    registry = path.parent / importresume.PENDING
    if args.list:
        print(chr(10).join(p.line() for p in importresume.pending(registry)) or tr("common.import_pending_none"))
        return 0
    if args.discard:
        gone = importresume.discard(args.folder, registry)
        print(tr("common.import_discarded" if gone else "common.import_resume_none", folder=args.folder))
        return 0 if gone else 1
    settings = Settings.load(path)
    try:
        report = importresume.resume(
            args.folder, settings, _say, importchecks.context_from_settings(settings, registry), resumable=False
        )
    except projectimport.ImportRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    settings.save(path)
    print(chr(10).join(report.lines()))
    return 0


def add_reimport(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "reimport",
        help="compare a mod with its project by the import's manifest and take what changed (never overwrites an edit unasked)",
    )
    p.add_argument("project", type=Path, help="the imported project's folder")
    p.add_argument(
        "source", type=Path, nargs="?", default=None, help="the mod (default: the one the project came from)"
    )
    p.add_argument("--apply", action="store_true", help="carry it out (without: only the comparison is printed)")
    p.add_argument(
        "--conflicts",
        choices=(reimport.KEEP, reimport.TAKE),
        default=reimport.KEEP,
        help="for a file both sides changed: keep the project's (default) or take the mod's",
    )
    p.set_defaults(func=cmd_reimport)


def cmd_reimport(args: argparse.Namespace) -> int:
    meta = importmeta.read_meta(args.project)
    source = args.source or Path(meta.source if meta else "")
    try:
        made = reimport.plan(args.project, source, _say)
    except projectimport.ImportRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(chr(10).join(made.lines()))
    if not args.apply:
        made.dispose()
        return 0
    print(chr(10).join(reimport.apply(made, reimport.choose_all(made, args.conflicts), _say)))
    return 0
