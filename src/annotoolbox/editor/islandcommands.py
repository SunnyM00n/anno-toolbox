"""The ``island`` commands of the command line: ``island port`` makes islands of 117 or 1800 into islands of another
region or of the other game of their engine generation, and islands of an older game where an edition ports them
(``islandconvert``), all into one mod; ``island regions`` lists a game's regions.

``register(sub)`` adds them (the Toolbox's ``island`` group and ``anno-toolbox island``); the port is imported
when a command runs, so the parser needs nothing beyond the standard library. docs/commands.md "island".
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..infra.i18n import tr
from ..islands import regions


def env_var(key: str) -> str:
    """The environment variable naming a game's install (``ANNO117_DIR``, ``ANNO2070_DIR``)."""
    return f"ANNO{key.removeprefix('anno').upper()}_DIR"


def register(sub: argparse._SubParsersAction) -> None:
    c = sub.add_parser("port", help="port islands to another region of their game, or to another game")
    c.add_argument("game", help="the game the islands are of: anno117, anno1800, or an older game this edition ports")
    c.add_argument("islands", nargs="+", help="island files (game paths: .a7m, an older game's .isd)")
    c.add_argument("--to", required=True, help="the target region, <game>:<region> (island regions lists them)")
    c.add_argument("--out", type=Path, required=True, help="the mods folder; the mod goes into <out>/<mod id>")
    c.add_argument("--mod-id", default="island-port", help="the mod's id and folder name")
    c.add_argument("--name", default="", help="the new island's name (one island only)")
    c.add_argument("--pool-region", default="", help="the pool region it is registered in (default the target's)")
    c.add_argument("--src", type=Path, help="the source game's install (default ANNO117_DIR, ANNO1800_DIR, ...)")
    c.add_argument("--dst", type=Path, help="the target game's install, for a port to the other game")
    c.add_argument("--cache", type=Path, help="where the install index and the regions' inventories are kept")
    c.set_defaults(func=cmd_convert)
    r = sub.add_parser("regions", help="the regions of a game and what their islands name")
    r.add_argument("game", choices=list(regions.GENERATION))
    r.set_defaults(func=cmd_regions)


def open_game(key: str, folder: Path | None, cache: Path | None):
    """An install as an ``islandconvert.GameSide`` (the folder, else its environment variable)."""
    from ..othergames.installs import GAMES
    from .islandconvert import GameSide

    var = env_var(key)
    root = folder or (Path(os.environ[var]) if os.environ.get(var) else None)
    if root is None or not root.is_dir():
        raise ValueError(tr("no {game} install: pass it, or set {var}", game=key, var=var))
    tree = GAMES[key].index(root, cache / "index" if cache else None)
    if key == "anno117":
        from ..islands.islandview import Files

        tree = Files(tree)
    return GameSide(key, tree, cache, root)


def cmd_convert(args) -> int:
    from ..modloader.modswap import check_folder_name, check_holds
    from . import islandconvert

    mod_id = check_folder_name(args.mod_id)  # one folder name: never "..", a path or a device name
    check_holds(args.out.resolve() / mod_id, mod_id)  # before the port's work: never another mod's or a game's folder
    games = islandconvert.source_games()
    if args.game not in games:
        raise ValueError(
            tr("no port from {game}; this edition ports from {games}", game=args.game, games=", ".join(games))
        )
    if not any(r.id == args.to for r in regions.REGIONS):
        raise ValueError(tr("unknown region {region}: island regions <game> lists them", region=args.to))
    if args.name and len(args.islands) > 1:
        raise ValueError(tr("--name names one island; {count} given", count=len(args.islands)))
    target = regions.region(args.to)
    src = open_game(args.game, args.src, args.cache)
    dst = src if target.game == args.game else open_game(target.game, args.dst, args.cache)
    requests = [
        islandconvert.Request(args.game, path.replace("\\", "/").lower(), args.to, args.name, args.pool_region)
        for path in dict.fromkeys(args.islands)
    ]
    done = islandconvert.convert_all(src, dst, requests, log=print)
    for item in done:
        print(islandconvert.summary(item))
    out = args.out.resolve() / mod_id
    written = islandconvert.write_mod(out, mod_id, mod_id, target.game, done)
    print(tr("{count} islands written into {out}", count=len(written), out=out))
    return 0


def cmd_regions(args) -> int:
    for r in regions.regions(args.game):
        pool = r.island_region + (f"/{r.sub_region}" if r.sub_region else "")
        print(f"{r.id}\t{tr(r.label)}\t{pool}\t{r.material_set or '(default set)'}\t{r.ambient}\t{r.vegetation}")
    return 0
