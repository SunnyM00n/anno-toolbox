"""The rules of the island's placed objects that draw a model: a building the edits moved stands on its build grid,
turned in its game's steps and on the ground its asset asks for, no two footprints overlap, and what the editor could
not draw is listed with the reason.

``check(session)`` returns ``(level, message, place)`` rows that ``islandrules.check`` turns into findings;
``check_place`` is the Place tool's live check of a building about to be placed. Footprints are ``island["footprints"]``,
the ground an asset asks for ``island["terrain"]``; only edited objects are held to the rules, every footprint to the
overlap. Rules and sources: island-editor "Placed objects".
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from ..infra.i18n import N_, alternatives, tr
from . import assetmodels, buildground, footprints
from .editsession import EditSession, Entry, game_of, label_of
from .placeables import turn_step

Row = tuple[str, str, tuple[float, float, float] | None]
CELL = 8  # tiles: the buckets footprints are looked up in
SERIOUS = ("cannot be read", "no asset")  # reasons that are a defect, not a model that is not there
SEA = 0.0625  # a tile is land when its ground stands above this (the land bit's rule)
REACH = 16  # tiles: no footprint reaches further from its object
GROUND_NAMES = {"land": N_("land"), "coast": N_("the coast"), "water": N_("water")}


def _edited(session: EditSession, e: Entry) -> bool:
    """Whether the edits placed or changed this object."""
    return e.added is not None or e.key in session.edits["objects"]


def _place(e: Entry) -> tuple[float, float, float]:
    return (float(e.position[0]), float(e.position[1]), float(e.position[2]))


def unresolved(session: EditSession) -> list[Row]:
    """One row per kind of object drawn as a pin for want of a model: how many, why."""
    groups: dict[tuple[str, str], list[Entry]] = defaultdict(list)
    for e in session.entries:
        why = e.base.get("unresolved")
        if why and e.alive and e.visible:
            groups[(why, label_of(e.base) or e.cfg)].append(e)
    rows = []
    for (why, name), found in sorted(groups.items()):
        level = "warning" if any(word in why for word in SERIOUS) else "info"
        message = tr("{n} x {name}: drawn as a pin, {why}", n=len(found), name=name, why=assetmodels.describe(why))
        rows.append((level, message, _place(found[0])))
    return rows


def _footprints(session: EditSession) -> list[tuple[Entry, list]]:
    table = session.island.get("footprints") or {}
    return [
        (e, footprints.world(table[e.cfg], e.position, e.rotation))
        for e in session.entries
        if e.kind == "object" and e.shown and e.cfg in table
    ]


def _turn_problem(rotation, step: str) -> str:
    """Why a building's turn is not one the game builds in ("" when it is)."""
    k = footprints.eighth_of(rotation)
    if k is not None and (step == "eighth" or k % 2 == 0):
        return ""
    deg = math.degrees(-footprints.turn_of(rotation)) % 360  # the view turns the other way round
    deg_text = f"{deg:.1f}"
    if step == "eighth":
        return tr("is turned {deg} degrees: the game turns a building in eighths (45)", deg=deg_text)
    return tr("is turned {deg} degrees: the game turns a building in quarters (90)", deg=deg_text)


def grid(session: EditSession) -> list[Row]:
    """The edited buildings off their build grid, turned off their game's steps or on ground they may not stand on. Once
    the game's asset list says which assets are buildings (``island["buildings"]``), decoration with a footprint is held
    to the ground rule only (the islands turn it freely)."""
    table = session.island.get("footprints") or {}
    step = turn_step(game_of(session.island))
    known = session.island.get("buildings")
    known = set(known) if known is not None else None
    rows = []
    for e in session.entries:
        if e.kind != "object" or not e.shown or e.cfg not in table or not _edited(session, e):
            continue
        name = label_of(e.base) or e.cfg
        building = known is None or str(e.base.get("guid")) in known
        turned = _turn_problem(e.rotation, step) if building else ""
        if turned:
            rows.append(("warning", tr("{name} {problem}", name=name, problem=turned), _place(e)))
        elif building and not footprints.on_grid(table[e.cfg], e.position, e.rotation):
            off = tr("stands off its build grid: its footprint's corners are not on tile corners")
            rows.append(("warning", tr("{name} {problem}", name=name, problem=off), _place(e)))
        polys = footprints.world(table[e.cfg], e.position, e.rotation)
        kinds = _terrain_of(session, e)
        found = [ground_problem(session, polys, kinds)] + (
            buildground.problems(session, polys) if building and "land" in kinds else []
        )
        for problem in filter(None, found):
            rows.append(("warning", tr("{name} {problem}", name=name, problem=problem), _place(e)))
    return rows


def _terrain_of(session: EditSession, e: Entry) -> set[str]:
    table = session.island.get("terrain") or {}
    return set(table.get(str(e.base.get("guid")), ()))


def _tiles(session: EditSession, polys: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """The land bit of the tiles whose centres lie under the footprint, and of the ring of tiles round it."""
    centres, inside, near = footprints.cell_grid(polys, margin=1)
    land = np.array([session.height_at(x, z) > SEA for x, z in centres])
    return land[inside], land[near]


def ground_problem(session: EditSession, polys: list[np.ndarray], kinds: set[str]) -> str:
    """Why a footprint stands on ground its asset does not allow ("" when it may stand there, or the asset names no
    ground): land wants every tile on land, the coast some land with water under or beside it, water no land."""
    if not kinds or not polys:
        return ""
    under, ring = _tiles(session, polys)
    if not len(under):
        return ""
    has_land, has_water = bool(under.any()), bool((~under).any())
    near_water = has_water or bool((~ring).any())
    if ("land" in kinds and not has_water) or ("coast" in kinds and has_land and near_water):
        return ""
    if "water" in kinds and not has_land:
        return ""
    names = [tr(GROUND_NAMES[k]) for k in ("land", "coast", "water") if k in kinds]
    return tr("needs {ground}", ground=alternatives(names))


def check_place(session: EditSession, polygons, position, rotation, kinds=(), skip: Entry | None = None) -> list[str]:
    """What the game's rules say of a building placed at ``position`` turned by ``rotation`` (its footprint
    ``polygons``, ``kinds`` the ground it may stand on): off the island, on the wrong ground or sloping, blocked or rocky
    ground, over another building (``skip``: the entry that is being checked itself, never over itself)."""
    if not polygons:
        return []
    polys = footprints.world(polygons, position, rotation)
    pts = np.concatenate(polys)
    out = []
    if pts.min() < 0 or pts.max() > session.size:
        out.append(tr("off the island"))
    ground = ground_problem(session, polys, set(kinds))
    if ground:
        out.append(ground)
    if "land" in kinds:
        out += buildground.problems(session, polys)
    out += [tr("over {name}", name=name) for name in _overlapping(session, polys, skip)]
    return out


def _overlapping(session: EditSession, polys: list[np.ndarray], skip: Entry | None = None, limit: int = 3) -> list[str]:
    table = session.island.get("footprints") or {}
    pts = np.concatenate(polys)
    centre = pts.mean(axis=0)
    reach = float(np.abs(pts - centre).max()) + REACH
    out = []
    for e in session.entries:
        if e is skip or e.kind != "object" or not e.shown or e.cfg not in table:
            continue
        if abs(e.position[0] - centre[0]) > reach or abs(e.position[2] - centre[1]) > reach:
            continue
        theirs = footprints.world(table[e.cfg], e.position, e.rotation)
        if any(footprints.overlap(a, b) > footprints.TOUCH for a in polys for b in theirs):
            out.append(label_of(e.base) or e.cfg)
            if len(out) >= limit:
                break
    return out


def overlaps(session: EditSession) -> list[Row]:
    """Footprints that reach into each other where at least one of the two was edited."""
    found = _footprints(session)
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (_e, polys) in enumerate(found):
        pts = [p for poly in polys for p in poly]
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        z0, z1 = min(p[1] for p in pts), max(p[1] for p in pts)
        for bx in range(int(x0 // CELL), int(x1 // CELL) + 1):
            for bz in range(int(z0 // CELL), int(z1 // CELL) + 1):
                buckets[(bx, bz)].append(i)
    rows, done = [], set()
    for members in buckets.values():
        for a in members:
            for b in members:
                if a >= b or (a, b) in done:
                    continue
                done.add((a, b))
                (ea, pa), (eb, pb) = found[a], found[b]
                if not (_edited(session, ea) or _edited(session, eb)):
                    continue
                if any(footprints.overlap(x, y) > footprints.TOUCH for x in pa for y in pb):
                    rows.append(
                        (
                            "warning",
                            tr(
                                "{a} and {b} overlap",
                                a=label_of(ea.base) or ea.cfg,
                                b=label_of(eb.base) or eb.cfg,
                            ),
                            _place(ea),
                        )
                    )
    return rows


def check(session: EditSession) -> list[Row]:
    return unresolved(session) + grid(session) + overlaps(session)
