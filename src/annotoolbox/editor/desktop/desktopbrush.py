"""What a stroke of the 3D view's brushes does to the edit session: the height brushes, paint, forest, path blockers
and the object brush. ``BrushTools`` starts a stroke at a ground point of the view, follows it and ends it, so the
view itself only passes the mouse on (``desktopview``).
"""

from __future__ import annotations

import itertools

from ...infra.i18n import tr
from ..cellbrush import AREA_KIND_IDS, FULL
from ..islandgrids import ZONES
from ..objectbrush import ObjectBrush

SPACING = 2.0  # the object brush's default spacing in tiles
_SEEDS = itertools.count(1)  # one seed per stroke: a stroke is random, a replay of the edit file is not


class BrushTools:
    """The brush strokes of one ``IslandView3D`` (``view``): ``start`` at the first ground point (view space),
    ``to`` for each further one, ``end`` at the button's release."""

    def __init__(self, view) -> None:
        self.view = view
        self.spray: ObjectBrush | None = None

    def start(self, hit) -> bool:
        """Begin a stroke of the view's tool; False when it cannot start (and says why in the status bar)."""
        v = self.view
        s, b = v.session, v.brush
        p = v.from_view(hit)
        x, z = p[0], p[2]
        if v.tool == "height":
            s.begin_height(b["mode"], b["radius"], b["strength"], x, z)
        elif v.tool == "paint":
            if v.paint_material is None:
                v.status.emit(tr("pick a ground material"))
                return False
            s.begin_paint(v.paint_material, b["radius"], b["strength"], x, z)
        elif v.tool == "forest":
            s.begin_forest(b["plant"], b["radius"], x, z)
        elif v.tool == "blocker":
            s.path.begin(b["plant"], b["radius"], x, z)
        elif v.tool == "active":
            value = self._cell_value()
            if b.get("grid") == "areakind" and value not in s.area.kind_ids:
                v.status.emit(tr("this island has no area {id}", id=hex(value)))
                return False
            s.cells(b.get("grid")).begin(value, b["radius"], x, z)
        elif v.tool == "surf":
            s.surf.begin(round(b["strength"] * FULL) if b["plant"] else 0, b["radius"], x, z)
        elif v.tool == "spray":
            brush = ObjectBrush(
                s, v.pick or {}, b["radius"], b["strength"], b.get("spacing", SPACING), seed=next(_SEEDS)
            )
            if not brush.usable:
                v.status.emit(tr("pick a model in the library to spray"))
                return False
            self.spray = brush
            brush.begin(x, z)
            v.apply_hidden()
        return True

    def _cell_value(self):
        """What a stroke of the cell tool sets: the zone of the Zone box on the environment grid, the id of the Area id
        box on the area kinds, else on or off."""
        b = self.view.brush
        if b.get("grid") == "areakind":
            return AREA_KIND_IDS[b.get("areakind", 0)]
        if b.get("grid") == "tint":
            return b.get("tint", 0), b["strength"]
        return ZONES[b.get("zone", 0)][0] if b.get("grid") == "environment" else b["plant"]

    def _follow(self) -> dict:
        """The tool's step and end of a stroke, for every tool that paints along the mouse."""
        s = self.view.session
        cells = s.cells(self.view.brush.get("grid"))
        return {
            "height": (s.height_to, s.end_height),
            "paint": (s.paint_to, s.end_paint),
            "forest": (s.forest_to, s.end_forest),
            "blocker": (s.path.to, s.path.end),
            "active": (cells.to, cells.end),
            "surf": (s.surf.to, s.surf.end),
        }

    def to(self, hit) -> None:
        v = self.view
        p = v.from_view(hit)
        x, z = p[0], p[2]
        if v.tool == "spray":
            if self.spray is not None and self.spray.to(x, z):
                v.apply_hidden()
            return
        self._follow()[v.tool][0](x, z)

    def end(self) -> None:
        v = self.view
        if v.tool == "spray":
            if self.spray is not None:
                v.status.emit(tr("{n} sprayed", n=self.spray.end()))
                self.spray = None
        else:
            self._follow()[v.tool][1]()
        v.update()
