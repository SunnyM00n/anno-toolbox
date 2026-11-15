"""The open island and its edits checked against the game's rules, each finding a sentence a mapper can act on.

``check(session, game)`` returns ``Finding`` rows (error: the game drops or breaks it; warning: it loads but likely
not as meant; info: a readout such as the size class); ``check_files`` adds the model files that do not draw;
``report`` turns them into the rows of the Checks tab and a status line. ``readout`` is the island's numbers (grid,
land tiles, size class, active cells). The rules and their sources: island-editor "Full spec"; the units:
island-editor "Ground edits of vanilla islands".
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from ..infra.i18n import N_, number, tr
from ..islands import sessionsettings
from ..suite.extspecs import Check
from . import placedrules
from .editsession import EditSession, Entry, game_of, label_of
from .sessionterrain import MATERIAL_SLOTS

NEW_ENGINE = ("117", "1800", "2205")
GRID_STEP = 64  # a 117 or 1800 island's grid is a multiple of this (its chunk and page pyramid)
LAND = 0.0625  # a tile is land when its mean corner is over this (the land bit's rule)
HEIGHT_LIMIT = 32767 / 256  # world units the coarse height map (1/256 unit, 16 bits) can hold either way
# the island's land against the size classes that set settling cost and land tax (editor-ideas "Island size
# classes"), read as the most land a class holds
SIZE_CLASSES = (
    (9_000, N_("Small")),
    (18_000, N_("Medium")),
    (36_000, N_("Large")),
    (72_000, N_("Extra large")),
    (144_000, N_("Continental")),
)
MAP_ISLANDS = 127  # islands one map can hold (the game's island ids)
FLOAT = 2.0  # a placed object this far over or under the ground floats or is buried
STACKED = 0.05  # two copies of one model closer than this stand in each other
CELL = 8  # tiles per active-map cell


@dataclass(frozen=True)
class Finding:
    level: str  # error | warning | info
    message: str
    place: tuple[float, float, float] | None = None


def size_class(land_tiles: int) -> str:
    """The size class ``land_tiles`` falls in (``SIZE_CLASSES``); past the largest, the largest."""
    return next((name for most, name in SIZE_CLASSES if land_tiles <= most), SIZE_CLASSES[-1][1])


def land_tiles(corners: np.ndarray) -> int:
    """Tiles whose four corners average over ``LAND`` (``corners``: (size + 1) x (size + 1) heights)."""
    mean = (corners[:-1, :-1] + corners[1:, :-1] + corners[:-1, 1:] + corners[1:, 1:]) / 4
    return int((mean > LAND).sum())


def active_cells(corners: np.ndarray) -> int:
    """The 8-tile cells that hold land: what the island's active map must cover at least."""
    mean = (corners[:-1, :-1] + corners[1:, :-1] + corners[:-1, 1:] + corners[1:, 1:]) / 4
    n = mean.shape[0] // CELL * CELL
    cells = (mean[:n, :n] > LAND).reshape(n // CELL, CELL, n // CELL, CELL).any(axis=(1, 3))
    return int(cells.sum())


def readout(session: EditSession) -> dict:
    """The island's numbers: grid size, land tiles now and as opened, their size classes, active cells."""
    now = session.corner_heights()
    base = session.base_heights[:: session.spt, :: session.spt]
    land, land0 = land_tiles(now), land_tiles(base)
    return {
        "grid": session.size,
        "land tiles": land,
        "size class": size_class(land),
        "land tiles as opened": land0,
        "size class as opened": size_class(land0),
        "active cells": active_cells(now),
    }


def _grid(session: EditSession, game: str, info: dict) -> list[Finding]:
    out = [
        Finding(
            "info",
            tr(
                "grid {grid} tiles, {land} land tiles: {size_class}; {cells} active cells of {cell} tiles hold land",
                grid=info["grid"],
                land=number(info["land tiles"]),
                size_class=tr(info["size class"]),
                cells=info["active cells"],
                cell=CELL,
            ),
        )
    ]
    if game in ("117", "1800") and session.size % GRID_STEP:
        out.append(
            Finding(
                "error",
                tr(
                    "the grid of {size} tiles is not a multiple of {step}: the game reads an island only in whole chunks",
                    size=session.size,
                    step=GRID_STEP,
                ),
            )
        )
    if info["size class"] != info["size class as opened"]:
        out.append(
            Finding(
                "warning",
                tr(
                    "the edits change the size class from {old} to {new} ({old_land} -> {new_land} land tiles): "
                    "settling cost and land tax follow it",
                    old=tr(info["size class as opened"]),
                    new=tr(info["size class"]),
                    old_land=number(info["land tiles as opened"]),
                    new_land=number(info["land tiles"]),
                ),
            )
        )
    return out


def _heights(session: EditSession, game: str) -> list[Finding]:
    if game not in NEW_ENGINE:
        return []
    h = session.corner_heights()
    worst = float(np.abs(h).max()) if h.size else 0.0
    if worst <= HEIGHT_LIMIT:
        return []
    z, x = np.unravel_index(int(np.abs(h).argmax()), h.shape)
    return [
        Finding(
            "error",
            tr(
                "a height of {height} is past the {limit} units the island's height map holds: it wraps round in the game",
                height=f"{worst:.1f}",
                limit=f"{HEIGHT_LIMIT:.0f}",
            ),
            (float(x), float(h[z, x]), float(z)),
        )
    ]


def _place(e: Entry) -> tuple[float, float, float]:
    return (float(e.position[0]), float(e.position[1]), float(e.position[2]))


def _objects(session: EditSession) -> list[Finding]:
    out = []
    s = session.size
    for e in session.entries:
        if not (e.alive and e.visible) or e.kind == "tree":
            continue
        x, y, z = _place(e)
        name = label_of(e.base) or e.cfg or tr("an object")
        if not (0 <= x <= s and 0 <= z <= s):
            out.append(
                Finding(
                    "error",
                    tr("{name} lies off the island's grid (0..{size}): the game cannot place it", name=name, size=s),
                    (x, y, z),
                )
            )
            continue
        if e.added is None:
            continue  # the island's own objects stand where its makers put them
        ground = session.height_at(x, z)
        if y > ground + FLOAT:
            out.append(
                Finding(
                    "warning", tr("{name} floats {by} over the ground", name=name, by=f"{y - ground:.1f}"), (x, y, z)
                )
            )
        elif y < ground - FLOAT:
            out.append(
                Finding(
                    "warning",
                    tr("{name} is buried {by} under the ground", name=name, by=f"{ground - y:.1f}"),
                    (x, y, z),
                )
            )
    return out + _stacked(session)


def _stacked(session: EditSession) -> list[Finding]:
    """Two copies of one model at one place: a paste or a copy made twice."""
    seen: dict[tuple, list[Entry]] = defaultdict(list)
    out = []
    for e in session.entries:
        if not e.shown or e.kind == "tree":
            continue
        key = (e.batch, round(e.position[0] / STACKED), round(e.position[2] / STACKED))
        for other in seen[key]:
            if math.dist(other.position, e.position) < STACKED:
                out.append(
                    Finding(
                        "warning",
                        tr("two copies of {name} stand in each other", name=label_of(e.base) or e.cfg),
                        _place(e),
                    )
                )
                break
        seen[key].append(e)
    return out


def _water(session: EditSession) -> list[Finding]:
    net = session.water_net or {}
    out = []
    for k in range(len(net.get("rivers") or [])):
        r = session.river(k)
        pts, widths = r.get("points") or [], r.get("widths") or []
        if len(pts) < 2:
            out.append(Finding("error", tr("river {k} has {n} point(s): a river needs two at least", k=k, n=len(pts))))
            continue
        if any(w <= 0 for w in widths):
            out.append(
                Finding(
                    "error",
                    tr("river {k} has a point without width: its bed cannot be drawn", k=k),
                    tuple(pts[0]),
                )
            )
        if any(not (0 <= p[0] <= session.size and 0 <= p[-1] <= session.size) for p in pts):
            out.append(Finding("warning", tr("river {k} runs off the island's grid", k=k), tuple(pts[0])))
    for k, _lake in enumerate(net.get("lakes") or []):
        if not math.isfinite(session.lake_level(k)):
            out.append(Finding("error", tr("lake {k} has no level", k=k)))
    return out


def _slots(session: EditSession) -> list[Finding]:
    """The slots the island lists against the ones standing: the build lists them as they stand (island-editor "The
    slot list"), so a difference is only a readout."""
    return [
        Finding(
            "info",
            tr(
                "{name}: {now} slots stand, the island lists {listed}; the build lists those that stand",
                name=row["name"] or row["guid"],
                now=row["now"],
                listed=row["listed"],
            ),
        )
        for row in session.slot_rows()
        if row["now"] != row["listed"]
    ]


def _session(session: EditSession) -> list[Finding]:
    """The session settings the edits changed, each as the game would take it."""
    return [
        Finding("error", why)
        for key, value in session.edits.get("session", {}).items()
        if (why := sessionsettings.problem(key, value, session.size))
    ]


def _paint(session: EditSession) -> list[Finding]:
    bad = sorted(
        {int(st["material"]) for st in session.edits.get("paint", []) if not 0 <= int(st["material"]) < MATERIAL_SLOTS}
    )
    return [
        Finding(
            "error",
            tr(
                "material {m} is outside the {slots} a material map can store (5 bits): its strokes are left out",
                m=m,
                slots=MATERIAL_SLOTS,
            ),
        )
        for m in bad
    ]


def check(session: EditSession, game: str = "117") -> list[Finding]:
    """Every rule over the island as it now is (unsaved edits included), errors first."""
    info = readout(session)
    rows = _grid(session, game, info) + _heights(session, game) + _objects(session) + _water(session)
    rows += _paint(session) + _slots(session) + _session(session)
    rows += [Finding(*row) for row in placedrules.check(session)]
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(rows, key=lambda f: order[f.level])


FILES_LISTED = 200  # missing model files named one by one in the Checks tab; the rest are counted


def check_files(editor) -> list[Finding]:
    """The files the models drawn so far name that do not draw (``IslandEditor.file_problems``: not in the game's
    files, with the reason by its own index, or not decodable); the view draws a missing colour map as a checker."""
    rows = editor.file_problems() if hasattr(editor, "file_problems") else []
    out = [Finding("warning", _file_message(name, why, owner)) for name, why, owner in rows[:FILES_LISTED]]
    if len(rows) > FILES_LISTED:
        out.append(Finding("warning", tr("... and {n} more model files that do not draw", n=len(rows) - FILES_LISTED)))
    return out


def check_map(islands: int) -> list[Finding]:
    """A map's island count against the ids the game has."""
    if islands <= MAP_ISLANDS:
        return []
    return [Finding("error", tr("{n} islands on one map: the game has ids for {limit}", n=islands, limit=MAP_ISLANDS))]


def _file_message(name: str, why: str, owner: str | None) -> str:
    if owner:
        return tr("model file {name}: {why} (named by {owner})", name=name, why=why, owner=owner)
    return tr("model file {name}: {why}", name=name, why=why)


def _level_name(level: str) -> str:
    return {"error": tr("error"), "warning": tr("warning"), "info": tr("info")}.get(level, level)


def report(findings: list[Finding]) -> tuple[list[tuple[str, tuple | None]], str]:
    """The Checks tab's rows (text, place) and the status line."""
    rows = [(tr("{level}: {message}", level=_level_name(f.level), message=f.message), f.place) for f in findings]
    errors = sum(f.level == "error" for f in findings)
    warnings = sum(f.level == "warning" for f in findings)
    if not errors and not warnings:
        return rows, tr("the island follows the game's rules")
    return rows, tr("{errors} error(s), {warnings} warning(s)", errors=errors, warnings=warnings)


# the editor's own check in the Checks tab, beside those registered at ``features.CHECKS``
RULES = Check(
    lambda session, editor: check(session, game_of(session.island).removeprefix("anno")) + check_files(editor),
    report,
    N_("checking the game's rules"),
)
