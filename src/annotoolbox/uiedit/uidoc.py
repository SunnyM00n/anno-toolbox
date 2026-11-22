"""The open UI file: its bytes, its decoded tree, its edits as commands on the suite's undo stack, and its bytes
again - the original ones while nothing changed, the codec's when something did.

``CUiDoc(files, key, data)`` is the entry; every edit (``set_field``, ``add_instance``, ``add_binding`` ...) records
the objects it touches and puts them back on undo. New objects take the file's own layout versions, else the newest
the game's files use (``uifiles.CUiFiles.m_versions``). docs/tool-suite.md section 20, "Editing" and "Writing".
"""

from __future__ import annotations

import uuid
import zlib
from collections.abc import Callable

from ..formats import phoenix, phoenixedit, phoenixread, phoenixwrite
from ..infra.i18n import tr
from ..suite.undo import CUndoStack, SCommand
from . import animations, bindings, elements, fields

INSTANCE_TYPES = {"container": "0f561333", "image": "c19f99dd", "label": "41a404e5", "video": "8eeb57e6"}
_LISTS = (bindings.BINDINGS, bindings.CONTAINERS, bindings.OWN, bindings.CONSTANTS, bindings.DATA_OBJECTS)


def _snapshot(node: dict) -> tuple:
    return node["mask"], node["n"], [{**entry, "objs": list(entry["objs"])} for entry in node["fields"]]


def _restore(node: dict, snap: tuple) -> None:
    node["mask"], node["n"] = snap[0], snap[1]
    node["fields"] = [{**entry, "objs": list(entry["objs"])} for entry in snap[2]]


class CUiDoc:
    """One Phoenix file being edited; ``key`` is its name in the generated folder (a GUID or a scene's name)."""

    def __init__(self, files, key: str, data: bytes, origin: str = "game") -> None:
        self.m_files, self.m_key, self.m_origin = files, key, origin
        self.m_schema = phoenix.full_schema(files.m_schema)
        self.m_original = data
        self.m_decoder = phoenixread.load(data, files.m_schema)
        self.m_root = self.m_decoder.m_root
        self.m_exact = phoenixwrite.save(self.m_decoder) == data  # whether an edit leaves the rest byte for byte
        self.m_undo = CUndoStack()
        self.m_counter = 0  # makes the GUIDs of new instances distinct and stable
        self.m_listeners: list[Callable[[], None]] = []
        self.m_undo.m_listeners.append(self._changed)

    @property
    def kind(self) -> str:
        return self.m_original[3:5].decode("latin1")

    @property
    def dirty(self) -> bool:
        return self.m_undo.dirty

    def _changed(self) -> None:
        for listener in list(self.m_listeners):
            listener()

    # bytes

    def to_bytes(self) -> bytes:
        """The file: its original bytes when no edit stands, else the tree encoded."""
        return self.m_original if self.m_undo.m_index == 0 else phoenixwrite.save(self.m_decoder)

    # new objects

    def versions(self) -> dict[str, str]:
        return {**self.m_files.m_versions, **self.m_decoder.m_guid_of}

    def register(self, node: dict) -> None:
        """Make the types of a new object known to the file's type table, in the layout it was built in."""
        versions = self.versions()
        for walked in phoenixedit.walk(node):  # the chain as these layouts have it: no record the game's files lack
            for record in [
                *phoenix.type_chain(self.m_schema, walked["schema_type"], versions),
                f"{walked['type']:08x}",
            ]:
                if record not in self.m_decoder.m_guid_of:
                    guid = versions.get(record) or self.m_schema.get(record, {}).get("guid")
                    if guid:
                        self.m_decoder.m_guid_of[record] = guid

    def new(self, type_hash: str, guid: bytes | None = None) -> dict:
        node = phoenixedit.new_object(self.m_schema, type_hash, guid=guid, versions=self.versions())
        self.register(node)
        return node

    def fresh_guid(self, salt: str = "") -> bytes:
        self.m_counter += 1
        return uuid.uuid5(
            uuid.NAMESPACE_OID, f"{self.m_key}/{salt}/{self.m_counter}/{len(self.m_undo.m_commands)}"
        ).bytes_le

    # the one way an edit is made

    def change(self, label: str, nodes: list[dict], edit: Callable[[], object]):
        """Run ``edit``, which changes only ``nodes`` (their fields, lists and masks); undo puts them back."""
        before = [(node, _snapshot(node)) for node in nodes]
        try:
            result = edit()
        except Exception:
            for node, snap in before:
                _restore(node, snap)
            raise
        after = [(node, _snapshot(node)) for node in nodes]
        self.m_undo.push(SCommand(label, lambda: [_restore(node, snapshot) for node, snapshot in before],
                               lambda: [_restore(node, snapshot) for node, snapshot in after]))  # fmt: skip
        return result

    def binding_nodes(self) -> list[dict]:
        """The root and every object a binding edit may touch (the lists of ends, their objects)."""
        return [self.m_root, *(item for name in _LISTS for item in phoenixedit.objects(self.m_root, name))]

    # fields

    def set_field(self, node: dict, field: str, value) -> None:
        self.change(f"set {field}", [node], lambda: fields.set_value(node, field, value))

    def set_color(self, node: dict, field: str, rgb, palette: str | None = None, entry: int = 0) -> None:
        objs = phoenixedit.objects(node, field)
        self.change(f"set {field}", [node, *objs, *(color for color_object in objs for color in phoenixedit.objects(color_object, "Color"))],
                    lambda: fields.set_color(node, field, rgb, palette, entry))  # fmt: skip

    def set_property_value(self, instance: dict, path: list[int], code: int, value) -> None:
        """An instance's own value of a template's or component's property (``path`` of UniqueIds)."""
        for property_value in phoenixedit.objects(instance, "PropertyValues"):
            if list(fields.get(property_value, "PropertyIdPath", []) or []) == list(path):
                self.change(
                    "set a property value",
                    [property_value],
                    lambda property_value=property_value: fields.set_value(property_value, "Value", (code, value)),
                )
                return

        def add() -> None:
            property_value = self.new("2df5f3e4")
            phoenixedit.set_field(property_value, "PropertyIdPath", list(path))
            fields.set_value(property_value, "Value", (code, value))
            phoenixedit.set_objects(
                instance, "PropertyValues", [*phoenixedit.objects(instance, "PropertyValues"), property_value]
            )

        self.change("add a property value", [instance], add)

    # instances

    def holder_of(self, parent: elements.SElement) -> dict:
        """The object whose ``WidgetInstances`` a new child of ``parent`` goes into (a group's: its template)."""
        if parent.kind == "template":
            return parent.node
        if parent.kind == "group":
            group = elements.groups_of(self.m_root).get(parent.template or "")
            if group is not None:
                return group
        return self._holder_node(parent)  # beside an image, a label or a container

    def _insert(self, holder: dict, node: dict, after: dict | None = None) -> None:
        objs = list(phoenixedit.objects(holder, "WidgetInstances"))
        at = next((i + 1 for i, item in enumerate(objs) if item is after), len(objs))
        phoenixedit.set_objects(holder, "WidgetInstances", [*objs[:at], node, *objs[at:]])

    def add_instance(self, parent: elements.SElement, node: dict, name: str) -> dict:
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
            prototype = next(
                (element.node for element in elements.tree(decoder.m_root).walk() if element.kind == kind), None
            )
            if prototype is None:
                continue
            self.m_counter += 1
            copy = phoenixedit.clone_object(prototype, fresh=True, salt=f"{self.m_key}/new/{self.m_counter}")
            try:
                phoenixedit.adopt(self.m_decoder, decoder, copy)
            except ValueError:  # stored in another layout version than this file's: build it instead
                continue
            phoenixedit.set_objects(copy, "PropertyValues", [])
            fields.set_value(copy, "Position", (0.0, 0.0, 0.0))
            return copy
        return self.new(INSTANCE_TYPES[kind], guid=self.fresh_guid(name))

    def _prototype_files(self, kind: str):
        yield self.m_decoder
        key = self.m_files.m_prototypes.get(kind)
        if key is None:
            for entry in self.m_files.of_kind("WI"):
                root = self.m_files.root(entry.key)
                if root is not None and any(element.kind == kind for element in elements.tree(root).walk()):
                    key = self.m_files.m_prototypes[kind] = entry.key
                    break
        if key is not None:
            yield self.m_files.load(key)

    def duplicate(self, element: elements.SElement) -> dict:
        holder = self._holder_node(element)
        self.m_counter += 1
        copy = phoenixedit.clone_object(element.node, fresh=True, salt=f"{self.m_key}/copy/{self.m_counter}")
        self.register(copy)

        def add() -> None:
            phoenixedit.set_name(copy, f"{element.name}_copy")
            self._insert(holder, copy, after=element.node)

        self.change(f"duplicate {element.name}", [holder], add)
        return copy

    def _holder_node(self, element: elements.SElement) -> dict:
        """The object whose instance list holds ``el`` (the template, or a group's template)."""
        for node in [self.m_root, *elements.groups_of(self.m_root).values()]:
            if any(item is element.node for item in phoenixedit.objects(node, "WidgetInstances")):
                return node
        raise KeyError(tr("ui.not_files_instance_lists", name=element.name))

    def remove(self, element: elements.SElement) -> int:
        """Take an instance out, with the bindings that name it; returns how many bindings went."""
        holder = self._holder_node(element)
        graph = bindings.read(self.m_root)
        gone = [binding.node for binding in graph.touching(element.guid)] if element.guid else []

        def cut() -> None:
            editor = bindings.CEditor(self.m_root, self.new)
            for binding in gone:
                editor.remove(binding)
            phoenixedit.set_objects(holder, "WidgetInstances",
                                    [item for item in phoenixedit.objects(holder, "WidgetInstances") if item is not element.node])  # fmt: skip

        self.change(f"remove {element.name}", [holder, *self.binding_nodes()], cut)
        return len(gone)

    def move(self, node: dict, position: tuple[float, float, float]) -> None:
        self.set_field(node, "Position", tuple(float(component) for component in position))

    # components and effects

    def add_component(self, node: dict, template: str, name: str) -> dict:
        self.m_counter += 1
        unique_id = phoenixedit.unique_id(0x70000000, self.m_key, template, name, str(self.m_counter))
        component = phoenixedit.component(self.m_schema, template, name, unique_id, self.versions())
        self.register(component)
        self.change(f"add {name}", [node],
                    lambda: phoenixedit.set_objects(node, "Components", [*phoenixedit.objects(node, "Components"), component]))  # fmt: skip
        return component

    def add_effect(self, node: dict, template: str, name: str) -> dict:
        self.m_counter += 1
        effect = self.new("15a03701")
        phoenixedit.set_field(
            effect, "UniqueId", phoenixedit.unique_id(0x70000000, self.m_key, "fx", template, str(self.m_counter))
        )
        phoenixedit.set_raw(effect, "EffectTemplate", phoenix.ref_bytes("c0f0995c", template))
        phoenixedit.set_field(effect, phoenixedit.NAME_FIELD, name)
        self.change(f"add {name}", [node],
                    lambda: phoenixedit.set_objects(node, "Effects", [*phoenixedit.objects(node, "Effects"), effect]))  # fmt: skip
        return effect

    def remove_object(self, node: dict, field: str, item: dict) -> None:
        """Take a component or an effect off an instance."""
        self.change(
            f"remove from {field}",
            [node],
            lambda: phoenixedit.set_objects(
                node, field, [other for other in phoenixedit.objects(node, field) if other is not item]
            ),
        )

    # animation keys (the sequence editor)

    def _key_type(self, track: dict) -> str:
        for field in self.m_schema.get(f"{track['type']:08x}", {}).get("fields", []):
            if field["name"] == "Track" and field.get("elem"):
                return field["elem"]
        raise KeyError(tr("ui.holds_no_keys", type=track["schema_type"]))

    def _write_key(self, track: dict, key: dict, time: float | None, value, interpolator: int | None) -> None:
        if time is not None:
            fields.set_value(key, "Time", max(0.0, float(time)))
        if interpolator is not None:
            fields.set_value(key, animations.INTERPOLATOR, int(interpolator))
        if value is not None:
            if phoenixedit.objects(key, "Value"):  # a colour key
                fields.set_color(key, "Value", tuple(int(component) for component in value)[:3])
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
                                     *(color for color_object in phoenixedit.objects(key, "Value") for color in phoenixedit.objects(color_object, "Color"))],
                    lambda: self._write_key(track, key, time, value, interpolator))  # fmt: skip

    def remove_key(self, track: dict, key: dict) -> None:
        self.change("remove a key", [track],
                    lambda: phoenixedit.set_objects(track, "Track", [k for k in phoenixedit.objects(track, "Track") if k is not key]))  # fmt: skip

    # bindings and custom properties

    def add_binding(self, sources: list[bindings.SEndSpec], targets: list[bindings.SEndSpec], op: str) -> dict:
        return self.change("add a binding", self.binding_nodes(),
                           lambda: bindings.CEditor(self.m_root, self.new).add(sources, targets, op))  # fmt: skip

    def remove_binding(self, binding: dict) -> None:
        self.change(
            "remove a binding", self.binding_nodes(), lambda: bindings.CEditor(self.m_root, self.new).remove(binding)
        )

    def set_operator(self, binding: dict, op: str) -> None:
        self.change("change an operator", [binding],
                    lambda: bindings.CEditor(self.m_root, self.new).set_operator(binding, op))  # fmt: skip

    def add_property(self, name: str, property_type: int, code: int = 0, default=None) -> int:
        """A public custom property of the template (``ptype`` the files' number, ``code`` its value's type code)."""
        unique_id = phoenixedit.unique_id(0x10000000, self.m_key, name)

        def add() -> None:
            property_node = self.new("1294a820")
            phoenixedit.set_field(property_node, "UniqueId", unique_id)
            phoenixedit.set_field(property_node, "PropertyType", property_type)
            if code:
                fields.set_value(property_node, "DefaultValue", (code, default))
            phoenixedit.set_field(property_node, "PropertyNameCrc", zlib.crc32(name.encode("utf-8")))
            phoenixedit.set_field(property_node, phoenixedit.NAME_FIELD, name)
            listed = bindings.list_name(self.m_root, bindings.CUSTOM_PROPERTIES)  # 117's field, or the older games'
            phoenixedit.set_objects(self.m_root, listed, [*bindings.custom_properties(self.m_root), property_node])

        self.change(f"add the property {name}", [self.m_root], add)
        return unique_id


def empty_widget_file(files, guid: str) -> bytes:
    """The bytes of an empty widget file named ``guid``, its objects in the layout versions the game's files use."""
    schema = phoenix.full_schema(files.m_schema)
    root = phoenixedit.new_object(schema, phoenixedit.WIDGET_TEMPLATE, guid=uuid.UUID(guid).bytes_le, root=True,
                                  versions=files.m_versions)  # fmt: skip
    guid_of: dict[str, str] = {}
    for node in phoenixedit.walk(root):
        for record in [*phoenix.type_chain(schema, node["schema_type"], files.m_versions), f"{node['type']:08x}"]:
            guid_of.setdefault(record, files.m_versions.get(record) or schema[record]["guid"])
    return phoenixwrite.write_file(schema, "WI", root, guid_of)
