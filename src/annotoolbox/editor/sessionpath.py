"""The path edits of an editing session: coast lines and blocker shapes moved with the water tool, and path blocker
cells painted with the blocker brush (``CEditSession.path``). The edit file keeps ``coast`` (``"k"`` -> ``points``: x, z),
``blocker_shapes`` (``"build"`` / ``"camera"`` -> ``"k"`` -> ``points``) and ``path_blockers`` (``"cx,cz"`` -> 1 blocked,
0 free; a cell is half a tile); only what differs from the island's own is kept, so an untouched island writes its
vanilla layers back (``formats.isd.water``, island-editor "Anno 2070 editing", "Blocker shapes").
"""

from __future__ import annotations

import base64
import math

import numpy as np

from ..infra.i18n import tr
from ..islands.islandgrid import cell_key

CELLS = 2  # blocker cells per tile and axis


def moved_outline(points, i: int, position) -> dict:
    """An outline's edit (``{"points": ...}``) with point ``i`` over the ground at (x, z of ``position``)."""
    moved = [list(point) for point in points]
    moved[i] = [float(position[0]), float(position[2])]
    return {"points": moved}


class CPathEdits:
    """Coast lines and blocker cells of one ``CEditSession``."""

    def __init__(self, session) -> None:
        self.m_session = session
        if session.m_anno2070_editing or (session.m_water_net or {}).get("coasts"):
            session.m_edits.setdefault("coast", {})  # 2070-format islands and the new engine's coast lines
        if (session.m_water_net or {}).get("blockers"):
            session.m_edits.setdefault("blocker_shapes", {})
        if session.m_anno2070_editing:
            session.m_edits.setdefault("path_blockers", {})
        data = session.m_island.get("blockers")
        self.m_base: np.ndarray | None = None
        if data:
            size = int(data["n"])
            flat = np.unpackbits(np.frombuffer(base64.b64decode(data["bits"]), np.uint8), bitorder="little")
            self.m_base = flat[: size * size].reshape(size, size).astype(bool)
        self.m_blocked = self.m_base.copy() if self.m_base is not None else None
        for k, value in session.m_edits.get("path_blockers", {}).items():
            self._set(k, bool(value))

    # coast lines
    def coast(self, k: int) -> dict:
        return self.m_session.m_edits["coast"].get(str(k)) or self.m_session.m_water_net["coasts"][k]

    def move_coast_point(self, k: int, i: int, position) -> None:
        """Move a coast line's point over the ground (x, z of ``position``)."""
        snap = self.m_session.water_snapshot()
        snap["coast"][str(k)] = moved_outline(self.coast(k)["points"], i, position)
        self.m_session._water_put(snap)

    # blocker shapes
    def shapes(self, kind: str) -> list[dict]:
        """The island's blocker shapes of ``kind`` (``build`` or ``camera``) with the edits on: ``points`` (x, z)."""
        edited = self.m_session.m_edits.get("blocker_shapes", {}).get(kind, {})
        return [
            edited.get(str(k)) or shape for k, shape in enumerate(self.m_session.m_water_net["blockers"].get(kind, []))
        ]

    def move_shape_point(self, kind: str, k: int, i: int, position) -> None:
        """Move a blocker shape's point over the ground (x, z of ``position``)."""
        snap = self.m_session.water_snapshot()
        snap["blocker_shapes"].setdefault(kind, {})[str(k)] = moved_outline(self.shapes(kind)[k]["points"], i, position)
        self.m_session._water_put(snap)

    # blocker cells
    def _set(self, key: str, blocked: bool) -> None:
        cell = cell_key(key, len(self.m_blocked)) if self.m_blocked is not None else None
        if cell is not None:
            self.m_blocked[cell[1], cell[0]] = blocked

    def begin(self, block: bool, radius_tiles: float, x: float, z: float) -> None:
        session = self.m_session
        if session.m_readonly or self.m_base is None:
            return
        session._m_brush = {
            "kind": "blockers",
            "block": block,
            "radius": radius_tiles,
            "before": dict(session.m_edits["path_blockers"]),
        }
        self.to(x, z)

    def to(self, centre_x: float, centre_z: float) -> None:
        session, stroke = self.m_session, self.m_session._m_brush
        if not stroke or stroke["kind"] != "blockers":
            return
        size, radius = len(self.m_base), stroke["radius"]
        for z in range(
            max(0, math.floor((centre_z - radius) * CELLS)), min(size, math.ceil((centre_z + radius) * CELLS) + 1)
        ):
            for x in range(
                max(0, math.floor((centre_x - radius) * CELLS)), min(size, math.ceil((centre_x + radius) * CELLS) + 1)
            ):
                if math.hypot((x + 0.5) / CELLS - centre_x, (z + 0.5) / CELLS - centre_z) > radius:
                    continue
                key = f"{x},{z}"
                if bool(self.m_base[z, x]) == stroke["block"]:
                    session.m_edits["path_blockers"].pop(key, None)
                else:
                    session.m_edits["path_blockers"][key] = 1 if stroke["block"] else 0
                self.m_blocked[z, x] = stroke["block"]
        session.blockers_changed()

    def end(self) -> None:
        session = self.m_session
        stroke, session._m_brush = session._m_brush, None
        if not stroke or stroke["kind"] != "blockers":
            return
        before, after = stroke["before"], dict(session.m_edits["path_blockers"])

        def put(cells: dict) -> None:
            session.m_edits["path_blockers"] = dict(cells)
            self.m_blocked = self.m_base.copy()
            for k, value in cells.items():
                self._set(k, bool(value))
            session.blockers_changed()

        session._push(tr("islands.path_blockers_lower"), lambda: put(before), lambda: put(after))
