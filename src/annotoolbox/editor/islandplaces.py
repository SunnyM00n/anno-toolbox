"""An island's fixed places on its game's maps: the map templates that place an island file as a fixed element (as both
games place their 768-tile continental islands), and those templates with another island in that place.

``fixed_islands(side)`` lists where every fixed island stands; ``placed_at(side, island)`` the templates and elements
of one island; ``take_place(side, old, new, label)`` the templates rewritten with ``new`` in ``old``'s places, ready to
go into a mod at their own paths (a file at a vanilla template's path replaces it for every new game). Rules:
docs/island-regions.md "A place on a map".
"""

from __future__ import annotations

from dataclasses import dataclass

from ..formats import filedb
from ..mapedit import template


@dataclass(frozen=True)
class SPlace:
    """A fixed island of a map template: the template, the island file, its corner (tiles) and quarter turns."""

    template: str
    island: str
    x: int
    z: int
    Rotation90: int


def templates(side) -> list[str]:
    """Every map template (``.a7tinfo``) of the game, sorted."""
    listing = getattr(side.m_files, "list", None) or side.m_files.iter_paths
    return sorted(path for path in listing("data/") if path.endswith(".a7tinfo"))


def _read(side, path: str) -> template.SMapTemplate | None:
    try:
        return template.read(side.read(path))
    except (KeyError, OSError, ValueError):
        return None


def fixed_islands(side) -> list[SPlace]:
    """Every fixed island of every map template of the game (island paths lower case, forward slashes)."""
    out = []
    for path in templates(side):
        parsed = _read(side, path)
        for element in parsed.elements if parsed is not None else []:
            if element.kind == template.KIND_FIXED and element.MapFilePath:
                island = element.MapFilePath.replace("\\", "/").lower()
                out.append(SPlace(path, island, element.x, element.z, element.Rotation90))
    return out


def placed_at(side, island: str) -> list[SPlace]:
    """The places of the island file ``island`` on the game's maps."""
    key = island.replace("\\", "/").lower()
    return [place for place in fixed_islands(side) if place.island == key]


def story_map(path: str) -> bool:
    """A campaign's or scenario's map: its quests name the islands it places, so no other island takes a place there."""
    low = path.lower()
    return "/campaign/" in low or "/scenario" in low


def take_place(side, old: str, new: str, label: str) -> dict[str, bytes]:
    """Every map template that places ``old`` (not a ``story_map``), rewritten with ``new`` there (``label`` its
    ``IslandLabel``), by template path. The old island's mine slot mapping goes: it names the old island's slot objects, and a mapping
    left empty lets the game deal the mines itself. Everything else of the place stays: position, turn, type,
    fertilities and the template's other islands."""
    key = old.replace("\\", "/").lower()
    out = {}
    for path in sorted({place.template for place in placed_at(side, old) if not story_map(place.template)}):
        parsed = _read(side, path)
        changed = False
        for element in parsed.elements if parsed is not None else []:
            if element.kind == template.KIND_FIXED and element.MapFilePath.replace("\\", "/").lower() == key:
                element.MapFilePath, element.IslandLabel, element.MineSlotActivation = new, label, ()
                changed = True
        if changed:
            out[path] = parsed.render()
    return out


def island_size(side, island: str) -> int | None:
    """The grid of an island file (its ``.a7minfo``'s ``MapSize``), None where it does not read."""
    try:
        map_info = filedb.read(side.read(island[:-4] + ".a7minfo"))
        return int.from_bytes(map_info.find("MapSize").data[:4], "little")
    except (KeyError, OSError, ValueError, AttributeError):
        return None
