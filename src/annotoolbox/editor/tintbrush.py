"""The tint brush of an editing session: strokes that multiply the ground's colour (``edits["tint"]``), laid into the
island's tint pages by the build (``islands.tintpages``, island-editor "Tint pages").

It answers the cell tool's calls (``begin``, ``to``, ``end``), so the window's cell tool paints with it (grid
``"tint"``). A stroke keeps the colour factor of the chosen preset, the brush's radius and strength and the points
the mouse passed, a point only when it moved a quarter radius (the build fills the line in).
"""

from __future__ import annotations

import math

from ..infra.i18n import N_, tr
from .editchange import Change, copy_json

PRESETS = (
    (N_("Darker"), (0.7, 0.7, 0.7)),
    (N_("Lighter"), (1.4, 1.4, 1.4)),
    (N_("Warmer"), (1.25, 1.0, 0.8)),
    (N_("Cooler"), (0.8, 1.0, 1.25)),
    (N_("Greener"), (0.9, 1.2, 0.9)),
    (N_("Drier"), (1.2, 1.05, 0.8)),
)  # the factors the albedo is multiplied with at full strength: 1 leaves it


class TintEdits:
    """The tint strokes of one ``EditSession``; ``cells`` is None (there is no grid to outline)."""

    cells = None

    def __init__(self, session) -> None:
        self.s = session
        self.stroke: dict | None = None

    @property
    def strokes(self) -> list[dict]:
        return self.s.edits.setdefault("tint", [])

    def begin(self, value: tuple[int, float], radius: float, x: float, z: float) -> None:
        """Start a stroke: ``value`` the preset's index and the brush's strength."""
        s = self.s
        if s.readonly:
            return
        preset, strength = value
        s._brush = {"kind": "tint", "before": copy_json(self.strokes)}
        self.stroke = {
            "color": list(PRESETS[preset][1]),
            "radius": float(radius),
            "strength": round(float(strength), 3),
            "points": [[round(x, 2), round(z, 2)]],
        }
        self.strokes.append(self.stroke)
        s._notify(Change())

    def to(self, x: float, z: float) -> None:
        s, stroke = self.s, self.stroke
        if not s._brush or s._brush["kind"] != "tint" or stroke is None:
            return
        last = stroke["points"][-1]
        if math.hypot(x - last[0], z - last[1]) >= stroke["radius"] / 4:
            stroke["points"].append([round(x, 2), round(z, 2)])
            s._notify(Change())

    def end(self) -> None:
        s = self.s
        b, s._brush = s._brush, None
        self.stroke = None
        if not b or b["kind"] != "tint":
            return
        before, after = b["before"], copy_json(self.strokes)

        def put(strokes: list[dict]) -> None:
            s.edits["tint"] = copy_json(strokes)
            s._notify(Change())

        s._push(tr("tint"), lambda: put(before), lambda: put(after))
