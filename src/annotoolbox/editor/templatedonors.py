"""Objects of a game's other vanilla islands, to copy when the island being edited holds none of an asset or of its
template: a building placed from the asset list then carries the blocks its template gives it (a building's
``Building``, ``Maintenance``, ``Factory7`` ...) instead of a bare placed object's.

``CTemplateDonors.find(guid, template)`` is the entry (``objectclones.place_copies`` asks it); the islands are read one by
one, on demand, and each once per install (``donor117.vanilla_objects``). docs/island-editor.md "Placed objects".
"""

from __future__ import annotations

import struct
from collections.abc import Callable

from ..formats import filedb
from ..islands import donor117
from .assetmodels import CAssetModels

MAX_ISLANDS = 24  # a search reads at most this many islands: a kind no island carries ends the search


class CTemplateDonors:
    """The first vanilla object of an asset, else of an asset of the same template, over ``paths`` (game paths of the
    game's islands; ``skip`` is the island being edited). ``template_of(guid)`` names an asset's template."""

    def __init__(
        self, files, paths: list[str], template_of: Callable[[int], str], skip: str = "", limit: int = MAX_ISLANDS
    ) -> None:
        self.m_files = files
        self.m_paths = [path for path in paths if path != skip][:limit]
        self.m_template_of = template_of
        self._m_islands: list[dict[int, filedb.STag]] = []  # the objects of the islands read so far, by GUID

    def _read_next(self) -> bool:
        """Read one more island into ``_islands``; False when all are read."""
        if len(self._m_islands) >= len(self.m_paths):
            return False
        by_guid: dict[int, filedb.STag] = {}
        try:
            objects = donor117.vanilla_objects(self.m_files, self.m_paths[len(self._m_islands)])
        except (KeyError, OSError, ValueError, struct.error):  # an island that does not read is not a donor
            objects = []
        for tag in objects:
            if tag.find("guid") is not None and tag.find("Position") is not None:
                by_guid.setdefault(struct.unpack_from("<I", tag.find("guid").data)[0], tag)
        self._m_islands.append(by_guid)
        return True

    def _all(self):
        """Every island's objects, reading on while the caller keeps asking."""
        index = 0
        while index < len(self._m_islands) or self._read_next():
            yield self._m_islands[index]
            index += 1

    def find(self, guid: int, template: str = "") -> filedb.STag | None:
        """A vanilla object of asset ``guid`` (the first island that has one), else of an asset of ``template``."""
        for by_guid in self._all():
            if guid in by_guid:
                return by_guid[guid]
        for by_guid in self._all():
            for other, tag in by_guid.items():
                if template and self.m_template_of(other) == template:
                    return tag
        return None


def for_game(key: str, files, models: CAssetModels, skip: str = "") -> CTemplateDonors:
    """The donors of game ``key`` (``anno117``, ``anno1800``) over its islands, templates from ``models``."""
    from .islandfiles import island_paths

    def template_of(guid: int) -> str:
        asset = models.asset(guid)
        return asset.template if asset is not None else ""

    return CTemplateDonors(files, island_paths(key, files), template_of, skip)
