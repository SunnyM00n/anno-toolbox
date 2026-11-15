"""What an operation's content does to its target: ``merge`` (by name and position, the leaf style, ``ModItem``
lists by key), ``ModValue`` (a node or a value calculated, flags merged or removed) and ``ModOpContent`` (nodes taken
from the document). Stdlib only.

``merge(doc, target, content, calc)`` is the entry for a merge, ``resolve(doc, element, context, calc)`` puts the
``ModValue`` results into content about to be inserted, ``fill(content, found)`` the nodes of ``Content=``; ``calc``
evaluates an expression (its variables replaced) at a node. The rules: ``docs/engine-modloader.md`` ("Merge,
exactly", "ModValue and ModItem").
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable

from .patchdoc import PatchDoc
from .xpath import format_value, select

Calc = Callable[[str, object], object]  # (expression, context node) -> XPath value
SPECIAL = frozenset({"ModValue", "ModItem", "ModOpContent", "ModValueContent"})


def flags(text: str) -> list[str]:
    return [f.strip() for f in (text or "").split(";") if f.strip()]


def merge(doc: PatchDoc, target: ET.Element, content: list[ET.Element], calc: Calc) -> None:
    """``content`` merged into ``target``: into the target itself when its one element has the target's name."""
    if len(content) == 1 and content[0].tag == target.tag:
        _merge_into(doc, target, content[0], calc)
    else:
        _merge_children(doc, target, content, calc)
    doc.changed(target)


def _merge_into(doc: PatchDoc, target: ET.Element, el: ET.Element, calc: Calc) -> None:
    for k, v in el.attrib.items():
        target.set(k, v)
    kids = [c for c in el if isinstance(c.tag, str)]
    if kids:
        _merge_children(doc, target, kids, calc)
    elif el.text is not None:
        target.text = el.text


def _merge_children(doc: PatchDoc, target: ET.Element, content: list[ET.Element], calc: Calc) -> None:
    seen: Counter = Counter()
    for el in content:
        if el.tag == "ModItem":
            mod_item(doc, target, el, calc)
            continue
        if el.tag == "ModValue":
            target.text = _value_text(doc, el, target, calc, target.text)
            continue
        k = seen[el.tag]
        seen[el.tag] += 1
        same = [c for c in doc.children(target) if c.tag == el.tag]
        if k < len(same):
            _merge_into(doc, same[k], el, calc)
        else:
            new = copy.deepcopy(el)
            resolve(doc, new, target, calc)
            doc.insert(target, len(target), [new])


def mod_item(doc: PatchDoc, target: ET.Element, el: ET.Element, calc: Calc) -> None:
    """An ``<Item>`` merged by key, or inserted where ``Append`` / ``Prepend`` says (after the last item)."""
    key = el.get("Merge", "")
    items = [c for c in el if isinstance(c.tag, str)]
    found = select(doc, f"Item[{item_test(el)}]", target) if key else []
    if found:
        _merge_children(doc, found[0], items, calc)
        doc.changed(found[0])
        return
    new = ET.Element("Item")
    new.extend(copy.deepcopy(items))
    resolve(doc, new, target, calc)
    doc.insert(target, _item_place(doc, target, el), [new])


def item_test(el: ET.Element) -> str:
    """The test that picks an item by key: ``Merge="a=b"`` as it is, ``Merge="Key"`` by the text of its ``Key`` child."""
    key = el.get("Merge", "")
    if "=" in key:
        return key
    value = (el.findtext(key) or "").strip() if key else ""
    return f"{key}={_quote(value)}"


def _item_place(doc: PatchDoc, target: ET.Element, el: ET.Element) -> int:
    for attr, after in (("Append", True), ("Prepend", False)):
        where = el.get(attr)
        if where:
            found = select(doc, f"Item[{where}]", target)
            if found:
                return doc.index_of(found[0]) + (1 if after else 0)
    items = [k for k, c in enumerate(doc.children(target)) if c.tag == "Item"]
    return items[-1] + 1 if items else len(target)


def _quote(text: str) -> str:
    return f"'{text}'" if "'" not in text else f'"{text}"'


def resolve(doc: PatchDoc, el: ET.Element, context, calc: Calc) -> None:
    """Every ``ModValue`` below ``el`` replaced by its result; ``context`` is the node ``el`` stands for (its
    children's are found by name and position), or the node it is inserted into."""
    seen: Counter = Counter()
    for child in list(el):
        if not isinstance(child.tag, str):
            continue
        if child.tag == "ModValue":
            _apply_value(doc, el, child, context, calc)
            continue
        k = seen[child.tag]
        seen[child.tag] += 1
        sub = None
        if isinstance(context, ET.Element) and context.tag == el.tag:
            same = [c for c in doc.children(context) if c.tag == child.tag]
            sub = same[k] if k < len(same) else None
        resolve(doc, child, sub if sub is not None else context, calc)


def _apply_value(doc: PatchDoc, parent: ET.Element, mv: ET.Element, context, calc: Calc) -> None:
    at = list(parent).index(mv)
    parent.remove(mv)
    nodes = None
    if "Insert" in mv.attrib:
        found = calc(mv.get("Insert"), context)
        if isinstance(found, list) and found and all(isinstance(n, ET.Element) for n in found):
            nodes = [copy.deepcopy(n) for n in found]
            if "SkipParent" in mv.attrib:
                nodes = [c for n in nodes for c in n]
    if nodes is not None:
        for k, node in enumerate(nodes):
            parent.insert(at + k, node)
        return
    base = parent.text if parent.text and parent.text.strip() else None
    parent.text = _value_text(doc, mv, context, calc, base)


def _value_text(doc: PatchDoc, mv: ET.Element, context, calc: Calc, base: str | None) -> str:
    """The text a ``ModValue`` gives: an ``Insert`` value, then its ``Merge`` and ``Remove`` flags."""
    text = base
    for attr, value in mv.attrib.items():
        if attr == "Insert":
            text = format_value(calc(value, context), doc)
        elif attr in ("Merge", "Remove"):
            if text is None:
                text = doc.string(context) if isinstance(context, ET.Element) else ""
            have = flags(text)
            names = flags(value)
            have = (
                have + [f for f in names if f not in have] if attr == "Merge" else [f for f in have if f not in names]
            )
            text = ";".join(have)
    return text or ""


def fill(content: list[ET.Element], found) -> list[ET.Element]:
    """The operation's content with ``Content=``'s result put in: at each ``ModOpContent``, or as the whole content
    when there is none (nodes copied; ``SkipParent``: their children; a value: text)."""
    nodes = found if isinstance(found, list) else None
    holders = [(p, c) for top in content for p in top.iter() for c in p if c.tag == "ModOpContent"]
    tops = [c for c in content if c.tag == "ModOpContent"]
    if not holders and not tops:
        return [copy.deepcopy(n) for n in nodes if isinstance(n, ET.Element)] if nodes is not None else content
    out = []
    for el in content:
        if el.tag == "ModOpContent":
            out += _payload(el, nodes)
        else:
            out.append(el)
    for parent, holder in holders:
        at = list(parent).index(holder)
        parent.remove(holder)
        if nodes is None:
            parent.text = (parent.text or "").strip() + format_value(found)
            continue
        for k, node in enumerate(_payload(holder, nodes)):
            parent.insert(at + k, node)
    return out


def _payload(holder: ET.Element, nodes: list | None) -> list[ET.Element]:
    copies = [copy.deepcopy(n) for n in nodes or () if isinstance(n, ET.Element)]
    return [c for n in copies for c in n] if "SkipParent" in holder.attrib else copies
