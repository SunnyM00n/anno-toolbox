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
LATE_PHASE, LOAD_AFTER, NAME_ORDER, ORDER = "late phase", "LoadAfter", "name order", "order"  # ``SConflict.decided``
# a conflict's value where it is a note of ours and not the game's data (``NOTED``): a word, or a count and its unit
SET_VALUE, VALUES, BYTES = "set a value", "value(s)", "bytes"
NOTED = frozenset({GONE, DUPLICATE, FILE, ASSET})


@dataclass(frozen=True, slots=True)
class SChange:
    """One value: ``path`` from the target's root, ``before`` and ``after`` as text (None: not there)."""

    path: str
    before: str | None
    after: str | None


@dataclass(slots=True)
class CTarget:
    """A thing the mods changed: an asset (``key`` its GUID), a text (its id) or a file's data (``key`` empty)."""

    m_file: str
    m_key: str
    m_kind: (
        str  # ``ADDED`` / ``REMOVED`` / ``CHANGED``; in a comparison of versions ``ONLY_A`` / ``ONLY_B`` / ``DIFFERS``
    )
    m_name: str = ""
    m_template: str = ""
    m_changes: list[SChange] = field(default_factory=list)
    m_mods: list[str] = field(default_factory=list)  # the mods that touched it, in order
    m_packed: bytes = b""  # an added target's values, compressed JSON: read by ``values``, compared between versions

    def set_values(self, data) -> None:
        """Keep an added target's values (a data tree of ``elementdata.element_data``) in a few hundred bytes."""
        self.m_packed = zlib.compress(json.dumps(data, sort_keys=True, separators=(",", ":")).encode(), 1)

    def values(self):
        """The added target's values as a data tree; None for a target that has none."""
        return json.loads(zlib.decompress(self.m_packed)) if self.m_packed else None

    @property
    def ident(self) -> tuple[str, str]:
        return (self.m_file, self.m_key)

    @property
    def title(self) -> str:
        label = f"{self.m_key} {self.m_name}".strip() if self.m_key else ""
        return label or self.m_file


@dataclass(slots=True)
class SFileChange:
    """A file of a mod that is no patch: it adds the game's file or replaces it."""

    path: str
    kind: str  # ``NEW`` / ``REPLACED`` / ``SAME``
    size_bytes: int
    mods: list[str] = field(default_factory=list)
    digest: str = ""  # of the content, to compare two versions (empty: not read)
    source: str | tuple[str, str] = ""  # where the content can be read again (``filechanges.read_source``)


@dataclass(frozen=True, slots=True)
class SConflict:
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
    decided: str  # what put the winner last: ``LOAD_AFTER``, ``LATE_PHASE``, ``NAME_ORDER``, ``ORDER``


@dataclass
class SReport:
    """One comparison: ``title``, its two sides' names (``left``, ``right``: "game" and a mod, or two versions), the
    targets and files, the conflicts and what the loader would drop."""

    title: str
    game: str
    left: str = ""
    right: str = ""
    mods: list[str] = field(default_factory=list)  # "id version" in load order
    targets: list[CTarget] = field(default_factory=list)
    files: list[SFileChange] = field(default_factory=list)
    conflicts: list[SConflict] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Targets by kind, the value changes, the files and the conflicts, for the status line and the export."""
        out: dict[str, int] = {}
        for target in self.targets:
            out[target.m_kind] = out.get(target.m_kind, 0) + 1
        out["values"] = sum(len(target.m_changes) for target in self.targets)
        out["files"] = len(self.files)
        out["conflicts"] = sum(1 for conflict in self.conflicts if conflict.level == CONFLICT)
        out["overlaps"] = sum(1 for conflict in self.conflicts if conflict.level == OVERLAP)
        out["dropped"] = len(self.dropped)
        return out
