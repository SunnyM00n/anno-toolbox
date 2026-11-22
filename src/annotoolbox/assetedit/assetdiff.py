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
from ..modloader.patchdoc import CPatchDoc, canonical
from . import modops
from .changeset import SAssetChange
from .values import apply_change, values_of
from .xmltree import to_text

VALUES, BLOCKS, WHOLE = "values", "blocks", "whole"  # the forms, finest first


def asset_change(guid: int, vanilla: ET.Element, patched: ET.Element) -> tuple[SAssetChange, str]:
    """The change that turns ``vanilla`` into ``patched`` and the form it took."""
    for form, make in ((VALUES, _values), (BLOCKS, _blocks)):
        change = make(vanilla, patched)
        if change is not None and reproduces(guid, vanilla, patched, change):
            return change, form
    return SAssetChange(whole=to_text(patched)), WHOLE


def reproduces(guid: int, vanilla: ET.Element, patched: ET.Element, change: SAssetChange) -> bool:
    """Whether ``change`` gives ``patched`` back, in the editor's own model and as ModOps applied to ``vanilla``."""
    want = canonical(patched)
    if canonical(apply_change(vanilla, change)) != want:
        return False
    root = ET.Element("AssetList")
    ET.SubElement(ET.SubElement(ET.SubElement(root, "Groups"), "Group"), "Assets").append(copy.deepcopy(vanilla))
    doc = CPatchDoc.of(root)
    text = "<ModOps>" + "\n".join(modops.change_ops(guid, vanilla, change)) + "</ModOps>"
    if any(finding.level == "warning" for finding in run_text(doc, text)):
        return False
    found = doc.assets(str(guid))
    return len(found) == 1 and canonical(found[0]) == want


def _values(vanilla: ET.Element, patched: ET.Element) -> SAssetChange | None:
    change = SAssetChange()
    return change if _diff(values_of(vanilla), values_of(patched), "", change) else None


def _elements(element: ET.Element) -> list[ET.Element]:
    return [child for child in element if isinstance(child.tag, str)]


def _text(element: ET.Element) -> str:
    return element.text if element.text and element.text.strip() else ""


def _diff(before: ET.Element, after: ET.Element, path: str, change: SAssetChange) -> bool:
    """The differences below ``a`` and ``b`` (at ``Values``-relative ``path``) into ``change``; False when the
    difference is the element at the top itself (no path to put it at)."""
    before_children, after_children = _elements(before), _elements(after)
    before_names, after_names = [child.tag for child in before_children], [child.tag for child in after_children]
    repeated = len(set(before_names)) < len(before_names) or len(set(after_names)) < len(after_names)
    if before.attrib != after.attrib or bool(before_children) != bool(after_children) or repeated:
        return _block(after, path, change)
    if not before_children:
        if _text(before) != _text(after):
            if not path:
                return False
            change.set[path] = after.text or ""
        return True
    for child in before_children:
        if child.tag not in after_names:
            change.reset.append(_join(path, child.tag))
    for child in after_children:
        sub = _join(path, child.tag)
        other = before.find(child.tag)
        if other is None:
            if not len(child) and not child.attrib:
                change.set[sub] = child.text or ""
            else:
                change.blocks[sub] = to_text(child)
        elif not _diff(other, child, sub, change):
            return False
    return True


def _block(element: ET.Element, path: str, change: SAssetChange) -> bool:
    if not path:
        return False
    change.blocks[path] = to_text(element)
    return True


def _join(path: str, name: str) -> str:
    return f"{path}/{name}" if path else name


def _blocks(vanilla: ET.Element, patched: ET.Element) -> SAssetChange | None:
    """Each property that differs replaced whole, each one the mod removed reset."""
    vanilla_blocks, patched_blocks = _elements(values_of(vanilla)), _elements(values_of(patched))
    if len({block.tag for block in vanilla_blocks}) < len(vanilla_blocks) or len(
        {block.tag for block in patched_blocks}
    ) < len(patched_blocks):
        return None
    change, have = SAssetChange(), {block.tag: block for block in vanilla_blocks}
    for block in vanilla_blocks:
        if block.tag not in {patched_block.tag for patched_block in patched_blocks}:
            change.reset.append(block.tag)
    for block in patched_blocks:
        if block.tag not in have or canonical(have[block.tag]) != canonical(block):
            change.blocks[block.tag] = to_text(block)
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
    for text_id, entries in after.items():
        old = before.get(text_id, [])
        if len(entries) > max(1, len(old)):
            lost.append(tr("assets.text_times_change_set_keeps", id=text_id, n=len(entries)))
        entry = entries[-1]
        inner = entry.find("Text")
        extra = [child.tag for child in _elements(entry) if child.tag not in (key, "Text")]
        if old and canonical(old[-1]) == canonical(entry):
            continue
        if extra or inner is not None and len(inner):
            lost.append(
                tr(
                    "assets.text_fields_beside_not_kept",
                    id=text_id,
                    fields=", ".join(extra) or tr("assets.markup"),
                )
            )
        changed[text_id] = (inner.text or "") if inner is not None else ""
    for text_id in before:
        if text_id not in after:
            lost.append(tr("assets.text_removed_change_set_cannot", id=text_id))
    return changed, lost
