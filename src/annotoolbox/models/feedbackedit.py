"""The feedback file's loops as the object builder's feedback timeline edits them: per item and state of the object
its loops (enter, repeat, leave), a loop's elements as bars over the time one play of the actor gives them, and the
edits a bar's new length, a new place, a new or a removed element make in the document (``models.cfgdoc``). No Qt.

``items(doc)`` lists the items, ``bars(doc, item, state, loop)`` a loop's bars, ``retimed`` / ``moved`` / ``added`` /
``removed`` the edits. docs/tool-suite.md section 23, "The feedback timeline".
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from ..infra.i18n import N_, english
from . import cfgdoc, feedbackplay
from .cfgdoc import SDoc, SEdit, SNode
from .feedbackread import element_kind

KINDS = {0: N_("common.walk"), 1: N_("common.play"), 2: N_("common.wait"), 3: N_("common.branch"), 4: N_("common.walk_spline"), 6: N_("common.show_hide"),
         7: N_("common.barrier"), 9: N_("islands.scale"), 10: N_("islands.turn"), 11: N_("common.walk_around"), 12: N_("common.play_one"),
         13: N_("common.walk_around")}  # fmt: skip
LOOPS = (N_("common.enter"), N_("common.repeat"), N_("common.leave"))  # Loop0, Loop1, Loop2 of a sequence definition
NOMINAL = 1.0  # seconds a bar of an element the play did not reach is drawn with (a leave loop's, an untaken branch)
SHORT = 0.25  # seconds an instant element (show, scale, turn, barrier) is drawn with
# a newer game's element of a kind the file has none of to copy: the fields the shipped files give it
TEMPLATES = {2: (("hasValue", "1"), ("elementType", "2"), ("MinTime", "1000"), ("MaxTime", "2000")),
             6: (("hasValue", "1"), ("elementType", "6"), ("ShowObject", "1"), ("FadingMode", "1"),
                 ("TimeOffset", "0"))}  # fmt: skip


@dataclass(frozen=True)
class SItem:
    """A ``FeedbackConfigs`` item: its index, node, its sequence definitions' nodes and state -> definition."""

    index: int
    node: SNode
    definitions: tuple[SNode, ...]
    states: dict[int, int]


@dataclass(frozen=True, eq=False)
class SBar:
    """One element of a loop: its node and list, its type, where the timeline puts it (seconds), and whether the
    play reached it (else its length is nominal)."""

    node: SNode
    parent: SNode
    kind: int
    start: float
    end: float
    played: bool


def items(doc: SDoc) -> list[SItem]:
    configs = doc.root.path("FeedbackDefinition/FeedbackConfigs")
    out = []
    for index, item in enumerate(configs.findall("i") if configs is not None else []):
        definitions = item.path("SequenceDefinitions")
        pairs = item.find("FeedbackLoops") or item.find("FeedbackLoops2")
        keys = pairs.findall("k") if pairs is not None else []
        values = pairs.findall("v") if pairs is not None else []
        states = {
            int(key_node.text or 0): int(value_node.text or 0)
            for key_node, value_node in zip(keys, values, strict=False)
        }
        out.append(SItem(index, item, tuple(definitions.findall("i")) if definitions is not None else (), states))
    return out


def elements_of(item: SItem, state: int, loop: int) -> SNode | None:
    """The ``Elements`` list of a loop of the definition a state plays (None: there is none)."""
    index = item.states.get(state)
    if index is None or not 0 <= index < len(item.definitions):
        return None
    return item.definitions[index].path(f"Loop{loop}/ElementContainer/Elements")


def _nominal(element: SNode) -> float:
    """An element's length without playing it: a wait's mean, a play's or a choice's shortest, an instant one's
    short bar."""
    kind = element_kind(element)
    if kind == 2:
        return max((element.int("MinTime") + max(element.int("MaxTime"), element.int("MinTime"))) / 2000.0, SHORT)
    if kind in (1, 12):
        return max(element.int("MinPlayTime") / 1000.0, NOMINAL if kind == 1 else 1.0)
    if kind in (0, 4, 11, 13):
        return NOMINAL
    return SHORT


def bars(doc: SDoc, item: SItem, state: int, loop: int, movement: feedbackplay.SMovement | None = None) -> list[SBar]:
    """A loop's elements in order, each placed after the one before with the length the actor's play gives it (its
    first run), or a nominal one where the play did not reach it."""
    parent = elements_of(item, state, loop)
    if parent is None:
        return []
    feedback = feedbackplay.read(doc.root)
    actor = feedback.actors[item.index] if item.index < len(feedback.actors) else None
    spans = {}
    if actor is not None and actor.states:
        spans = {
            id(node): end - start
            for node, start, end in feedbackplay.play(feedback, actor, state, movement=movement).spans()
        }
    out, cursor = [], 0.0
    for element in parent.findall("i"):
        played = id(element) in spans
        length = max(spans[id(element)], 0.0) if played else _nominal(element)
        length = length if length > feedbackplay.TICK * 2 else SHORT
        out.append(SBar(element, parent, element_kind(element), cursor, cursor + length, played))
        cursor += length
    return out


def _leaf(node: SNode, name: str) -> SNode | None:
    return next(
        (
            child
            for child in node.children
            if cfgdoc.strip(child.name) == name and not child.children and child.data is None
        ),
        None,
    )


def retimed(bar: SBar, seconds: float) -> list[SEdit]:
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


def moved(bar: SBar, place: int) -> SEdit | None:
    """The element moved to place ``to`` among its loop's elements."""
    siblings = [child for child in bar.parent.children if child is bar.node or child.name == bar.node.name]
    here = next(i for i, child in enumerate(siblings) if child is bar.node)
    place = min(max(place, 0), len(siblings) - 1)
    if place == here:
        return None
    step = next(i for i, child in enumerate(bar.parent.children) if child is siblings[place]) - next(
        i for i, child in enumerate(bar.parent.children) if child is bar.node
    )
    return cfgdoc.move(bar.parent, bar.node, step, f"move {english(KINDS.get(bar.kind, 'element'))}")


def removed(bar: SBar) -> SEdit:
    return cfgdoc.remove(bar.parent, bar.node, f"remove {english(KINDS.get(bar.kind, 'element'))}")


def new_element(doc: SDoc, kind: int) -> SNode | None:
    """An element of ``kind``: a copy of the file's first one of that kind, else (a newer game's file) a template
    of the fields the shipped files give it; None when neither is there."""
    for node in doc.root.iter():
        if node.name == "i" and node.children and element_kind(node) == kind:
            return cfgdoc.restyled(node, doc.newline)
    fields = TEMPLATES.get(kind)
    sample = next(
        (
            candidate
            for candidate in doc.root.iter()
            if candidate.name == "i" and candidate.find("elementType") is not None
        ),
        None,
    )
    if fields is None or sample is None:
        return None
    node = copy.deepcopy(sample)
    leaf = next(leaf_candidate for leaf_candidate in node.children if not leaf_candidate.children)
    node.children = []
    for name, value in fields:
        child = copy.deepcopy(leaf)
        child.name, child.text, child.data = name, value, None
        node.children.append(child)
    node.children[-1].tail = sample.children[-1].tail  # the closing tag's indentation
    return node


def added(parent: SNode, after: SNode | None, node: SNode, kind: int) -> SEdit:
    """``node`` put into a loop's elements after ``after`` (None: at the end)."""
    insert_index = next((i + 1 for i, child in enumerate(parent.children) if child is after), len(parent.children))
    return cfgdoc.insert(parent, insert_index, [node], f"add {english(KINDS.get(kind, 'element'))}")


def part_path(doc: SDoc, node: SNode) -> str:
    """The object tree's path of a node of the document ("" when it lists none)."""
    return next((part.path for part in doc.parts().walk() if part.node is node), "")
