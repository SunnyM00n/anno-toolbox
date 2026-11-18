"""What the input does in Play mode, on the areas a layout left (``layout.Frame.hits``): the widgets under the pointer,
their events, and what a click, a drag, the wheel or a key does to a tab, a checkbox, toggle or combo box, a slider,
a scroll bar, a draggable button, a list, a radial wheel, an info tip, a scene link (docs/ui/117/game-data.md "Play
mode"). Runs on the window's thread on what the layout left; ``playmode`` is the Qt side.

``under``, ``kinds``; ``hover_values`` and ``press_values`` the passing events; ``click``, ``slide``, ``scroll``,
``drag_to``, ``pick_entry``, ``wheel_focus`` the kept changes (``Outcome``); ``neighbour`` the focus by direction;
``entries`` a toggle's or combo box's entries; ``tip`` an info tip's text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..infra.i18n import tr
from . import arrange, bindings, fields, listlayout
from .layout import apply, invert
from .playstate import ELEMENT, Hit, Trail, name_of
from .previewdefaults import PLAY
from .scope import GameObjectRef, GivenObject

BUTTONS = {"4fdbe42a", "776925ee", "71b842da"}  # Button, the game's button, its draggable button
CHECKBOXES = {"16ea67bd", "71ad579c"}
SLIDERS = {"4d8efcd7", "b6bd17dd"}
SWITCHES, COMBOS = {"8b647527"}, {"4464ab4e"}  # SwitchChoice (a toggle), ComboBox
WHEELS = {"9eb9b942", "3758ca04"}  # SelectionWheel and the game's
SCROLLBARS, DRAGGABLES = {"ad55d6e7", "31faf68a"}, {"71b842da"}  # ScrollBar and the game's; DraggableButton
SCROLL_INFO, HANDLE, HORIZONTAL = 0x3556741E, 0x32CC0DFA, 0x3E3730E8  # ScrollBar's
DRAG_STARTED, DRAG_STOPPED, MIN_POS, MAX_POS = 0x3765D8AC, 0x30F7F496, 0x343B2040, 0x3AA6CE96  # DraggableButton's
RESTRICTION, IN_RANGE = 0x33B48861, 1391134490  # its PositionRestriction, SpecifiedRange
SWITCH_ENTRIES, COMBO_ENTRIES, COMBO_INDEX = 0x3761E2F0, 0x310878E9, 0x31B666DC
NEIGHBOURS = {"up": 0x3065A3CF, "down": 0x32D3772C, "left": 0x36D5D33E, "right": 0x31AF7BED}  # NeighborUp, ...
DIRECTIONS = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}
POSITION, SCALE, ROTATION = 0x80000001, 0x80000035, 0x80000003
MOUSE = ("dcf471c7", "caffb4ce", "ea4f1025")  # the game's mouse handler, MouseHandler, MouseBaseHandler
TAB_BAR, TAB_ELEMENT, INFO_TIP = "4f14c372", "f36d9167", "d2efb1b6"
ON_PRESS, ON_RELEASE, ON_ENTER = 0x351AC0D8, 0x302142A6, 0x37FBD979  # Button's events
M_DOWN, M_UP, M_ENTER, M_HOVER = 0x7B5FAFC7, 0x76ED896F, 0x70993B24, 0x7EC95017  # a mouse handler's
IS_CHECKED = 0x3EE3BB5D
SELECTED_TAB, TAB_SELECTED, ELEMENT_ID, TAB_SELECT = 0x7A2DED6D, 0x7F7463C9, 0x733F7D1A, 0x7C1F1AC1
S_VALUE, S_MIN, S_MAX, S_STEPS, S_STEP = 0x330DFD8E, 0x35A4C4F9, 0x37769CD2, 0x3ACB84A4, 0x30CA541C  # Slider's
SWITCH_INDEX, COMBO_OPEN = 0x39A38F1C, 0x3CB0AA80
FOCUSED, SELECTION = 0x3DA1E9C8, 0x3FDCEC67  # the wheel's FocusedIndex, CurrentSelectionIndex
TIP_GUID, TIP_REF, TIP_TEXT = 0x728D7F13, 0x7B839511, 0x79BB3A2E  # InfotipGUID, RefGUID, RefTextID
TWO_WAY = "bba41a31"
CONTROLS = ("tab", "checkbox", "switch", "combo", "slider", "wheel", "scrollbar")
PRESSABLE = (*CONTROLS, "button", "mouse")


@dataclass
class Outcome:
    """What an input changed: values kept for the Play session (``kept``: trail, path, value), the lists scrolled
    (``scrolls``: trail, first shown element), choices for the Preview settings (``choices``: store name -> value,
    ``keys`` their sample keys), a status note, a scene to open."""

    kept: list = field(default_factory=list)
    choices: dict = field(default_factory=dict)
    keys: list = field(default_factory=list)
    note: str = ""
    open: str = ""
    scrolls: list = field(default_factory=list)


@dataclass(frozen=True)
class Drag:
    """A drag under way: a scroll bar's (``kind`` "scrollbar") or a draggable button's ("draggable"), the dragged
    widget's ``trail``, where the pointer pressed and the button's position then (in its parent's units)."""

    kind: str
    trail: Trail
    start: tuple[float, float]
    origin: tuple[float, float] = (0.0, 0.0)


def contains(hit: Hit, x: float, y: float) -> bool:
    w, h = hit.size
    if w <= 0 or h <= 0:
        return False
    lx, ly = apply(invert(hit.matrix), x, y)
    return 0 <= lx <= w and 0 <= ly <= h


def local(hit: Hit, x: float, y: float) -> tuple[float, float]:
    """A point of the reference space as a fraction of the hit's box."""
    lx, ly = apply(invert(hit.matrix), x, y)
    w, h = hit.size
    return (lx / w if w else 0.0, ly / h if h else 0.0)


def under(frame, x: float, y: float) -> list[Hit]:
    """The hits under a point, the last drawn last, one per trail."""
    found = {h.trail: h for h in (frame.hits if frame is not None else ()) if contains(h, x, y)}
    return list(found.values())


def by_trail(frame, trail: Trail) -> Hit | None:
    return next((h for h in reversed(frame.hits) if h.trail == trail), None) if frame is not None else None


def _placed(ctx, hit: Hit) -> dict | None:
    el = hit.el
    return ctx.files.root(el.template) if el.kind == "container" and el.template else None


_KNOWN: dict[tuple, object] = {}  # (what, files, node, template) -> comps or kinds; ``forget`` after an edit


def forget() -> None:
    """Drop what is known of the instances (an edit may have changed their components or files)."""
    _KNOWN.clear()


def _known(what: str, ctx, hit: Hit, make):
    key = (what, id(ctx.files), id(hit.el.node), hit.el.template)
    if key not in _KNOWN:
        if len(_KNOWN) > 200_000:
            _KNOWN.clear()
        _KNOWN[key] = make()
    return _KNOWN[key]


def comps(ctx, hit: Hit) -> dict[str, int]:
    """The instance's components and those of the root of the file it places (the game addresses both through it)."""

    def make():
        root = _placed(ctx, hit)
        return {**(arrange.components(root) if root is not None else {}), **arrange.components(hit.el.node)}

    return _known("comps", ctx, hit, make)


def kinds(ctx, hit: Hit) -> frozenset[str]:
    """What an instance is to the input: button, checkbox, switch, combo, slider, wheel, list, tab, bar, mouse, tip."""
    return _known("kinds", ctx, hit, lambda: _kinds(ctx, hit))


def _kinds(ctx, hit: Hit) -> frozenset[str]:
    root = _placed(ctx, hit)
    chain = set(listlayout.type_chain(ctx.files, root)) if root is not None else set()
    out = {name for name, types in (("button", BUTTONS), ("checkbox", CHECKBOXES), ("slider", SLIDERS),
                                    ("switch", SWITCHES), ("combo", COMBOS), ("wheel", WHEELS),
                                    ("scrollbar", SCROLLBARS), ("draggable", DRAGGABLES)) if chain & types}  # fmt: skip
    if root is not None and listlayout.list_kind(ctx.files, root) in ("list", "grid"):
        out.add("list")
    c = comps(ctx, hit)
    out |= {name for name, prefix in (("tab", TAB_ELEMENT), ("bar", TAB_BAR), ("tip", INFO_TIP)) if prefix in c}
    if any(m in c for m in MOUSE):
        out.add("mouse")
    return frozenset(out)


def _mouse(ctx, hit: Hit) -> int | None:
    c = comps(ctx, hit)
    return next((c[m] for m in MOUSE if m in c), None)


def hover_values(ctx, hit: Hit) -> dict:
    """The flags and events the pointer over a widget gives it: a mouse handler's enter and hover, a button's enter."""
    out, m = {}, _mouse(ctx, hit)
    if m is not None:
        out.update({(m, M_ENTER): True, (m, M_HOVER): True})
    if "button" in kinds(ctx, hit):
        out[(ON_ENTER,)] = True
    return out


def press_values(ctx, hit: Hit, released: bool) -> dict:
    """A press (``released`` False) or the release after it, over the widget: the mouse handler's and the button's."""
    out, m = {}, _mouse(ctx, hit)
    if m is not None:
        out[(m, M_UP if released else M_DOWN)] = True
    if "button" in kinds(ctx, hit):
        out[(ON_RELEASE if released else ON_PRESS,)] = True
    c = comps(ctx, hit)
    if released and TAB_ELEMENT in c:
        out[(c[TAB_ELEMENT], TAB_SELECT)] = True
    return out


def target(ctx, hits: list[Hit]) -> Hit | None:
    """The widget a press goes to: the topmost one that takes it."""
    return next((h for h in reversed(hits) if kinds(ctx, h) & set(PRESSABLE)), None)


def focusable(ctx, frame) -> list[Hit]:
    """The widgets the keyboard's focus walks through, in drawing order."""
    seen: dict[Trail, Hit] = {}
    for h in frame.hits if frame is not None else ():
        if h.size[0] > 0 and h.size[1] > 0 and kinds(ctx, h) & set(PRESSABLE):
            seen.setdefault(h.trail, h)
    return list(seen.values())


# two-way bindings: the field of the game's data a control writes back to


def _on(end, scope, guid: str, path: tuple) -> bool:
    if end.kind != "path" or tuple(end.ids) != tuple(path):
        return False
    if end.mode == bindings.ITEM and scope.slot is not None:
        return scope.slot.item == guid
    return end.mode == bindings.CHILD and end.instance == guid


def setting_for(ctx, frame, hit: Hit, path: tuple, depth: int = 0) -> tuple[str, str] | None:
    """The field a control's value at ``path`` is written back to through its two-way bindings, up through the files'
    own properties that pass it on: (its name, its sample key); None where no binding writes it back."""
    scope = hit.scope
    for b in scope.graph.bindings:
        if b.operator != TWO_WAY or not b.sources or not b.targets:
            continue
        for mine, other in ((b.targets[0], b.sources[0]), (b.sources[0], b.targets[0])):
            if _on(mine, scope, hit.el.guid, path):
                found = _field(ctx, frame, hit, other, depth)
                if found is not None:
                    return found
    return None


def _field(ctx, frame, hit: Hit, end, depth: int) -> tuple[str, str] | None:
    scope = hit.scope
    own = scope._own_end(end)
    if own is not None:  # the file's own property: whoever places the file binds it on
        parent = by_trail(frame, hit.trail[:-1]) if depth < 12 else None
        return setting_for(ctx, frame, parent, (own,), depth + 1) if parent is not None else None
    if end.kind != "path" or not end.ids:
        return None
    if end.mode == bindings.SELF and len(end.ids) > 1:
        base, rest = scope.own.get(end.ids[0]), end.ids[1:]
    elif end.mode == bindings.ELEMENT and scope.slot is not None:
        base, rest = scope.slot.element, end.ids
    else:
        return None
    if not isinstance(base, GameObjectRef):
        return None
    key = "/".join([base.key, *(f"{i:08x}" for i in rest)])
    return name_of(ctx, key), key


def _write(ctx, frame, hit: Hit, path: tuple, value, out: Outcome) -> None:
    """A control's new value: kept for the session, and a choice where a two-way binding writes it back."""
    out.kept.append((hit.trail, tuple(path), value))
    found = setting_for(ctx, frame, hit, path)
    if found is not None:
        out.choices[f"options.{PLAY}{found[0]}"] = value
        out.keys.append(found[1])


# clicks


def click(ctx, frame, hits: list[Hit], at: tuple[float, float], current: str, models=None) -> Outcome:
    """A click (press and release over the same widget): the topmost control under the pointer acts (a tab is
    selected, a checkbox flips, a toggle steps over its entries, a combo box opens or closes its list), and a button
    that opens a scene says which; ``models``: the game's data classes (a toggle's enum)."""
    out = Outcome()
    for hit in reversed(hits):
        k = kinds(ctx, hit)
        if "tab" in k:
            _tab(ctx, frame, hit, out)
        elif "checkbox" in k:
            _write(ctx, frame, hit, (IS_CHECKED,), not bool(hit.values.get((IS_CHECKED,))), out)
        elif "switch" in k:
            step = -1 if local(hit, *at)[0] < 0.5 else 1  # its left and right buttons
            n = max(1, len(entries(ctx, hit, models)))
            _write(ctx, frame, hit, (SWITCH_INDEX,), (int(hit.values.get((SWITCH_INDEX,)) or 0) + step) % n, out)
        elif "combo" in k:
            _write(ctx, frame, hit, (COMBO_OPEN,), not bool(hit.values.get((COMBO_OPEN,))), out)
        elif "wheel" in k:
            focused = wheel_focus(ctx, frame, at)
            if focused is not None:
                for path in ((FOCUSED,), (SELECTION,)):
                    _write(ctx, frame, focused[0], path, focused[1], out)
        else:
            continue
        break
    for hit in reversed(hits):
        link = scene_link(ctx, hit, current)
        if link is not None:
            out.open, out.note = link
            break
    return out


def _below(trail: Trail, top: Trail) -> bool:
    return len(trail) > len(top) and trail[: len(top)] == top


def _tab(ctx, frame, hit: Hit, out: Outcome) -> None:
    """A tab chosen: its bar's ``SelectedTabID`` is its id, each tab of the bar ``IsSelected`` when it is the one."""
    bar = next((h for h in reversed(frame.hits) if _below(hit.trail, h.trail) and "bar" in kinds(ctx, h)), None)
    tabs: dict[Trail, Hit] = {}
    for h in frame.hits:
        if (bar is None or _below(h.trail, bar.trail)) and TAB_ELEMENT in comps(ctx, h):
            tabs.setdefault(h.trail, h)
    order = list(tabs.values())
    given = [h.values.get((comps(ctx, h)[TAB_ELEMENT], ELEMENT_ID)) for h in order]
    ids = given if all(isinstance(i, int) for i in given) and len(set(given)) == len(given) else list(range(len(order)))
    mine = ids[next((i for i, h in enumerate(order) if h.trail == hit.trail), 0)] if order else 0
    for h, i in zip(order, ids, strict=True):
        path = (comps(ctx, h)[TAB_ELEMENT], TAB_SELECTED)
        if i == mine or h.values.get(path) is not False:  # one already unselected is left as it is (and kept)
            out.kept.append((h.trail, path, i == mine))
    if bar is not None:
        _write(ctx, frame, bar, (comps(ctx, bar)[TAB_BAR], SELECTED_TAB), mine, out)


def slide(ctx, frame, hits: list[Hit], at: tuple[float, float]) -> Outcome | None:
    """A slider pressed or dragged: its value where the pointer is along it, in its steps."""
    hit = next((h for h in reversed(hits) if "slider" in kinds(ctx, h)), None)
    if hit is None:
        return None
    fx, fy = local(hit, *at)
    w, h = hit.size
    f = min(1.0, max(0.0, 1.0 - fy if h > w * 1.5 else fx))  # an upright slider grows upwards
    lo, hi = (float(hit.values.get((uid,), d) or d) for uid, d in ((S_MIN, 0.0), (S_MAX, 1.0)))
    steps = int(hit.values.get((S_STEPS,)) or 0)
    out = Outcome()
    if steps > 0:
        step = round(f * steps)
        _write(ctx, frame, hit, (S_STEP,), step, out)
        f = step / steps
    _write(ctx, frame, hit, (S_VALUE,), lo + (hi - lo) * f, out)
    return out


def scroll(ctx, frame, hits: list[Hit], steps: int, first: int) -> tuple[Trail, int] | None:
    """The wheel over a list or a grid: (the list's trail, its new first shown element); a grid moves by rows."""
    hit = next((h for h in reversed(hits) if "list" in kinds(ctx, h)), None)
    reach = _reach(frame, hit, first) if hit is not None else None
    if reach is None:
        return None
    across, last = reach
    new = max(0, min(first + steps * across, last))
    return (hit.trail, new) if new != first else None


def _reach(frame, hit: Hit, first: int) -> tuple[int, int] | None:
    """A list's elements in a row (a grid's columns, else 1) and the last first shown element it scrolls to."""
    items = [h for h in frame.hits if len(h.trail) == len(hit.trail) + 1 and h.trail[:-1] == hit.trail]
    if not items:
        return None
    row = apply(items[0].matrix, 0, 0)[1]
    across = max(1, sum(1 for h in items if abs(apply(h.matrix, 0, 0)[1] - row) < 0.5))
    total = getattr(items[0].scope, "play_total", None)  # the data source's count (``playstate.seed``)
    full = int(total) if isinstance(total, int) and total > 0 else first + len(items)
    return across, max(0, full - len(items))


# drags other than a slider's: a scroll bar's handle, a draggable button


def begin_drag(ctx, frame, hits: list[Hit], at: tuple[float, float]) -> Drag | None:
    """A press that starts a drag: on a scroll bar, or on a draggable button (at its position then)."""
    for hit in reversed(hits):
        k = kinds(ctx, hit)
        if "scrollbar" in k:
            return Drag("scrollbar", hit.trail, at)
        if "draggable" in k:
            pos = hit.values.get((POSITION,), fields.get(hit.el.node, "Position", (0.0, 0.0, 0.0)))
            return Drag("draggable", hit.trail, at, (float(pos[0]), float(pos[1])))
    return None


def drag_to(ctx, frame, drag: Drag, at: tuple[float, float], scrolls: dict) -> Outcome:
    """The pointer dragged to ``at``: a scroll bar's handle moves along it and scrolls the list its ``ScrollInfo``
    is bound to (``scrolls``: the lists' first shown elements now), a draggable button moves with the pointer within
    its range; the button's ``DragStarted`` is held by the caller while the drag lasts."""
    out, hit = Outcome(), by_trail(frame, drag.trail)
    if hit is None:
        return out
    if drag.kind == "scrollbar":
        _scroll_bar(ctx, frame, hit, at, scrolls, out)
        return out
    lx0, ly0 = apply(invert(hit.matrix), *drag.start)
    lx1, ly1 = apply(invert(hit.matrix), *at)
    sx, sy = (float(v) for v in tuple(hit.values.get((SCALE,), fields.get(hit.el.node, "Scale", (1.0, 1.0))))[:2])
    turn = math.radians(float(hit.values.get((ROTATION,), fields.get(hit.el.node, "RotationAngle", 0.0)) or 0.0))
    dx, dy = (lx1 - lx0) * sx, (ly1 - ly0) * sy  # the move in the parent's units: the widget's own scale and turn
    x = drag.origin[0] + dx * math.cos(turn) - dy * math.sin(turn)
    y = drag.origin[1] + dx * math.sin(turn) + dy * math.cos(turn)
    lo, hi = hit.values.get((MIN_POS,)), hit.values.get((MAX_POS,))
    ranged = hit.values.get((RESTRICTION,), IN_RANGE) == IN_RANGE
    if ranged and isinstance(lo, (tuple, list)) and isinstance(hi, (tuple, list)) and tuple(lo) != tuple(hi):
        x, y = min(max(x, float(lo[0])), float(hi[0])), min(max(y, float(lo[1])), float(hi[1]))
    old = hit.values.get((POSITION,), fields.get(hit.el.node, "Position", (0.0, 0.0, 0.0)))
    _write(ctx, frame, hit, (POSITION,), (x, y, float(old[2]) if len(old) > 2 else 0.0), out)
    return out


def _scroll_bar(ctx, frame, bar: Hit, at, scrolls: dict, out: Outcome) -> None:
    """The handle where the pointer is along the bar, and the bound list's first element at that fraction."""
    across = bool(bar.values.get((HORIZONTAL,)))
    fx, fy = local(bar, *at)
    handle = _handle(ctx, frame, bar)
    span = (bar.size[0] if across else bar.size[1]) - ((handle.size[0] if across else handle.size[1]) if handle else 0)
    f = min(1.0, max(0.0, fx if across else fy))
    if handle is not None and span > 0:  # the engine places the handle: inferred, along the bar from its start
        pos = tuple(handle.values.get((POSITION,), fields.get(handle.el.node, "Position", (0.0, 0.0, 0.0))))
        moved = (f * span, float(pos[1])) if across else (float(pos[0]), f * span)
        out.kept.append((handle.trail, (POSITION,), (*moved, float(pos[2]) if len(pos) > 2 else 0.0)))
    listed = scrolled_by(frame, bar)
    reach = _reach(frame, listed, scrolls.get(listed.trail, 0)) if listed is not None else None
    if reach is not None:
        across_n, last = reach
        out.scrolls.append((listed.trail, round(f * last / across_n) * across_n))


def _handle(ctx, frame, bar: Hit) -> Hit | None:
    """The scroll bar's handle: the child its ``Handle`` names, else one named so."""
    named = bar.values.get((HANDLE,))
    kids = [h for h in frame.hits if len(h.trail) == len(bar.trail) + 1 and h.trail[:-1] == bar.trail]
    found = next((h for h in kids if isinstance(named, str) and h.trail[-1][0] == named), None)
    return found or next((h for h in kids if "handle" in (h.el.name or "").lower()), None)


def scrolled_by(frame, bar: Hit) -> Hit | None:
    """The list a scroll bar scrolls: the instance beside it whose property its ``ScrollInfo`` is bound to."""
    scope, guid = bar.scope, bar.trail[-1][0]
    for b in scope.graph.bindings:
        ends = [*b.sources, *b.targets]
        if not any(e.kind == "path" and e.instance == guid and e.ids[:1] == [SCROLL_INFO] for e in ends):
            continue
        for e in ends:
            if e.kind == "path" and e.mode == bindings.CHILD and e.instance and e.instance != guid:
                found = by_trail(frame, (*bar.trail[:-1], (e.instance, bar.trail[-1][1])))
                if found is not None:
                    return found
    return None


# the focus walk by direction, as a pad's


def neighbour(ctx, frame, current: Hit | None, direction: str, order: list[Hit]) -> Hit | None:
    """The widget the focus moves to from ``current`` in ``direction``: the neighbour its ``Neighbor...`` property
    names, else the nearest focusable one that way (its distance along the way, twice the one across)."""
    if current is None:
        return order[0] if order else None
    named = current.values.get((NEIGHBOURS[direction],))
    if isinstance(named, str) and named.strip("0-"):
        found = by_trail(frame, (*current.trail[:-1], (named, current.trail[-1][1])))
        if found is not None:
            return found
    (cx, cy), (ux, uy) = _centre(current), DIRECTIONS[direction]
    best, score = None, math.inf
    for h in order:
        if h.trail == current.trail:
            continue
        x, y = _centre(h)
        along, across = (x - cx) * ux + (y - cy) * uy, abs((x - cx) * uy - (y - cy) * ux)
        if along > 0.5 and along + 2 * across < score:
            best, score = h, along + 2 * across
    return best


def _centre(hit: Hit) -> tuple[float, float]:
    return apply(hit.matrix, hit.size[0] / 2, hit.size[1] / 2)


# a toggle's and a combo box's entries


def entries(ctx, hit: Hit, models=None) -> list[str]:
    """A toggle's or a combo box's entries, as the list shows them: the elements of the array its ``Entries`` holds
    (the game's data or the sample values; their names where the game's data has them), else the values of the
    enum of the field its index is bound to. At least two for a toggle (it flips)."""
    combo = "combo" in kinds(ctx, hit)
    value = hit.values.get((COMBO_ENTRIES if combo else SWITCH_ENTRIES,))
    found = _array(ctx, value.base if isinstance(value, GivenObject) else value)
    if not found and not combo:
        found = _enum_entries(ctx, hit, models)
    return found or ([] if combo else [tr("ui.play_entry", n=1), tr("ui.play_entry", n=2)])


def _array(ctx, value) -> list[str]:
    if not isinstance(value, GameObjectRef):
        return []
    game, key = getattr(ctx, "game_data", None), value.key
    nodes = game.elements(key) if game is not None and hasattr(game, "elements") else None
    if nodes:
        return [game.assets.name(n[0], ctx.language) or tr("ui.play_entry", n=i + 1) for i, n in enumerate(nodes)]
    prefix, seen = f"{key}/", set()
    for k in ctx.samples.given_under(prefix):
        part = k[len(prefix) :].split("/", 1)[0]
        if len(part) == 8 and int(part, 16) >= ELEMENT:
            seen.add(int(part, 16) - ELEMENT)
    return [tr("ui.play_entry", n=i + 1) for i in range(max(seen) + 1)] if seen else []


def _enum_entries(ctx, hit: Hit, models) -> list[str]:
    """The names of the enum the toggle's index is written back to (its field's type in the game's data classes)."""
    from ..sources import enums117

    found = setting_for(ctx, None, hit, (SWITCH_INDEX,)) if models is not None else None
    name = found[0].rsplit(".", 1)[-1] if found else ""
    kind = next((f.type for c in getattr(models, "classes", {}).values() for f in c.fields if f.name == name), "")
    space, _, enum = kind.partition(".")
    folder = {"halo": enums117.LUA_ENGINE, "datasets": enums117.LUA_DATASETS}.get(space)
    tree = getattr(ctx.files, "tree", None)
    if not folder or not enum or tree is None or not tree.exists(f"{folder}{enum}.lua"):
        return []
    parsed = enums117.read_lua_enum(tree.read(f"{folder}{enum}.lua").decode("utf-8", "replace"))
    return [n for n, _ in parsed.values if n not in ("Invalid", "Count")] if parsed is not None else []


def pick_entry(ctx, frame, combo: Hit, index: int) -> Outcome:
    """A combo box's entry chosen from its list: its ``SelectedIndex`` (and the index property its placing binds two
    ways, which the file passes on), the list closed."""
    out = Outcome()
    _write(ctx, frame, combo, (COMBO_INDEX,), index, out)
    if not out.choices:
        for path in _two_way_paths(combo):
            _write(ctx, frame, combo, path, index, out)
    _write(ctx, frame, combo, (COMBO_OPEN,), False, out)
    return out


def _two_way_paths(hit: Hit) -> list[tuple]:
    """The own properties of an instance its placing binds two ways (an index the file passes on)."""
    out = []
    for b in hit.scope.graph.bindings:
        if b.operator == TWO_WAY and b.sources and b.targets:
            for end in (b.sources[0], b.targets[0]):
                if end.kind == "path" and end.instance == hit.trail[-1][0] and len(end.ids) == 1:
                    out.append(tuple(end.ids))
    return out


def wheel_focus(ctx, frame, at: tuple[float, float]) -> tuple[Hit, int] | None:
    """The radial wheel's element under the pointer, by its sector (the angle from the wheel's centre): the
    wheel's hit and the element's index."""
    for wheel in reversed(frame.hits if frame is not None else ()):
        if "wheel" not in kinds(ctx, wheel):
            continue
        items = [h for h in frame.hits if len(h.trail) == len(wheel.trail) + 1 and h.trail[:-1] == wheel.trail]
        if not items:
            continue
        cx, cy = apply(wheel.matrix, 0, 0)
        spots = [(apply(h.matrix, h.size[0] / 2, h.size[1] / 2), h.trail[-1][1] or 0) for h in items]
        reach = max(math.hypot(x - cx, y - cy) for (x, y), _ in spots)
        dist = math.hypot(at[0] - cx, at[1] - cy)
        if reach <= 0 or dist > reach * 1.6 or dist < reach * 0.15:
            continue
        angle = math.atan2(at[0] - cx, -(at[1] - cy))
        return wheel, min(spots, key=lambda s, c=(cx, cy), a=angle: _apart(s[0], c, a))[1]
    return None


def _apart(spot: tuple[float, float], centre: tuple[float, float], angle: float) -> float:
    """How far a point's angle around ``centre`` (from straight up, clockwise) lies from ``angle``."""
    d = abs(math.atan2(spot[0] - centre[0], -(spot[1] - centre[1])) - angle) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


# what a widget shows or starts


def tip(ctx, hits: list[Hit]) -> str:
    """The info tip of the topmost widget under the pointer that has one: its text line, the asset it is about (name
    and description from the game's data), else the definition's number."""
    for hit in reversed(hits):
        c = comps(ctx, hit)
        if INFO_TIP not in c:
            continue
        uid = c[INFO_TIP]
        text, ref, guid = (hit.values.get((uid, p)) for p in (TIP_TEXT, TIP_REF, TIP_GUID))
        lines = []
        if isinstance(text, str) and text.strip():
            lines.append(ctx.files.text_of(text.strip(), ctx.language) or text)
        game = getattr(ctx, "game_data", None)
        if isinstance(ref, int) and ref > 0 and game is not None:
            lines += [t for t in (game.assets.name(ref, ctx.language), game.assets.description(ref, ctx.language)) if t]
        if not lines and isinstance(guid, int) and guid > 0:
            lines.append(tr("ui.play_tip_definition", guid=guid))
        if lines:
            return "\n".join(dict.fromkeys(lines))
    return ""


def scene_link(ctx, hit: Hit, current: str) -> tuple[str, str] | None:
    """Where a click of the widget goes: (a scene file to open, the note to show), or None when its release starts
    nothing in the game's data. A field ``Open<Name>`` names the scene ``<name>scene``; any other event is named."""
    m = _mouse(ctx, hit)
    events = [(ON_RELEASE,), *([(m, M_UP)] if m is not None else [])]
    for b in hit.scope.graph.bindings:
        if not any(_on(s, hit.scope, hit.el.guid, e) for s in b.sources for e in events):
            continue
        for t in b.targets:
            found = _field(ctx, None, hit, t, 99) if t.kind == "path" and t.mode == bindings.SELF else None
            if found is not None:
                return _scene_of(ctx, found[0], current)
    return None


def _scene_of(ctx, name: str, current: str) -> tuple[str, str]:
    parts = name.split(".")
    for part in reversed(parts):
        for verb in ("Open", "Show"):
            if part.startswith(verb) and len(part) > len(verb):
                base = part[len(verb) :].lower()
                for key in (f"{base}scene", base, f"{base.removesuffix('scene')}scene"):
                    entry = ctx.files.entries.get(key)
                    if entry is not None and entry.kind == "SC" and key != current:
                        return key, tr("ui.play_opens_scene", scene=key)
                return "", tr("ui.play_opens_not_scene", name=part[len(verb) :])
    return "", tr("ui.play_fires_event", name=".".join(parts[1:]) or name)
