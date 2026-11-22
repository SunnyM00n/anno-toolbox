"""The tint brush of an editing session: strokes that multiply the ground's colour (``edits["tint"]``), laid into the
island's tint pages by the build (``islands.tintpages``, island-editor "Tint pages").

It answers the cell tool's calls (``begin``, ``to``, ``end``), so the window's cell tool paints with it (grid
``"tint"``). A stroke keeps the colour factor of the chosen preset, the brush's radius and strength and the points
the mouse passed, a point only when it moved a quarter radius (the build fills the line in).
"""

from __future__ import annotations

import math

from ..infra.i18n import N_, tr
from .editchange import SChange, copy_json

PRESETS = (
    (N_("common.darker"), (0.7, 0.7, 0.7)),
    (N_("common.lighter"), (1.4, 1.4, 1.4)),
    (N_("common.warmer"), (1.25, 1.0, 0.8)),
    (N_("common.cooler"), (0.8, 1.0, 1.25)),
    (N_("common.greener"), (0.9, 1.2, 0.9)),
    (N_("common.drier"), (1.2, 1.05, 0.8)),
)  # the factors the albedo is multiplied with at full strength: 1 leaves it


class CTintEdits:
    """The tint strokes of one ``CEditSession``; ``cells`` is None (there is no grid to outline)."""

    m_cells = None

    def __init__(self, session) -> None:
        self.m_session = session
        self.m_stroke: dict | None = None

    @property
    def strokes(self) -> list[dict]:
        return self.m_session.m_edits.setdefault("tint", [])

    def begin(self, value: tuple[int, float], radius_tiles: float, x: float, z: float) -> None:
        """Start a stroke: ``value`` the preset's index and the brush's strength."""
        session = self.m_session
        if session.m_readonly:
            return
        preset, strength = value
        session._m_brush = {"kind": "tint", "before": copy_json(self.strokes)}
        self.m_stroke = {
            "color": list(PRESETS[preset][1]),
            "radius": float(radius_tiles),
            "strength": round(float(strength), 3),
            "points": [[round(x, 2), round(z, 2)]],
        }
        self.strokes.append(self.m_stroke)
        session._notify(SChange())

    def to(self, x: float, z: float) -> None:
        session, stroke = self.m_session, self.m_stroke
        if not session._m_brush or session._m_brush["kind"] != "tint" or stroke is None:
            return
        last = stroke["points"][-1]
        if math.hypot(x - last[0], z - last[1]) >= stroke["radius"] / 4:
            stroke["points"].append([round(x, 2), round(z, 2)])
            session._notify(SChange())

    def end(self) -> None:
        session = self.m_session
        brush, session._m_brush = session._m_brush, None
        self.m_stroke = None
        if not brush or brush["kind"] != "tint":
            return
        before, after = brush["before"], copy_json(self.strokes)

        def put(strokes: list[dict]) -> None:
            session.m_edits["tint"] = copy_json(strokes)
            session._notify(SChange())

        session._push(tr("islands.tint_lower"), lambda: put(before), lambda: put(after))
