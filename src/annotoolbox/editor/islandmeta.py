"""An island's registration in the random map's pool: the ``RandomIsland`` asset that names its file, region,
difficulty, type and slot size, read from the game's assets, changed by hand and written for an island saved as a new
one.

``registrations(assets_xml, path)`` reads what registers ``path``; ``known_values`` lists what the game's own assets
use per field (the choices a form offers); ``check`` says what a set of changes would do wrong; ``apply`` writes the
changes into an asset's XML. The edit file keeps the changes as ``registration`` (field -> value); the fields and
the slot sizes by grid: island-editor "Full spec".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..infra.i18n import tr

FIELDS = ("IslandRegion", "IslandDifficulty", "IslandType", "IslandPlaceholderSize")
LISTS = ("IslandDifficulty", "IslandType")  # fields that hold several values joined by ";"
ASSET = re.compile(r"<Asset>(?:(?!</Asset>).)*?<Template>RandomIsland</Template>.*?</Asset>", re.S)
# the slot sizes vanilla gives islands by grid (island-editor-tools "Game rules"): 128-256 untagged, up to 320
# Medium, larger Large
SLOT_BY_GRID = ((256, ""), (320, "Medium"))
LARGE_SLOT = "Large"


@dataclass(frozen=True)
class SRegistration:
    guid: int
    name: str
    file_path: str
    values: dict  # field (``FIELDS``) -> text, "" when the asset leaves it out


def _tag(block: str, name: str) -> str:
    match = re.search(rf"<{name}>([^<]*)</{name}>", block)
    return match.group(1) if match else ""


def _of(block: str) -> SRegistration:
    guid = _tag(block, "GUID")
    return SRegistration(
        int(guid) if guid.isdigit() else 0,
        _tag(block, "Name"),
        _tag(block, "FilePath"),
        {field_name: _tag(block, field_name) for field_name in FIELDS},
    )


def assets(assets_xml: str) -> list[str]:
    """Every ``RandomIsland`` asset of the text, as XML."""
    return ASSET.findall(assets_xml)


def registrations(assets_xml: str, path: str) -> list[SRegistration]:
    """The registrations of the island file ``path`` (a game path to its ``.a7m``)."""
    return [_of(block) for block in assets(assets_xml) if f"<FilePath>{path}</FilePath>" in block]


def known_values(assets_xml: str) -> dict[str, list[str]]:
    """Per field, the values the game's own registrations use (each part of a ";" list on its own), sorted."""
    seen: dict[str, set[str]] = {field_name: set() for field_name in FIELDS}
    for block in assets(assets_xml):
        for field_name, value in _of(block).values.items():
            parts = value.split(";") if field_name in LISTS else [value]
            seen[field_name].update(part for part in parts if part)
    return {field_name: sorted(value) for field_name, value in seen.items()}


def slot_size(grid: int) -> str:
    """The slot size vanilla gives an island of ``grid`` tiles."""
    return next((tag for most, tag in SLOT_BY_GRID if grid <= most), LARGE_SLOT)


def check(changes: dict, known: dict[str, list[str]], grid: int | None = None) -> list[str]:
    """What is wrong with ``changes`` (field -> value): an unknown field, a value the game's own assets never use
    (the game turns a value it does not know into its default without a word), a slot size the grid does not fit."""
    out = []
    for field_name, value in changes.items():
        if field_name not in FIELDS:
            out.append(
                tr(
                    "islands.not_field_islands_registration_one",
                    field=field_name,
                    names=", ".join(FIELDS),
                )
            )
            continue
        parts = [part for part in (value.split(";") if field_name in LISTS else [value]) if part]
        odd = [part for part in parts if known.get(field_name) and part not in known[field_name]]
        if odd:
            out.append(
                tr(
                    "islands.no_island_game_uses",
                    field=field_name,
                    values=", ".join(odd),
                    known=", ".join(known[field_name]),
                )
            )
    size = changes.get("IslandPlaceholderSize")
    if grid is not None and size is not None and size != slot_size(grid):
        want = slot_size(grid) or tr("islands.untagged")
        out.append(
            tr(
                "islands.tile_island_sits_slots_expect",
                grid=grid,
                want=want,
                size=size or tr("islands.untagged"),
            )
        )
    return out


def apply(asset_xml: str, changes: dict) -> str:
    """The asset with ``changes`` (field -> value) written in; an empty value removes the field."""
    for field_name, value in changes.items():
        if field_name not in FIELDS:
            continue
        has = re.search(rf"<{field_name}>[^<]*</{field_name}>\n?", asset_xml)
        if has and value:
            asset_xml = asset_xml.replace(
                has.group(0), f"<{field_name}>{value}</{field_name}>" + ("\n" if has.group(0).endswith("\n") else "")
            )
        elif has:
            asset_xml = asset_xml.replace(has.group(0), "")
        elif value:
            asset_xml = asset_xml.replace(
                "</RandomIsland>", f"<{field_name}>{value}</{field_name}>\n</RandomIsland>", 1
            )
    return asset_xml
