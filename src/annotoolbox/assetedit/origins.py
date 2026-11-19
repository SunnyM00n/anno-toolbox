"""Where an asset comes from when a mod is laid under the Assets app's edits (docs/mod-import.md, "Phase 6 as built").
Stdlib only.

``Layer`` records what the mod (and the mods it requires) did to the game's asset file: the assets it added, changed
and removed. The document asks it for ``origin(guid)``; the tree and the form mark the rows by the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GAME = "game"  # as the game's own files have it
MOD_NEW = "mod-new"  # added by the mod
MOD_CHANGED = "mod-changed"  # a game asset the mod changed
PERSON = "person"  # added in the change set (the person's own)


@dataclass
class Layer:
    """The mod under the edits: ``mods`` its id and the ids of the mods it requires (their changes count as the mod's),
    ``added`` / ``changed`` / ``removed`` the GUIDs of the asset file that differ from the game's."""

    mods: list[str] = field(default_factory=list)
    added: set[int] = field(default_factory=set)
    changed: set[int] = field(default_factory=set)
    removed: set[int] = field(default_factory=set)

    def origin(self, guid: int) -> str:
        if guid in self.added:
            return MOD_NEW
        return MOD_CHANGED if guid in self.changed else GAME

    def counts(self) -> tuple[int, int, int]:
        return len(self.added), len(self.changed), len(self.removed)

    def to_json(self) -> dict:
        return {
            "mods": self.mods,
            "added": sorted(self.added),
            "changed": sorted(self.changed),
            "removed": sorted(self.removed),
        }

    @classmethod
    def from_json(cls, data: dict) -> Layer:
        return cls(
            [str(m) for m in data.get("mods", [])],
            {int(g) for g in data.get("added", [])},
            {int(g) for g in data.get("changed", [])},
            {int(g) for g in data.get("removed", [])},
        )
