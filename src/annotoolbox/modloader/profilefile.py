"""A profile file read and written without losing a line: comments, blank lines, order, each line's own end, the byte
order mark and the encoding stay as they were; only the line of the mod that changed is touched. Stdlib only.

``ProfileText`` is a profile, ``ActiveFile`` the ``profile.txt``; ``mods.read_profile`` / ``mods.active_profile`` read
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
class Profile:
    """What a profile switches: the ids listed on and off, and ``EnableNewMods`` (``resolve_profile`` takes it)."""

    name: str
    on: list[str] = field(default_factory=list)
    off: list[str] = field(default_factory=list)
    enable_new: bool = False


@dataclass
class Entry:
    """One mod line of a profile."""

    mod_id: str
    on: bool
    note: str  # what follows the id after a ``#`` (``not installed``), "" when nothing
    index: int  # the line


class _Text:
    """The lines of a text file, each with the line end it had, and the byte order mark and encoding of the file."""

    def __init__(self, text: str, *, bom: bool = False, encoding: str = "utf-8") -> None:
        self.bom = bom or text.startswith("﻿")
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
            return cls(body.decode("utf-8"), bom=bom)
        except UnicodeDecodeError:
            return cls(body.decode("cp1252", errors="replace"), bom=bom, encoding="cp1252")

    def to_text(self) -> str:
        return "".join(line + end for line, end in zip(self.lines, self.ends, strict=True))

    def to_bytes(self) -> bytes:
        try:
            data = self.to_text().encode(self.encoding)
        except UnicodeEncodeError:  # a name typed with a letter the file's encoding lacks
            data, self.encoding = self.to_text().encode("utf-8"), "utf-8"
        return codecs.BOM_UTF8 + data if self.bom else data

    def append(self, line: str, *, final: bool = True) -> None:
        """A line at the end (with a line end of its own only when ``final``)."""
        if self.ends and not self.ends[-1]:
            self.ends[-1] = self.eol
        self.lines.append(line)
        self.ends.append(self.eol if final else "")

    def insert(self, at: int, line: str) -> None:
        if at >= len(self.lines):
            self.append(line)
            return
        self.lines.insert(at, line)
        self.ends.insert(at, self.eol)

    def drop(self, indexes: set[int]) -> None:
        keep = [n for n in range(len(self.lines)) if n not in indexes]
        self.lines, self.ends = [self.lines[n] for n in keep], [self.ends[n] for n in keep]


class ProfileText(_Text):
    """A profile file. ``entries()`` lists its mods, ``set_state`` switches one (adding its line when it has none),
    ``to_text()`` gives the file back: unchanged lines byte for byte."""

    def entries(self) -> list[Entry]:
        out = []
        for n, line in enumerate(self.lines):
            m = None if line.lstrip().startswith("##") or NEW_MODS.match(line) else MOD_LINE.match(line)
            if m and not NAME_LINE.match(line):
                tail = m["tail"].strip().lstrip("#").strip()
                out.append(Entry(m["id"], not m["hash"], tail, n))
        return out

    def state(self, mod_id: str) -> bool | None:
        """True when the profile lists the mod switched on, False switched off, None when it does not list it. Listed
        twice: the last line counts."""
        found = [e.on for e in self.entries() if e.mod_id == mod_id]
        return found[-1] if found else None

    def ids(self, on: bool | None = None) -> list[str]:
        """The listed ids (those on, those off, or all)."""
        return [e.mod_id for e in self.entries() if on is None or e.on == on]

    def set_state(self, mod_id: str, on: bool, note: str = "") -> bool:
        """Switch ``mod_id`` on or off in every line that names it, a new line after the last mod line when none does
        (``note`` after it). True when the file changed."""
        hits = [e for e in self.entries() if e.mod_id == mod_id]
        if not hits:
            self._append(f"{'' if on else '# '}{mod_id}" + (f" # {note}" if note else ""))
            return True
        changed = False
        for e in hits:
            if e.on != on:
                m = MOD_LINE.match(self.lines[e.index])
                assert m is not None
                self.lines[e.index] = f"{m['indent']}{'' if on else '# '}{m['id']}{m['tail']}"
                changed = True
        return changed

    def remove(self, mod_id: str) -> bool:
        gone = {e.index for e in self.entries() if e.mod_id == mod_id}
        self.drop(gone)
        return bool(gone)

    @property
    def enable_new(self) -> bool | None:
        """``EnableNewMods`` on (True), switched off by a ``#`` (False), None when the file has no such line."""
        for line in self.lines:
            m = NEW_MODS.match(line)
            if m:
                return "#" not in m["hash"]
        return None

    def set_enable_new(self, on: bool) -> bool:
        for n, line in enumerate(self.lines):
            m = NEW_MODS.match(line)
            if m:
                if ("#" not in m["hash"]) == on:
                    return False
                self.lines[n] = f"{'' if on else '# '}EnableNewMods{m['tail']}"
                return True
        at = next((n for n, line in enumerate(self.lines) if NAME_LINE.match(line)), None)
        self.insert(len(self.lines) if at is None else at, "EnableNewMods" if on else "# EnableNewMods")
        return True

    @property
    def display_name(self) -> str:
        for line in self.lines:
            m = NAME_LINE.match(line)
            if m:
                return m["name"]
        return ""

    def set_display_name(self, name: str) -> None:
        for n, line in enumerate(self.lines):
            if NAME_LINE.match(line):
                self.lines[n] = f"DisplayName {name}"
                return
        self._append(f"DisplayName {name}", after_entries=False)

    def to_profile(self, name: str = "") -> Profile:
        """The ``Profile`` of this file (what ``resolve_profile`` takes); a mod listed twice counts by its last line."""
        state = {e.mod_id: e.on for e in self.entries()}
        return Profile(
            name,
            [i for i, on in state.items() if on],
            [i for i, on in state.items() if not on],
            bool(self.enable_new),
        )

    def _append(self, line: str, after_entries: bool = True) -> None:
        last = max((e.index for e in self.entries()), default=None) if after_entries else None
        if last is None:
            while self.lines and not self.lines[-1].strip():
                self.drop({len(self.lines) - 1})
            self.append(line)
        else:
            self.insert(last + 1, line)


class ActiveFile(_Text):
    """``profile.txt``: the line ``ActiveProfile <name>`` with the game's comments above it. With two such lines the
    last one counts (inferred), and ``set_name`` rewrites every one of them, so the change holds whichever is read."""

    @property
    def name(self) -> str | None:
        """The profile named; "" when the value is empty (no mods at all); None without the line."""
        found = None
        for line in self.lines:
            m = ACTIVE_LINE.match(line)
            if m:
                found = m["name"]
        return found

    @property
    def profile(self) -> str:
        """The profile the game starts with: ``default`` without the line, "" for none (no mods)."""
        return DEFAULT if self.name is None else self.name.removesuffix(".txt")

    def set_name(self, name: str, *, last: bool = False, final: bool = True) -> None:
        """Every ``ActiveProfile`` line says ``name``; a file with none gets one. ``last``: the one line goes to the
        end of the file (``final``: with a line end of its own)."""
        text = f"ActiveProfile {name}"
        hits = {n for n, line in enumerate(self.lines) if ACTIVE_LINE.match(line)}
        if last or not hits:
            self.drop(hits)
            self.append(text, final=final)
            return
        for n in hits:
            self.lines[n] = text


def new_profile(
    name: str, on: list[str] = (), off: list[str] = (), enable_new: bool = False, eol: str = "\n"
) -> ProfileText:
    """A new profile in the game's own layout: its comments, ``EnableNewMods``, ``DisplayName``, then a line a mod
    (``on`` first, then ``off``)."""
    lines = [
        *HEADER,
        "EnableNewMods" if enable_new else "# EnableNewMods",
        f"DisplayName {name}",
        "",
        *on,
        *(f"# {i}" for i in off),
    ]
    return ProfileText(eol.join(lines) + eol)


def new_active(name: str, eol: str = "\n", final: bool = True) -> ActiveFile:
    return ActiveFile(eol.join([*ACTIVE_HEADER, f"ActiveProfile {name}"]) + (eol if final else ""))
