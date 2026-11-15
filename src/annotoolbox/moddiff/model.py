"""What the Mod diff app finds, as plain records: the targets a mod changes, the files it carries, the conflicts between
mods, and the ``Report`` that holds one comparison for the views and the export. Stdlib only. ``docs/mod-diff.md``.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field

ADDED, REMOVED, CHANGED = "added", "removed", "changed"  # what happened to a target
ONLY_A, ONLY_B, DIFFERS = "only-a", "only-b", "differs"  # a target of two versions: in one only, or different
NEW, REPLACED, SAME = "new", "replaced", "same"  # a carried file: not in the game, replaces its file, is its file
VALUE, BLOCK, GONE, DUPLICATE, FILE, ASSET = "value", "block", "removed", "duplicate", "file", "asset"  # conflicts
CONFLICT, OVERLAP = "conflict", "overlap"  # how much a conflict matters


@dataclass(frozen=True, slots=True)
class Change:
    """One value: ``path`` from the target's root, ``before`` and ``after`` as text (None: not there)."""

    path: str
    before: str | None
    after: str | None


@dataclass(slots=True)
class Target:
    """A thing the mods changed: an asset (``key`` its GUID), a text (its id) or a file's data (``key`` empty)."""

    file: str
    key: str
    kind: (
        str  # ``ADDED`` / ``REMOVED`` / ``CHANGED``; in a comparison of versions ``ONLY_A`` / ``ONLY_B`` / ``DIFFERS``
    )
    name: str = ""
    template: str = ""
    changes: list[Change] = field(default_factory=list)
    mods: list[str] = field(default_factory=list)  # the mods that touched it, in order
    packed: bytes = b""  # an added target's values, compressed JSON: read by ``values``, compared between versions

    def set_values(self, data) -> None:
        """Keep an added target's values (a data tree of ``elementdata.element_data``) in a few hundred bytes."""
        self.packed = zlib.compress(json.dumps(data, sort_keys=True, separators=(",", ":")).encode(), 1)

    def values(self):
        """The added target's values as a data tree; None for a target that has none."""
        return json.loads(zlib.decompress(self.packed)) if self.packed else None

    @property
    def ident(self) -> tuple[str, str]:
        return (self.file, self.key)

    @property
    def title(self) -> str:
        label = f"{self.key} {self.name}".strip() if self.key else ""
        return label or self.file


@dataclass(slots=True)
class FileChange:
    """A file of a mod that is no patch: it adds the game's file or replaces it."""

    path: str
    kind: str  # ``NEW`` / ``REPLACED`` / ``SAME``
    size: int
    mods: list[str] = field(default_factory=list)
    digest: str = ""  # of the content, to compare two versions (empty: not read)
    source: str | tuple[str, str] = ""  # where the content can be read again (``filechanges.read_source``)


@dataclass(frozen=True, slots=True)
class Conflict:
    """Where the steps of two or more mods meet. ``values`` is each mod's value in load order (None: removed)."""

    kind: str
    level: str  # ``CONFLICT`` or ``OVERLAP``
    file: str
    key: str
    name: str
    path: str
    mods: tuple[str, ...]
    values: tuple[str | None, ...]
    winner: str
    decided: str  # what put the winner last: "LoadAfter", "late phase", "name order"


@dataclass
class Report:
    """One comparison: ``title``, its two sides' names (``left``, ``right``: "game" and a mod, or two versions), the
    targets and files, the conflicts and what the loader would drop."""

    title: str
    game: str
    left: str = ""
    right: str = ""
    mods: list[str] = field(default_factory=list)  # "id version" in load order
    targets: list[Target] = field(default_factory=list)
    files: list[FileChange] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Targets by kind, the value changes, the files and the conflicts, for the status line and the export."""
        out: dict[str, int] = {}
        for t in self.targets:
            out[t.kind] = out.get(t.kind, 0) + 1
        out["values"] = sum(len(t.changes) for t in self.targets)
        out["files"] = len(self.files)
        out["conflicts"] = sum(1 for c in self.conflicts if c.level == CONFLICT)
        out["overlaps"] = sum(1 for c in self.conflicts if c.level == OVERLAP)
        out["dropped"] = len(self.dropped)
        return out
