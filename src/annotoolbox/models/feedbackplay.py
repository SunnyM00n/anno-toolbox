"""A feedback file (``.fc``) as the game plays it: the actors the object makes of each item (chance, count, a group's
dummies), and every actor's loop for a state of the object worked out together (the barriers wait across items) as a
timeline: where the actor stands, which way it faces, how far it has faded in, which sequence it plays.

``read(root)`` (an ``isd2070`` tree) gives a ``SFeedback`` (``models.feedbackread``); ``build`` the actors,
``play_all`` their ``CTimeline``s (``play`` one actor alone), whose ``at(seconds)`` is a ``SPose``. Rules and chosen
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
from .feedbackread import SActor, SDummy, SFeedback, SLoop, barriers_in, element_kind, read

__all__ = ["SActor", "SDummy", "SFeedback", "SLoop", "read"]

WALK_PACE = 0.22  # units a second of a 2070 walk whose unit's sequence speeds the view does not have (chosen)
HORIZON = 600.0  # seconds a timeline is worked out for (it then starts over)
MIN_STEP = 0.05  # a loop pass takes at least this long, so a loop of instant elements cannot spin
TICK = 0.001  # what an instant element stands: a barrier, a show without waiting
FADE, FADE_STAND = 1.0, 1.2  # a fade runs 1 s; with FadingMode 2 the actor stands 1.2 s for it
BARRIER, BRANCH, WALK = 7, 3, 0  # element types


@dataclass(frozen=True)
class SMovement:
    """What an actor's walks need of its unit: ``SMovement.BaseSpeed`` (None: a unit without one, 2070's, whose walk
    speed is ``SpeedFactorF`` units a second), the turn speeds in degrees a second (0: at once), and per sequence id
    its ``Speed`` in the unit's info file and its cycle in seconds (one play)."""

    base_speed: float | None = None
    rotation: float = 0.0
    spot_rotation: float = 0.0
    speeds: Mapping[int, float] = field(default_factory=dict)
    cycles: Mapping[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SBuilt:
    """One actor the object makes of an item: the item's index, the actor's number among the item's, its unit
    ((GUID, variation) or None), its random seed; ``main`` the object itself (played for the barriers, not drawn)."""

    item: int
    number: int
    unit: tuple[int, int] | None
    seed: int
    main: bool = False


def build(feedback: SFeedback, seed: int = 0, quality: float = 1.0) -> list[SBuilt]:
    """The actors of every item: a main object always one, another item's made when a roll of 0 to 99 is at most
    ``CreateChance``, as many as the group ``MultiplyActorByDummyCount`` has dummies or a random count from
    ``ActorCount`` to ``MaxActorCount``, each kept by the feedback quality (1: all). A fixed seed: the same each
    time."""
    rng, out = random.Random(seed), []
    for item, actor in enumerate(feedback.actors):
        if not actor.states:
            continue
        if actor.main_object:
            out.append(SBuilt(item, 0, None, seed * 7919 + item * 101, True))
            continue
        if rng.randrange(100) > actor.chance:
            continue
        if actor.by_dummies:
            count = len(feedback.groups.get(actor.by_dummies, ()))
        else:
            count = rng.randint(actor.actor_count, max(actor.actor_count, actor.max_count))
        for number in range(count):
            if not actor.always_visible and rng.random() > quality:
                continue
            unit = actor.units[number % len(actor.units)] if actor.units else None  # chosen: the list in turn
            out.append(SBuilt(item, number, unit, seed * 7919 + item * 101 + number))
    return out


@dataclass(frozen=True)
class SPose:
    """Where an actor is at a time: place, heading (radians about +y), shown, the sequence it plays and since when,
    its scale and how far it has faded in (``alpha``; ``visible`` from half way)."""

    position: tuple[float, float, float]
    heading: float
    visible: bool
    sequence: int
    since_s: float
    scale: float = 1.0
    alpha: float = 1.0


@dataclass
class _SSegment:
    start: float
    end: float
    path: tuple[tuple[float, float, float], ...]  # one point: standing
    heading: float  # standing: the heading it turns to; walking: the one it starts with
    sequence: int
    scale: float
    turn_from: float = 0.0  # the heading shown when it starts
    rate: float = 0.0  # radians a second it turns at (0: at once)
    element: isd2070.SNode | None = None


def facing(dx: float, dz: float, default: float) -> float:
    """The turn about +y that points a unit's front (-x, as unit models are built) along (dx, dz)."""
    return math.atan2(dz, -dx) if math.hypot(dx, dz) > 1e-9 else default


def _approach(start: float, goal: float, step: float) -> float:
    """From ``start`` toward ``goal`` the short way round by at most ``step`` radians."""
    difference = (goal - start + math.pi) % (2 * math.pi) - math.pi
    return goal if abs(difference) <= step else start + math.copysign(step, difference)


class CTimeline:
    """An actor's segments and fades over ``HORIZON`` seconds; ``at(seconds)`` the pose (it repeats after the
    horizon), ``spans()`` when each element first ran."""

    def __init__(self, segments: list[_SSegment], fades: list[tuple[float, bool, int]] | None = None) -> None:
        self.m_segments = segments or [_SSegment(0.0, HORIZON, ((0.0, 0.0, 0.0),), 0.0, 0, 1.0)]
        self.m_length_s = max(self.m_segments[-1].end, MIN_STEP)
        self.m_fades = sorted(fades or [(0.0, False, 0)], key=lambda fade: fade[0])
        self.m_held = False  # stopped at a barrier that never opens

    def alpha(self, seconds: float) -> float:
        """How far the actor has faded in at ``t`` (within the horizon): the last switch before it, a fade running
        linearly over a second from its time."""
        last = next((fade for fade in reversed(self.m_fades) if fade[0] <= seconds), None)
        if last is None:
            return 0.0
        fade_start, is_shown, mode = last
        progress = 1.0 if mode == 0 else min(max((seconds - fade_start) / FADE, 0.0), 1.0)
        return progress if is_shown else 1.0 - progress

    def at(self, seconds: float) -> SPose:
        cycle_seconds = seconds % self.m_length_s
        low, high = 0, len(self.m_segments) - 1
        while low < high:  # the last segment starting at or before t
            mid = (low + high + 1) // 2
            if self.m_segments[mid].start <= cycle_seconds:
                low = mid
            else:
                high = mid - 1
        segment = self.m_segments[low]
        progress = (
            0.0
            if segment.end <= segment.start
            else min(max((cycle_seconds - segment.start) / (segment.end - segment.start), 0.0), 1.0)
        )
        position, heading = _along(segment.path, progress, segment.heading)
        if segment.rate > 0:
            heading = _approach(segment.turn_from, heading, segment.rate * (cycle_seconds - segment.start))
        alpha = self.alpha(cycle_seconds)
        return SPose(
            position,
            heading,
            alpha > 0.5,
            segment.sequence,
            seconds - (cycle_seconds - segment.start),
            segment.scale,
            alpha,
        )

    def spans(self) -> list[tuple[isd2070.SNode, float, float]]:
        """(element, start, end) of each element's first run, in time order (the elements that take time)."""
        seen, out = set(), []
        for segment in self.m_segments:
            if segment.element is not None and id(segment.element) not in seen:
                seen.add(id(segment.element))
                out.append((segment.element, segment.start, segment.end))
        return out


def _along(path, share: float, heading: float):
    """The point a share ``k`` of the way along a polyline, and the heading of its piece there."""
    if len(path) == 1:
        return path[0], heading
    lengths = [math.dist(start, end) for start, end in zip(path, path[1:], strict=False)]
    total = sum(lengths)
    if total <= 0:
        return path[-1], heading
    goal, run = share * total, 0.0
    for (start, end), length in zip(zip(path, path[1:], strict=False), lengths, strict=True):
        if run + length >= goal and length > 0:
            fraction = (goal - run) / length
            point = tuple(
                from_value + (to_value - from_value) * fraction for from_value, to_value in zip(start, end, strict=True)
            )
            return point, facing(end[0] - start[0], end[2] - start[2], heading)
        run += length
    start, end = path[-2], path[-1]
    return path[-1], facing(end[0] - start[0], end[2] - start[2], heading)


class _CPlayer:
    """Runs an actor's elements into segments and fades; ``script`` is a generator that stops at each barrier
    (yields its id and arrival time, is sent the time it opens)."""

    def __init__(self, feedback: SFeedback, seed: int, movement: SMovement | None = None, scale_speed: bool = True,
                 cycle: Callable[[int], float] | None = None) -> None:  # fmt: skip
        self.m_feedback, self.m_random_generator = feedback, random.Random(seed)
        self.m_movement = movement or SMovement()
        self.m_scale_speed = scale_speed
        cycles = self.m_movement.cycles
        self.m_cycle = cycle or (lambda sequence: cycles.get(sequence) or 1.0)
        self.m_time_s, self.m_position, self.m_heading, self.m_shown = 0.0, (0.0, 0.0, 0.0), 0.0, 0.0
        self.m_sequence, self.m_scale = 0, 1.0
        self.m_chain: float | None = None  # the speed of the walk chain under way (back-to-back walks)
        self.m_out: list[_SSegment] = []
        self.m_fades: list[tuple[float, bool, int]] = []

    def add(self, seconds: float, path=None, sequence: int | None = None, element=None, min_seconds: float = MIN_STEP):
        seconds = max(seconds, min_seconds)
        path = tuple(path) if path else (self.m_position,)
        if sequence is not None:
            self.m_sequence = sequence
        walking = len(path) > 1
        end_heading = self.m_heading
        if walking:
            previous, last = path[-2], path[-1]
            end_heading = facing(last[0] - previous[0], last[2] - previous[2], self.m_heading)
        rate = math.radians(
            self.m_movement.rotation if walking else self.m_movement.spot_rotation or self.m_movement.rotation
        )
        self.m_out.append(_SSegment(self.m_time_s, self.m_time_s + seconds, path, self.m_heading, self.m_sequence, self.m_scale, self.m_shown,
                                 rate, element))  # fmt: skip
        self.m_time_s += seconds
        self.m_position, self.m_heading = path[-1], end_heading
        self.m_shown = end_heading if walking or rate <= 0 else _approach(self.m_shown, self.m_heading, rate * seconds)

    def start(self, loop: SLoop, number: int) -> None:
        """At the loop's start dummy; without one at dummy ``number`` of its start group (by the data, a group's
        dummies are numbered in order), else the origin; playing the loop's default sequence (docs/engine-cloth.md
        "StartDummyGroup")."""
        self.m_sequence = loop.sequence
        dummy = self.m_feedback.dummy(loop.dummy, loop.dummy_id)
        group = self.m_feedback.groups.get(loop.start_group, ()) if dummy is None else ()
        if dummy is not None:
            self.m_position, self.m_heading = dummy.placed(), dummy.heading
        elif group:
            member = group[number % len(group)]
            self.m_position, self.m_heading = member.position, member.heading
        else:
            self.m_position, self.m_heading = (0.0, 0.0, 0.0), 0.0  # the object's origin
        self.m_shown = self.m_heading
        self.m_fades.append((self.m_time_s, loop.visible, 1 if loop.fade else 0))

    def run(self, elements, depth: int = 0):
        for element in elements:
            if self.m_time_s >= HORIZON or depth > 8:
                return
            kind = element_kind(element)
            if kind != WALK:
                self.m_chain = None
            if kind == BARRIER:
                yield from self._barrier(element)
            elif kind == BRANCH:
                yield from self._branch(element, depth)
            else:
                handler = ELEMENTS.get(kind)
                if handler is not None:
                    handler(self, element)

    def _barrier(self, element: isd2070.SNode):
        arrival = self.m_time_s + max(element.int("TimeOffset"), 0) / 1000.0
        opens = yield (element.int("BarrierId"), arrival)
        self.add(max(float(opens) - self.m_time_s, TICK), element=element, min_seconds=TICK)

    def _branch(self, element: isd2070.SNode, depth: int):
        branches = element.path("BranchList").findall("i") if element.path("BranchList") is not None else []
        roll = self.m_random_generator.uniform(0, 100)
        for branch in branches:
            if roll < branch.int("pair1"):
                found = branch.path("pair2/Elements")
                yield from self.run(found.findall("i") if found is not None else [], depth + 1)
                return

    def script(self, loops: tuple[SLoop, ...], number: int = 0):
        """The enter loop once, then the repeat loop until the horizon."""
        if not loops:
            return
        first, repeat = loops[0], loops[1] if len(loops) > 1 else loops[0]
        self.start(first, number)
        yield from self.run(first.elements)
        while self.m_time_s < HORIZON:
            before = self.m_time_s
            if repeat.reset:  # every pass starts again at the default place and visibility
                self.start(repeat, number)
            yield from self.run(repeat.elements)
            if self.m_time_s < before + MIN_STEP:  # an empty or instant loop stands to the horizon
                self.add(HORIZON - self.m_time_s)

    def pace(self, element: isd2070.SNode) -> float:
        """A walk's speed in units a second (docs/engine-cloth.md "Pace of a walk"); a walk chain keeps the
        first's."""
        if self.m_chain is not None:
            return self.m_chain
        factor = element.float("SpeedFactorF", element.float("SpeedFactor", 0.0))
        base = self.m_movement.base_speed
        if base is None:  # 2070: a spline's SpeedFactor (x4096) is its speed, else the walk sequence's Speed
            raw = element.value("SpeedFactor", "") or ""
            factor = (
                int(raw) / isd2070.FIXED
                if element.find("SpeedFactorF") is None and raw.lstrip("-").isdigit()
                else factor
            )
            listed = self.m_movement.speeds.get(
                element.int("WalkSequence"), 1.0 if self.m_movement.speeds else WALK_PACE
            )
            speed = factor if factor > 0 else listed
        elif factor > 0:
            speed = factor * base
        else:
            own = self.m_movement.speeds.get(element.int("WalkSequence"), 1.0)
            speed = own * (self.m_scale if self.m_scale_speed else 1.0) * base
        self.m_chain = max(speed, 1e-3) if element_kind(element) == WALK else None
        return max(speed, 1e-3)

    def walk_to(self, target, pace: float, sequence: int, turn: float | None, element=None) -> None:
        span = math.dist(self.m_position, target)
        if span > 1e-6:
            self.add(span / pace, (self.m_position, target), sequence, element)
        if turn is not None:
            self.m_heading = turn


def _walk(player: _CPlayer, element: isd2070.SNode) -> None:
    grouped = player.m_feedback.group_dummy(element.find("DummyGroup"), player.m_random_generator)
    target = grouped or player.m_feedback.dummy(element.value("TargetDummy", "") or "", element.int("TargetDummyId"))
    if target is None:
        return
    if not element.int("WalkFromCurrentPosition", 1):
        start = player.m_feedback.dummy(element.value("StartDummy", "") or "", element.int("StartDummyId"))
        if start is not None:
            player.m_position = start.placed()
    turn = target.heading if element.int("UseTargetDummyDirection") else None
    where = target.position if grouped is not None else target.placed()  # a group's dummy keeps its own height
    player.walk_to(where, player.pace(element), element.int("WalkSequence"), turn, element)


def _play(player: _CPlayer, element: isd2070.SNode) -> None:
    sequence = element.int("m_IdleSequenceID")
    seconds = max(element.int("MinPlayTime") / 1000.0, max(element.int("MinPlayCount"), 1) * player.m_cycle(sequence))
    player.add(seconds, sequence=sequence, element=element)


def _wait(player: _CPlayer, element: isd2070.SNode) -> None:
    min_ms, max_ms = element.int("MinTime"), max(element.int("MaxTime"), element.int("MinTime"))
    player.add(player.m_random_generator.uniform(min_ms, max_ms) / 1000.0, element=element)


def _spline(player: _CPlayer, element: isd2070.SNode) -> None:
    spline = player.m_feedback.splines.get(element.value("SplineName", "") or "")
    if spline is None:
        return
    points = list(reversed(spline.points)) if element.int("FollowBackwards") else list(spline.points)
    pace, sequence = player.pace(element), element.int("WalkSequence")
    if element.int("MoveToSplineStart", 1):
        player.walk_to(points[0], pace, sequence, None, element)
    span = sum(math.dist(from_point, to_point) for from_point, to_point in zip(points, points[1:], strict=False))
    player.add(
        span / pace,
        (player.m_position, *points[1:]) if math.dist(player.m_position, points[0]) < 1e-6 else points,
        sequence,
        element,
    )


def _show(player: _CPlayer, element: isd2070.SNode) -> None:
    """The switch at ``TimeOffset`` ms; with a fading mode a fade over a second, mode 2 standing 1.2 s for it."""
    mode = element.int("FadingMode", 1)
    player.m_fades.append(
        (player.m_time_s + max(element.int("TimeOffset"), 0) / 1000.0, element.int("ShowObject", 1) != 0, mode)
    )
    player.add(FADE_STAND if mode == 2 else TICK, element=element, min_seconds=TICK)


def _scale(player: _CPlayer, element: isd2070.SNode) -> None:
    min_scale, max_scale = element.float("m_MinScaleFactor", 1.0), element.float("m_MaxScaleFactor", 1.0)
    if min_scale > 0:
        player.m_scale = player.m_random_generator.uniform(min_scale, max_scale) if max_scale > min_scale else min_scale


def _turn(player: _CPlayer, element: isd2070.SNode) -> None:
    """To face a dummy (``TurnToDummy`` by name or id), else by ``TurnAngleF`` degrees; with the unit's turn speed it
    stands for the turn playing ``TurnSequence``."""
    name = element.value("TurnToDummy", "") or ""
    target = (
        player.m_feedback.dummy(name, element.int("TurnToDummyId")) if name or element.int("TurnToDummyId") else None
    )
    before = player.m_heading
    if target is not None:
        player.m_heading = facing(
            target.position[0] - player.m_position[0], target.position[2] - player.m_position[2], player.m_heading
        )
    else:
        player.m_heading += math.radians(element.float("TurnAngleF", element.float("TurnAngle", 0.0)))
    rate = math.radians(player.m_movement.spot_rotation or player.m_movement.rotation)
    angle = abs((player.m_heading - before + math.pi) % (2 * math.pi) - math.pi)
    if rate > 0 and angle > 1e-3:
        player.add(angle / rate, sequence=element.int("TurnSequence") or None, element=element, min_seconds=TICK)


def _around(player: _CPlayer, element: isd2070.SNode) -> None:
    centre = player.m_feedback.dummy(element.value("CenterDummy", "") or "", element.int("CenterDummyId"))
    if centre is None:
        return
    radius = element.float("RandomRadiusF", element.float("RandomRadius", 0.0)) * math.sqrt(
        player.m_random_generator.random()
    )
    angle = player.m_random_generator.uniform(0, 2 * math.pi)
    center_position = centre.placed()
    target = (
        center_position[0] + radius * math.cos(angle),
        center_position[1],
        center_position[2] + radius * math.sin(angle),
    )
    turn = math.radians(element.float("TargetDirectionF")) if element.int("UseTargetDirection") else None
    player.walk_to(target, player.pace(element), element.int("WalkSequence"), turn, element)


def _one_of(player: _CPlayer, element: isd2070.SNode) -> None:
    node = element.find("m_SequenceIds")
    if node is not None and node.data:
        ids = struct.unpack(f"<{len(node.data) // 4}I", node.data[: len(node.data) // 4 * 4])
    else:
        ids = tuple(int(item.text) for item in (node.children if node is not None else []) if (item.text or "").strip())
    min_ms, max_ms = element.int("MinPlayTime"), max(element.int("MaxPlayTime"), element.int("MinPlayTime"))
    player.add(
        max(player.m_random_generator.uniform(min_ms, max_ms), 1000.0) / 1000.0,
        sequence=player.m_random_generator.choice(ids) if ids else player.m_sequence,
        element=element,
    )


ELEMENTS = {0: _walk, 1: _play, 2: _wait, 4: _spline, 6: _show, 9: _scale, 10: _turn, 11: _around, 12: _one_of,
            13: _around}  # fmt: skip


def _loops(actor: SActor, state: int) -> tuple[SLoop, ...]:
    """The loops an actor plays in ``state`` (a state the item has none for: its lowest)."""
    return actor.states.get(state) or (actor.states[min(actor.states)] if actor.states else ())


def play_all(feedback: SFeedback, built: list[SBuilt], state: int,
             movement: Callable[[SBuilt], SMovement | None] = lambda _b: None) -> list[CTimeline]:  # fmt: skip
    """Every built actor's timeline in ``state``, worked out together: a barrier opens when its arrivals reach one
    per actor and barrier element of that id in its item (``SActor.barriers``), all who wait on it then go on; one
    that never opens holds its actors to the horizon (no timeout)."""
    players, scripts, required, reachable = [], [], Counter(), Counter()
    for built_actor in built:
        actor = feedback.actors[built_actor.item]
        required.update(actor.barriers)
        own: Counter = Counter()
        for loop in _loops(actor, state):
            barriers_in(loop.elements, own)
        reachable.update(set(own))  # an actor waits at one barrier at a time
        player = _CPlayer(feedback, built_actor.seed, movement(built_actor), actor.scale_speed)
        players.append(player)
        scripts.append(player.script(_loops(actor, state), built_actor.number))
    waiting: dict[int, tuple[int, float]] = {}
    arrived: Counter = Counter()
    runnable, sent = deque(range(len(scripts))), {}
    while runnable:
        actor_index = runnable.popleft()
        try:
            barrier, arrival = scripts[actor_index].send(sent.pop(actor_index, None))
        except StopIteration:
            continue
        waiting[actor_index] = (barrier, arrival)
        arrived[barrier] += 1
        if arrived[barrier] >= required[barrier]:
            who = [j for j, (barrier_id, _arrival) in waiting.items() if barrier_id == barrier]
            opens = max(waiting[j][1] for j in who)
            for j in who:
                del waiting[j]
                sent[j] = opens
                runnable.append(j)
            arrived[barrier] = 0
    for actor_index in list(waiting):  # held for good: they stand to the horizon
        try:
            scripts[actor_index].send(HORIZON)
        except StopIteration:
            pass
    out = [CTimeline(player.m_out, player.m_fades) for player in players]
    for actor_index, (
        barrier,
        _arrival,
    ) in waiting.items():  # held: fewer actors can wait there than it needs (else the horizon)
        out[actor_index].m_held = reachable[barrier] < required[barrier]
    return out


def play(feedback: SFeedback, actor: SActor, state: int, seed: int = 0, cycle=None,
         movement: SMovement | None = None) -> CTimeline:  # fmt: skip
    """One actor's timeline alone in ``state`` (its barriers open by its own arrivals); ``cycle(sequence)`` the
    seconds one play of a sequence takes (default: ``movement``'s, else 1)."""
    player = _CPlayer(feedback, seed, movement, actor.scale_speed, cycle)
    required = actor.barriers
    script, arrived, send, held = player.script(_loops(actor, state)), Counter(), None, False
    while True:
        try:
            barrier, arrival = script.send(send)
        except StopIteration:
            break
        arrived[barrier] += 1
        opens = arrived[barrier] >= required[barrier]
        send, held = (arrival, held) if opens else (HORIZON, True)  # alone, a barrier not opened by it never opens
        arrived[barrier] = 0 if opens else arrived[barrier]
    out = CTimeline(player.m_out, player.m_fades)
    out.m_held = held
    return out
