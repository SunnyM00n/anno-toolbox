"""The path edits of an editing session: coast lines and blocker shapes moved with the water tool, and path blocker
cells painted with the blocker brush (``EditSession.path``). The edit file keeps ``coast`` (``"k"`` -> ``points``: x, z),
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
    moved = [list(p) for p in points]
    moved[i] = [float(position[0]), float(position[2])]
    return {"points": moved}


class PathEdits:
    """Coast lines and blocker cells of one ``EditSession``."""

    def __init__(self, session) -> None:
        self.s = session
        if session.anno2070_editing or (session.water_net or {}).get("coasts"):
            session.edits.setdefault("coast", {})  # 2070-format islands and the new engine's coast lines
        if (session.water_net or {}).get("blockers"):
            session.edits.setdefault("blocker_shapes", {})
        if session.anno2070_editing:
            session.edits.setdefault("path_blockers", {})
        data = session.island.get("blockers")
        self.base: np.ndarray | None = None
        if data:
            n = int(data["n"])
            flat = np.unpackbits(np.frombuffer(base64.b64decode(data["bits"]), np.uint8), bitorder="little")
            self.base = flat[: n * n].reshape(n, n).astype(bool)
        self.blocked = self.base.copy() if self.base is not None else None
        for k, v in session.edits.get("path_blockers", {}).items():
            self._set(k, bool(v))

    # coast lines
    def coast(self, k: int) -> dict:
        return self.s.edits["coast"].get(str(k)) or self.s.water_net["coasts"][k]

    def move_coast_point(self, k: int, i: int, position) -> None:
        """Move a coast line's point over the ground (x, z of ``position``)."""
        snap = self.s.water_snapshot()
        snap["coast"][str(k)] = moved_outline(self.coast(k)["points"], i, position)
        self.s._water_put(snap)

    # blocker shapes
    def shapes(self, kind: str) -> list[dict]:
        """The island's blocker shapes of ``kind`` (``build`` or ``camera``) with the edits on: ``points`` (x, z)."""
        edited = self.s.edits.get("blocker_shapes", {}).get(kind, {})
        return [edited.get(str(k)) or shape for k, shape in enumerate(self.s.water_net["blockers"].get(kind, []))]

    def move_shape_point(self, kind: str, k: int, i: int, position) -> None:
        """Move a blocker shape's point over the ground (x, z of ``position``)."""
        snap = self.s.water_snapshot()
        snap["blocker_shapes"].setdefault(kind, {})[str(k)] = moved_outline(self.shapes(kind)[k]["points"], i, position)
        self.s._water_put(snap)

    # blocker cells
    def _set(self, key: str, blocked: bool) -> None:
        cell = cell_key(key, len(self.blocked)) if self.blocked is not None else None
        if cell is not None:
            self.blocked[cell[1], cell[0]] = blocked

    def begin(self, block: bool, radius: float, x: float, z: float) -> None:
        s = self.s
        if s.readonly or self.base is None:
            return
        s._brush = {"kind": "blockers", "block": block, "radius": radius, "before": dict(s.edits["path_blockers"])}
        self.to(x, z)

    def to(self, cx: float, cz: float) -> None:
        s, b = self.s, self.s._brush
        if not b or b["kind"] != "blockers":
            return
        n, r = len(self.base), b["radius"]
        for z in range(max(0, math.floor((cz - r) * CELLS)), min(n, math.ceil((cz + r) * CELLS) + 1)):
            for x in range(max(0, math.floor((cx - r) * CELLS)), min(n, math.ceil((cx + r) * CELLS) + 1)):
                if math.hypot((x + 0.5) / CELLS - cx, (z + 0.5) / CELLS - cz) > r:
                    continue
                key = f"{x},{z}"
                if bool(self.base[z, x]) == b["block"]:
                    s.edits["path_blockers"].pop(key, None)
                else:
                    s.edits["path_blockers"][key] = 1 if b["block"] else 0
                self.blocked[z, x] = b["block"]
        s.blockers_changed()

    def end(self) -> None:
        s = self.s
        b, s._brush = s._brush, None
        if not b or b["kind"] != "blockers":
            return
        before, after = b["before"], dict(s.edits["path_blockers"])

        def put(cells: dict) -> None:
            s.edits["path_blockers"] = dict(cells)
            self.blocked = self.base.copy()
            for k, v in cells.items():
                self._set(k, bool(v))
            s.blockers_changed()

        s._push(tr("path blockers"), lambda: put(before), lambda: put(after))
