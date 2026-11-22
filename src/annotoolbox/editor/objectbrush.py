"""The object brush: spray any model of the library over the ground along a stroke, with a spacing between the
objects, a random turn and a little random size.

``CObjectBrush`` is one stroke on an ``CEditSession``: ``begin`` at the first point, ``to`` for each further point,
``end`` when the button goes up; everything the stroke placed is one step of the undo stack. The placed objects are
ordinary added objects (``CEditSession.place``), so the edit file and the build need nothing new. The forest brush is
its form for trees (island-editor "Full spec").
"""

from __future__ import annotations

import math
import random

from ..infra.i18n import tr
from .editsession import CEditSession
from .selection import one_change

SPACING = 2.0  # tiles between two sprayed objects, at least
SCALE_SPREAD = 0.15  # a sprayed object is scaled by 1 +- this much, unless the scale is held
LAND = 0.05  # no object is sprayed onto ground lower than this (the sea)
TRIES = 6  # candidate spots per tile of path and unit of strength


class CObjectBrush:
    """One stroke of the object brush for the library pick ``pick`` (a model or an editor-only asset)."""

    def __init__(
        self,
        session: CEditSession,
        pick: dict,
        radius_tiles: float,
        strength: float = 0.5,
        spacing_tiles: float = SPACING,
        random_turn: bool = True,
        random_scale: bool = True,
        seed: int | None = None,
    ) -> None:
        self.m_session, self.m_pick = session, pick
        self.m_radius_tiles, self.m_strength, self.m_spacing_tiles = (
            float(radius_tiles),
            float(strength),
            max(0.25, float(spacing_tiles)),
        )
        self.m_random_turn, self.m_random_scale = random_turn, random_scale
        self.m_rnd = random.Random(seed)
        self.m_placed: list = []
        self.m_points: list[tuple[float, float]] = []  # where this stroke put something, for the spacing
        self.m_last: tuple[float, float] | None = None
        self._m_change = None  # the open undo step of the stroke

    @property
    def usable(self) -> bool:
        """A pick that can be sprayed: a model or an editor-only asset (a forest tree has its own brush)."""
        return (
            not self.m_session.m_readonly
            and self.m_pick.get("tree") is None
            and bool(self.m_pick.get("cfg") or self.m_pick.get("guid"))
        )

    def begin(self, x: float, z: float) -> int:
        self.m_last = None
        if self._m_change is None:
            self._m_change = one_change(self.m_session, tr("islands.spray"))
            self._m_change.__enter__()
        return self.to(x, z)

    def to(self, x: float, z: float) -> int:
        """Spray along the way from the last point to (x, z); returns how many objects this step placed."""
        if not self.usable:
            return 0
        start, end = self.m_last or (x, z), (x, z)
        self.m_last = end
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        steps = max(1, math.ceil(length / max(self.m_radius_tiles / 2, 0.5)))
        count = 0
        for k in range(1, steps + 1) if length else (1,):
            progress = k / steps
            count += self._stamp(start[0] + (end[0] - start[0]) * progress, start[1] + (end[1] - start[1]) * progress)
        return count

    def _stamp(self, centre_x: float, centre_z: float) -> int:
        tries = max(1, round(TRIES * self.m_strength * (self.m_radius_tiles / self.m_spacing_tiles) ** 2 / 4))
        made = 0
        for _ in range(tries):
            distance, angle = self.m_radius_tiles * math.sqrt(self.m_rnd.random()), self.m_rnd.random() * 2 * math.pi
            x, z = centre_x + distance * math.cos(angle), centre_z + distance * math.sin(angle)
            session = self.m_session
            if (
                not (0 <= x <= session.m_size_tiles and 0 <= z <= session.m_size_tiles)
                or session.height_at(x, z) < LAND
                or self._crowded(x, z)
            ):
                continue
            yaw = self.m_rnd.random() * 2 * math.pi if self.m_random_turn else 0.0
            entry = session.place(self.m_pick, [x, session.height_at(x, z), z], yaw)
            if entry is None:
                continue
            if self.m_random_scale and not self.m_pick.get("guid"):
                size = 1 + (self.m_rnd.random() * 2 - 1) * SCALE_SPREAD
                session.set_transform(entry, scale=[size] * 3 if entry.kind == "prop" else size)
            self.m_placed.append(entry)
            self.m_points.append((x, z))
            made += 1
        return made

    def _crowded(self, x: float, z: float) -> bool:
        return any(
            math.hypot(x - placed_x, z - placed_z) < self.m_spacing_tiles for placed_x, placed_z in self.m_points
        )

    def end(self) -> int:
        """Close the stroke: its placements become one undo step (named "spray"); returns how many there were."""
        if self._m_change is not None:
            self._m_change.__exit__(None, None, None)
            self._m_change = None
        return len(self.m_placed)
