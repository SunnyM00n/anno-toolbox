"""Where a built island's rivers run: 2070's river pieces joined into 117 rivers, the water levels (pools, falls,
rapids), the mouths into the sea and the joins with lakes, the ribbons cut back at falls, and the smoothed course the
ground and the splines follow (island-rivers "Rivers"). The look per point is ``riverlook``'s."""

from __future__ import annotations

import math
import struct

from .islandgrid import dist_to_polygon, point_in_polygon
from .riverlook import read_point

RIVER_JOIN = 2.5  # tiles: 2070 river pieces whose ends meet within this are one 117 river
RIVER_STEP = 0.25  # a drop of the water this big from one 2070 point to the next is a step: kept sharp
RIVER_STEEP = 1.0  # drop per tile of run: a step this steep is a fall whether or not 2070 hangs one there
RIVER_FALL_REACH = 3.0  # tiles: a river end or step this close to a 2070 fall's top runs into that fall
RIVER_FALL_COAST = 0.5  # a fall where the ground is lower than this is where its river meets the sea
RIVER_SEA_FALL = 0.8  # a river this high over the sea falls into it; lower, it runs out as a mouth (2070 lines
# n_l03's main mouth, 0.45 high, with cascades 0.2 high, which one 117 fall drew as a flat white band)
RIVER_FALL_LIP = 1.5  # tiles: a 2070 fall top this close to the end of a side river is where it falls
RIVER_SIDE_DROP = 0.5  # a side river ending this far above the river it would join keeps its own level (2070 ends it
# in a pool above a fall; lowered 2.6 to the river below, n_l03's drew a wall of water)
RIVER_ESTUARY = 6.0  # tiles from the open sea where a river bed may stay below the sea level
RIVER_MOUTH_SEA = 0.5  # tiles: a mouth's water comes down to the sea's level where its course is this close to
# ground under the sea that joins the open sea; before, 2070's level (RIVER_ESTUARY until 2026-11-06 lowered the
# last 6 tiles, 0.45 to 0.1 on n_l17, and the sea came up the river with its waves; island-rivers "Archived tuning")
RIVER_END_WATER = -0.3  # the water of a mouth's last point, under the sea (vanilla river ends: -0.3..0.3)
RIVER_MOUTH_CUT = -1.0  # world units: the course is in the sea where its bed is this far under the sea level
# (flat lowland beds wander a little under 0 inland: n_l17's whole river lies within 0.2 of it)
# A mouth ends RIVER_MOUTH_REACH tiles past where the course reaches RIVER_MOUTH_CUT, its last RIVER_MOUTH_SINK
# tiles sinking under the sea: further out 117 draws the river's edge lines over open water
RIVER_MOUTH_REACH = 0.5  # tiles
RIVER_MOUTH_SINK = 1.5  # tiles
RIVER_MOUTH_WATER = 0.1  # world height of river water where it meets the sea (vanilla: -0.3..0.3)
# A river joins a lake as vanilla's do: run on LAKE_INLET tiles into it, LAKE_WIDEN times as wide there, its water
# LAKE_RISE over the lake's (vanilla's median 0.05-0.08). LAKE_REACH: an end this close to a lake joins it
LAKE_INLET = 2.0
LAKE_WIDEN = 1.5
LAKE_RISE = 0.05
LAKE_REACH = RIVER_JOIN + 1.0


def is_fall_step(upper, lower, falls=None) -> bool:
    """Whether the water drops as a fall from point ``upper`` to ``lower`` (x, water, z): a step of ``RIVER_STEP`` or more
    with a 2070 fall top (``falls``) within ``RIVER_FALL_REACH``, or as steep as ``RIVER_STEEP``. ``falls`` None:
    every step is one. The other steps are rapids, 2070's pieces handing over at a lower level (210 of the 293
    steps, 0.35 over 1.8 tiles at the median): the ribbon slopes down them."""
    drop = upper[1] - lower[1]
    if drop < RIVER_STEP:
        return False
    if falls is None or drop >= RIVER_STEEP * math.hypot(lower[0] - upper[0], lower[2] - upper[2]):
        return True
    return any(
        min(math.hypot(upper[0] - fall_x, upper[2] - fall_z), math.hypot(lower[0] - fall_x, lower[2] - fall_z))
        <= RIVER_FALL_REACH
        for fall_x, fall_z in falls
    )


# The smoothed course
def smooth_river(points, widths, step_tiles: float, falls=None):
    """Points (x, water, z, width) every ``step`` tiles along a Catmull-Rom curve through the river points (water
    and width interpolated along). At a fall step (``is_fall_step``) the water drops right after the upper point
    instead of sloping down the whole next stretch (which left the falls' lips hanging over the river); a rapid
    slopes as its ribbon does."""
    point_count = len(points)
    samples = []
    for k in range(point_count - 1):
        point_before, point_start, point_end, point_after = (
            points[max(0, k - 1)],
            points[k],
            points[k + 1],
            points[min(point_count - 1, k + 2)],
        )
        segment_length = math.hypot(point_end[0] - point_start[0], point_end[2] - point_start[2])
        step_count = max(1, int(math.ceil(segment_length / step_tiles)))
        is_sharp = is_fall_step(point_start, point_end, falls)
        for j in range(step_count + (1 if k == point_count - 2 else 0)):
            fraction = j / step_count
            fraction_squared, fraction_cubed = fraction * fraction, fraction * fraction * fraction
            position = [
                0.5
                * (
                    2 * point_start[i]
                    + (-point_before[i] + point_end[i]) * fraction
                    + (2 * point_before[i] - 5 * point_start[i] + 4 * point_end[i] - point_after[i]) * fraction_squared
                    + (-point_before[i] + 3 * point_start[i] - 3 * point_end[i] + point_after[i]) * fraction_cubed
                )
                for i in (0, 2)
            ]
            water = (
                point_start[1] + (point_end[1] - point_start[1]) * fraction
                if not is_sharp
                else (point_start[1] if fraction == 0 else point_end[1])
            )
            width = widths[k] + (widths[k + 1] - widths[k]) * fraction
            samples.append((position[0], water, position[1], width))
    return samples


def resample_river(points, widths, spacing_tiles: float, falls=None):
    """The river as points about ``spacing`` tiles apart along its smoothed course, and both sides of every step
    (``RIVER_STEP``); the water never rises downstream (the source is first)."""
    dense = smooth_river(points, widths, 0.25, falls)
    keep = [dense[0]]
    run_length = 0.0
    for start, end in zip(dense, dense[1:], strict=False):
        run_length += math.hypot(end[0] - start[0], end[2] - start[2])
        if start[1] - end[1] >= RIVER_STEP:  # a step: the water above it and below it
            if keep[-1] is not start:
                keep.append(start)
            keep.append(end)
            run_length = 0.0
        elif run_length >= spacing_tiles:
            keep.append(end)
            run_length = 0.0
    if keep[-1] is not dense[-1]:
        keep.append(dense[-1])
    kept_points, kept_widths, lowest_water = [], [], float("inf")
    for x, water, z, width in keep:
        lowest_water = min(lowest_water, water)
        kept_points.append((x, lowest_water, z))
        kept_widths.append(width)
    return kept_points, kept_widths


# Lakes and mouths
def nearest_on_polygon(x: float, z: float, outline) -> tuple[float, float]:
    best_distance, nearest = float("inf"), outline[0]
    for (start_x, start_z), (end_x, end_z) in zip(outline, outline[1:] + outline[:1], strict=True):
        delta_x, delta_z = end_x - start_x, end_z - start_z
        along = max(
            0.0,
            min(
                1.0,
                ((x - start_x) * delta_x + (z - start_z) * delta_z) / (delta_x * delta_x + delta_z * delta_z or 1e-9),
            ),
        )
        nearest_x, nearest_z = start_x + along * delta_x, start_z + along * delta_z
        if (distance := math.hypot(x - nearest_x, z - nearest_z)) < best_distance:
            best_distance, nearest = distance, (nearest_x, nearest_z)
    return nearest


def into_lakes(points, widths, outlines, stays=None):
    """A river that leaves or enters a lake, run on ``LAKE_INLET`` tiles into it and widened there by
    ``LAKE_WIDEN``, as vanilla's do; cut at the outline, its end lay on the lake as a straight cut (in game,
    2026-10-23). The stretch further inside is dropped. An end within ``LAKE_REACH`` of a lake but outside it is run
    on towards it, unless ``stays(point)``: an end at a 2070 fall, which the fall joins to the lake. Points are (x, z,
    water); returns (pts, widths, head, tail): the points at each end that lie in the lake."""

    def inside(point) -> bool:
        return any(point_in_polygon(point[0], point[1], outline) for outline in outlines)

    def crossing(
        from_point, to_point, from_width, to_width
    ):  # the outline between a (one side) and b (the other), by halving
        low_fraction, high_fraction = 0.0, 1.0
        for _ in range(20):
            fraction = (low_fraction + high_fraction) / 2
            point = tuple(from_point[i] + (to_point[i] - from_point[i]) * fraction for i in range(len(from_point)))
            low_fraction, high_fraction = (
                (fraction, high_fraction) if inside(point) == inside(from_point) else (low_fraction, fraction)
            )
        fraction = (low_fraction + high_fraction) / 2
        return tuple(
            from_point[i] + (to_point[i] - from_point[i]) * fraction for i in range(len(from_point))
        ), from_width + (to_width - from_width) * fraction

    def inlet(at, toward, width):  # the points from the shore ``at`` into the lake, towards ``toward``
        delta_x, delta_z = toward[0] - at[0], toward[1] - at[1]
        length = math.hypot(delta_x, delta_z) or 1.0
        reach = LAKE_INLET
        for _ in range(5):  # a narrow lake: shorter
            inlet_points = (at[0] + delta_x / length * reach, at[1] + delta_z / length * reach, at[2])
            if inside(inlet_points):
                return [inlet_points], [width * LAKE_WIDEN]
            reach /= 2
        return [], []

    def end(point, width):  # head of a river whose first point is ``p``: the points to put before it
        if not outlines or inside(point) or (stays is not None and stays(point)):
            return [], []
        nearest_outline = min(outlines, key=lambda outline: dist_to_polygon(point[0], point[1], outline))
        if dist_to_polygon(point[0], point[1], nearest_outline) > LAKE_REACH:
            return [], []
        shore_x, shore_z = nearest_on_polygon(point[0], point[1], nearest_outline)
        delta_x, delta_z = shore_x - point[0], shore_z - point[1]
        length = math.hypot(delta_x, delta_z) or 1.0
        shore = (
            shore_x + delta_x / length * 0.05,
            shore_z + delta_z / length * 0.05,
            point[2],
        )  # just inside the outline
        inlet_points, inlet_widths = inlet(shore, (shore[0] + delta_x, shore[1] + delta_z), width)
        return (inlet_points + [shore], inlet_widths + [width]) if inlet_points else ([], [])

    outside_indices = [i for i, point in enumerate(points) if not inside(point)]
    if not outside_indices:
        return list(points), list(widths), 0, 0
    first, last = outside_indices[0], outside_indices[-1]
    new_points, new_widths = list(points[first : last + 1]), list(widths[first : last + 1])
    head = tail = 0
    if first > 0:
        point, width = crossing(points[first], points[first - 1], widths[first], widths[first - 1])
        inlet_points, inlet_widths = inlet(point, points[first - 1], width)
        new_points[:0], new_widths[:0], head = inlet_points + [point], inlet_widths + [width], 1 + len(inlet_points)
    else:
        inlet_points, inlet_widths = end(new_points[0], new_widths[0])
        new_points[:0], new_widths[:0], head = inlet_points, inlet_widths, len(inlet_points)
    if last < len(points) - 1:
        point, width = crossing(points[last], points[last + 1], widths[last], widths[last + 1])
        inlet_points, inlet_widths = inlet(point, points[last + 1], width)
        new_points += [point] + inlet_points
        new_widths += [width] + inlet_widths
        tail = 1 + len(inlet_points)
    else:
        inlet_points, inlet_widths = end(new_points[-1], new_widths[-1])
        new_points += inlet_points[::-1]
        new_widths += inlet_widths[::-1]
        tail = len(inlet_points)
    return new_points, new_widths, head, tail


def _steps(total: float, step: float):
    offset = 0.0
    while offset < total:
        yield offset
        offset += step
    yield total


def river_mouth(points, widths, water, ground, head: int = 0, sea=None):
    """A river that runs into the sea, ended ``RIVER_MOUTH_REACH`` tiles past the first place along its course where
    ``ground(x, z)`` is under ``RIVER_MOUTH_CUT`` and sinking to ``RIVER_END_WATER`` over its last ``RIVER_MOUTH_SINK``
    tiles; where 2070's river stops short of that it is run on along its last stretch. ``head``: the points the river
    runs in a lake it leaves, whose bed is no sea; ``sea(x, z)``: whether a point lies by the open sea (default: over
    ground below 0). Returns (pts, widths, water)."""
    segment_lengths = [
        math.hypot(to_point[0] - from_point[0], to_point[1] - from_point[1])
        for from_point, to_point in zip(points, points[1:], strict=False)
    ]
    arc_starts = [0.0]
    for length in segment_lengths:
        arc_starts.append(arc_starts[-1] + length)

    def at(arc_length):  # x, z, width, water at arc length s; past the end along the last stretch
        k = next(
            (i for i, segment_end in enumerate(arc_starts[1:-1]) if arc_length <= segment_end), len(segment_lengths) - 1
        )
        fraction = (arc_length - arc_starts[k]) / (segment_lengths[k] or 1.0)
        clamped_fraction = min(fraction, 1.0)
        from_point, to_point = points[k], points[k + 1]
        return (
            from_point[0] + (to_point[0] - from_point[0]) * fraction,
            from_point[1] + (to_point[1] - from_point[1]) * fraction,
            widths[k] + (widths[k + 1] - widths[k]) * clamped_fraction,
            water[k] + (water[k + 1] - water[k]) * clamped_fraction,
        )

    shore = arc_starts[min(max(head - 1, 0), len(arc_starts) - 1)]
    deep_length = next(
        (
            arc_length
            for arc_length in _steps(arc_starts[-1], 0.25)
            if arc_length > shore and ground(*at(arc_length)[:2]) < RIVER_MOUTH_CUT
        ),
        arc_starts[-1],
    )
    mouth_length = deep_length + RIVER_MOUTH_REACH
    sink_length = max(mouth_length - RIVER_MOUTH_SINK, 0.25)
    keep = [i for i, arc_length in enumerate(arc_starts) if i == 0 or arc_length < sink_length - 0.5]
    kept_points = [points[i] for i in keep]
    kept_widths = [widths[i] for i in keep]
    # down to the mouth's level only where the course runs by the sea from there on: every point over ground below
    # 0 took it, and a lowland river (n_m05: 75 tiles over ground of -0.4 to 0.25) zig-zagged between its own level
    # and the mouth's, foaming all the way (in game, 2026-10-23)
    sea = sea or (lambda x, z: ground(x, z) < 0)
    wet = len(keep)
    while wet > max(head, 1) and sea(points[keep[wet - 1]][0], points[keep[wet - 1]][1]):
        wet -= 1
    kept_water = [min(water[i], RIVER_MOUTH_WATER) if k >= wet else water[i] for k, i in enumerate(keep)]
    x, z, width, level = at(sink_length)
    kept_points.append((x, z, min(level, RIVER_MOUTH_WATER)))
    kept_widths.append(width)
    kept_water.append(min(level, RIVER_MOUTH_WATER))
    x, z, width, _ = at(mouth_length)
    kept_points.append((x, z, RIVER_END_WATER))
    kept_widths.append(width)
    kept_water.append(RIVER_END_WATER)
    return kept_points, kept_widths, kept_water


# The network, from 2070's pieces
def at_fall(first_end, second_end, falls) -> bool:
    """Whether two piece ends (x, z, water) are the lip and the pool of a 2070 waterfall: their water a step
    (``RIVER_STEP``) apart and a fall top (``falls``) within ``RIVER_FALL_REACH`` of either."""
    return abs(first_end[2] - second_end[2]) >= RIVER_STEP and any(
        min(
            math.hypot(first_end[0] - fall_x, first_end[1] - fall_z),
            math.hypot(second_end[0] - fall_x, second_end[1] - fall_z),
        )
        <= RIVER_FALL_REACH
        for fall_x, fall_z in falls
    )


def _join_pieces(pieces: list, falls=(), ends: dict | None = None) -> list:
    """Join pieces whose ends meet within ``RIVER_JOIN`` and touch no third piece at their level (no branch point; a
    pool below a fall is none: two pieces of one pool that end at its lip are one ribbon, not two overlapping); in
    place. Ends
    that meet at a 2070 fall (``falls``: its tops; ``at_fall``) stay apart, as 2070 has them: the upper river ends at
    the lip, the lower starts in the pool, and the fall's segments carry the water between (joined, a steep ribbon ran
    from the lip to the pool through the rock models, island-rivers "Falls between pieces"); ``ends`` gets each such
    end, (x, z) -> "lip" or "pool"."""
    near = lambda point_a, point_b: math.hypot(point_a[0] - point_b[0], point_a[1] - point_b[1]) <= RIVER_JOIN  # noqa: E731

    def ends_meeting(i, j):
        (first_points, _), (second_points, _) = pieces[i], pieces[j]
        for is_first_reversed in (False, True):
            for is_second_reversed in (False, True):
                first_end = first_points[0] if is_first_reversed else first_points[-1]
                second_start = second_points[-1] if is_second_reversed else second_points[0]
                if near(first_end, second_start) and falls and at_fall(first_end, second_start, falls):
                    if ends is not None:
                        lip, pool = (
                            (first_end, second_start) if first_end[2] > second_start[2] else (second_start, first_end)
                        )
                        ends[lip[:2]], ends[pool[:2]] = "lip", "pool"
                    continue
                if near(first_end, second_start):
                    return is_first_reversed, is_second_reversed
        return None

    def degree(
        end_point, skip
    ):  # the other pieces at this end and its level: a pool below a fall is no branch of the lip
        level = [
            other_end
            for k, (piece_points, _) in enumerate(pieces)
            if k not in skip
            for other_end in (piece_points[0], piece_points[-1])
            if near(end_point, other_end)
        ]
        return sum(1 for other_end in level if not (falls and at_fall(end_point, other_end, falls)))

    merged = True
    while merged:
        merged = False
        for i in range(len(pieces)):
            for j in range(len(pieces)):
                if i == j:
                    continue
                meeting = ends_meeting(i, j)
                if meeting is None:
                    continue
                is_first_reversed, is_second_reversed = meeting
                first_points, first_widths = pieces[i]
                second_points, second_widths = pieces[j]
                first_points, first_widths = (
                    (first_points[::-1], first_widths[::-1]) if is_first_reversed else (first_points, first_widths)
                )
                second_points, second_widths = (
                    (second_points[::-1], second_widths[::-1]) if is_second_reversed else (second_points, second_widths)
                )
                if degree(first_points[-1], {i, j}):
                    continue
                pieces[i] = (first_points + second_points[1:], first_widths + second_widths[1:])
                del pieces[j]
                merged = True
                break
            if merged:
                break
    return pieces


def _orient(points, widths, ground, lake_at, fall_ends: dict | None = None) -> tuple:
    """A river with its source first: an end over the sea is the mouth; else the higher water is the source (a
    river falling into a lake, taken to leave it, was held at the lake's level all along: n_l16, 8 under 2070's
    water, in game 2026-10-23); else a fall's lip is the mouth and its pool the source (``fall_ends``, from
    ``_join_pieces``); else a lake end; else the higher ground."""
    ground_start, ground_end = ground(points[0]), ground(points[-1])
    lake_start, lake_end = lake_at(points[0]), lake_at(points[-1])
    water_start = (
        lake_start if lake_start is not None else points[0][2]
    )  # the water at each end: a lake's level, else 2070's
    water_end = lake_end if lake_end is not None else points[-1][2]
    fall_start, fall_end = ((fall_ends or {}).get(tuple(point[:2])) for point in (points[0], points[-1]))
    if ground_start < 0 <= ground_end:
        is_reversed = True
    elif ground_end < 0 <= ground_start:
        is_reversed = False
    elif abs(water_start - water_end) > RIVER_STEP:
        is_reversed = water_start < water_end
    elif fall_start or fall_end:
        is_reversed = fall_start == "lip" or fall_end == "pool"
    elif (lake_start is None) != (lake_end is None):
        is_reversed = lake_end is not None
    else:
        is_reversed = ground_start < ground_end
    return (points[::-1], widths[::-1]) if is_reversed else (points, widths)


def _downstream_order(oriented: list) -> tuple[list[int], dict[int, int | None]]:
    """The rivers after the river they flow into (a side river's mouth lies on another's course within
    ``RIVER_JOIN``; a side river of a side river comes last); cycles give up ordering."""

    def mouth_on(k):
        end = oriented[k][0][-1]
        for j, (points, _) in enumerate(oriented):
            if j != k and min(math.hypot(end[0] - x, end[1] - z) for x, z, *_ in points) <= RIVER_JOIN:
                return j
        return None

    joins = {k: mouth_on(k) for k in range(len(oriented))}
    order: list[int] = []
    while len(order) < len(oriented):
        ready = [k for k in range(len(oriented)) if k not in order and (joins[k] is None or joins[k] in order)]
        order.extend(ready or [k for k in range(len(oriented)) if k not in order])
    return order, joins


def _water_levels(points, in_lake: tuple[int, int], lake_at) -> list[float]:
    """A river's water per point: 2070's levels (flat pools per piece, at least ``RIVER_MOUTH_WATER``), never lower
    than the water after it, then never higher than before it; ``LAKE_RISE`` over a lake it leaves or runs into."""
    water = [max(point[2], RIVER_MOUTH_WATER) for point in points]
    head, tail = in_lake
    for i in range(len(water) - 2, -1, -1):
        water[i] = max(water[i], water[i + 1])
    level = lake_at(points[0])
    if level is not None and level >= water[min(head, len(water) - 1)] - RIVER_STEP:  # leaves the lake
        water[: max(head, 1)] = [level + LAKE_RISE] * max(head, 1)
    for i in range(1, len(water)):
        water[i] = min(water[i], water[i - 1])
    level = lake_at(points[-1])
    if level is not None and level <= water[0]:  # runs into a lake: ends just over it, never under it
        water = [max(value, level + LAKE_RISE) for value in water]
        if tail:
            water[-tail:] = [level + LAKE_RISE] * tail
    return water


def _river_end(points, widths, water, head: int, ground, falls, by_sea):
    """Where a river over the sea ends: at its fall, as high as 2070 has it (``RIVER_SEA_FALL``; the sea floor inside
    its banks is raised later), else as a mouth sinking under the sea (``river_mouth``). ``falls``: 2070's fall tops;
    ``by_sea(x, z)``: whether a point lies by the open sea, None before the beds measured it."""
    fall_indices = [  # a fall with nothing but sea after it
        i
        for i, point in enumerate(points)
        if ground(point) < RIVER_FALL_COAST
        and water[i] >= RIVER_SEA_FALL
        and any(math.hypot(point[0] - fall_x, point[1] - fall_z) <= RIVER_JOIN for fall_x, fall_z in falls)
        and all(ground(later_point) < 0 for later_point in points[i + 1 :])
    ]
    if fall_indices and ground(points[-1]) < 0:
        fall_x, fall_z = min(
            falls,
            key=lambda fall: math.hypot(points[fall_indices[0]][0] - fall[0], points[fall_indices[0]][1] - fall[1]),
        )
        cut = min(fall_indices, key=lambda i: math.hypot(points[i][0] - fall_x, points[i][1] - fall_z))
        return points[: cut + 1], widths[: cut + 1], water[: cut + 1]
    if ground(points[-1]) < 0:
        return river_mouth(points, widths, water, lambda x, z: ground((x, z)), head, by_sea)
    return points, widths, water


CUT_STEP = 0.1  # tiles: how finely a pool's start is moved out of the ribbon above it
CUT_MAX = 0.5  # of a river's length, at most, is cut from its start (a pool wholly under the river above stays)


def _outside(centre, half_width: float, normal, ribbon) -> bool:
    """Whether a cross-section (centre ``pt`` (x, z), ``half`` wide each side along ``normal``) lies clear of a
    ribbon (``smooth_river`` samples (x, water, z, width))."""
    for fraction in (-1.0, -0.5, 0.0, 0.5, 1.0):
        x, z = centre[0] + normal[0] * half_width * fraction, centre[1] + normal[1] * half_width * fraction
        if any(
            math.hypot(x - sample_x, z - sample_z) < sample_width / 2 for sample_x, _, sample_z, sample_width in ribbon
        ):
            return False
    return True


def cut_back_at_falls(rivers: list) -> list[float]:
    """Cut the start of every river that begins under the end of a river a step (``RIVER_STEP``) higher - the pool
    below a fall under its lip - back to where its first cross-section clears the upper ribbon: 117 draws both
    ribbons, unsorted by depth, and two waters over one pool showed as a lighter rectangle (in game 2026-11-06); 2070
    faded its overlapping pieces apart. ``rivers``: (points (x, water, z), widths), in place. Returns the cuts (tiles)."""
    cuts = []
    for index, (points, widths) in enumerate(rivers):
        above = [
            smooth_river(upper_points, upper_widths, 0.25)
            for j, (upper_points, upper_widths) in enumerate(rivers)
            if j != index
            and upper_points[-1][1] - points[0][1] >= RIVER_STEP
            and math.hypot(upper_points[-1][0] - points[0][0], upper_points[-1][2] - points[0][2])
            <= RIVER_JOIN + (upper_widths[-1] + widths[0]) / 2
        ]
        if not above or len(points) < 2:
            continue
        course = smooth_river(points, widths, CUT_STEP)
        total = sum(
            math.hypot(end[0] - start[0], end[2] - start[2]) for start, end in zip(course, course[1:], strict=False)
        )
        run, sample_index = 0.0, 0
        while sample_index < len(course) - 1 and run <= CUT_MAX * total:
            (start_x, _, start_z, start_width), (end_x, _, end_z, _) = course[sample_index], course[sample_index + 1]
            length = math.hypot(end_x - start_x, end_z - start_z) or 1e-9
            normal = (-(end_z - start_z) / length, (end_x - start_x) / length)
            if all(_outside((start_x, start_z), start_width / 2, normal, upper_ribbon) for upper_ribbon in above):
                break
            run += length
            sample_index += 1
        if sample_index == 0 or run > CUT_MAX * total:
            continue
        x, _, z, width = course[sample_index]
        keep = [i for i, point in enumerate(points) if _along(points, i) > run + CUT_STEP]
        rivers[index] = ([(x, points[0][1], z), *(points[i] for i in keep)], [width, *(widths[i] for i in keep)])
        cuts.append(round(run, 2))
    return cuts


def _along(points, i: int) -> float:
    """Tiles along a river's points to its ``i``-th."""
    return sum(
        math.hypot(end[0] - start[0], end[2] - start[2])
        for start, end in zip(points[:i], points[1 : i + 1], strict=False)
    )


class CRiverCourseMixin:
    """The methods of ``CIslandBuilder`` that read 2070's rivers and decide where 117's run."""

    def _river_pieces(self):
        """2070's river pieces: (points (x, z, water) in the builder's tiles, widths)."""
        for points, fields in self._river_piece_fields():
            yield points, [point.w for point in fields]

    def _river_piece_fields(self):
        """2070's river pieces with every point's fields (``riverlook.SFields``: w, b, d, s, 2070's defaults where a
        point lacks one): (points (x, z, water) in the builder's tiles, fields)."""
        offset_x, offset_z = self.pad_x, self.pad_z
        rivers = self.island.m_root.path("Rivers")
        for item in rivers.children if rivers else []:
            points, fields = [], []
            for point_node in item.path("m_Points").children if item.path("m_Points") else []:
                if point_node.path("p") is None or point_node.path("p").data is None:
                    continue
                x, height, z = struct.unpack("<3f", point_node.path("p").data)
                points.append((x + offset_x, z + offset_z, height))
                fields.append(read_point(point_node))
            if len(points) >= 2:
                yield points, fields

    def _river_network(self):
        """117 rivers from 2070's river pieces (island-rivers "Rivers"): the pieces joined, each river from its
        source to its mouth, its water 2070's levels per piece (the steps become falls or rapids), never rising
        downstream and never lower than the water after it (a single low 2070 point held a whole river in a pit:
        n_l09, in game 2026-10-23); a lake end starts at the lake level, a river into a lake ends just over it, a
        side river ends at most at the level of the river it joins. Returns (points (x, water, z), widths)."""
        fall_ends: dict = {}  # the ends kept apart at 2070's falls (pieces mode, ``has_split_falls``)
        split = self.falls if getattr(self, "has_split_falls", False) else ()
        pieces = _join_pieces(
            [(list(piece_points), list(piece_widths)) for piece_points, piece_widths in self._river_pieces()],
            split,
            fall_ends,
        )
        lakes = [(outline, level) for (_, outline), level in zip(self._lake_polygons(), self.lake_levels, strict=False)]

        def lake_at(point):
            for outline, level in lakes:
                if (
                    point_in_polygon(point[0], point[1], outline)
                    or dist_to_polygon(point[0], point[1], outline) <= RIVER_JOIN + 1
                ):
                    return level
            return None

        def ground(point, radius=0.0):
            return self._ground(point[0], point[1], radius)

        def stays(point):  # an end at a 2070 fall: the fall, not the river, joins it to a lake beside it
            return tuple(point[:2]) in fall_ends

        def lake_end(point):
            return None if stays(point) else lake_at(point)

        oriented, in_lake = [], []
        for points, widths in pieces:
            points, widths = _orient(points, widths, ground, lake_end, fall_ends)
            *river, head, tail = into_lakes(points, widths, [outline for outline, _ in lakes], stays)
            oriented.append(tuple(river))
            in_lake.append((head, tail))
        order, joins = _downstream_order(oriented)
        by_sea = None  # at the sea: over ground under it that joins the open sea (None before the beds measured it)
        mouth_sea = getattr(self, "mouth_sea_distance", None)
        if "mouth-estuary" in getattr(self, "archived", ()) and getattr(self, "sea_distance", None) is not None:
            by_sea = lambda x, z: self._sea_distance_at(x, z) <= RIVER_ESTUARY  # noqa: E731
        elif mouth_sea is not None:
            corner_count = mouth_sea.shape[0]
            by_sea = lambda x, z: (  # noqa: E731
                mouth_sea[min(corner_count - 1, max(0, round(z))), min(corner_count - 1, max(0, round(x)))]
                <= RIVER_MOUTH_SEA
            )
        done: dict[int, list[float]] = {}
        result = []
        left_out = []  # the pieces that make no river, by where they start
        for k in order:
            points, widths = oriented[k]
            water = _water_levels(points, in_lake[k], lake_end)
            j = joins[k]
            # a side river that ends at a 2070 fall keeps its level to the lip and the waterfall takes it down
            falls_in = fall_ends.get(tuple(points[-1][:2])) == "lip" or any(
                math.hypot(points[-1][0] - fall_x, points[-1][1] - fall_z) <= RIVER_FALL_LIP
                for fall_x, fall_z in self.falls
            )
            if j is not None and j in done and not falls_in:
                joined_points, joined_levels = oriented[j][0], done[j]
                end = points[-1]
                junction = min(
                    range(len(joined_points)),
                    key=lambda i: math.hypot(end[0] - joined_points[i][0], end[1] - joined_points[i][1]),
                )
                if (
                    water[-1] - joined_levels[junction] < RIVER_SIDE_DROP
                ):  # its own water, ending at most at the junction
                    water[-1] = min(water[-1], joined_levels[junction])
            points, widths, water = _river_end(points, widths, water, in_lake[k][0], ground, self.falls, by_sea)
            if len(points) < 2:  # a river whose fall hangs at its very first point is no course (n_l17, n_l34)
                left_out.append(f"({oriented[k][0][0][0]:.0f}, {oriented[k][0][0][1]:.0f}) ends at its fall")
                continue
            done[k] = water
            oriented[k] = (points, widths)
            result.append(
                ([(x, water_value, z) for (x, z, _), water_value in zip(points, water, strict=False)], widths)
            )
        cut = cut_back_at_falls(result) if split else []
        self.report["rivers"] = [len(piece_points) for piece_points, _ in result]
        if left_out:
            self.report["river pieces left out"] = left_out
        if cut:
            self.report["river starts cut back at falls"] = cut
        return result

    def _steepen_falls(self, slope_deg: float) -> int:
        """Make each river step at a 2070 fall ``slope_deg`` degrees steep, its run no longer than ``FALL_RUN_MAX`` (the
        sheet over it hangs within its lip zone): where its run is longer, the lower water starts that run on and
        the old end follows level. Returns the steps."""
        from .falls.fallrules import FALL_RUN_MAX

        tangent = math.tan(math.radians(slope_deg))
        steepened = 0
        for index, (points, widths) in enumerate(self.rivers):
            points, widths, k = list(points), list(widths), 0
            while k < len(points) - 1:
                (start_x, start_water, start_z), (end_x, end_water, end_z) = points[k], points[k + 1]
                run = math.hypot(end_x - start_x, end_z - start_z)
                want = min((start_water - end_water) / tangent, FALL_RUN_MAX)
                at_fall = any(
                    math.hypot(start_x - fall_x, start_z - fall_z) <= RIVER_FALL_REACH for fall_x, fall_z in self.falls
                )
                if start_water - end_water >= RIVER_STEP and at_fall and run > want + 0.02:
                    fraction = want / run
                    points.insert(
                        k + 1,
                        (start_x + (end_x - start_x) * fraction, end_water, start_z + (end_z - start_z) * fraction),
                    )
                    widths.insert(k + 1, widths[k] + (widths[k + 1] - widths[k]) * fraction)
                    steepened += 1
                    k += 1
                k += 1
            self.rivers[index] = (points, widths)
        return steepened
