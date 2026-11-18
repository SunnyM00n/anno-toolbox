"""Play mode's values in the layout: what the input set on the open file's widgets, by the trail of placings to each,
and the calls the layout and the bindings make (docs/ui/117/game-data.md "Play mode: what is built").

``PlayState`` is the window's; ``PlayState.trees`` the frozen ``PlayNode`` per file a layout takes (``layout.Context.
play``, None in Design mode), reaching each placing as a ``PlayInput``. ``seed`` and ``settle`` are the bindings'
calls; ``chosen``, ``scrolled``, ``narrow``, ``record`` and ``span`` the layout's; ``replay`` places again only the
placings a change reaches (``changed``) and puts their items into the last frame.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace

from . import animations, arrange, gamedata, listlayout, operators

PLAY_UID = 0x0FFFFFF1  # what a placing instance passes for Play mode: below every id the files use
ANIMATION, START = 2, 0x8000001F  # an end on an animation (DataMode 2) and its Start action
RISING, FALLING, CHANGED = "f8fa428f", "5b0e8ff5", "4c9ce34d"  # FalseToTrueChange, TrueToFalseChange, ValueChange
ENUM_EVENT, TOGGLE = "5e3ef09a", "599c5545"  # EnumToEvent, EventToBoolChange
TOKEN = "play"  # what a read of a choice notes in the kept results (``chosen``)
STEP = 1 / 30  # the step of an animation played over time (seconds; ``PlayState.ages``)
ELEMENT = gamedata.ELEMENT
_BASELINE = threading.local()  # set while a placing's design state is evaluated: it takes no Play mode value

Trail = tuple[tuple[str | None, int | None], ...]  # (instance GUID, the list index of its file's copy) per placing


def choice_tokens(name: str) -> list[tuple[str, str]]:
    """What a change of the choice ``name`` (a field's path of names) drops: its readers, and what looked for a
    choice by the field's name or below its object before it was made (``chosen_index``, ``gameplans.chosen_value``)."""
    parent, _, last = name.rpartition(".")
    return [(TOKEN, name), (TOKEN, f"tail:{last}"), (TOKEN, f"under:{parent}")]


@dataclass(frozen=True)
class PlayNode:
    """What Play mode set on one instance (``values``: path -> value, sorted), on the instances of the file it places
    (``children`` by GUID and list index), for a list the first element shown (``first``), and how many seconds ago
    its values changed (``age``; None: not timed, the animations a change starts are shown at their end)."""

    values: tuple = ()
    children: tuple = ()
    first: int = 0
    age: float | None = None


EMPTY = PlayNode()


@dataclass(frozen=True)
class PlayInput:
    """A placing's Play mode values, and what it is given without Play mode (``base``: uid -> value, sorted), against
    which a change shows as the event the game would fire."""

    node: PlayNode
    base: tuple = ()


@dataclass(frozen=True, eq=False)
class Hit:
    """The area of an instance the input can reach, as one layout left it: its matrix and size, its ``trail``, the
    scope it was placed in, the element and the values it had (its stored values, what the bindings wrote, the game's)."""

    matrix: tuple
    size: tuple[float, float]
    trail: Trail
    scope: object
    el: object
    values: dict


@dataclass(frozen=True, eq=False)
class Span:
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
class Choices:
    """The choices Play mode keeps in the Preview settings, by the name of the field they were written to
    (``previewdefaults.play_choices``), and the names of the keys read so far (kept per game data stamp)."""

    values: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    stamp: object = None


def _sorted(items) -> tuple:
    return tuple(sorted(items, key=lambda kv: repr(kv[0])))


def freeze(entries: dict[Trail, dict], firsts: dict[Trail, int] | None = None,
           ages: dict[Trail, float] | None = None) -> PlayNode:  # fmt: skip
    """The tree of the values by trail (``entries``), the lists' first shown elements (``firsts``) and the ages of
    the values (``ages``, of the instances that hold values)."""
    root: dict = {}

    def at(trail: Trail) -> dict:
        here = root
        for step in trail:
            here = here.setdefault("children", {}).setdefault(step, {})
        return here

    for trail, values in entries.items():
        at(trail).setdefault("values", {}).update(values)
    for trail, first in (firsts or {}).items():
        at(trail)["first"] = int(first)
    for trail, age in (ages or {}).items():
        if trail in entries or any(len(t) > len(trail) and t[: len(trail)] == trail for t in entries):
            at(trail)["age"] = age

    def make(d: dict) -> PlayNode:
        kids = _sorted((step, make(sub)) for step, sub in d.get("children", {}).items())
        return PlayNode(_sorted(d.get("values", {}).items()), kids, d.get("first", 0), d.get("age"))

    return make(root)


def youngest(node: PlayNode) -> float | None:
    """The age of the latest change at or below a node (what a file placed there has seen start), None if untimed."""
    ages = [a for a in (node.age, *(youngest(c) for _, c in node.children)) if a is not None]
    return min(ages) if ages else None


class PlayState:
    """The values Play mode set while the window plays a file: ``kept`` (a tab, a checkbox, a slider: they stay for
    the session), ``passing`` (the pointer's hover and press: the open file's only) and the lists' scrolls."""

    def __init__(self) -> None:
        self.kept: dict[str, dict[Trail, dict]] = {}
        self.scrolls: dict[str, dict[Trail, int]] = {}
        self.passing: dict[Trail, dict] = {}
        self.since: dict[str, dict[Trail, float]] = {}  # when the values on an instance changed (``touch``)

    def keep(self, file: str, trail: Trail, path: tuple, value) -> None:
        self.kept.setdefault(file, {}).setdefault(trail, {})[path] = value

    def touch(self, file: str, before: dict[Trail, dict], after: dict[Trail, dict], now: float) -> None:
        """Note the time the values on each instance changed from ``before`` to ``after``: the animations the change
        starts play from then (``ages``)."""
        for trail in {*before, *after}:
            if before.get(trail) != after.get(trail):
                self.since.setdefault(file, {})[trail] = now

    def ages(self, file: str, now: float, longest: float) -> dict[Trail, float]:
        """How long ago the values on each instance changed, in steps of ``STEP``, at most ``longest`` (the longest
        animation: after it every age stays the same, and what was placed with it is kept)."""
        return {t: min(longest, round((now - at) / STEP) * STEP) for t, at in self.since.get(file, {}).items()}

    def running(self, file: str, now: float, longest: float) -> bool:
        """Whether an animation a change started may still be playing."""
        return any(age < longest for age in self.ages(file, now, longest).values())

    def value(self, file: str, trail: Trail, path: tuple, default=None):
        return self.kept.get(file, {}).get(trail, {}).get(path, default)

    def scroll(self, file: str, trail: Trail, first: int) -> None:
        self.scrolls.setdefault(file, {})[trail] = max(0, int(first))

    def first(self, file: str, trail: Trail) -> int:
        return self.scrolls.get(file, {}).get(trail, 0)

    def clear(self, file: str | None = None) -> None:
        """Forget what the input set on ``file`` (every file: None)."""
        for kept in (self.kept, self.scrolls, self.since):
            if file is None:
                kept.clear()
            else:
                kept.pop(file, None)
        self.passing.clear()

    def tree(self, file: str, passing: bool = True, ages: dict[Trail, float] | None = None) -> PlayNode:
        """What a layout of ``file`` takes: the kept values with the passing ones over them (``passing``: the file is
        the open one), timed by ``ages`` (``PlayState.ages``; None: untimed)."""
        entries = {t: dict(v) for t, v in self.kept.get(file, {}).items()}
        for trail, values in self.passing.items() if passing else ():
            entries.setdefault(trail, {}).update(values)
        return freeze(entries, self.scrolls.get(file, {}), ages)

    def entries(self, file: str) -> dict[Trail, dict]:
        """The values a layout of the open ``file`` takes by trail (what ``touch`` compares)."""
        out = {t: dict(v) for t, v in self.kept.get(file, {}).items()}
        for trail, values in self.passing.items():
            out.setdefault(trail, {}).update(values)
        return out

    def trees(self, current: str, ages: dict[Trail, float] | None = None) -> dict[str, PlayNode]:
        """Every file's tree, by file key (``layout.Context.play``): the open file ``current`` with the passing values
        and the ``ages`` of its values."""
        files = {*self.kept, *self.scrolls, current}
        return {f: self.tree(f, f == current, ages if f == current else None) for f in files}


# the layout's calls


def record(placer, kid, scope, world) -> None:
    """Leave the area of a placed instance in the frame (``layout.Frame.hits``): a container (it places a file the
    input may reach), a list's item or an instance with components (a mouse handler, a tab, an info tip)."""
    el = kid.el
    item = scope.slot is not None and scope.slot.item == el.guid
    if el.kind != "container" and not item and not arrange.components(el.node):
        return
    index = scope.slot.index if scope.slot is not None else None
    trail = (*getattr(placer, "trail", ()), (el.guid, index))
    placer.frame.hits.append(Hit(world, kid.slot.size, trail, scope, el, kid.overrides))


def span(frame, placing: tuple, items: list, hits: list, spans: list) -> None:
    """Note in the frame where a placed file's items and hits land (``layout.Frame.spans``), before they join it;
    ``placing``: its trail, file key, matrix, inputs and run; ``spans`` those of the placings inside it."""
    i, h = len(frame.items), len(frame.hits)
    frame.spans.append(Span(*placing, (i, i + len(items)), (h, h + len(hits)), tuple(spans)))


def narrow(inputs: dict, slot) -> dict:
    """What a list's copy ``slot`` takes of its file's Play mode input: only the values on the instances of that copy,
    none where it has none and is given what it is in the design state (it is then kept from Design mode)."""
    given = inputs.get(PLAY_UID) if slot is not None else None
    if not isinstance(given, PlayInput) or not given.node.children:
        return inputs
    mine = tuple(c for c in given.node.children if c[0][1] == slot.index)
    if len(mine) == len(given.node.children):
        return inputs
    node = PlayNode(given.node.values, mine, 0, given.node.age if mine or given.node.values else None)
    out = {k: v for k, v in inputs.items() if k != PLAY_UID}
    if node == EMPTY and dict(given.base) == {k: v for k, v in out.items() if k != listlayout.TOTAL_ITEMS}:
        return out
    return {**out, PLAY_UID: PlayInput(node, given.base)}


def scrolled(inputs: dict, n: int, element):
    """A list's elements from the first Play mode scrolled it to: (count shown, element key by shown index)."""
    given = inputs.get(PLAY_UID)
    first = min(given.node.first, n - 1) if isinstance(given, PlayInput) else 0
    if first <= 0:
        return n, element
    return n - first, lambda i: element(first + i)


def name_of(ctx, key: str) -> str:
    """The name of the field a sample key stands for: its owner, then the field names the game's data gives (an id
    without one in hex, ``#3ee3bb5d``; an element as ``[i]``)."""
    owner, _, rest = key.partition("/")
    game = getattr(ctx, "game_data", None)
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
    choices = getattr(ctx, "play_choices", None)
    if choices is None:
        return None
    stamp = gamedata.stamp(ctx)
    if choices.stamp != stamp:
        choices.names, choices.stamp = {}, stamp
    name = choices.names.get(key)
    if name is None:
        name = choices.names[key] = name_of(ctx, key)
    ctx.memo.note((TOKEN, name))
    return choices.values.get(name)


def chosen_index(ctx, list_key: str) -> int | None:
    """The element of the list at ``list_key`` Play mode selected: the choice of a field of the object holding the
    list that names a selected index or id (the options tabs: ``TabsData.SelectedTabID``, which the click on a tab
    writes), None without one; its elements' selection flags follow it (``placeholders.selection_for``)."""
    choices = getattr(ctx, "play_choices", None)
    if choices is None:
        return None
    owner = name_of(ctx, list_key.rsplit("/", 1)[0])
    ctx.memo.note((TOKEN, f"under:{owner}"))  # a choice made later (``choice_tokens``)
    prefix = owner + "."
    for name, value in choices.values.items():
        rest = name[len(prefix) :].lower() if name.startswith(prefix) else ""
        if rest and "." not in rest and "selected" in rest and rest.endswith(("id", "index")):
            if isinstance(value, int) and not isinstance(value, bool):
                ctx.memo.note((TOKEN, name))
                return value
    return None


# the bindings' calls


def seed(scope, inputs: dict, owner_key: str) -> None:
    """Before a scope's bindings run: the values Play mode set on its instances written in, each instance's own part of
    the tree passed down with what it is given without Play mode. The design state of the placing is kept beside it
    (``settle`` compares with it); a placing without Play mode values costs nothing."""
    scope.play_base = None
    scope.play_total = inputs.get(listlayout.TOTAL_ITEMS) if scope.slot is not None else None  # a list's count
    trees = getattr(scope.ctx, "play", None)
    if trees is None or getattr(_BASELINE, "on", False):
        return
    given, tree = inputs.get(PLAY_UID), trees.get(scope.key, EMPTY)
    if isinstance(given, PlayInput):
        node, base = given.node, dict(given.base)
        if listlayout.TOTAL_ITEMS in inputs:  # what the list layout adds: its design state was kept with it
            base[listlayout.TOTAL_ITEMS] = inputs[listlayout.TOTAL_ITEMS]
    elif owner_key == scope.key and (tree.values or tree.children):
        node, base = tree, dict(inputs)
    else:
        return
    design = _design(scope, base, owner_key)
    index = scope.slot.index if scope.slot is not None else None
    for path, value in node.values:
        if len(path) > 1:  # a component of the instance: the file it places reads it as its own
            scope.writes.setdefault(None, {})[path] = value
    given_to = set()
    for (guid, at), child in node.children:
        if at != index or guid not in scope.children:
            continue
        written = scope.writes.setdefault(guid, {})
        written.update(child.values)
        written[(PLAY_UID,)] = PlayInput(child, _given(scope, design, guid))
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
    host, design.host = design.host, scope.host  # its reads of the placed files' outputs ask this placing
    try:
        moved = _start(scope, design, age)
        moved = _pass_on(scope, design, given_to, age) or moved
    finally:
        design.host = host
    if moved:
        from .scope import PASSES

        scope.run(PASSES)


def _design(scope, base: dict, owner_key: str):
    """The scope of the same placing without Play mode: the kept one where the layout made it before."""
    from .scope import Scope

    _BASELINE.on = True
    try:
        host = scope.host
        if host is not None and hasattr(host, "scope"):
            return host.scope(scope.key, scope.root, base, owner_key, scope.slot, owner_key != scope.key)
        return Scope(scope.key, scope.root, base, scope.ctx, owner_key, host, scope.slot)
    finally:
        _BASELINE.on = False


def _given(scope, design, guid: str) -> tuple:
    """What the instance ``guid`` passes into its file in the design state (``layout.Placer.own``'s order)."""
    from .scope import inputs_of

    node = scope.children[guid]
    el = scope.by_node.get(id(node))
    filled = getattr(scope.host, "filled", {}).get(guid, {}) if scope.host is not None else {}
    live = scope.ctx.live_of(el) if el is not None and hasattr(scope.ctx, "live_of") else {}
    over = {**scope.ctx.values(node), **design.writes.get(guid, {}), **filled, **live}
    return _sorted(inputs_of(over, node).items())


def _read(scope, end):
    scope._asking = None
    return scope.read(end)


def _fires(scope, design, b, target: int) -> bool:
    """Whether the binding ``b`` fires its target ``target`` now and did not in the design state: the event the game
    sends when the value changed."""
    now = [_read(scope, e) for e in b.sources]
    then = [_read(design, e) for e in b.sources]
    if now == then:
        return False
    if b.operator == RISING:
        return bool(now[0]) and not bool(then[0])
    if b.operator == FALLING:
        return not now[0] and bool(then[0])
    if b.operator == CHANGED:
        return True
    if b.operator == ENUM_EVENT:
        return _enum_target(b, target) == now[0]
    outs = operators.evaluate(b.operator, now)
    return bool(outs) and target < len(outs) and bool(outs[target])


def _enum_target(b, target: int):
    """The enum entry for which an EnumToEvent fires ``target`` (its ``BindingInfos``)."""
    from ..formats import phoenixedit
    from . import fields

    for conv in phoenixedit.objects(b.node, "ConverterInstance")[:1]:
        for info in phoenixedit.objects(conv, "BindingInfos"):
            if int(fields.get(info, "TargetIndex", 0) or 0) == target:
                return fields.get(info, "EnumValueId")
    return None


def _start(scope, design, age: float | None = None) -> bool:
    """The animations of the file a change started, at the time since the change (``age``; untimed: at their end),
    and the toggles a change fired, flipped."""
    graph, anims, moved = scope.graph, None, False
    for b in graph.bindings:
        for i, t in enumerate(b.targets):
            if b.operator == TOGGLE and _fires(scope, design, b, i):
                scope.write(t, not bool(_read(design, t)))
                moved = True
                continue
            if t.kind != "path" or t.mode != ANIMATION or not t.ids or t.ids[-1] != START or not t.steps:
                continue
            if not _fires(scope, design, b, i):
                continue
            anims = anims if anims is not None else {a.guid: a for a in animations.read(scope.root)}
            anim = anims.get(t.steps[-1][1])
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
    moved, node_of = False, PlayNode(age=age)
    for guid, node in scope.children.items():
        if guid in given_to:
            continue
        now = {p: v for p, v in scope.writes.get(guid, {}).items() if p != (PLAY_UID,)}
        if now == design.writes.get(guid, {}):
            continue
        el = scope.by_node.get(id(node))
        if el is None or el.kind != "container":
            continue
        scope.writes.setdefault(guid, {})[(PLAY_UID,)] = PlayInput(node_of, _given(scope, design, guid))
        scope._bump(guid)
        moved = True
    return moved


# placing again only what a change reaches (docs/ui/117/game-data.md "Play mode: what is built", "Speed")


def changed(old: PlayNode, new: PlayNode, trail: Trail = ()) -> list[Trail]:
    """The placings to place again from the tree ``old`` to ``new``: the one holding an instance whose values changed
    (its bindings take them), the list whose first shown element moved; none inside another of them."""
    if old == new:
        return []
    found = []
    if old.values != new.values or old.age != new.age:
        found.append(trail[:-1])
    if old.first != new.first:
        found.append(trail)
    a, b = dict(old.children), dict(new.children)
    for step in sorted({*a, *b}, key=repr):
        found += changed(a.get(step, EMPTY), b.get(step, EMPTY), (*trail, step))
    out: list[Trail] = []
    for t in sorted(set(found), key=len):
        if not any(t[: len(o)] == o for o in out):
            out.append(t)
    return out


def node_at(tree: PlayNode, trail: Trail) -> PlayNode:
    for step in trail:
        tree = dict(tree.children).get(step, EMPTY)
    return tree


def replay(ctx, key: str, root: dict, frame, old: dict | None, new: dict):
    """The open file ``key`` laid out with Play mode's values ``new`` from its ``frame``, laid out with ``old``: only
    the placings a change reaches are placed again, where they were; one whose size or outputs then differ from
    before places the file around it again too, up to the whole file (``layout.place``)."""
    from . import layout

    ctx.play = new
    if frame is None or old is None or ctx.epoch != ctx.conditions():
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
    for s in spans:
        yield s
        yield from walk_spans(s.inner)


def _find(spans, trail: Trail, at: tuple[int, int] = (0, 0)):
    """The placing ``trail`` among ``spans`` and where its items and hits start in the frame."""
    for s in spans:
        if s.trail == trail:
            return s, at[0] + s.items[0], at[1] + s.hits[0]
        if s.trail == trail[: len(s.trail)]:
            return _find(s.inner, trail, (at[0] + s.items[0], at[1] + s.hits[0]))
    return None


def _again(ctx, frame, found, node: PlayNode):
    """The frame with one placing placed again with its new Play mode values (``node``), None when its size or its
    outputs differ from before: the file that places it reads them, so it has to be placed again instead."""
    from . import layout

    s, at, hit_at = found
    root = ctx.root(s.key)
    given = s.inputs.get(PLAY_UID)
    base = given.base if isinstance(given, PlayInput) else _sorted(s.inputs.items())
    inputs = {k: v for k, v in s.inputs.items() if k != PLAY_UID}
    if node != EMPTY or dict(base) != inputs:
        inputs[PLAY_UID] = PlayInput(node, base)
    if root is None:
        return None
    if inputs == s.inputs:
        return frame
    if _reaches(ctx, frame, s, root, s.inputs) != _reaches(ctx, frame, s, root, inputs):
        return None
    for _ in range(layout.REPLACE):
        ctx.memo.dropped = 0
        sub = layout.Frame(frame.size)
        ctx.memo.gather(lambda f=sub: layout.Placer(ctx, f)._template(s.key, root, s.world, inputs, s.run))
        if not ctx.memo.dropped:
            break
    n, h = s.items[1] - s.items[0], s.hits[1] - s.hits[0]
    new = Span(s.trail, s.key, s.world, inputs, s.run, (s.items[0], s.items[0] + len(sub.items)),
               (s.hits[0], s.hits[0] + len(sub.hits)), tuple(sub.spans))  # fmt: skip
    return replace(frame, items=[*frame.items[:at], *sub.items, *frame.items[at + n :]],
                   hits=[*frame.hits[:hit_at], *sub.hits, *frame.hits[hit_at + h :]],
                   spans=_spliced(frame.spans, new, len(sub.items) - n, len(sub.hits) - h))  # fmt: skip


def _reaches(ctx, frame, s: Span, root: dict, inputs: dict) -> tuple:
    """What the file placing ``s`` reads of it given ``inputs``: its size and its outputs, less those that hand back
    what it was given (the placing file reads its own write of those, ``Scope._read_child``)."""
    from . import layout

    placer = layout.Placer(ctx, layout.Frame(frame.size))
    placer.stack = list(s.run.stack)
    size, outputs = placer.measure(s.key, root, inputs, s.run.data_owner)
    return size, {uid: v for uid, v in outputs.items() if inputs.get(uid, _NOTHING) != v}


_NOTHING = object()


def _spliced(spans, new: Span, di: int, dh: int) -> list:
    """``spans`` with ``new`` in place of the placing of its trail, the ones after it moved by its change in length."""
    out, done = [], False
    for s in spans:
        if done:
            out.append(replace(s, items=(s.items[0] + di, s.items[1] + di), hits=(s.hits[0] + dh, s.hits[1] + dh)))
        elif s.trail == new.trail:
            out.append(new)
            done = True
        elif s.trail == new.trail[: len(s.trail)]:
            out.append(replace(s, items=(s.items[0], s.items[1] + di), hits=(s.hits[0], s.hits[1] + dh),
                               inner=tuple(_spliced(s.inner, new, di, dh))))  # fmt: skip
            done = True
        else:
            out.append(s)
    return out
