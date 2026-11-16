"""The values a save may be edited in, and the write of a new save next to the original (``docs/save-editor.md``,
"What can be written, and how"). ``fields(save)`` lists what the form shows, ``write_beside(save, changes)`` writes;
the format modules do the byte work (``formats.savegame117``, ``formats.savegame2070``). An edition may add values
at the extension point ``features.SAVE_EDITS`` (``extspecs.SaveEdit``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..formats import savegame117, savegame2070
from ..infra.i18n import N_
from ..suite import features
from . import summary117
from .container import TAIL, SaveFile

TEXT, GUID, NUMBER = "text", "guid", "number"
EDITABLE = ("anno117", "anno2070")
SUFFIX = " (edited)"
EDITED = re.compile(r" \(edited(?: \d+)?\)$")


@dataclass(frozen=True)
class Field:
    key: str  # "save_name", "name", ..., "money:<participant>"
    label: str  # marked for translation; ``{who}`` is the participant
    kind: str
    value: str | int
    who: str = ""


class EditError(ValueError):
    pass


def _player_fields(save: savegame117.Save) -> list[Field]:
    head = savegame117.read_player(save)["header.a7s"]
    return [
        Field("save_name", N_("Save name"), TEXT, savegame117.save_name(save)),
        Field("name", N_("Company name"), TEXT, head.name),
        Field("profile_name", N_("Profile name"), TEXT, head.profile_name or ""),
        Field("color", N_("Colour (ParticipantColor GUID)"), GUID, head.color),
        Field("logo", N_("Logo (PlayerLogo GUID)"), GUID, head.logo),
        Field("portrait", N_("Portrait (Portrait GUID)"), GUID, head.portrait),
    ]


def save117(save: SaveFile) -> savegame117.Save:
    """The opened parts as ``savegame117`` holds them (no second inflate); every part must have the game's tail."""
    parts = {n: save.part(n) for n in savegame117.PARTS}
    missing = [n for n, p in parts.items() if p is None or p.packing != TAIL]
    if missing:
        raise EditError(f"{save.path.name}: {', '.join(missing)} missing or without the game's tail")
    return savegame117.Save({n: p.doc for n, p in parts.items()}, {n: p.raw for n, p in parts.items()}, save.data)


def _fields117(save: SaveFile) -> list[Field]:
    out = _player_fields(save117(save))
    money = summary117.money_by_participant(save.part("data.a7s").tree())
    for guid in sorted(money, key=lambda g: (g != savegame117.HUMAN_PARTICIPANT, g)):
        label = N_("Money of the player ({who})") if guid == savegame117.HUMAN_PARTICIPANT else N_("Money of {who}")
        out.append(Field(f"money:{guid}", label, NUMBER, money[guid], str(guid)))
    return out


def _fields2070(save: SaveFile) -> list[Field]:
    parsed = savegame2070.read_bytes(save.data, save.path.name)
    who = str(savegame2070.human_id(parsed))
    return [Field("money", N_("Money of the player ({who})"), NUMBER, savegame2070.read_money(parsed), who)]


def _own_fields(save: SaveFile) -> list[Field]:
    if save.game == "anno117":
        return _fields117(save)
    if save.game == "anno2070" and save.part("save.sww") is not None:
        return _fields2070(save)
    return []


def _extra_fields(save: SaveFile) -> list[tuple[object, list[Field]]]:
    """Per ``SaveEdit`` registered for the save's game, the rows it offers."""
    specs = features.extensions(features.SAVE_EDITS).values()
    return [(spec, spec.fields(save)) for spec in specs if save.game in spec.games]


def fields(save: SaveFile) -> list[Field]:
    """The editable values with what the save holds now; none for a game whose layout no sample pinned."""
    own = _own_fields(save)
    return own + [f for _spec, rows in _extra_fields(save) for f in rows] if own else []


def _apply117(save: SaveFile, changes: dict[str, str | int]) -> savegame117.Save:
    parsed = save117(save)
    if "save_name" in changes:
        parsed = savegame117.set_save_name(parsed, str(changes["save_name"]))
    player = {k: changes[k] for k in ("name", "profile_name", "color", "logo", "portrait") if k in changes}
    if "name" in player and "profile_name" not in player:  # the profile keeps its folder's name
        player["profile_name"] = savegame117.read_player(parsed)["header.a7s"].profile_name
    if player:
        parsed = savegame117.set_player(parsed, **player)
    for key, value in changes.items():
        if key.startswith("money:"):
            parsed = savegame117.set_money(parsed, int(value), int(key.split(":", 1)[1]))
    return parsed


def parse_value(field: Field, text: str) -> str | int:
    """A form's text as ``write_beside`` takes it: a name as typed, a GUID in decimal or ``0x`` hex, money in
    decimal (leading zeros and thousands separators allowed); ``ValueError`` for anything else."""
    if field.kind == TEXT:
        return text
    text = text.strip()
    if field.kind == GUID and text.lower().startswith("0x"):
        return int(text, 16)
    return int(text.replace(",", "").replace("_", ""), 10)


def changed_values(fields: list[Field], typed: dict[str, str]) -> dict[str, str | int]:
    """The typed values (by field key) that differ from the save's, parsed."""
    out: dict[str, str | int] = {}
    for f in fields:
        text = typed.get(f.key)
        if text is None or (text if f.kind == TEXT else text.strip()) == str(f.value):
            continue
        out[f.key] = parse_value(f, text)
    return out


def target(original: Path) -> Path:
    """The new file's name: ``<name> (edited).<ext>``, then ``(edited 2)`` and on; never an existing file. Editing an
    edited file counts on from its own number instead of nesting the suffix."""
    original = Path(original)
    stem = EDITED.sub("", original.stem)
    n = 1
    while True:
        tag = SUFFIX if n == 1 else f" (edited {n})"
        path = original.with_name(f"{stem}{tag}{original.suffix}")
        if not path.exists():
            return path
        n += 1


def _reserve(original: Path) -> Path:
    """Take the next free name at once (an empty file), so two writes never pick the same."""
    while True:
        path = target(original)
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            continue
        return path


def write_beside(save: SaveFile, changes: dict[str, str | int], settings=None) -> Path:
    """Write the save with ``changes`` (keys of ``fields``) as a new file next to the original; returns its path.
    ``settings`` (the suite's) go to the values an extension adds, which may check them against the installed game."""
    if save.game not in EDITABLE:
        raise EditError(f"{save.game}: saves of this game are read only")
    own_keys = {f.key for f in _own_fields(save)}
    extra = [(spec, {f.key for f in rows}) for spec, rows in _extra_fields(save)]
    unknown = sorted(set(changes) - own_keys - {k for _spec, keys in extra for k in keys})
    if unknown:
        raise EditError(f"{save.path.name}: no such value to change: {', '.join(unknown)}")
    own = {k: v for k, v in changes.items() if k in own_keys}
    dest = _reserve(save.path)
    try:
        if save.game == "anno117":
            parsed = _apply117(save, own)
        else:
            parsed = savegame2070.read_bytes(save.data, save.path.name)
            if "money" in own:
                parsed = savegame2070.set_money(parsed, int(own["money"]))
        for spec, keys in extra:
            mine = {k: v for k, v in changes.items() if k in keys}
            if mine:
                parsed = spec.apply(save, parsed, mine, settings)
        (savegame117 if save.game == "anno117" else savegame2070).write(parsed, dest)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest
