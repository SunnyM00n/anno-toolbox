"""``mods/mod-loader.log`` of the last start, read per mod: which mods were enabled and loaded, what each one took to
patch, and every line that is a problem (a level above information, a file position, or one of the loss messages).

``read_log(mods_dir)`` returns a ``SLoadLog`` or None without a log. The loader writes ``Load`` before it patches a
mod, so the time between two ``Load`` lines is the first mod's patch time and the lines between them are its own
(``docs/engine-modloader.md`` "Everything that fails without a word"). Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LOG_FILE = "mod-loader.log"
LINE = re.compile(r"^\[(?P<time>[^\]]*)\]\s*\[(?P<level>[^\]]*)\]\s?(?P<text>.*)$")
ENABLED = re.compile(r"^Enabled mod: (?P<path>.*?)\s+\((?P<id>[^@()]*)@(?P<version>[^)]*)\)\s*$")
LOADED = re.compile(r'^Load "(?P<name>.*)" (?P<id>\S+)@(?P<version>\S*) \(from "(?P<path>.*)"\)\s*$')
COUNT = re.compile(r"^Enabled mods: (?P<n>\d+)(?: \(plus (?P<nested>\d+) nested\))?")
DONE = re.compile(r"^Patching completed in (?P<seconds>[\d.]+)\s*s")
SEARCHED = re.compile(r'^Path: "(?P<path>.*)"')
POSITION = re.compile(r"\([\w./\\-]+\.xml:\d+\)")
SUSPECT = re.compile(
    r"No matching node|doesn't match|Can't find file|Failed to parse|Unknown ModOp|Include file|is disabled"
    r"|not the correct game version|Doesn't contain|Failed to get root|is missing|is incompatible|not found"
    r"|Cannot specify|Invalid ModID|Nothing matches|needs attribute",
    re.IGNORECASE,
)
TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


@dataclass
class SModRun:
    """What the log says about one mod."""

    mod_id: str
    version: str = ""
    name: str = ""  # ``[Category] Name`` as the loader printed it
    folder: str = ""
    enabled: bool = False
    loaded: bool = False
    seconds: float | None = None  # its patch time (from the time stamps)
    problems: list[str] = field(default_factory=list)


@dataclass
class SLoadLog:
    path: Path
    modified: float
    mods: dict[str, SModRun] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)  # the mods in the order they were loaded
    problems: list[str] = field(default_factory=list)  # the problem lines no mod claims
    searched: list[str] = field(default_factory=list)
    enabled: int | None = None  # the loader's own count
    nested: int | None = None
    seconds: float | None = None  # ``Patching completed in``
    lines: list[str] = field(default_factory=list)

    def run_of(self, mod_id: str) -> SModRun | None:
        return self.mods.get(mod_id)

    @property
    def problem_count(self) -> int:
        return len(self.problems) + sum(len(mod.problems) for mod in self.mods.values())


def is_problem(line: str) -> bool:
    """A line that is not a plain information line: another level, a file position or a loss message."""
    match = LINE.match(line)
    if not match:
        return bool(line.strip())
    return match["level"].strip().lower() != "info" or bool(POSITION.search(line) or SUSPECT.search(match["text"]))


def _time(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp.strip(), TIME_FORMAT)
    except ValueError:
        return None


def parse_log(lines: list[str], path: Path = Path(LOG_FILE), modified: float = 0.0) -> SLoadLog:
    log = SLoadLog(path, modified, lines=list(lines))
    current: SModRun | None = None
    started: dict[str, datetime] = {}
    last_load: SModRun | None = None
    for line in lines:
        match = LINE.match(line)
        text = match["text"] if match else line
        when = _time(match["time"]) if match else None
        if (found := ENABLED.match(text)) is not None:
            run = log.mods.setdefault(found["id"], SModRun(found["id"]))
            run.enabled, run.version, run.folder = True, found["version"], found["path"]
        elif (found := LOADED.match(text)) is not None:
            run = log.mods.setdefault(found["id"], SModRun(found["id"]))
            run.loaded, run.version, run.name, run.folder = True, found["version"], found["name"], found["path"]
            _close(last_load, started, when)
            if when is not None:
                started[run.mod_id] = when
            log.order.append(run.mod_id)
            current = last_load = run
        elif (found := COUNT.match(text)) is not None:
            log.enabled, log.nested = int(found["n"]), int(found["nested"] or 0)
        elif (found := DONE.match(text)) is not None:
            log.seconds = float(found["seconds"])
            _close(last_load, started, when)
            last_load = current = None
        elif (found := SEARCHED.match(text)) is not None:
            log.searched.append(found["path"])
        if is_problem(line) and not (ENABLED.match(text) or LOADED.match(text)):
            _file(log, text, line, current)
    return log


def _close(run: SModRun | None, started: dict[str, datetime], when: datetime | None) -> None:
    if run is not None and when is not None and run.mod_id in started:
        run.seconds = (when - started[run.mod_id]).total_seconds()


def _file(log: SLoadLog, text: str, line: str, current: SModRun | None) -> None:
    """Put a problem line with the mod it names, else with the mod being patched, else with the log."""
    named = next(
        (mod for mod in log.mods.values() if mod.mod_id and re.search(rf"(?<![\w-]){re.escape(mod.mod_id)}\s*:", text)),
        None,
    )
    owner = named or current
    (owner.problems if owner is not None else log.problems).append(line)


def read_log(mods_dir: Path) -> SLoadLog | None:
    """The log in a mods folder; None when there is none."""
    path = Path(mods_dir) / LOG_FILE
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_log(text.splitlines(), path, stat.st_mtime)
