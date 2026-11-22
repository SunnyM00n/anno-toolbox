"""The slot assets of a game and the slot objects of an island: what the island editor's Slots tab lists and what a port
links from one game's slots to the other's (``slotlinks``).

A slot asset is an asset of the template ``Slot`` (and ``Slot_Marsh``), or a work-area slot (``WorkAreaSlot``,
``WorkAreaRiverSlot``): Anno 117 and Anno 1800 name them so. ``SSlotAsset.slot_type`` is the asset's ``SlotType``:
117's engine enum (Mountain, River, WorkArea), 1800's resource of the slot (Coal, Oil, Random, River ...). Plan and
use: ``docs/island-editor.md`` "The Slots tab", ``docs/island-regions.md`` "Slots between the games".
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .assetmodels import CAssetModels

SLOT_TEMPLATE = re.compile(r"^(Slot(_\w+)?|WorkArea\w*Slot)$")
WORK_AREA = "workarea"  # the family of the slots a building's work area stands on
PLAIN = "slot"  # the family of the others
_SCAN = re.compile(
    rb"<Asset>\s*<Template>(Slot(?:_\w+)?|WorkArea\w*Slot|SlotFactoryBuilding\w*)</Template>(.*?)</Asset>", re.S
)
_GUID = re.compile(rb"<GUID>(\d+)</GUID>")
_NAME = re.compile(rb"<Name>([^<]*)</Name>")
_REPLACEMENTS = re.compile(rb"<Replacements>(.*?)</Replacements>", re.S)
_NEW_ASSET = re.compile(rb"<NewAsset>(\d+)</NewAsset>")
_OUTPUTS = re.compile(rb"<FactoryOutputs>(.*?)</FactoryOutputs>", re.S)
_PRODUCT = re.compile(rb"<Product>(\d+)</Product>")
_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


@dataclass(frozen=True)
class SSlotAsset:
    """One slot asset: its GUID, name, template and ``SlotType`` ("" where it has none); ``replacements``: the slot
    assets a random slot becomes on a generated map (``RandomMapObject/Replacements``)."""

    guid: int
    name: str
    template: str
    slot_type: str = ""
    replacements: tuple[int, ...] = ()

    @property
    def family(self) -> str:
        return WORK_AREA if self.template.startswith("WorkArea") else PLAIN

    @property
    def kind(self) -> str:
        """What a person calls it: its slot type, else its name."""
        return self.slot_type or self.name

    @property
    def words(self) -> frozenset[str]:
        """The lower case words of its template, slot type and name (``WorkAreaRiverSlot`` gives work, area, river, slot)."""
        return words_of(self.template, self.slot_type, self.name)


def words_of(*texts: str) -> frozenset[str]:
    """The words of some names: split at case changes, spaces, underscores and brackets; numbers left out."""
    out: set[str] = set()
    for text in texts:
        for piece in re.split(r"[\s_|()]+", text):
            out.update(word.lower() for word in _CAMEL.findall(piece) if not word.isdigit())
    return frozenset(out)


def is_slot_template(template: str) -> bool:
    return bool(SLOT_TEMPLATE.match(template))


def read_assets(models: CAssetModels) -> dict[int, SSlotAsset]:
    """Every slot asset of the game's asset files, by GUID (a later file's definition wins)."""
    out: dict[int, SSlotAsset] = {}
    for data in models.m_files:
        for match in _SCAN.finditer(data):
            template = match.group(1).decode("ascii")
            guid = _GUID.search(match.group(2))
            if not is_slot_template(template) or guid is None:
                continue
            name = _NAME.search(match.group(2))
            guid_value = int(guid.group(1))
            out[guid_value] = SSlotAsset(
                guid_value,
                name.group(1).decode("utf-8", "replace") if name else "",
                template,
                models.value_of(guid_value, "SlotType") or "",
                tuple(int(number) for number in _NEW_ASSET.findall(_REPLACEMENTS.search(match.group(2)).group(1)))
                if _REPLACEMENTS.search(match.group(2))
                else (),
            )
    return out


def product_name(models: CAssetModels, guid: int) -> str:
    """A product asset's own name as the games compare goods: lower case, 117's "Good " prefix taken off ("" for none)."""
    asset = models.asset(guid)
    return asset.name.lower().removeprefix("good ").strip() if asset is not None else ""


def read_resources(models: CAssetModels) -> tuple[dict[str, set[str]], Counter]:
    """What the slot buildings say about their slots: for each slot type the products (by ``product_name``) of the
    buildings that stand on it (the building's ``FactoryOutputs``), and how many buildings stand on each slot type."""
    products: dict[str, set[str]] = {}
    per_type: Counter = Counter()
    for data in models.m_files:
        for match in _SCAN.finditer(data):
            if not match.group(1).startswith(b"SlotFactoryBuilding"):
                continue
            guid = _GUID.search(match.group(2))
            slot_type = models.value_of(int(guid.group(1)), "SlotType") if guid else None
            if not slot_type:
                continue
            per_type[slot_type] += 1
            outputs = _OUTPUTS.search(match.group(2))
            for product in _PRODUCT.findall(outputs.group(1)) if outputs else []:
                if name := product_name(models, int(product)):
                    products.setdefault(slot_type, set()).add(name)
    return products, per_type


@dataclass(frozen=True)
class SSlotRow:
    """A slot object of an island: its entry (``editsession.SEntry``) and its asset."""

    entry: object
    asset: SSlotAsset

    @property
    def position(self) -> tuple[float, float, float]:
        entry_position = self.entry.position
        return float(entry_position[0]), float(entry_position[1]), float(entry_position[2])


def rows(entries: Iterable, assets: dict[int, SSlotAsset]) -> list[SSlotRow]:
    """The slot objects among an island's entries (the standing ones), in the order of the island."""
    out = []
    for entry in entries:
        asset = assets.get(entry.base.get("guid")) if entry.kind == "object" else None
        if asset is not None and entry.alive and entry.visible:
            out.append(SSlotRow(entry, asset))
    return out


def matches(row: SSlotRow, text: str = "", kind: str = "") -> bool:
    """A row the Slots tab's filters keep: ``kind`` is a slot type or name (``SSlotAsset.kind``), ``text`` any part of its
    kind, name, GUID or template (case does not matter)."""
    asset = row.asset
    if kind and asset.kind != kind:
        return False
    low = text.strip().lower()
    return not low or low in f"{asset.kind} {asset.name} {asset.guid} {asset.template}".lower()
