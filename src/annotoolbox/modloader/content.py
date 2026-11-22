"""What an operation's content does to its target: ``merge`` (by name and position, the leaf style, ``ModItem``
lists by key), ``ModValue`` (a node or a value calculated, flags merged or removed) and ``ModOpContent`` (nodes taken
from the document). Stdlib only.

``merge(document, target, content, calc)`` is the entry for a merge, ``resolve(document, element, context, calc)`` puts the
``ModValue`` results into content about to be inserted, ``fill(content, found)`` the nodes of ``Content=``; ``calc``
evaluates an expression (its variables replaced) at a node. The rules: ``docs/engine-modloader.md`` ("Merge,
exactly", "ModValue and ModItem").
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable

from .patchdoc import CPatchDoc
from .xpath import format_value, select

Calc = Callable[[str, object], object]  # (expression, context node) -> XPath value
SPECIAL = frozenset({"ModValue", "ModItem", "ModOpContent", "ModValueContent"})


def flags(text: str) -> list[str]:
    return [flag.strip() for flag in (text or "").split(";") if flag.strip()]


def merge(document: CPatchDoc, target: ET.Element, content: list[ET.Element], calc: Calc) -> None:
    """``content`` merged into ``target``: into the target itself when its one element has the target's name."""
    if len(content) == 1 and content[0].tag == target.tag:
        _merge_into(document, target, content[0], calc)
    else:
        _merge_children(document, target, content, calc)
    document.changed(target)


def _merge_into(document: CPatchDoc, target: ET.Element, element: ET.Element, calc: Calc) -> None:
    for name, value in element.attrib.items():
        target.set(name, value)
    kids = [child for child in element if isinstance(child.tag, str)]
    if kids:
        _merge_children(document, target, kids, calc)
    elif element.text is not None:
        target.text = element.text


def _merge_children(document: CPatchDoc, target: ET.Element, content: list[ET.Element], calc: Calc) -> None:
    seen: Counter = Counter()
    for element in content:
        if element.tag == "ModItem":
            mod_item(document, target, element, calc)
            continue
        if element.tag == "ModValue":
            target.text = _value_text(document, element, target, calc, target.text)
            continue
        k = seen[element.tag]
        seen[element.tag] += 1
        same = [child for child in document.children(target) if child.tag == element.tag]
        if k < len(same):
            _merge_into(document, same[k], element, calc)
        else:
            new = copy.deepcopy(element)
            resolve(document, new, target, calc)
            document.insert(target, len(target), [new])


def mod_item(document: CPatchDoc, target: ET.Element, element: ET.Element, calc: Calc) -> None:
    """An ``<Item>`` merged by key, or inserted where ``Append`` / ``Prepend`` says (after the last item)."""
    key = element.get("Merge", "")
    items = [child for child in element if isinstance(child.tag, str)]
    found = select(document, f"Item[{item_test(element)}]", target) if key else []
    if found:
        _merge_children(document, found[0], items, calc)
        document.changed(found[0])
        return
    new = ET.Element("Item")
    new.extend(copy.deepcopy(items))
    resolve(document, new, target, calc)
    document.insert(target, _item_place(document, target, element), [new])


def item_test(element: ET.Element) -> str:
    """The test that picks an item by key: ``Merge="a=b"`` as it is, ``Merge="Key"`` by the text of its ``Key`` child."""
    key = element.get("Merge", "")
    if "=" in key:
        return key
    value = (element.findtext(key) or "").strip() if key else ""
    return f"{key}={_quote(value)}"


def _item_place(document: CPatchDoc, target: ET.Element, element: ET.Element) -> int:
    for attribute, after in (("Append", True), ("Prepend", False)):
        where = element.get(attribute)
        if where:
            found = select(document, f"Item[{where}]", target)
            if found:
                return document.index_of(found[0]) + (1 if after else 0)
    items = [k for k, child in enumerate(document.children(target)) if child.tag == "Item"]
    return items[-1] + 1 if items else len(target)


def _quote(text: str) -> str:
    return f"'{text}'" if "'" not in text else f'"{text}"'


def resolve(document: CPatchDoc, element: ET.Element, context, calc: Calc) -> None:
    """Every ``ModValue`` below ``element`` replaced by its result; ``context`` is the node ``element`` stands for (its
    children's are found by name and position), or the node it is inserted into."""
    seen: Counter = Counter()
    for child in list(element):
        if not isinstance(child.tag, str):
            continue
        if child.tag == "ModValue":
            _apply_value(document, element, child, context, calc)
            continue
        k = seen[child.tag]
        seen[child.tag] += 1
        sub = None
        if isinstance(context, ET.Element) and context.tag == element.tag:
            same = [peer for peer in document.children(context) if peer.tag == child.tag]
            sub = same[k] if k < len(same) else None
        resolve(document, child, sub if sub is not None else context, calc)


def _apply_value(document: CPatchDoc, parent: ET.Element, mod_value: ET.Element, context, calc: Calc) -> None:
    index = list(parent).index(mod_value)
    parent.remove(mod_value)
    nodes = None
    if "Insert" in mod_value.attrib:
        found = calc(mod_value.get("Insert"), context)
        if isinstance(found, list) and found and all(isinstance(item, ET.Element) for item in found):
            nodes = [copy.deepcopy(item) for item in found]
            if "SkipParent" in mod_value.attrib:
                nodes = [child for item in nodes for child in item]
    if nodes is not None:
        for k, node in enumerate(nodes):
            parent.insert(index + k, node)
        return
    base = parent.text if parent.text and parent.text.strip() else None
    parent.text = _value_text(document, mod_value, context, calc, base)


def _value_text(document: CPatchDoc, mod_value: ET.Element, context, calc: Calc, base: str | None) -> str:
    """The text a ``ModValue`` gives: an ``Insert`` value, then its ``Merge`` and ``Remove`` flags."""
    text = base
    for attribute, value in mod_value.attrib.items():
        if attribute == "Insert":
            text = format_value(calc(value, context), document)
        elif attribute in ("Merge", "Remove"):
            if text is None:
                text = document.string(context) if isinstance(context, ET.Element) else ""
            have = flags(text)
            names = flags(value)
            have = (
                have + [flag for flag in names if flag not in have]
                if attribute == "Merge"
                else [flag for flag in have if flag not in names]
            )
            text = ";".join(have)
    return text or ""


def fill(content: list[ET.Element], found) -> list[ET.Element]:
    """The operation's content with ``Content=``'s result put in: at each ``ModOpContent``, or as the whole content
    when there is none (nodes copied; ``SkipParent``: their children; a value: text)."""
    nodes = found if isinstance(found, list) else None
    holders = [
        (owner, child) for top in content for owner in top.iter() for child in owner if child.tag == "ModOpContent"
    ]
    tops = [child for child in content if child.tag == "ModOpContent"]
    if not holders and not tops:
        return [copy.deepcopy(item) for item in nodes if isinstance(item, ET.Element)] if nodes is not None else content
    out = []
    for element in content:
        if element.tag == "ModOpContent":
            out += _payload(element, nodes)
        else:
            out.append(element)
    for parent, holder in holders:
        index = list(parent).index(holder)
        parent.remove(holder)
        if nodes is None:
            parent.text = (parent.text or "").strip() + format_value(found)
            continue
        for k, node in enumerate(_payload(holder, nodes)):
            parent.insert(index + k, node)
    return out


def _payload(holder: ET.Element, nodes: list | None) -> list[ET.Element]:
    copies = [copy.deepcopy(node) for node in nodes or () if isinstance(node, ET.Element)]
    return [child for node in copies for child in node] if "SkipParent" in holder.attrib else copies
