"""A profile file read and written without losing a line: comments, blank lines, order, each line's own end, the byte
order mark and the encoding stay as they were; only the line of the mod that changed is touched. Stdlib only.

``CProfileText`` is a profile, ``CActiveFile`` the ``profile.txt``; ``mods.read_profile`` / ``mods.active_profile`` read
through them. The format and the "last line counts" rule for a mod or ``ActiveProfile`` named twice (inferred): see
``docs/engine-modloader.md`` "Where mods live and which ones run".
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field

DEFAULT = "default"  # the profile the game reads without a ``profile.txt``
MOD_LINE = re.compile(r"^(?P<indent>\s*)(?P<hash>#*)\s*(?P<id>[A-Za-z0-9][\w.\-]*)(?P<tail>(?:\s+#.*|\s*))$")
NEW_MODS = re.compile(r"^(?P<hash>\s*#?\s*)EnableNewMods(?P<tail>(?:\s+#.*|\s*))$")
NAME_LINE = re.compile(r"^DisplayName\s+(?P<name>.*?)\s*$")
ACTIVE_LINE = re.compile(r"^(?P<lead>\s*)ActiveProfile\b[ \t]*(?P<name>.*?)\s*$")

HEADER = (
    "## Download mods at: https://mod.io/g/anno-117-pax-romana",
    "##",
    "## Disable a mod:",
    "## - Add a single `#` before it.",
    "## Enable a mod:",
    "## - Remove the `#` before it.",
    "##",
    "## Disable mods not listed:",
    "## - Add `#` before `EnableNewMods` to automatically disable new mods.",
    "## - That's useful if you want to backup your activation list as a mod profile.",
)
ACTIVE_HEADER = (
    '## Change the value after "ActiveProfile " to configure which mod profile will be loaded by the game on startup.',
    '## Example: "ActiveProfile default" will cause the game to read the profile located at "/profiles/default.txt"',
    "## Not specifying the active profile or leaving the value empty will start the game without any mods.",
)


@dataclass
class SProfile:
    """What a profile switches: the ids listed on and off, and ``EnableNewMods`` (``resolve_profile`` takes it)."""

    name: str
    on: list[str] = field(default_factory=list)
    off: list[str] = field(default_factory=list)
    enable_new: bool = False


@dataclass
class SEntry:
    """One mod line of a profile."""

    mod_id: str
    is_on: bool
    note: str  # what follows the id after a ``#`` (``not installed``), "" when nothing
    index: int  # the line


class _CText:
    """The lines of a text file, each with the line end it had, and the byte order mark and encoding of the file."""

    def __init__(self, text: str, *, has_bom: bool = False, encoding: str = "utf-8") -> None:
        self.bom = has_bom or text.startswith("﻿")
        self.encoding = encoding
        text = text.lstrip("﻿")
        self.eol = "\r\n" if "\r\n" in text else "\n"  # the end of a line added later
        parts = re.split(r"(\r\n|\n)", text) if text else []
        self.lines: list[str] = parts[0::2]
        self.ends: list[str] = [*parts[1::2], ""]  # a last line without an end has ""
        if parts and not self.lines[-1]:  # the text ended with a line end: no empty last line
            self.lines.pop()
        self.ends = self.ends[: len(self.lines)]

    @classmethod
    def from_bytes(cls, data: bytes):
        """A file's bytes: UTF-8 (with or without a mark), else Windows-1252, which ``to_bytes`` writes back."""
        bom = data.startswith(codecs.BOM_UTF8)
        body = data[len(codecs.BOM_UTF8) :] if bom else data
        try:
            return cls(body.decode("utf-8"), has_bom=bom)
        except UnicodeDecodeError:
            return cls(body.decode("cp1252", errors="replace"), has_bom=bom, encoding="cp1252")

    def to_text(self) -> str:
        return "".join(line + end for line, end in zip(self.lines, self.ends, strict=True))

    def to_bytes(self) -> bytes:
        try:
            data = self.to_text().encode(self.encoding)
        except UnicodeEncodeError:  # a name typed with a letter the file's encoding lacks
            data, self.encoding = self.to_text().encode("utf-8"), "utf-8"
        return codecs.BOM_UTF8 + data if self.bom else data

    def append(self, line: str, *, has_final_newline: bool = True) -> None:
        """A line at the end (with a line end of its own only when ``has_final_newline``)."""
        if self.ends and not self.ends[-1]:
            self.ends[-1] = self.eol
        self.lines.append(line)
        self.ends.append(self.eol if has_final_newline else "")

    def insert(self, index: int, line: str) -> None:
        if index >= len(self.lines):
            self.append(line)
            return
        self.lines.insert(index, line)
        self.ends.insert(index, self.eol)

    def drop(self, indexes: set[int]) -> None:
        keep = [line_index for line_index in range(len(self.lines)) if line_index not in indexes]
        self.lines, self.ends = (
            [self.lines[line_index] for line_index in keep],
            [self.ends[line_index] for line_index in keep],
        )


class CProfileText(_CText):
    """A profile file. ``entries()`` lists its mods, ``set_state`` switches one (adding its line when it has none),
    ``to_text()`` gives the file back: unchanged lines byte for byte."""

    def entries(self) -> list[SEntry]:
        out = []
        for line_index, line in enumerate(self.lines):
            match = None if line.lstrip().startswith("##") or NEW_MODS.match(line) else MOD_LINE.match(line)
            if match and not NAME_LINE.match(line):
                tail = match["tail"].strip().lstrip("#").strip()
                out.append(SEntry(match["id"], not match["hash"], tail, line_index))
        return out

    def state(self, mod_id: str) -> bool | None:
        """True when the profile lists the mod switched on, False switched off, None when it does not list it. Listed
        twice: the last line counts."""
        found = [entry.is_on for entry in self.entries() if entry.mod_id == mod_id]
        return found[-1] if found else None

    def ids(self, is_on: bool | None = None) -> list[str]:
        """The listed ids (those on, those off, or all)."""
        return [entry.mod_id for entry in self.entries() if is_on is None or entry.is_on == is_on]

    def set_state(self, mod_id: str, is_on: bool, note: str = "") -> bool:
        """Switch ``mod_id`` on or off in every line that names it, a new line after the last mod line when none does
        (``note`` after it). True when the file changed."""
        hits = [entry for entry in self.entries() if entry.mod_id == mod_id]
        if not hits:
            self._append(f"{'' if is_on else '# '}{mod_id}" + (f" # {note}" if note else ""))
            return True
        changed = False
        for entry in hits:
            if entry.is_on != is_on:
                match = MOD_LINE.match(self.lines[entry.index])
                assert match is not None
                self.lines[entry.index] = f"{match['indent']}{'' if is_on else '# '}{match['id']}{match['tail']}"
                changed = True
        return changed

    def remove(self, mod_id: str) -> bool:
        gone = {entry.index for entry in self.entries() if entry.mod_id == mod_id}
        self.drop(gone)
        return bool(gone)

    @property
    def enable_new(self) -> bool | None:
        """``EnableNewMods`` on (True), switched off by a ``#`` (False), None when the file has no such line."""
        for line in self.lines:
            match = NEW_MODS.match(line)
            if match:
                return "#" not in match["hash"]
        return None

    def set_enable_new(self, is_on: bool) -> bool:
        for line_index, line in enumerate(self.lines):
            match = NEW_MODS.match(line)
            if match:
                if ("#" not in match["hash"]) == is_on:
                    return False
                self.lines[line_index] = f"{'' if is_on else '# '}EnableNewMods{match['tail']}"
                return True
        index = next((line_index for line_index, line in enumerate(self.lines) if NAME_LINE.match(line)), None)
        self.insert(len(self.lines) if index is None else index, "EnableNewMods" if is_on else "# EnableNewMods")
        return True

    @property
    def display_name(self) -> str:
        for line in self.lines:
            match = NAME_LINE.match(line)
            if match:
                return match["name"]
        return ""

    def set_display_name(self, name: str) -> None:
        for line_index, line in enumerate(self.lines):
            if NAME_LINE.match(line):
                self.lines[line_index] = f"DisplayName {name}"
                return
        self._append(f"DisplayName {name}", is_after_entries=False)

    def to_profile(self, name: str = "") -> SProfile:
        """The ``SProfile`` of this file (what ``resolve_profile`` takes); a mod listed twice counts by its last line."""
        state = {entry.mod_id: entry.is_on for entry in self.entries()}
        return SProfile(
            name,
            [i for i, is_on in state.items() if is_on],
            [i for i, is_on in state.items() if not is_on],
            bool(self.enable_new),
        )

    def _append(self, line: str, is_after_entries: bool = True) -> None:
        last = max((entry.index for entry in self.entries()), default=None) if is_after_entries else None
        if last is None:
            while self.lines and not self.lines[-1].strip():
                self.drop({len(self.lines) - 1})
            self.append(line)
        else:
            self.insert(last + 1, line)


class CActiveFile(_CText):
    """``profile.txt``: the line ``ActiveProfile <name>`` with the game's comments above it. With two such lines the
    last one counts (inferred), and ``set_name`` rewrites every one of them, so the change holds whichever is read."""

    @property
    def name(self) -> str | None:
        """The profile named; "" when the value is empty (no mods at all); None without the line."""
        found = None
        for line in self.lines:
            match = ACTIVE_LINE.match(line)
            if match:
                found = match["name"]
        return found

    @property
    def profile(self) -> str:
        """The profile the game starts with: ``default`` without the line, "" for none (no mods)."""
        return DEFAULT if self.name is None else self.name.removesuffix(".txt")

    def set_name(self, name: str, *, is_last: bool = False, has_final_newline: bool = True) -> None:
        """Every ``ActiveProfile`` line says ``name``; a file with none gets one. ``is_last``: the one line goes to the
        end of the file (``has_final_newline``: with a line end of its own)."""
        text = f"ActiveProfile {name}"
        hits = {line_index for line_index, line in enumerate(self.lines) if ACTIVE_LINE.match(line)}
        if is_last or not hits:
            self.drop(hits)
            self.append(text, has_final_newline=has_final_newline)
            return
        for line_index in hits:
            self.lines[line_index] = text


def new_profile(
    name: str, on_ids: list[str] = (), off_ids: list[str] = (), enable_new: bool = False, eol: str = "\n"
) -> CProfileText:
    """A new profile in the game's own layout: its comments, ``EnableNewMods``, ``DisplayName``, then a line a mod
    (``on_ids`` first, then ``off_ids``)."""
    lines = [
        *HEADER,
        "EnableNewMods" if enable_new else "# EnableNewMods",
        f"DisplayName {name}",
        "",
        *on_ids,
        *(f"# {i}" for i in off_ids),
    ]
    return CProfileText(eol.join(lines) + eol)


def new_active(name: str, eol: str = "\n", has_final_newline: bool = True) -> CActiveFile:
    return CActiveFile(eol.join([*ACTIVE_HEADER, f"ActiveProfile {name}"]) + (eol if has_final_newline else ""))
