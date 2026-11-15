"""The feedback file's loops as the object builder's feedback timeline edits them: per item and state of the object
its loops (enter, repeat, leave), a loop's elements as bars over the time one play of the actor gives them, and the
edits a bar's new length, a new place, a new or a removed element make in the document (``models.cfgdoc``). No Qt.

``items(doc)`` lists the items, ``bars(doc, item, state, loop)`` a loop's bars, ``retimed`` / ``moved`` / ``added`` /
``removed`` the edits. docs/tool-suite.md section 23, "The feedback timeline".
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from ..infra.i18n import N_
from . import cfgdoc, feedbackplay
from .cfgdoc import Doc, Edit, Node
from .feedbackread import element_kind

KINDS = {0: N_("Walk"), 1: N_("Play"), 2: N_("Wait"), 3: N_("Branch"), 4: N_("Walk a spline"), 6: N_("Show or hide"),
         7: N_("Barrier"), 9: N_("Scale"), 10: N_("Turn"), 11: N_("Walk around"), 12: N_("Play one of"),
         13: N_("Walk around")}  # fmt: skip
LOOPS = (N_("Enter"), N_("Repeat"), N_("Leave"))  # Loop0, Loop1, Loop2 of a sequence definition
NOMINAL = 1.0  # seconds a bar of an element the play did not reach is drawn with (a leave loop's, an untaken branch)
SHORT = 0.25  # seconds an instant element (show, scale, turn, barrier) is drawn with
# a newer game's element of a kind the file has none of to copy: the fields the shipped files give it
TEMPLATES = {2: (("hasValue", "1"), ("elementType", "2"), ("MinTime", "1000"), ("MaxTime", "2000")),
             6: (("hasValue", "1"), ("elementType", "6"), ("ShowObject", "1"), ("FadingMode", "1"),
                 ("TimeOffset", "0"))}  # fmt: skip


@dataclass(frozen=True)
class Item:
    """A ``FeedbackConfigs`` item: its index, node, its sequence definitions' nodes and state -> definition."""

    index: int
    node: Node
    definitions: tuple[Node, ...]
    states: dict[int, int]


@dataclass(frozen=True, eq=False)
class Bar:
    """One element of a loop: its node and list, its type, where the timeline puts it (seconds), and whether the
    play reached it (else its length is nominal)."""

    node: Node
    parent: Node
    kind: int
    start: float
    end: float
    played: bool


def items(doc: Doc) -> list[Item]:
    configs = doc.root.path("FeedbackDefinition/FeedbackConfigs")
    out = []
    for k, item in enumerate(configs.findall("i") if configs is not None else []):
        definitions = item.path("SequenceDefinitions")
        pairs = item.find("FeedbackLoops") or item.find("FeedbackLoops2")
        keys = pairs.findall("k") if pairs is not None else []
        values = pairs.findall("v") if pairs is not None else []
        states = {int(a.text or 0): int(b.text or 0) for a, b in zip(keys, values, strict=False)}
        out.append(Item(k, item, tuple(definitions.findall("i")) if definitions is not None else (), states))
    return out


def elements_of(item: Item, state: int, loop: int) -> Node | None:
    """The ``Elements`` list of a loop of the definition a state plays (None: there is none)."""
    index = item.states.get(state)
    if index is None or not 0 <= index < len(item.definitions):
        return None
    return item.definitions[index].path(f"Loop{loop}/ElementContainer/Elements")


def _nominal(e: Node) -> float:
    """An element's length without playing it: a wait's mean, a play's or a choice's shortest, an instant one's
    short bar."""
    kind = element_kind(e)
    if kind == 2:
        return max((e.int("MinTime") + max(e.int("MaxTime"), e.int("MinTime"))) / 2000.0, SHORT)
    if kind in (1, 12):
        return max(e.int("MinPlayTime") / 1000.0, NOMINAL if kind == 1 else 1.0)
    if kind in (0, 4, 11, 13):
        return NOMINAL
    return SHORT


def bars(doc: Doc, item: Item, state: int, loop: int, movement: feedbackplay.Movement | None = None) -> list[Bar]:
    """A loop's elements in order, each placed after the one before with the length the actor's play gives it (its
    first run), or a nominal one where the play did not reach it."""
    parent = elements_of(item, state, loop)
    if parent is None:
        return []
    feedback = feedbackplay.read(doc.root)
    actor = feedback.actors[item.index] if item.index < len(feedback.actors) else None
    spans = {}
    if actor is not None and actor.states:
        spans = {id(n): b - a for n, a, b in feedbackplay.play(feedback, actor, state, movement=movement).spans()}
    out, t = [], 0.0
    for e in parent.findall("i"):
        played = id(e) in spans
        length = max(spans[id(e)], 0.0) if played else _nominal(e)
        length = length if length > feedbackplay.TICK * 2 else SHORT
        out.append(Bar(e, parent, element_kind(e), t, t + length, played))
        t += length
    return out


def _leaf(node: Node, name: str) -> Node | None:
    return next((c for c in node.children if cfgdoc.strip(c.name) == name and not c.children and c.data is None), None)


def retimed(bar: Bar, seconds: float) -> list[Edit]:
    """The edits that give an element the length ``seconds`` (its bar's end dragged): a play's ``MinPlayTime``, a
    wait's or a choice's two times moved by the difference, a walk's ``SpeedFactorF`` scaled; [] for a kind whose
    length is not its own (an instant one, a walk at its sequence's speed)."""
    old, new = bar.end - bar.start, max(seconds, 0.0)
    e, out = bar.node, []

    def put(name: str, value: str) -> None:
        leaf = _leaf(e, name)
        if leaf is not None and (leaf.text or "") != value:
            out.append(cfgdoc.set_value(leaf, value, f"{name} = {value}"))

    delta = int(round((new - old) * 1000))
    if bar.kind == 1:
        put("MinPlayTime", str(max(int(round(new * 1000)), 0)))
    elif bar.kind == 2:
        put("MinTime", str(max(e.int("MinTime") + delta, 0)))
        put("MaxTime", str(max(e.int("MaxTime") + delta, 0)))
    elif bar.kind == 12:
        put("MinPlayTime", str(max(e.int("MinPlayTime") + delta, 0)))
        put("MaxPlayTime", str(max(e.int("MaxPlayTime") + delta, 0)))
    elif bar.kind in (0, 4, 11, 13) and bar.played and new > 0 and e.float("SpeedFactorF") > 0:
        put("SpeedFactorF", f"{e.float('SpeedFactorF') * old / new:.6f}")
    return out


def moved(bar: Bar, to: int) -> Edit | None:
    """The element moved to place ``to`` among its loop's elements."""
    siblings = [c for c in bar.parent.children if c is bar.node or c.name == bar.node.name]
    here = next(i for i, c in enumerate(siblings) if c is bar.node)
    to = min(max(to, 0), len(siblings) - 1)
    if to == here:
        return None
    step = next(i for i, c in enumerate(bar.parent.children) if c is siblings[to]) - next(
        i for i, c in enumerate(bar.parent.children) if c is bar.node
    )
    return cfgdoc.move(bar.parent, bar.node, step, f"move {KINDS.get(bar.kind, 'element')}")


def removed(bar: Bar) -> Edit:
    return cfgdoc.remove(bar.parent, bar.node, f"remove {KINDS.get(bar.kind, 'element')}")


def new_element(doc: Doc, kind: int) -> Node | None:
    """An element of ``kind``: a copy of the file's first one of that kind, else (a newer game's file) a template
    of the fields the shipped files give it; None when neither is there."""
    for node in doc.root.iter():
        if node.name == "i" and node.children and element_kind(node) == kind:
            return cfgdoc.restyled(node, doc.newline)
    fields = TEMPLATES.get(kind)
    sample = next((n for n in doc.root.iter() if n.name == "i" and n.find("elementType") is not None), None)
    if fields is None or sample is None:
        return None
    node = copy.deepcopy(sample)
    leaf = next(c for c in node.children if not c.children)
    node.children = []
    for name, value in fields:
        child = copy.deepcopy(leaf)
        child.name, child.text, child.data = name, value, None
        node.children.append(child)
    node.children[-1].tail = sample.children[-1].tail  # the closing tag's indentation
    return node


def added(parent: Node, after: Node | None, node: Node, kind: int) -> Edit:
    """``node`` put into a loop's elements after ``after`` (None: at the end)."""
    at = next((i + 1 for i, c in enumerate(parent.children) if c is after), len(parent.children))
    return cfgdoc.insert(parent, at, [node], f"add {KINDS.get(kind, 'element')}")


def part_path(doc: Doc, node: Node) -> str:
    """The object tree's path of a node of the document ("" when it lists none)."""
    return next((p.path for p in doc.parts().walk() if p.node is node), "")
