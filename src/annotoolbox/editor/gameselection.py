"""Following the running game's selection on the open island: the objects the player selected, found among the island's
objects by their asset (and, when the game says where they stand, the nearest of that asset).

``entries_for(session, selection)`` is the entry; ``suite/apps`` hands it the live link's ``selection`` event. The
payload is ``{"objects": [{"guid": n, "position": [x, y, z]?, ...}]}`` or one such object; the game's coordinates are
taken as the island's (not tried against a running game). docs/live-link.md "Toolbox side".
"""

from __future__ import annotations

import math

from .editsession import CEditSession, SEntry


def _objects(selection: dict) -> list[dict]:
    listed = selection.get("objects")
    return [item for item in (listed if isinstance(listed, list) else [selection]) if isinstance(item, dict)]


def _near(entry: SEntry, position) -> float:
    return math.hypot(entry.position[0] - position[0], entry.position[2] - position[2])


def entries_for(session: CEditSession, selection: dict) -> list[SEntry]:
    """The entries of the open island the game's selection names, one per selected object: the nearest of its asset to
    where the game says it stands, else the first. An object with no GUID, or of an asset the island lacks, is left out."""
    found: list[SEntry] = []
    for item in _objects(selection):
        guid = str(item.get("guid", "")).strip()
        if not guid.isdecimal():
            continue
        same = [
            entry
            for entry in session.m_entries
            if entry.kind == "object" and entry.shown and str(entry.base.get("guid")) == guid
        ]
        place = item.get("position")
        if same and isinstance(place, (list, tuple)) and len(place) >= 3:
            same.sort(key=lambda entry: _near(entry, place))
        if same and all(same[0] is not entry for entry in found):
            found.append(same[0])
    return found
