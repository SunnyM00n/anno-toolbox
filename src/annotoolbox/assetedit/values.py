"""An asset's values in layers, as ToolOne shows them: its own, then its base asset's (``BaseAssetGUID``, up the
chain), then its template's defaults, then the property group's defaults; and a change applied to an asset.

``CLayers`` answers "what is the value at this path, and where does it come from" (``lookup``); ``apply_change``
writes a change (``changeset.SAssetChange``) into a copy of an ``<Asset>``. An empty leaf means "nothing set here"
(ToolOne writes ``<Mesh />`` for a component that only keeps its defaults), so the next layer's value shows.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ..mod.asset117 import set_value
from .xmltree import find_path, parse

OWN, BASE, TEMPLATE, DEFAULT, NONE = "own", "base", "template", "default", "none"


@dataclass
class SLayer:
    origin: str  # OWN, BASE, TEMPLATE, DEFAULT
    values: ET.Element  # ``Values``-shaped: one child per property
    source: int | None = None  # the base asset's GUID


@dataclass
class SFound:
    element: ET.Element | None
    origin: str
    source: int | None = None

    @property
    def text(self) -> str:
        return (self.element.text or "").strip() if self.element is not None else ""


class CLayers:
    """The value layers of one asset, its own first."""

    def __init__(self, layers: list[SLayer]) -> None:
        self.m_layers = layers

    @property
    def own(self) -> ET.Element:
        return self.m_layers[0].values

    def lookup(self, path: tuple[str, ...]) -> SFound:
        """The element at ``path`` from the first layer that sets it: a leaf with text, or a block with children;
        an element that is only present (empty) counts when no later layer has more."""
        first: SFound | None = None
        for layer in self.m_layers:
            element = find_path(layer.values, path)
            if element is None:
                continue
            if len(element) or (element.text or "").strip():
                return SFound(element, layer.origin, layer.source)
            first = first or SFound(element, layer.origin, layer.source)
        return first or SFound(None, NONE)

    def property_names(self) -> list[str]:
        """The asset's properties: its template's in their order, then any other a layer has."""
        names: list[str] = []
        for layer in sorted(self.m_layers, key=lambda layer: layer.origin != TEMPLATE):
            names += [child.tag for child in layer.values if child.tag not in names]
        return names


def values_of(asset: ET.Element) -> ET.Element:
    found = asset.find("Values")
    return found if found is not None else ET.Element("Values")


def template_values(template_props: ET.Element | None) -> ET.Element:
    """A template's ``Properties`` as a ``Values`` layer."""
    out = ET.Element("Values")
    out.extend(copy.deepcopy(list(template_props)) if template_props is not None else [])
    return out


def defaults_layer(defaults: dict[str, ET.Element], names: list[str]) -> ET.Element:
    out = ET.Element("Values")
    out.extend(copy.deepcopy(defaults[name]) for name in names if name in defaults)
    return out


def apply_change(asset: ET.Element, change) -> ET.Element:
    """A copy of ``asset`` with ``change`` written in: the whole asset when the change holds one, then blocks replaced
    (in their place, as a ``replace`` does), values set, values reset (removed)."""
    out = parse(change.whole) if change.whole else copy.deepcopy(asset)
    values = out.find("Values")
    if values is None:
        values = ET.SubElement(out, "Values")
    for path, xml in change.blocks.items():
        old = find_path(values, path)
        holder = find_path(values, path.rsplit("/", 1)[0]) if "/" in path else values
        if old is not None and holder is not None:
            position = list(holder).index(old)
            holder.remove(old)
            holder.insert(position, parse(xml))
        else:
            set_value(values, path, parse(xml))
    for path, text in change.set.items():
        set_value(values, path, text)
    for path in change.reset:
        if find_path(values, path) is not None:  # (set_value would make the missing parents)
            set_value(values, path, None)
    return out
