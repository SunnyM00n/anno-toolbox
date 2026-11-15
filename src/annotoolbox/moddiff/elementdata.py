"""An XML element as plain data, and the value changes between two of them (``sources.valuediff``).

``element_data`` turns an element into text (a leaf), or a dict by child name (a repeated name a list, an attribute
``@name``); ``data_changes`` lists the leaves that differ as ``Change``; ``asset_facts`` reads an asset's name and
template. ``docs/mod-diff.md`` "What a change is".
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from ..sources.valuediff import diff_data
from .model import Change

TEXT = "#text"  # the text of an element that also has children or attributes


def element_data(el: ET.Element):
    """``el`` as text, or as a dict: children by tag (two or more of a name make a list), attributes as ``@name``."""
    if not len(el) and not el.attrib:
        return (el.text or "").strip()
    out: dict = {f"@{k}": v for k, v in el.attrib.items()}
    if (el.text or "").strip():
        out[TEXT] = el.text.strip()
    groups: dict[str, list] = {}
    for child in el:
        if isinstance(child.tag, str):
            groups.setdefault(child.tag, []).append(element_data(child))
    for tag, values in groups.items():
        out[tag] = values[0] if len(values) == 1 else values
    return out


def data_changes(before, after) -> list[Change]:
    """The leaves that differ between two data trees; text as it is, a block as its JSON (whole), in path order."""
    found: list[tuple] = []
    diff_data(before, after, "", found, full=True)  # whole: two long values must differ in what is compared
    return [Change(p, _text(a), _text(b)) for p, a, b in found]


def _text(value) -> str | None:
    return None if value is None else value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def asset_facts(asset: ET.Element | None) -> tuple[str, str]:
    """``(name, template)`` of an ``<Asset>`` element, empty where it names none."""
    if asset is None:
        return "", ""
    name = (asset.findtext("Values/Standard/Name") or "").strip()
    template = (asset.findtext("Template") or "").strip()
    return name, template
