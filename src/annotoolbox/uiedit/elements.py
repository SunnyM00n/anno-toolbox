"""The element tree of a template: its widget instances and groups (a group's instances live in the template's
``Groups`` and are reached through the group instance), each with its kind, name and GUID.

``tree(root)`` is the entry; an ``Element`` knows the list it sits in (``holder``), so it can be removed, moved or
copied, and a container's ``template`` (the widget file it places). docs/ui/117/widget-types.md, "The four widget kinds".
"""

from __future__ import annotations

import uuid
import zlib
from dataclasses import dataclass, field
from functools import cached_property

from ..formats import phoenixedit
from ..infra.i18n import tr
from . import fields

KINDS = {
    "0f561333": "container",
    "41a404e5": "label",
    "c19f99dd": "image",
    "fa702d0c": "group",
    "8eeb57e6": "video",
    "379e78aa": "shape",
}
TEMPLATE_TYPES = {"da06bd6e", "1795cb2b", "2f208351"}  # widget file, scene, group template


@dataclass(eq=False)
class Element:
    """An instance (or the template itself, ``kind`` "template") in the tree of one file."""

    node: dict
    kind: str
    parent: Element | None = None
    holder: dict | None = None  # the WidgetInstances entry the node sits in
    children: list[Element] = field(default_factory=list)
    given: str | None = None  # the name the editor's document gives an instance the file stores none for

    @property
    def named(self) -> bool:
        """Whether the file gives it a name (``name`` is a display text otherwise)."""
        return bool(phoenixedit.object_name(self.node))

    @property
    def name(self) -> str:
        own = phoenixedit.object_name(self.node) or self.given
        return own or (tr("ui.template") if self.kind == "template" else tr("ui.unnamed"))

    @cached_property
    def guid(self) -> str | None:
        g = self.node.get("guid")
        return str(uuid.UUID(bytes_le=g)) if g else None

    @cached_property
    def template(self) -> str | None:
        """The widget file a container places, the group template a group instance shows."""
        if self.kind == "container":
            return fields.get(self.node, "Template")
        if self.kind == "group":
            return fields.get(self.node, "GroupTemplate")
        return None

    def path(self) -> list[Element]:
        out, cur = [], self
        while cur is not None:
            out.append(cur)
            cur = cur.parent
        return out[::-1]

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def kind_of(node: dict) -> str:
    return KINDS.get(node["schema_type"], "template" if node["schema_type"] in TEMPLATE_TYPES else "object")


def groups_of(root: dict) -> dict[str, dict]:
    """Every group template of the file by GUID (groups nest in groups)."""
    out: dict[str, dict] = {}

    def visit(node: dict) -> None:
        for g in phoenixedit.objects(node, "Groups"):
            if g.get("guid"):
                out[str(uuid.UUID(bytes_le=g["guid"]))] = g
            visit(g)

    visit(root)
    return out


def tree(root: dict, names: dict[str, str] | None = None) -> Element:
    """The file's elements: the template, its instances, and under each group instance its group's instances;
    ``names`` (instance GUID -> name) names those the file stores no name for (``uifiles.UiFiles.names_of``)."""
    groups = groups_of(root)
    top = Element(root, "template")
    seen: set[int] = set()

    def fill(owner: Element, holder_node: dict) -> None:
        entry = phoenixedit.get_entry(holder_node, "WidgetInstances")
        for inst in entry["objs"] if entry else []:
            el = Element(inst, kind_of(inst), owner, entry)
            el.given = _given(inst, names.get(el.guid or "")) if names else None
            owner.children.append(el)
            group = groups.get(el.template or "") if el.kind == "group" else None
            if group is not None and id(group) not in seen:
                seen.add(id(group))
                fill(el, group)

    fill(top, root)
    return top


def _given(node: dict, name: str | None) -> str | None:
    """A document's name for an instance, unless the instance was renamed since (its ``NameCrc`` no longer matches)."""
    crc = fields.get(node, "NameCrc") if name else None
    return name if crc is None or int(crc) & 0xFFFFFFFF == zlib.crc32(name.encode("utf-8")) else None


def animations(root: dict) -> list[dict]:
    """The template's animation instances (their tracks target instances by GUID)."""
    return list(phoenixedit.objects(root, "AnimationInstances"))


def find(top: Element, node: dict) -> Element | None:
    return next((e for e in top.walk() if e.node is node), None)


def crc_key(el: Element) -> str | None:
    """The key of an instance by its name CRC: a running game's ``guid`` is a run-time handle, the file's ``NameCrc``
    is what both share (names are unique within a file)."""
    crc = fields.get(el.node, "NameCrc")
    return crc_name(int(crc)) if crc else None


def crc_name(crc: int) -> str:
    """``crc:<name CRC>``, the one spelling of that key (the file's instances, a dump's and a live answer's widgets)."""
    return f"crc:{crc & 0xFFFFFFFF:08x}"


def keys(el: Element) -> tuple[str, ...]:
    """The keys a running game's widget is matched to an instance by: its GUID, then its name CRC."""
    return tuple(k for k in (el.guid, crc_key(el)) if k)


def held_for(held: dict, el: Element):
    """What ``held`` (live values or texts by instance key) has for an instance: by GUID, else by name CRC; None."""
    return next((held[k] for k in keys(el) if k in held), None) if held else None


def by_guid(top: Element, guid: str) -> Element | None:
    return next((e for e in top.walk() if e.guid == guid), None)


def by_node_id(top: Element | None, node_id: int) -> Element | None:
    """The element whose node has this ``id()``: what the preview's signals carry (a node is no signal argument)."""
    return next((e for e in top.walk() if id(e.node) == node_id), None) if top is not None else None
