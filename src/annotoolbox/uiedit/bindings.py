"""A template's bindings as a graph: each binding's source ends, its operator and its target ends, and the edits
that add, change and remove one with the one index space of its ends kept whole.

The ends are, in one index space, the property containers (a path of UniqueIds from a data object: the widget
itself, a child instance, a list's element or item), then the containers of the widget's own custom properties,
then the constants (docs/formats/phoenix-scene-format.md, "Bindings"). ``read(root)`` is the entry; ``add``,
``remove`` and ``set_operator`` change the tree in place (``uidoc`` records them for undo).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import cached_property

from ..formats import phoenix, phoenixedit
from . import fields, operators

CUSTOM_PROPERTIES, BINDINGS, DATA_OBJECTS = "18b0dff0", "a4336119", "8fda299c"
CONTAINERS, CONSTANTS, OWN = "PropertyContainers", "ConstantPropertyContainers", "CustomPropertyContainers"
SOURCES, TARGETS = "SourcePropertyIndices", "DestinationPropertyIndices"
DATA_PATH, DATA_REF, DATA_INDEX = "15a30137", "b25f63de", "a86f2815"
SELF, CHILD, ITEM, ELEMENT = 1, 0, 3, 4
AXES = {1: "", 2: ".x", 3: ".y", 4: ".z"}
# a list by its 117 name, and the field older layouts keep it in: 1800's and the History Editions' custom properties,
# the History Editions' ends and data objects (phoenix-scene-format.md "The History Editions' files")
LISTS = {CUSTOM_PROPERTIES: (CUSTOM_PROPERTIES, "CustomProperties"), CONTAINERS: (CONTAINERS, "ca35751c"),
         DATA_OBJECTS: (DATA_OBJECTS, "6ab6e607")}  # fmt: skip


def list_name(root: dict, name: str) -> str:
    """The field the file's layout keeps the list ``name`` (117's name of it) in."""
    for candidate in LISTS.get(name, ()):
        if phoenixedit.has_field(root, candidate):
            return candidate
    return name


def objects(root: dict, name: str) -> list[dict]:
    """The objects of the list ``name`` (117's name) in whichever field the file's layout keeps it."""
    return phoenixedit.objects(root, list_name(root, name))


@dataclass
class SEnd:
    """One end of a binding: ``kind`` "path" (a data object and a path of UniqueIds), "constant" or "own" (one of
    the widget's custom properties); ``field`` 1 the whole value, 2 / 3 / 4 its x / y / z."""

    index: int
    kind: str
    node: dict
    steps: list[tuple[int, str | None]] = field(default_factory=list)  # the data object's (mode, instance GUID)
    ids: list[int] = field(default_factory=list)
    field: int = 1
    value: object = None  # a constant's (type code, value); an own property's index
    uses: int = 0

    @cached_property
    def mode(self) -> int | None:
        return self.steps[-1][0] if self.steps else None

    @cached_property
    def instance(self) -> str | None:
        """The instance GUID the path starts at (None: the widget itself, a list's element or item)."""
        return next((guid for step_mode, guid in reversed(self.steps) if step_mode == CHILD and guid), None)


@dataclass
class SBinding:
    node: dict
    operator: str  # the operator's type hash
    sources: list[SEnd]
    targets: list[SEnd]

    @property
    def op_name(self) -> str:
        return operators.name_of(self.operator)


@dataclass
class SGraph:
    ends: list[SEnd]
    bindings: list[SBinding]
    properties: list[dict]  # the template's custom properties

    def touching(self, guid: str | None) -> list[SBinding]:
        """The bindings with an end on the instance ``guid`` (None: every binding)."""
        if guid is None:
            return list(self.bindings)
        return [
            binding
            for binding in self.bindings
            if any(end.instance == guid for end in (*binding.sources, *binding.targets))
        ]


def _steps(dobj: dict) -> list[tuple[int, str | None]]:
    out = []
    for item in phoenixedit.objects(dobj, DATA_PATH):
        ref = fields.get(item, DATA_REF)
        out.append((int(fields.get(item, "DataMode", 0) or 0), ref))
    return out


def read(root: dict) -> SGraph:
    """The bindings of a template (a widget file, a scene, a group template)."""
    dobjs = [_steps(data_object) for data_object in objects(root, DATA_OBJECTS)]
    props = list(custom_properties(root))
    ends: list[SEnd] = []
    for pc in objects(root, CONTAINERS):
        ids, axis = [], 1
        for pid in phoenixedit.objects(pc, "PropertyPath"):
            ids.append(int(fields.get(pid, "UniqueId", 0) or 0))
            axis = int(fields.get(pid, "PropertyField", 1) or 1)
        di = int(fields.get(pc, DATA_INDEX, 0) or 0)
        steps = dobjs[di] if di < len(dobjs) else []
        ends.append(SEnd(len(ends), "path", pc, steps, ids, axis, uses=int(fields.get(pc, "UseCount", 0) or 0)))
    for container in phoenixedit.objects(root, OWN):
        i = int(fields.get(container, "CustomPropertyIndex", 0) or 0)
        uid = int(fields.get(props[i], "UniqueId", 0) or 0) if i < len(props) else 0
        axis = int(fields.get(container, "PropertyField", 1) or 1)
        ends.append(
            SEnd(len(ends), "own", container, [], [uid], axis, i, int(fields.get(container, "UseCount", 0) or 0))
        )
    for container in phoenixedit.objects(root, CONSTANTS):
        ends.append(SEnd(len(ends), "constant", container, value=fields.get(container, "DefaultValue")))
    out = []
    for binding in phoenixedit.objects(root, BINDINGS):
        conv = phoenixedit.objects(binding, "ConverterInstance")
        op = f"{conv[0]['type']:08x}" if conv else operators.ASSIGN
        sources = [ends[i] for i in fields.get(binding, SOURCES, []) or [] if i < len(ends)]
        targets = [ends[i] for i in fields.get(binding, TARGETS, []) or [] if i < len(ends)]
        out.append(SBinding(binding, op, sources, targets))
    return SGraph(ends, out, props)


def custom_properties(root: dict) -> list[dict]:
    """The custom properties a file declares: a widget file's (under the template's field) or an asset's (an effect,
    a component, a widget type: a field of its own of the same name; 1800's and the History Editions' files keep a
    widget file's there too)."""
    return objects(root, CUSTOM_PROPERTIES)


def property_info(prop: dict) -> dict:
    """A custom property as the panels show it: name, UniqueId, type, accessibility, default."""
    return {
        "name": phoenixedit.object_name(prop) or "",
        "uid": int(fields.get(prop, "UniqueId", 0) or 0),
        "type": int(fields.get(prop, "PropertyType", 0) or 0),
        "access": int(fields.get(prop, "PropertyAccessibility", 0) or 0),
        "default": fields.get(prop, "DefaultValue"),
        "template": fields.get(prop, "ObjectTemplate"),
    }


# Building ends


@dataclass(frozen=True)
class SEndSpec:
    """What an end of a new binding is: ``kind`` "path" (``instance``: a child's GUID, None for the widget itself;
    ``ids``: the UniqueIds), "constant" (``value``: (type code, value)) or "own" (``name``: a custom property)."""

    kind: str
    instance: str | None = None
    ids: tuple[int, ...] = ()
    field: int = 1
    value: tuple | None = None
    name: str = ""
    ptype: int = 0


def _stored(node: dict, field: str, value) -> None:
    """Set a field where the object's layout has it (the older layouts lack the use counts, the profile ids, the
    operators' counts)."""
    if phoenixedit.has_field(node, field):
        phoenixedit.set_field(node, field, value)


class CEditor:
    """Adds and removes bindings of one template; ``new(type_hash)`` makes an object in the file's layouts."""

    def __init__(self, root: dict, new) -> None:
        self.m_root, self.m_new = root, new
        self.m_pending: list[int] = []

    def _list(self, name: str) -> list[dict]:
        return list(objects(self.m_root, name))

    def _set_list(self, name: str, objs: list[dict]) -> None:
        phoenixedit.set_objects(self.m_root, list_name(self.m_root, name), objs)

    def _shift(self, start: int, by: int) -> None:
        """Every binding index at or after ``start`` moved by ``by`` (a container inserted before them)."""
        for binding in self._list(BINDINGS):
            for key in (SOURCES, TARGETS):
                indices = fields.get(binding, key, []) or []
                fields.set_value(binding, key, [i + by if i >= start else i for i in indices])
        self.m_pending[:] = [i + by if i >= start else i for i in self.m_pending]  # the ends of the binding being made

    def data_object(self, instance: str | None) -> int:
        """The data object of the widget itself (None) or of a child instance, made when the file has none."""
        objs = self._list(DATA_OBJECTS)
        want = [(SELF, None)] if instance is None else [(CHILD, instance)]
        for i, candidate in enumerate(objs):
            if _steps(candidate) == want:
                return i
        item = self.m_new("7e4fae85")
        if instance is None:
            phoenixedit.set_field(item, "DataMode", SELF)
        else:
            phoenixedit.set_field(item, "DataMode", CHILD)
            phoenixedit.set_raw(item, DATA_REF, phoenix.ref_bytes(phoenixedit.INSTANCE_REF, instance))
        dobj = self.m_new("b0dd1389")
        phoenixedit.set_objects(dobj, DATA_PATH, [item])
        self._set_list(DATA_OBJECTS, [*objs, dobj])
        return len(objs)

    def end(self, spec: SEndSpec) -> int:
        """The index of a new end for ``spec`` (an own property's container reused when one matches)."""
        pcs, consts, own = self._list(CONTAINERS), self._list(CONSTANTS), self._list(OWN)
        if spec.kind == "path":
            pc = self.m_new("1bb72d66")
            ids = []
            for i, uid in enumerate(spec.ids):
                pid = self.m_new("6e5a7f0e")
                phoenixedit.set_field(pid, "UniqueId", uid)
                if i == len(spec.ids) - 1 and spec.field != 1:
                    phoenixedit.set_field(pid, "PropertyField", spec.field)
                ids.append(pid)
            phoenixedit.set_objects(pc, "PropertyPath", ids)
            phoenixedit.set_field(pc, DATA_INDEX, self.data_object(spec.instance))
            _stored(pc, "UseCount", 0)
            self._shift(len(pcs), 1)
            self._set_list(CONTAINERS, [*pcs, pc])
            return len(pcs)
        if spec.kind == "constant":  # the constants come last: nothing after them moves
            container = self.m_new("fb2cf8c8")
            code, value = spec.value
            fields.set_value(container, "DefaultValue", (code, value))
            phoenixedit.set_field(container, "PropertyType", spec.ptype)
            self._set_list(CONSTANTS, [*consts, container])
            return len(pcs) + len(own) + len(consts)
        props = self._list(CUSTOM_PROPERTIES)
        index = next(i for i, prop in enumerate(props) if phoenixedit.object_name(prop) == spec.name)
        for k, container in enumerate(own):
            same = int(fields.get(container, "CustomPropertyIndex", -1)) == index
            if same and int(fields.get(container, "PropertyField", 1) or 1) == spec.field:
                return len(pcs) + k
        container = self.m_new("9df79719")
        phoenixedit.set_field(container, "CustomPropertyIndex", index)
        phoenixedit.set_field(container, "PropertyField", spec.field)
        _stored(container, "UseCount", 0)
        self._shift(len(pcs) + len(own), 1)
        self._set_list(OWN, [*own, container])
        return len(pcs) + len(own)

    def _container(self, index: int) -> dict:
        pcs, consts, own = self._list(CONTAINERS), self._list(CONSTANTS), self._list(OWN)
        return (pcs + own + consts)[index]

    def _use(self, index: int, by: int) -> None:
        container = self._container(index)
        if phoenixedit.get_entry(container, "UseCount") is not None:
            phoenixedit.set_field(container, "UseCount", max(0, int(fields.get(container, "UseCount", 0)) + by))

    def add(self, sources: list[SEndSpec], targets: list[SEndSpec], op: str = operators.ASSIGN) -> dict:
        """A new binding from ``sources`` through the operator ``op`` to ``targets``; returns its object."""
        self.m_pending = []
        for spec in (*sources, *targets):
            self.m_pending.append(self.end(spec))
        source_ends, target_ends = self.m_pending[: len(sources)], self.m_pending[len(sources) :]
        self.m_pending = []
        binding = self.m_new("edcf0433")
        phoenixedit.set_objects(binding, "ConverterInstance", [self._operator(op, len(source_ends), len(target_ends))])
        fields.set_value(binding, SOURCES, source_ends)
        fields.set_value(binding, TARGETS, target_ends)
        seed = f"{len(self._list(BINDINGS))}/{source_ends}/{target_ends}/{op}"
        _stored(binding, "ProfileID", str(uuid.uuid5(uuid.NAMESPACE_OID, seed)))
        for i in (*source_ends, *target_ends):
            self._use(i, 1)
        self._set_list(BINDINGS, [*self._list(BINDINGS), binding])
        return binding

    def _operator(self, op: str, n_in: int, n_out: int) -> dict:
        conv = self.m_new(op)
        _stored(conv, "570c3823", n_in)
        _stored(conv, "3f22df21", n_out)
        _stored(conv, "IsDynamic", n_in > 2)
        if op == "bba41a31":  # TwoWayAssign is the one operator with a binding mode
            phoenixedit.set_field(conv, "BindingMode", 1)
        return conv

    def set_operator(self, binding: dict, op: str) -> None:
        """Run the binding through another operator (its ends kept)."""
        n_in = len(fields.get(binding, SOURCES, []) or [])
        n_out = len(fields.get(binding, TARGETS, []) or [])
        phoenixedit.set_objects(binding, "ConverterInstance", [self._operator(op, n_in, n_out)])

    def remove(self, binding: dict) -> None:
        """Take a binding out; its ends lose a use, and an end no binding uses any more is removed with it."""
        rest = [other for other in self._list(BINDINGS) if other is not binding]
        mine = set(fields.get(binding, SOURCES, []) or []) | set(fields.get(binding, TARGETS, []) or [])
        self._set_list(BINDINGS, rest)
        for i in mine:
            self._use(i, -1)
        used = {i for other in rest for key in (SOURCES, TARGETS) for i in fields.get(other, key, []) or []}
        for i in sorted(mine - used, reverse=True):
            self._drop_end(i)

    def _drop_end(self, index: int) -> None:
        pcs, consts, own = self._list(CONTAINERS), self._list(CONSTANTS), self._list(OWN)
        if index < len(pcs):
            self._set_list(CONTAINERS, pcs[:index] + pcs[index + 1 :])
        elif index < len(pcs) + len(own):
            k = index - len(pcs)
            self._set_list(OWN, own[:k] + own[k + 1 :])
        else:
            k = index - len(pcs) - len(own)
            self._set_list(CONSTANTS, consts[:k] + consts[k + 1 :])
        self._shift(index + 1, -1)
