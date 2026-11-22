"""What an operation sets, read from its content before it runs: the value paths a mod wrote, with the value, even where
the value was the one already there (such an operation changes nothing, so a comparison of results cannot see it).

``set_values(doc, kind, targets, content)`` lists a ``SWrite`` for every value a ``merge`` or a ``replace`` puts in an
asset. The path is the one ``elementdata.data_changes`` names (``Values/Product/Price``); inside a list, or where a name
repeats, the entry is named by its position in the list (``Values/Costs/Item[1]/Amount``). A value a ``ModValue`` gives is
known only after the operation: ``SWrite.settle`` reads it from the document then. ``docs/mod-diff.md`` "Conflicts".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass

from ..modloader import content as modcontent
from ..modloader.patchdoc import GUID_PATH, CPatchDoc
from ..modloader.xpath import CXPathError, select
from ..sources.valuediff import LIST_KEYS

Steps = tuple[tuple[str, int | None], ...]  # (tag, position among the same-named siblings or None) from the asset down


@dataclass(frozen=True)
class SWrite:
    """One value an operation sets: the asset (``GUID``), the value path, the text; None where a ``ModValue`` gives it."""

    guid: str
    path: str
    value: str | None
    asset: ET.Element | None = None
    steps: Steps = ()

    def settle(self, doc: CPatchDoc) -> str:
        """The value the operation left at the path (a ``ModValue``'s result), read after it ran."""
        if self.value is not None:
            return self.value
        node = self.asset
        for tag, position in self.steps:
            found = [child for child in doc.children(node) if child.tag == tag] if node is not None else []
            node = found[position or 0] if (position or 0) < len(found) else None
        return doc.string(node).strip() if node is not None else ""


def guid_of(doc: CPatchDoc, node) -> str | None:
    """The GUID (text) of the asset that holds ``node`` or is it, None for a node outside the assets."""
    asset = doc.asset_of(node) if hasattr(node, "tag") else None
    if asset is None:
        return None
    guid = doc.m_guid_text.get(asset) or (None if doc.is_lazy(asset) else asset.findtext(GUID_PATH))
    return guid.strip() if guid else None


def set_values(doc: CPatchDoc, kind: str, targets: list, content: list[ET.Element]) -> list[SWrite]:
    """The ``SWrite`` of every value ``content`` sets on ``targets`` (``kind``: merge or replace)."""
    if kind not in ("merge", "replace"):
        return []
    out: list[SWrite] = []
    for target in targets:
        guid = guid_of(doc, target) if isinstance(target, ET.Element) else None
        if guid is None:
            continue
        steps = _steps(doc, target)
        found = _replaced(steps, target, content) if kind == "replace" else _merged(doc, steps, target, content)
        asset = doc.asset_of(target)
        out += [SWrite(guid, _path(path), value, asset, tuple(path)) for path, value in found]
    return out


def _path(steps: Steps | list) -> str:
    return "/".join(tag if position is None else f"{tag}[{position}]" for tag, position in steps)


def _place(siblings: list[ET.Element], element: ET.Element | None, tag: str, count: int) -> int | None:
    """The position ``el`` is named by: its place among ``siblings`` where the name is a list's (an ``Item``, or two or
    more of the name), else None (the data path has no index)."""
    if tag not in LIST_KEYS and count < 2:
        return None
    return next((k for k, child in enumerate(siblings) if child is element), 0)


def _steps(doc: CPatchDoc, node: ET.Element) -> list[tuple[str, int | None]]:
    """The steps from the asset's child down to ``node`` (empty for the asset)."""
    steps: list[tuple[str, int | None]] = []
    while (parent_node := doc.parent(node)) is not None and not (node.tag == "Asset" and parent_node.tag == "Assets"):
        same = [child for child in doc.children(parent_node) if child.tag == node.tag]
        steps.append((node.tag, _place(same, node, node.tag, len(same))))
        node = parent_node
    return steps[::-1]


def _replaced(steps: list, target: ET.Element, content: list[ET.Element]) -> list[tuple[list, str]]:
    """A leaf replaced by a leaf of its name: its path and the new text."""
    if len(content) == 1 and steps and content[0].tag == target.tag and not len(content[0]) and not len(target):
        return [(steps, (content[0].text or "").strip())]
    return []


def _merged(doc: CPatchDoc, steps: list, target: ET.Element, content: list[ET.Element]) -> list:
    if len(content) == 1 and content[0].tag == "ModValue":  # the target's own value, calculated
        return [(steps, None)] if steps and not len(target) else []
    if len(content) == 1 and content[0].tag == target.tag:  # merged into the target itself
        kids = [child for child in content[0] if isinstance(child.tag, str)]
        if not kids:
            return [(steps, (content[0].text or "").strip())] if steps and not len(target) else []
        return _leaves(doc, steps, target, kids)
    return _leaves(doc, steps, target, content)


def _leaves(doc: CPatchDoc, steps: list, anchor: ET.Element | None, items: list[ET.Element]) -> list:
    """The values of ``items`` merged below the node ``at`` (None: a part the game's file lacks)."""
    out, count, seen = [], Counter(child.tag for child in items if isinstance(child.tag, str)), Counter()
    for element in items:
        if not isinstance(element.tag, str):
            continue
        if element.tag == "ModItem":
            out += _item(doc, steps, anchor, element)
            continue
        if element.tag in modcontent.SPECIAL:
            continue
        k = seen[element.tag]
        seen[element.tag] += 1
        same = [child for child in doc.children(anchor) if child.tag == element.tag] if anchor is not None else []
        here = same[k] if k < len(same) else None
        place = _place(same, here, element.tag, max(len(same), count[element.tag]))
        if place is not None and here is None:  # a list entry that is new: it shows as an addition
            continue
        out += _leaf(doc, [*steps, (element.tag, place)], here, element)
    return out


def _leaf(doc: CPatchDoc, steps: list, here: ET.Element | None, element: ET.Element) -> list:
    """What the element ``el`` merged into ``here`` sets: its leaves, or its own text."""
    kids = [child for child in element if isinstance(child.tag, str)]
    if kids and all(child.tag == "ModValue" for child in kids):
        return [(steps, None)]  # a calculated value: known when the operation ran
    if kids:
        return _leaves(doc, steps, here, kids)
    return [(steps, (element.text or "").strip())] if here is None or not len(here) else []


def _item(doc: CPatchDoc, steps: list, anchor: ET.Element | None, element: ET.Element) -> list:
    """A ``ModItem`` that finds an item by its key: the leaves it sets in that item (one that is appended is a change)."""
    if anchor is None or not element.get("Merge"):
        return []
    try:
        found = select(doc, f"Item[{modcontent.item_test(element)}]", anchor)
    except (CXPathError, RecursionError):
        return []
    if not found:
        return []
    items = [child for child in doc.children(anchor) if child.tag == "Item"]
    position = next((k for k, child in enumerate(items) if child is found[0]), 0)
    kids = [child for child in element if isinstance(child.tag, str)]
    return _leaves(doc, [*steps, ("Item", position)], found[0], kids)
