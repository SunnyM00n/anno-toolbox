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
INFO_TIP, REFERENCE_WIDGET = "d2efb1b6", 0x71FFEDC5  # the InfoTip component of a widget file, its sibling reference


@dataclass
class SRule:
    """What a group's components ask for: a stack (``flow``), a background, or both."""

    flow: bool = False
    vertical: bool = False
    space: float = 0.0
    inverted: bool = False
    margin: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # left, top, right, bottom
    align: int = BEGIN
    background: tuple[float, float, float, float] | None = None  # the first child, stretched, with this margin


@dataclass
class SSlot:
    """One child of a group, placed by itself: ``local`` and ``size`` are what the rule may change."""

    guid: str | None
    local: Matrix
    size: tuple[float, float]
    rect: tuple[float, float, float, float]  # its place in the group: x0, y0, x1, y1 under ``local``
    flows: bool  # visible and counted by its container: the rule places it
    pad: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # PaddingUtility: left, top, right, bottom
    align: int = INHERITED  # AlignmentUtility
    background: bool = False
    holds: bool | None = None  # counted by its container, hidden or not: a stack gives it room (None: ``flows``)


def components(node: dict) -> dict[str, int]:
    """The instance's components: the first part of the template GUID -> the instance's component id."""
    out = {}
    for component in phoenixedit.objects(node, "Components"):
        template = fields.get(component, "ComponentTemplate") or ""
        out[template[:8]] = int(fields.get(component, "UniqueId", 0) or 0)
    return out


def vector(value, default: tuple) -> tuple:
    """``value`` as a vector of floats as long as ``default`` (a missing or unusable part from ``default``)."""
    parts = list(value) if isinstance(value, (tuple, list)) else []
    return tuple(
        float(parts[i]) if i < len(parts) and isinstance(parts[i], (int, float)) else float(fallback)
        for i, fallback in enumerate(default)
    )


def read_rule(comps: dict[str, int], overrides: dict) -> SRule | None:
    """The rule of a group instance from its components and its values of their properties."""
    if STACK not in comps and BACKGROUND not in comps:
        return None
    rule = SRule()
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


def reference_of(placed_root: dict | None, overrides: dict) -> str | None:
    """The sibling an instance hangs on when the widget file it places has an ``InfoTip`` component: its
    ``ReferenceWidget`` (an instance GUID), else None."""
    comp = components(placed_root).get(INFO_TIP) if placed_root is not None else None
    reference = overrides.get((comp, REFERENCE_WIDGET)) if comp is not None else None
    return reference if isinstance(reference, str) and reference else None


def hang(tip: SSlot, reference: SSlot) -> None:
    """An info tip's area takes its reference's rectangle: placed at its corner, as large as it (layout.md "Info
    tips")."""
    x0, y0, x1, y1 = reference.rect
    tip.size, tip.local, tip.rect = (x1 - x0, y1 - y0), (1.0, 0.0, 0.0, 1.0, x0, y0), reference.rect


def _shift(slot: SSlot, delta_x: float, delta_y: float) -> None:
    matrix_a, matrix_b, matrix_c, matrix_d, translate_x, translate_y = slot.local
    slot.local = (matrix_a, matrix_b, matrix_c, matrix_d, translate_x + delta_x, translate_y + delta_y)
    x0, y0, x1, y1 = slot.rect
    slot.rect = (x0 + delta_x, y0 + delta_y, x1 + delta_x, y1 + delta_y)


def _across(rule: SRule, slot: SSlot, extent: float) -> float:
    """Where the child's cross-axis edge goes, from the group's cross-axis start: its alignment inside ``extent``.
    The margin does not move the children (it grows the group's box); a middle centres the child with its paddings."""
    is_vertical = rule.vertical
    begin = 0.0
    pad_begin, pad_end = (slot.pad[0], slot.pad[2]) if is_vertical else (slot.pad[1], slot.pad[3])
    length = (slot.rect[2] - slot.rect[0]) if is_vertical else (slot.rect[3] - slot.rect[1])
    align = rule.align if slot.align in (INHERITED, 0) else slot.align
    if align == MIDDLE:
        return begin + (extent - length - pad_begin - pad_end) / 2 + pad_begin
    if align == END:
        return begin + extent - length - pad_end
    return begin + pad_begin


def arrange(rule: SRule, slots: list[SSlot]) -> None:
    """Put the stack's children one after another along its axis and each aligned across it; then stretch the
    background round them. Both take the children that count, hidden ones too (``holds``). The first child is the
    background of a background layout."""
    content = [slot for slot in slots if (slot.flows if slot.holds is None else slot.holds) and not slot.background]
    if rule.flow:
        _stack(rule, content)
    if rule.background is not None:
        _stretch(rule.background, slots, content)


def _stack(rule: SRule, flow: list[SSlot]) -> None:
    is_vertical = rule.vertical
    extent = max(
        ((slot.rect[2] - slot.rect[0] + slot.pad[0] + slot.pad[2]) if is_vertical else (slot.rect[3] - slot.rect[1] + slot.pad[1] + slot.pad[3]))
        for slot in flow
    ) if flow else 0.0  # fmt: skip
    cursor = 0.0
    for slot in flow if rule.inverted else reversed(flow):  # the game lays the last child of the file out first
        x0, y0, x1, y1 = slot.rect
        cursor += slot.pad[1] if is_vertical else slot.pad[0]
        across = _across(rule, slot, extent)
        if is_vertical:
            _shift(slot, across - x0, cursor - y0)
            cursor += (y1 - y0) + rule.space + slot.pad[3]
        else:
            _shift(slot, cursor - x0, across - y0)
            cursor += (x1 - x0) + rule.space + slot.pad[2]


def _stretch(margin_px: tuple[float, float, float, float], slots: list[SSlot], content: list[SSlot]) -> None:
    """The background child covers the others' union grown by the margin (left, top, right, bottom)."""
    back = next((slot for slot in slots if slot.background), None)
    if back is None or not content:
        return
    x0, y0 = min(slot.rect[0] for slot in content), min(slot.rect[1] for slot in content)
    x1, y1 = max(slot.rect[2] for slot in content), max(slot.rect[3] for slot in content)
    left, top, right, bottom = margin_px
    back.size = (max(x1 - x0 + left + right, 0.0), max(y1 - y0 + top + bottom, 0.0))
    back.local = (1.0, 0.0, 0.0, 1.0, x0 - left, y0 - top)
    back.rect = (x0 - left, y0 - top, x0 - left + back.size[0], y0 - top + back.size[1])
