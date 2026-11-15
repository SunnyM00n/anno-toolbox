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
class Registration:
    guid: int
    name: str
    file_path: str
    values: dict  # field (``FIELDS``) -> text, "" when the asset leaves it out


def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}>([^<]*)</{name}>", block)
    return m.group(1) if m else ""


def _of(block: str) -> Registration:
    guid = _tag(block, "GUID")
    return Registration(
        int(guid) if guid.isdigit() else 0,
        _tag(block, "Name"),
        _tag(block, "FilePath"),
        {f: _tag(block, f) for f in FIELDS},
    )


def assets(assets_xml: str) -> list[str]:
    """Every ``RandomIsland`` asset of the text, as XML."""
    return ASSET.findall(assets_xml)


def registrations(assets_xml: str, path: str) -> list[Registration]:
    """The registrations of the island file ``path`` (a game path to its ``.a7m``)."""
    return [_of(b) for b in assets(assets_xml) if f"<FilePath>{path}</FilePath>" in b]


def known_values(assets_xml: str) -> dict[str, list[str]]:
    """Per field, the values the game's own registrations use (each part of a ";" list on its own), sorted."""
    seen: dict[str, set[str]] = {f: set() for f in FIELDS}
    for block in assets(assets_xml):
        for f, v in _of(block).values.items():
            parts = v.split(";") if f in LISTS else [v]
            seen[f].update(p for p in parts if p)
    return {f: sorted(v) for f, v in seen.items()}


def slot_size(grid: int) -> str:
    """The slot size vanilla gives an island of ``grid`` tiles."""
    return next((tag for most, tag in SLOT_BY_GRID if grid <= most), LARGE_SLOT)


def check(changes: dict, known: dict[str, list[str]], grid: int | None = None) -> list[str]:
    """What is wrong with ``changes`` (field -> value): an unknown field, a value the game's own assets never use
    (the game turns a value it does not know into its default without a word), a slot size the grid does not fit."""
    out = []
    for f, v in changes.items():
        if f not in FIELDS:
            out.append(
                tr(
                    "{field} is not a field of the island's registration (one of {names})",
                    field=f,
                    names=", ".join(FIELDS),
                )
            )
            continue
        parts = [p for p in (v.split(";") if f in LISTS else [v]) if p]
        odd = [p for p in parts if known.get(f) and p not in known[f]]
        if odd:
            out.append(
                tr(
                    "{field} {values}: no island of the game uses it ({known} do)",
                    field=f,
                    values=", ".join(odd),
                    known=", ".join(known[f]),
                )
            )
    size = changes.get("IslandPlaceholderSize")
    if grid is not None and size is not None and size != slot_size(grid):
        want = slot_size(grid) or tr("untagged")
        out.append(
            tr(
                "a {grid}-tile island sits in {want} slots; {size} slots expect another size",
                grid=grid,
                want=want,
                size=size or tr("untagged"),
            )
        )
    return out


def apply(asset_xml: str, changes: dict) -> str:
    """The asset with ``changes`` (field -> value) written in; an empty value removes the field."""
    for f, v in changes.items():
        if f not in FIELDS:
            continue
        has = re.search(rf"<{f}>[^<]*</{f}>\n?", asset_xml)
        if has and v:
            asset_xml = asset_xml.replace(
                has.group(0), f"<{f}>{v}</{f}>" + ("\n" if has.group(0).endswith("\n") else "")
            )
        elif has:
            asset_xml = asset_xml.replace(has.group(0), "")
        elif v:
            asset_xml = asset_xml.replace("</RandomIsland>", f"<{f}>{v}</{f}>\n</RandomIsland>", 1)
    return asset_xml
