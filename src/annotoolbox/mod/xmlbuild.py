"""Small element builders shared by the modules that write assets as XML trees."""

from __future__ import annotations

import xml.etree.ElementTree as ET


def sub(parent: ET.Element, tag: str, text: object = None) -> ET.Element:
    """A child ``tag`` of ``parent`` holding ``text`` (nothing when None)."""
    element = ET.SubElement(parent, tag)
    if text is not None:
        element.text = str(text)
    return element


def el(tag: str, text: object = None, *children: ET.Element) -> ET.Element:
    """An element with ``text`` and ``children``."""
    out = ET.Element(tag)
    if text is not None:
        out.text = str(text)
    out.extend(children)
    return out


def asset(template: str, guid: int, name: str) -> tuple[ET.Element, ET.Element]:
    """An ``Asset`` of ``template`` with its Standard block; returns it and its ``Values``."""
    root = ET.Element("Asset")
    sub(root, "Template", template)
    values = sub(root, "Values")
    std = sub(values, "Standard")
    sub(std, "GUID", guid)
    sub(std, "Name", name)
    return root, values


def items(tag: str, children: list[ET.Element]) -> ET.Element:
    """``<tag>`` holding ``children``."""
    out = ET.Element(tag)
    for child in children:
        out.append(child)
    return out


def item(**fields: object) -> ET.Element:
    """``<Item>`` with one child per field."""
    out = ET.Element("Item")
    for k, value in fields.items():
        ET.SubElement(out, k).text = str(value)
    return out


def add_after(anchor: int, xml: str) -> str:
    """The mod operation that adds ``xml`` (an asset) right after the vanilla asset ``anchor``."""
    return f'<ModOp Type="addNextSibling" GUID="{anchor}">\n{xml}\n</ModOp>\n'


def add_values(guid: int, body: str) -> str:
    """The mod operation that adds ``body`` (one or more blocks) to the ``Values`` of the asset ``guid``."""
    return f'<ModOp Type="add" GUID="{guid}" Path="/Values">\n{body}\n</ModOp>\n'


def int_text(node: ET.Element | None) -> int:
    """The whole number an element's text holds; 0 without an element, a text or a number."""
    try:
        return int((node.text or "0").strip()) if node is not None else 0
    except ValueError:
        return 0
