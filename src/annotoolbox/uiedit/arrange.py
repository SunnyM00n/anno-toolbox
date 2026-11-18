"""The layout rules of a group: a stack's order, spacing and cross-axis alignment, and a background layout that
stretches the first child round the others (checked against the game's own layout: layout.md).

``read_rule(comps, overrides)`` is the rule of a group instance (None: its children keep their own places);
``arrange(rule, slots)`` moves the slots, one per child in the file's order, to where the rule puts them.
docs/ui/117/layout.md "Layout components" says what was settled against the game and what stays open.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..formats import phoenixedit
from . import fields

Matrix = tuple[float, float, float, float, float, float]
STACK, ALIGN, PADDING, BACKGROUND = "d5481210", "b973e271", "60a1f9fe", "33ee79a6"  # components, first GUID part
STACK_ORIENTATION, STACK_SPACE, VERTICAL = 2086100441, 2115500691, 1410225626
STACK_INVERT, STACK_MARGIN, STACK_ALIGN = 0x71ADECBB, 0x75274045, 0x7E2576CB
ALIGN_VALUE, PADDING_VALUE, BACKGROUND_MARGIN = 0x7869C5A7, 652292905, 0x7D19BB74
BEGIN, MIDDLE, END, INHERITED = 1403909053, 1503000153, 1378142755, 1449164074  # the LayoutAlignment enum


@dataclass
class Rule:
    """What a group's components ask for: a stack (``flow``), a background, or both."""

    flow: bool = False
    vertical: bool = False
    space: float = 0.0
    inverted: bool = False
    margin: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # left, top, right, bottom
    align: int = BEGIN
    background: tuple[float, float, float, float] | None = None  # the first child, stretched, with this margin


@dataclass
class Slot:
    """One child of a group, placed by itself: ``local`` and ``size`` are what the rule may change."""

    guid: str | None
    local: Matrix
    size: tuple[float, float]
    rect: tuple[float, float, float, float]  # its place in the group: x0, y0, x1, y1 under ``local``
    flows: bool  # visible and counted by its container: the rule places it
    pad: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # PaddingUtility: left, top, right, bottom
    align: int = INHERITED  # AlignmentUtility
    background: bool = False


def components(node: dict) -> dict[str, int]:
    """The instance's components: the first part of the template GUID -> the instance's component id."""
    out = {}
    for c in phoenixedit.objects(node, "Components"):
        tpl = fields.get(c, "ComponentTemplate") or ""
        out[tpl[:8]] = int(fields.get(c, "UniqueId", 0) or 0)
    return out


def vector(value, default: tuple) -> tuple:
    """``value`` as a vector of floats as long as ``default`` (a missing or unusable part from ``default``)."""
    parts = list(value) if isinstance(value, (tuple, list)) else []
    return tuple(
        float(parts[i]) if i < len(parts) and isinstance(parts[i], (int, float)) else float(d)
        for i, d in enumerate(default)
    )


def read_rule(comps: dict[str, int], overrides: dict) -> Rule | None:
    """The rule of a group instance from its components and its values of their properties."""
    if STACK not in comps and BACKGROUND not in comps:
        return None
    rule = Rule()
    if STACK in comps:
        stack = comps[STACK]
        rule.flow = True
        rule.vertical = overrides.get((stack, STACK_ORIENTATION)) == VERTICAL
        rule.space = float(overrides.get((stack, STACK_SPACE), 0) or 0)
        rule.inverted = bool(overrides.get((stack, STACK_INVERT)))
        rule.margin = vector(overrides.get((stack, STACK_MARGIN)), (0.0,) * 4)
        rule.align = int(overrides.get((stack, STACK_ALIGN), BEGIN) or BEGIN)
    if BACKGROUND in comps:
        rule.background = vector(overrides.get((comps[BACKGROUND], BACKGROUND_MARGIN)), (0.0,) * 4)
    return rule


def read_child(comps: dict[str, int], overrides: dict) -> tuple[tuple[float, float, float, float], int]:
    """A child's padding and its alignment override (``INHERITED``: the group's)."""
    pad = vector(overrides.get((comps[PADDING], PADDING_VALUE)), (0.0,) * 4) if PADDING in comps else (0.0,) * 4
    align = int(overrides.get((comps[ALIGN], ALIGN_VALUE), INHERITED) or INHERITED) if ALIGN in comps else INHERITED
    return pad, align


def _shift(slot: Slot, dx: float, dy: float) -> None:
    a, b, c, d, tx, ty = slot.local
    slot.local = (a, b, c, d, tx + dx, ty + dy)
    x0, y0, x1, y1 = slot.rect
    slot.rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)


def _across(rule: Rule, slot: Slot, extent: float) -> float:
    """Where the child's cross-axis edge goes, from the group's cross-axis start: its alignment inside ``extent``.
    The margin does not move the children (it grows the group's box); a middle centres the child with its paddings."""
    v = rule.vertical
    begin = 0.0
    pad_begin, pad_end = (slot.pad[0], slot.pad[2]) if v else (slot.pad[1], slot.pad[3])
    length = (slot.rect[2] - slot.rect[0]) if v else (slot.rect[3] - slot.rect[1])
    align = rule.align if slot.align in (INHERITED, 0) else slot.align
    if align == MIDDLE:
        return begin + (extent - length - pad_begin - pad_end) / 2 + pad_begin
    if align == END:
        return begin + extent - length - pad_end
    return begin + pad_begin


def arrange(rule: Rule, slots: list[Slot]) -> None:
    """Put the stack's children one after another along its axis (``flows`` ones only) and each aligned across it;
    then stretch the background round them. The first child is the background of a background layout."""
    slots_in_flow = [s for s in slots if s.flows and not s.background]
    if rule.flow:
        _stack(rule, slots_in_flow)
    if rule.background is not None:
        _stretch(rule.background, slots, slots_in_flow)


def _stack(rule: Rule, flow: list[Slot]) -> None:
    v = rule.vertical
    extent = max(
        ((s.rect[2] - s.rect[0] + s.pad[0] + s.pad[2]) if v else (s.rect[3] - s.rect[1] + s.pad[1] + s.pad[3]))
        for s in flow
    ) if flow else 0.0  # fmt: skip
    cursor = 0.0
    for s in flow if rule.inverted else reversed(flow):  # the game lays the last child of the file out first
        x0, y0, x1, y1 = s.rect
        cursor += s.pad[1] if v else s.pad[0]
        across = _across(rule, s, extent)
        if v:
            _shift(s, across - x0, cursor - y0)
            cursor += (y1 - y0) + rule.space + s.pad[3]
        else:
            _shift(s, cursor - x0, across - y0)
            cursor += (x1 - x0) + rule.space + s.pad[2]


def _stretch(margin: tuple[float, float, float, float], slots: list[Slot], content: list[Slot]) -> None:
    """The background child covers the others' union grown by the margin (left, top, right, bottom)."""
    back = next((s for s in slots if s.background), None)
    if back is None or not content:
        return
    x0, y0 = min(s.rect[0] for s in content), min(s.rect[1] for s in content)
    x1, y1 = max(s.rect[2] for s in content), max(s.rect[3] for s in content)
    left, top, right, bottom = margin
    back.size = (max(x1 - x0 + left + right, 0.0), max(y1 - y0 + top + bottom, 0.0))
    back.local = (1.0, 0.0, 0.0, 1.0, x0 - left, y0 - top)
    back.rect = (x0 - left, y0 - top, x0 - left + back.size[0], y0 - top + back.size[1])
