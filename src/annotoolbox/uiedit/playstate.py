"""Play mode's values in the layout: what the input set on the open file's widgets, by the trail of placings to each,
and the calls the layout and the bindings make (docs/ui/117/game-data.md "Play mode: what is built").

``CPlayState`` is the window's; ``CPlayState.trees`` the frozen ``SPlayNode`` per file a layout takes (``layout.Context.
play``, None in Design mode), reaching each placing as a ``SPlayInput``. ``seed`` and ``settle`` are the bindings'
calls; ``chosen``, ``scrolled``, ``narrow``, ``record`` and ``span`` the layout's; ``replay`` places again only the
placings a change reaches (``changed``) and puts their items into the last frame.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace

from ..formats import phoenixedit
from . import animations, arrange, fields, gamedata, listlayout, operators
from .scopevalues import PASSES, inputs_of

PLAY_UID = 0x0FFFFFF1  # what a placing instance passes for Play mode: below every id the files use
ANIMATION, START = 2, 0x8000001F  # an end on an animation (DataMode 2) and its Start action
RISING, FALLING, CHANGED = "f8fa428f", "5b0e8ff5", "4c9ce34d"  # FalseToTrueChange, TrueToFalseChange, ValueChange
ENUM_EVENT, TOGGLE = "5e3ef09a", "599c5545"  # EnumToEvent, EventToBoolChange
TOKEN = "play"  # what a read of a choice notes in the kept results (``chosen``)
STEP = 1 / 30  # the step of an animation played over time (seconds; ``CPlayState.ages``)
ELEMENT = gamedata.ELEMENT
_g_baseline = threading.local()  # set while a placing's design state is evaluated: it takes no Play mode value

Trail = tuple[tuple[str | None, int | None], ...]  # (instance GUID, the list index of its file's copy) per placing


def choice_tokens(name: str) -> list[tuple[str, str]]:
    """What a change of the choice ``name`` (a field's path of names) drops: its readers, and what looked for a
    choice by the field's name or below its object before it was made (``chosen_index``, ``gameplans.chosen_value``)."""
    parent, _, last = name.rpartition(".")
    return [(TOKEN, name), (TOKEN, f"tail:{last}"), (TOKEN, f"under:{parent}")]


@dataclass(frozen=True)
class SPlayNode:
    """What Play mode set on one instance (``values``: path -> value, sorted), on the instances of the file it places
    (``children`` by GUID and list index), for a list the first element shown (``first``), and how many seconds ago
    its values changed (``age``; None: not timed, the animations a change starts are shown at their end)."""

    values: tuple = ()
    children: tuple = ()
    first: int = 0
    age: float | None = None


EMPTY = SPlayNode()


@dataclass(frozen=True)
class SPlayInput:
    """A placing's Play mode values, and what it is given without Play mode (``base``: uid -> value, sorted), against
    which a change shows as the event the game would fire."""

    node: SPlayNode
    base: tuple = ()


@dataclass(frozen=True, eq=False)
class SHit:
    """The area of an instance the input can reach, as one layout left it: its matrix and size, its ``trail``, the
    scope it was placed in, the element and the values it had (its stored values, what the bindings wrote, the game's)."""

    matrix: tuple
    size: tuple[float, float]
    trail: Trail
    scope: object
    element: object
    values: dict


@dataclass(frozen=True, eq=False)
class SSpan:
    """Where one placing of a widget file left its draw items and hits in a frame (start, end), what it was placed
    with (the file ``key``, its matrix, inputs and ``layout.TemplateRun``) and the placings inside it, their starts
    counted from its own (``inner``): what ``replay`` places again alone."""

    trail: Trail
    key: str
    world: tuple
    inputs: dict
    run: object
    items: tuple[int, int]
    hits: tuple[int, int]
    inner: tuple = ()


@dataclass
class SChoices:
    """The choices Play mode keeps in the Preview settings, by the name of the field they were written to
    (``previewdefaults.play_choices``), and the names of the keys read so far (kept per game data stamp)."""

    values: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    stamp: object = None


def _sorted(items) -> tuple:
    return tuple(sorted(items, key=lambda item: repr(item[0])))


def freeze(entries: dict[Trail, dict], firsts: dict[Trail, int] | None = None,
           ages: dict[Trail, float] | None = None) -> SPlayNode:  # fmt: skip
    """The tree of the values by trail (``entries``), the lists' first shown elements (``firsts``) and the ages of
    the values (``ages``, of the instances that hold values)."""
    root: dict = {}

    def node_for(trail: Trail) -> dict:
        here = root
        for step in trail:
            here = here.setdefault("children", {}).setdefault(step, {})
        return here

    for trail, values in entries.items():
        node_for(trail).setdefault("values", {}).update(values)
    for trail, first in (firsts or {}).items():
        node_for(trail)["first"] = int(first)
    for trail, age in (ages or {}).items():
        if trail in entries or any(
            len(other_trail) > len(trail) and other_trail[: len(trail)] == trail for other_trail in entries
        ):
            node_for(trail)["age"] = age

    def make(node_data: dict) -> SPlayNode:
        kids = _sorted((step, make(sub)) for step, sub in node_data.get("children", {}).items())
        return SPlayNode(
            _sorted(node_data.get("values", {}).items()), kids, node_data.get("first", 0), node_data.get("age")
        )

    return make(root)


def youngest(node: SPlayNode) -> float | None:
    """The age of the latest change at or below a node (what a file placed there has seen start), None if untimed."""
    ages = [age for age in (node.age, *(youngest(child) for _, child in node.children)) if age is not None]
    return min(ages) if ages else None


class CPlayState:
    """The values Play mode set while the window plays a file: ``kept`` (a tab, a checkbox, a slider: they stay for
    the session), ``passing`` (the pointer's hover and press: the open file's only) and the lists' scrolls."""

    def __init__(self) -> None:
        self.m_kept: dict[str, dict[Trail, dict]] = {}
        self.m_scrolls: dict[str, dict[Trail, int]] = {}
        self.m_passing: dict[Trail, dict] = {}
        self.m_since: dict[str, dict[Trail, float]] = {}  # when the values on an instance changed (``touch``)

    def keep(self, file: str, trail: Trail, path: tuple, value) -> None:
        self.m_kept.setdefault(file, {}).setdefault(trail, {})[path] = value

    def touch(self, file: str, before: dict[Trail, dict], after: dict[Trail, dict], now: float) -> None:
        """Note the time the values on each instance changed from ``before`` to ``after``: the animations the change
        starts play from then (``ages``)."""
        for trail in {*before, *after}:
            if before.get(trail) != after.get(trail):
                self.m_since.setdefault(file, {})[trail] = now

    def ages(self, file: str, now: float, longest: float) -> dict[Trail, float]:
        """How long ago the values on each instance changed, in steps of ``STEP``, at most ``longest`` (the longest
        animation: after it every age stays the same, and what was placed with it is kept)."""
        return {
            trail: min(longest, round((now - changed_at) / STEP) * STEP)
            for trail, changed_at in self.m_since.get(file, {}).items()
        }

    def running(self, file: str, now: float, longest: float) -> bool:
        """Whether an animation a change started may still be playing."""
        return any(age < longest for age in self.ages(file, now, longest).values())

    def value(self, file: str, trail: Trail, path: tuple, default=None):
        return self.m_kept.get(file, {}).get(trail, {}).get(path, default)

    def scroll(self, file: str, trail: Trail, first: int) -> None:
        self.m_scrolls.setdefault(file, {})[trail] = max(0, int(first))

    def first(self, file: str, trail: Trail) -> int:
        return self.m_scrolls.get(file, {}).get(trail, 0)

    def clear(self, file: str | None = None) -> None:
        """Forget what the input set on ``file`` (every file: None)."""
        for kept in (self.m_kept, self.m_scrolls, self.m_since):
            if file is None:
                kept.clear()
            else:
                kept.pop(file, None)
        self.m_passing.clear()

    def tree(self, file: str, is_passing: bool = True, ages: dict[Trail, float] | None = None) -> SPlayNode:
        """What a layout of ``file`` takes: the kept values with the passing ones over them (``is_passing``: the file is
        the open one), timed by ``ages`` (``CPlayState.ages``; None: untimed)."""
        entries = {kept_trail: dict(kept_values) for kept_trail, kept_values in self.m_kept.get(file, {}).items()}
        for trail, values in self.m_passing.items() if is_passing else ():
            entries.setdefault(trail, {}).update(values)
        return freeze(entries, self.m_scrolls.get(file, {}), ages)

    def entries(self, file: str) -> dict[Trail, dict]:
        """The values a layout of the open ``file`` takes by trail (what ``touch`` compares)."""
        out = {kept_trail: dict(kept_values) for kept_trail, kept_values in self.m_kept.get(file, {}).items()}
        for trail, values in self.m_passing.items():
            out.setdefault(trail, {}).update(values)
        return out

    def trees(self, current: str, ages: dict[Trail, float] | None = None) -> dict[str, SPlayNode]:
        """Every file's tree, by file key (``layout.Context.play``): the open file ``current`` with the passing values
        and the ``ages`` of its values."""
        files = {*self.m_kept, *self.m_scrolls, current}
        return {
            file_key: self.tree(file_key, file_key == current, ages if file_key == current else None)
            for file_key in files
        }


# the layout's calls


def record(placer, kid, scope, world) -> None:
    """Leave the area of a placed instance in the frame (``layout.Frame.hits``): a container (it places a file the
    input may reach), a list's item or an instance with components (a mouse handler, a tab, an info tip)."""
    element = kid.element
    item = scope.m_slot is not None and scope.m_slot.item == element.guid
    if element.kind != "container" and not item and not arrange.components(element.node):
        return
    index = scope.m_slot.index if scope.m_slot is not None else None
    trail = (*getattr(placer, "m_trail", ()), (element.guid, index))
    placer.m_frame.hits.append(SHit(world, kid.slot.size, trail, scope, element, kid.overrides))


def span(frame, placing: tuple, items: list, hits: list, spans: list) -> None:
    """Note in the frame where a placed file's items and hits land (``layout.Frame.spans``), before they join it;
    ``placing``: its trail, file key, matrix, inputs and run; ``spans`` those of the placings inside it."""
    item_start, hit_start = len(frame.items), len(frame.hits)
    frame.spans.append(
        SSpan(*placing, (item_start, item_start + len(items)), (hit_start, hit_start + len(hits)), tuple(spans))
    )


def narrow(inputs: dict, slot) -> dict:
    """What a list's copy ``slot`` takes of its file's Play mode input: only the values on the instances of that copy,
    none where it has none and is given what it is in the design state (it is then kept from Design mode)."""
    given = inputs.get(PLAY_UID) if slot is not None else None
    if not isinstance(given, SPlayInput) or not given.node.children:
        return inputs
    mine = tuple(child for child in given.node.children if child[0][1] == slot.index)
    if len(mine) == len(given.node.children):
        return inputs
    node = SPlayNode(given.node.values, mine, 0, given.node.age if mine or given.node.values else None)
    out = {key: value for key, value in inputs.items() if key != PLAY_UID}
    if node == EMPTY and dict(given.base) == {
        key: value for key, value in out.items() if key != listlayout.TOTAL_ITEMS
    }:
        return out
    return {**out, PLAY_UID: SPlayInput(node, given.base)}


def scrolled(inputs: dict, count: int, element):
    """A list's elements from the first Play mode scrolled it to: (count shown, element key by shown index)."""
    given = inputs.get(PLAY_UID)
    first = min(given.node.first, count - 1) if isinstance(given, SPlayInput) else 0
    if first <= 0:
        return count, element
    return count - first, lambda i: element(first + i)


def name_of(ctx, key: str) -> str:
    """The name of the field a sample key stands for: its owner, then the field names the game's data gives (an id
    without one in hex, ``#3ee3bb5d``; an element as ``[i]``)."""
    owner, _, rest = key.partition("/")
    game = getattr(ctx, "m_game_data", None)
    parts = [owner]
    for part in rest.split("/") if rest else ():
        uid = int(part, 16) if len(part) == 8 else -1
        if uid >= ELEMENT:
            parts.append(f"[{uid - ELEMENT}]")
        else:
            parts.append((game.field_name(uid) if game is not None and uid >= 0 else "") or f"#{part}")
    return ".".join(parts)


def chosen(ctx, key: str):
    """The value Play mode chose for the field ``key`` (kept in the Preview settings), None for any other; what reads
    it is dropped when the choice changes (``TOKEN``)."""
    choices = getattr(ctx, "m_play_choices", None)
    if choices is None:
        return None
    stamp = gamedata.stamp(ctx)
    if choices.stamp != stamp:
        choices.names, choices.stamp = {}, stamp
    name = choices.names.get(key)
    if name is None:
        name = choices.names[key] = name_of(ctx, key)
    ctx.m_memo.note((TOKEN, name))
    return choices.values.get(name)


def chosen_index(ctx, list_key: str) -> int | None:
    """The element of the list at ``list_key`` Play mode selected: the choice of a field of the object holding the
    list that names a selected index or id (the options tabs: ``TabsData.SelectedTabID``, which the click on a tab
    writes), None without one; its elements' selection flags follow it (``placeholders.selection_for``)."""
    choices = getattr(ctx, "m_play_choices", None)
    if choices is None:
        return None
    owner = name_of(ctx, list_key.rsplit("/", 1)[0])
    ctx.m_memo.note((TOKEN, f"under:{owner}"))  # a choice made later (``choice_tokens``)
    prefix = owner + "."
    for name, value in choices.values.items():
        rest = name[len(prefix) :].lower() if name.startswith(prefix) else ""
        if rest and "." not in rest and "selected" in rest and rest.endswith(("id", "index")):
            if isinstance(value, int) and not isinstance(value, bool):
                ctx.m_memo.note((TOKEN, name))
                return value
    return None


# the bindings' calls


def seed(scope, inputs: dict, owner_key: str) -> None:
    """Before a scope's bindings run: the values Play mode set on its instances written in, each instance's own part of
    the tree passed down with what it is given without Play mode. The design state of the placing is kept beside it
    (``settle`` compares with it); a placing without Play mode values costs nothing."""
    scope.play_base = None
    scope.play_total = inputs.get(listlayout.TOTAL_ITEMS) if scope.m_slot is not None else None  # a list's count
    trees = getattr(scope.m_ctx, "m_play", None)
    if trees is None or getattr(_g_baseline, "on", False):
        return
    given, tree = inputs.get(PLAY_UID), trees.get(scope.m_key, EMPTY)
    if isinstance(given, SPlayInput):
        node, base = given.node, dict(given.base)
        if listlayout.TOTAL_ITEMS in inputs:  # what the list layout adds: its design state was kept with it
            base[listlayout.TOTAL_ITEMS] = inputs[listlayout.TOTAL_ITEMS]
    elif owner_key == scope.m_key and (tree.values or tree.children):
        node, base = tree, dict(inputs)
    else:
        return
    design = _design(scope, base, owner_key)
    index = scope.m_slot.index if scope.m_slot is not None else None
    for path, value in node.values:
        if len(path) > 1:  # a component of the instance: the file it places reads it as its own
            scope.m_writes.setdefault(None, {})[path] = value
    given_to = set()
    for (guid, child_index), child in node.children:
        if child_index != index or guid not in scope.m_children:
            continue
        written = scope.m_writes.setdefault(guid, {})
        written.update(child.values)
        written[(PLAY_UID,)] = SPlayInput(child, _given(scope, design, guid))
        scope._bump(guid)
        given_to.add(guid)
    scope.play_base = (design, given_to, youngest(node))


def settle(scope) -> None:
    """After a scope's bindings ran with Play mode's values: the animations an event or a flag started are put at
    their end, a toggle flips, and the instances given other values than in the design state pass that on (their
    files then see the change too); the bindings run again where anything moved."""
    found = getattr(scope, "play_base", None)
    if found is None:
        return
    design, given_to, age = found
    host, design.m_host = design.m_host, scope.m_host  # its reads of the placed files' outputs ask this placing
    try:
        moved = _start(scope, design, age)
        moved = _pass_on(scope, design, given_to, age) or moved
    finally:
        design.m_host = host
    if moved:
        scope.run(PASSES)


def _design(scope, base: dict, owner_key: str):
    """The scope of the same placing without Play mode: the kept one where the layout made it before."""
    from .scope import CScope

    _g_baseline.on = True
    try:
        host = scope.m_host
        if host is not None and hasattr(host, "scope"):
            return host.scope(scope.m_key, scope.m_root, base, owner_key, scope.m_slot, owner_key != scope.m_key)
        return CScope(scope.m_key, scope.m_root, base, scope.m_ctx, owner_key, host, scope.m_slot)
    finally:
        _g_baseline.on = False


def _given(scope, design, guid: str) -> tuple:
    """What the instance ``guid`` passes into its file in the design state (``layout.CPlacer.own``'s order)."""
    node = scope.m_children[guid]
    element = scope.m_by_node.get(id(node))
    filled = getattr(scope.m_host, "m_filled", {}).get(guid, {}) if scope.m_host is not None else {}
    live = scope.m_ctx.live_of(element) if element is not None and hasattr(scope.m_ctx, "live_of") else {}
    over = {**scope.m_ctx.values(node), **design.m_writes.get(guid, {}), **filled, **live}
    return _sorted(inputs_of(over, node).items())


def _read(scope, end):
    scope._m_asking = None
    return scope.read(end)


def _fires(scope, design, binding, target: int) -> bool:
    """Whether the binding ``b`` fires its target ``target`` now and did not in the design state: the event the game
    sends when the value changed."""
    now = [_read(scope, end) for end in binding.sources]
    then = [_read(design, end) for end in binding.sources]
    if now == then:
        return False
    if binding.operator == RISING:
        return bool(now[0]) and not bool(then[0])
    if binding.operator == FALLING:
        return not now[0] and bool(then[0])
    if binding.operator == CHANGED:
        return True
    if binding.operator == ENUM_EVENT:
        return _enum_target(binding, target) == now[0]
    outs = operators.evaluate(binding.operator, now)
    return bool(outs) and target < len(outs) and bool(outs[target])


def _enum_target(binding, target: int):
    """The enum entry for which an EnumToEvent fires ``target`` (its ``BindingInfos``)."""
    for instance_node in phoenixedit.objects(binding.node, "ConverterInstance")[:1]:
        for binding_info in phoenixedit.objects(instance_node, "BindingInfos"):
            if int(fields.get(binding_info, "TargetIndex", 0) or 0) == target:
                return fields.get(binding_info, "EnumValueId")
    return None


def _start(scope, design, age: float | None = None) -> bool:
    """The animations of the file a change started, at the time since the change (``age``; untimed: at their end),
    and the toggles a change fired, flipped."""
    graph, animation_by_guid, moved = scope.m_graph, None, False
    for binding in graph.bindings:
        for i, target_end in enumerate(binding.targets):
            if binding.operator == TOGGLE and _fires(scope, design, binding, i):
                scope.write(target_end, not bool(_read(design, target_end)))
                moved = True
                continue
            if (
                target_end.kind != "path"
                or target_end.mode != ANIMATION
                or not target_end.ids
                or target_end.ids[-1] != START
                or not target_end.steps
            ):
                continue
            if not _fires(scope, design, binding, i):
                continue
            animation_by_guid = (
                animation_by_guid
                if animation_by_guid is not None
                else {animation.guid: animation for animation in animations.read(scope.m_root)}
            )
            anim = animation_by_guid.get(target_end.steps[-1][1])
            if anim is None:
                continue
            for index, value in anim.values_at(anim.seconds if age is None else min(age, anim.seconds)):
                if index < len(graph.ends):
                    scope.write(graph.ends[index], value)
                    moved = True
    return moved


def _pass_on(scope, design, given_to: set, age: float | None = None) -> bool:
    """Give the containers whose values differ from the design state their Play mode input (no values of their own,
    the ``age`` of the change), so the files they place compare with their design state too."""
    moved, node_of = False, SPlayNode(age=age)
    for guid, node in scope.m_children.items():
        if guid in given_to:
            continue
        now = {path: value for path, value in scope.m_writes.get(guid, {}).items() if path != (PLAY_UID,)}
        if now == design.m_writes.get(guid, {}):
            continue
        element = scope.m_by_node.get(id(node))
        if element is None or element.kind != "container":
            continue
        scope.m_writes.setdefault(guid, {})[(PLAY_UID,)] = SPlayInput(node_of, _given(scope, design, guid))
        scope._bump(guid)
        moved = True
    return moved


# placing again only what a change reaches (docs/ui/117/game-data.md "Play mode: what is built", "Speed")


def changed(old: SPlayNode, new: SPlayNode, trail: Trail = ()) -> list[Trail]:
    """The placings to place again from the tree ``old`` to ``new``: the one holding an instance whose values changed
    (its bindings take them), the list whose first shown element moved; none inside another of them."""
    if old == new:
        return []
    found = []
    if old.values != new.values or old.age != new.age:
        found.append(trail[:-1])
    if old.first != new.first:
        found.append(trail)
    old_children, new_children = dict(old.children), dict(new.children)
    for step in sorted({*old_children, *new_children}, key=repr):
        found += changed(old_children.get(step, EMPTY), new_children.get(step, EMPTY), (*trail, step))
    out: list[Trail] = []
    for candidate in sorted(set(found), key=len):
        if not any(candidate[: len(included)] == included for included in out):
            out.append(candidate)
    return out


def node_at(tree: SPlayNode, trail: Trail) -> SPlayNode:
    for step in trail:
        tree = dict(tree.children).get(step, EMPTY)
    return tree


def replay(ctx, key: str, root: dict, frame, old: dict | None, new: dict):
    """The open file ``key`` laid out with Play mode's values ``new`` from its ``frame``, laid out with ``old``: only
    the placings a change reaches are placed again, where they were; one whose size or outputs then differ from
    before places the file around it again too, up to the whole file (``layout.place``)."""
    from . import layout

    ctx.m_play = new
    if frame is None or old is None or ctx.m_epoch != ctx.conditions():
        return layout.place(key, root, ctx)
    out, tree = frame, new.get(key, EMPTY)
    for trail in changed(old.get(key, EMPTY), tree):
        while trail:
            found = _find(out.spans, trail)
            again = _again(ctx, out, found, node_at(tree, trail)) if found is not None else None
            if again is not None:
                out = again
                break
            trail = trail[:-1]
        else:
            return layout.place(key, root, ctx)
    return out


def walk_spans(spans):
    """Every placing of a frame's ``spans``, the ones inside the others too."""
    for placing in spans:
        yield placing
        yield from walk_spans(placing.inner)


def _find(spans, trail: Trail, origin: tuple[int, int] = (0, 0)):
    """The placing ``trail`` among ``spans`` and where its items and hits start in the frame."""
    for placing in spans:
        if placing.trail == trail:
            return placing, origin[0] + placing.items[0], origin[1] + placing.hits[0]
        if placing.trail == trail[: len(placing.trail)]:
            return _find(placing.inner, trail, (origin[0] + placing.items[0], origin[1] + placing.hits[0]))
    return None


def _again(ctx, frame, found, node: SPlayNode):
    """The frame with one placing placed again with its new Play mode values (``node``), None when its size or its
    outputs differ from before: the file that places it reads them, so it has to be placed again instead."""
    from . import layout

    placing, item_start, hit_at = found
    root = ctx.root(placing.key)
    given = placing.inputs.get(PLAY_UID)
    base = given.base if isinstance(given, SPlayInput) else _sorted(placing.inputs.items())
    inputs = {k: v for k, v in placing.inputs.items() if k != PLAY_UID}
    if node != EMPTY or dict(base) != inputs:
        inputs[PLAY_UID] = SPlayInput(node, base)
    if root is None:
        return None
    if inputs == placing.inputs:
        return frame
    if _reaches(ctx, frame, placing, root, placing.inputs) != _reaches(ctx, frame, placing, root, inputs):
        return None
    for _ in range(layout.REPLACE):
        ctx.m_memo.m_dropped = 0
        sub = layout.SFrame(frame.size)
        ctx.m_memo.gather(
            lambda target_frame=sub: layout.CPlacer(ctx, target_frame)._template(
                placing.key, root, placing.world, inputs, placing.run
            )
        )
        if not ctx.m_memo.m_dropped:
            break
    item_count, hit_count = placing.items[1] - placing.items[0], placing.hits[1] - placing.hits[0]
    new = SSpan(placing.trail, placing.key, placing.world, inputs, placing.run, (placing.items[0], placing.items[0] + len(sub.items)),
               (placing.hits[0], placing.hits[0] + len(sub.hits)), tuple(sub.spans))  # fmt: skip
    return replace(
        frame,
        items=[*frame.items[:item_start], *sub.items, *frame.items[item_start + item_count :]],
        hits=[*frame.hits[:hit_at], *sub.hits, *frame.hits[hit_at + hit_count :]],
        spans=_spliced(frame.spans, new, len(sub.items) - item_count, len(sub.hits) - hit_count),
    )


def _reaches(ctx, frame, placing: SSpan, root: dict, inputs: dict) -> tuple:
    """What the file placing ``s`` reads of it given ``inputs``: its size and its outputs, less those that hand back
    what it was given (the placing file reads its own write of those, ``CScope._read_child``)."""
    from . import layout

    placer = layout.CPlacer(ctx, layout.SFrame(frame.size))
    placer.m_stack = list(placing.run.stack)
    size, outputs = placer.measure(placing.key, root, inputs, placing.run.data_owner)
    return size, {uid: value for uid, value in outputs.items() if inputs.get(uid, _NOTHING) != value}


_NOTHING = object()


def _spliced(spans, new: SSpan, item_shift: int, hit_shift: int) -> list:
    """``spans`` with ``new`` in place of the placing of its trail, the ones after it moved by its change in length."""
    out, done = [], False
    for placing in spans:
        if done:
            out.append(
                replace(
                    placing,
                    items=(placing.items[0] + item_shift, placing.items[1] + item_shift),
                    hits=(placing.hits[0] + hit_shift, placing.hits[1] + hit_shift),
                )
            )
        elif placing.trail == new.trail:
            out.append(new)
            done = True
        elif placing.trail == new.trail[: len(placing.trail)]:
            out.append(replace(placing, items=(placing.items[0], placing.items[1] + item_shift), hits=(placing.hits[0], placing.hits[1] + hit_shift),
                               inner=tuple(_spliced(placing.inner, new, item_shift, hit_shift))))  # fmt: skip
            done = True
        else:
            out.append(placing)
    return out
