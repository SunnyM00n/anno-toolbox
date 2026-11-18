"""The checks of a map: overlapping islands (not at a complex's designed seams), places outside the playable area, a
start inside an island, too many continental islands, more slots than the pool has islands, no start, missing island
files, a playable area off the map (docs/map-editor.md "The checks"). ``check(facts)`` returns the findings; nothing
here reads a file.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from ..infra.i18n import N_, tr
from .placing import overlap
from .template import KIND_FIXED, KIND_SLOT, KIND_START, SIZE_CONTINENTAL, SIZES, TYPES, Element

ERROR, WARNING = N_("common.error_lower"), N_("common.warning_lower")
MODDED_GAMES = ("anno117", "anno1800")  # the games with map templates
POOL_TYPES = (0, 7)  # Normal and Volcanic slots draw from the pool by size; starters, third parties, pirates otherwise


@dataclass(frozen=True)
class Finding:
    severity: str
    text: str
    elements: tuple[int, ...] = ()  # indexes into the map's elements


@dataclass
class Facts:
    """What the checks look at: the map, each element's square edge, and the game's pool and files."""

    game: str
    size: tuple[int, int]
    playable: tuple[int, int, int, int]
    elements: list[Element]
    land: Callable[[Element], tuple[int, int, int, int] | None]  # what an element takes, None for a start
    exists: Callable[[str], bool] = lambda _p: True
    pool: Counter = field(default_factory=Counter)  # (type id, size class) -> islands of the template's region
    continental: Callable[[Element], bool] = lambda _e: False
    joined: Callable[[int, int], bool] = lambda _i, _j: False  # two islands of a complex, still as designed


def _inside(box: tuple, area: tuple) -> bool:
    return area[0] <= box[0] and area[1] <= box[1] and box[2] <= area[2] and box[3] <= area[3]


def _name(e: Element) -> str:
    if e.kind == KIND_START:
        return tr("maps.start_position_x_z", x=e.x, z=e.z)
    if e.kind == KIND_SLOT:
        return tr("maps.size_slot_x_z", size=tr(SIZES[min(e.size, 4)]), x=e.x, z=e.z)
    return tr(
        "maps.island_at_x_z",
        island=e.island_label or e.map_file_path.rsplit("/", 1)[-1] or tr("maps.an_island"),
        x=e.x,
        z=e.z,
    )


def _places(f: Facts) -> list[Finding]:
    out = []
    boxes = [(i, box) for i, e in enumerate(f.elements) if (box := f.land(e)) is not None]
    for n, (i, a) in enumerate(boxes):
        for j, b in boxes[n + 1 :]:
            mass = f.continental(f.elements[i]) or f.continental(f.elements[j])  # islands may lie on its box (TAMPER)
            if overlap(a, b) and not mass and not f.joined(i, j):
                text = tr("islands.a_and_b_overlap", a=_name(f.elements[i]), b=_name(f.elements[j]))
                out.append(Finding(WARNING, text, (i, j)))
    x0, z0, x1, z1 = f.playable
    for i, e in enumerate(f.elements):
        box = f.land(e) or (e.x, e.z, e.x, e.z)
        cx, cz = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2  # half in the border is the community's line
        if not (x0 <= cx <= x1 and z0 <= cz <= z1):
            out.append(Finding(WARNING, tr("maps.lies_outside_playable_area", what=_name(e)), (i,)))
        if e.kind == KIND_START:  # a fixed island's bay may hold a start (vanilla does): slots only
            for j, b in boxes:
                if f.elements[j].kind == KIND_SLOT and b[0] < e.x < b[2] and b[1] < e.z < b[3]:
                    text = tr("maps.start_lies_inside_island", start=_name(e), island=_name(f.elements[j]))
                    out.append(Finding(WARNING, text, (i, j)))
    return out


def _template_rules(f: Facts) -> list[Finding]:
    out = []
    starts = [i for i, e in enumerate(f.elements) if e.kind == KIND_START]
    if not starts:
        out.append(Finding(ERROR, tr("maps.map_no_start_position_players")))
    continental = [i for i, e in enumerate(f.elements) if f.continental(e)]
    if len(continental) > 1:
        out.append(Finding(WARNING, tr("maps.more_one_continental_island_game"), tuple(continental)))
    slots = Counter((e.type, e.size) for e in f.elements if e.kind == KIND_SLOT and e.type in POOL_TYPES)
    for (type_id, size), count in sorted(slots.items()):
        have = f.pool.get((type_id, size), 0)
        if f.pool and count > have:
            text = tr(
                "maps.slots_size_pool_holds_such",
                count=count,
                type=tr(TYPES.get(type_id, TYPES[0])),
                size=tr(SIZES[min(size, 4)]),
                have=have,
            )
            out.append(Finding(WARNING, text))
    x0, z0, x1, z1 = f.playable
    if not (0 <= x0 < x1 <= f.size[0] and 0 <= z0 < z1 <= f.size[1]):
        out.append(Finding(ERROR, tr("maps.playable_area_not_lie_inside")))
    if f.size[0] != f.size[1]:
        out.append(Finding(WARNING, tr("maps.map_not_square_no_game")))
    return out


def check(f: Facts) -> list[Finding]:
    """Every finding of the map, errors first."""
    out = _places(f)
    for i, e in enumerate(f.elements):
        if e.kind == KIND_FIXED and e.map_file_path and not f.exists(e.map_file_path):
            out.append(Finding(ERROR, tr("maps.file_not_game_project", island=_name(e)), (i,)))
    if f.game in MODDED_GAMES:
        out += _template_rules(f)
    return sorted(out, key=lambda x: x.severity != ERROR)


def is_continental(e: Element) -> bool:
    """A continental island: a fixed island whose file the game names so (117's DLC land mass, 1800's New World
    continent), or a slot of the continental size."""
    if e.kind == KIND_SLOT:
        return e.size == SIZE_CONTINENTAL
    return e.kind == KIND_FIXED and "continental" in e.map_file_path.rsplit("/", 1)[-1]
