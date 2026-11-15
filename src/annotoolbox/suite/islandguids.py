"""The GUIDs of islands a project saves as new ones: numbers from the project's own range, kept in the project.

``ProjectIslandGuids(path, range, earlier)`` hands out ``get(island)``. The registry file ``island-guids.json`` keeps
every number, so the same island has the same GUID in every build (a savegame holds it). A project whose file does
not exist yet but whose islands were already built before ran under ``earlier`` (the editor's own scheme); the first
build records those numbers as they were, so nothing is renumbered, and every island after that takes the next
number of the range. A project made empty gets its file at once (``Project.save``). docs/tool-suite.md, section 18.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..mod.guidrange import RangeAllocator

FILE = "island-guids.json"


class IslandNumbering(Protocol):
    """What the island build asks of a numbering: the editor's own scheme and this one both answer it."""

    def get(self, source: str) -> int: ...

    def name_assets(self, xml: str) -> str: ...

    def save(self) -> None: ...


class _Registry(RangeAllocator):
    """The range allocator, with the numbers of islands saved before the range (``legacy``) carried in the file."""

    def __init__(self, start: int, end: int, registry: Path) -> None:
        self.legacy: dict[str, int] = {}
        super().__init__(start, end, registry=registry)

    def read_extra(self, data: dict) -> None:
        self.legacy.update({str(k): int(v) for k, v in data.get("legacy", {}).items()})

    def extra(self) -> dict:
        return {"legacy": dict(sorted(self.legacy.items(), key=lambda kv: kv[1]))}


class ProjectIslandGuids:
    """Island GUIDs from a project's range; islands numbered before it keep the number ``earlier`` gave them."""

    def __init__(self, path: Path, guid_range: tuple[int, int], earlier: IslandNumbering) -> None:
        self.earlier = earlier
        self.first_run = not Path(path).is_file()
        try:
            self.registry = _Registry(*guid_range, registry=Path(path))
        except ValueError as exc:
            raise ValueError(f"{exc} (the project's GUID range changed after islands were numbered?)") from exc

    def get(self, source: str) -> int:
        reg = self.registry
        if source in reg.legacy:
            return reg.legacy[source]
        if self.first_run and source not in reg.known:
            reg.legacy[source] = self.earlier.get(source)
            return reg.legacy[source]
        return reg.get(source)

    def name_assets(self, xml: str) -> str:
        return self.earlier.name_assets(xml)

    def save(self) -> None:
        self.registry.save()
        self.earlier.save()


def create(path: Path, guid_range: tuple[int, int]) -> None:
    """An empty registry for a new project: every island it ever builds takes its number from the range."""
    if not Path(path).is_file():
        _Registry(*guid_range, registry=Path(path)).save()


def held(path: Path) -> list[int]:
    """The GUIDs a project's registry file has handed out (none: no file, or one that does not read)."""
    try:
        return sorted(int(v) for v in json.loads(Path(path).read_text(encoding="utf-8"))["guids"].values())
    except (OSError, ValueError, KeyError, AttributeError):
        return []
