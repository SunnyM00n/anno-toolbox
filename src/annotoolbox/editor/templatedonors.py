"""Objects of a game's other vanilla islands, to copy when the island being edited holds none of an asset or of its
template: a building placed from the asset list then carries the blocks its template gives it (a building's
``Building``, ``Maintenance``, ``Factory7`` ...) instead of a bare placed object's.

``TemplateDonors.find(guid, template)`` is the entry (``objectclones.place_copies`` asks it); the islands are read one by
one, on demand, and each once per install (``donor117.vanilla_objects``). docs/island-editor.md "Placed objects".
"""

from __future__ import annotations

import struct
from collections.abc import Callable

from ..formats import filedb
from ..islands import donor117
from .assetmodels import AssetModels

MAX_ISLANDS = 24  # a search reads at most this many islands: a kind no island carries ends the search


class TemplateDonors:
    """The first vanilla object of an asset, else of an asset of the same template, over ``paths`` (game paths of the
    game's islands; ``skip`` is the island being edited). ``template_of(guid)`` names an asset's template."""

    def __init__(
        self, files, paths: list[str], template_of: Callable[[int], str], skip: str = "", limit: int = MAX_ISLANDS
    ) -> None:
        self.files = files
        self.paths = [p for p in paths if p != skip][:limit]
        self.template_of = template_of
        self._islands: list[dict[int, filedb.Tag]] = []  # the objects of the islands read so far, by GUID

    def _read_next(self) -> bool:
        """Read one more island into ``_islands``; False when all are read."""
        if len(self._islands) >= len(self.paths):
            return False
        by_guid: dict[int, filedb.Tag] = {}
        try:
            objects = donor117.vanilla_objects(self.files, self.paths[len(self._islands)])
        except (KeyError, OSError, ValueError, struct.error):  # an island that does not read is not a donor
            objects = []
        for o in objects:
            if o.find("guid") is not None and o.find("Position") is not None:
                by_guid.setdefault(struct.unpack_from("<I", o.find("guid").data)[0], o)
        self._islands.append(by_guid)
        return True

    def _all(self):
        """Every island's objects, reading on while the caller keeps asking."""
        k = 0
        while k < len(self._islands) or self._read_next():
            yield self._islands[k]
            k += 1

    def find(self, guid: int, template: str = "") -> filedb.Tag | None:
        """A vanilla object of asset ``guid`` (the first island that has one), else of an asset of ``template``."""
        for by_guid in self._all():
            if guid in by_guid:
                return by_guid[guid]
        for by_guid in self._all():
            for other, o in by_guid.items():
                if template and self.template_of(other) == template:
                    return o
        return None


def for_game(key: str, files, models: AssetModels, skip: str = "") -> TemplateDonors:
    """The donors of game ``key`` (``anno117``, ``anno1800``) over its islands, templates from ``models``."""
    from .islandfiles import island_paths

    def template_of(guid: int) -> str:
        info = models.asset(guid)
        return info.template if info is not None else ""

    return TemplateDonors(files, island_paths(key, files), template_of, skip)
