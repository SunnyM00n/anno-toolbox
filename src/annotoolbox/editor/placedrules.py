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
from .editsession import CEditSession, SEntry, game_of, label_of
from .placeables import turn_step

Row = tuple[str, str, tuple[float, float, float] | None]
CELL = 8  # tiles: the buckets footprints are looked up in
SERIOUS = ("cannot be read", "no asset")  # reasons that are a defect, not a model that is not there
SEA = 0.0625  # a tile is land when its ground stands above this (the land bit's rule)
REACH = 16  # tiles: no footprint reaches further from its object
GROUND_NAMES = {"land": N_("islands.land"), "coast": N_("islands.the_coast"), "water": N_("islands.water")}


def _edited(session: CEditSession, entry: SEntry) -> bool:
    """Whether the edits placed or changed this object."""
    return entry.added is not None or entry.key in session.m_edits["objects"]


def _place(entry: SEntry) -> tuple[float, float, float]:
    return (float(entry.position[0]), float(entry.position[1]), float(entry.position[2]))


def unresolved(session: CEditSession) -> list[Row]:
    """One row per kind of object drawn as a pin for want of a model: how many, why."""
    groups: dict[tuple[str, str], list[SEntry]] = defaultdict(list)
    for entry in session.m_entries:
        why = entry.base.get("unresolved")
        if why and entry.alive and entry.visible:
            groups[(why, label_of(entry.base) or entry.cfg)].append(entry)
    rows = []
    for (why, name), found in sorted(groups.items()):
        level = "warning" if any(word in why for word in SERIOUS) else "info"
        message = tr("islands.x_drawn_pin", n=len(found), name=name, why=assetmodels.describe(why))
        rows.append((level, message, _place(found[0])))
    return rows


def _footprints(session: CEditSession) -> list[tuple[SEntry, list]]:
    table = session.m_island.get("footprints") or {}
    return [
        (entry, footprints.world(table[entry.cfg], entry.position, entry.rotation))
        for entry in session.m_entries
        if entry.kind == "object" and entry.shown and entry.cfg in table
    ]


def _turn_problem(rotation, step: str) -> str:
    """Why a building's turn is not one the game builds in ("" when it is)."""
    k = footprints.eighth_of(rotation)
    if k is not None and (step == "eighth" or k % 2 == 0):
        return ""
    deg = math.degrees(-footprints.turn_of(rotation)) % 360  # the view turns the other way round
    deg_text = f"{deg:.1f}"
    if step == "eighth":
        return tr("islands.turned_degrees_game_turns_building", deg=deg_text)
    return tr("islands.turned_degrees_game_turns_building_lower", deg=deg_text)


def grid(session: CEditSession) -> list[Row]:
    """The edited buildings off their build grid, turned off their game's steps or on ground they may not stand on. Once
    the game's asset list says which assets are buildings (``island["buildings"]``), decoration with a footprint is held
    to the ground rule only (the islands turn it freely)."""
    table = session.m_island.get("footprints") or {}
    step = turn_step(game_of(session.m_island))
    known = session.m_island.get("buildings")
    known = set(known) if known is not None else None
    rows = []
    for entry in session.m_entries:
        if entry.kind != "object" or not entry.shown or entry.cfg not in table or not _edited(session, entry):
            continue
        name = label_of(entry.base) or entry.cfg
        building = known is None or str(entry.base.get("guid")) in known
        turned = _turn_problem(entry.rotation, step) if building else ""
        if turned:
            rows.append(("warning", tr("islands.name_problem", name=name, problem=turned), _place(entry)))
        elif building and not footprints.on_grid(table[entry.cfg], entry.position, entry.rotation):
            off_note = tr("islands.stands_off_build_grid_footprints")
            rows.append(("warning", tr("islands.name_problem", name=name, problem=off_note), _place(entry)))
        polys = footprints.world(table[entry.cfg], entry.position, entry.rotation)
        kinds = _terrain_of(session, entry)
        found = [ground_problem(session, polys, kinds)] + (
            buildground.problems(session, polys) if building and "land" in kinds else []
        )
        for problem in filter(None, found):
            rows.append(("warning", tr("islands.name_problem", name=name, problem=problem), _place(entry)))
    return rows


def _terrain_of(session: CEditSession, entry: SEntry) -> set[str]:
    table = session.m_island.get("terrain") or {}
    return set(table.get(str(entry.base.get("guid")), ()))


def _tiles(session: CEditSession, polys: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """The land bit of the tiles whose centres lie under the footprint, and of the ring of tiles round it."""
    centres, inside, near = footprints.cell_grid(polys, margin=1)
    land = np.array([session.height_at(x, z) > SEA for x, z in centres])
    return land[inside], land[near]


def ground_problem(session: CEditSession, polys: list[np.ndarray], kinds: set[str]) -> str:
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
    return tr("islands.needs_ground", ground=alternatives(names))


def check_place(session: CEditSession, polygons, position, rotation, kinds=(), skip: SEntry | None = None) -> list[str]:
    """What the game's rules say of a building placed at ``position`` turned by ``rotation`` (its footprint
    ``polygons``, ``kinds`` the ground it may stand on): off the island, on the wrong ground or sloping, blocked or rocky
    ground, over another building (``skip``: the entry that is being checked itself, never over itself)."""
    if not polygons:
        return []
    polys = footprints.world(polygons, position, rotation)
    points = np.concatenate(polys)
    out = []
    if points.min() < 0 or points.max() > session.m_size_tiles:
        out.append(tr("islands.off_island"))
    ground = ground_problem(session, polys, set(kinds))
    if ground:
        out.append(ground)
    if "land" in kinds:
        out += buildground.problems(session, polys)
    out += [tr("islands.over_name", name=name) for name in _overlapping(session, polys, skip)]
    return out


def _overlapping(
    session: CEditSession, polys: list[np.ndarray], skip: SEntry | None = None, limit: int = 3
) -> list[str]:
    table = session.m_island.get("footprints") or {}
    points = np.concatenate(polys)
    centre = points.mean(axis=0)
    reach = float(np.abs(points - centre).max()) + REACH
    out = []
    for entry in session.m_entries:
        if entry is skip or entry.kind != "object" or not entry.shown or entry.cfg not in table:
            continue
        if abs(entry.position[0] - centre[0]) > reach or abs(entry.position[2] - centre[1]) > reach:
            continue
        theirs = footprints.world(table[entry.cfg], entry.position, entry.rotation)
        if any(
            footprints.overlap(own_polygon, their_polygon) > footprints.TOUCH
            for own_polygon in polys
            for their_polygon in theirs
        ):
            out.append(label_of(entry.base) or entry.cfg)
            if len(out) >= limit:
                break
    return out


def overlaps(session: CEditSession) -> list[Row]:
    """Footprints that reach into each other where at least one of the two was edited."""
    found = _footprints(session)
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (_entry, polys) in enumerate(found):
        points = [point for poly in polys for point in poly]
        min_x, max_x = min(point[0] for point in points), max(point[0] for point in points)
        min_z, max_z = min(point[1] for point in points), max(point[1] for point in points)
        for cell_x in range(int(min_x // CELL), int(max_x // CELL) + 1):
            for cell_z in range(int(min_z // CELL), int(max_z // CELL) + 1):
                buckets[(cell_x, cell_z)].append(i)
    rows, done = [], set()
    for members in buckets.values():
        for first in members:
            for second in members:
                if first >= second or (first, second) in done:
                    continue
                done.add((first, second))
                (first_entry, first_polygons), (second_entry, second_polygons) = found[first], found[second]
                if not (_edited(session, first_entry) or _edited(session, second_entry)):
                    continue
                if any(footprints.overlap(x, y) > footprints.TOUCH for x in first_polygons for y in second_polygons):
                    rows.append(
                        (
                            "warning",
                            tr(
                                "islands.a_and_b_overlap",
                                a=label_of(first_entry.base) or first_entry.cfg,
                                b=label_of(second_entry.base) or second_entry.cfg,
                            ),
                            _place(first_entry),
                        )
                    )
    return rows


def check(session: CEditSession) -> list[Row]:
    return unresolved(session) + grid(session) + overlaps(session)
