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
from .template import KIND_FIXED, KIND_SLOT, KIND_START, SIZE_CONTINENTAL, SIZES, TYPES, SElement

ERROR, WARNING = N_("common.error_lower"), N_("common.warning_lower")
MODDED_GAMES = ("anno117", "anno1800")  # the games with map templates
POOL_TYPES = (0, 7)  # Normal and Volcanic slots draw from the pool by size; starters, third parties, pirates otherwise


@dataclass(frozen=True)
class SFinding:
    severity: str
    text: str
    elements: tuple[int, ...] = ()  # indexes into the map's elements


@dataclass
class SFacts:
    """What the checks look at: the map, each element's square edge, and the game's pool and files."""

    game: str
    size: tuple[int, int]
    playable: tuple[int, int, int, int]
    elements: list[SElement]
    land: Callable[[SElement], tuple[int, int, int, int] | None]  # what an element takes, None for a start
    exists: Callable[[str], bool] = lambda _p: True
    pool: Counter = field(default_factory=Counter)  # (type id, size class) -> islands of the template's region
    continental: Callable[[SElement], bool] = lambda _e: False
    joined: Callable[[int, int], bool] = lambda _i, _j: False  # two islands of a complex, still as designed


def _inside(box: tuple, area: tuple) -> bool:
    return area[0] <= box[0] and area[1] <= box[1] and box[2] <= area[2] and box[3] <= area[3]


def _name(element: SElement) -> str:
    if element.kind == KIND_START:
        return tr("maps.start_position_x_z", x=element.x, z=element.z)
    if element.kind == KIND_SLOT:
        return tr("maps.size_slot_x_z", size=tr(SIZES[min(element.size, 4)]), x=element.x, z=element.z)
    return tr(
        "maps.island_at_x_z",
        island=element.IslandLabel or element.MapFilePath.rsplit("/", 1)[-1] or tr("maps.an_island"),
        x=element.x,
        z=element.z,
    )


def _places(facts: SFacts) -> list[SFinding]:
    out = []
    boxes = [(i, box) for i, element in enumerate(facts.elements) if (box := facts.land(element)) is not None]
    for number, (i, land_box) in enumerate(boxes):
        for j, other_box in boxes[number + 1 :]:
            mass = facts.continental(facts.elements[i]) or facts.continental(
                facts.elements[j]
            )  # islands may lie on its box (TAMPER)
            if overlap(land_box, other_box) and not mass and not facts.joined(i, j):
                text = tr("islands.a_and_b_overlap", a=_name(facts.elements[i]), b=_name(facts.elements[j]))
                out.append(SFinding(WARNING, text, (i, j)))
    x0, z0, x1, z1 = facts.playable
    for i, element in enumerate(facts.elements):
        box = facts.land(element) or (element.x, element.z, element.x, element.z)
        cx, cz = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2  # half in the border is the community's line
        if not (x0 <= cx <= x1 and z0 <= cz <= z1):
            out.append(SFinding(WARNING, tr("maps.lies_outside_playable_area", what=_name(element)), (i,)))
        if element.kind == KIND_START:  # a fixed island's bay may hold a start (vanilla does): slots only
            for j, other_box in boxes:
                if (
                    facts.elements[j].kind == KIND_SLOT
                    and other_box[0] < element.x < other_box[2]
                    and other_box[1] < element.z < other_box[3]
                ):
                    text = tr("maps.start_lies_inside_island", start=_name(element), island=_name(facts.elements[j]))
                    out.append(SFinding(WARNING, text, (i, j)))
    return out


def _template_rules(facts: SFacts) -> list[SFinding]:
    out = []
    starts = [i for i, element in enumerate(facts.elements) if element.kind == KIND_START]
    if not starts:
        out.append(SFinding(ERROR, tr("maps.map_no_start_position_players")))
    continental = [i for i, element in enumerate(facts.elements) if facts.continental(element)]
    if len(continental) > 1:
        out.append(SFinding(WARNING, tr("maps.more_one_continental_island_game"), tuple(continental)))
    slots = Counter(
        (element.type, element.size)
        for element in facts.elements
        if element.kind == KIND_SLOT and element.type in POOL_TYPES
    )
    for (type_id, size), count in sorted(slots.items()):
        have = facts.pool.get((type_id, size), 0)
        if facts.pool and count > have:
            text = tr(
                "maps.slots_size_pool_holds_such",
                count=count,
                type=tr(TYPES.get(type_id, TYPES[0])),
                size=tr(SIZES[min(size, 4)]),
                have=have,
            )
            out.append(SFinding(WARNING, text))
    x0, z0, x1, z1 = facts.playable
    if not (0 <= x0 < x1 <= facts.size[0] and 0 <= z0 < z1 <= facts.size[1]):
        out.append(SFinding(ERROR, tr("maps.playable_area_not_lie_inside")))
    if facts.size[0] != facts.size[1]:
        out.append(SFinding(WARNING, tr("maps.map_not_square_no_game")))
    return out


def check(facts: SFacts) -> list[SFinding]:
    """Every finding of the map, errors first."""
    out = _places(facts)
    for i, element in enumerate(facts.elements):
        if element.kind == KIND_FIXED and element.MapFilePath and not facts.exists(element.MapFilePath):
            out.append(SFinding(ERROR, tr("maps.file_not_game_project", island=_name(element)), (i,)))
    if facts.game in MODDED_GAMES:
        out += _template_rules(facts)
    return sorted(out, key=lambda x: x.severity != ERROR)


def is_continental(element: SElement) -> bool:
    """A continental island: a fixed island whose file the game names so (117's DLC land mass, 1800's New World
    continent), or a slot of the continental size."""
    if element.kind == KIND_SLOT:
        return element.size == SIZE_CONTINENTAL
    return element.kind == KIND_FIXED and "continental" in element.MapFilePath.rsplit("/", 1)[-1]
