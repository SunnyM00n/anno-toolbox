"""The bindings of one placed template evaluated over its inputs and the sample values: its own properties, what it
writes into its instances, and what it reads from them - their stored fields, their computed sizes, the outputs of
the widget files they place and, in a list, the element an item stands for.

``Scope(key, root, inputs, ctx, owner_key, host)`` evaluates; ``host`` (``layout.Placer``) answers what needs
placing: a child's size and the outputs of a child's widget file. docs/ui-editor.md "The preview".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol

from ..formats import phoenixedit
from . import (
    animations,
    bindings,
    elements,
    fields,
    gamedata,
    operators,
    placeholders,
    playstate,
    previewdefaults,  # fmt: skip
)
from .viewmodels import Samples

ENUM_BOOLEAN, ENUM_VALUE = "d7ef3d58", "6c4a2404"  # the enum operators that read an enum asset
ENUM_CONVERTERS = (ENUM_BOOLEAN, ENUM_VALUE)
TWO_WAY = "bba41a31"  # TwoWayAssign
ALPHA, FADED = 0x80000006, 0.01  # AlphaTint; below FADED the design view shows the widget (layout.HIDDEN_ALPHA)
ANIMATOR = "d2506d55"  # the Animator component: StartValue, EndValue, CurrentValue (docs/ui/117/components.md)
ANIM_START, ANIM_END, ANIM_CURRENT = 0x71047B15, 0x7BEB711E, 0x7FB90677
ANIM_GO, ADDED = 0x78773C49, 0x8000003C  # the Animator's Start action; AddedToScene, fired as the scene opens

SIZE = 0x80000004
DATA_SOURCE, DATA_ITEM = 0x30000001, 0x30000002  # a list's array; the element (or its index) an item gets
COMPUTED = (SIZE, 0x80000037)  # read from the placed widget, not from its stored fields
DISPLAYED = (0x8000004F, 0x80000052)  # a label's DisplayedText and DisplayedSize: its text as laid out
PASSES = 12  # a value one binding writes may be another one's source: passes until nothing changes, at most
SHARES = 8  # exchanges of shared objects' fields with the placed files, at most (``Scope._share``)
GIVEN, OWN = "<given>", "<own>"  # who wrote an object's fields besides the placed instances (``Scope.shared``)
STORED = "<stored>"  # what the placing instance stores below the property: this file reads it, it is not shared
MISSING = object()
ALL = ("child", "*")  # in ``Scope._plan``: what reads a computed size, which every write to a child may change
OWN_TYPES = {0: "bool", 1: "int", 2: "int", 3: "float", 4: "string", 18: "color"}  # PropertyType -> value type
BLANK = "<a game object holding no given value>"  # ``canon``
FIRES_DEPTH = 6  # bindings followed back from an Animator's start to the scene's opening (``Scope._fires``)


@dataclass(frozen=True)
class GameObjectRef:
    """A game object as the preview knows it: the sample key its fields are read under. ``holder``: inside a file,
    the instance whose own object it is (two instances of one widget file own objects of one key); never passed on."""

    key: str
    holder: str | None = None


@dataclass(frozen=True)
class ListSlot:
    """The element a list's item stands for: its game object and its index in the array."""

    element: GameObjectRef
    index: int
    item: str | None  # the GUID of the item instance (the list file's one child)
    given: tuple[tuple[tuple[int, ...], object], ...] = ()  # what the game gives the item over all items (compares)


class Host(Protocol):
    def size_of(self, node: dict, scope: Scope) -> tuple[float, float]: ...

    def outputs(self, node: dict, scope: Scope) -> dict[int, object]: ...

    def places(self, node: dict, scope: Scope) -> bool: ...

    def displayed(self, node: dict, scope: Scope) -> dict[int, object]: ...


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


def component_default(ctx, node: dict, path: tuple[int, ...]):
    """The default a component's file declares for one of its properties (``path``: the instance's component id, the
    property's id), for an instance that stores no value of it: the state the game starts it in, a UI scale of 1 and
    no gamepad (docs/ui/117/components.md). None for any other path."""
    if len(path) != 2:
        return None
    for c in phoenixedit.objects(node, "Components"):
        if int(fields.get(c, "UniqueId", 0) or 0) == path[0]:
            root = ctx.root(fields.get(c, "ComponentTemplate"))
            info = next((i for i in root_info(ctx, root).props if i["uid"] == path[1]), None) if root else None
            return info["default"][1] if info is not None and isinstance(info["default"], tuple) else None
    return None


def animated_end(node: dict, path: tuple[int, ...], stored: dict, written: dict, default=lambda _uid: None):
    """In the design view, what an instance's ``Animator`` holds once its animation has run: its ``EndValue`` for a
    read of its ``CurrentValue`` while the instance stores the start there (the main menu's panels slide in from
    -30 to 40: layout.md "Values the game gives at run time"), or stores neither and the component declares them
    alike (``default``: a property id -> its declared default). None for any other read."""
    if len(path) != 2 or path[1] != ANIM_CURRENT:
        return None
    animators = {int(fields.get(c, "UniqueId", 0) or 0) for c in phoenixedit.objects(node, "Components")
                 if (fields.get(c, "ComponentTemplate") or "").startswith(ANIMATOR)}  # fmt: skip
    if path[0] not in animators:  # an instance may hold several (a text's show and hide)
        return None
    start, now = stored.get((path[0], ANIM_START), default(ANIM_START)), stored.get(path, default(ANIM_CURRENT))
    if now is None or now != start:
        return None
    return written.get((path[0], ANIM_END), stored.get((path[0], ANIM_END)))


def truthy(value) -> bool:
    """A value read as a flag: none, false, zero and the empty text are off."""
    return value not in (None, False, 0, 0.0, "")


def first_entry(ctx, enum: str | None) -> int | None:
    """The first entry of an enum asset (its UniqueId): what an enum the game fills reads in the design view while its
    value is unknown (a popup's state: closed), as an enum loader shows its first entry."""
    root = ctx.root(enum) if enum else None
    entries = phoenixedit.objects(root, "EnumEntries") if root is not None else []
    found = int(fields.get(entries[0], "UniqueId", 0) or 0) if entries else 0
    return found or None


@dataclass(frozen=True)
class GivenObject:
    """A game object property of a placed widget file with fields the placing file wrote into it (the text plane,
    alignment and sizing of a text file's layout object): the placed file reads these fields as written."""

    base: object  # the value passed for the property itself (a ``GameObjectRef``), None if none
    fields: tuple[tuple[tuple[int, ...], object], ...]  # (path below the property, value), sorted
    local: tuple[tuple[tuple[int, ...], object], ...] = ()  # what the instance stores there: read, not shared


def inputs_of(overrides: dict, node: dict | None = None) -> dict[int, object]:
    """The values an instance passes into its widget file's own properties (the ids below the built-ins); fields
    written below one of them come as a ``GivenObject``, those the instance only stores (an author's preview text)
    as its ``local`` ones. ``node``: the instance, whose own components' values are not passed."""
    out: dict[int, object] = {p[0]: v for p, v in overrides.items() if len(p) == 1 and p[0] < 0x80000000}
    comps = {int(fields.get(c, "UniqueId", 0) or 0) for c in phoenixedit.objects(node, "Components")} if node else ()
    stored = fields.property_values(node) if node else {}
    nested: dict[int, list] = {}
    for p, v in overrides.items():
        if len(p) > 1 and p[0] < 0x80000000 and p[0] not in comps:
            nested.setdefault(p[0], []).append((tuple(p[1:]), v))
    for uid, given in nested.items():
        base = out.get(uid)
        if isinstance(base, GivenObject):  # an object handed on with fields its file wrote: those win
            given, base = list({**dict(given), **dict(base.fields)}.items()), base.base
        local = [(p, v) for p, v in given if stored.get((uid, *p), MISSING) == v]
        written = [kv for kv in given if kv not in local]
        out[uid] = GivenObject(base, tuple(sorted(written, key=lambda kv: kv[0])),
                               tuple(sorted(local, key=lambda kv: kv[0])))  # fmt: skip
    return out


def passed(ref: GameObjectRef, known: dict[tuple[int, ...], object]):
    """A game object as a file passes it on (into a placed file, or back as an output): its key, with the fields
    written into it that the receiver does not know yet, as a ``GivenObject`` (layout.md "Values the game gives at
    run time": objects are shared)."""
    base = GameObjectRef(ref.key)
    return GivenObject(base, tuple(sorted(known.items(), key=lambda kv: kv[0]))) if known else base


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
    subtrees: dict[str, tuple[str, ...]] = field(default_factory=dict)  # instance -> it and the instances below it


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

    def one(v):
        if isinstance(v, GivenObject):
            return replace(v, base=one(v.base))
        return canon(samples, v) if isinstance(v, GameObjectRef) else v

    return repr(sorted(((k, one(v)) for k, v in inputs.items()), key=lambda kv: kv[0]))


class Scope:
    """One template being placed: its own property values, what its bindings write into its instances."""

    def __init__(self, key: str, root: dict, inputs: dict[int, object], ctx, owner_key: str,
                 host: Host | None = None, slot: ListSlot | None = None) -> None:  # fmt: skip
        self.key, self.root, self.ctx, self.host, self.slot = key, root, ctx, host, slot
        shared = root_info(ctx, root)
        self.graph, self.by_node, self.children = shared.graph, shared.by_node, shared.children
        self.inputs, self.own = inputs, {}
        self.given: dict[tuple[int, ...], object] = {}  # fields the placing file wrote below an own property
        # the fields of each game object this file holds, by who wrote them: STORED and GIVEN (the placing instance),
        # an instance of this file (what its file wrote), OWN (this file's bindings); keyed by (holder, key)
        self.shared: dict[tuple, dict[str, dict[tuple[int, ...], object]]] = {}
        self.hands: dict[str, dict[int, GameObjectRef]] = {}  # instance -> its property -> the object it holds
        for info in shared.props:
            default = info["default"][1] if isinstance(info["default"], tuple) else None
            if info["type"] in (16, 19):
                default = GameObjectRef(Samples.key(owner_key, [info["uid"]]))
            given = inputs.get(info["uid"])
            if isinstance(given, GivenObject):
                self.given.update({(info["uid"], *path): v for path, v in (*given.local, *given.fields)})
                ref = default if given.base is None else given.base
                if isinstance(ref, GameObjectRef):
                    self.shared.setdefault((None, ref.key), {}).update({STORED: dict(given.local),
                                                                         GIVEN: dict(given.fields)})  # fmt: skip
                given = given.base
            self.own[info["uid"]] = default if given is None else given
        self.writes: dict[str | None, dict[tuple[int, ...], object]] = {}
        self.own_info = {i["uid"]: i for i in shared.props}
        for uid, given in inputs.items():  # fields of the file's own components the game fills (a wheel's sector)
            if isinstance(given, GivenObject) and uid not in self.own_info:
                self.given.update({(uid, *path): v for path, v in given.fields})
        # per target (and 0: all targets) how often a write changed what it gets: when to ask the host again
        self.versions: dict[str | int | None, int] = {}
        self.answers: dict[tuple, tuple[int, object]] = {}  # (kind, target) -> (version, what the host said)
        self._touched: list[tuple] = []  # what the running binding changed: (``own``, uid) or (``child``, target)
        self._asking: tuple | None = None  # (binding, source index) being read: whose placeholder a missing value gets
        self._readers = self._plan()
        playstate.seed(self, inputs, owner_key)  # Play mode's values for this placing, if it has any
        self.run(PASSES)
        for _ in range(SHARES):  # what the placed files wrote into the objects they share with this one
            if not self._share():
                break
            self.run(PASSES)
        if ctx.design and self._shown():
            self.run(PASSES)
        playstate.settle(self)  # what they started: animations at their end, toggles
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

    def _shown(self) -> bool:
        """In the design view, the file's animations the scene's show event starts (a field of the scene's animation
        object named ``Show...``: ``ShowSingle``) put at their end, as the open scene stands in the game (an object
        menu's mask grown to 6000 and its panel faded in: layout.md "Values the game gives at run time")."""
        found = [t.steps[-1][1] for b in self.graph.bindings for t in b.targets
                 if t.kind == "path" and t.mode == playstate.ANIMATION and t.ids[-1:] == [playstate.START] and t.steps
                 and any(s.kind == "path" and len(s.ids) > 1
                         and gamedata.field_name(self.ctx, s.ids[-1]).startswith("Show") for s in b.sources)]  # fmt: skip
        if not found:
            return False
        for anim in animations.read(self.root):
            if anim.guid in found:
                for index, value in anim.values_at(anim.seconds):
                    if index < len(self.graph.ends):
                        self.write(self.graph.ends[index], value)
        return True

    def _bump(self, target: str | None) -> None:
        self.versions[target] = self.versions.get(target, 0) + 1
        self.versions[0] = self.versions.get(0, 0) + 1

    def computed(self) -> dict[int, object]:
        """The own values that differ from their declared defaults: a value the file only declares stands for one
        the game fills at run time (an animation's state, say), so the reader keeps its own stored value."""
        defaults = {
            uid: i["default"][1] if isinstance(i["default"], tuple) else None for uid, i in self.own_info.items()
        }
        return {uid: self._back(v) for uid, v in self.own.items() if v is not None and v != defaults.get(uid)}

    def _back(self, value):
        """An own value as the placing file reads it: a game object with the fields this file and the files it
        places wrote into it (what the placing file gave it knows)."""
        return passed(value, self.known(value, given=False)) if isinstance(value, GameObjectRef) else value

    def known(self, ref: GameObjectRef, skip: str | None = None, given: bool = True) -> dict[tuple[int, ...], object]:
        """The fields of a game object as this file knows them: what the placing file gave, then what each placed
        file wrote (in the order they got it), then this file's own writes; the later wins. ``skip``: the instance
        it is handed to, whose writes are left out (it knows them); what the placing instance only stores (an
        author's preview text) counts for this file's own reads alone, as it would otherwise reach every file below.
        A field written by a longer path into an object this one lies in counts too (``Data.TextData.ID`` written,
        ``TextData`` handed on)."""
        out: dict[tuple[int, ...], object] = {}
        outer = sorted(k for k in self.shared if k[0] == ref.holder and ref.key.startswith(k[1] + "/"))
        for holder, key in [*outer, (ref.holder, ref.key)]:  # the object's own fields win
            below = tuple(int(p, 16) for p in ref.key[len(key) + 1 :].split("/")) if key != ref.key else ()
            view = self._view(self.shared.get((holder, key), {}), skip, given)
            out.update(
                {p[len(below) :]: v for p, v in view.items() if len(p) > len(below) and p[: len(below)] == below}
            )
        return out

    @staticmethod
    def _view(found: dict, skip: str | None, given: bool) -> dict[tuple[int, ...], object]:
        """One object's fields by who wrote them (``Scope.shared``) merged in ``known``'s order."""
        view = {**(found.get(STORED, {}) if skip is None else {}), **found.get(GIVEN, {})} if given else {}
        for who, written in found.items():
            if who not in (GIVEN, OWN, STORED, skip):
                view.update(written)
        view.update(found.get(OWN, {}))
        return view

    def _take(self, value, child: str, output: bool = True):
        """A game object a placed instance gives back (``output``; else what this file handed into it): the object
        as this file knows it (the one handed in, else that instance's own), an output's fields kept as that
        instance's writes."""
        base = value.base if isinstance(value, GivenObject) else value
        if not isinstance(base, GameObjectRef):
            return value
        ref = next((r for r in self.hands.get(child, {}).values() if r.key == base.key), None)
        ref = ref or GameObjectRef(base.key, child)
        if output:
            written = dict(value.fields) if isinstance(value, GivenObject) else {}
            if written or child in self.shared.get((ref.holder, ref.key), {}):
                self.shared.setdefault((ref.holder, ref.key), {})[child] = written
        return ref

    def _share(self) -> bool:
        """One exchange with the placed files of the objects this file shares with them (layout.md "Values the game
        gives at run time"): their outputs taken in (what they and the files below wrote), then every object handed
        in again with what the others wrote. True where anything changed (the bindings run again)."""
        if self.host is None:
            return False
        before = repr(self.shared)
        asked = [t for kind, t in self.answers if kind == "outputs"]
        for target in dict.fromkeys([*self.hands, *asked]):
            node = self.children.get(target or "")
            if node is None or not self.host.places(node, self):
                continue
            for uid, value in self._asked("outputs", target, node).items():
                ref = self._take(value, target)
                if isinstance(ref, GameObjectRef):  # held there: what the others write reaches it
                    self.hands.setdefault(target, {}).setdefault(uid, ref)
        moved = False
        for target, held in self.hands.items():
            slot = self.writes.get(target, {})
            for uid, ref in held.items():
                new = passed(ref, self.known(ref, skip=target))
                if (uid,) in slot or isinstance(new, GivenObject):
                    if slot.get((uid,)) != new:
                        self.writes.setdefault(target, {})[(uid,)] = new
                        self._bump(target)
                        moved = True
        return moved or repr(self.shared) != before

    def run(self, passes: int) -> None:
        """The bindings in file order once, then again only those whose source changed, until no value changes: as
        the game, where a binding fires when what it reads changes, so of two bindings writing one value the one
        whose source changed last wins, not the last in the file (layout.md "Bindings that lay a stack out"). A
        binding that reads what placing a child gives (its size, its outputs) waits until the others have settled:
        asked earlier, the child would be placed for values about to change (ui-editor.md "Speed")."""
        todo = [True] * len(self.graph.bindings)
        for _ in range(passes):
            for i in self._order:
                if not todo[i]:
                    continue
                todo[i] = False
                self._touched.clear()
                self._run(self.graph.bindings[i])
                for dep in self._touched:
                    for j in self._readers.get(dep, ()):
                        todo[j] = True
                    for j in self._readers.get(ALL, ()) if dep[0] == "child" else ():  # a size reads every write
                        todo[j] = True
            if not any(todo):
                break

    def _plan(self) -> dict[tuple, list[int]]:
        """Which bindings read each value: an own property, or anything of a child (``ALL``: every child, which is
        what a computed size reads). Sets the order ``run`` takes them in: those that ask the host last."""
        readers: dict[tuple, list[int]] = {}
        hosted = []
        self._writers: dict[int, list[bindings.Binding]] = {}  # own property -> the bindings writing it
        for i, b in enumerate(self.graph.bindings):
            for e in b.sources:
                for dep in self._reads(e):
                    readers.setdefault(dep, []).append(i)
            hosted.append(self.host is not None and any(self._asks_host(e) for e in b.sources))
            for uid in dict.fromkeys(self._own_end(t) for t in b.targets):
                if uid is not None:
                    self._writers.setdefault(uid, []).append(b)
        self._order = sorted(range(len(hosted)), key=lambda i: (hosted[i], i))
        return readers

    def _asks_host(self, end: bindings.End) -> bool:
        """Whether reading a source end may place a child (``_read_child``): its size, its outputs, its laid-out text."""
        if end.kind != "path" or not end.ids or end.mode == bindings.SELF or not self._target(end):
            return False
        if end.mode == bindings.ELEMENT and self.slot is not None:
            return False
        first = end.ids[0]
        return first < 0x80000000 or (len(end.ids) == 1 and (first in COMPUTED or first in DISPLAYED))

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
        if isinstance(value, GivenObject):  # an object passed with fields written into it (``passed``)
            hit = dict(value.fields).get(tuple(rest))
            if hit is not None:
                return hit
            value = value.base
        if not isinstance(value, GameObjectRef):
            return None
        known = self.known(value)  # written into the object by this file or a file that shares it
        for n in range(len(rest), 0, -1):
            if tuple(rest[:n]) in known:
                return self._follow(known[tuple(rest[:n])], rest[n:]) if n < len(rest) else known[tuple(rest)]
        key, samples = "/".join([value.key, *(f"{i:08x}" for i in rest)]), self.ctx.samples
        found = playstate.chosen(self.ctx, key)  # a choice made in Play mode wins over the samples
        if found is not None:
            return found
        if key in samples.values and key not in samples.generated:
            return samples.get(key)
        found = gamedata.lookup(self, key)  # the installed game's data wins over a placeholder
        if found is not None:
            return found
        selected = self._selection(key, rest[-1])
        if selected is not None:  # one selected element per list, whoever reads it (a tab and its page)
            samples.placeholder(key, selected)
            return samples.get(key)
        if self._asking is None or not samples.fill:
            return samples.get(key)
        locked = placeholders.is_locked(self.ctx, value.key)
        guess = placeholders.guess(*self._asking, self.describe, locked, previewdefaults.current(self.ctx),
                                   self.stored_at, self._readers_of(*self._asking))  # fmt: skip
        if guess == placeholders.PICTURE + "picture":  # a slot the source does not name: the image's own name
            guess = placeholders.PICTURE + (self._target_name(self._asking[0]) or "picture")
        samples.placeholder(key, guess)  # the sample panel shows the first; made before the read depends on it
        samples.get(key)
        return guess

    def _target_name(self, b: bindings.Binding) -> str:
        """The name of the instance a binding's first target is on ("" for the widget itself)."""
        node = self.children.get(self._target(b.targets[0]) or "") if b.targets else None
        el = self.by_node.get(id(node)) if node is not None else None
        return el.name if el is not None and el.name else ""

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
            if first not in self.own_info and rest:  # a component of the widget itself: written, filled, its default
                written = self.writes.get(None, {}).get(tuple(end.ids), self.given.get(tuple(end.ids)))
                return axis(written if written is not None else component_default(self.ctx, self.root, tuple(end.ids)),
                            end.field)  # fmt: skip
            own = self.own.get(first)
            if not isinstance(own, GameObjectRef) and tuple(end.ids) in self.given:  # written by the placing file
                return axis(self.given[tuple(end.ids)], end.field)
            return axis(self._follow(own, rest), end.field)
        if end.mode == bindings.ELEMENT and self.slot is not None:
            if first == DATA_SOURCE:
                return axis(self.slot.index, end.field)
            return axis(self._follow(self.slot.element, rest), end.field)
        target = self._target(end)
        return axis(self._read_child(target, tuple(end.ids)), end.field) if target else None

    def _read_child(self, target: str, path: tuple[int, ...]):
        written = self.writes.get(target, {})
        if path in written:
            return self._take(written[path], target, output=False)
        for n in range(len(path) - 1, 0, -1):
            if path[:n] in written:
                return self._follow(self._take(written[path[:n]], target, output=False), list(path[n:]))
        if self.ctx.design and target in self.children and path[1:] == (ANIM_CURRENT,) and self._started(target, path):
            node = self.children[target]
            end = animated_end(node, path, self.ctx.values(node), written,
                               lambda uid: component_default(self.ctx, node, (path[0], uid)))  # fmt: skip
            if end is not None:
                return end
        if self.slot is not None and target == self.slot.item and self.slot.given:
            hit = dict(self.slot.given).get(path)  # a list item's compared values (``listlayout.compared``)
            if hit is not None:
                return hit
        node = self.children.get(target)
        if node is None:
            return None
        if path[0] < 0x80000000 and self.host is not None:  # an output of the widget file the child places
            value = self._asked("outputs", target, node).get(path[0])
            if value is not None:
                return self._follow(self._take(value, target), list(path[1:]))
        if path[0] in DISPLAYED and len(path) == 1 and self.host is not None:  # a label's laid-out text
            shown = self._asked("displayed", target, node).get(path[0])
            if shown is not None:
                return shown
        stored = self.ctx.values(node).get(path)
        if stored is None and len(path) != 1:
            return component_default(self.ctx, node, path)
        if stored is None and path[0] in COMPUTED and self.host is not None:
            return self._asked("size", target, node)
        stored = builtin_value(node, path[0]) if stored is None else stored
        if path == (ALPHA,) and self.ctx.design and isinstance(stored, (int, float)) and stored < FADED:
            return 1.0  # faded in by an animation: read as the design view draws it (the religion scene's pages)
        return stored

    def _started(self, target: str, path: tuple[int, ...]) -> bool:
        """In the design view, whether the ``Animator`` of an instance (``path``: its component, then the property
        read) has run: one a binding starts at the scene's opening has where that binding fires then (``_fires``: a
        tab's text shows on the selected tab only, the icon-text gap opens there only), one whose start is not known
        has (layout.md "Values the game gives at run time")."""
        seen = False
        for b in self.graph.bindings:
            for t in b.targets:
                if t.kind == "path" and self._target(t) == target and t.ids == [path[0], ANIM_GO]:
                    fired = self._fires(b, FIRES_DEPTH)
                    if fired:
                        return True
                    seen = seen or fired is not None
        return not seen

    def _fires(self, b: bindings.Binding, depth: int) -> bool | None:
        """Whether a binding's event fires as the scene opens: from ``AddedToScene``, or from a change of a flag to
        true (``FalseToTrueChange``: where the flag is on), passed on by ``Assign`` and gated by ``IfTrueAssign`` /
        ``IfFalseAssign``. None where the preview cannot tell (a hover, a click)."""
        name = getattr(operators.OPERATORS.get(b.operator), "name", "")
        if not b.sources or depth <= 0:
            return None
        if name == "FalseToTrueChange":
            return truthy(self.read(b.sources[0]))
        if name not in ("Assign", "IfTrueAssign", "IfFalseAssign"):
            return None
        fired = self._source_fires(b.sources[0], depth - 1)
        if fired and name != "Assign":
            return truthy(self.read(b.sources[1])) == (name == "IfTrueAssign") if len(b.sources) > 1 else None
        return fired

    def _source_fires(self, end: bindings.End, depth: int) -> bool | None:
        """``_fires`` of what a source end reads: the scene's opening, or an own property the file's bindings write."""
        if end.kind == "path" and end.ids == [ADDED]:
            return True
        uid = self._own_end(end)
        if uid is None:
            return None
        found = [self._fires(b, depth) for b in self._writers.get(uid, ())]
        return True if any(found) else (False if found and all(f is False for f in found) else None)

    def _asked(self, what: str, target: str, node: dict):
        """What the host says about the child ``target`` (its outputs, its size), asked once per change of what the
        bindings write into it: it places the child's whole widget file."""
        # a group's size reads its children's writes too: a size follows the writes into the instance's subtree
        stamp = self.versions.get(target, 0) if what != "size" else self._subtree_version(target)
        hit = self.answers.get((what, target))
        if hit is None or hit[0] != stamp:
            ask = {"outputs": self.host.outputs, "displayed": self.host.displayed}.get(what, self.host.size_of)
            hit = (stamp, ask(node, self))
            self.answers[(what, target)] = hit
        return hit[1]

    def _subtree_version(self, target: str) -> int:
        """How often the writes into an instance and the instances below it in this file changed: what its size
        depends on of this file's writes."""
        info = root_info(self.ctx, self.root)
        guids = info.subtrees.get(target)
        if guids is None:
            el = self.by_node.get(id(self.children.get(target)))
            guids = info.subtrees[target] = tuple(e.guid for e in el.walk() if e.guid) if el is not None else (target,)
        return sum(self.versions.get(g, 0) for g in guids)

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
            if target is not None and len(path) == 1 and isinstance(value, GameObjectRef):  # an object handed in
                self.hands.setdefault(target, {})[path[0]] = value
                value = passed(value, self.known(value, skip=target))
            own = self.own.get(path[0]) if target is None and len(path) > 1 else None
            if isinstance(own, GameObjectRef):  # into an own object: every file that shares it sees it
                mine = self.shared.setdefault((own.holder, own.key), {}).setdefault(OWN, {})
                mine[path[1:]] = with_axis(self.known(own).get(path[1:]), end.field, value)
            old = slot.get(path)
            if old is None and end.field != 1 and target in self.children:
                old = self._stored(self.children[target], path)  # one axis written: the others as stored
            new = with_axis(old, end.field, value)
            if new != old:
                self._touched.append(("child", target) if target is not None else ("own", path[0]))  # ``_reads``
                self._bump(target)
            slot[path] = new

    def _readers_of(self, binding: bindings.Binding, index: int) -> tuple:
        """The other (binding, source index) pairs of this file that read the game value source ``index`` of
        ``binding`` reads: one value has one placeholder state for all of them (``placeholders.flag_for``)."""
        src = binding.sources[index]
        same = (src.kind, src.mode, src.instance, tuple(src.ids))
        return tuple((b, i) for b in self.graph.bindings for i, e in enumerate(b.sources)
                     if b is not binding and (e.kind, e.mode, e.instance, tuple(e.ids)) == same)  # fmt: skip

    def stored_at(self, end: bindings.End):
        """What an instance stores in the built-in a target end names (its ``Visible``, say), for the placeholders: the
        file's own state is the typical one. None for other ends (the declared defaults of helper properties are
        no state)."""
        if end.kind != "path" or len(end.ids) != 1 or end.ids[0] < 0x80000000 or self._own_end(end) is not None:
            return None
        node = self.children.get(self._target(end) or "")
        return builtin_value(node, end.ids[0]) if node is not None else None

    def _stored(self, node: dict, path: tuple[int, ...]):
        """What an instance stores under ``path``: its own value of a property (a component's margin, say), else
        its field behind a built-in, else the default the placed widget file or the component declares (one axis
        of a size written keeps the declared other one)."""
        value = self.ctx.values(node).get(path)
        if value is not None:
            return value
        if len(path) != 1:
            return component_default(self.ctx, node, path)
        if path[0] >= 0x80000000:
            return builtin_value(node, path[0])
        default = self._placed_info(node, path[0]).get("default")
        return default[1] if isinstance(default, tuple) else None

    def _placed_info(self, node: dict, uid: int) -> dict:
        """``bindings.property_info`` of a property of the widget file the instance ``node`` places ({} if none)."""
        placed = self.ctx.root(fields.get(node, "Template"))
        return next((i for i in root_info(self.ctx, placed).props if i["uid"] == uid), {}) if placed else {}

    def describe(self, end: bindings.End) -> tuple[str, str]:
        """(value type, name) of an end a binding writes, for the placeholders."""
        uid = self._own_end(end)
        if uid is not None:
            info = self.own_info.get(uid, {})
            return OWN_TYPES.get(info.get("type", -1), "value"), info.get("name", "")
        if end.kind == "path" and end.ids and end.ids[-1] >= 0x80000000:
            return placeholders.target_kind(end.ids[-1])
        node = self.children.get(self._target(end) or "") if end.kind == "path" and len(end.ids) == 1 else None
        if node is not None:  # a property of the widget file the child places, as that file declares it
            info = self._placed_info(node, end.ids[0])
            return OWN_TYPES.get(info.get("type", -1), "value"), info.get("name", "")
        return "value", ""

    def _game_side(self, end: bindings.End):
        """What the game's side of a two-way binding holds, where the preview knows it: a value set by hand (or in
        Play mode) under its key, else a selection flag's typical state (``placeholders.selection_for``); None
        otherwise, and the widget's own value flows to the game."""
        if end.kind != "path" or len(end.ids) < 2:
            return None
        if end.mode == bindings.SELF and end.ids[0] in self.own_info:
            base = self.own.get(end.ids[0])
        else:
            base = self.slot.element if end.mode == bindings.ELEMENT and self.slot is not None else None
        if not isinstance(base, GameObjectRef):
            return None
        rest, samples = list(end.ids[1:]), self.ctx.samples
        key = "/".join([base.key, *(f"{i:08x}" for i in rest)])
        found = playstate.chosen(self.ctx, key)
        if found is not None or (key in samples.values and key not in samples.generated):
            return found if found is not None else samples.get(key)
        return self._selection(key, rest[-1])

    def _selection(self, key: str, uid: int) -> bool | None:
        """A selection flag's typical state at ``key`` (``placeholders.selection_for``), None for any other field or
        with the placeholders off."""
        if not self.ctx.samples.fill:
            return None
        first = lambda field: gamedata.first_of_group(self.ctx, field)  # noqa: E731
        index = lambda list_key: playstate.chosen_index(self.ctx, list_key)  # noqa: E731
        return placeholders.selection_for(gamedata.field_name(self.ctx, uid), key, first, index)

    def _enum_outputs(self, b: bindings.Binding, value) -> list | None:
        """What an enum operator gives for the enum entry ``value`` (docs/ui/117/layout.md "Bindings that lay a
        stack out"): ``EnumToBoolean`` one bool per target, true for the entry its ``BindingInfos`` name; ``EnumToValue``
        the value its enum asset's value rule holds for the entry. None: not an enum entry, or no such table."""
        conv = phoenixedit.objects(b.node, "ConverterInstance")
        if conv and value is None and self.ctx.design:  # unknown: the enum's first entry, as an enum loader's
            value = first_entry(self.ctx, fields.get(conv[0], "EnumAsset"))
        if not conv or not isinstance(value, int) or isinstance(value, bool):
            return None
        if b.operator == ENUM_BOOLEAN:
            wanted = {int(fields.get(i, "TargetIndex", 0) or 0): fields.get(i, "EnumValueId")
                      for i in phoenixedit.objects(conv[0], "BindingInfos")}  # fmt: skip
            return [wanted.get(i) == value for i in range(len(b.targets))]
        asset = self.ctx.root(fields.get(conv[0], "EnumAsset"))
        rule = fields.get(conv[0], "RuleId")
        for rec in phoenixedit.objects(asset, "ConversionRules") if asset is not None else []:
            if fields.get(rec, "RuleId") == rule:
                for entry in phoenixedit.objects(rec, "ConversionValues"):
                    if fields.get(entry, "EnumEntryUniqueId") == value:
                        got = fields.get(entry, "Value")
                        return [got[1] if isinstance(got, tuple) else got]
        return None

    def _run(self, b: bindings.Binding) -> None:
        values = []
        for i, e in enumerate(b.sources):
            self._asking = (b, i)
            values.append(self.read(e))
        self._asking = None
        if b.operator == TWO_WAY and b.targets and (game := self._game_side(b.targets[0])) is not None:
            self.write(b.sources[0], game)  # what the game's data holds drives the widget (a tab's selection)
            return
        if b.operator in (operators.ASSIGN, TWO_WAY) and values and values[0] is None:
            return
        outs = self._enum_outputs(b, values[0] if values else None) if b.operator in ENUM_CONVERTERS else None
        if outs is None:
            outs = operators.evaluate(b.operator, values)
        for end, v in zip(b.targets, outs or [], strict=False):
            self.write(end, v)
