"""The ``unreferenced`` command: the files of an installed game that no other file names, and what names a file.

``register(sub)`` adds it to a parser (the Toolbox's command line); the graph is ``graph``'s,
the verdicts ``rules``'s (docs/references.md).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

GAMES = ("anno117", "anno1800", "anno2205", "anno2070", "anno1404", "anno1701")


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "unreferenced",
        help="the files of an installed game that no other file names (and, with --who, what names a file): the "
        "reference graph is built once and kept beside the game; the list as CSV. 'Unreferenced' means no reference "
        "found in the data - the program may still load the file (docs/references.md)",
    )
    parser.add_argument("game", choices=GAMES)
    parser.add_argument("--folder", default="", help="the install (default: the folder in the suite's settings)")
    parser.add_argument("--csv", default="", help="write the unreferenced files here (path, suffix, archive)")
    parser.add_argument("--who", default="", help="list what names this game path, and what it names")
    parser.add_argument("--rebuild", action="store_true", help="read every file again, not the kept graph")
    parser.set_defaults(func=cmd_unreferenced)


def cmd_unreferenced(args: argparse.Namespace) -> int:
    from ..sources import gametree
    from ..suite.settings import installed
    from . import graph, rules

    settings, folder = installed(args.game, args.folder)
    if folder is None:
        print(f"unreferenced: no install folder for {args.game} (--folder, or set it in the suite)", file=sys.stderr)
        return 2
    tree = gametree.open_game(args.game, folder, settings.index_dir(args.game))
    cache = graph.cache_file(settings.index_dir(args.game), args.game)
    if args.rebuild and cache.exists():
        cache.unlink()
    found = graph.build(tree, cache, lambda done, total: print(f"  {done} of {total} files", file=sys.stderr))
    if args.who:
        key = args.who.replace("\\", "/").lower()
        print("\n".join([f"named by ({len(found.referenced_by(key))}):", *found.referenced_by(key)]))
        print("\n".join([f"names ({len(found.references(key))}):", *found.references(key)]))
        return 0
    verdicts = found.verdicts(tree.m_paths)
    counts = {
        verdict_class: sum(1 for verdict in verdicts.values() if verdict == verdict_class)
        for verdict_class in rules.CLASSES
    }
    print(
        f"{args.game}: {len(tree.m_paths)} files, {found.m_scanned} read; "
        + ", ".join(f"{verdict_class} {count}" for verdict_class, count in counts.items())
    )
    if args.csv:
        Path(args.csv).write_text(graph.unreferenced_csv(tree, found, verdicts), encoding="utf-8", newline="\n")
        print(f"wrote {args.csv}")
    return 0
