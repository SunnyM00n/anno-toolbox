"""The bindings of one placed template evaluated over its inputs and the sample values: its own properties, what it
writes into its instances, and what it reads from them - their stored fields, their computed sizes, the outputs of
the widget files they place and, in a list, the element an item stands for.

``CScope(key, root, inputs, ctx, owner_key, host)`` evaluates; ``host`` (``layout.CPlacer``) answers what needs
placing: a child's size and the outputs of a child's widget file. docs/ui-editor.md "The preview".
"""

from __future__ import annotations

from typing import Protocol

from ..formats import phoenixedit
from . import animations, bindings, fields, gamedata, operators, placeholders, playstate, previewdefaults
from .scopeinfo import (  # noqa: F401  (the preview and its tests read them through this module)
    SRootInfo,
    component_default,
    feeds_picture,
    root_info,
)
from .scopevalues import (
    ADDED,
    ALL,
    ALPHA,
    ANIM_CURRENT,
    ANIM_END,  # noqa: F401
    ANIM_GO,
    ANIM_RAN,
    ANIM_START,  # noqa: F401
    COMPUTED,
    DATA_SOURCE,
    DISPLAYED,
    ENUM_BOOLEAN,
    ENUM_CONVERTERS,
    ENUM_VALUE,  # noqa: F401
    FADED,
    FIRES_DEPTH,
    GIVEN,
    OWN,
    OWN_TYPES,
    PASSES,
    SHARES,
    SIZE,
    STORED,
    TWO_WAY,
    SGameObjectRef,
    SGivenObject,
    SListSlot,
    animated_end,
    axis,
    builtin_value,
    canon,  # noqa: F401
    first_entry,
    inputs_key,  # noqa: F401
    inputs_of,  # noqa: F401
    passed,
    truthy,
    with_axis,
)
from .viewmodels import CSamples


class IHost(Protocol):
    """What a scope asks of the layout that places its children: their sizes, outputs and displayed values."""

    def size_of(self, node: dict, scope: CScope) -> tuple[float, float]: ...

    def outputs(self, node: dict, scope: CScope) -> dict[int, object]: ...

    def places(self, node: dict, scope: CScope) -> bool: ...

    def displayed(self, node: dict, scope: CScope) -> dict[int, object]: ...


class CScope:
    """One template being placed: its own property values, what its bindings write into its instances."""

    def __init__(self, key: str, root: dict, inputs: dict[int, object], ctx, owner_key: str,
                 host: IHost | None = None, slot: SListSlot | None = None) -> None:  # fmt: skip
        self.m_key, self.m_root, self.m_ctx, self.m_host, self.m_slot = key, root, ctx, host, slot
        shared = root_info(ctx, root)
        self.m_graph, self.m_by_node, self.m_children = shared.graph, shared.by_node, shared.children
        self.m_inputs, self.m_own = inputs, {}
        self.m_pinned: frozenset[int] = getattr(
            host, "pinned", frozenset()
        )  # own values the host holds as given (a dump's)
        self.m_given: dict[tuple[int, ...], object] = {}  # fields the placing file wrote below an own property
        self._m_starts: dict | None = None  # (child, animator) -> the bindings that start it (``_ran``)
        # the fields of each game object this file holds, by who wrote them: STORED and GIVEN (the placing instance),
        # an instance of this file (what its file wrote), OWN (this file's bindings); keyed by (holder, key)
        self.m_shared: dict[tuple, dict[str, dict[tuple[int, ...], object]]] = {}
        self.m_hands: dict[str, dict[int, SGameObjectRef]] = {}  # instance -> its property -> the object it holds
        for property_info in shared.props:
            default = property_info["default"][1] if isinstance(property_info["default"], tuple) else None
            if property_info["type"] in (16, 19):
                default = SGameObjectRef(CSamples.key(owner_key, [property_info["uid"]]))
            given = inputs.get(property_info["uid"])
            if isinstance(given, SGivenObject):
                self.m_given.update(
                    {(property_info["uid"], *path): value for path, value in (*given.local, *given.fields)}
                )
                ref = default if given.base is None else given.base
                if isinstance(ref, SGameObjectRef):
                    self.m_shared.setdefault((None, ref.key), {}).update({STORED: dict(given.local),
                                                                         GIVEN: dict(given.fields)})  # fmt: skip
                given = given.base
            self.m_own[property_info["uid"]] = default if given is None else given
        self.m_writes: dict[str | None, dict[tuple[int, ...], object]] = {}
        self.m_own_info = {details["uid"]: details for details in shared.props}
        for uid, given in inputs.items():  # fields of the file's own components the game fills (a wheel's segment)
            if isinstance(given, SGivenObject) and uid not in self.m_own_info:
                self.m_given.update({(uid, *path): value for path, value in given.fields})
        # per target (and 0: all targets) how often a write changed what it gets: when to ask the host again
        self.m_versions: dict[str | int | None, int] = {}
        self.m_answers: dict[tuple, tuple[int, object]] = {}  # (kind, target) -> (version, what the host said)
        self._m_touched: list[tuple] = []  # what the running binding changed: (``own``, uid) or (``child``, target)
        self._m_asking: tuple | None = (
            None  # (binding, source index) being read: whose placeholder a missing value gets
        )
        self._m_readers = self._plan()
        playstate.seed(self, inputs, owner_key)  # Play mode's values for this placing, if it has any
        self.run(PASSES)
        for _ in range(SHARES):  # what the placed files wrote into the objects they share with this one
            if not self._share():
                break
            self.run(PASSES)
        if ctx.m_design and self._shown():
            self.run(PASSES)
        playstate.settle(self)  # what they started: animations at their end, toggles
        if owner_key == key:
            self._play()

    def _play(self) -> None:
        """The open file's animation at the timeline's time, and its tweens, written over what its bindings gave."""
        if self.m_ctx.m_animation is not None:
            guid, seconds = self.m_ctx.m_animation
            for anim in animations.read(self.m_root):
                if anim.guid == guid:
                    for index, value in anim.values_at(seconds):
                        if index < len(self.m_graph.ends):
                            self.write(self.m_graph.ends[index], value)
            self.run(PASSES)
        if self.m_ctx.m_tweens:
            for guid, values in self.m_ctx.m_tweens.items():
                self.m_writes.setdefault(guid, {}).update(values)
                self._bump(guid)
            self.run(PASSES)

    def _shown(self) -> bool:
        """In the design view, the file's animations the scene's show event starts (a field of the scene's animation
        object named ``Show...``: ``ShowSingle``) put at their end, as the open scene stands in the game (an object
        menu's mask grown to 6000 and its panel faded in: layout.md "Values the game gives at run time")."""
        found = [
            target_end.steps[-1][1]
            for binding in self.m_graph.bindings
            for target_end in binding.targets
            if target_end.kind == "path"
            and target_end.mode == playstate.ANIMATION
            and target_end.ids[-1:] == [playstate.START]
            and target_end.steps
            and any(
                source_end.kind == "path"
                and len(source_end.ids) > 1
                and gamedata.field_name(self.m_ctx, source_end.ids[-1]).startswith("Show")
                for source_end in binding.sources
            )
        ]
        if not found:
            return False
        for anim in animations.read(self.m_root):
            if anim.guid in found:
                for index, value in anim.values_at(anim.seconds):
                    if index < len(self.m_graph.ends):
                        self.write(self.m_graph.ends[index], value)
        return True

    def _bump(self, target: str | None) -> None:
        self.m_versions[target] = self.m_versions.get(target, 0) + 1
        self.m_versions[0] = self.m_versions.get(0, 0) + 1

    def computed(self) -> dict[int, object]:
        """The own values that differ from their declared defaults: a value the file only declares stands for one
        the game fills at run time (an animation's state, say), so the reader keeps its own stored value."""
        defaults = {
            uid: details["default"][1] if isinstance(details["default"], tuple) else None
            for uid, details in self.m_own_info.items()
        }
        return {
            uid: self._back(value)
            for uid, value in self.m_own.items()
            if value is not None and value != defaults.get(uid)
        }

    def _back(self, value):
        """An own value as the placing file reads it: a game object with the fields this file and the files it
        places wrote into it (what the placing file gave it knows)."""
        return passed(value, self.known(value, use_given=False)) if isinstance(value, SGameObjectRef) else value

    def known(
        self, ref: SGameObjectRef, skip: str | None = None, use_given: bool = True
    ) -> dict[tuple[int, ...], object]:
        """The fields of a game object as this file knows them: what the placing file gave, then what each placed
        file wrote (in the order they got it), then this file's own writes; the later wins. ``skip``: the instance
        it is handed to, whose writes are left out (it knows them); what the placing instance only stores (an
        author's preview text) counts for this file's own reads alone, as it would otherwise reach every file below.
        A field written by a longer path into an object this one lies in counts too (``Data.TextData.ID`` written,
        ``TextData`` handed on)."""
        out: dict[tuple[int, ...], object] = {}
        outer = sorted(
            shared_key
            for shared_key in self.m_shared
            if shared_key[0] == ref.holder and ref.key.startswith(shared_key[1] + "/")
        )
        for holder, key in [*outer, (ref.holder, ref.key)]:  # the object's own fields win
            below = tuple(int(part, 16) for part in ref.key[len(key) + 1 :].split("/")) if key != ref.key else ()
            view = self._view(self.m_shared.get((holder, key), {}), skip, use_given)
            out.update(
                {
                    path[len(below) :]: value
                    for path, value in view.items()
                    if len(path) > len(below) and path[: len(below)] == below
                }
            )
        return out

    @staticmethod
    def _view(found: dict, skip: str | None, use_given: bool) -> dict[tuple[int, ...], object]:
        """One object's fields by who wrote them (``CScope.m_shared``) merged in ``known``'s order."""
        view = {**(found.get(STORED, {}) if skip is None else {}), **found.get(GIVEN, {})} if use_given else {}
        for who, written in found.items():
            if who not in (GIVEN, OWN, STORED, skip):
                view.update(written)
        view.update(found.get(OWN, {}))
        return view

    def _take(self, value, child: str, is_output: bool = True):
        """A game object a placed instance gives back (``is_output``; else what this file handed into it): the object
        as this file knows it (the one handed in, else that instance's own), an output's fields kept as that
        instance's writes."""
        base = value.base if isinstance(value, SGivenObject) else value
        if not isinstance(base, SGameObjectRef):
            return value
        ref = next((held for held in self.m_hands.get(child, {}).values() if held.key == base.key), None)
        ref = ref or SGameObjectRef(base.key, child)
        if is_output:
            written = dict(value.fields) if isinstance(value, SGivenObject) else {}
            if written or child in self.m_shared.get((ref.holder, ref.key), {}):
                self.m_shared.setdefault((ref.holder, ref.key), {})[child] = written
        return ref

    def _share(self) -> bool:
        """One exchange with the placed files of the objects this file shares with them (layout.md "Values the game
        gives at run time"): their outputs taken in (what they and the files below wrote), then every object handed
        in again with what the others wrote. True where anything changed (the bindings run again)."""
        if self.m_host is None:
            return False
        before = repr(self.m_shared)
        asked = [asked_target for kind, asked_target in self.m_answers if kind == "outputs"]
        for target in dict.fromkeys([*self.m_hands, *asked]):
            node = self.m_children.get(target or "")
            if node is None or not self.m_host.places(node, self):
                continue
            for uid, value in self._asked("outputs", target, node).items():
                ref = self._take(value, target)
                if isinstance(ref, SGameObjectRef):  # held there: what the others write reaches it
                    self.m_hands.setdefault(target, {}).setdefault(uid, ref)
        moved = False
        for target, held in self.m_hands.items():
            slot = self.m_writes.get(target, {})
            for uid, ref in held.items():
                new = passed(ref, self.known(ref, skip=target))
                if (uid,) in slot or isinstance(new, SGivenObject):
                    if slot.get((uid,)) != new:
                        self.m_writes.setdefault(target, {})[(uid,)] = new
                        self._bump(target)
                        moved = True
        return moved or repr(self.m_shared) != before

    def run(self, passes: int) -> None:
        """The bindings in file order once, then again only those whose source changed, until no value changes: as
        the game, where a binding fires when what it reads changes, so of two bindings writing one value the one
        whose source changed last wins, not the last in the file (layout.md "Bindings that lay a stack out"). A
        binding that reads what placing a child gives (its size, its outputs) waits until the others have settled:
        asked earlier, the child would be placed for values about to change (ui-editor.md "Speed")."""
        todo = [True] * len(self.m_graph.bindings)
        for _ in range(passes):
            for i in self._m_order:
                if not todo[i]:
                    continue
                todo[i] = False
                self._m_touched.clear()
                self._run(self.m_graph.bindings[i])
                for dep in self._m_touched:
                    for j in self._m_readers.get(dep, ()):
                        todo[j] = True
                    for j in self._m_readers.get(ALL, ()) if dep[0] == "child" else ():  # a size reads every write
                        todo[j] = True
            if not any(todo):
                break

    def _plan(self) -> dict[tuple, list[int]]:
        """Which bindings read each value: an own property, or anything of a child (``ALL``: every child, which is
        what a computed size reads). Sets the order ``run`` takes them in: those that ask the host last."""
        readers: dict[tuple, list[int]] = {}
        hosted = []
        self._m_writers: dict[int, list[bindings.SBinding]] = {}  # own property -> the bindings writing it
        for i, binding in enumerate(self.m_graph.bindings):
            for source_end in binding.sources:
                for dep in self._reads(source_end):
                    readers.setdefault(dep, []).append(i)
            hosted.append(
                self.m_host is not None and any(self._asks_host(source_end) for source_end in binding.sources)
            )
            for uid in dict.fromkeys(self._own_end(target_end) for target_end in binding.targets):
                if uid is not None:
                    self._m_writers.setdefault(uid, []).append(binding)
        self._m_order = sorted(range(len(hosted)), key=lambda i: (hosted[i], i))
        return readers

    def _asks_host(self, end: bindings.SEnd) -> bool:
        """Whether reading a source end may place a child (``_read_child``): its size, its outputs, its laid-out text."""
        if end.kind != "path" or not end.ids or end.mode == bindings.SELF or not self._target(end):
            return False
        if end.mode == bindings.ELEMENT and self.m_slot is not None:
            return False
        first = end.ids[0]
        return first < 0x80000000 or (len(end.ids) == 1 and (first in COMPUTED or first in DISPLAYED))

    def _own_end(self, end: bindings.SEnd) -> int | None:
        """The own property an end names: an own end, or a path on the widget itself to one of its properties (how
        the History Editions' files write them)."""
        if end.kind == "own":
            return end.ids[0]
        if end.kind == "path" and end.mode == bindings.SELF and len(end.ids) == 1 and end.ids[0] in self.m_own_info:
            return end.ids[0]
        return None

    def _reads(self, end: bindings.SEnd) -> tuple:
        """What of this scope a source end reads (``read`` follows the same cases)."""
        if end.kind == "constant" or not end.ids:
            return ()
        if end.kind == "own" or end.mode == bindings.SELF:
            return (("own", end.ids[0]),)
        target = self._target(end)
        if (end.mode == bindings.ELEMENT and self.m_slot is not None) or not target:
            return ()
        return (("child", target), ALL) if len(end.ids) == 1 and end.ids[0] in COMPUTED else (("child", target),)

    def _follow(self, value, rest: list[int]):
        """The game value ``rest`` names under a game object: a sample value, else (placeholders on) the placeholder
        of the binding source being read, its own whichever binding asked for the key first."""
        if not rest:
            return value
        if isinstance(value, SGivenObject):  # an object passed with fields written into it (``passed``)
            hit = dict(value.fields).get(tuple(rest))
            if hit is not None:
                return hit
            value = value.base
        if not isinstance(value, SGameObjectRef):
            return None
        known = self.known(value)  # written into the object by this file or a file that shares it
        for prefix_length in range(len(rest), 0, -1):
            if tuple(rest[:prefix_length]) in known:
                return (
                    self._follow(known[tuple(rest[:prefix_length])], rest[prefix_length:])
                    if prefix_length < len(rest)
                    else known[tuple(rest)]
                )
        key, samples = "/".join([value.key, *(f"{i:08x}" for i in rest)]), self.m_ctx.m_samples
        found = playstate.chosen(self.m_ctx, key)  # a choice made in Play mode wins over the samples
        if found is not None:
            return found
        if key in samples.m_values and key not in samples.m_generated:
            return samples.get(key)
        found = gamedata.lookup(self, key)  # the installed game's data wins over a placeholder
        if found is not None:
            return found
        selected = self._selection(key, rest[-1])
        if selected is not None:  # one selected element per list, whoever reads it (a tab and its page)
            samples.placeholder(key, selected)
            return samples.get(key)
        if self._m_asking is None or not samples.m_fill:
            return samples.get(key)
        locked = placeholders.is_locked(self.m_ctx, value.key)
        guess = placeholders.guess(*self._m_asking, self.describe, locked, previewdefaults.current(self.m_ctx),
                                   self.stored_at, self._readers_of(*self._m_asking))  # fmt: skip
        if guess == placeholders.PICTURE + "picture":  # a slot the source does not name: the image's own name
            guess = placeholders.PICTURE + (self._target_name(self._m_asking[0]) or "picture")
        samples.placeholder(key, guess)  # the sample panel shows the first; made before the read depends on it
        samples.get(key)
        return guess

    def _target_name(self, binding: bindings.SBinding) -> str:
        """The name of the instance a binding's first target is on ("" for the widget itself)."""
        node = self.m_children.get(self._target(binding.targets[0]) or "") if binding.targets else None
        element = self.m_by_node.get(id(node)) if node is not None else None
        return element.name if element is not None and element.name else ""

    def _target(self, end: bindings.SEnd) -> str | None:
        """The instance an end names: a child, or a list's item (DataMode 3), None for the widget itself."""
        if end.mode == bindings.ITEM and self.m_slot is not None:
            return self.m_slot.item
        return end.instance if end.mode == bindings.CHILD else None

    def read(self, end: bindings.SEnd):
        if end.kind == "constant":
            return end.value[1] if isinstance(end.value, tuple) else None
        if end.kind == "own":
            return axis(self.m_own.get(end.ids[0]), end.field)
        if not end.ids:
            return None
        first, rest = end.ids[0], end.ids[1:]
        if end.mode == bindings.SELF:
            if first not in self.m_own_info and rest:  # a component of the widget itself: written, filled, its default
                written = self.m_writes.get(None, {}).get(tuple(end.ids), self.m_given.get(tuple(end.ids)))
                if written is None:  # filled from the player's state (a region info's region) or stored on the root
                    written = self.m_ctx.values(self.m_root).get(tuple(end.ids))
                return axis(written if written is not None else component_default(self.m_ctx, self.m_root, tuple(end.ids)),
                            end.field)  # fmt: skip
            own = self.m_own.get(first)
            if not isinstance(own, SGameObjectRef) and tuple(end.ids) in self.m_given:  # written by the placing file
                return axis(self.m_given[tuple(end.ids)], end.field)
            return axis(self._follow(own, rest), end.field)
        if end.mode == bindings.ELEMENT and self.m_slot is not None:
            if first == DATA_SOURCE:
                return axis(self.m_slot.index, end.field)
            return axis(self._follow(self.m_slot.element, rest), end.field)
        target = self._target(end)
        return axis(self._read_child(target, tuple(end.ids)), end.field) if target else None

    def _read_child(self, target: str, path: tuple[int, ...]):
        written = self.m_writes.get(target, {})
        if path in written:
            return self._take(written[path], target, is_output=False)
        for prefix_length in range(len(path) - 1, 0, -1):
            if path[:prefix_length] in written:
                return self._follow(
                    self._take(written[path[:prefix_length]], target, is_output=False), list(path[prefix_length:])
                )
        if (
            self.m_ctx.m_design
            and target in self.m_children
            and path[1:] == (ANIM_CURRENT,)
            and self._started(target, path)
        ):
            node = self.m_children[target]
            end = animated_end(node, path, self.m_ctx.values(node), written,
                               lambda uid: component_default(self.m_ctx, node, (path[0], uid)))  # fmt: skip
            if end is not None:
                return end
        if self.m_ctx.m_design and target in self.m_children and len(path) == 2 and path[1] in ANIM_RAN:
            return self._ran(target, path[0])  # its Started and Completed have fired (layout.md "A list opens ...")
        if self.m_slot is not None and target == self.m_slot.item and self.m_slot.given:
            hit = dict(self.m_slot.given).get(path)  # a list item's compared values (``listlayout.compared``)
            if hit is not None:
                return hit
        node = self.m_children.get(target)
        if node is None:
            return None
        if path[0] < 0x80000000 and self.m_host is not None:  # an output of the widget file the child places
            value = self._asked("outputs", target, node).get(path[0])
            if value is not None:
                return self._follow(self._take(value, target), list(path[1:]))
        if path[0] in DISPLAYED and len(path) == 1 and self.m_host is not None:  # a label's laid-out text
            shown = self._asked("displayed", target, node).get(path[0])
            if shown is not None:
                return shown
        stored = self.m_ctx.values(node).get(path)
        if stored is None and len(path) != 1:
            return component_default(self.m_ctx, node, path)
        if stored is None and path[0] in COMPUTED and self.m_host is not None:
            return self._asked("size", target, node)
        stored = builtin_value(node, path[0]) if stored is None else stored
        if path == (ALPHA,) and self.m_ctx.m_design and isinstance(stored, (int, float)) and stored < FADED:
            return 1.0  # faded in by an animation: read as the design view draws it (the religion scene's pages)
        return stored

    def _started(self, target: str, path: tuple[int, ...]) -> bool:
        """In the design view, whether the ``Animator`` of an instance (``path``: its component, then the property
        read) has run: one a binding starts at the scene's opening has where that binding fires then (``_fires``: a
        tab's text shows on the selected tab only, the icon-text gap opens there only), one whose start is not known
        has (layout.md "Values the game gives at run time")."""
        seen = False
        for binding in self.m_graph.bindings:
            for target_end in binding.targets:
                if (
                    target_end.kind == "path"
                    and self._target(target_end) == target
                    and target_end.ids == [path[0], ANIM_GO]
                ):
                    fired = self._fires(binding, FIRES_DEPTH)
                    if fired:
                        return True
                    seen = seen or fired is not None
        return not seen

    def _ran(self, target: str, comp: int) -> bool:
        """Whether the scene's opening starts the animation ``comp`` of the child ``target`` (``_fires``)."""
        if self._m_starts is None:  # the bindings that start each child's animation, found once
            self._m_starts = {}
            for binding in self.m_graph.bindings:
                for end in binding.targets:
                    if len(end.ids) == 2 and end.ids[1] == ANIM_GO:
                        self._m_starts.setdefault((self._target(end), end.ids[0]), []).append(binding)
        return any(self._fires(binding, FIRES_DEPTH) is True for binding in self._m_starts.get((target, comp), ()))

    def _fires(self, binding: bindings.SBinding, depth: int) -> bool | None:
        """Whether a binding's event fires as the scene opens: from ``AddedToScene``, or from a change of a flag to
        true (``FalseToTrueChange``: where the flag is on), passed on by ``Assign`` and gated by ``IfTrueAssign`` /
        ``IfFalseAssign``. None where the preview cannot tell (a hover, a click)."""
        name = getattr(operators.OPERATORS.get(binding.operator), "name", "")
        if not binding.sources or depth <= 0:
            return None
        if name == "FalseToTrueChange":
            return truthy(self.read(binding.sources[0]))
        if name not in ("Assign", "IfTrueAssign", "IfFalseAssign"):
            return None
        fired = self._source_fires(binding.sources[0], depth - 1)
        if fired and name != "Assign":
            return (
                truthy(self.read(binding.sources[1])) == (name == "IfTrueAssign") if len(binding.sources) > 1 else None
            )
        return fired

    def _source_fires(self, end: bindings.SEnd, depth: int) -> bool | None:
        """``_fires`` of what a source end reads: the scene's opening, or an own property the file's bindings write."""
        if end.kind == "path" and end.ids == [ADDED]:
            return True
        uid = self._own_end(end)
        if uid is None:
            return None
        found = [self._fires(binding, depth) for binding in self._m_writers.get(uid, ())]
        return True if any(found) else (False if found and all(outcome is False for outcome in found) else None)

    def _asked(self, what: str, target: str, node: dict):
        """What the host says about the child ``target`` (its outputs, its size), asked once per change of what the
        bindings write into it: it places the child's whole widget file."""
        # a group's size reads its children's writes too: a size follows the writes into the instance's subtree
        stamp = self.m_versions.get(target, 0) if what != "size" else self._subtree_version(target)
        hit = self.m_answers.get((what, target))
        if hit is None or hit[0] != stamp:
            ask = {"outputs": self.m_host.outputs, "displayed": self.m_host.displayed}.get(what, self.m_host.size_of)
            hit = (stamp, ask(node, self))
            self.m_answers[(what, target)] = hit
        return hit[1]

    def _subtree_version(self, target: str) -> int:
        """How often the writes into an instance and the instances below it in this file changed: what its size
        depends on of this file's writes."""
        root_details = root_info(self.m_ctx, self.m_root)
        guids = root_details.subtrees.get(target)
        if guids is None:
            element = self.m_by_node.get(id(self.m_children.get(target)))
            guids = root_details.subtrees[target] = (
                tuple(descendant.guid for descendant in element.walk() if descendant.guid)
                if element is not None
                else (target,)
            )
        return sum(self.m_versions.get(guid, 0) for guid in guids)

    def write(self, end: bindings.SEnd, value) -> None:
        if value is None:
            return
        uid = self._own_end(end)
        if uid is not None and uid in self.m_pinned:
            return
        if uid is not None:
            new = with_axis(self.m_own.get(uid), end.field, value)
            if new != self.m_own.get(uid):
                self._m_touched.append(("own", uid))
            self.m_own[uid] = new
        elif end.kind == "path" and end.ids:
            target = self._target(end)
            slot = self.m_writes.setdefault(target, {})
            path = tuple(end.ids)
            if target is not None and len(path) == 1 and isinstance(value, SGameObjectRef):  # an object handed in
                self.m_hands.setdefault(target, {})[path[0]] = value
                value = passed(value, self.known(value, skip=target))
            own = self.m_own.get(path[0]) if target is None and len(path) > 1 else None
            if isinstance(own, SGameObjectRef):  # into an own object: every file that shares it sees it
                mine = self.m_shared.setdefault((own.holder, own.key), {}).setdefault(OWN, {})
                mine[path[1:]] = with_axis(self.known(own).get(path[1:]), end.field, value)
            old = slot.get(path)
            if old is None and end.field != 1 and target in self.m_children:
                old = self._stored(self.m_children[target], path)  # one axis written: the others as stored
            new = with_axis(old, end.field, value)
            if new != old:
                self._m_touched.append(("child", target) if target is not None else ("own", path[0]))  # ``_reads``
                self._bump(target)
            slot[path] = new

    def _readers_of(self, binding: bindings.SBinding, index: int) -> tuple:
        """The other (binding, source index) pairs of this file that read the game value source ``index`` of
        ``binding`` reads: one value has one placeholder state for all of them (``placeholders.flag_for``)."""
        source_end = binding.sources[index]
        same = (source_end.kind, source_end.mode, source_end.instance, tuple(source_end.ids))
        return tuple(
            (other, i)
            for other in self.m_graph.bindings
            for i, end in enumerate(other.sources)
            if other is not binding and (end.kind, end.mode, end.instance, tuple(end.ids)) == same
        )

    def stored_at(self, end: bindings.SEnd):
        """What an instance stores in the built-in a target end names (its ``Visible``, say), for the placeholders: the
        file's own state is the typical one. None for other ends (the declared defaults of helper properties are
        no state)."""
        if end.kind != "path" or len(end.ids) != 1 or end.ids[0] < 0x80000000 or self._own_end(end) is not None:
            return None
        node = self.m_children.get(self._target(end) or "")
        return builtin_value(node, end.ids[0]) if node is not None else None

    def _stored(self, node: dict, path: tuple[int, ...]):
        """What an instance stores under ``path``: its own value of a property (a component's margin, say), else
        its field behind a built-in, else the default the placed widget file or the component declares (one axis
        of a size written keeps the declared other one)."""
        value = self.m_ctx.values(node).get(path)
        if value is not None:
            return value
        if len(path) != 1:
            return component_default(self.m_ctx, node, path)
        if path[0] >= 0x80000000:
            value = builtin_value(node, path[0])
            return self._read_picture_size(node) if value is None and path[0] == SIZE else value
        default = self._placed_info(node, path[0]).get("default")
        return default[1] if isinstance(default, tuple) else None

    def _read_picture_size(self, node: dict) -> tuple[float, float] | None:
        """A picture's size as stored: its image's size times its ``Scale``, the axis a binding that writes the other
        one keeps (the buttons' "Selected_Gold": 54 wide by its binding, 3 high as stored; layout.md "Scale is the
        size"). None for any other instance."""
        image = fields.get(node, "Image")
        if not image:
            return None
        image_root = self.m_ctx.root(image)
        width, height = fields.get(image_root, "Size", (64.0, 64.0)) if image_root is not None else (64.0, 64.0)
        scale = fields.get(node, "Scale") or (1.0, 1.0, 1.0)
        return (float(width) * float(scale[0]), float(height) * float(scale[1]))

    def _placed_info(self, node: dict, uid: int) -> dict:
        """``bindings.property_info`` of a property of the widget file the instance ``node`` places ({} if none)."""
        placed = self.m_ctx.root(fields.get(node, "Template"))
        return next((i for i in root_info(self.m_ctx, placed).props if i["uid"] == uid), {}) if placed else {}

    def describe(self, end: bindings.SEnd) -> tuple[str, str]:
        """(value type, name) of an end a binding writes, for the placeholders."""
        uid = self._own_end(end)
        if uid is not None:
            property_info = self.m_own_info.get(uid, {})
            return self._typed(
                OWN_TYPES.get(property_info.get("type", -1), "value"), self.m_root, uid
            ), property_info.get("name", "")
        if end.kind == "path" and end.ids and end.ids[-1] >= 0x80000000:
            return placeholders.target_kind(end.ids[-1])
        node = self.m_children.get(self._target(end) or "") if end.kind == "path" and len(end.ids) == 1 else None
        if node is not None:  # a property of the widget file the child places, as that file declares it
            property_info = self._placed_info(node, end.ids[0])
            placed = self.m_ctx.root(fields.get(node, "Template"))
            return self._typed(
                OWN_TYPES.get(property_info.get("type", -1), "value"), placed, end.ids[0]
            ), property_info.get("name", "")
        if end.kind == "path" and len(end.ids) > 1:  # a field of a game object: the game's name for it
            return "value", gamedata.field_name(self.m_ctx, end.ids[-1])
        return "value", ""

    def _typed(self, vtype: str, root: dict | None, uid: int) -> str:
        """A text property that the file feeds into a picture's ``DynamicImageId`` holds a picture's name."""
        return "picture" if vtype == "string" and root is not None and feeds_picture(self.m_ctx, root, uid) else vtype

    def _game_side(self, end: bindings.SEnd):
        """What the game's side of a two-way binding holds, where the preview knows it: a value set by hand (or in
        Play mode) under its key, else a selection flag's typical state (``placeholders.selection_for``); None
        otherwise, and the widget's own value flows to the game."""
        if end.kind != "path" or len(end.ids) < 2:
            return None
        if end.mode == bindings.SELF and end.ids[0] in self.m_own_info:
            base = self.m_own.get(end.ids[0])
        else:
            base = self.m_slot.element if end.mode == bindings.ELEMENT and self.m_slot is not None else None
        if not isinstance(base, SGameObjectRef):
            return None
        rest, samples = list(end.ids[1:]), self.m_ctx.m_samples
        key = "/".join([base.key, *(f"{i:08x}" for i in rest)])
        found = playstate.chosen(self.m_ctx, key)
        if found is not None or (key in samples.m_values and key not in samples.m_generated):
            return found if found is not None else samples.get(key)
        return self._selection(key, rest[-1])

    def _selection(self, key: str, uid: int) -> bool | None:
        """A selection flag's typical state at ``key`` (``placeholders.selection_for``), None for any other field or
        with the placeholders off."""
        if not self.m_ctx.m_samples.m_fill:
            return None
        first = lambda field: gamedata.first_of_group(self.m_ctx, field)  # noqa: E731
        index = lambda list_key: playstate.chosen_index(self.m_ctx, list_key)  # noqa: E731
        return placeholders.selection_for(gamedata.field_name(self.m_ctx, uid), key, first, index)

    def _enum_outputs(self, binding: bindings.SBinding, value) -> list | None:
        """What an enum operator gives for the enum entry ``value`` (docs/ui/117/layout.md "Bindings that lay a
        stack out"): ``EnumToBoolean`` one bool per target, true for the entry its ``BindingInfos`` name; ``EnumToValue``
        the value its enum asset's value rule holds for the entry. None: not an enum entry, or no such table."""
        instance_nodes = phoenixedit.objects(binding.node, "ConverterInstance")
        if (
            instance_nodes and value is None and self.m_ctx.m_design
        ):  # unknown: the enum's first entry, as an enum loader's
            value = first_entry(self.m_ctx, fields.get(instance_nodes[0], "EnumAsset"))
        if not instance_nodes or not isinstance(value, int) or isinstance(value, bool):
            return None
        if binding.operator == ENUM_BOOLEAN:
            wanted = {int(fields.get(binding_info, "TargetIndex", 0) or 0): fields.get(binding_info, "EnumValueId")
                      for binding_info in phoenixedit.objects(instance_nodes[0], "BindingInfos")}  # fmt: skip
            return [wanted.get(i) == value for i in range(len(binding.targets))]
        asset = self.m_ctx.root(fields.get(instance_nodes[0], "EnumAsset"))
        rule = fields.get(instance_nodes[0], "RuleId")
        for rec in phoenixedit.objects(asset, "ConversionRules") if asset is not None else []:
            if fields.get(rec, "RuleId") == rule:
                for entry in phoenixedit.objects(rec, "ConversionValues"):
                    if fields.get(entry, "EnumEntryUniqueId") == value:
                        got = fields.get(entry, "Value")
                        return [got[1] if isinstance(got, tuple) else got]
        return None

    def _run(self, binding: bindings.SBinding) -> None:
        values = []
        for i, source_end in enumerate(binding.sources):
            self._m_asking = (binding, i)
            values.append(self.read(source_end))
        self._m_asking = None
        if (
            binding.operator == TWO_WAY
            and binding.targets
            and (game := self._game_side(binding.targets[0])) is not None
        ):
            self.write(binding.sources[0], game)  # what the game's data holds drives the widget (a tab's selection)
            return
        if binding.operator in (operators.ASSIGN, TWO_WAY) and values and values[0] is None:
            return
        outs = (
            self._enum_outputs(binding, values[0] if values else None) if binding.operator in ENUM_CONVERTERS else None
        )
        if outs is None:
            outs = operators.evaluate(binding.operator, values)
        for end, value in zip(binding.targets, outs or [], strict=False):
            self.write(end, value)
