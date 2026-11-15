"""An object's state: the condition bits its sequence's condition tracks (type 2 elements) and the view's hand hold at
a time, which a part's ``VISIBILITY_TRANSFORM``, ``DAYNIGHT_TRANSFORM`` and conditioned ``ANIMATION_TRANSFORM`` read
(docs/engine-animation.md "Spinning parts", "Time of day and the states of effects, lights and decals").

``States.of(sequence, seconds, held)`` is the entry; ``shown(when)`` says whether a part's masks let it show,
``day_night(times, night, since)`` how far its time slices let it (the fade), ``on_seconds(mask)`` how long a
conditioned motion has run; ``slice_on`` is the engine's test of a time slice at a time of day; ``bits_read(scene)``
lists the bits a model reads, ``bit_name`` names one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import reduce
from operator import or_

from ..models.cfgfields import CONDITION_BITS_117, ENGINE_STATE_BITS
from ..models.configtree import Sequence

NOON, MIDNIGHT = 0.5, 0.0  # the times of day (a share of the day) View > Night switches between
SUNRISE = 0.25  # a symmetric sun's path: the sunset at 1 - SUNRISE
RAMP = 0.1  # the ramps' length, a share of the day (2.4 hours)


def _clamp(v: float) -> float:
    return min(1.0, max(0.0, v))


def ramps(day_time: float, sunrise: float = SUNRISE) -> tuple[float, float, float, float]:
    """(day wide, day core, night core, night wide) at the time of day ``day_time``: the engine's four ramps around
    sunrise and sunset (engine-animation "The slices, the fade and the offset")."""
    # how far into the day: before the sunset in the evening, after the sunrise in the morning (negative: the night)
    inside = (1.0 - sunrise - day_time) if day_time >= 0.5 else (day_time - sunrise)
    wide = _clamp((inside + RAMP) / (2 * RAMP))
    return wide, _clamp(inside / RAMP), _clamp(-inside / RAMP), 1.0 - wide


def slice_on(time_slice: int, day_time: float, offset: float = 0.0) -> bool:
    """Whether a ``DAYNIGHT_TRANSFORM``'s ``TimeSlice`` is on at ``day_time`` (``offset``: the object's random offset,
    0 without ``ApplyRandomOffset``): 0 to 2 the night's three windows, 3 to 5 their complements, the rest always."""
    day_wide, day_core, night_core, night_wide = ramps(day_time)
    tests = {0: night_core >= 1 - offset, 1: night_core > offset, 2: night_wide > offset,
             3: day_core >= 1 - offset, 4: day_core > offset, 5: day_wide > offset}  # fmt: skip
    return tests.get(time_slice, True)


def slice_fade(time_slice: int, fade_time: float, night: bool, since: float) -> float:
    """The fade (0..1) of a time slice ``since`` seconds after View > Night switched to ``night``: towards on or off
    over ``fade_time`` seconds where the switch changed it, else at its end (the engine's fade, played from a full
    one: engine-animation "The slices, the fade and the offset")."""
    on = slice_on(time_slice, MIDNIGHT if night else NOON)
    if on == slice_on(time_slice, NOON if night else MIDNIGHT) or fade_time <= 0:
        return 1.0 if on else 0.0
    k = _clamp(since / fade_time)
    return k if on else 1.0 - k


def bit_name(bit: int, game: str) -> str:
    """A condition bit's name: 117's list (``cfgfields.CONDITION_BITS_117``), the number for the other games."""
    if game == "anno117" and bit < len(CONDITION_BITS_117):
        return CONDITION_BITS_117[bit]
    return f"{bit}"


def bits_read(scene) -> list[int]:
    """The condition bits a scene (``modelscene.Scene``) reads or sets, with 117's engine's own (destroyed, selected,
    unselected), lowest first: what the view offers to hold."""
    found = [ENGINE_STATE_BITS if scene.game == "anno117" else 0]
    for thing in (*scene.parts, *scene.effects, *scene.lights, *scene.decals):
        found += [*thing.when, *(t[1] for t in thing.times), *(getattr(s, "when", 0) for s in thing.motion)]
    found += [e.condition for seq in scene.sequences for track in seq.tracks for e in track if e.kind == 2]
    masks = reduce(or_, found, 0)
    return [b for b in range(32) if masks >> b & 1]


@dataclass(frozen=True)
class States:
    """The condition bits on now (``bits``), and the windows (start, end, bits) in view seconds that set them up to
    now, for the time a mask has been on."""

    bits: int = 0
    windows: tuple[tuple[float, float, int], ...] = field(default_factory=tuple)
    seconds: float = 0.0
    held: int = 0  # the bits the view holds by hand (View > Condition bits), on all along

    @classmethod
    def of(cls, sequence: Sequence | None, seconds: float, held: int = 0) -> States:
        """The state ``sequence`` sets at ``seconds`` of the view, with the bits ``held`` by hand."""
        if sequence is None:
            return cls(held, (), seconds, held)
        speed = sequence.speed if sequence.speed > 0 else 1.0
        length = max(sequence.duration_ms / 1000, 0.001) / speed  # one pass in view seconds
        elements = [(e.start_ms / 1000 / speed, max(e.end_ms, e.start_ms + 1) / 1000 / speed, e.condition)
                    for track in sequence.tracks for e in track if e.kind == 2 and e.condition]  # fmt: skip
        passes = int(seconds // length) if sequence.looped else 0
        windows = []
        for k in range(passes + 1):
            for start, end, bits in elements:
                a, b = start + k * length, end + k * length
                if a < seconds:
                    windows.append((a, min(b, seconds), bits))
        local = seconds - passes * length if sequence.looped else seconds
        now = held
        for start, end, bits in elements:
            if start <= local < end:
                now |= bits
        return cls(now, tuple(windows), seconds, held)

    def shown(self, when: tuple[int, ...]) -> bool:
        """Whether a part shows: each of its masks (its own, its nested files') has a bit on."""
        return all(mask & self.bits for mask in when)

    def day_night(self, times: tuple[tuple[int, int, float], ...], night: bool, since: float = math.inf) -> float:
        """How far a part's ``DAYNIGHT_TRANSFORM``s (its own, its nested files': time slice, condition bits, fade
        time) let it show by night (``night``) or by day, ``since`` seconds after View > Night switched: the product
        of their fades (0 hidden, 1 in full), each 0 while its bits (if any) are all off [I]."""
        out = 1.0
        for time_slice, bits, fade_time in times:
            out *= slice_fade(time_slice, fade_time, night, since) if not bits or bits & self.bits else 0.0
        return out

    def on_seconds(self, mask: int) -> float:
        """The view seconds up to now during which one of ``mask``'s bits was on (a bit held by hand counts all
        along)."""
        if not mask or mask & self.held:
            return self.seconds
        spans = sorted((a, b) for a, b, bits in self.windows if bits & mask)
        total, reach = 0.0, float("-inf")
        for a, b in spans:  # the union of the windows
            a = max(a, reach)
            if b > a:
                total += b - a
                reach = b
        return total
