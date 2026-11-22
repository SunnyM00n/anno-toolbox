"""Standalone programs (any language) listed in the Toolbox: a manifest says how to start one, nothing is embedded.

``SProgram`` is a manifest read (``from_manifest``); ``command_line`` fills the placeholders of its command from the
settings and the open project, ``launch`` starts it as a process, ``for_file`` finds the programs that open a file
(the "open with" lists) and ``CLauncherPage`` is the page the app switcher shows for one. Manifest keys and
placeholders: ``docs/app-api.md``, "Programs".
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.i18n import tr
from ..sources import gametree
from . import appapi
from . import project as projects
from .settings import CSettings

PLACEHOLDER = re.compile(r"\{(\w+)\}")
VALUES = (
    "game",
    "game_dir",
    "project",
    "mods_dir",
    "workspace",
    "language",
    "file",
)  # the placeholders; each also reaches the program as the environment variable ANNO_TOOLBOX_<NAME>
ENV_PREFIX = "ANNO_TOOLBOX_"


@dataclass(frozen=True)
class SProgram:
    """A program's manifest. ``command``: the program and its arguments, each a string with placeholders (an argument
    holding a placeholder that has no value is left out, unless ``requires`` names it: then the start is refused).
    ``file_types``: the suffixes it opens (".xml"); ``games``: the games it works with (the first with a folder set
    gives ``{game_dir}``)."""

    key: str
    title: str
    command: tuple[str, ...]
    folder: Path  # where the manifest is: a relative program path is looked for there
    api_version: int = appapi.API_VERSION
    summary: str = ""
    icon: str = appapi.DEFAULT_ICON
    games: tuple[str, ...] = ()
    file_types: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    cwd: str = ""
    extra: dict = field(default_factory=dict)  # keys the manifest has beyond these, kept for the program's own use

    def opens(self, file: str) -> bool:
        """Whether the program takes ``file`` by its suffix, and its command has a place for a file."""
        return any("{file}" in part for part in self.command) and Path(file).suffix.lower() in self.file_types


def split_command(text: str, posix: bool | None = None) -> list[str]:
    """A command written as one string, split into arguments. On Windows (``posix`` False) backslashes stay, and the
    quotes that group an argument are taken off as the shell would: the program gets the path, not its quotes."""
    posix = os.name != "nt" if posix is None else posix
    parts = shlex.split(text, posix=posix)
    if posix:
        return parts
    return [
        piece[1:-1] if len(piece) >= 2 and piece[0] == piece[-1] and piece[0] in "\"'" else piece for piece in parts
    ]


def from_manifest(data: object, folder: Path, key: str) -> SProgram:
    """A ``SProgram`` from a manifest's JSON; ValueError (with a reason a person can read) for one that is not valid.
    ``key``: the name the manifest's file or folder gives when it has no ``key``."""
    if not isinstance(data, dict):
        raise ValueError(tr("appapi.manifest_not_object"))
    reason = appapi.refusal(data.get("api_version"))
    if reason:
        raise ValueError(reason)
    command = data.get("command")
    if isinstance(command, str):
        command = split_command(command)
    if not command or not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise ValueError(tr("appapi.manifest_command"))
    own = str(data.get("key") or key)
    if not appapi.KEY_PATTERN.fullmatch(own):
        raise ValueError(tr("appapi.bad_key", key=own))
    unknown = [
        placeholder for part in command for placeholder in PLACEHOLDER.findall(part) if placeholder not in VALUES
    ]
    if unknown:
        raise ValueError(tr("appapi.unknown_placeholder", name=unknown[0], known=", ".join(VALUES)))
    icon = str(data.get("icon") or appapi.DEFAULT_ICON)
    if icon not in ("", appapi.DEFAULT_ICON) and not icon.startswith("app_") and icon != "project":
        picture = (folder / icon).resolve()  # a picture file next to the manifest
        icon = str(picture) if picture.is_file() else appapi.DEFAULT_ICON
    known = {
        "api_version",
        "key",
        "name",
        "title",
        "summary",
        "icon",
        "command",
        "games",
        "file_types",
        "requires",
        "cwd",
    }
    return SProgram(
        key=own,
        title=str(data.get("name") or data.get("title") or own),
        command=tuple(command),
        folder=folder,
        api_version=data["api_version"],
        summary=str(data.get("summary") or ""),
        icon=icon,
        games=tuple(game for game in data.get("games") or () if game in gametree.GAMES),
        file_types=tuple(
            ("." + file_type.lstrip(".")).lower()
            for file_type in data.get("file_types") or ()
            if isinstance(file_type, str)
        ),
        requires=tuple(requirement for requirement in data.get("requires") or () if requirement in VALUES),
        cwd=str(data.get("cwd") or ""),
        extra={k: value for k, value in data.items() if k not in known},
    )


def values_for(settings: CSettings, program: SProgram, file: str = "") -> dict[str, str]:
    """The placeholders' values for ``program`` now: the selected game (or the first of the program's games that has a
    folder), the open project and the settings' folders; "" for what is not known."""
    game = settings.game if (not program.games or settings.game in program.games) else ""
    game = game or next((game_key for game_key in program.games if getattr(settings, game_key, "")), "")
    return {
        "game": game,
        "game_dir": getattr(settings, game, "") if game in gametree.GAMES else "",
        "project": str(projects.folder_of(settings)),
        "mods_dir": settings.mods,
        "workspace": settings.workspace,
        "language": settings.language,
        "file": file,
    }


def command_line(program: SProgram, values: dict[str, str]) -> list[str]:
    """The argument list: a relative program path made absolute where the manifest is (when it is there), each
    placeholder filled. ValueError says which value a ``requires`` entry lacks; an argument with an empty
    placeholder is left out."""
    for name in program.requires:
        if not values.get(name):
            raise ValueError(tr("appapi.value_missing", name=name, title=program.title))
    out = []
    for index, part in enumerate(program.command):
        names = PLACEHOLDER.findall(part)
        if names and any(not values.get(name) for name in names):
            continue
        filled = PLACEHOLDER.sub(lambda m: values[m.group(1)], part)
        if index == 0 and not os.path.isabs(filled) and (here := program.folder / filled).is_file():
            filled = str(here)
        out.append(filled)
    if not out:
        raise ValueError(tr("appapi.manifest_command"))
    return out


def launch(settings: CSettings, program: SProgram, file: str = "") -> subprocess.Popen:
    """Start the program as its own process (no shell: the values are single arguments, never parsed again), with
    the values also in its environment. Raises ValueError for a missing required value, OSError when it cannot start."""
    values = values_for(settings, program, file)
    arguments = command_line(program, values)
    env = {**os.environ, **{ENV_PREFIX + name.upper(): value for name, value in values.items()}}
    cwd = program.cwd or None
    if cwd:
        cwd = PLACEHOLDER.sub(lambda m: values.get(m.group(1), ""), cwd)
    return subprocess.Popen(arguments, cwd=cwd or program.folder, env=env)


def for_file(programs: list[SProgram], file: str) -> list[SProgram]:
    """The programs that open ``file`` (by suffix), by title: what an "open with" menu lists."""
    return sorted((program for program in programs if program.opens(file)), key=lambda program: program.title.lower())


def launcher_maker(program: SProgram):
    """The ``SAppSpec.make`` of a program: a page with its summary, the command it would run now and the buttons."""

    def make(shell):
        from .appprogrampage import CLauncherPage

        return CLauncherPage(program, shell)

    return make
