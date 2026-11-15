"""Following the running game's selection on the open island: the objects the player selected, found among the island's
objects by their asset (and, when the game says where they stand, the nearest of that asset).

``entries_for(session, selection)`` is the entry; ``suite/apps`` hands it the live link's ``selection`` event. The
payload is ``{"objects": [{"guid": n, "position": [x, y, z]?, ...}]}`` or one such object; the game's coordinates are
taken as the island's (not tried against a running game). docs/live-link.md "Toolbox side".
"""

from __future__ import annotations

import math

from .editsession import EditSession, Entry


def _objects(selection: dict) -> list[dict]:
    listed = selection.get("objects")
    return [o for o in (listed if isinstance(listed, list) else [selection]) if isinstance(o, dict)]


def _near(entry: Entry, position) -> float:
    return math.hypot(entry.position[0] - position[0], entry.position[2] - position[2])


def entries_for(session: EditSession, selection: dict) -> list[Entry]:
    """The entries of the open island the game's selection names, one per selected object: the nearest of its asset to
    where the game says it stands, else the first. An object with no GUID, or of an asset the island lacks, is left out."""
    found: list[Entry] = []
    for item in _objects(selection):
        guid = str(item.get("guid", "")).strip()
        if not guid.isdecimal():
            continue
        same = [e for e in session.entries if e.kind == "object" and e.shown and str(e.base.get("guid")) == guid]
        place = item.get("position")
        if same and isinstance(place, (list, tuple)) and len(place) >= 3:
            same.sort(key=lambda e: _near(e, place))
        if same and all(same[0] is not e for e in found):
            found.append(same[0])
    return found
