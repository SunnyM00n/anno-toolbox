"""The summary's records (``Summary``: sections of rows and tables, the mods a save was saved with, notes) and the
small value readings the summaries share. Labels are marked for translation, values are data; ``summary`` makes one.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime

GUID, OPTION, TEXT_ID, GOODS = "guid", "option", "text", "goods"  # what a ``Ref`` refers to


class Ref(str):
    """A cell that is an id of the game's data: it reads as its number (or text) and, once ``names.apply`` has run,
    ``shown()`` is the name from the game's assets. ``keys`` are the ids, ``amounts`` those of a ``GOODS`` list."""

    kind: str
    keys: tuple
    amounts: tuple[int, ...]
    label: str

    def __new__(cls, text: str, kind: str, keys: tuple = (), amounts: tuple[int, ...] = ()):
        self = super().__new__(cls, text)
        self.kind, self.keys, self.amounts, self.label = kind, tuple(keys), tuple(amounts), ""
        return self

    def shown(self) -> str:
        return self.label or str(self)


def guid_ref(*guids: int) -> Ref:
    return Ref(", ".join(str(g) for g in guids), GUID, guids)


def option_ref(tag: str, value: str) -> Ref:
    """A ``Setup...`` option's stored number; ``tag`` is the option's name in the save."""
    return Ref(value, OPTION, (tag, value))


def text_ref(text_id: str) -> Ref:
    return Ref(text_id, TEXT_ID, (text_id,))


def goods_ref(pairs: list[tuple[int, int]]) -> Ref:
    """The goods of a storage with an amount (those with none are left out)."""
    held = [(g, n) for g, n in pairs if n]
    return Ref(", ".join(f"{g}: {n:,}" for g, n in held), GOODS, tuple(g for g, _ in held), tuple(n for _, n in held))


def shown(cell) -> str:
    """A cell as the window shows it."""
    return cell.shown() if isinstance(cell, Ref) else str(cell)


@dataclass
class Table:
    headers: list[str]  # marked labels
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Section:
    title: str  # a marked label
    rows: list[tuple[str, str]] = field(default_factory=list)  # (marked label, value)
    table: Table | None = None
    named: list[tuple[str, str]] = field(default_factory=list)  # (a name as the save spells it, value)


@dataclass(frozen=True)
class ModEntry:
    mod_id: str
    version: str


@dataclass
class Summary:
    game: str
    sections: list[Section] = field(default_factory=list)
    mods: list[ModEntry] = field(default_factory=list)
    notes: list[tuple[str, dict]] = field(default_factory=list)  # (marked text, its places)

    def section(self, title: str) -> Section:
        found = next((s for s in self.sections if s.title == title), None)
        if found is None:
            found = Section(title)
            self.sections.append(found)
        return found

    def note(self, text: str, **places) -> None:
        self.notes.append((text, places))


def number(text: str | None, default: int = 0) -> int:
    """A tag tree's number text as an integer, ``default`` for none or for text that is no number."""
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def u32(data: bytes) -> int:
    return struct.unpack_from("<I", data)[0] if len(data) >= 4 else 0


def i32(data: bytes) -> int:
    return struct.unpack_from("<i", data)[0] if len(data) >= 4 else 0


def u64(data: bytes) -> int:
    return struct.unpack_from("<Q", data)[0] if len(data) >= 8 else 0


def utf16(data: bytes) -> str:
    return data.decode("utf-16-le", "replace")


def utf8(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def stamp(seconds: int) -> str:
    """A Unix time as the UTC date and time, "" for none."""
    if seconds <= 0:
        return ""
    try:
        return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return str(seconds)


def duration(ms: int) -> str:
    """Milliseconds as hours and minutes (``12:05 h``)."""
    minutes = max(0, ms) // 60000
    return f"{minutes // 60}:{minutes % 60:02d} h"
