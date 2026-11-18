"""A command of the command line as a form: its options read from its own argparse parser (``fields``), and the
command line made from the form's values (``argv``), so the window and the command line never drift apart.

``parser_of(change)`` finds the command's parser (None when this build has no such command), ``fields`` lists what
the form shows, ``argv`` gives the words after the program's name, ``parse`` turns them back into the command's
arguments. No Qt. docs/converter-app.md, "The form".
"""

from __future__ import annotations

import argparse
import shlex
from dataclasses import dataclass
from pathlib import Path

LIST_SEPARATOR = ";"  # several values in one text field (an option given more than once, or nargs)
FOLDER_HINTS = ("folder", "directory")  # a path option whose help says this takes a folder
FOLDER_DESTS = frozenset({"out", "work", "mod", "cache", "folder", "anims", "src", "dst", "anno117", "rad"})


@dataclass(frozen=True)
class Field:
    """One option or argument of the command as the form shows it (``kind``: flag, choice, int, float, path, text)."""

    dest: str
    flag: str  # the long option ("--lods"); "" for a positional argument
    kind: str
    default: object
    choices: tuple[str, ...]
    help: str
    required: bool
    folder: bool  # a path that names a folder
    many: bool  # several values: nargs or an option given more than once
    forced: bool = False  # always given: the entry narrowed the option to its own choices


class ParseError(ValueError):
    """The command refused its arguments (argparse's own message)."""


_parsers: dict[str, argparse.ArgumentParser | None] = {}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would print the usage and end the program
        raise ParseError(message)


def parser_of(change) -> argparse.ArgumentParser | None:
    """The parser of ``change``'s command, made once; None when this build does not have the command (yet)."""
    if change.key not in _parsers:
        root = _Parser(prog="", add_help=False)
        change.register(root.add_subparsers(dest="command", parser_class=_Parser))
        parser: argparse.ArgumentParser | None = root
        for word in change.words:
            group = _subparsers(parser) if parser is not None else None
            parser = group.choices.get(word) if group is not None else None
        _parsers[change.key] = parser
    return _parsers[change.key]


def _subparsers(parser: argparse.ArgumentParser):
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def _shown(action: argparse.Action) -> bool:
    return not isinstance(action, argparse._HelpAction | argparse._VersionAction | argparse._SubParsersAction)


def fixed_dests(change, parser: argparse.ArgumentParser) -> set[str]:
    """The options ``change.fixed`` sets: the form leaves them out."""
    by_flag = {s: a.dest for a in parser._actions for s in a.option_strings}
    return {by_flag[w] for w in change.fixed if w in by_flag}


def fields(change, parser: argparse.ArgumentParser) -> list[Field]:
    """What the form shows of the command: every option and argument but the sources, the target and the fixed."""
    left_out = {change.inputs, change.output, *change.hidden, *fixed_dests(change, parser)}
    return [_field(change, a) for a in parser._actions if _shown(a) and a.dest not in left_out]


def _field(change, action: argparse.Action) -> Field:
    flag = max(action.option_strings, key=len) if action.option_strings else ""
    many = isinstance(action, argparse._AppendAction) or action.nargs in ("+", "*")
    choices = change.choices.get(action.dest) or (tuple(str(c) for c in action.choices) if action.choices else ())
    if isinstance(action, argparse._StoreTrueAction | argparse._StoreFalseAction | argparse._StoreConstAction):
        kind = "flag"
    elif choices:
        kind = "choice"
    elif action.type in (int, float):
        kind = action.type.__name__
    elif action.type is Path:
        kind = "path"
    else:
        kind = "text"
    default = choices[0] if action.dest in change.choices else action.default
    help_text = (action.help or "").replace("%%", "%")
    folder = kind == "path" and (action.dest in FOLDER_DESTS or any(w in help_text.lower() for w in FOLDER_HINTS))
    forced = action.dest in change.choices
    return Field(action.dest, flag, kind, default, choices, help_text, bool(action.required), folder, many, forced)


def values_of(items: str) -> list[str]:
    return [v.strip() for v in items.replace("\n", LIST_SEPARATOR).split(LIST_SEPARATOR) if v.strip()]


def argv(change, parser: argparse.ArgumentParser, sources: list[str], out: str, values: dict) -> list[str]:
    """The command line (after the program's name) that runs ``change`` on ``sources`` into ``out`` with the form's
    ``values`` (by dest; a value equal to the default is left out, as a person would). A hidden option is given
    when ``values`` holds it (the window fills it: the source game, a game folder)."""
    words: list[str] = []
    options: list[str] = []
    shown = {f.dest for f in fields(change, parser)}
    for action in parser._actions:
        if not _shown(action):
            continue
        flag = max(action.option_strings, key=len) if action.option_strings else ""
        if action.dest == change.inputs:
            given = [x for s in sources for x in ([flag, s] if flag else [s])]
        elif action.dest == change.output:
            given = ([flag, out] if flag else [out]) if out else []
        elif action.dest in shown or values.get(action.dest) not in (None, ""):
            given = _given(_field(change, action), values.get(action.dest))
        else:
            continue
        (options if flag else words).extend(given)
    return [*change.command, *words, *options, *change.fixed]


def _given(f: Field, value) -> list[str]:
    if f.kind == "flag":
        return [f.flag] if bool(value) != bool(f.default) else []
    items = values_of(str(value)) if f.many and value is not None else [] if value in (None, "") else [str(value)]
    unchanged = items and items[0] == str(f.default if f.default is not None else "")
    if not f.many and unchanged and not (f.required or f.forced):
        return []
    if not f.flag:
        return items
    return [x for item in items for x in (f.flag, item)] if f.many else [f.flag, items[0]] if items else []


def parse(change, parser: argparse.ArgumentParser, line: list[str]) -> argparse.Namespace:
    """The command's arguments of a command line ``argv`` made; ``ParseError`` with argparse's message."""
    return parser.parse_args(line[len(change.command) :])


def target_of(change, out: Path, source: str) -> Path:
    """The target of one source: the folder itself, or a file or a folder named after the source in it."""
    name = Path(source.rstrip("/\\")).name
    if change.output_file:
        return out / ((Path(name).stem if Path(name).suffix else name) + change.output_file)
    if change.output_subfolder:
        return out / Path(name).stem
    return out


def shown(program: str, line: list[str]) -> str:
    """The command line as a person types it."""
    return shlex.join([program, *line] if program else line)
