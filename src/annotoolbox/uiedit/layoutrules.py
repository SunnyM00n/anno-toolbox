"""The placing rules that need no state: the cancel modes of a scale above a widget, the draw order of a container's
children, the masks, and the small readers of a size (docs/ui/117/layout.md). ``layout.CPlacer`` applies them."""

from __future__ import annotations

from . import fields
from .layouttypes import SPaint
from .scope import CScope


def paint_key(paint: SPaint) -> tuple:
    """What of a ``SPaint`` decides the items drawn under it (the masks as their rectangles and pictures)."""
    clips = tuple((clip.matrix, clip.size, clip.image, clip.image_id, repr(clip.soft)) for clip in paint.clips)
    return paint.color, paint.alpha, clips, repr(sorted(paint.effects.items()))


def scalar(value, default: float) -> float:
    """``value`` as one number (the first part of a vector, ``default`` for anything else)."""
    if isinstance(value, (tuple, list)):
        value = value[0] if value else default
    return float(value) if isinstance(value, (int, float)) else float(default)


def cancel(
    modes: dict[str, int], position, scale, ancestors: tuple[float, float], parent: tuple[float, float] = (1.0, 1.0)
):
    """Position and scale with the modes that undo a scale above the widget applied: per axis Fixed (1) divides by
    the product of all ancestors' scales and FixedToParent (2) by the ``parent``'s own scale, and the aspect ratio
    (1 fit the smaller axis, 2 follow the width, 3 the height) (docs/ui/117/layout.md "Position, anchor and pivot")."""
    ancestor_x, ancestor_y = (max(abs(component), 1e-6) for component in ancestors)
    divisors = {1: (ancestor_x, ancestor_y), 2: tuple(max(abs(component), 1e-6) for component in parent)}
    position, scale = list(position), list(scale)
    for i, (move_mode, size_mode) in enumerate(
        (("XRepositioning", "WidthResizing"), ("YRepositioning", "HeightResizing"))
    ):
        if modes[move_mode] in divisors:
            position[i] = float(position[i]) / divisors[modes[move_mode]][i]
        if modes[size_mode] in divisors:
            scale[i] = float(scale[i]) / divisors[modes[size_mode]][i]
    aspect = modes["AspectRatio"]
    if aspect == 1:
        low = min(ancestor_x, ancestor_y)
        scale[0], scale[1] = float(scale[0]) * low / ancestor_x, float(scale[1]) * low / ancestor_y
    elif aspect == 2:
        scale[1] = float(scale[1]) * ancestor_x / ancestor_y
    elif aspect == 3:
        scale[0] = float(scale[0]) * ancestor_y / ancestor_x
    return tuple(position), tuple(scale)


def is_mask_widget(node: dict) -> bool:
    return int(fields.get(node, "MaskMode", 0) or 0) != 0


def draw_order(children: list, is_mask, receives, zindex) -> list:
    """A container's children in the order the game draws them (layout.md "Draw order"): the file's order with each run
    of masks moved in front of the siblings it covers, then, from the first child with a ``ZIndex`` on, sorted by it as
    an unsigned 32-bit number (stable)."""
    draw, run, insert_at = [], [], 0
    for i, child in enumerate(children):
        if is_mask(child):
            run.append(child)
            continue
        if run:
            draw[insert_at:insert_at] = run
            run, insert_at = [], i
        draw.append(child)
        if not receives(child):
            insert_at += 1
    if run:
        draw[insert_at:insert_at] = run
    first = next((i for i, candidate in enumerate(children) if zindex(candidate)), None)
    if first is not None:
        draw[first:] = sorted(draw[first:], key=lambda candidate: zindex(candidate) & 0xFFFFFFFF)
    return draw


def is_size(value) -> bool:
    """Whether a value is a usable size (two numbers or more)."""
    return (
        isinstance(value, (tuple, list))
        and len(value) >= 2
        and all(isinstance(number, (int, float)) for number in value[:2])
    )


def settled(scope: CScope, inputs: dict) -> dict:
    """A file's own values after its bindings, where they are given or computed (not a default nobody set): what a
    list lays its items out by (a tab bar's own binding makes it one row)."""
    given = {uid: scope.m_own[uid] for uid in inputs if uid in scope.m_own}
    return {**inputs, **given, **scope.computed()}
