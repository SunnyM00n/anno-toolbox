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
class SRegion:
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
REGIONS: tuple[SRegion, ...] = (
    SRegion(
        "anno117", "roman", N_("islands.latium"), "Roman", "data/base/provinces/roman/islands/pool",
        _SETS_117 + "roman_materialset_02.xml", "amb_roman_01", "ls_heartlands_01", r"/provinces/roman/",
    ),
    SRegion(
        "anno117", "celtic", N_("islands.albion"), "Celtic", "data/base/provinces/celtic/islands/pool",
        _SETS_117 + "celtic_materialset_02.xml", "amb_celtic_01", "ls_wetlands_01", r"/provinces/celtic/",
    ),
    SRegion(
        "anno117", "egypt", N_("islands.the_nile"), "Roman", "data/dlc03/provinces/egypt/islands/pool",
        "data/dlc03/graphics/library/environment/material_sets/egypt_materialset_01.xml", "amb_egypt_01",
        "ls_egypt_01", r"/provinces/egypt/",
    ),
    SRegion(
        "anno1800", "cape", N_("islands.cape_trelawney"), "Moderate", "data/dlc01/sessions/islands/pool", "", "Moderate_01",
        "Moderate", r"/moderate_dst_", "SecondaryRegion", "moderate_",
    ),
    SRegion(
        "anno1800", "moderate", N_("islands.old_world"), "Moderate", "data/sessions/islands/pool/moderate", "", "Moderate_01",
        "Moderate", r"/moderate", "PrimaryRegion", "moderate_",
    ),
    SRegion(
        "anno1800", "colony01", N_("islands.new_world"), "Colony01", "data/sessions/islands/pool/colony01",
        "data/config/engine/material_sets/south_america_01.xml", "south_america_caribic_01", "Colony", r"/colony01",
        prefix="colony01_",
    ),
    SRegion(
        "anno1800", "colony02", N_("islands.enbesa"), "Africa", "data/dlc06/sessions/islands/pool",
        "data/config/engine/material_sets/colony02_01.xml", "Colony_02", "Colony02", r"/colony02", prefix="colony02_",
    ),
    SRegion(
        "anno1800", "colony03", N_("islands.the_arctic"), "Arctic", "data/dlc03/sessions/islands/pool",
        "data/dlc03/config/engine/material_sets/arctic_01.xml", "DLC03_01", "Arctic", r"/colony03", prefix="colony03_",
    ),
)  # fmt: skip

# Words a region puts into its assets' names: taken out, two regions' assets of one kind read alike
# (``Slot Roman Mountain`` and ``Slot Celtic Mountain``: ``slot mountain``).
REGION_WORDS = {
    "anno117": ("roman", "celtic", "albion", "latium", "egyptian", "egypt", "ro|", "ce|", "eg|", "lf|"),
    "anno1800": ("moderate", "colony01", "colony02", "colony03", "colony", "arctic", "africa", "south_america"),
}


def regions(game: str) -> list[SRegion]:
    return [region for region in REGIONS if region.game == game]


def region(region_id: str) -> SRegion:
    """The region ``<game>:<key>``; ``KeyError`` for an unknown one."""
    found = next((candidate for candidate in REGIONS if candidate.id == region_id), None)
    if found is None:
        raise KeyError(region_id)
    return found


def region_of(game: str, path: str) -> SRegion | None:
    """The region of the island at the game path ``path``, None when its path names none (a scenario's island)."""
    low = path.replace("\\", "/").lower()
    return next((candidate for candidate in regions(game) if re.search(candidate.pattern, low)), None)


def same_look(region_a: SRegion, region_b: SRegion) -> bool:
    """Whether two regions draw an island alike (one game, set, ambient and vegetation): Cape Trelawney and the Old
    World differ only in their pool, so the island itself does not change between them."""
    return (region_a.game, region_a.material_set, region_a.ambient, region_a.vegetation) == (
        region_b.game,
        region_b.material_set,
        region_b.ambient,
        region_b.vegetation,
    )


def plain_name(name: str, game: str) -> str:
    """An asset's name with its game's region words taken out, lower case and single-spaced."""
    low = f" {name.lower()} "
    for word in REGION_WORDS.get(game, ()):
        low = low.replace(word, " ")
    return " ".join(re.sub(r"[_|]+", " ", low).split())
