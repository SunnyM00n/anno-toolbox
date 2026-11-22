"""A GUID range a mod owns, handed out key by key and kept stable in a file: the same key gets the same GUID in
every run, a new key the next free number, nothing is reused.

``CRangeAllocator(start, end, registry, mode)`` is the allocator: ``get(key)`` gives a key's GUID, ``save`` writes the
file (``{"format": 1, "range": [start, end], "guids": {key: guid}, "names": {key: text}}``). Runs that overlap on one
file are serialised by a lock file and merged, never guessed. The modes: ``ASSIGN`` numbers a new key at once,
``RECORD`` notes the new keys and numbers them in ascending key order on ``save`` (so the numbers do not depend on the
order they were asked in), ``LOOKUP`` refuses a key the file lacks (``CMissingKey``). Stdlib only.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from ..infra.buildcache import write_atomic

LOCK_WAIT = 30.0  # seconds to wait for another run's save before giving up
LOCK_STALE = 120.0  # a lock file older than this belongs to a run that died
ASSIGN, RECORD, LOOKUP = "assign", "record", "lookup"


class CMissingKey(KeyError):
    """A lookup-only allocator was asked for a key its file does not hold."""

    def __init__(self, key, registry: Path | None):
        super().__init__(key)
        self.key, self.registry = key, registry

    def __reduce__(self):
        return type(self), (self.key, self.registry)

    def __str__(self) -> str:
        return f"key {self.key} has no GUID in {self.registry or 'the allocator'}"


class CRangeAllocator:
    """Key -> GUID inside ``start``-``end``, kept stable by an optional registry file.

    Keys the file knows keep their GUID, new keys get the next number above everything ever handed out. ``mapping``
    holds the keys asked for in this run, ``names`` an optional text per key that the file carries for its readers.
    A subclass changes the key type (``key_of``: how a key reads back from the file) and adds to what is noted
    (``noted``) and saved (``saved_names``, ``extra``).
    """

    key_of = staticmethod(str)

    def __init__(
        self,
        start: int,
        end: int,
        existing: dict | None = None,
        registry: Path | None = None,
        mode: str = ASSIGN,
    ):
        if mode not in (ASSIGN, RECORD, LOOKUP):
            raise ValueError(f"unknown allocator mode {mode!r}")
        if start > end:
            raise ValueError(f"the GUID range {start}-{end} is empty")
        self.start, self.end = start, end
        self.registry = registry
        self.mode = mode
        self.recorded: dict = {}  # RECORD: new key -> its provisional GUID, replaced by ``save``
        self.planned: dict = {}  # the keys ``commit_plan`` numbered: key -> GUID
        self.known: dict = dict(existing or {})
        self.names: dict = {}
        self._stamp: tuple[int, int] | None = None  # (mtime_ns, size) of the registry file as last read or written
        if registry is not None and registry.is_file():
            self._load(registry)
        self.mapping: dict = {}
        self._next = max(self.known.values(), default=start - 1) + 1

    # what a subclass adds
    def noted(self, guid: int, key, source=None) -> None:
        """Called for every key the allocator learns or hands out (``source``: what the caller said it stands for)."""

    def read_extra(self, data: dict) -> None:
        """The subclass's own keys of a registry file just read."""

    def saved_names(self) -> dict:
        return dict(self.names)

    def extra(self) -> dict:
        """The subclass's own keys of the registry file."""
        return {}

    def missing(self, key) -> KeyError:
        return CMissingKey(key, self.registry)

    # the file
    def _read(self, path: Path) -> tuple[dict, dict]:
        try:
            stat_result = path.stat()
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {self.key_of(k): int(guid) for k, guid in data["guids"].items()}
            names = {self.key_of(k): str(guid) for k, guid in data.get("names", {}).items()}
        except (OSError, ValueError, KeyError, AttributeError) as exc:
            raise ValueError(f"GUID registry {path} is unreadable: {exc}") from exc
        if len(set(known.values())) != len(known):
            raise ValueError(f"GUID registry {path} gives one GUID to two keys")
        stray = [guid for guid in known.values() if not self.start <= guid <= self.end]
        if stray:
            raise ValueError(f"GUID registry {path} holds GUIDs outside {self.start}-{self.end}, e.g. {stray[0]}")
        self._stamp = (stat_result.st_mtime_ns, stat_result.st_size)
        self.read_extra(data)
        return known, names

    def _load(self, path: Path) -> None:
        known, self.names = self._read(path)
        self.known.update(known)
        for key, guid in known.items():
            self.noted(guid, key)

    def _merge_disk(self) -> None:
        """Take over what another run wrote to the registry since we read it; a clash is an error, never a guess."""
        if self.registry is None or not self.registry.is_file():
            return
        known, names = self._read(self.registry)
        taken = {value: k for k, value in self.known.items()}
        for key, guid in known.items():
            if key in self.known:
                if self.known[key] != guid:
                    raise ValueError(
                        f"GUID registry {self.registry}: key {key} is {guid} on disk but {self.known[key]} here"
                    )
            elif guid in taken:
                raise ValueError(
                    f"GUID registry {self.registry}: another run gave {guid} to key {key}, this run gave it to {taken[guid]}"
                )
            else:
                self.known[key] = guid
                taken[guid] = key
                self.noted(guid, key)
        for key, text in names.items():
            self.names.setdefault(key, text)
        self._next = max(self._next, max(self.known.values(), default=self.start - 1) + 1)

    def _registry_changed(self) -> bool:
        if self.registry is None or self._stamp is None:
            return False
        try:
            stat_result = self.registry.stat()
        except OSError:
            return False
        return (stat_result.st_mtime_ns, stat_result.st_size) != self._stamp

    # handing out
    def get(self, key, listed: bool = True, source=None) -> int:
        """The GUID of a key. ``listed`` False keeps it out of ``mapping`` but in the registry; ``source`` is passed
        to ``noted``."""
        if key not in self.known:
            if self._registry_changed():
                self._merge_disk()  # another run may have taken the next number
            if key not in self.known:
                provisional = self._unknown(key, listed)
                if provisional is not None:  # a recording pass: the number only keeps it going
                    return provisional
        self.noted(self.known[key], key, source)
        if listed:
            self.mapping[key] = self.known[key]
        return self.known[key]

    def _unknown(self, key, listed: bool) -> int | None:
        """What a key the registry lacks gets: the next number, which it now knows (``ASSIGN``; returns None), a
        provisional one (``RECORD``), or an error (``LOOKUP``)."""
        if self.mode == LOOKUP:
            raise self.missing(key)
        if self._next > self.end:
            raise RuntimeError(f"GUID range {self.start}-{self.end} exhausted")
        if self.mode == RECORD:
            if key not in self.recorded:  # numbered after the known ones, only to keep the pass going
                self.recorded[key] = self._next + len(self.recorded)
            if listed:
                self.mapping[key] = self.recorded[key]
            return self.recorded[key]
        self.known[key] = self._next
        self._next += 1
        return None

    def commit_plan(self) -> dict:
        """Number the recorded keys in ascending key order after every GUID the registry knows (nothing known moves)
        and return them. Another run's numbers are merged in first, so a key it numbered meanwhile keeps its GUID."""
        if self.registry is not None and self.registry.is_file():
            self._merge_disk()
        fresh = sorted(k for k in self.recorded if k not in self.known)
        self.recorded.clear()
        if self._next + len(fresh) - 1 > self.end:
            raise RuntimeError(f"GUID range {self.start}-{self.end} exhausted")
        for key in fresh:
            self.known[key] = self._next
            self._next += 1
        self.planned = {k: self.known[k] for k in fresh}
        return self.planned

    def plan(self, keys) -> dict:
        """Number the ``keys`` the registry lacks now, in ascending order (a caller that knows its keys beforehand,
        instead of the recording pass); returns the new ones. Known keys keep their GUID."""
        self.recorded.update({k: 0 for k in keys if k not in self.known})
        return self.commit_plan()

    def save(self) -> None:
        """Write the registry (all keys ever known, ordered by GUID); a file that would not change is left alone."""
        if self.registry is None:
            return
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        with registry_lock(self.registry):
            self._merge_disk()
            if self.mode == RECORD:
                self.commit_plan()
            names = self.saved_names()
            sorted_known = sorted(self.known.items(), key=lambda kv: kv[1])
            doc = {
                "format": 1,
                "range": [self.start, self.end],
                "guids": {str(k): guid for k, guid in sorted_known},
                "names": {str(k): names[k] for k, _ in sorted_known if k in names},
                **self.extra(),
            }
            text = json.dumps(doc, indent=1) + "\n"
            if self.registry.is_file() and self.registry.read_text(encoding="utf-8") == text:
                return
            write_atomic(self.registry, text.encode("utf-8"))
            stat_result = self.registry.stat()
            self._stamp = (stat_result.st_mtime_ns, stat_result.st_size)


@contextmanager
def registry_lock(registry: Path):
    """An exclusive lock file next to the registry (``O_EXCL`` works the same on every OS and filesystem we use)."""
    lock = registry.with_name(registry.name + ".lock")
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass  # it went away between the two calls, or (Windows) a process holds it: the deadline decides
            if time.monotonic() > deadline:
                raise TimeoutError(f"GUID registry {registry} is locked by another run ({lock} exists)") from None
            time.sleep(0.05)
    os.close(lock_descriptor)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)
