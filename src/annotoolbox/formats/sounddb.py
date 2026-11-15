"""The sound database of the old games (``sounddatabase.xml`` of 2070, 1404, 1701): every sound's file and mixing
values, the lists the game draws from, the ambients.

``read(data)`` gives a ``SoundDb``: ``sounds`` (id -> ``SoundDef``), ``groups`` (1404, 1701), ``lists`` (the
``Object`` entries: weighted sounds, in 1404 and 1701 per event) and ``ambients``. The edits (``set_fields``,
``set_file``, ``add_sound``, ``set_weight``, ``add_member``, ``remove_member``) change the file's bytes in place and leave the rest as shipped.
Layout and meaning of the values: ``docs/sounds.md`` ("What 2070 has", "The sound database of the old games").
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from ..infra.i18n import N_

FIELDS = (  # the children of a sound definition, in the shipped order
    "ID", "SoundType", "Filename", "MasterVolume", "MaxCount", "MinDistance", "MaxDistance", "Prio",
    "SoundFlags", "VolumeID", "RandomPitch", "SoundGroupID", "XZDistanceFactor",
)  # fmt: skip
TYPES = {
    0: N_("interface"),
    1: N_("one-shot"),
    2: N_("atmosphere"),
    3: N_("music"),
    4: N_("3D loop"),
}  # 2070's SoundType (sounds.md)
NO_EVENT = -1  # the key of a list whose sounds hang directly below it (2070)


class SoundDbError(ValueError):
    """An edit that cannot be made (no such sound, an unknown field): the file is left as it was."""


@dataclass
class SoundDef:
    id: int
    values: dict[str, str]  # tag -> text of every child, in file order (``ID`` included)

    def number(self, tag: str) -> int:
        try:
            return int(self.values.get(tag, "0").strip())
        except ValueError:
            return 0

    @property
    def filename(self) -> str:
        return self.values.get("Filename", "").strip().replace("\\", "/")


@dataclass
class SoundList:
    """An ``Object``: ``events`` maps an event id (``NO_EVENT`` in 2070) to its (sound id, weight) members."""

    id: int
    events: dict[int, list[tuple[int, int]]] = field(default_factory=dict)


@dataclass
class Channel:
    min_pause: int
    max_pause: int
    members: list[tuple[int, int]]


@dataclass
class Ambient:
    id: int
    name: str
    channels: list[Channel]


@dataclass
class SoundDb:
    sounds: dict[int, SoundDef] = field(default_factory=dict)
    groups: list[dict[str, str]] = field(default_factory=list)
    lists: dict[int, SoundList] = field(default_factory=dict)
    ambients: dict[int, Ambient] = field(default_factory=dict)


def number(node: ET.Element | None) -> int:
    try:
        return int((node.text or "").strip()) if node is not None else 0
    except ValueError:
        return 0


def _members(node: ET.Element) -> list[tuple[int, int]]:
    return [(number(m.find("ID")), number(m.find("Propability"))) for m in node.findall("SoundID")]


def read(data: bytes) -> SoundDb:
    """The database of a ``sounddatabase.xml``; ValueError for a file that is not one."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"not a sound database: {exc}") from exc
    if root.tag != "SoundDataBase":
        raise ValueError(f"not a sound database: the root is <{root.tag}>")
    db = SoundDb()
    for el in root.iterfind("SoundIDs/SoundID"):
        values = {c.tag: (c.text or "") for c in el}
        db.sounds[int(values.get("ID", "0").strip() or 0)] = SoundDef(int(values.get("ID", "0").strip() or 0), values)
    db.groups = [{c.tag: (c.text or "") for c in el} for el in root.iterfind("SoundGroupIDs/SoundGroupID")]
    for el in root.iterfind("ObjectIDs/Object"):
        entry = SoundList(number(el.find("ID")))
        if el.find("Event") is None:
            entry.events[NO_EVENT] = _members(el)
        for event in el.findall("Event"):
            entry.events[number(event.find("ID"))] = _members(event)
        db.lists[entry.id] = entry
    for el in root.iterfind("AmbientIDs/Ambient"):
        channels = [
            Channel(number(c.find("m_MinPause")), number(c.find("m_MaxPause")), _members(c))
            for c in el.findall("Channel")
        ]
        db.ambients[number(el.find("ID"))] = Ambient(number(el.find("ID")), el.findtext("Name") or "", channels)
    return db


# Edits


def _eol(data: bytes) -> bytes:
    """The line ending the file uses (what a new entry is written with)."""
    return b"\r\n" if b"\r\n" in data[:2000] else b"\n"


def _section(data: bytes, tag: bytes) -> tuple[int, int]:
    start = data.find(b"<" + tag + b">")
    end = data.find(b"</" + tag + b">", start)
    if start < 0 or end < 0:
        raise SoundDbError(f"the file has no <{tag.decode()}>")
    return start + len(tag) + 2, end


def _sound_block(data: bytes, sound: int) -> re.Match[bytes]:
    lo, hi = _section(data, b"SoundIDs")
    pattern = re.compile(rb"<SoundID><ID>\s*%d\s*</ID>.*?</SoundID>" % sound, re.S)
    found = pattern.search(data, lo, hi)
    if found is None:
        raise SoundDbError(f"the database has no sound {sound}")
    return found


def set_fields(data: bytes, sound: int, **values: object) -> bytes:
    """The database with the given values (tag -> value) of sound ``sound`` replaced; SoundDbError for a tag the
    sound's entry does not have or a value with markup in it."""
    found = _sound_block(data, sound)
    block = found.group(0)
    for tag, value in values.items():
        text = str(value)
        if "<" in text or "&" in text:
            raise SoundDbError(f"{tag}: markup is not allowed in a value")
        hit = re.compile(rb"<%s>([^<]*)</%s>" % (tag.encode(), tag.encode())).search(block)
        if tag == "ID" or hit is None:
            raise SoundDbError(f"sound {sound} has no editable {tag}")
        block = block[: hit.start(1)] + text.encode("utf-8") + block[hit.end(1) :]
    return data[: found.start()] + block + data[found.end() :]


def set_file(data: bytes, sound: int, path: str) -> bytes:
    """The database with sound ``sound`` playing the file ``path`` (a game path, forward slashes)."""
    return set_fields(data, sound, Filename=path)


def add_sound(data: bytes, path: str, like: int | None = None, **values: object) -> tuple[bytes, int]:
    """A new sound that plays ``path``, after the last one, with the next free id; its other values are those of
    sound ``like`` (the first sound when None) and then ``values``. Returns the new bytes and the id."""
    db = read(data)
    model = db.sounds.get(like if like is not None else next(iter(db.sounds), -1))
    if model is None:
        raise SoundDbError("the database has no sound to copy the values from")
    unknown = [tag for tag in values if tag not in FIELDS]
    if unknown:
        raise SoundDbError(f"a sound has no {', '.join(unknown)}")
    new = max(db.sounds, default=0) + 1  # sounds, lists and ambients each count their own ids (checked in all three)
    fields = {**model.values, **{k: str(v) for k, v in values.items()}, "ID": str(new), "Filename": path}
    eol = _eol(data)
    block = (
        b"<SoundID>"
        + b"".join(
            b"<%s>%s</%s>%s" % (tag.encode(), escape(fields.get(tag, "0")).encode("utf-8"), tag.encode(), eol)
            for tag in FIELDS
        )
        + b"</SoundID>"
        + eol
    )
    _lo, hi = _section(data, b"SoundIDs")
    return data[:hi] + block + data[hi:], new


def _list_span(data: bytes, list_id: int, event: int) -> tuple[int, int, bytes]:
    """(start, end, closing tag) in ``data`` of what holds a list's members: the ``Object``, or below ``event`` the
    ``Event`` where the game keeps events; SoundDbError when the list or the event is not there."""
    lo, hi = _section(data, b"ObjectIDs")
    head = re.compile(rb"<Object><ID>\s*%d\s*</ID>.*?</Object>" % list_id, re.S).search(data, lo, hi)
    if head is None:
        raise SoundDbError(f"the database has no list {list_id}")
    if event == NO_EVENT:
        if b"<Event>" in head.group(0):
            raise SoundDbError(f"list {list_id} keeps its sounds below events: name the event")
        return head.start(), head.end(), b"</Object>"
    found = re.compile(rb"<Event><ID>\s*%d\s*</ID>.*?</Event>" % event, re.S).search(head.group(0))
    if found is None:
        raise SoundDbError(f"list {list_id} has no event {event}")
    return head.start() + found.start(), head.start() + found.end(), b"</Event>"


def set_weight(data: bytes, list_id: int, sound: int, weight: int, event: int = NO_EVENT) -> bytes:
    """The database with the weight of ``sound`` in list ``list_id`` (below ``event`` where the game keeps events)
    set to ``weight``; SoundDbError when the list does not hold the sound."""
    start, end, _closing = _list_span(data, list_id, event)
    member = re.compile(rb"(<SoundID><ID>\s*%d\s*</ID>\s*<Propability>)[^<]*(</Propability>)" % sound)
    hit = member.search(data, start, end)
    if hit is None:
        raise SoundDbError(f"list {list_id} does not hold sound {sound}")
    return data[: hit.start()] + hit.group(1) + str(int(weight)).encode() + hit.group(2) + data[hit.end() :]


def add_member(data: bytes, list_id: int, sound: int, weight: int, event: int = NO_EVENT) -> bytes:
    """The database with ``sound`` drawn by list ``list_id`` (below ``event`` where the game keeps events) at
    ``weight``, after the list's last member; SoundDbError for a sound the database lacks or one the list holds."""
    if sound not in read(data).sounds:
        raise SoundDbError(f"the database has no sound {sound}")
    start, end, closing = _list_span(data, list_id, event)
    if re.compile(rb"<SoundID><ID>\s*%d\s*</ID>" % sound).search(data, start, end):
        raise SoundDbError(f"list {list_id} already holds sound {sound}")
    eol = _eol(data)
    member = b"<SoundID><ID>%d</ID>%s<Propability>%d</Propability>%s</SoundID>%s" % (sound, eol, int(weight), eol, eol)
    at = end - len(closing)
    return data[:at] + member + data[at:]


def remove_member(data: bytes, list_id: int, sound: int, event: int = NO_EVENT) -> bytes:
    """The database without ``sound`` in list ``list_id``; SoundDbError when the list does not hold it."""
    start, end, _closing = _list_span(data, list_id, event)
    member = re.compile(rb"<SoundID><ID>\s*%d\s*</ID>.*?</SoundID>(?:\r?\n)?" % sound, re.S)
    hit = member.search(data, start, end)
    if hit is None:
        raise SoundDbError(f"list {list_id} does not hold sound {sound}")
    return data[: hit.start()] + data[hit.end() :]
