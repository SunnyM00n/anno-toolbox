"""The open UI file: its bytes, its decoded tree, its edits as commands on the suite's undo stack, and its bytes
again - the original ones while nothing changed, the codec's when something did.

``UiDoc(files, key, data)`` is the entry; every edit (``set_field``, ``add_instance``, ``add_binding`` ...) records
the objects it touches and puts them back on undo. New objects take the file's own layout versions, else the newest
the game's files use (``uifiles.UiFiles.versions``). docs/tool-suite.md section 20, "Editing" and "Writing".
"""

from __future__ import annotations

import uuid
import zlib
from collections.abc import Callable

from ..formats import phoenix, phoenixedit, phoenixread, phoenixwrite
from ..infra.i18n import tr
from ..suite.undo import Command, UndoStack
from . import animations, bindings, elements, fields

INSTANCE_TYPES = {"container": "0f561333", "image": "c19f99dd", "label": "41a404e5", "video": "8eeb57e6"}
_LISTS = (bindings.BINDINGS, bindings.CONTAINERS, bindings.OWN, bindings.CONSTANTS, bindings.DATA_OBJECTS)


def _snapshot(node: dict) -> tuple:
    return node["mask"], node["n"], [{**e, "objs": list(e["objs"])} for e in node["fields"]]


def _restore(node: dict, snap: tuple) -> None:
    node["mask"], node["n"] = snap[0], snap[1]
    node["fields"] = [{**e, "objs": list(e["objs"])} for e in snap[2]]


class UiDoc:
    """One Phoenix file being edited; ``key`` is its name in the generated folder (a GUID or a scene's name)."""

    def __init__(self, files, key: str, data: bytes, origin: str = "game") -> None:
        self.files, self.key, self.origin = files, key, origin
        self.schema = phoenix.full_schema(files.schema)
        self.original = data
        self.decoder = phoenixread.load(data, files.schema)
        self.root = self.decoder.root
        self.exact = phoenixwrite.save(self.decoder) == data  # whether an edit leaves the rest byte for byte
        self.undo = UndoStack()
        self.counter = 0  # makes the GUIDs of new instances distinct and stable
        self.listeners: list[Callable[[], None]] = []
        self.undo.listeners.append(self._changed)

    @property
    def kind(self) -> str:
        return self.original[3:5].decode("latin1")

    @property
    def dirty(self) -> bool:
        return self.undo.dirty

    def _changed(self) -> None:
        for f in list(self.listeners):
            f()

    # bytes

    def to_bytes(self) -> bytes:
        """The file: its original bytes when no edit stands, else the tree encoded."""
        return self.original if self.undo.index == 0 else phoenixwrite.save(self.decoder)

    # new objects

    def versions(self) -> dict[str, str]:
        return {**self.files.versions, **self.decoder.guid_of}

    def register(self, node: dict) -> None:
        """Make the types of a new object known to the file's type table, in the layout it was built in."""
        versions = self.versions()
        for n in phoenixedit.walk(node):  # the chain as these layouts have it: no record the game's files lack
            for rec in [*phoenix.type_chain(self.schema, n["schema_type"], versions), f"{n['type']:08x}"]:
                if rec not in self.decoder.guid_of:
                    guid = versions.get(rec) or self.schema.get(rec, {}).get("guid")
                    if guid:
                        self.decoder.guid_of[rec] = guid

    def new(self, type_hash: str, guid: bytes | None = None) -> dict:
        node = phoenixedit.new_object(self.schema, type_hash, guid=guid, versions=self.versions())
        self.register(node)
        return node

    def fresh_guid(self, salt: str = "") -> bytes:
        self.counter += 1
        return uuid.uuid5(uuid.NAMESPACE_OID, f"{self.key}/{salt}/{self.counter}/{len(self.undo.commands)}").bytes_le

    # the one way an edit is made

    def change(self, label: str, nodes: list[dict], edit: Callable[[], object]):
        """Run ``edit``, which changes only ``nodes`` (their fields, lists and masks); undo puts them back."""
        before = [(n, _snapshot(n)) for n in nodes]
        try:
            result = edit()
        except Exception:
            for n, snap in before:
                _restore(n, snap)
            raise
        after = [(n, _snapshot(n)) for n in nodes]
        self.undo.push(Command(label, lambda: [_restore(n, s) for n, s in before],
                               lambda: [_restore(n, s) for n, s in after]))  # fmt: skip
        return result

    def binding_nodes(self) -> list[dict]:
        """The root and every object a binding edit may touch (the lists of ends, their objects)."""
        return [self.root, *(o for name in _LISTS for o in phoenixedit.objects(self.root, name))]

    # fields

    def set_field(self, node: dict, field: str, value) -> None:
        self.change(f"set {field}", [node], lambda: fields.set_value(node, field, value))

    def set_color(self, node: dict, field: str, rgb, palette: str | None = None, entry: int = 0) -> None:
        objs = phoenixedit.objects(node, field)
        self.change(f"set {field}", [node, *objs, *(c for o in objs for c in phoenixedit.objects(o, "Color"))],
                    lambda: fields.set_color(node, field, rgb, palette, entry))  # fmt: skip

    def set_property_value(self, instance: dict, path: list[int], code: int, value) -> None:
        """An instance's own value of a template's or component's property (``path`` of UniqueIds)."""
        for pv in phoenixedit.objects(instance, "PropertyValues"):
            if list(fields.get(pv, "PropertyIdPath", []) or []) == list(path):
                self.change("set a property value", [pv], lambda pv=pv: fields.set_value(pv, "Value", (code, value)))
                return

        def add() -> None:
            pv = self.new("2df5f3e4")
            phoenixedit.set_field(pv, "PropertyIdPath", list(path))
            fields.set_value(pv, "Value", (code, value))
            phoenixedit.set_objects(instance, "PropertyValues", [*phoenixedit.objects(instance, "PropertyValues"), pv])

        self.change("add a property value", [instance], add)

    # instances

    def holder_of(self, parent: elements.Element) -> dict:
        """The object whose ``WidgetInstances`` a new child of ``parent`` goes into (a group's: its template)."""
        if parent.kind == "template":
            return parent.node
        if parent.kind == "group":
            group = elements.groups_of(self.root).get(parent.template or "")
            if group is not None:
                return group
        return self._holder_node(parent)  # beside an image, a label or a container

    def _insert(self, holder: dict, node: dict, after: dict | None = None) -> None:
        objs = list(phoenixedit.objects(holder, "WidgetInstances"))
        at = next((i + 1 for i, o in enumerate(objs) if o is after), len(objs))
        phoenixedit.set_objects(holder, "WidgetInstances", [*objs[:at], node, *objs[at:]])

    def add_instance(self, parent: elements.Element, node: dict, name: str) -> dict:
        """Put a new instance (built or cloned elsewhere) under ``parent`` with a fresh GUID and ``name``."""
        holder = self.holder_of(parent)
        node["guid"] = node.get("guid") or self.fresh_guid(name)
        node["flags"] = 2
        self.register(node)

        def add() -> None:
            phoenixedit.set_name(node, name)
            self._insert(holder, node)

        self.change(f"add {name}", [holder], add)
        return node

    def new_instance(self, kind: str, name: str) -> dict:
        """A new instance of a kind: a copy of one the game ships (a label keeps its text style that way), else one
        with the values most shipped instances carry (``phoenixedit.new_object``)."""
        for decoder in self._prototype_files(kind):
            proto = next((e.node for e in elements.tree(decoder.root).walk() if e.kind == kind), None)
            if proto is None:
                continue
            self.counter += 1
            copy = phoenixedit.clone_object(proto, fresh=True, salt=f"{self.key}/new/{self.counter}")
            try:
                phoenixedit.adopt(self.decoder, decoder, copy)
            except ValueError:  # stored in another layout version than this file's: build it instead
                continue
            phoenixedit.set_objects(copy, "PropertyValues", [])
            fields.set_value(copy, "Position", (0.0, 0.0, 0.0))
            return copy
        return self.new(INSTANCE_TYPES[kind], guid=self.fresh_guid(name))

    def _prototype_files(self, kind: str):
        yield self.decoder
        key = self.files.prototypes.get(kind)
        if key is None:
            for entry in self.files.of_kind("WI"):
                root = self.files.root(entry.key)
                if root is not None and any(e.kind == kind for e in elements.tree(root).walk()):
                    key = self.files.prototypes[kind] = entry.key
                    break
        if key is not None:
            yield self.files.load(key)

    def duplicate(self, el: elements.Element) -> dict:
        holder = self._holder_node(el)
        self.counter += 1
        copy = phoenixedit.clone_object(el.node, fresh=True, salt=f"{self.key}/copy/{self.counter}")
        self.register(copy)

        def add() -> None:
            phoenixedit.set_name(copy, f"{el.name}_copy")
            self._insert(holder, copy, after=el.node)

        self.change(f"duplicate {el.name}", [holder], add)
        return copy

    def _holder_node(self, el: elements.Element) -> dict:
        """The object whose instance list holds ``el`` (the template, or a group's template)."""
        for n in [self.root, *elements.groups_of(self.root).values()]:
            if any(o is el.node for o in phoenixedit.objects(n, "WidgetInstances")):
                return n
        raise KeyError(tr("ui.not_files_instance_lists", name=el.name))

    def remove(self, el: elements.Element) -> int:
        """Take an instance out, with the bindings that name it; returns how many bindings went."""
        holder = self._holder_node(el)
        graph = bindings.read(self.root)
        gone = [b.node for b in graph.touching(el.guid)] if el.guid else []

        def cut() -> None:
            ed = bindings.Editor(self.root, self.new)
            for b in gone:
                ed.remove(b)
            phoenixedit.set_objects(holder, "WidgetInstances",
                                    [o for o in phoenixedit.objects(holder, "WidgetInstances") if o is not el.node])  # fmt: skip

        self.change(f"remove {el.name}", [holder, *self.binding_nodes()], cut)
        return len(gone)

    def move(self, node: dict, position: tuple[float, float, float]) -> None:
        self.set_field(node, "Position", tuple(float(v) for v in position))

    # components and effects

    def add_component(self, node: dict, template: str, name: str) -> dict:
        self.counter += 1
        uid = phoenixedit.unique_id(0x70000000, self.key, template, name, str(self.counter))
        comp = phoenixedit.component(self.schema, template, name, uid, self.versions())
        self.register(comp)
        self.change(f"add {name}", [node],
                    lambda: phoenixedit.set_objects(node, "Components", [*phoenixedit.objects(node, "Components"), comp]))  # fmt: skip
        return comp

    def add_effect(self, node: dict, template: str, name: str) -> dict:
        self.counter += 1
        eff = self.new("15a03701")
        phoenixedit.set_field(
            eff, "UniqueId", phoenixedit.unique_id(0x70000000, self.key, "fx", template, str(self.counter))
        )
        phoenixedit.set_raw(eff, "EffectTemplate", phoenix.ref_bytes("c0f0995c", template))
        phoenixedit.set_field(eff, phoenixedit.NAME_FIELD, name)
        self.change(f"add {name}", [node],
                    lambda: phoenixedit.set_objects(node, "Effects", [*phoenixedit.objects(node, "Effects"), eff]))  # fmt: skip
        return eff

    def remove_object(self, node: dict, field: str, obj: dict) -> None:
        """Take a component or an effect off an instance."""
        self.change(f"remove from {field}", [node],
                    lambda: phoenixedit.set_objects(node, field, [o for o in phoenixedit.objects(node, field) if o is not obj]))  # fmt: skip

    # animation keys (the sequence editor)

    def _key_type(self, track: dict) -> str:
        for f in self.schema.get(f"{track['type']:08x}", {}).get("fields", []):
            if f["name"] == "Track" and f.get("elem"):
                return f["elem"]
        raise KeyError(tr("ui.holds_no_keys", type=track["schema_type"]))

    def _write_key(self, track: dict, key: dict, time: float | None, value, interpolator: int | None) -> None:
        if time is not None:
            fields.set_value(key, "Time", max(0.0, float(time)))
        if interpolator is not None:
            fields.set_value(key, animations.INTERPOLATOR, int(interpolator))
        if value is not None:
            if phoenixedit.objects(key, "Value"):  # a colour key
                fields.set_color(key, "Value", tuple(int(v) for v in value)[:3])
            else:
                fields.set_value(key, "Value", value)
        keys = sorted(phoenixedit.objects(track, "Track"), key=lambda k: float(fields.get(k, "Time", 0.0) or 0.0))
        phoenixedit.set_objects(track, "Track", keys)

    def add_key(self, track: dict, time: float, value, interpolator: int = 1) -> dict:
        """A new key on ``track`` at ``time`` seconds (the keys stay in time order)."""
        key = self.new(self._key_type(track))

        def add() -> None:
            phoenixedit.set_objects(track, "Track", [*phoenixedit.objects(track, "Track"), key])
            self._write_key(track, key, time, value, interpolator)

        self.change("add a key", [track, key, *phoenixedit.objects(key, "Value")], add)
        return key

    def set_key(self, track: dict, key: dict, time: float | None = None, value=None, interpolator: int | None = None):
        """Move a key in time, give it a value or another interpolator."""
        self.change("change a key", [track, key, *phoenixedit.objects(key, "Value"),
                                     *(c for o in phoenixedit.objects(key, "Value") for c in phoenixedit.objects(o, "Color"))],
                    lambda: self._write_key(track, key, time, value, interpolator))  # fmt: skip

    def remove_key(self, track: dict, key: dict) -> None:
        self.change("remove a key", [track],
                    lambda: phoenixedit.set_objects(track, "Track", [k for k in phoenixedit.objects(track, "Track") if k is not key]))  # fmt: skip

    # bindings and custom properties

    def add_binding(self, sources: list[bindings.EndSpec], targets: list[bindings.EndSpec], op: str) -> dict:
        return self.change("add a binding", self.binding_nodes(),
                           lambda: bindings.Editor(self.root, self.new).add(sources, targets, op))  # fmt: skip

    def remove_binding(self, binding: dict) -> None:
        self.change(
            "remove a binding", self.binding_nodes(), lambda: bindings.Editor(self.root, self.new).remove(binding)
        )

    def set_operator(self, binding: dict, op: str) -> None:
        self.change("change an operator", [binding],
                    lambda: bindings.Editor(self.root, self.new).set_operator(binding, op))  # fmt: skip

    def add_property(self, name: str, ptype: int, code: int = 0, default=None) -> int:
        """A public custom property of the template (``ptype`` the files' number, ``code`` its value's type code)."""
        uid = phoenixedit.unique_id(0x10000000, self.key, name)

        def add() -> None:
            p = self.new("1294a820")
            phoenixedit.set_field(p, "UniqueId", uid)
            phoenixedit.set_field(p, "PropertyType", ptype)
            if code:
                fields.set_value(p, "DefaultValue", (code, default))
            phoenixedit.set_field(p, "PropertyNameCrc", zlib.crc32(name.encode("utf-8")))
            phoenixedit.set_field(p, phoenixedit.NAME_FIELD, name)
            listed = bindings.list_name(self.root, bindings.CUSTOM_PROPERTIES)  # 117's field, or the older games'
            phoenixedit.set_objects(self.root, listed, [*bindings.custom_properties(self.root), p])

        self.change(f"add the property {name}", [self.root], add)
        return uid


def empty_widget_file(files, guid: str) -> bytes:
    """The bytes of an empty widget file named ``guid``, its objects in the layout versions the game's files use."""
    schema = phoenix.full_schema(files.schema)
    root = phoenixedit.new_object(schema, phoenixedit.WIDGET_TEMPLATE, guid=uuid.UUID(guid).bytes_le, root=True,
                                  versions=files.versions)  # fmt: skip
    guid_of: dict[str, str] = {}
    for n in phoenixedit.walk(root):
        for rec in [*phoenix.type_chain(schema, n["schema_type"], files.versions), f"{n['type']:08x}"]:
            guid_of.setdefault(rec, files.versions.get(rec) or schema[rec]["guid"])
    return phoenixwrite.write_file(schema, "WI", root, guid_of)
