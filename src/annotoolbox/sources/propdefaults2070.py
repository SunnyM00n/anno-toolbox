"""The defaults, ranges and enumerations 2070's tool files declare, read from the files instead of typed in.

Three small readers over files every install has: ``datasets`` (``data/config/game/datasets.xml``: the items of each
enumeration, such as ``ItemSocketQuality``), ``default_values`` (the ``DefaultValues`` block of a ``properties.xml``:
what a field holds when an asset or profile does not set it), ``value_range`` (the ``Min`` / ``Max`` a ``properties.xml``
declares for a field). Which question each answers: docs/formats/2070-defaults.md.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .overlay import CSourceTree

DATASETS = "data/config/game/datasets.xml"
AI_PROPERTIES = "data/config/ai/properties.xml"
FEATURE_PROPERTIES = "data/config/features/properties.xml"
GAME_PROPERTIES = "data/config/game/properties.xml"


@dataclass(frozen=True)
class SRange:
    minimum: int | None
    maximum: int | None


def _root(tree: CSourceTree, path: str) -> ET.Element:
    return ET.fromstring(tree.read_text(path, "latin-1"))


def datasets(tree: CSourceTree, path: str = DATASETS) -> dict[str, tuple[str, ...]]:
    """Every enumeration by name with its item names in file order (a name that repeats keeps the first)."""
    out: dict[str, tuple[str, ...]] = {}
    for node in _root(tree, path).iter("DataSet"):
        name = node.findtext("Name")
        if name and name not in out:
            out[name] = tuple(i.findtext("Name") or "" for i in node.findall("Items/Item"))
    return out


def _flatten(node: ET.Element, prefix: str, out: dict[str, list[str]]) -> None:
    for child in node:
        path = f"{prefix}{child.tag}"
        if len(child):
            _flatten(child, path + "/", out)
        else:
            out.setdefault(path, []).append((child.text or "").strip())


def default_values_in(text: str) -> dict[str, list[str]]:
    """``default_values`` of a properties file already read: ``"Budget/StartBudget" -> ["4000"]``. A path that repeats
    (lists of items) keeps every value in file order. Empty when the text has no such block or does not parse."""
    try:
        block = ET.fromstring(text).find("DefaultValues") if text else None
    except ET.ParseError:
        return {}
    out: dict[str, list[str]] = {}
    if block is not None:
        _flatten(block, "", out)
    return out


def default_values(tree: CSourceTree, path: str) -> dict[str, list[str]]:
    """The ``DefaultValues`` block of a ``properties.xml`` in the install, as ``default_values_in`` gives it."""
    return default_values_in(tree.read_text(path, "latin-1"))


def default(values: dict[str, list[str]], key: str) -> str | None:
    """The first value of a default, ``None`` when the file declares none (an empty text counts as none)."""
    found = values.get(key)
    return found[0] if found and found[0] != "" else None


def _int(text: str | None) -> int | None:
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def value_range(tree: CSourceTree, path: str, field: str, prop: str | None = None) -> SRange | None:
    """The ``Min`` / ``Max`` of the first ``ValueDefinition`` named ``field`` (inside the ``Property`` named ``prop``
    when given), ``None`` when there is none."""
    for property_node in _root(tree, path).iter("Property"):
        if prop is not None and property_node.findtext("Name") != prop:
            continue
        for value_definition in property_node.iter("ValueDefinition"):
            if value_definition.findtext("Name") == field:
                return SRange(_int(value_definition.findtext("Min")), _int(value_definition.findtext("Max")))
    return None
