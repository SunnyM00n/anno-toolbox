"""The ``save`` command group: show or change the player, the name and the money of a 117 savegame, or make a new one
from a template (docs/formats/save-format.md). ``register(sub)`` adds it to a parser (the Toolbox's ``save`` group)."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    s = sub.add_parser(
        "save", help="show or change the player, name and money of a 117 savegame (.a8s; docs/formats/save-format.md)"
    )
    save_sub = s.add_subparsers(dest="save_command", required=True)

    sp = save_sub.add_parser("show", help="the save's name, the player as its three places hold it, the money")
    sp.add_argument("file", type=Path)
    sp.set_defaults(func=cmd_save_show)

    sp = save_sub.add_parser("edit", help="change the player in all three places, the save's name and/or the money")
    sp.add_argument("file", type=Path)
    _player_options(sp)
    money = sp.add_mutually_exclusive_group()
    money.add_argument("--money", type=int, help="set the participant's money to this amount")
    money.add_argument("--add-money", type=int, help="add this amount to the participant's money (negative: take)")
    sp.add_argument(
        "--participant", type=int, default=41, help="whose money: a participant GUID (default 41, the human)"
    )
    where = sp.add_mutually_exclusive_group(required=True)
    where.add_argument("-o", "--out", type=Path, help="write the edited save here (a file, or a folder)")
    where.add_argument(
        "--in-place", action="store_true", help="overwrite the file; <file>.orig keeps the original (if none yet)"
    )
    sp.set_defaults(func=cmd_save_edit)

    sp = save_sub.add_parser(
        "new", help="a new save from a template the game wrote (the dummy profile save of the setup flow)"
    )
    sp.add_argument("--template", type=Path, required=True, help="a save the user's own game wrote")
    sp.add_argument("--out", type=Path, required=True, help="a .a8s file, or the profile folder (<save name>.a8s)")
    sp.add_argument("--overwrite", action="store_true", help="replace an existing file")
    _player_options(sp)
    sp.set_defaults(func=cmd_save_new)


def _player_options(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--name", help="company name (also the profile name in the save unless --profile-name)")
    sp.add_argument("--profile-name", help="SavegameProfileDisplayName in header and game setup")
    for key in ("color", "logo", "portrait"):
        sp.add_argument(f"--{key}", type=lambda v: int(v, 0), help=f"{key} GUID")
    sp.add_argument("--save-name", help="the save's own name (meta.a7s)")


def _player_changes(args: argparse.Namespace) -> dict:
    changes = {k: getattr(args, k) for k in ("name", "profile_name", "color", "logo", "portrait")}
    return {k: v for k, v in changes.items() if v is not None}


def _edited_save(save, args: argparse.Namespace):
    from ..formats import savegame117

    changes = _player_changes(args)
    if changes:
        save = savegame117.set_player(save, **changes)
    if args.save_name is not None:
        save = savegame117.set_save_name(save, args.save_name)
    return save


def cmd_save_show(args: argparse.Namespace) -> int:
    from ..formats import savegame117

    save = savegame117.read(args.file)
    print(f"{args.file}: save name {savegame117.save_name(save)!r}")
    for part, p in savegame117.read_player(save).items():
        text = f"  {part:14} name {p.name!r}"
        if p.profile_name is not None:
            text += f" profile {p.profile_name!r}"
        text += f" colour {p.color} logo {p.logo} portrait {p.portrait}"
        print(text + (f" custom flags {''.join(map(str, p.flags))}" if p.flags else ""))
    try:
        print(f"  money {savegame117.read_money(save):,}")
    except savegame117.SaveError as e:
        print(f"  money: {e}")
    return 0


def _with_money(save, args: argparse.Namespace):
    from ..formats import savegame117

    if args.money is None and args.add_money is None:
        return save
    old = savegame117.read_money(save, args.participant)
    new = args.money if args.money is not None else old + args.add_money
    print(f"participant {args.participant}: money {old:,} -> {new:,}")
    return savegame117.set_money(save, new, args.participant)


def cmd_save_edit(args: argparse.Namespace) -> int:
    from ..formats import savegame117

    if args.save_name is None and args.money is None and args.add_money is None and not _player_changes(args):
        raise ValueError(
            "nothing to change: give --name, --color, --logo, --portrait, --profile-name, --save-name, --money"
            " or --add-money"
        )
    save = _with_money(_edited_save(savegame117.read(args.file), args), args)
    if args.in_place:
        backup = args.file.with_name(args.file.name + ".orig")
        if not backup.exists():
            shutil.copy2(args.file, backup)
        dest = args.file
    else:
        dest = args.out / args.file.name if args.out.is_dir() else args.out
    savegame117.write(save, dest)
    print(f"written {dest}")
    return 0


def cmd_save_new(args: argparse.Namespace) -> int:
    from ..formats import savegame117

    if args.save_name is None:
        raise ValueError("--save-name is required")
    if args.out.suffix.lower() == ".a8s":
        dest = args.out
    else:
        if any(c in args.save_name for c in '\\/:*?"<>|'):
            raise ValueError(f"{args.save_name!r} cannot be a file name")
        dest = args.out / f"{args.save_name}.a8s"
    if dest.exists() and not args.overwrite:
        raise ValueError(f"{dest} exists (--overwrite replaces it)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    savegame117.write(_edited_save(savegame117.read(args.template), args), dest)
    print(f"written {dest}")
    return 0
