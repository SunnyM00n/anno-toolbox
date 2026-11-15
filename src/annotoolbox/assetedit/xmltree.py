"""The small XML helpers the asset editor shares: parse a game file or a slice of one, walk the nested groups of an
asset, template, property or data set file, read a child's text, a flag or a number. Stdlib only.

The games' ToolOne files share one shape (``docs/tool-suite.md`` section 2): groups nest under ``Groups`` (assets,
properties) or directly (templates, data sets), and each group may carry a ``Name``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator

from ..mod.modxml import close_tags

TRUE = ("1", "true", "True", "TRUE")


def parse(data: bytes | str) -> ET.Element:
    """An element from a game file's bytes or text: the BOM dropped, Anno's ``</>`` shorthand closed."""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    text = text.lstrip("﻿")
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return ET.fromstring(close_tags(text))


def text(el: ET.Element | None, child: str, default: str = "") -> str:
    """The stripped text of ``child`` of ``el`` (a path is allowed), ``default`` when absent or empty."""
    if el is None:
        return default
    found = el.findtext(child)
    return found.strip() if found and found.strip() else default


def flag(el: ET.Element | None, child: str) -> bool:
    return text(el, child) in TRUE


def number(el: ET.Element | None, child: str) -> float | None:
    """The number in ``child``, None when absent or no number."""
    raw = text(el, child)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def walk_groups(root: ET.Element, path: tuple[str, ...] = ()) -> Iterator[tuple[ET.Element, tuple[str, ...]]]:
    """(group, group path) for every group under ``root``, outer groups first; ``root`` itself when it is a group."""
    if root.tag == "Group":
        name = text(root, "Name")
        path = path + (name,) if name else path
        yield root, path
    for sub in [*root.findall("Group"), *root.findall("Groups/Group")]:
        yield from walk_groups(sub, path)


def leaves(el: ET.Element, prefix: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str]]:
    """(path, text) of every leaf below ``el``, in document order (the k-th repeated name is a separate path)."""
    for child in el:
        here = prefix + (child.tag,)
        if len(child):
            yield from leaves(child, here)
        else:
            yield here, (child.text or "").strip()


def find_path(el: ET.Element | None, path: tuple[str, ...] | str) -> ET.Element | None:
    """The first element at ``path`` (names, or a "/" string) below ``el``; None when a step is missing."""
    parts = path.split("/") if isinstance(path, str) else path
    node = el
    for name in parts:
        if node is None:
            return None
        node = node.find(name)
    return node


def to_text(el: ET.Element) -> str:
    """An element as compact text, the way the writers put it into a file."""
    return ET.tostring(el, encoding="unicode", short_empty_elements=True)
