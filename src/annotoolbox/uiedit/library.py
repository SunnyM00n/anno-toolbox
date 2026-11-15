"""The Phoenix Widget Library and the names of UniqueIds: the studio editor's descriptions the game ships
(``data/ui/pwl/``: components, widget types, effects, enums, game objects) and the custom properties of the shipped
templates, so the editor can name every id a file gives and offer the library as its palette.

``Library.read(tree)`` reads the descriptions; ``Namer`` turns a binding end, a binding path, a property value's path
or a sample key into words.
docs/tool-suite.md section 5, point 3.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import phoenixedit
from ..infra.i18n import tr
from . import bindings, elements, fields

PWL = "data/ui/pwl/"
STUDIO = "data/ui/studio/"  # the History Editions ship the same descriptions beside their files, among others
KINDS = {"components": "Component", "widgettypes": "Widget type", "effects": "Effect", "enums": "Enum",
         "gameobjects": "Game object"}  # fmt: skip
TYPE_NAMES = {  # the descriptions' property type names to the files' numbers (fields.ENUMS["PropertyType"])
    "Boolean": 0, "Int32": 1, "UInt32": 2, "Float32": 3, "String": 4, "Vector2": 5, "Vector3": 6, "Event": 9,
    "Action": 10, "WidgetSibling": 11, "Array": 12, "LocalizationId": 13, "AnyGameObject": 16, "Enum": 17,
    "Color": 18, "ObjectReference": 19, "Spacing": 22, "WidgetChild": 23, "AnyEnum": 17, "Image": 24,
}  # fmt: skip


def plain(text: str) -> str:
    """A description without the editor's markup (``<Bold>`` and the like), line ends unified."""
    return re.sub(r"</?[A-Za-z]+>", "", text or "").replace("\r\n", "\n").strip()


@dataclass(frozen=True)
class PropertyDoc:
    uid: int
    name: str
    ptype: int
    description: str = ""
    default: object = None
    public: bool = True


@dataclass
class Item:
    """One description of the library: a component, widget type, effect, enum or game object template."""

    kind: str  # a value of KINDS
    guid: str
    name: str
    description: str = ""
    folder: str = ""
    parent: str | None = None
    properties: list[PropertyDoc] = field(default_factory=list)
    entries: dict[int, str] = field(default_factory=dict)  # an enum's values


def _property(obj: dict) -> PropertyDoc:
    o = obj.get("Object", obj)
    ptype = TYPE_NAMES.get(str(o.get("PropertyType", "")).removeprefix("CustomPropertyType_"), -1)
    default = (o.get("DefaultValue") or {}).get("Value")
    public = o.get("PropertyAccessibility", "Accessibility_Public").endswith("Public")
    return PropertyDoc(int(o.get("UniqueId", 0)), o.get("PropertyName", ""), ptype, plain(o.get("Description", "")),
                       default, public)  # fmt: skip


def read_item(kind: str, data: dict) -> Item:
    o = data["PhoenixAssetData"]["Object"]
    parent = o.get("ParentComponentTemplate") or o.get("ParentWidgetType") or None
    item = Item(KINDS.get(kind, kind), str(o.get("ObjectID", "")).lower(), o.get("Name", ""),
                plain(o.get("Description", "")), o.get("FolderPath") or "", parent.lower() if parent else None)  # fmt: skip
    item.properties = [_property(p) for p in o.get("CustomProperties", [])]
    for e in o.get("EnumEntries", []):
        e = e.get("Object", e)
        item.entries[int(e.get("UniqueId", 0))] = e.get("EntryName", "")
    return item


class Library:
    """The Widget Library's items by GUID, and every described property by UniqueId."""

    def __init__(self, items: list[Item]) -> None:
        self.items = {i.guid: i for i in items}
        self.by_uid: dict[int, tuple[Item, PropertyDoc]] = {}
        for item in items:
            for p in item.properties:
                self.by_uid.setdefault(p.uid, (item, p))

    @classmethod
    def read(cls, tree) -> Library:
        """The descriptions of a game tree (``sources.gametree.GameTree``); empty for a game that ships none."""
        items = []
        for path in [*tree.under(PWL), *tree.under(STUDIO)]:
            if not path.endswith(".json"):
                continue
            root = PWL if path.startswith(PWL) else STUDIO
            kind = (
                path[len(root) :].removeprefix("data/").split("/", 1)[0]
            )  # the History Editions' library: ``pwl/data/``
            if root == STUDIO and kind not in KINDS:
                continue  # the studio folder holds the files themselves too: widgets, scenes, images
            try:
                items.append(read_item(kind, json.loads(tree.read(path).decode("utf-8-sig"))))
            except (ValueError, KeyError, TypeError):
                continue
        return cls(items)

    def of_kind(self, kind: str) -> list[Item]:
        return sorted((i for i in self.items.values() if i.kind == kind), key=lambda i: (i.folder, i.name))

    def chain(self, guid: str) -> list[Item]:
        """An item and the items it extends, its own first."""
        out, cur = [], self.items.get(guid)
        while cur is not None and cur not in out:
            out.append(cur)
            cur = self.items.get(cur.parent or "")
        return out

    def properties_of(self, guid: str) -> list[PropertyDoc]:
        """A component's or widget type's properties with those of the templates it extends."""
        return [p for item in self.chain(guid) for p in item.properties]

    def enum_named(self, name: str) -> dict[int, str]:
        item = next((i for i in self.items.values() if i.kind == "Enum" and i.name == name), None)
        return dict(item.entries) if item else {}


def template_properties(root: dict) -> dict[int, tuple[str, int]]:
    """UniqueId -> (name, property type) of the custom properties a shipped template declares (a widget file, a
    component, a widget type), from the file itself."""
    out = {}
    for p in bindings.custom_properties(root):
        out[int(fields.get(p, "UniqueId", 0) or 0)] = (
            phoenixedit.object_name(p) or "",
            int(fields.get(p, "PropertyType", 0) or 0),
        )
    return out


class Namer:
    """Words for UniqueIds: built-ins, the library's properties, a template's own properties (``load`` gives the
    decoded root of a file by GUID, or None), the components of an instance, the names a user gave field ids."""

    def __init__(
        self, library: Library, load: Callable[[str], dict | None], names: dict[int, str] | None = None
    ) -> None:
        self.library, self.load = library, load
        self.names = names if names is not None else {}
        self._templates: dict[str, dict[int, tuple[str, int]]] = {}

    def template(self, guid: str | None) -> dict[int, tuple[str, int]]:
        """The custom properties of the file ``guid`` (a widget file, component or widget type), cached."""
        if not guid:
            return {}
        if guid not in self._templates:
            root = self.load(guid)
            props = template_properties(root) if root is not None else {}
            if root is not None:  # a widget file's type adds its properties
                wtype = fields.get(root, "WidgetType")
                for uid, (name, t) in self.template(wtype).items() if wtype and wtype != guid else ():
                    props.setdefault(uid, (name, t))
            for p in self.library.properties_of(guid):
                props.setdefault(p.uid, (p.name, p.ptype))
            self._templates[guid] = props
        return self._templates[guid]

    def uid(self, uid: int) -> str:
        """A lone id: a built-in, a described property, a user's name, else its hex."""
        b = fields.BUILTIN_BY_ID.get(uid)
        if b is not None:
            return b.name
        if uid in self.names:
            return self.names[uid]
        found = self.library.by_uid.get(uid)
        return found[1].name if found else f"<{uid:08x}>"

    def path(self, ids: list[int], instance: dict | None = None, own: dict[int, tuple[str, int]] | None = None) -> str:
        """A path of ids as words: the first on ``instance`` (its components, its template's properties, its
        built-ins) or on the widget's own properties ``own``, the rest as fields of what that holds."""
        if not ids:
            return ""
        words, rest, first = [], list(ids), ids[0]
        if instance is not None:
            comps = {int(fields.get(c, "UniqueId", 0) or 0): c for c in phoenixedit.objects(instance, "Components")}
            if first in comps and len(rest) > 1:
                comp = comps[first]
                tpl = fields.get(comp, "ComponentTemplate")
                words.append(phoenixedit.object_name(comp) or self.uid(first))
                props = self.template(tpl)
                words.append(props.get(rest[1], (self.uid(rest[1]), 0))[0])
                rest = rest[2:]
                return ".".join(words + [self.uid(i) for i in rest])
        tpl = fields.get(instance, "Template") if instance is not None else None
        props = {**(self.template(tpl) if tpl else {}), **(own or {})}
        if first in props:
            words.append(props[first][0])
            rest = rest[1:]
        return ".".join(words + [self.uid(i) for i in rest])

    @staticmethod
    def own(root: dict) -> dict[int, tuple[str, int]]:
        """A file's own properties: UniqueId -> (name, property type)."""
        infos = [bindings.property_info(p) for p in bindings.custom_properties(root)]
        return {i["uid"]: (i["name"], i["type"]) for i in infos}

    def end(self, e: bindings.End, top: elements.Element, own: dict[int, tuple[str, int]]) -> str:
        """A binding end of the file whose element tree is ``top`` (its own properties ``own``) as words."""
        if e.kind == "constant":
            return tr("constant {value}", value=e.value[1] if isinstance(e.value, tuple) else repr(e.value))
        axis = bindings.AXES.get(e.field, "")
        if e.kind == "own":
            return f"own.{own.get(e.ids[0], (f'{e.ids[0]:08x}', 0))[0]}{axis}"
        el = elements.by_guid(top, e.instance) if e.instance else None
        modes = {bindings.SELF: "self", bindings.ITEM: "item", bindings.ELEMENT: "element", 2: "animation"}
        who = el.name if el is not None else modes.get(e.mode, f"mode {e.mode}")
        node = el.node if el is not None else (top.node if e.mode == bindings.SELF else None)
        return f"{who}.{self.path(e.ids, node, own if e.mode == bindings.SELF else None)}{axis}"

    def key(self, key: str, title: Callable[[str], str]) -> str:
        """A sample key (``<file>/<id>/<id>...``) as the file's title and the ids' names."""
        parts = key.split("/")
        ids = [int(p, 16) for p in parts[1:] if len(p) == 8]
        return f"{title(parts[0].split(':')[-1])}: " + ".".join(self.uid(i) for i in ids)
