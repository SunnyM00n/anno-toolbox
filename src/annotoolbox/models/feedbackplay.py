"""A feedback file (``.fc``) as the game plays it: the actors the object makes of each item (chance, count, a group's
dummies), and every actor's loop for a state of the object worked out together (the barriers wait across items) as a
timeline: where the actor stands, which way it faces, how far it has faded in, which sequence it plays.

``read(root)`` (an ``isd2070`` tree) gives a ``Feedback`` (``models.feedbackread``); ``build`` the actors,
``play_all`` their ``Timeline``s (``play`` one actor alone), whose ``at(seconds)`` is a ``Pose``. Rules and chosen
values: docs/engine-cloth.md "The walking figures".
"""

from __future__ import annotations

import math
import random
import struct
from collections import Counter, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ..formats import isd2070
from .feedbackread import Actor, Dummy, Feedback, Loop, barriers_in, element_kind, read

__all__ = ["Actor", "Dummy", "Feedback", "Loop", "read"]

WALK_PACE = 0.22  # units a second of a 2070 walk whose unit's sequence speeds the view does not have (chosen)
HORIZON = 600.0  # seconds a timeline is worked out for (it then starts over)
MIN_STEP = 0.05  # a loop pass takes at least this long, so a loop of instant elements cannot spin
TICK = 0.001  # what an instant element stands: a barrier, a show without waiting
FADE, FADE_STAND = 1.0, 1.2  # a fade runs 1 s; with FadingMode 2 the actor stands 1.2 s for it
BARRIER, BRANCH, WALK = 7, 3, 0  # element types


@dataclass(frozen=True)
class Movement:
    """What an actor's walks need of its unit: ``Movement.BaseSpeed`` (None: a unit without one, 2070's, whose walk
    speed is ``SpeedFactorF`` units a second), the turn speeds in degrees a second (0: at once), and per sequence id
    its ``Speed`` in the unit's info file and its cycle in seconds (one play)."""

    base_speed: float | None = None
    rotation: float = 0.0
    spot_rotation: float = 0.0
    speeds: Mapping[int, float] = field(default_factory=dict)
    cycles: Mapping[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Built:
    """One actor the object makes of an item: the item's index, the actor's number among the item's, its unit
    ((GUID, variation) or None), its random seed; ``main`` the object itself (played for the barriers, not drawn)."""

    item: int
    number: int
    unit: tuple[int, int] | None
    seed: int
    main: bool = False


def build(feedback: Feedback, seed: int = 0, quality: float = 1.0) -> list[Built]:
    """The actors of every item: a main object always one, another item's made when a roll of 0 to 99 is at most
    ``CreateChance``, as many as the group ``MultiplyActorByDummyCount`` has dummies or a random count from
    ``ActorCount`` to ``MaxActorCount``, each kept by the feedback quality (1: all). A fixed seed: the same each
    time."""
    rng, out = random.Random(seed), []
    for k, actor in enumerate(feedback.actors):
        if not actor.states:
            continue
        if actor.main_object:
            out.append(Built(k, 0, None, seed * 7919 + k * 101, True))
            continue
        if rng.randrange(100) > actor.chance:
            continue
        if actor.by_dummies:
            count = len(feedback.groups.get(actor.by_dummies, ()))
        else:
            count = rng.randint(actor.actor_count, max(actor.actor_count, actor.max_count))
        for n in range(count):
            if not actor.always_visible and rng.random() > quality:
                continue
            unit = actor.units[n % len(actor.units)] if actor.units else None  # chosen: the list in turn
            out.append(Built(k, n, unit, seed * 7919 + k * 101 + n))
    return out


@dataclass(frozen=True)
class Pose:
    """Where an actor is at a time: place, heading (radians about +y), shown, the sequence it plays and since when,
    its scale and how far it has faded in (``alpha``; ``visible`` from half way)."""

    position: tuple[float, float, float]
    heading: float
    visible: bool
    sequence: int
    since: float
    scale: float = 1.0
    alpha: float = 1.0


@dataclass
class _Segment:
    start: float
    end: float
    path: tuple[tuple[float, float, float], ...]  # one point: standing
    heading: float  # standing: the heading it turns to; walking: the one it starts with
    sequence: int
    scale: float
    turn_from: float = 0.0  # the heading shown when it starts
    rate: float = 0.0  # radians a second it turns at (0: at once)
    element: isd2070.Node | None = None


def facing(dx: float, dz: float, default: float) -> float:
    """The turn about +y that points a unit's front (-x, as unit models are built) along (dx, dz)."""
    return math.atan2(dz, -dx) if math.hypot(dx, dz) > 1e-9 else default


def _approach(start: float, goal: float, step: float) -> float:
    """From ``start`` toward ``goal`` the short way round by at most ``step`` radians."""
    d = (goal - start + math.pi) % (2 * math.pi) - math.pi
    return goal if abs(d) <= step else start + math.copysign(step, d)


class Timeline:
    """An actor's segments and fades over ``HORIZON`` seconds; ``at(seconds)`` the pose (it repeats after the
    horizon), ``spans()`` when each element first ran."""

    def __init__(self, segments: list[_Segment], fades: list[tuple[float, bool, int]] | None = None) -> None:
        self.segments = segments or [_Segment(0.0, HORIZON, ((0.0, 0.0, 0.0),), 0.0, 0, 1.0)]
        self.length = max(self.segments[-1].end, MIN_STEP)
        self.fades = sorted(fades or [(0.0, False, 0)], key=lambda f: f[0])
        self.held = False  # stopped at a barrier that never opens

    def alpha(self, t: float) -> float:
        """How far the actor has faded in at ``t`` (within the horizon): the last switch before it, a fade running
        linearly over a second from its time."""
        last = next((f for f in reversed(self.fades) if f[0] <= t), None)
        if last is None:
            return 0.0
        at, show, mode = last
        k = 1.0 if mode == 0 else min(max((t - at) / FADE, 0.0), 1.0)
        return k if show else 1.0 - k

    def at(self, seconds: float) -> Pose:
        t = seconds % self.length
        lo, hi = 0, len(self.segments) - 1
        while lo < hi:  # the last segment starting at or before t
            mid = (lo + hi + 1) // 2
            if self.segments[mid].start <= t:
                lo = mid
            else:
                hi = mid - 1
        s = self.segments[lo]
        k = 0.0 if s.end <= s.start else min(max((t - s.start) / (s.end - s.start), 0.0), 1.0)
        pos, heading = _along(s.path, k, s.heading)
        if s.rate > 0:
            heading = _approach(s.turn_from, heading, s.rate * (t - s.start))
        alpha = self.alpha(t)
        return Pose(pos, heading, alpha > 0.5, s.sequence, seconds - (t - s.start), s.scale, alpha)

    def spans(self) -> list[tuple[isd2070.Node, float, float]]:
        """(element, start, end) of each element's first run, in time order (the elements that take time)."""
        seen, out = set(), []
        for s in self.segments:
            if s.element is not None and id(s.element) not in seen:
                seen.add(id(s.element))
                out.append((s.element, s.start, s.end))
        return out


def _along(path, k: float, heading: float):
    """The point a share ``k`` of the way along a polyline, and the heading of its piece there."""
    if len(path) == 1:
        return path[0], heading
    lengths = [math.dist(a, b) for a, b in zip(path, path[1:], strict=False)]
    total = sum(lengths)
    if total <= 0:
        return path[-1], heading
    goal, run = k * total, 0.0
    for (a, b), length in zip(zip(path, path[1:], strict=False), lengths, strict=True):
        if run + length >= goal and length > 0:
            u = (goal - run) / length
            point = tuple(p + (q - p) * u for p, q in zip(a, b, strict=True))
            return point, facing(b[0] - a[0], b[2] - a[2], heading)
        run += length
    a, b = path[-2], path[-1]
    return path[-1], facing(b[0] - a[0], b[2] - a[2], heading)


class _Player:
    """Runs an actor's elements into segments and fades; ``script`` is a generator that stops at each barrier
    (yields its id and arrival time, is sent the time it opens)."""

    def __init__(self, feedback: Feedback, seed: int, movement: Movement | None = None, scale_speed: bool = True,
                 cycle: Callable[[int], float] | None = None) -> None:  # fmt: skip
        self.fb, self.rng = feedback, random.Random(seed)
        self.move = movement or Movement()
        self.scale_speed = scale_speed
        cycles = self.move.cycles
        self.cycle = cycle or (lambda sequence: cycles.get(sequence) or 1.0)
        self.t, self.pos, self.heading, self.shown = 0.0, (0.0, 0.0, 0.0), 0.0, 0.0
        self.sequence, self.scale = 0, 1.0
        self.chain: float | None = None  # the speed of the walk chain under way (back-to-back walks)
        self.out: list[_Segment] = []
        self.fades: list[tuple[float, bool, int]] = []

    def add(self, seconds: float, path=None, sequence: int | None = None, element=None, least: float = MIN_STEP):
        seconds = max(seconds, least)
        path = tuple(path) if path else (self.pos,)
        if sequence is not None:
            self.sequence = sequence
        walking = len(path) > 1
        end_heading = self.heading
        if walking:
            a, b = path[-2], path[-1]
            end_heading = facing(b[0] - a[0], b[2] - a[2], self.heading)
        rate = math.radians(self.move.rotation if walking else self.move.spot_rotation or self.move.rotation)
        self.out.append(_Segment(self.t, self.t + seconds, path, self.heading, self.sequence, self.scale, self.shown,
                                 rate, element))  # fmt: skip
        self.t += seconds
        self.pos, self.heading = path[-1], end_heading
        self.shown = end_heading if walking or rate <= 0 else _approach(self.shown, self.heading, rate * seconds)

    def start(self, loop: Loop, number: int) -> None:
        """At the loop's start dummy; without one at dummy ``number`` of its start group (by the data, a group's
        dummies are numbered in order), else the origin; playing the loop's default sequence (docs/engine-cloth.md
        "StartDummyGroup")."""
        self.sequence = loop.sequence
        d = self.fb.dummy(loop.dummy, loop.dummy_id)
        group = self.fb.groups.get(loop.start_group, ()) if d is None else ()
        if d is not None:
            self.pos, self.heading = d.placed(), d.heading
        elif group:
            g = group[number % len(group)]
            self.pos, self.heading = g.position, g.heading
        else:
            self.pos, self.heading = (0.0, 0.0, 0.0), 0.0  # the object's origin
        self.shown = self.heading
        self.fades.append((self.t, loop.visible, 1 if loop.fade else 0))

    def run(self, elements, depth: int = 0):
        for e in elements:
            if self.t >= HORIZON or depth > 8:
                return
            kind = element_kind(e)
            if kind != WALK:
                self.chain = None
            if kind == BARRIER:
                yield from self._barrier(e)
            elif kind == BRANCH:
                yield from self._branch(e, depth)
            else:
                handler = ELEMENTS.get(kind)
                if handler is not None:
                    handler(self, e)

    def _barrier(self, e: isd2070.Node):
        arrival = self.t + max(e.int("TimeOffset"), 0) / 1000.0
        opens = yield (e.int("BarrierId"), arrival)
        self.add(max(float(opens) - self.t, TICK), element=e, least=TICK)

    def _branch(self, e: isd2070.Node, depth: int):
        branches = e.path("BranchList").findall("i") if e.path("BranchList") is not None else []
        roll = self.rng.uniform(0, 100)
        for b in branches:
            if roll < b.int("pair1"):
                found = b.path("pair2/Elements")
                yield from self.run(found.findall("i") if found is not None else [], depth + 1)
                return

    def script(self, loops: tuple[Loop, ...], number: int = 0):
        """The enter loop once, then the repeat loop until the horizon."""
        if not loops:
            return
        first, repeat = loops[0], loops[1] if len(loops) > 1 else loops[0]
        self.start(first, number)
        yield from self.run(first.elements)
        while self.t < HORIZON:
            before = self.t
            if repeat.reset:  # every pass starts again at the default place and visibility
                self.start(repeat, number)
            yield from self.run(repeat.elements)
            if self.t < before + MIN_STEP:  # an empty or instant loop stands to the horizon
                self.add(HORIZON - self.t)

    def pace(self, e: isd2070.Node) -> float:
        """A walk's speed in units a second (docs/engine-cloth.md "Pace of a walk"); a walk chain keeps the
        first's."""
        if self.chain is not None:
            return self.chain
        f = e.float("SpeedFactorF", e.float("SpeedFactor", 0.0))
        base = self.move.base_speed
        if base is None:  # 2070: a spline's SpeedFactor (x4096) is its speed, else the walk sequence's Speed
            raw = e.value("SpeedFactor", "") or ""
            f = int(raw) / isd2070.FIXED if e.find("SpeedFactorF") is None and raw.lstrip("-").isdigit() else f
            listed = self.move.speeds.get(e.int("WalkSequence"), 1.0 if self.move.speeds else WALK_PACE)
            speed = f if f > 0 else listed
        elif f > 0:
            speed = f * base
        else:
            own = self.move.speeds.get(e.int("WalkSequence"), 1.0)
            speed = own * (self.scale if self.scale_speed else 1.0) * base
        self.chain = max(speed, 1e-3) if element_kind(e) == WALK else None
        return max(speed, 1e-3)

    def walk_to(self, target, pace: float, sequence: int, turn: float | None, element=None) -> None:
        span = math.dist(self.pos, target)
        if span > 1e-6:
            self.add(span / pace, (self.pos, target), sequence, element)
        if turn is not None:
            self.heading = turn


def _walk(p: _Player, e: isd2070.Node) -> None:
    grouped = p.fb.group_dummy(e.find("DummyGroup"), p.rng)
    target = grouped or p.fb.dummy(e.value("TargetDummy", "") or "", e.int("TargetDummyId"))
    if target is None:
        return
    if not e.int("WalkFromCurrentPosition", 1):
        start = p.fb.dummy(e.value("StartDummy", "") or "", e.int("StartDummyId"))
        if start is not None:
            p.pos = start.placed()
    turn = target.heading if e.int("UseTargetDummyDirection") else None
    where = target.position if grouped is not None else target.placed()  # a group's dummy keeps its own height
    p.walk_to(where, p.pace(e), e.int("WalkSequence"), turn, e)


def _play(p: _Player, e: isd2070.Node) -> None:
    sequence = e.int("m_IdleSequenceID")
    seconds = max(e.int("MinPlayTime") / 1000.0, max(e.int("MinPlayCount"), 1) * p.cycle(sequence))
    p.add(seconds, sequence=sequence, element=e)


def _wait(p: _Player, e: isd2070.Node) -> None:
    lo, hi = e.int("MinTime"), max(e.int("MaxTime"), e.int("MinTime"))
    p.add(p.rng.uniform(lo, hi) / 1000.0, element=e)


def _spline(p: _Player, e: isd2070.Node) -> None:
    s = p.fb.splines.get(e.value("SplineName", "") or "")
    if s is None:
        return
    points = list(reversed(s.points)) if e.int("FollowBackwards") else list(s.points)
    pace, sequence = p.pace(e), e.int("WalkSequence")
    if e.int("MoveToSplineStart", 1):
        p.walk_to(points[0], pace, sequence, None, e)
    span = sum(math.dist(a, b) for a, b in zip(points, points[1:], strict=False))
    p.add(span / pace, (p.pos, *points[1:]) if math.dist(p.pos, points[0]) < 1e-6 else points, sequence, e)


def _show(p: _Player, e: isd2070.Node) -> None:
    """The switch at ``TimeOffset`` ms; with a fading mode a fade over a second, mode 2 standing 1.2 s for it."""
    mode = e.int("FadingMode", 1)
    p.fades.append((p.t + max(e.int("TimeOffset"), 0) / 1000.0, e.int("ShowObject", 1) != 0, mode))
    p.add(FADE_STAND if mode == 2 else TICK, element=e, least=TICK)


def _scale(p: _Player, e: isd2070.Node) -> None:
    lo, hi = e.float("m_MinScaleFactor", 1.0), e.float("m_MaxScaleFactor", 1.0)
    if lo > 0:
        p.scale = p.rng.uniform(lo, hi) if hi > lo else lo


def _turn(p: _Player, e: isd2070.Node) -> None:
    """To face a dummy (``TurnToDummy`` by name or id), else by ``TurnAngleF`` degrees; with the unit's turn speed it
    stands for the turn playing ``TurnSequence``."""
    name = e.value("TurnToDummy", "") or ""
    target = p.fb.dummy(name, e.int("TurnToDummyId")) if name or e.int("TurnToDummyId") else None
    before = p.heading
    if target is not None:
        p.heading = facing(target.position[0] - p.pos[0], target.position[2] - p.pos[2], p.heading)
    else:
        p.heading += math.radians(e.float("TurnAngleF", e.float("TurnAngle", 0.0)))
    rate = math.radians(p.move.spot_rotation or p.move.rotation)
    angle = abs((p.heading - before + math.pi) % (2 * math.pi) - math.pi)
    if rate > 0 and angle > 1e-3:
        p.add(angle / rate, sequence=e.int("TurnSequence") or None, element=e, least=TICK)


def _around(p: _Player, e: isd2070.Node) -> None:
    centre = p.fb.dummy(e.value("CenterDummy", "") or "", e.int("CenterDummyId"))
    if centre is None:
        return
    r = e.float("RandomRadiusF", e.float("RandomRadius", 0.0)) * math.sqrt(p.rng.random())
    a = p.rng.uniform(0, 2 * math.pi)
    c = centre.placed()
    target = (c[0] + r * math.cos(a), c[1], c[2] + r * math.sin(a))
    turn = math.radians(e.float("TargetDirectionF")) if e.int("UseTargetDirection") else None
    p.walk_to(target, p.pace(e), e.int("WalkSequence"), turn, e)


def _one_of(p: _Player, e: isd2070.Node) -> None:
    node = e.find("m_SequenceIds")
    if node is not None and node.data:
        ids = struct.unpack(f"<{len(node.data) // 4}I", node.data[: len(node.data) // 4 * 4])
    else:
        ids = tuple(int(i.text) for i in (node.children if node is not None else []) if (i.text or "").strip())
    lo, hi = e.int("MinPlayTime"), max(e.int("MaxPlayTime"), e.int("MinPlayTime"))
    p.add(max(p.rng.uniform(lo, hi), 1000.0) / 1000.0, sequence=p.rng.choice(ids) if ids else p.sequence, element=e)


ELEMENTS = {0: _walk, 1: _play, 2: _wait, 4: _spline, 6: _show, 9: _scale, 10: _turn, 11: _around, 12: _one_of,
            13: _around}  # fmt: skip


def _loops(actor: Actor, state: int) -> tuple[Loop, ...]:
    """The loops an actor plays in ``state`` (a state the item has none for: its lowest)."""
    return actor.states.get(state) or (actor.states[min(actor.states)] if actor.states else ())


def play_all(feedback: Feedback, built: list[Built], state: int,
             movement: Callable[[Built], Movement | None] = lambda _b: None) -> list[Timeline]:  # fmt: skip
    """Every built actor's timeline in ``state``, worked out together: a barrier opens when its arrivals reach one
    per actor and barrier element of that id in its item (``Actor.barriers``), all who wait on it then go on; one
    that never opens holds its actors to the horizon (no timeout)."""
    players, scripts, required, reachable = [], [], Counter(), Counter()
    for b in built:
        actor = feedback.actors[b.item]
        required.update(actor.barriers)
        own: Counter = Counter()
        for loop in _loops(actor, state):
            barriers_in(loop.elements, own)
        reachable.update(set(own))  # an actor waits at one barrier at a time
        p = _Player(feedback, b.seed, movement(b), actor.scale_speed)
        players.append(p)
        scripts.append(p.script(_loops(actor, state), b.number))
    waiting: dict[int, tuple[int, float]] = {}
    arrived: Counter = Counter()
    runnable, sent = deque(range(len(scripts))), {}
    while runnable:
        k = runnable.popleft()
        try:
            barrier, at = scripts[k].send(sent.pop(k, None))
        except StopIteration:
            continue
        waiting[k] = (barrier, at)
        arrived[barrier] += 1
        if arrived[barrier] >= required[barrier]:
            who = [j for j, (i, _at) in waiting.items() if i == barrier]
            opens = max(waiting[j][1] for j in who)
            for j in who:
                del waiting[j]
                sent[j] = opens
                runnable.append(j)
            arrived[barrier] = 0
    for k in list(waiting):  # held for good: they stand to the horizon
        try:
            scripts[k].send(HORIZON)
        except StopIteration:
            pass
    out = [Timeline(p.out, p.fades) for p in players]
    for k, (barrier, _at) in waiting.items():  # held: fewer actors can wait there than it needs (else the horizon)
        out[k].held = reachable[barrier] < required[barrier]
    return out


def play(feedback: Feedback, actor: Actor, state: int, seed: int = 0, cycle=None,
         movement: Movement | None = None) -> Timeline:  # fmt: skip
    """One actor's timeline alone in ``state`` (its barriers open by its own arrivals); ``cycle(sequence)`` the
    seconds one play of a sequence takes (default: ``movement``'s, else 1)."""
    p = _Player(feedback, seed, movement, actor.scale_speed, cycle)
    required = actor.barriers
    script, arrived, send, held = p.script(_loops(actor, state)), Counter(), None, False
    while True:
        try:
            barrier, at = script.send(send)
        except StopIteration:
            break
        arrived[barrier] += 1
        opens = arrived[barrier] >= required[barrier]
        send, held = (at, held) if opens else (HORIZON, True)  # alone, a barrier not opened by it never opens
        arrived[barrier] = 0 if opens else arrived[barrier]
    out = Timeline(p.out, p.fades)
    out.held = held
    return out
