"""The object brush: spray any model of the library over the ground along a stroke, with a spacing between the
objects, a random turn and a little random size.

``ObjectBrush`` is one stroke on an ``EditSession``: ``begin`` at the first point, ``to`` for each further point,
``end`` when the button goes up; everything the stroke placed is one step of the undo stack. The placed objects are
ordinary added objects (``EditSession.place``), so the edit file and the build need nothing new. The forest brush is
its form for trees (island-editor "Full spec").
"""

from __future__ import annotations

import math
import random

from ..infra.i18n import tr
from .editsession import EditSession
from .selection import one_change

SPACING = 2.0  # tiles between two sprayed objects, at least
SCALE_SPREAD = 0.15  # a sprayed object is scaled by 1 +- this much, unless the scale is held
LAND = 0.05  # no object is sprayed onto ground lower than this (the sea)
TRIES = 6  # candidate spots per tile of path and unit of strength


class ObjectBrush:
    """One stroke of the object brush for the library pick ``pick`` (a model or an editor-only asset)."""

    def __init__(
        self,
        session: EditSession,
        pick: dict,
        radius: float,
        strength: float = 0.5,
        spacing: float = SPACING,
        random_turn: bool = True,
        random_scale: bool = True,
        seed: int | None = None,
    ) -> None:
        self.session, self.pick = session, pick
        self.radius, self.strength, self.spacing = float(radius), float(strength), max(0.25, float(spacing))
        self.random_turn, self.random_scale = random_turn, random_scale
        self.rnd = random.Random(seed)
        self.placed: list = []
        self.points: list[tuple[float, float]] = []  # where this stroke put something, for the spacing
        self.last: tuple[float, float] | None = None
        self._change = None  # the open undo step of the stroke

    @property
    def usable(self) -> bool:
        """A pick that can be sprayed: a model or an editor-only asset (a forest tree has its own brush)."""
        return (
            not self.session.readonly
            and self.pick.get("tree") is None
            and bool(self.pick.get("cfg") or self.pick.get("guid"))
        )

    def begin(self, x: float, z: float) -> int:
        self.last = None
        if self._change is None:
            self._change = one_change(self.session, tr("islands.spray"))
            self._change.__enter__()
        return self.to(x, z)

    def to(self, x: float, z: float) -> int:
        """Spray along the way from the last point to (x, z); returns how many objects this step placed."""
        if not self.usable:
            return 0
        a, b = self.last or (x, z), (x, z)
        self.last = b
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(1, math.ceil(length / max(self.radius / 2, 0.5)))
        count = 0
        for k in range(1, steps + 1) if length else (1,):
            t = k / steps
            count += self._stamp(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        return count

    def _stamp(self, cx: float, cz: float) -> int:
        tries = max(1, round(TRIES * self.strength * (self.radius / self.spacing) ** 2 / 4))
        made = 0
        for _ in range(tries):
            r, a = self.radius * math.sqrt(self.rnd.random()), self.rnd.random() * 2 * math.pi
            x, z = cx + r * math.cos(a), cz + r * math.sin(a)
            s = self.session
            if not (0 <= x <= s.size and 0 <= z <= s.size) or s.height_at(x, z) < LAND or self._crowded(x, z):
                continue
            yaw = self.rnd.random() * 2 * math.pi if self.random_turn else 0.0
            entry = s.place(self.pick, [x, s.height_at(x, z), z], yaw)
            if entry is None:
                continue
            if self.random_scale and not self.pick.get("guid"):
                size = 1 + (self.rnd.random() * 2 - 1) * SCALE_SPREAD
                s.set_transform(entry, scale=[size] * 3 if entry.kind == "prop" else size)
            self.placed.append(entry)
            self.points.append((x, z))
            made += 1
        return made

    def _crowded(self, x: float, z: float) -> bool:
        return any(math.hypot(x - px, z - pz) < self.spacing for px, pz in self.points)

    def end(self) -> int:
        """Close the stroke: its placements become one undo step (named "spray"); returns how many there were."""
        if self._change is not None:
            self._change.__exit__(None, None, None)
            self._change = None
        return len(self.placed)
