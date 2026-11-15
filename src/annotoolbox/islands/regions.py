"""The game regions of Anno 117 and Anno 1800 as data: where a region keeps its islands, how its pool asset names it,
and what its vanilla islands name most (material set, ambient, vegetation set).

``region_of(game, path)`` tells an island's region from its game path; ``regions(game)`` lists a game's; ``GENERATION``
is the games that share the engine generation the porting between regions works within. The values are the most common of
each region's islands, read 2026-11-13 (island-regions "The regions").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..infra.i18n import N_

GENERATION = ("anno117", "anno1800")  # one engine generation: an island moves between them (gametransfer)


@dataclass(frozen=True)
class Region:
    game: str
    key: str
    label: str  # shown through tr()
    island_region: str  # the pool asset's IslandRegion (an engine enum)
    folder: str  # where a ported island of this region goes (a game path)
    material_set: str  # rd3d MaterialSetFileName, "" for the engine's default (1800's Old World)
    ambient: str  # SessionSettings GlobalAmbientName
    vegetation: str  # SessionSettings VegetationPropSetName
    pattern: str  # a regular expression an island's game path of this region matches
    sub_region: str = ""  # 1800's IslandSubRegion
    prefix: str = ""  # 1800's region prefix of an island's name

    @property
    def id(self) -> str:
        """``<game>:<key>``, as the command line and the edit files name a region."""
        return f"{self.game}:{self.key}"


_SETS_117 = "data/base/graphics/library/environment/material_sets/"
REGIONS: tuple[Region, ...] = (
    Region(
        "anno117", "roman", N_("Latium"), "Roman", "data/base/provinces/roman/islands/pool",
        _SETS_117 + "roman_materialset_02.xml", "amb_roman_01", "ls_heartlands_01", r"/provinces/roman/",
    ),
    Region(
        "anno117", "celtic", N_("Albion"), "Celtic", "data/base/provinces/celtic/islands/pool",
        _SETS_117 + "celtic_materialset_02.xml", "amb_celtic_01", "ls_wetlands_01", r"/provinces/celtic/",
    ),
    Region(
        "anno117", "egypt", N_("The Nile"), "Roman", "data/dlc03/provinces/egypt/islands/pool",
        "data/dlc03/graphics/library/environment/material_sets/egypt_materialset_01.xml", "amb_egypt_01",
        "ls_egypt_01", r"/provinces/egypt/",
    ),
    Region(
        "anno1800", "cape", N_("Cape Trelawney"), "Moderate", "data/dlc01/sessions/islands/pool", "", "Moderate_01",
        "Moderate", r"/moderate_dst_", "SecondaryRegion", "moderate_",
    ),
    Region(
        "anno1800", "moderate", N_("Old World"), "Moderate", "data/sessions/islands/pool/moderate", "", "Moderate_01",
        "Moderate", r"/moderate", "PrimaryRegion", "moderate_",
    ),
    Region(
        "anno1800", "colony01", N_("New World"), "Colony01", "data/sessions/islands/pool/colony01",
        "data/config/engine/material_sets/south_america_01.xml", "south_america_caribic_01", "Colony", r"/colony01",
        prefix="colony01_",
    ),
    Region(
        "anno1800", "colony02", N_("Enbesa"), "Africa", "data/dlc06/sessions/islands/pool",
        "data/config/engine/material_sets/colony02_01.xml", "Colony_02", "Colony02", r"/colony02", prefix="colony02_",
    ),
    Region(
        "anno1800", "colony03", N_("The Arctic"), "Arctic", "data/dlc03/sessions/islands/pool",
        "data/dlc03/config/engine/material_sets/arctic_01.xml", "DLC03_01", "Arctic", r"/colony03", prefix="colony03_",
    ),
)  # fmt: skip

# Words a region puts into its assets' names: taken out, two regions' assets of one kind read alike
# (``Slot Roman Mountain`` and ``Slot Celtic Mountain``: ``slot mountain``).
REGION_WORDS = {
    "anno117": ("roman", "celtic", "albion", "latium", "egyptian", "egypt", "ro|", "ce|", "eg|", "lf|"),
    "anno1800": ("moderate", "colony01", "colony02", "colony03", "colony", "arctic", "africa", "south_america"),
}


def regions(game: str) -> list[Region]:
    return [r for r in REGIONS if r.game == game]


def region(region_id: str) -> Region:
    """The region ``<game>:<key>``; ``KeyError`` for an unknown one."""
    found = next((r for r in REGIONS if r.id == region_id), None)
    if found is None:
        raise KeyError(region_id)
    return found


def region_of(game: str, path: str) -> Region | None:
    """The region of the island at the game path ``path``, None when its path names none (a scenario's island)."""
    low = path.replace("\\", "/").lower()
    return next((r for r in regions(game) if re.search(r.pattern, low)), None)


def same_look(a: Region, b: Region) -> bool:
    """Whether two regions draw an island alike (one game, set, ambient and vegetation): Cape Trelawney and the Old
    World differ only in their pool, so the island itself does not change between them."""
    return (a.game, a.material_set, a.ambient, a.vegetation) == (b.game, b.material_set, b.ambient, b.vegetation)


def plain_name(name: str, game: str) -> str:
    """An asset's name with its game's region words taken out, lower case and single-spaced."""
    low = f" {name.lower()} "
    for word in REGION_WORDS.get(game, ()):
        low = low.replace(word, " ")
    return " ".join(re.sub(r"[_|]+", " ", low).split())
