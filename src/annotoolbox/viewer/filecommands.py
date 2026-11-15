"""The ``viewer-files`` command: every model config of an installed game looked up as the viewer looks its files up.

``register(sub)`` adds it to a parser (the Toolbox's command line); the check is ``filecheck``'s,
with ``--islands`` the island editor's drawing of a sample of the game's islands too (docs/viewer.md).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

GAMES = ("anno117", "anno1800", "anno2205", "anno2070", "anno1404", "anno1701")


def register(sub: argparse._SubParsersAction) -> None:
    f = sub.add_parser(
        "viewer-files",
        help="every model config of an installed game looked up as the viewer and the island editor look its files "
        "up: meshes, textures of every slot, nested configs, effects, animations; what finds no file, by reason, and "
        "the textures that do not decode (docs/viewer.md 'How files are found')",
    )
    f.add_argument("game", choices=GAMES)
    f.add_argument("--folder", default="", help="the install (default: the folder in the suite's settings)")
    f.add_argument("--sample", type=int, default=0, help="files check this many configs, picked evenly (default: all)")
    f.add_argument("--no-decode", action="store_true", help="skip the decode check of the textures found")
    f.add_argument("--examples", type=int, default=4, help="names listed per kind and reason")
    f.add_argument(
        "--islands",
        type=int,
        default=0,
        help="also open this many of the game's islands in the island "
        "editor and draw what they place its way (their configs are checked by the viewer's rules too)",
    )
    f.set_defaults(func=cmd_viewer_files)


def cmd_viewer_files(args: argparse.Namespace) -> int:
    from ..sources import gametree
    from ..suite.settings import installed
    from . import filecheck

    settings, folder = installed(args.game, args.folder)
    if folder is None:
        print(f"viewer-files: no install folder for {args.game} (--folder, or set it in the suite)", file=sys.stderr)
        return 2
    placed, islands = (), None
    if args.islands:
        import tempfile

        from ..editor.islandfiles import check_islands

        with tempfile.TemporaryDirectory(prefix="anno-island-files check-") as scratch:  # the editor's PNGs: gone after
            islands = check_islands(args.game, folder, Path(scratch), args.islands,
                                    lambda p: print(f"  island {p}", file=sys.stderr))  # fmt: skip
        placed = tuple(sorted(islands.configs))
    tree = gametree.open_game(args.game, folder, settings.index_dir(args.game))
    options = filecheck.CheckOptions(args.sample, not args.no_decode, args.examples, placed)
    report = filecheck.check(tree, options, lambda k, n: print(f"  {k} of {n} configs", file=sys.stderr))
    print("\n".join(report.lines() + (islands.lines() if islands is not None else [])))
    return 0
