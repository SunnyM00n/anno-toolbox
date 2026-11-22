"""The slot linker of the island ports between Anno 117 and Anno 1800: a table from each slot asset of the source game to
the slot asset of the target game its objects become. ``suggest`` fills it by rule from the two games' assets, the person
edits it (the island editor's Slots tab), ``save`` keeps it with the project, and ``apply`` writes it into the port's
object table (``regiontables.STables.objects``); a slot with no link is listed (``unlinked``), never dropped
unseen. Rules and what the games store: ``docs/island-regions.md`` "Slots between the games".
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .assetmodels import CAssetModels
from .slotkinds import SSlotAsset, read_resources

GENERIC_WORDS = frozenset({"slot", "slots", "work", "area", "workarea", "workareaslot", "random"})
GENERIC_BUILDINGS = 3  # [I] slot buildings on one type from which a slot counts as taking many kinds (117: 5 and more)
LEFT_OUT_TYPES = frozenset({"oil"})  # 1800's crude oil slots (every variant) have no 117 equivalent
LEFT_OUT_TEMPLATES = frozenset({"Slot_Marsh"})  # 117's mud (marsh) slot has no 1800 equivalent
EQUIVALENT_GOODS = (  # (1800 product, its GUID, 117 product, its GUID): one material under two names
    ("iron", 1010227, "iron ore", 2080),
    ("copper", 1010230, "copper ore", 2099),
)  # coal stays out (117 burns charcoal in a kiln); zinc, cement, bauxite and the DLC ores have no 117 material
PARTNERS: dict[str, set[str]] = {}
for _a, _ga, _b, _gb in EQUIVALENT_GOODS:
    PARTNERS.setdefault(_a, set()).add(_b)
    PARTNERS.setdefault(_b, set()).add(_a)
FORMAT = 1
Links = dict[int, int | None]  # source slot GUID -> target slot GUID, None: left out on purpose


@dataclass
class SResources:
    """What a game's slot buildings say: per slot type the products they make, and how many stand on each type."""

    products: dict[str, set[str]] = field(default_factory=dict)
    per_type: Counter = field(default_factory=Counter)

    @classmethod
    def read(cls, models: CAssetModels) -> SResources:
        return cls(*read_resources(models))

    def commonest(self, types: Iterable[str]) -> str:
        """Of these slot types the one most slot buildings stand on ("" when none has a building)."""
        best = Counter({slot_type: self.per_type[slot_type] for slot_type in types if self.per_type[slot_type]})
        return best.most_common(1)[0][0] if best else ""


def kinds_of(asset: SSlotAsset, assets: dict[int, SSlotAsset]) -> set[str]:
    """The slot types a slot stands for: its own, or for a random slot those of the slots it becomes on a generated map."""
    if asset.slot_type.lower() == "random":
        return {
            assets[replacement].slot_type
            for replacement in asset.replacements
            if replacement in assets and assets[replacement].slot_type
        }
    return {asset.slot_type} if asset.slot_type else set()


def is_left_out(asset: SSlotAsset, kinds: set[str]) -> bool:
    """A slot the port leaves out on purpose: crude oil (its own type, or a random slot that only becomes oil) and mud."""
    return asset.template in LEFT_OUT_TEMPLATES or (bool(kinds) and {k.lower() for k in kinds} <= LEFT_OUT_TYPES)


def is_generic(asset: SSlotAsset, kinds: set[str], mine: SResources) -> bool:
    """A slot that takes many kinds of building rather than one resource's: a random slot that becomes several kinds (or
    none known), or a type that ``GENERIC_BUILDINGS`` or more of the game's slot buildings stand on (117's Mountain)."""
    if asset.slot_type.lower() == "random":
        return len(kinds) != 1
    return len(kinds) == 1 and mine.per_type[next(iter(kinds))] >= GENERIC_BUILDINGS


def _pick(source_asset: SSlotAsset, options: list[SSlotAsset], prefer: str) -> SSlotAsset | None:
    """The best of ``options`` for ``src``: the target region's own, then the most words shared (a work-area slot's
    mountain or river), the most slots it becomes (the plainest random slot), the fewest words, the lowest GUID."""
    if not options:
        return None
    own = source_asset.words - GENERIC_WORDS
    return min(
        options,
        key=lambda candidate: (
            prefer not in candidate.words,
            -len(own & candidate.words),
            -len(candidate.replacements),
            len(candidate.words),
            candidate.guid,
        ),
    )


def candidates(
    source_asset: SSlotAsset,
    assets: dict[int, SSlotAsset],
    target_assets: dict[int, SSlotAsset],
    mine: SResources,
    theirs: SResources,
) -> list[SSlotAsset]:
    """The target assets the rules allow for ``src``, in order (docs/island-regions.md "Slots between the games"): none
    for crude oil and mud (``is_left_out``); the same family (work-area slots pair by their mountain or river word); the
    same slot type; for a slot taking many kinds of building the target's such slot; else the slot types whose buildings
    make the same product, by the product assets' names compared whole, else by the table ``EQUIVALENT_GOODS``. No word
    of a building name is matched."""
    kinds = kinds_of(source_asset, assets)
    if is_left_out(source_asset, kinds):
        return []
    same = [
        candidate
        for candidate in target_assets.values()
        if candidate.family == source_asset.family and candidate.template not in LEFT_OUT_TEMPLATES
    ]
    if source_asset.family != "slot":
        return [candidate for candidate in same if (source_asset.words - GENERIC_WORDS) & candidate.words]
    if source_asset.slot_type and (
        kind := [candidate for candidate in same if candidate.slot_type.lower() == source_asset.slot_type.lower()]
    ):
        return kind
    if is_generic(source_asset, kinds, mine):
        if random := [candidate for candidate in same if candidate.slot_type.lower() == "random"]:
            return random
        plain = theirs.commonest(
            {candidate.slot_type for candidate in same if theirs.per_type[candidate.slot_type] >= GENERIC_BUILDINGS}
        )
        return [candidate for candidate in same if candidate.slot_type == plain] if plain else []
    made = set().union(*(mine.products.get(k, set()) for k in kinds)) if kinds else set()
    equal = made | set().union(*(PARTNERS.get(name, set()) for name in made))  # the table of equivalent goods
    types = {slot_type for slot_type, products in theirs.products.items() if products & made} or {
        slot_type for slot_type, products in theirs.products.items() if products & equal
    }
    return [candidate for candidate in same if candidate.slot_type in types]


def suggest(
    source_assets: dict[int, SSlotAsset],
    target_assets: dict[int, SSlotAsset],
    mine: SResources,
    theirs: SResources,
    prefer: str = "",
) -> Links:
    """A link for every source slot asset by rule (``candidates``, ``_pick``); None where no rule finds one. ``prefer``:
    a word of the target region ("roman") whose own slot wins among equals."""
    out: Links = {}
    for guid, asset in sorted(source_assets.items()):
        found = _pick(asset, candidates(asset, source_assets, target_assets, mine, theirs), prefer.lower())
        out[guid] = found.guid if found is not None else None
    return out


def resolve(suggested: Links, saved: Links | None) -> Links:
    """The suggestions with the person's links over them (a saved None leaves the slot out)."""
    return {**suggested, **(saved or {})}


def merge_saved(saved: Links, chosen: Links, suggested: Links) -> Links:
    """The saved links after the person chose ``chosen`` (for some slots): a choice that differs from the suggestion is
    kept, one that agrees with it drops the saved link of that slot; the other slots' links stay."""
    out = {guid: link for guid, link in saved.items() if guid not in chosen}
    out.update({guid: link for guid, link in chosen.items() if suggested.get(guid) != link})
    return out


def unlinked(used: Counter, links: Links, source_assets: dict[int, SSlotAsset]) -> list[dict]:
    """The slots the island has that the links leave out, as ``guid``, ``name``, ``count`` (a slot without any link and
    a slot linked to None alike)."""
    return [
        {"guid": guid, "name": source_assets[guid].name, "count": count}
        for guid, count in sorted(used.items())
        if guid in source_assets and links.get(guid) is None
    ]


def apply(table: dict[int, int | None], links: Links, used: Counter, source_assets: dict[int, SSlotAsset]) -> int:
    """Write the links of the slots the island has into the port's object table (over what the table found by name);
    a slot with no link is set to None (left out). Returns the objects linked."""
    done = 0
    for guid, count in used.items():
        if guid in source_assets:
            table[guid] = links.get(guid)
            done += count if links.get(guid) is not None else 0
    return done


def path_for(workspace: Path, src_game: str, dst_game: str) -> Path:
    """Where the person's links between two games are kept (the project's workspace)."""
    return workspace / "ports" / f"slotlinks-{src_game}-{dst_game}.json"


def save(path: Path, links: Links) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = {str(guid): link for guid, link in sorted(links.items())}
    path.write_text(json.dumps({"format": FORMAT, "links": rows}, indent=1) + "\n", encoding="utf-8", newline="\n")


def load(path: Path) -> Links:
    """The saved links; none when the file is missing or unreadable (a project without any)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("links") if isinstance(data, dict) and data.get("format") == FORMAT else None
    try:
        return {int(guid): (int(link) if link is not None else None) for guid, link in (rows or {}).items()}
    except (ValueError, TypeError, AttributeError):  # a hand-edited file: no links rather than a failed port
        return {}
