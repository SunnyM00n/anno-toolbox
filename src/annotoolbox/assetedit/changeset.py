"""The asset editor's document on disk: a change set, not a copy of the game's files. Per asset the values set, the
values reset to what they inherit, the blocks (lists, arrays, inline assets) replaced whole; the new assets (from a
template, as a copy, from a base asset, imported whole), the assets removed, the texts per language, the mod's name,
the GUID range new assets take their numbers from and the folders whose other files a build carries. Stdlib only.

A build writes it out in the form the game takes (``modops``, ``native``), against the installed files, so a game
update never loses an edit (``docs/tool-suite.md`` section 3). ``ChangeSet.load`` / ``save`` read and write the JSON.
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
class AssetChange:
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
class NewAsset:
    template: str
    name: str
    base: int | None = None  # made from a base asset: it inherits every value it does not set
    copy_of: int | None = None  # a copy of that asset's own values
    anchor: int | None = None  # written beside this asset (its group); None: the asset file's last group
    place: str = "after"  # "after" the anchor, or "before" it (an imported asset first in its list)
    xml: str = ""  # the whole asset as a mod had it (an import); empty: made from template, base or copy


@dataclass
class Carried:
    folder: str  # the imported mod's folder
    skip: list[str] = field(default_factory=list)  # its files the change set writes itself (mod-relative, lower case)


@dataclass
class ChangeSet:
    game: str
    mod_id: str = "my-asset-mod"
    mod_name: str = "My asset mod"
    version: str = "0.1.0"
    guid_range: tuple[int, int] = DEFAULT_RANGE
    guid_choice: str = CUSTOM  # own, public or custom (``suite.guidranges``); the numbers are ``guid_range``
    guid_source: str = ""  # a public choice's key
    assets: dict[int, AssetChange] = field(default_factory=dict)
    new: dict[int, NewAsset] = field(default_factory=dict)
    removed: list[int] = field(default_factory=list)
    texts: dict[str, dict[str, str]] = field(default_factory=dict)  # language -> text id -> text
    carry: list[Carried] = field(default_factory=list)  # an imported mod's other files, copied by a build
    icons: dict[int, list[list[int]]] = field(default_factory=dict)  # GUID -> by icon: [atlas id, tile], [] unchanged

    requires: list[str] = field(default_factory=list)  # ModIDs of the mods an import needed: ``Dependencies.Require``

    def change(self, guid: int) -> AssetChange:
        return self.assets.setdefault(guid, AssetChange())

    def touched(self) -> list[int]:
        """Every asset the change set changes, adds or removes, in GUID order."""
        return sorted({*(g for g, c in self.assets.items() if not c.empty), *self.new, *self.removed})

    def next_guid(self, taken, within: tuple[int, int] | None = None) -> int:
        """The first GUID of the range (``within``: the own range of a group) that neither the game (``taken``: a
        container of GUIDs) nor this set uses."""
        lo, hi = within or self.guid_range
        for guid in range(lo, hi + 1):
            if guid not in taken and guid not in self.new:
                return guid
        raise ValueError(tr("the GUID range {lo}-{hi} is full", lo=lo, hi=hi))

    # the file
    def outside_range(self) -> list[int]:
        """The new assets whose numbers lie outside the range (made before it changed; they keep their numbers)."""
        lo, hi = self.guid_range
        return sorted(g for g, n in self.new.items() if not n.xml and not lo <= g <= hi)

    def to_json(self) -> str:
        data = asdict(self)
        data["format"] = FORMAT
        data["assets"] = {str(g): asdict(c) for g, c in self.assets.items() if not c.empty}
        data["new"] = {str(g): asdict(n) for g, n in self.new.items()}
        return json.dumps(data, indent=1, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> ChangeSet:
        data = json.loads(text)
        if data.get("format", FORMAT) > FORMAT:
            raise ValueError(
                tr(
                    "change set format {format} is newer than this editor's ({current})",
                    format=data["format"],
                    current=FORMAT,
                )
            )
        data.pop("format", None)
        data["assets"] = {int(g): AssetChange(**c) for g, c in data.get("assets", {}).items()}
        data["new"] = {int(g): NewAsset(**n) for g, n in data.get("new", {}).items()}
        data["guid_range"] = tuple(data.get("guid_range", DEFAULT_RANGE))
        if data.get("guid_choice", CUSTOM) not in (OWN, PUBLIC, CUSTOM):
            data["guid_choice"] = CUSTOM  # a hand edit or a later format: keep the numbers, as typed
        data["carry"] = [Carried(**c) for c in data.get("carry", [])]
        data["icons"] = {
            int(g): [[int(n) for n in tile] for tile in tiles] for g, tiles in data.get("icons", {}).items()
        }
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        write_atomic(path, self.to_json().encode("utf-8"))

    @classmethod
    def load(cls, path: Path) -> ChangeSet:
        return cls.from_json(path.read_text(encoding="utf-8"))

    @classmethod
    def for_project(cls, game: str, project, settings=None) -> ChangeSet:
        """A new change set for ``game`` in a project (``suite.project``): the project's mod and GUID range when it
        defines them. A workspace from before projects keeps the defaults, or with ``settings`` takes the user's
        own range (the default one belongs to another modder in the community registry)."""
        if not project.defined:
            made = cls(game)
            if settings is not None:
                taken = other_ranges(settings, project.folder, game)
                made.guid_range = resolve(OWN, "", made.guid_range, settings, game, taken)
                made.guid_choice = OWN
            return made
        return cls(
            game, project.mod_id, project.name, project.version, tuple(project.guid_range),
            project.guid_choice, project.guid_source,
        )  # fmt: skip
