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
    p = sub.add_parser(
        "handover",
        help="hand an imported project's patch files to the Assets and Texts apps (prints what is lost first)",
    )
    p.add_argument("folder", type=Path, help="the imported project's folder")
    p.add_argument("--yes", action="store_true", help="do it after the report (without: the report only)")
    p.add_argument("--layered", action="store_true", help="edit on top of the mod's own files (they stay as they are)")
    p.add_argument("--undo", action="store_true", help="put the original files back (--yes: also drop later edits)")
    p.add_argument("--settings", type=Path, default=None, help="the Toolbox's settings file (the game folders)")
    p.set_defaults(func=cmd_handover)


def _say(text: str) -> None:
    if text:
        print(text, file=sys.stderr, flush=True)


def cmd_handover(args: argparse.Namespace) -> int:
    from ..suite import project as projects
    from ..suite.settings import Settings, settings_file
    from . import handover, layered
    from .assetgame import open_installed

    proj = projects.Project.open(args.folder)
    try:
        if args.undo and args.layered:
            layered.undo(proj, discard=args.yes)
            print(tr("assets.layered_undone"))
            return 0
        if args.undo:
            print(tr("assets.handover_undone", n=handover.undo(proj, discard=args.yes)))
            return 0
        settings = Settings.load(args.settings or settings_file())
        way = layered if args.layered else handover
        game = open_installed(settings, proj.game, _say)
        made = way.plan(proj, game, _say, handover.mods_folders(settings, proj.game))
        print("\n".join(made.lines()))
        if args.yes and args.layered:
            print(tr("assets.layered_taken", include=layered.take(proj, made)["include"]))
        elif args.yes:
            print(tr("assets.handover_taken", n=len(handover.take(proj, made).files)))
    except (handover.HandoverRefused, layered.LayerRefused) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0
