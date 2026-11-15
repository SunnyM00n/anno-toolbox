"""What an operation sets, read from its content before it runs: the value paths a mod wrote, with the value, even where
the value was the one already there (such an operation changes nothing, so a comparison of results cannot see it).

``set_values(doc, kind, targets, content)`` lists a ``Write`` for every value a ``merge`` or a ``replace`` puts in an
asset. The path is the one ``elementdata.data_changes`` names (``Values/Product/Price``); inside a list, or where a name
repeats, the entry is named by its position in the list (``Values/Costs/Item[1]/Amount``). A value a ``ModValue`` gives is
known only after the operation: ``Write.settle`` reads it from the document then. ``docs/mod-diff.md`` "Conflicts".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass

from ..modloader import content as modcontent
from ..modloader.patchdoc import GUID_PATH, PatchDoc
from ..modloader.xpath import XPathError, select
from ..sources.valuediff import LIST_KEYS

Steps = tuple[tuple[str, int | None], ...]  # (tag, position among the same-named siblings or None) from the asset down


@dataclass(frozen=True)
class Write:
    """One value an operation sets: the asset (``GUID``), the value path, the text; None where a ``ModValue`` gives it."""

    guid: str
    path: str
    value: str | None
    asset: ET.Element | None = None
    steps: Steps = ()

    def settle(self, doc: PatchDoc) -> str:
        """The value the operation left at the path (a ``ModValue``'s result), read after it ran."""
        if self.value is not None:
            return self.value
        node = self.asset
        for tag, at in self.steps:
            found = [c for c in doc.children(node) if c.tag == tag] if node is not None else []
            node = found[at or 0] if (at or 0) < len(found) else None
        return doc.string(node).strip() if node is not None else ""


def guid_of(doc: PatchDoc, node) -> str | None:
    """The GUID (text) of the asset that holds ``node`` or is it, None for a node outside the assets."""
    asset = doc.asset_of(node) if hasattr(node, "tag") else None
    if asset is None:
        return None
    guid = doc.guid_text.get(asset) or (None if doc.is_lazy(asset) else asset.findtext(GUID_PATH))
    return guid.strip() if guid else None


def set_values(doc: PatchDoc, kind: str, targets: list, content: list[ET.Element]) -> list[Write]:
    """The ``Write`` of every value ``content`` sets on ``targets`` (``kind``: merge or replace)."""
    if kind not in ("merge", "replace"):
        return []
    out: list[Write] = []
    for target in targets:
        guid = guid_of(doc, target) if isinstance(target, ET.Element) else None
        if guid is None:
            continue
        steps = _steps(doc, target)
        found = _replaced(steps, target, content) if kind == "replace" else _merged(doc, steps, target, content)
        asset = doc.asset_of(target)
        out += [Write(guid, _path(path), value, asset, tuple(path)) for path, value in found]
    return out


def _path(steps: Steps | list) -> str:
    return "/".join(tag if at is None else f"{tag}[{at}]" for tag, at in steps)


def _place(siblings: list[ET.Element], el: ET.Element | None, tag: str, count: int) -> int | None:
    """The position ``el`` is named by: its place among ``siblings`` where the name is a list's (an ``Item``, or two or
    more of the name), else None (the data path has no index)."""
    if tag not in LIST_KEYS and count < 2:
        return None
    return next((k for k, c in enumerate(siblings) if c is el), 0)


def _steps(doc: PatchDoc, node: ET.Element) -> list[tuple[str, int | None]]:
    """The steps from the asset's child down to ``node`` (empty for the asset)."""
    steps: list[tuple[str, int | None]] = []
    while (up := doc.parent(node)) is not None and not (node.tag == "Asset" and up.tag == "Assets"):
        same = [c for c in doc.children(up) if c.tag == node.tag]
        steps.append((node.tag, _place(same, node, node.tag, len(same))))
        node = up
    return steps[::-1]


def _replaced(steps: list, target: ET.Element, content: list[ET.Element]) -> list[tuple[list, str]]:
    """A leaf replaced by a leaf of its name: its path and the new text."""
    if len(content) == 1 and steps and content[0].tag == target.tag and not len(content[0]) and not len(target):
        return [(steps, (content[0].text or "").strip())]
    return []


def _merged(doc: PatchDoc, steps: list, target: ET.Element, content: list[ET.Element]) -> list:
    if len(content) == 1 and content[0].tag == "ModValue":  # the target's own value, calculated
        return [(steps, None)] if steps and not len(target) else []
    if len(content) == 1 and content[0].tag == target.tag:  # merged into the target itself
        kids = [c for c in content[0] if isinstance(c.tag, str)]
        if not kids:
            return [(steps, (content[0].text or "").strip())] if steps and not len(target) else []
        return _leaves(doc, steps, target, kids)
    return _leaves(doc, steps, target, content)


def _leaves(doc: PatchDoc, steps: list, at: ET.Element | None, items: list[ET.Element]) -> list:
    """The values of ``items`` merged below the node ``at`` (None: a part the game's file lacks)."""
    out, count, seen = [], Counter(c.tag for c in items if isinstance(c.tag, str)), Counter()
    for el in items:
        if not isinstance(el.tag, str):
            continue
        if el.tag == "ModItem":
            out += _item(doc, steps, at, el)
            continue
        if el.tag in modcontent.SPECIAL:
            continue
        k = seen[el.tag]
        seen[el.tag] += 1
        same = [c for c in doc.children(at) if c.tag == el.tag] if at is not None else []
        here = same[k] if k < len(same) else None
        place = _place(same, here, el.tag, max(len(same), count[el.tag]))
        if place is not None and here is None:  # a list entry that is new: it shows as an addition
            continue
        out += _leaf(doc, [*steps, (el.tag, place)], here, el)
    return out


def _leaf(doc: PatchDoc, steps: list, here: ET.Element | None, el: ET.Element) -> list:
    """What the element ``el`` merged into ``here`` sets: its leaves, or its own text."""
    kids = [c for c in el if isinstance(c.tag, str)]
    if kids and all(c.tag == "ModValue" for c in kids):
        return [(steps, None)]  # a calculated value: known when the operation ran
    if kids:
        return _leaves(doc, steps, here, kids)
    return [(steps, (el.text or "").strip())] if here is None or not len(here) else []


def _item(doc: PatchDoc, steps: list, at: ET.Element | None, el: ET.Element) -> list:
    """A ``ModItem`` that finds an item by its key: the leaves it sets in that item (one that is appended is a change)."""
    if at is None or not el.get("Merge"):
        return []
    try:
        found = select(doc, f"Item[{modcontent.item_test(el)}]", at)
    except (XPathError, RecursionError):
        return []
    if not found:
        return []
    items = [c for c in doc.children(at) if c.tag == "Item"]
    pos = next((k for k, c in enumerate(items) if c is found[0]), 0)
    kids = [c for c in el if isinstance(c.tag, str)]
    return _leaves(doc, [*steps, ("Item", pos)], found[0], kids)
