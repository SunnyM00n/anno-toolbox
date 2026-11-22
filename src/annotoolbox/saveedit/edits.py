"""The values a save may be edited in, and the write of a new save next to the original (``docs/save-editor.md``,
"What can be written, and how"). ``fields(save)`` lists what the form shows, ``write_beside(save, changes)`` writes;
the format modules do the byte work (``formats.savegame117``, ``formats.savegame2070``). The emperor's values are in
``emperoredit``; an edition may add more at the extension point ``features.SAVE_EDITS`` (``extspecs.SSaveEdit``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..formats import savegame117, savegame2070
from ..infra import i18n
from ..infra.i18n import N_, tr
from ..suite import features
from . import summary117
from .container import TAIL, SSaveFile

TEXT, GUID, NUMBER = "text", "guid", "number"
EDITABLE = ("anno117", "anno2070")
SUFFIX = " (edited)"
EDITED = re.compile(r" \(edited(?: \d+)?\)$")
GROUPED_DE = re.compile(r"[+-]?\d{1,3}(\.\d{3})+")  # German: points group the thousands, three digits each


@dataclass(frozen=True)
class SField:
    key: str  # "save_name", "name", ..., "money:<participant>"
    label: str  # marked for translation; ``{who}`` is the participant
    kind: str
    value: str | int
    who: str = ""  # with ``named``: ``{0}``, ``{1}`` ... stand for those GUIDs' names (``who_text``)
    choices: tuple[int, ...] = ()  # a GUID field offered as a list of these (the save's value among them)
    named: tuple[int, ...] = ()


def who_text(field: SField, name: Callable[[int], str] | None) -> str:
    """The field's ``who`` with each GUID of ``named`` as ``name(guid)`` gives it (its number when that is "")."""
    if not field.named:
        return field.who
    return field.who.format(*(guid_label(guid, name) for guid in field.named))


def guid_label(guid: int, name: Callable[[int], str] | None) -> str:
    """``Name (guid)``, or the number alone without a name."""
    found = name(guid) if name is not None else ""
    return f"{found} ({guid})" if found else str(guid)


class CEditError(ValueError):
    pass


def _participant_fields(save: savegame117.SSave) -> list[SField]:
    head = savegame117.read_participant(save)["header.a7s"]
    return [
        SField("save_name", N_("saves.save_name"), TEXT, savegame117.save_name(save)),
        SField("name", N_("saves.company_name"), TEXT, head.name),
        SField("profile_name", N_("saves.profile_name"), TEXT, head.profile_name or ""),
        SField("color", N_("saves.colour_participantcolor_guid"), GUID, head.color),
        SField("logo", N_("saves.logo_playerlogo_guid"), GUID, head.logo),
        SField("portrait", N_("saves.portrait_guid"), GUID, head.portrait),
    ]


def save117(save: SSaveFile) -> savegame117.SSave:
    """The opened parts as ``savegame117`` holds them (no second inflate); every part must have the game's tail."""
    parts = {part_name: save.part(part_name) for part_name in savegame117.PARTS}
    missing = [part_name for part_name, part in parts.items() if part is None or part.m_packing != TAIL]
    if missing:
        raise CEditError(f"{save.path.name}: {', '.join(missing)} missing or without the game's tail")
    return savegame117.SSave(
        {part_name: part.m_doc for part_name, part in parts.items()},
        {part_name: part.m_raw for part_name, part in parts.items()},
        save.data,
    )


def _fields117(save: SSaveFile) -> list[SField]:
    out = _participant_fields(save117(save))
    money = summary117.money_by_participant(save.part("data.a7s").tree())
    for guid in sorted(money, key=lambda participant: (participant != savegame117.HUMAN_PARTICIPANT, participant)):
        label = N_("saves.money_player_who") if guid == savegame117.HUMAN_PARTICIPANT else N_("saves.money_of_who")
        out.append(SField(f"money:{guid}", label, NUMBER, money[guid], str(guid)))
    return out


def _fields2070(save: SSaveFile) -> list[SField]:
    parsed = savegame2070.read_bytes(save.data, save.path.name)
    who = str(savegame2070.human_participant(parsed))
    return [SField("money", N_("saves.money_player_who"), NUMBER, savegame2070.read_money(parsed), who)]


def _own_fields(save: SSaveFile) -> list[SField]:
    if save.game == "anno117":
        return _fields117(save)
    if save.game == "anno2070" and save.part("save.sww") is not None:
        return _fields2070(save)
    return []


def _extra_fields(save: SSaveFile) -> list[tuple[object, list[SField]]]:
    """Per ``SSaveEdit`` of the save's game (the emperor's, then those registered), the rows it offers."""
    from . import emperoredit

    specs = [emperoredit.EDIT, *features.extensions(features.SAVE_EDITS).values()]
    return [(spec, spec.fields(save)) for spec in specs if save.game in spec.games]


def fields(save: SSaveFile) -> list[SField]:
    """The editable values with what the save holds now; none for a game whose layout no sample pinned."""
    own = _own_fields(save)
    return own + [field for _spec, rows in _extra_fields(save) for field in rows] if own else []


def _apply117(save: SSaveFile, changes: dict[str, str | int]) -> savegame117.SSave:
    parsed = save117(save)
    if "save_name" in changes:
        parsed = savegame117.set_save_name(parsed, str(changes["save_name"]))
    participant_changes = {k: changes[k] for k in ("name", "profile_name", "color", "logo", "portrait") if k in changes}
    if (
        "name" in participant_changes and "profile_name" not in participant_changes
    ):  # the profile keeps its folder's name
        participant_changes["profile_name"] = savegame117.read_participant(parsed)["header.a7s"].profile_name
    if participant_changes:
        parsed = savegame117.set_participant(parsed, **participant_changes)
    for key, value in changes.items():
        if key.startswith("money:"):
            parsed = savegame117.set_money(parsed, int(value), int(key.split(":", 1)[1]))
    return parsed


def parse_value(field: SField, text: str, language: str | None = None) -> str | int:
    """A form's text as ``write_beside`` takes it: a name as typed, a GUID in decimal or ``0x`` hex, money in
    decimal (leading zeros and thousands separators allowed); ``ValueError`` for anything else. The separators
    are read by ``language`` (the interface's when None): English groups with ``,``; German groups with ``.``,
    in threes, and refuses ``,``, its decimal sign, so "1.000" and "1,000" are never taken for each other."""
    if field.kind == TEXT:
        return text
    text = text.strip()
    if field.kind == GUID and text.lower().startswith("0x"):
        return int(text, 16)
    if (language or i18n.current()) == "de":
        if "," in text:
            raise ValueError(tr("saves.comma_is_decimal_sign", text=text))
        if "." in text and not GROUPED_DE.fullmatch(text):
            raise ValueError(tr("saves.points_group_thousands", text=text))
        return int(text.replace(".", "").replace("_", ""), 10)
    return int(text.replace(",", "").replace("_", ""), 10)


def changed_values(fields: list[SField], typed: dict[str, str]) -> dict[str, str | int]:
    """The typed values (by field key) that differ from the save's, parsed."""
    out: dict[str, str | int] = {}
    for field in fields:
        text = typed.get(field.key)
        if text is None or (text if field.kind == TEXT else text.strip()) == str(field.value):
            continue
        out[field.key] = parse_value(field, text)
    return out


def target(original: Path) -> Path:
    """The new file's name: ``<name> (edited).<ext>``, then ``(edited 2)`` and on; never an existing file. Editing an
    edited file counts on from its own number instead of nesting the suffix."""
    original = Path(original)
    stem = EDITED.sub("", original.stem)
    part_name = 1
    while True:
        tag = SUFFIX if part_name == 1 else f" (edited {part_name})"
        path = original.with_name(f"{stem}{tag}{original.suffix}")
        if not path.exists():
            return path
        part_name += 1


def _reserve(original: Path) -> Path:
    """Take the next free name at once (an empty file), so two writes never pick the same."""
    while True:
        path = target(original)
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            continue
        return path


def write_beside(save: SSaveFile, changes: dict[str, str | int], settings: object = None) -> Path:
    """Write the save with ``changes`` (keys of ``fields``) as a new file next to the original; returns its path.
    ``settings`` (the suite's) go to the values an extension adds, which may check them against the installed game."""
    if save.game not in EDITABLE:
        raise CEditError(f"{save.game}: saves of this game are read only")
    own_keys = {field.key for field in _own_fields(save)}
    extra = [(spec, {field.key for field in rows}) for spec, rows in _extra_fields(save)]
    unknown = sorted(set(changes) - own_keys - {k for _spec, keys in extra for k in keys})
    if unknown:
        raise CEditError(f"{save.path.name}: no such value to change: {', '.join(unknown)}")
    own = {k: value for k, value in changes.items() if k in own_keys}
    dest = _reserve(save.path)
    try:
        if save.game == "anno117":
            parsed = _apply117(save, own)
        else:
            parsed = savegame2070.read_bytes(save.data, save.path.name)
            if "money" in own:
                parsed = savegame2070.set_money(parsed, int(own["money"]))
        for spec, keys in extra:
            mine = {k: value for k, value in changes.items() if k in keys}
            if mine:
                parsed = spec.apply(save, parsed, mine, settings)
        (savegame117 if save.game == "anno117" else savegame2070).write(parsed, dest)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest
