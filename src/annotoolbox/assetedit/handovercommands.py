"""The ``mod handover`` command: hand an imported project's patch files to the Assets and Texts apps (way A) or, with
``--layered``, let the Assets app edit on top of the mod (way B), or undo either (docs/mod-import.md, "Phase 2 as built"
and "Phase 6 as built"). ``add_handover(sub)`` adds it to the ``mod`` command group's parser. Needs no window; the
heavy modules load when the command runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..infra.i18n import tr


def add_handover(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "handover",
        help="hand an imported project's patch files to the Assets and Texts apps (prints what is lost first)",
    )
    parser.add_argument("folder", type=Path, help="the imported project's folder")
    parser.add_argument("--yes", action="store_true", help="do it after the report (without: the report only)")
    parser.add_argument(
        "--layered", action="store_true", help="edit on top of the mod's own files (they stay as they are)"
    )
    parser.add_argument(
        "--undo", action="store_true", help="put the original files back (--yes: also drop later edits)"
    )
    parser.add_argument("--settings", type=Path, default=None, help="the Toolbox's settings file (the game folders)")
    parser.set_defaults(func=cmd_handover)


def _say(text: str) -> None:
    if text:
        print(text, file=sys.stderr, flush=True)


def cmd_handover(args: argparse.Namespace) -> int:
    from ..suite import project as projects
    from ..suite.settings import CSettings, settings_file
    from . import handover, layered
    from .assetgame import open_installed

    project = projects.CProject.open(args.folder)
    try:
        if args.undo and args.layered:
            layered.undo(project, discard=args.yes)
            print(tr("assets.layered_undone"))
            return 0
        if args.undo:
            print(tr("assets.handover_undone", n=handover.undo(project, discard=args.yes)))
            return 0
        settings = CSettings.load(args.settings or settings_file())
        way = layered if args.layered else handover
        game = open_installed(settings, project.game, _say)
        made = way.plan(project, game, _say, handover.mods_folders(settings, project.game))
        print("\n".join(made.lines()))
        if args.yes and args.layered:
            print(tr("assets.layered_taken", include=layered.take(project, made)["include"]))
        elif args.yes:
            print(tr("assets.handover_taken", n=len(handover.take(project, made).files)))
    except (handover.CHandoverRefused, layered.CLayerRefused) as error:
        print(error, file=sys.stderr)
        return 1
    return 0
