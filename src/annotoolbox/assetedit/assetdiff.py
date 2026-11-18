"""The difference between a game asset and the same asset as a mod left it, in the change set's terms, and the same
for a language's texts. Stdlib only.

``asset_change(guid, vanilla, patched)`` tries the finest form first (values set and reset, blocks where a name
repeats), then a block per property, then the whole asset, and keeps the first that gives the patched asset back
both ways the editor writes it (``values.apply_change`` and the ModOps of ``modops.change_ops`` run by the
loader's rules); ``text_changes`` compares two text files. ``docs/tool-suite.md`` section 17.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

from ..infra.i18n import tr
from ..modloader.engine import run_text
from ..modloader.patchdoc import PatchDoc, canonical
from . import modops
from .changeset import AssetChange
from .values import apply_change, values_of
from .xmltree import to_text

VALUES, BLOCKS, WHOLE = "values", "blocks", "whole"  # the forms, finest first


def asset_change(guid: int, vanilla: ET.Element, patched: ET.Element) -> tuple[AssetChange, str]:
    """The change that turns ``vanilla`` into ``patched`` and the form it took."""
    for form, make in ((VALUES, _values), (BLOCKS, _blocks)):
        change = make(vanilla, patched)
        if change is not None and reproduces(guid, vanilla, patched, change):
            return change, form
    return AssetChange(whole=to_text(patched)), WHOLE


def reproduces(guid: int, vanilla: ET.Element, patched: ET.Element, change: AssetChange) -> bool:
    """Whether ``change`` gives ``patched`` back, in the editor's own model and as ModOps applied to ``vanilla``."""
    want = canonical(patched)
    if canonical(apply_change(vanilla, change)) != want:
        return False
    root = ET.Element("AssetList")
    ET.SubElement(ET.SubElement(ET.SubElement(root, "Groups"), "Group"), "Assets").append(copy.deepcopy(vanilla))
    doc = PatchDoc.of(root)
    text = "<ModOps>" + "\n".join(modops.change_ops(guid, vanilla, change)) + "</ModOps>"
    if any(e.level == "warning" for e in run_text(doc, text)):
        return False
    found = doc.assets(str(guid))
    return len(found) == 1 and canonical(found[0]) == want


def _values(vanilla: ET.Element, patched: ET.Element) -> AssetChange | None:
    change = AssetChange()
    return change if _diff(values_of(vanilla), values_of(patched), "", change) else None


def _elements(el: ET.Element) -> list[ET.Element]:
    return [c for c in el if isinstance(c.tag, str)]


def _text(el: ET.Element) -> str:
    return el.text if el.text and el.text.strip() else ""


def _diff(a: ET.Element, b: ET.Element, path: str, change: AssetChange) -> bool:
    """The differences below ``a`` and ``b`` (at ``Values``-relative ``path``) into ``change``; False when the
    difference is the element at the top itself (no path to put it at)."""
    ka, kb = _elements(a), _elements(b)
    names_a, names_b = [c.tag for c in ka], [c.tag for c in kb]
    repeated = len(set(names_a)) < len(names_a) or len(set(names_b)) < len(names_b)
    if a.attrib != b.attrib or bool(ka) != bool(kb) or repeated:
        return _block(b, path, change)
    if not ka:
        if _text(a) != _text(b):
            if not path:
                return False
            change.set[path] = b.text or ""
        return True
    for c in ka:
        if c.tag not in names_b:
            change.reset.append(_join(path, c.tag))
    for c in kb:
        sub = _join(path, c.tag)
        other = a.find(c.tag)
        if other is None:
            if not len(c) and not c.attrib:
                change.set[sub] = c.text or ""
            else:
                change.blocks[sub] = to_text(c)
        elif not _diff(other, c, sub, change):
            return False
    return True


def _block(el: ET.Element, path: str, change: AssetChange) -> bool:
    if not path:
        return False
    change.blocks[path] = to_text(el)
    return True


def _join(path: str, name: str) -> str:
    return f"{path}/{name}" if path else name


def _blocks(vanilla: ET.Element, patched: ET.Element) -> AssetChange | None:
    """Each property that differs replaced whole, each one the mod removed reset."""
    va, pa = _elements(values_of(vanilla)), _elements(values_of(patched))
    if len({c.tag for c in va}) < len(va) or len({c.tag for c in pa}) < len(pa):
        return None
    change, have = AssetChange(), {c.tag: c for c in va}
    for c in va:
        if c.tag not in {p.tag for p in pa}:
            change.reset.append(c.tag)
    for c in pa:
        if c.tag not in have or canonical(have[c.tag]) != canonical(c):
            change.blocks[c.tag] = to_text(c)
    return change


def text_entries(root: ET.Element | None, key: str) -> dict[str, list[ET.Element]]:
    """Text id -> its entries (``Text`` elements under ``TextExport/Texts``)."""
    out: dict[str, list[ET.Element]] = {}
    for texts in root.iter("Texts") if root is not None else ():
        for entry in texts:
            if entry.tag == "Text":
                out.setdefault((entry.findtext(key) or "").strip(), []).append(entry)
    return out


def text_changes(vanilla: ET.Element | None, patched: ET.Element, key: str) -> tuple[dict[str, str], list[str]]:
    """The texts that differ or are new (id -> text) and what the change set cannot hold (one line each)."""
    before, after = text_entries(vanilla, key), text_entries(patched, key)
    changed, lost = {}, []
    for tid, entries in after.items():
        old = before.get(tid, [])
        if len(entries) > max(1, len(old)):
            lost.append(tr("assets.text_times_change_set_keeps", id=tid, n=len(entries)))
        entry = entries[-1]
        inner = entry.find("Text")
        extra = [c.tag for c in _elements(entry) if c.tag not in (key, "Text")]
        if old and canonical(old[-1]) == canonical(entry):
            continue
        if extra or inner is not None and len(inner):
            lost.append(
                tr(
                    "assets.text_fields_beside_not_kept",
                    id=tid,
                    fields=", ".join(extra) or tr("assets.markup"),
                )
            )
        changed[tid] = (inner.text or "") if inner is not None else ""
    for tid in before:
        if tid not in after:
            lost.append(tr("assets.text_removed_change_set_cannot", id=tid))
    return changed, lost
