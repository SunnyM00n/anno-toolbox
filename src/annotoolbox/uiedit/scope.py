"""The bindings of one placed template evaluated over its inputs and the sample values: its own properties, what it
writes into its instances, and what it reads from them - their stored fields, their computed sizes, the outputs of
the widget files they place and, in a list, the element an item stands for.

``Scope(key, root, inputs, ctx, owner_key, host)`` evaluates; ``host`` (``layout.Placer``) answers what needs
placing: a child's size and the outputs of a child's widget file. docs/ui-editor.md "The preview".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from . import animations, bindings, elements, fields, operators, placeholders
from .viewmodels import Samples

SIZE = 0x80000004
DATA_SOURCE, DATA_ITEM = 0x30000001, 0x30000002  # a list's array; the element (or its index) an item gets
COMPUTED = (SIZE, 0x80000037)  # read from the placed widget, not from its stored fields
PASSES = 12  # a value one binding writes may be another one's source: passes until nothing changes, at most
ALL = ("child", "*")  # in ``Scope._plan``: what reads a computed size, which every write to a child may change
OWN_TYPES = {0: "bool", 1: "int", 2: "int", 3: "float", 4: "string", 18: "color"}  # PropertyType -> value type
BLANK = "<a game object holding no given value>"  # ``canon``


@dataclass(frozen=True)
class GameObjectRef:
    """A game object as the preview knows it: the sample key its fields are read under."""

    key: str


@dataclass(frozen=True)
class ListSlot:
    """The element a list's item stands for: its game object and its index in the array."""

    element: GameObjectRef
    index: int
    item: str | None  # the GUID of the item instance (the list file's one child)


class Host(Protocol):
    def size_of(self, node: dict, scope: Scope) -> tuple[float, float]: ...

    def outputs(self, node: dict, scope: Scope) -> dict[int, object]: ...


def axis(value, field: int):
    """One part of a value: ``field`` 1 the whole, 2 / 3 / 4 x / y / z."""
    if field == 1 or not isinstance(value, (tuple, list)):
        return value
    return value[field - 2] if field - 2 < len(value) else None


def with_axis(old, field: int, value, size: int = 3):
    """``old`` with one part (or the whole, ``field`` 1) replaced by ``value``."""
    if field == 1:
        return value
    if isinstance(value, (tuple, list)):  # a whole vector into one axis: that axis of it
        value = value[field - 2] if field - 2 < len(value) else (value[0] if value else 0.0)
    base = list(old) if isinstance(old, (tuple, list)) else [0.0] * size
    while len(base) < field - 1:
        base.append(0.0)
    base[field - 2] = value
    return tuple(base)


def builtin_value(node: dict, uid: int):
    """The stored value behind a built-in property of an instance (its field of the same name)."""
    b = fields.BUILTIN_BY_ID.get(uid)
    if b is None or b.kind != "property":
        return None
    return fields.get(node, b.name)


def inputs_of(overrides: dict) -> dict[int, object]:
    """The values an instance passes into its widget file's own properties (the ids below the built-ins)."""
    return {p[0]: v for p, v in overrides.items() if len(p) == 1 and p[0] < 0x80000000}


@dataclass
class RootInfo:
    """What placing derives from one file alone, once: its element tree, bindings and custom properties."""

    root: dict
    top: elements.Element
    graph: bindings.Graph
    by_node: dict[int, elements.Element]
    children: dict[str, dict]  # instance GUID -> node
    props: list[dict]  # bindings.property_info of each custom property, its widget type chain's after its own
    seeds: dict[str, object] | None = None  # the file's own preview values for lists (listlayout.preview_values)
    atoms: dict[int, int] | None = None  # AtomPropertyId -> UniqueId of those properties (the History Editions)
    types: tuple[tuple[str, int], ...] = ()  # the widget type files read for them: (key, id of the tree)


def root_info(ctx, root: dict) -> RootInfo:
    """The ``RootInfo`` of ``root``, kept in ``ctx.roots`` until the window forgets the file or one of its widget
    types (an edit)."""
    hit = ctx.roots.get(id(root))
    if hit is not None and hit.root is root:
        for key, tree in hit.types:  # what reads them depends on the widget types too
            ctx.memo.note(tree)
            ctx.memo.note(("file", key))
        return hit
    top = elements.tree(root)
    by_node = {id(e.node): e for e in top.walk()}
    graph = bindings.read(root)
    types, inherited = widget_types(ctx, root)
    props = [*graph.properties, *inherited]
    infos, seen = [], set()
    for p in props:  # a widget's own declaration of a property wins over its type's
        info = bindings.property_info(p)
        if info["uid"] not in seen:
            seen.add(info["uid"])
            infos.append(info)
    atoms: dict[int, int] = {}
    for p in props:  # and its own of an atom id over its type's, as above
        atoms.setdefault(int(fields.get(p, "AtomPropertyId", 0) or 0), int(fields.get(p, "UniqueId", 0) or 0))
    atoms.pop(0, None)
    info = RootInfo(root, top, graph, by_node, {e.guid: e.node for e in by_node.values() if e.guid}, infos,
                    atoms=atoms, types=types)  # fmt: skip
    ctx.roots[id(root)] = info
    return info


def widget_types(ctx, root: dict) -> tuple[tuple[tuple[str, int], ...], list[dict]]:
    """The widget type files of a widget file (its ``WidgetType`` and that type's parents) and the custom properties
    they declare, which every widget of the type has as its own (bindings read them by their UniqueId)."""
    types, props, key = [], [], fields.get(root, "WidgetType")
    while isinstance(key, str) and key not in {k for k, _ in types}:
        tree = ctx.root(key)
        if tree is None:
            break
        types.append((key, id(tree)))
        props += bindings.custom_properties(tree)
        key = fields.get(tree, "ParentWidgetType")
    return tuple(types), props


def canon(samples: Samples, element: GameObjectRef) -> object:
    """A list's element as a part of what is kept: one under which no value is given stands for any such, since every
    value read under it is made up the same way (the elements of a list the game fills share their bindings;
    docs/tool-suite.md section 20, "Speed")."""
    return BLANK if samples.blank(element.key) else element


def inputs_key(samples: Samples, inputs: dict[int, object]) -> str:
    """What a file is given, as a part of what is kept: a game object in it holding no given value as any such
    (``canon``), so the elements of a list share what they pass into their widget files too."""
    shared = ((k, canon(samples, v) if isinstance(v, GameObjectRef) else v) for k, v in inputs.items())
    return repr(sorted(shared, key=lambda kv: kv[0]))


class Scope:
    """One template being placed: its own property values, what its bindings write into its instances."""

    def __init__(self, key: str, root: dict, inputs: dict[int, object], ctx, owner_key: str,
                 host: Host | None = None, slot: ListSlot | None = None) -> None:  # fmt: skip
        self.key, self.root, self.ctx, self.host, self.slot = key, root, ctx, host, slot
        shared = root_info(ctx, root)
        self.graph, self.by_node, self.children = shared.graph, shared.by_node, shared.children
        self.own: dict[int, object] = {}
        for info in shared.props:
            default = info["default"][1] if isinstance(info["default"], tuple) else None
            if info["type"] in (16, 19):
                default = GameObjectRef(Samples.key(owner_key, [info["uid"]]))
            given = inputs.get(info["uid"])
            self.own[info["uid"]] = default if given is None else given
        self.writes: dict[str | None, dict[tuple[int, ...], object]] = {}
        self.own_info = {i["uid"]: i for i in shared.props}
        # per target (and 0: all targets) how often a write changed what it gets: when to ask the host again
        self.versions: dict[str | int | None, int] = {}
        self.answers: dict[tuple, tuple[int, object]] = {}  # (kind, target) -> (version, what the host said)
        self._touched: list[tuple] = []  # what the running binding changed: (``own``, uid) or (``child``, target)
        self._asking: tuple | None = None  # (binding, source index) being read: whose placeholder a missing value gets
        self._readers, self._writers = self._plan()
        self.run(PASSES)
        if owner_key == key:
            self._play()

    def _play(self) -> None:
        """The open file's animation at the timeline's time, and its tweens, written over what its bindings gave."""
        if self.ctx.animation is not None:
            guid, t = self.ctx.animation
            for anim in animations.read(self.root):
                if anim.guid == guid:
                    for index, value in anim.values_at(t):
                        if index < len(self.graph.ends):
                            self.write(self.graph.ends[index], value)
            self.run(PASSES)
        if self.ctx.tweens:
            for guid, values in self.ctx.tweens.items():
                self.writes.setdefault(guid, {}).update(values)
                self._bump(guid)
            self.run(PASSES)

    def _bump(self, target: str | None) -> None:
        self.versions[target] = self.versions.get(target, 0) + 1
        self.versions[0] = self.versions.get(0, 0) + 1

    def computed(self) -> dict[int, object]:
        """The own values that differ from their declared defaults: a value the file only declares stands for one
        the game fills at run time (an animation's state, say), so the reader keeps its own stored value."""
        defaults = {
            uid: i["default"][1] if isinstance(i["default"], tuple) else None for uid, i in self.own_info.items()
        }
        return {uid: v for uid, v in self.own.items() if v is not None and v != defaults.get(uid)}

    def run(self, passes: int) -> None:
        """The bindings in file order, again until no value changes (a chain of them settles one link a pass). A pass
        runs only the bindings a change can matter to: those that read what changed, and the others that write it (a
        binding that runs again writes its value over theirs), which is what a pass over all of them gives."""
        todo = [True] * len(self.graph.bindings)
        for _ in range(passes):
            for i, b in enumerate(self.graph.bindings):
                if not todo[i]:
                    continue
                todo[i] = False
                self._touched.clear()
                self._run(b)
                for dep in self._touched:
                    for j in self._readers.get(dep, ()):
                        todo[j] = True
                    for j in self._writers.get(dep, ()):
                        todo[j] = todo[j] or j != i
                    for j in self._readers.get(ALL, ()) if dep[0] == "child" else ():  # a size reads every write
                        todo[j] = True
            if not any(todo):
                break

    def _plan(self) -> tuple[dict[tuple, list[int]], dict[tuple, list[int]]]:
        """Which bindings read and which write each value: an own property, or anything of a child (``ALL``: every
        child, which is what a computed size reads)."""
        readers: dict[tuple, list[int]] = {}
        writers: dict[tuple, list[int]] = {}
        for i, b in enumerate(self.graph.bindings):
            for e in b.sources:
                for dep in self._reads(e):
                    readers.setdefault(dep, []).append(i)
            for e in b.targets:
                if e.kind == "own" or (e.kind == "path" and e.ids):
                    uid = self._own_end(e)
                    dep = ("own", uid) if uid is not None else ("child", self._target(e))
                    writers.setdefault(dep, []).append(i)
        return readers, writers

    def _own_end(self, end: bindings.End) -> int | None:
        """The own property an end names: an own end, or a path on the widget itself to one of its properties (how
        the History Editions' files write them)."""
        if end.kind == "own":
            return end.ids[0]
        if end.kind == "path" and end.mode == bindings.SELF and len(end.ids) == 1 and end.ids[0] in self.own_info:
            return end.ids[0]
        return None

    def _reads(self, end: bindings.End) -> tuple:
        """What of this scope a source end reads (``read`` follows the same cases)."""
        if end.kind == "constant" or not end.ids:
            return ()
        if end.kind == "own" or end.mode == bindings.SELF:
            return (("own", end.ids[0]),)
        target = self._target(end)
        if (end.mode == bindings.ELEMENT and self.slot is not None) or not target:
            return ()
        return (("child", target), ALL) if len(end.ids) == 1 and end.ids[0] in COMPUTED else (("child", target),)

    def _follow(self, value, rest: list[int]):
        """The game value ``rest`` names under a game object: a sample value, else (placeholders on) the placeholder
        of the binding source being read, its own whichever binding asked for the key first."""
        if not rest:
            return value
        if not isinstance(value, GameObjectRef):
            return None
        key, samples = "/".join([value.key, *(f"{i:08x}" for i in rest)]), self.ctx.samples
        if self._asking is None or not samples.fill or (key in samples.values and key not in samples.generated):
            return samples.get(key)
        guess = placeholders.guess(*self._asking, self.describe)
        samples.placeholder(key, guess)  # the sample panel shows the first; made before the read depends on it
        samples.get(key)
        return guess

    def _target(self, end: bindings.End) -> str | None:
        """The instance an end names: a child, or a list's item (DataMode 3), None for the widget itself."""
        if end.mode == bindings.ITEM and self.slot is not None:
            return self.slot.item
        return end.instance if end.mode == bindings.CHILD else None

    def read(self, end: bindings.End):
        if end.kind == "constant":
            return end.value[1] if isinstance(end.value, tuple) else None
        if end.kind == "own":
            return axis(self.own.get(end.ids[0]), end.field)
        if not end.ids:
            return None
        first, rest = end.ids[0], end.ids[1:]
        if end.mode == bindings.SELF:
            return axis(self._follow(self.own.get(first), rest), end.field)
        if end.mode == bindings.ELEMENT and self.slot is not None:
            if first == DATA_SOURCE:
                return axis(self.slot.index, end.field)
            return axis(self._follow(self.slot.element, rest), end.field)
        target = self._target(end)
        return axis(self._read_child(target, tuple(end.ids)), end.field) if target else None

    def _read_child(self, target: str, path: tuple[int, ...]):
        written = self.writes.get(target, {})
        if path in written:
            return written[path]
        for n in range(len(path) - 1, 0, -1):
            if path[:n] in written:
                return self._follow(written[path[:n]], list(path[n:]))
        node = self.children.get(target)
        if node is None:
            return None
        if path[0] < 0x80000000 and self.host is not None:  # an output of the widget file the child places
            value = self._asked("outputs", target, node).get(path[0])
            if value is not None:
                return self._follow(value, list(path[1:]))
        stored = self.ctx.values(node).get(path)
        if stored is not None:
            return stored
        if len(path) != 1:
            return None
        if path[0] in COMPUTED and self.host is not None:
            return self._asked("size", target, node)
        return builtin_value(node, path[0])

    def _asked(self, what: str, target: str, node: dict):
        """What the host says about the child ``target`` (its outputs, its size), asked once per change of what the
        bindings write into it: it places the child's whole widget file."""
        # a group's size reads its children's writes too, so a size follows every write
        stamp = self.versions.get(target, 0) if what == "outputs" else self.versions.get(0, 0)
        hit = self.answers.get((what, target))
        if hit is None or hit[0] != stamp:
            ask = self.host.outputs if what == "outputs" else self.host.size_of
            hit = (stamp, ask(node, self))
            self.answers[(what, target)] = hit
        return hit[1]

    def write(self, end: bindings.End, value) -> None:
        if value is None:
            return
        uid = self._own_end(end)
        if uid is not None:
            new = with_axis(self.own.get(uid), end.field, value)
            if new != self.own.get(uid):
                self._touched.append(("own", uid))
            self.own[uid] = new
        elif end.kind == "path" and end.ids:
            target = self._target(end)
            slot = self.writes.setdefault(target, {})
            path = tuple(end.ids)
            old = slot.get(path)
            if old is None and end.field != 1 and len(path) == 1 and target in self.children:
                old = builtin_value(self.children[target], path[0])  # one axis written: the others as stored
            new = with_axis(old, end.field, value)
            if new != old:
                self._touched.append(("child", target))
                self._bump(target)
            slot[path] = new

    def describe(self, end: bindings.End) -> tuple[str, str]:
        """(value type, name) of an end a binding writes, for the placeholders."""
        uid = self._own_end(end)
        if uid is not None:
            info = self.own_info.get(uid, {})
            return OWN_TYPES.get(info.get("type", -1), "value"), info.get("name", "")
        if end.kind == "path" and end.ids and end.ids[-1] >= 0x80000000:
            return placeholders.target_kind(end.ids[-1])
        return "value", ""

    def _run(self, b: bindings.Binding) -> None:
        values = []
        for i, e in enumerate(b.sources):
            self._asking = (b, i)
            values.append(self.read(e))
        self._asking = None
        if b.operator in (operators.ASSIGN, "bba41a31") and values and values[0] is None:
            return
        outs = operators.evaluate(b.operator, values)
        for end, v in zip(b.targets, outs or [], strict=False):
            self.write(end, v)
