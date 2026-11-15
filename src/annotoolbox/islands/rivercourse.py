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


def is_fall_step(a, b, falls=None) -> bool:
    """Whether the water drops as a fall from point ``a`` to ``b`` (x, water, z): a step of ``RIVER_STEP`` or more
    with a 2070 fall top (``falls``) within ``RIVER_FALL_REACH``, or as steep as ``RIVER_STEEP``. ``falls`` None:
    every step is one. The other steps are rapids, 2070's pieces handing over at a lower level (210 of the 293
    steps, 0.35 over 1.8 tiles at the median): the ribbon slopes down them."""
    drop = a[1] - b[1]
    if drop < RIVER_STEP:
        return False
    if falls is None or drop >= RIVER_STEEP * math.hypot(b[0] - a[0], b[2] - a[2]):
        return True
    return any(
        min(math.hypot(a[0] - fx, a[2] - fz), math.hypot(b[0] - fx, b[2] - fz)) <= RIVER_FALL_REACH for fx, fz in falls
    )


# The smoothed course
def smooth_river(pts, widths, step: float, falls=None):
    """Points (x, water, z, width) every ``step`` tiles along a Catmull-Rom curve through the river points (water
    and width interpolated along). At a fall step (``is_fall_step``) the water drops right after the upper point
    instead of sloping down the whole next stretch (which left the falls' lips hanging over the river); a rapid
    slopes as its ribbon does."""
    n = len(pts)
    out = []
    for k in range(n - 1):
        p0, p1, p2, p3 = pts[max(0, k - 1)], pts[k], pts[k + 1], pts[min(n - 1, k + 2)]
        seg = math.hypot(p2[0] - p1[0], p2[2] - p1[2])
        m = max(1, int(math.ceil(seg / step)))
        sharp = is_fall_step(p1, p2, falls)
        for j in range(m + (1 if k == n - 2 else 0)):
            t = j / m
            t2, t3 = t * t, t * t * t
            c = [
                0.5
                * (
                    2 * p1[i]
                    + (-p0[i] + p2[i]) * t
                    + (2 * p0[i] - 5 * p1[i] + 4 * p2[i] - p3[i]) * t2
                    + (-p0[i] + 3 * p1[i] - 3 * p2[i] + p3[i]) * t3
                )
                for i in (0, 2)
            ]
            water = p1[1] + (p2[1] - p1[1]) * t if not sharp else (p1[1] if t == 0 else p2[1])
            w = widths[k] + (widths[k + 1] - widths[k]) * t
            out.append((c[0], water, c[1], w))
    return out


def resample_river(pts, widths, spacing: float, falls=None):
    """The river as points about ``spacing`` tiles apart along its smoothed course, and both sides of every step
    (``RIVER_STEP``); the water never rises downstream (the source is first)."""
    dense = smooth_river(pts, widths, 0.25, falls)
    keep = [dense[0]]
    run = 0.0
    for a, b in zip(dense, dense[1:], strict=False):
        run += math.hypot(b[0] - a[0], b[2] - a[2])
        if a[1] - b[1] >= RIVER_STEP:  # a step: the water above it and below it
            if keep[-1] is not a:
                keep.append(a)
            keep.append(b)
            run = 0.0
        elif run >= spacing:
            keep.append(b)
            run = 0.0
    if keep[-1] is not dense[-1]:
        keep.append(dense[-1])
    out_pts, out_w, low = [], [], float("inf")
    for x, water, z, w in keep:
        low = min(low, water)
        out_pts.append((x, low, z))
        out_w.append(w)
    return out_pts, out_w


# Lakes and mouths
def nearest_on_polygon(x: float, z: float, poly) -> tuple[float, float]:
    best, out = float("inf"), poly[0]
    for (ax, az), (bx, bz) in zip(poly, poly[1:] + poly[:1], strict=True):
        dx, dz = bx - ax, bz - az
        t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz or 1e-9)))
        px, pz = ax + t * dx, az + t * dz
        if (d := math.hypot(x - px, z - pz)) < best:
            best, out = d, (px, pz)
    return out


def into_lakes(pts, widths, polys, stays=None):
    """A river that leaves or enters a lake, run on ``LAKE_INLET`` tiles into it and widened there by
    ``LAKE_WIDEN``, as vanilla's do; cut at the outline, its end lay on the lake as a straight cut (in game,
    2026-10-23). The stretch further inside is dropped. An end within ``LAKE_REACH`` of a lake but outside it is run
    on towards it, unless ``stays(point)``: an end at a 2070 fall, which the fall joins to the lake. Points are (x, z,
    water); returns (pts, widths, head, tail): the points at each end that lie in the lake."""

    def inside(p) -> bool:
        return any(point_in_polygon(p[0], p[1], poly) for poly in polys)

    def crossing(a, b, wa, wb):  # the outline between a (one side) and b (the other), by halving
        lo, hi = 0.0, 1.0
        for _ in range(20):
            t = (lo + hi) / 2
            p = tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a)))
            lo, hi = (t, hi) if inside(p) == inside(a) else (lo, t)
        t = (lo + hi) / 2
        return tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a))), wa + (wb - wa) * t

    def inlet(at, toward, w):  # the points from the shore ``at`` into the lake, towards ``toward``
        dx, dz = toward[0] - at[0], toward[1] - at[1]
        n = math.hypot(dx, dz) or 1.0
        reach = LAKE_INLET
        for _ in range(5):  # a narrow lake: shorter
            q = (at[0] + dx / n * reach, at[1] + dz / n * reach, at[2])
            if inside(q):
                return [q], [w * LAKE_WIDEN]
            reach /= 2
        return [], []

    def end(p, w):  # head of a river whose first point is ``p``: the points to put before it
        if not polys or inside(p) or (stays is not None and stays(p)):
            return [], []
        near = min(polys, key=lambda poly: dist_to_polygon(p[0], p[1], poly))
        if dist_to_polygon(p[0], p[1], near) > LAKE_REACH:
            return [], []
        sx, sz = nearest_on_polygon(p[0], p[1], near)
        dx, dz = sx - p[0], sz - p[1]
        n = math.hypot(dx, dz) or 1.0
        shore = (sx + dx / n * 0.05, sz + dz / n * 0.05, p[2])  # just inside the outline
        q, qw = inlet(shore, (shore[0] + dx, shore[1] + dz), w)
        return (q + [shore], qw + [w]) if q else ([], [])

    out = [i for i, p in enumerate(pts) if not inside(p)]
    if not out:
        return list(pts), list(widths), 0, 0
    first, last = out[0], out[-1]
    new_pts, new_w = list(pts[first : last + 1]), list(widths[first : last + 1])
    head = tail = 0
    if first > 0:
        p, w = crossing(pts[first], pts[first - 1], widths[first], widths[first - 1])
        q, qw = inlet(p, pts[first - 1], w)
        new_pts[:0], new_w[:0], head = q + [p], qw + [w], 1 + len(q)
    else:
        q, qw = end(new_pts[0], new_w[0])
        new_pts[:0], new_w[:0], head = q, qw, len(q)
    if last < len(pts) - 1:
        p, w = crossing(pts[last], pts[last + 1], widths[last], widths[last + 1])
        q, qw = inlet(p, pts[last + 1], w)
        new_pts += [p] + q
        new_w += [w] + qw
        tail = 1 + len(q)
    else:
        q, qw = end(new_pts[-1], new_w[-1])
        new_pts += q[::-1]
        new_w += qw[::-1]
        tail = len(q)
    return new_pts, new_w, head, tail


def _steps(total: float, step: float):
    s = 0.0
    while s < total:
        yield s
        s += step
    yield total


def river_mouth(pts, widths, water, ground, head: int = 0, sea=None):
    """A river that runs into the sea, ended ``RIVER_MOUTH_REACH`` tiles past the first place along its course where
    ``ground(x, z)`` is under ``RIVER_MOUTH_CUT`` and sinking to ``RIVER_END_WATER`` over its last ``RIVER_MOUTH_SINK``
    tiles; where 2070's river stops short of that it is run on along its last stretch. ``head``: the points the river
    runs in a lake it leaves, whose bed is no sea; ``sea(x, z)``: whether a point lies by the open sea (default: over
    ground below 0). Returns (pts, widths, water)."""
    lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:], strict=False)]
    start = [0.0]
    for ln in lens:
        start.append(start[-1] + ln)

    def at(s):  # x, z, width, water at arc length s; past the end along the last stretch
        k = next((i for i, e in enumerate(start[1:-1]) if s <= e), len(lens) - 1)
        t = (s - start[k]) / (lens[k] or 1.0)
        u = min(t, 1.0)
        a, b = pts[k], pts[k + 1]
        return (
            a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            widths[k] + (widths[k + 1] - widths[k]) * u,
            water[k] + (water[k + 1] - water[k]) * u,
        )

    shore = start[min(max(head - 1, 0), len(start) - 1)]
    deep = next((s for s in _steps(start[-1], 0.25) if s > shore and ground(*at(s)[:2]) < RIVER_MOUTH_CUT), start[-1])
    end = deep + RIVER_MOUTH_REACH
    sink = max(end - RIVER_MOUTH_SINK, 0.25)
    keep = [i for i, s in enumerate(start) if i == 0 or s < sink - 0.5]
    out_p = [pts[i] for i in keep]
    out_w = [widths[i] for i in keep]
    # down to the mouth's level only where the course runs by the sea from there on: every point over ground below
    # 0 took it, and a lowland river (n_m05: 75 tiles over ground of -0.4 to 0.25) zig-zagged between its own level
    # and the mouth's, foaming all the way (in game, 2026-10-23)
    sea = sea or (lambda x, z: ground(x, z) < 0)
    wet = len(keep)
    while wet > max(head, 1) and sea(pts[keep[wet - 1]][0], pts[keep[wet - 1]][1]):
        wet -= 1
    out_v = [min(water[i], RIVER_MOUTH_WATER) if k >= wet else water[i] for k, i in enumerate(keep)]
    x, z, w, v = at(sink)
    out_p.append((x, z, min(v, RIVER_MOUTH_WATER)))
    out_w.append(w)
    out_v.append(min(v, RIVER_MOUTH_WATER))
    x, z, w, _ = at(end)
    out_p.append((x, z, RIVER_END_WATER))
    out_w.append(w)
    out_v.append(RIVER_END_WATER)
    return out_p, out_w, out_v


# The network, from 2070's pieces
def at_fall(a, b, falls) -> bool:
    """Whether two piece ends (x, z, water) are the lip and the pool of a 2070 waterfall: their water a step
    (``RIVER_STEP``) apart and a fall top (``falls``) within ``RIVER_FALL_REACH`` of either."""
    return abs(a[2] - b[2]) >= RIVER_STEP and any(
        min(math.hypot(a[0] - fx, a[1] - fz), math.hypot(b[0] - fx, b[1] - fz)) <= RIVER_FALL_REACH for fx, fz in falls
    )


def _join_pieces(pieces: list, falls=(), ends: dict | None = None) -> list:
    """Join pieces whose ends meet within ``RIVER_JOIN`` and touch no third piece at their level (no branch point; a
    pool below a fall is none: two pieces of one pool that end at its lip are one ribbon, not two overlapping); in
    place. Ends
    that meet at a 2070 fall (``falls``: its tops; ``at_fall``) stay apart, as 2070 has them: the upper river ends at
    the lip, the lower starts in the pool, and the fall's segments carry the water between (joined, a steep ribbon ran
    from the lip to the pool through the rock models, island-rivers "Falls between pieces"); ``ends`` gets each such
    end, (x, z) -> "lip" or "pool"."""
    near = lambda a, b: math.hypot(a[0] - b[0], a[1] - b[1]) <= RIVER_JOIN  # noqa: E731

    def ends_meeting(i, j):
        (a, _), (b, _) = pieces[i], pieces[j]
        for ra in (False, True):
            for rb in (False, True):
                ea = a[0] if ra else a[-1]
                sb = b[-1] if rb else b[0]
                if near(ea, sb) and falls and at_fall(ea, sb, falls):
                    if ends is not None:
                        lip, pool = (ea, sb) if ea[2] > sb[2] else (sb, ea)
                        ends[lip[:2]], ends[pool[:2]] = "lip", "pool"
                    continue
                if near(ea, sb):
                    return ra, rb
        return None

    def degree(pt, skip):  # the other pieces at this end and its level: a pool below a fall is no branch of the lip
        level = [e for k, (q, _) in enumerate(pieces) if k not in skip for e in (q[0], q[-1]) if near(pt, e)]
        return sum(1 for e in level if not (falls and at_fall(pt, e, falls)))

    merged = True
    while merged:
        merged = False
        for i in range(len(pieces)):
            for j in range(len(pieces)):
                if i == j:
                    continue
                m = ends_meeting(i, j)
                if m is None:
                    continue
                ra, rb = m
                a, wa = pieces[i]
                b, wb = pieces[j]
                a, wa = (a[::-1], wa[::-1]) if ra else (a, wa)
                b, wb = (b[::-1], wb[::-1]) if rb else (b, wb)
                if degree(a[-1], {i, j}):
                    continue
                pieces[i] = (a + b[1:], wa + wb[1:])
                del pieces[j]
                merged = True
                break
            if merged:
                break
    return pieces


def _orient(pts, widths, ground, lake_at, fall_ends: dict | None = None) -> tuple:
    """A river with its source first: an end over the sea is the mouth; else the higher water is the source (a
    river falling into a lake, taken to leave it, was held at the lake's level all along: n_l16, 8 under 2070's
    water, in game 2026-10-23); else a fall's lip is the mouth and its pool the source (``fall_ends``, from
    ``_join_pieces``); else a lake end; else the higher ground."""
    g0, g1 = ground(pts[0]), ground(pts[-1])
    l0, l1 = lake_at(pts[0]), lake_at(pts[-1])
    e0 = l0 if l0 is not None else pts[0][2]  # the water at each end: a lake's level, else 2070's
    e1 = l1 if l1 is not None else pts[-1][2]
    f0, f1 = ((fall_ends or {}).get(tuple(p[:2])) for p in (pts[0], pts[-1]))
    if g0 < 0 <= g1:
        flip = True
    elif g1 < 0 <= g0:
        flip = False
    elif abs(e0 - e1) > RIVER_STEP:
        flip = e0 < e1
    elif f0 or f1:
        flip = f0 == "lip" or f1 == "pool"
    elif (l0 is None) != (l1 is None):
        flip = l1 is not None
    else:
        flip = g0 < g1
    return (pts[::-1], widths[::-1]) if flip else (pts, widths)


def _downstream_order(oriented: list) -> tuple[list[int], dict[int, int | None]]:
    """The rivers after the river they flow into (a side river's mouth lies on another's course within
    ``RIVER_JOIN``; a side river of a side river comes last); cycles give up ordering."""

    def mouth_on(k):
        end = oriented[k][0][-1]
        for j, (q, _) in enumerate(oriented):
            if j != k and min(math.hypot(end[0] - x, end[1] - z) for x, z, *_ in q) <= RIVER_JOIN:
                return j
        return None

    joins = {k: mouth_on(k) for k in range(len(oriented))}
    order: list[int] = []
    while len(order) < len(oriented):
        ready = [k for k in range(len(oriented)) if k not in order and (joins[k] is None or joins[k] in order)]
        order.extend(ready or [k for k in range(len(oriented)) if k not in order])
    return order, joins


def _water_levels(pts, in_lake: tuple[int, int], lake_at) -> list[float]:
    """A river's water per point: 2070's levels (flat pools per piece, at least ``RIVER_MOUTH_WATER``), never lower
    than the water after it, then never higher than before it; ``LAKE_RISE`` over a lake it leaves or runs into."""
    water = [max(pt[2], RIVER_MOUTH_WATER) for pt in pts]
    head, tail = in_lake
    for i in range(len(water) - 2, -1, -1):
        water[i] = max(water[i], water[i + 1])
    level = lake_at(pts[0])
    if level is not None and level >= water[min(head, len(water) - 1)] - RIVER_STEP:  # leaves the lake
        water[: max(head, 1)] = [level + LAKE_RISE] * max(head, 1)
    for i in range(1, len(water)):
        water[i] = min(water[i], water[i - 1])
    level = lake_at(pts[-1])
    if level is not None and level <= water[0]:  # runs into a lake: ends just over it, never under it
        water = [max(v, level + LAKE_RISE) for v in water]
        if tail:
            water[-tail:] = [level + LAKE_RISE] * tail
    return water


def _river_end(pts, widths, water, head: int, ground, falls, by_sea):
    """Where a river over the sea ends: at its fall, as high as 2070 has it (``RIVER_SEA_FALL``; the sea floor inside
    its banks is raised later), else as a mouth sinking under the sea (``river_mouth``). ``falls``: 2070's fall tops;
    ``by_sea(x, z)``: whether a point lies by the open sea, None before the beds measured it."""
    at_fall = [  # a fall with nothing but sea after it
        i
        for i, pt in enumerate(pts)
        if ground(pt) < RIVER_FALL_COAST
        and water[i] >= RIVER_SEA_FALL
        and any(math.hypot(pt[0] - fx, pt[1] - fz) <= RIVER_JOIN for fx, fz in falls)
        and all(ground(q) < 0 for q in pts[i + 1 :])
    ]
    if at_fall and ground(pts[-1]) < 0:
        fx, fz = min(falls, key=lambda f: math.hypot(pts[at_fall[0]][0] - f[0], pts[at_fall[0]][1] - f[1]))
        cut = min(at_fall, key=lambda i: math.hypot(pts[i][0] - fx, pts[i][1] - fz))
        return pts[: cut + 1], widths[: cut + 1], water[: cut + 1]
    if ground(pts[-1]) < 0:
        return river_mouth(pts, widths, water, lambda x, z: ground((x, z)), head, by_sea)
    return pts, widths, water


CUT_STEP = 0.1  # tiles: how finely a pool's start is moved out of the ribbon above it
CUT_MAX = 0.5  # of a river's length, at most, is cut from its start (a pool wholly under the river above stays)


def _outside(pt, half: float, normal, ribbon) -> bool:
    """Whether a cross-section (centre ``pt`` (x, z), ``half`` wide each side along ``normal``) lies clear of a
    ribbon (``smooth_river`` samples (x, water, z, width))."""
    for t in (-1.0, -0.5, 0.0, 0.5, 1.0):
        x, z = pt[0] + normal[0] * half * t, pt[1] + normal[1] * half * t
        if any(math.hypot(x - sx, z - sz) < sw / 2 for sx, _, sz, sw in ribbon):
            return False
    return True


def cut_back_at_falls(rivers: list) -> list[float]:
    """Cut the start of every river that begins under the end of a river a step (``RIVER_STEP``) higher - the pool
    below a fall under its lip - back to where its first cross-section clears the upper ribbon: 117 draws both
    ribbons, unsorted by depth, and two waters over one pool showed as a lighter rectangle (in game 2026-11-06); 2070
    faded its overlapping pieces apart. ``rivers``: (points (x, water, z), widths), in place. Returns the cuts (tiles)."""
    cuts = []
    for index, (pts, widths) in enumerate(rivers):
        above = [
            smooth_river(q, qw, 0.25)
            for j, (q, qw) in enumerate(rivers)
            if j != index
            and q[-1][1] - pts[0][1] >= RIVER_STEP
            and math.hypot(q[-1][0] - pts[0][0], q[-1][2] - pts[0][2]) <= RIVER_JOIN + (qw[-1] + widths[0]) / 2
        ]
        if not above or len(pts) < 2:
            continue
        course = smooth_river(pts, widths, CUT_STEP)
        total = sum(math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(course, course[1:], strict=False))
        run, k = 0.0, 0
        while k < len(course) - 1 and run <= CUT_MAX * total:
            (ax, _, az, ar), (bx, _, bz, _) = course[k], course[k + 1]
            length = math.hypot(bx - ax, bz - az) or 1e-9
            normal = (-(bz - az) / length, (bx - ax) / length)
            if all(_outside((ax, az), ar / 2, normal, r) for r in above):
                break
            run += length
            k += 1
        if k == 0 or run > CUT_MAX * total:
            continue
        x, _, z, w = course[k]
        keep = [i for i, p in enumerate(pts) if _along(pts, i) > run + CUT_STEP]
        rivers[index] = ([(x, pts[0][1], z), *(pts[i] for i in keep)], [w, *(widths[i] for i in keep)])
        cuts.append(round(run, 2))
    return cuts


def _along(pts, i: int) -> float:
    """Tiles along a river's points to its ``i``-th."""
    return sum(math.hypot(b[0] - a[0], b[2] - a[2]) for a, b in zip(pts[:i], pts[1 : i + 1], strict=False))


class RiverCourseMixin:
    """The methods of ``IslandBuilder`` that read 2070's rivers and decide where 117's run."""

    def _river_pieces(self):
        """2070's river pieces: (points (x, z, water) in the builder's tiles, widths)."""
        for pts, fields in self._river_piece_fields():
            yield pts, [f.w for f in fields]

    def _river_piece_fields(self):
        """2070's river pieces with every point's fields (``riverlook.Fields``: w, b, d, s, 2070's defaults where a
        point lacks one): (points (x, z, water) in the builder's tiles, fields)."""
        px, pz = self.pad_x, self.pad_z
        rivers = self.isl.root.path("Rivers")
        for item in rivers.children if rivers else []:
            pts, fields = [], []
            for pt in item.path("m_Points").children if item.path("m_Points") else []:
                if pt.path("p") is None or pt.path("p").data is None:
                    continue
                x, h, z = struct.unpack("<3f", pt.path("p").data)
                pts.append((x + px, z + pz, h))
                fields.append(read_point(pt))
            if len(pts) >= 2:
                yield pts, fields

    def _river_network(self):
        """117 rivers from 2070's river pieces (island-rivers "Rivers"): the pieces joined, each river from its
        source to its mouth, its water 2070's levels per piece (the steps become falls or rapids), never rising
        downstream and never lower than the water after it (a single low 2070 point held a whole river in a pit:
        n_l09, in game 2026-10-23); a lake end starts at the lake level, a river into a lake ends just over it, a
        side river ends at most at the level of the river it joins. Returns (points (x, water, z), widths)."""
        fall_ends: dict = {}  # the ends kept apart at 2070's falls (pieces mode, ``split_falls``)
        split = self.falls if getattr(self, "split_falls", False) else ()
        pieces = _join_pieces([(list(p), list(w)) for p, w in self._river_pieces()], split, fall_ends)
        lakes = [(poly, level) for (_, poly), level in zip(self._lake_polygons(), self.lake_levels, strict=False)]

        def lake_at(pt):
            for poly, level in lakes:
                if point_in_polygon(pt[0], pt[1], poly) or dist_to_polygon(pt[0], pt[1], poly) <= RIVER_JOIN + 1:
                    return level
            return None

        def ground(pt, radius=0.0):
            return self._ground(pt[0], pt[1], radius)

        def stays(pt):  # an end at a 2070 fall: the fall, not the river, joins it to a lake beside it
            return tuple(pt[:2]) in fall_ends

        def lake_end(pt):
            return None if stays(pt) else lake_at(pt)

        oriented, in_lake = [], []
        for pts, widths in pieces:
            pts, widths = _orient(pts, widths, ground, lake_end, fall_ends)
            *river, head, tail = into_lakes(pts, widths, [poly for poly, _ in lakes], stays)
            oriented.append(tuple(river))
            in_lake.append((head, tail))
        order, joins = _downstream_order(oriented)
        by_sea = None  # at the sea: over ground under it that joins the open sea (None before the beds measured it)
        mouth_sea = getattr(self, "mouth_sea_distance", None)
        if "mouth-estuary" in getattr(self, "archived", ()) and getattr(self, "sea_distance", None) is not None:
            by_sea = lambda x, z: self._sea_distance_at(x, z) <= RIVER_ESTUARY  # noqa: E731
        elif mouth_sea is not None:
            n = mouth_sea.shape[0]
            by_sea = lambda x, z: (  # noqa: E731
                mouth_sea[min(n - 1, max(0, round(z))), min(n - 1, max(0, round(x)))] <= RIVER_MOUTH_SEA
            )
        done: dict[int, list[float]] = {}
        result = []
        left_out = []  # the pieces that make no river, by where they start
        for k in order:
            pts, widths = oriented[k]
            water = _water_levels(pts, in_lake[k], lake_end)
            j = joins[k]
            # a side river that ends at a 2070 fall keeps its level to the lip and the waterfall takes it down
            falls_in = fall_ends.get(tuple(pts[-1][:2])) == "lip" or any(
                math.hypot(pts[-1][0] - fx, pts[-1][1] - fz) <= RIVER_FALL_LIP for fx, fz in self.falls
            )
            if j is not None and j in done and not falls_in:
                q, qw = oriented[j][0], done[j]
                end = pts[-1]
                junction = min(range(len(q)), key=lambda i: math.hypot(end[0] - q[i][0], end[1] - q[i][1]))
                if water[-1] - qw[junction] < RIVER_SIDE_DROP:  # its own water, ending at most at the junction
                    water[-1] = min(water[-1], qw[junction])
            pts, widths, water = _river_end(pts, widths, water, in_lake[k][0], ground, self.falls, by_sea)
            if len(pts) < 2:  # a river whose fall hangs at its very first point is no course (n_l17, n_l34)
                left_out.append(f"({oriented[k][0][0][0]:.0f}, {oriented[k][0][0][1]:.0f}) ends at its fall")
                continue
            done[k] = water
            oriented[k] = (pts, widths)
            result.append(([(x, wv, z) for (x, z, _), wv in zip(pts, water, strict=False)], widths))
        cut = cut_back_at_falls(result) if split else []
        self.report["rivers"] = [len(p) for p, _ in result]
        if left_out:
            self.report["river pieces left out"] = left_out
        if cut:
            self.report["river starts cut back at falls"] = cut
        return result

    def _steepen_falls(self, slope: float) -> int:
        """Make each river step at a 2070 fall ``slope`` degrees steep, its run no longer than ``FALL_RUN_MAX`` (the
        sheet over it hangs within its lip zone): where its run is longer, the lower water starts that run on and
        the old end follows level. Returns the steps."""
        from .falls.fallrules import FALL_RUN_MAX

        tan = math.tan(math.radians(slope))
        steepened = 0
        for index, (pts, widths) in enumerate(self.rivers):
            pts, widths, k = list(pts), list(widths), 0
            while k < len(pts) - 1:
                (ax, aw, az), (bx, bw, bz) = pts[k], pts[k + 1]
                run = math.hypot(bx - ax, bz - az)
                want = min((aw - bw) / tan, FALL_RUN_MAX)
                at_fall = any(math.hypot(ax - fx, az - fz) <= RIVER_FALL_REACH for fx, fz in self.falls)
                if aw - bw >= RIVER_STEP and at_fall and run > want + 0.02:
                    f = want / run
                    pts.insert(k + 1, (ax + (bx - ax) * f, bw, az + (bz - az) * f))
                    widths.insert(k + 1, widths[k] + (widths[k + 1] - widths[k]) * f)
                    steepened += 1
                    k += 1
                k += 1
            self.rivers[index] = (pts, widths)
        return steepened
