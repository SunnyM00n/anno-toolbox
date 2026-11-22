"""What a stroke of the 3D view's brushes does to the edit session: the height brushes, paint, forest, path blockers
and the object brush. ``CBrushTools`` starts a stroke at a ground point of the view, follows it and ends it, so the
view itself only passes the mouse on (``desktopview``).
"""

from __future__ import annotations

import itertools

from ...infra.i18n import tr
from ..cellbrush import AREA_KIND_IDS, FULL
from ..islandgrids import ZONES
from ..objectbrush import CObjectBrush

SPACING = 2.0  # the object brush's default spacing in tiles
_SEEDS = itertools.count(1)  # one seed per stroke: a stroke is random, a replay of the edit file is not


class CBrushTools:
    """The brush strokes of one ``CIslandView3D`` (``view``): ``start`` at the first ground point (view space),
    ``to`` for each further one, ``end`` at the button's release."""

    def __init__(self, view) -> None:
        self.m_view = view
        self.m_spray: CObjectBrush | None = None
        self.m_tool: str | None = None  # the tool of the stroke under way: a key may change the view's meanwhile

    def start(self, hit) -> bool:
        """Begin a stroke of the view's tool; False when it cannot start (and says why in the status bar)."""
        view = self.m_view
        session, settings = view.m_session, view.m_brush
        point = view.from_view(hit)
        x, z = point[0], point[2]
        if view.m_tool == "height":
            session.begin_height(settings["mode"], settings["radius"], settings["strength"], x, z)
        elif view.m_tool == "paint":
            if view.m_paint_material is None:
                view.status.emit(tr("islands.pick_ground_material"))
                return False
            session.begin_paint(view.m_paint_material, settings["radius"], settings["strength"], x, z)
        elif view.m_tool == "forest":
            session.begin_forest(settings["plant"], settings["radius"], x, z)
        elif view.m_tool == "blocker":
            session.m_path.begin(settings["plant"], settings["radius"], x, z)
        elif view.m_tool == "active":
            value = self._cell_value()
            if settings.get("grid") == "areakind" and value not in session.m_area.m_kind_ids:
                view.status.emit(tr("islands.island_no_area", id=hex(value)))
                return False
            session.cells(settings.get("grid")).begin(value, settings["radius"], x, z)
        elif view.m_tool == "surf":
            session.m_surf.begin(
                round(settings["strength"] * FULL) if settings["plant"] else 0, settings["radius"], x, z
            )
        elif view.m_tool == "spray":
            brush = CObjectBrush(
                session,
                view.m_pick or {},
                settings["radius"],
                settings["strength"],
                settings.get("spacing", SPACING),
                seed=next(_SEEDS),
            )
            if not brush.usable:
                view.status.emit(tr("islands.pick_model_library_spray"))
                return False
            self.m_spray = brush
            brush.begin(x, z)
            view.apply_hidden()
        self.m_tool = view.m_tool
        return True

    def _cell_value(self):
        """What a stroke of the cell tool sets: the zone of the Zone box on the environment grid, the id of the Area id
        box on the area kinds, else on or off."""
        settings = self.m_view.m_brush
        if settings.get("grid") == "areakind":
            return AREA_KIND_IDS[settings.get("areakind", 0)]
        if settings.get("grid") == "tint":
            return settings.get("tint", 0), settings["strength"]
        return ZONES[settings.get("zone", 0)][0] if settings.get("grid") == "environment" else settings["plant"]

    def _follow(self) -> dict:
        """The tool's step and end of a stroke, for every tool that paints along the mouse."""
        session = self.m_view.m_session
        cells = session.cells(self.m_view.m_brush.get("grid"))
        return {
            "height": (session.height_to, session.end_height),
            "paint": (session.paint_to, session.end_paint),
            "forest": (session.forest_to, session.end_forest),
            "blocker": (session.m_path.to, session.m_path.end),
            "active": (cells.to, cells.end),
            "surf": (session.m_surf.to, session.m_surf.end),
        }

    def to(self, hit) -> None:
        view = self.m_view
        point = view.from_view(hit)
        x, z = point[0], point[2]
        if self.m_tool == "spray":
            if self.m_spray is not None and self.m_spray.to(x, z):
                view.apply_hidden()
        elif self.m_tool is not None:
            self._follow()[self.m_tool][0](x, z)

    def end(self) -> None:
        view = self.m_view
        tool, self.m_tool = self.m_tool, None
        if tool == "spray":
            if self.m_spray is not None:
                view.status.emit(tr("islands.n_sprayed", n=self.m_spray.end()))
                self.m_spray = None
        elif tool is not None:
            self._follow()[tool][1]()
        view.update()
