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
class End:
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
        return next((g for m, g in reversed(self.steps) if m == CHILD and g), None)


@dataclass
class Binding:
    node: dict
    operator: str  # the operator's type hash
    sources: list[End]
    targets: list[End]

    @property
    def op_name(self) -> str:
        return operators.name_of(self.operator)


@dataclass
class Graph:
    ends: list[End]
    bindings: list[Binding]
    properties: list[dict]  # the template's custom properties

    def touching(self, guid: str | None) -> list[Binding]:
        """The bindings with an end on the instance ``guid`` (None: every binding)."""
        if guid is None:
            return list(self.bindings)
        return [b for b in self.bindings if any(e.instance == guid for e in (*b.sources, *b.targets))]


def _steps(dobj: dict) -> list[tuple[int, str | None]]:
    out = []
    for item in phoenixedit.objects(dobj, DATA_PATH):
        ref = fields.get(item, DATA_REF)
        out.append((int(fields.get(item, "DataMode", 0) or 0), ref))
    return out


def read(root: dict) -> Graph:
    """The bindings of a template (a widget file, a scene, a group template)."""
    dobjs = [_steps(d) for d in objects(root, DATA_OBJECTS)]
    props = list(custom_properties(root))
    ends: list[End] = []
    for pc in objects(root, CONTAINERS):
        ids, axis = [], 1
        for pid in phoenixedit.objects(pc, "PropertyPath"):
            ids.append(int(fields.get(pid, "UniqueId", 0) or 0))
            axis = int(fields.get(pid, "PropertyField", 1) or 1)
        di = int(fields.get(pc, DATA_INDEX, 0) or 0)
        steps = dobjs[di] if di < len(dobjs) else []
        ends.append(End(len(ends), "path", pc, steps, ids, axis, uses=int(fields.get(pc, "UseCount", 0) or 0)))
    for c in phoenixedit.objects(root, OWN):
        i = int(fields.get(c, "CustomPropertyIndex", 0) or 0)
        uid = int(fields.get(props[i], "UniqueId", 0) or 0) if i < len(props) else 0
        axis = int(fields.get(c, "PropertyField", 1) or 1)
        ends.append(End(len(ends), "own", c, [], [uid], axis, i, int(fields.get(c, "UseCount", 0) or 0)))
    for c in phoenixedit.objects(root, CONSTANTS):
        ends.append(End(len(ends), "constant", c, value=fields.get(c, "DefaultValue")))
    out = []
    for b in phoenixedit.objects(root, BINDINGS):
        conv = phoenixedit.objects(b, "ConverterInstance")
        op = f"{conv[0]['type']:08x}" if conv else operators.ASSIGN
        src = [ends[i] for i in fields.get(b, SOURCES, []) or [] if i < len(ends)]
        dst = [ends[i] for i in fields.get(b, TARGETS, []) or [] if i < len(ends)]
        out.append(Binding(b, op, src, dst))
    return Graph(ends, out, props)


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
class EndSpec:
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


class Editor:
    """Adds and removes bindings of one template; ``new(type_hash)`` makes an object in the file's layouts."""

    def __init__(self, root: dict, new) -> None:
        self.root, self.new = root, new
        self.pending: list[int] = []

    def _list(self, name: str) -> list[dict]:
        return list(objects(self.root, name))

    def _set_list(self, name: str, objs: list[dict]) -> None:
        phoenixedit.set_objects(self.root, list_name(self.root, name), objs)

    def _shift(self, start: int, by: int) -> None:
        """Every binding index at or after ``start`` moved by ``by`` (a container inserted before them)."""
        for b in self._list(BINDINGS):
            for key in (SOURCES, TARGETS):
                idx = fields.get(b, key, []) or []
                fields.set_value(b, key, [i + by if i >= start else i for i in idx])
        self.pending[:] = [i + by if i >= start else i for i in self.pending]  # the ends of the binding being made

    def data_object(self, instance: str | None) -> int:
        """The data object of the widget itself (None) or of a child instance, made when the file has none."""
        objs = self._list(DATA_OBJECTS)
        want = [(SELF, None)] if instance is None else [(CHILD, instance)]
        for i, d in enumerate(objs):
            if _steps(d) == want:
                return i
        item = self.new("7e4fae85")
        if instance is None:
            phoenixedit.set_field(item, "DataMode", SELF)
        else:
            phoenixedit.set_field(item, "DataMode", CHILD)
            phoenixedit.set_raw(item, DATA_REF, phoenix.ref_bytes(phoenixedit.INSTANCE_REF, instance))
        dobj = self.new("b0dd1389")
        phoenixedit.set_objects(dobj, DATA_PATH, [item])
        self._set_list(DATA_OBJECTS, [*objs, dobj])
        return len(objs)

    def end(self, spec: EndSpec) -> int:
        """The index of a new end for ``spec`` (an own property's container reused when one matches)."""
        pcs, consts, own = self._list(CONTAINERS), self._list(CONSTANTS), self._list(OWN)
        if spec.kind == "path":
            pc = self.new("1bb72d66")
            ids = []
            for i, uid in enumerate(spec.ids):
                pid = self.new("6e5a7f0e")
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
            c = self.new("fb2cf8c8")
            code, value = spec.value
            fields.set_value(c, "DefaultValue", (code, value))
            phoenixedit.set_field(c, "PropertyType", spec.ptype)
            self._set_list(CONSTANTS, [*consts, c])
            return len(pcs) + len(own) + len(consts)
        props = self._list(CUSTOM_PROPERTIES)
        index = next(i for i, p in enumerate(props) if phoenixedit.object_name(p) == spec.name)
        for k, c in enumerate(own):
            same = int(fields.get(c, "CustomPropertyIndex", -1)) == index
            if same and int(fields.get(c, "PropertyField", 1) or 1) == spec.field:
                return len(pcs) + k
        c = self.new("9df79719")
        phoenixedit.set_field(c, "CustomPropertyIndex", index)
        phoenixedit.set_field(c, "PropertyField", spec.field)
        _stored(c, "UseCount", 0)
        self._shift(len(pcs) + len(own), 1)
        self._set_list(OWN, [*own, c])
        return len(pcs) + len(own)

    def _container(self, index: int) -> dict:
        pcs, consts, own = self._list(CONTAINERS), self._list(CONSTANTS), self._list(OWN)
        return (pcs + own + consts)[index]

    def _use(self, index: int, by: int) -> None:
        c = self._container(index)
        if phoenixedit.get_entry(c, "UseCount") is not None:
            phoenixedit.set_field(c, "UseCount", max(0, int(fields.get(c, "UseCount", 0)) + by))

    def add(self, sources: list[EndSpec], targets: list[EndSpec], op: str = operators.ASSIGN) -> dict:
        """A new binding from ``sources`` through the operator ``op`` to ``targets``; returns its object."""
        self.pending = []
        for spec in (*sources, *targets):
            self.pending.append(self.end(spec))
        src, dst = self.pending[: len(sources)], self.pending[len(sources) :]
        self.pending = []
        b = self.new("edcf0433")
        phoenixedit.set_objects(b, "ConverterInstance", [self._operator(op, len(src), len(dst))])
        fields.set_value(b, SOURCES, src)
        fields.set_value(b, TARGETS, dst)
        seed = f"{len(self._list(BINDINGS))}/{src}/{dst}/{op}"
        _stored(b, "ProfileID", str(uuid.uuid5(uuid.NAMESPACE_OID, seed)))
        for i in (*src, *dst):
            self._use(i, 1)
        self._set_list(BINDINGS, [*self._list(BINDINGS), b])
        return b

    def _operator(self, op: str, n_in: int, n_out: int) -> dict:
        conv = self.new(op)
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
        rest = [b for b in self._list(BINDINGS) if b is not binding]
        mine = set(fields.get(binding, SOURCES, []) or []) | set(fields.get(binding, TARGETS, []) or [])
        self._set_list(BINDINGS, rest)
        for i in mine:
            self._use(i, -1)
        used = {i for b in rest for key in (SOURCES, TARGETS) for i in fields.get(b, key, []) or []}
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
