"""The asset editor's document on disk: a change set, not a copy of the game's files. Per asset the values set, the
values reset to what they inherit, the blocks (lists, arrays, inline assets) replaced whole; the new assets (from a
template, as a copy, from a base asset, imported whole), the assets removed, the texts per language, the mod's name,
the GUID range new assets take their numbers from and the folders whose other files a build carries. Stdlib only.

A build writes it out in the form the game takes (``modops``, ``native``), against the installed files, so a game
update never loses an edit (``docs/tool-suite.md`` section 3). ``CChangeSet.load`` / ``save`` read and write the JSON.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from ..suite.guidranges import CUSTOM, OWN, PUBLIC, resolve
from ..suite.project import DEFAULT_RANGE, other_ranges

FORMAT = 2  # the change set file's version (2: imported mods, ``whole``, ``xml``, ``place``, ``carry``)


@dataclass
class SAssetChange:
    set: dict[str, str] = field(default_factory=dict)  # ``Values``-relative path -> text
    reset: list[str] = field(default_factory=list)  # own values removed: the asset inherits them again
    blocks: dict[str, str] = field(default_factory=dict)  # ``Values``-relative path -> the element as XML
    whole: str = ""  # the asset as XML, replacing the game's (an import's last resort); the rest applies on top

    @property
    def empty(self) -> bool:
        return not (self.set or self.reset or self.blocks or self.whole)

    def paths(self) -> list[str]:
        return sorted({*self.set, *self.reset, *self.blocks})


@dataclass
class SNewAsset:
    template: str
    name: str
    base: int | None = None  # made from a base asset: it inherits every value it does not set
    copy_of: int | None = None  # a copy of that asset's own values
    anchor: int | None = None  # written beside this asset (its group); None: the asset file's last group
    place: str = "after"  # "after" the anchor, or "before" it (an imported asset first in its list)
    xml: str = ""  # the whole asset as a mod had it (an import); empty: made from template, base or copy


@dataclass
class SCarried:
    folder: str  # the imported mod's folder
    skip: list[str] = field(default_factory=list)  # its files the change set writes itself (mod-relative, lower case)


@dataclass
class CChangeSet:
    m_game: str
    m_mod_id: str = "my-asset-mod"
    m_mod_name: str = "My asset mod"
    m_version: str = "0.1.0"
    m_guid_range: tuple[int, int] = DEFAULT_RANGE
    m_guid_choice: str = CUSTOM  # own, public or custom (``suite.guidranges``); the numbers are ``guid_range``
    m_guid_source: str = ""  # a public choice's key
    m_assets: dict[int, SAssetChange] = field(default_factory=dict)
    m_new: dict[int, SNewAsset] = field(default_factory=dict)
    m_removed: list[int] = field(default_factory=list)
    m_texts: dict[str, dict[str, str]] = field(default_factory=dict)  # language -> text id -> text
    m_carry: list[SCarried] = field(default_factory=list)  # an imported mod's other files, copied by a build
    m_icons: dict[int, list[list[int]]] = field(default_factory=dict)  # GUID -> by icon: [atlas id, tile], [] unchanged

    m_requires: list[str] = field(default_factory=list)  # ModIDs of the mods an import needed: ``Dependencies.Require``

    def change(self, guid: int) -> SAssetChange:
        return self.m_assets.setdefault(guid, SAssetChange())

    def touched(self) -> list[int]:
        """Every asset the change set changes, adds or removes, in GUID order."""
        return sorted(
            {*(guid for guid, change in self.m_assets.items() if not change.empty), *self.m_new, *self.m_removed}
        )

    def next_guid(self, taken, within: tuple[int, int] | None = None) -> int:
        """The first GUID of the range (``within``: the own range of a group) that neither the game (``taken``: a
        container of GUIDs) nor this set uses."""
        min_guid, max_guid = within or self.m_guid_range
        for guid in range(min_guid, max_guid + 1):
            if guid not in taken and guid not in self.m_new:
                return guid
        raise ValueError(tr("assets.guid_range_full", lo=min_guid, hi=max_guid))

    # the file
    def outside_range(self) -> list[int]:
        """The new assets whose numbers lie outside the range (made before it changed; they keep their numbers)."""
        min_guid, max_guid = self.m_guid_range
        return sorted(guid for guid, added in self.m_new.items() if not added.xml and not min_guid <= guid <= max_guid)

    def to_json(self) -> str:
        data = {name.removeprefix("m_"): value for name, value in asdict(self).items()}  # the file's keys are plain
        data["format"] = FORMAT
        data["assets"] = {str(guid): asdict(change) for guid, change in self.m_assets.items() if not change.empty}
        data["new"] = {str(guid): asdict(added) for guid, added in self.m_new.items()}
        return json.dumps(data, indent=1, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> CChangeSet:
        data = json.loads(text)
        if data.get("format", FORMAT) > FORMAT:
            raise ValueError(
                tr(
                    "assets.change_set_format_newer_editors",
                    format=data["format"],
                    current=FORMAT,
                )
            )
        data.pop("format", None)
        data["assets"] = {int(guid): SAssetChange(**fields) for guid, fields in data.get("assets", {}).items()}
        data["new"] = {int(guid): SNewAsset(**added) for guid, added in data.get("new", {}).items()}
        data["guid_range"] = tuple(data.get("guid_range", DEFAULT_RANGE))
        if data.get("guid_choice", CUSTOM) not in (OWN, PUBLIC, CUSTOM):
            data["guid_choice"] = CUSTOM  # a hand edit or a later format: keep the numbers, as typed
        data["carry"] = [SCarried(**fields) for fields in data.get("carry", [])]
        data["icons"] = {
            int(guid): [[int(value) for value in tile] for tile in tiles]
            for guid, tiles in data.get("icons", {}).items()
        }
        known = cls.__dataclass_fields__
        return cls(**{f"m_{k}": value for k, value in data.items() if f"m_{k}" in known})

    def save(self, path: Path) -> None:
        write_atomic(path, self.to_json().encode("utf-8"))

    @classmethod
    def load(cls, path: Path) -> CChangeSet:
        return cls.from_json(path.read_text(encoding="utf-8"))

    @classmethod
    def for_project(cls, game: str, project, settings=None) -> CChangeSet:
        """A new change set for ``game`` in a project (``suite.project``): the project's mod and GUID range when it
        defines them. A workspace from before projects keeps the defaults, or with ``settings`` takes the user's
        own range (the default one belongs to another modder in the community registry)."""
        if not project.defined:
            made = cls(game)
            if settings is not None:
                taken = other_ranges(settings, project.folder, game)
                made.m_guid_range = resolve(OWN, "", made.m_guid_range, settings, game, taken)
                made.m_guid_choice = OWN
            return made
        return cls(
            game, project.mod_id, project.name, project.version, tuple(project.guid_range),
            project.guid_choice, project.guid_source,
        )  # fmt: skip
