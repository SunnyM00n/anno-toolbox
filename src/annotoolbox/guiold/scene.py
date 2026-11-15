"""A dialog of the old interface as placed rectangles: every widget with its effective properties (its own over its
templates' and its component's), its rectangle in the visible space of a screen (1024 high), its text and its
background.

``build(files, dialog, state, screen, measure)`` is the entry: a ``Node`` tree in one of the ``STATES`` on one of
the ``SCREENS``. ``tile_rects(look, w, h)`` cuts a background state into the rectangles its tiles fill. Inferred rules
(a box layout's stacking, a card layout's first page, the colours, autosize, the modes, a label resizing its parent)
are in docs/ui/old-gui.md; the rules the game applies are docs/ui/2070/layout.md.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from . import richtext
from .files import Font, Look, OldGui, Tile, hsv_rgb, number
from .gui2070 import REFERENCE_SIZE, Templates, Widget, extent

REFERENCE = REFERENCE_SIZE
SCREENS = {"5:4": (1280.0, 1024.0), "4:3": (1365.0, 1024.0), "16:10": (1638.0, 1024.0), "16:9": (1820.0, 1024.0),
           "21:9": (2389.0, 1024.0)}  # fmt: skip  # the visible space: the scale is the screen's height / 1024
Measure = Callable[[str, Font | None, float, richtext.Style | None], tuple[float, float]]  # wrap width or 0 -> size
STATE_ORDER = ("Normal", "EmptyLayout", "FR_Background", "PB_Background")  # the state drawn at rest, the first found
STATES = ("Normal", "Normal+MouseOver", "Normal+MouseButtonDown", "Normal+Disabled", "Checked", "Checked+MouseOver",
          "Checked+MouseButtonDown", "Checked+Disabled")  # fmt: skip
STATE_TEXT = {"MouseOver": "MOUSEOVERTEXTCOLOR", "MouseButtonDown": "PRESSEDBUTTONTEXTCOLOR",
              "Disabled": "DISABLEDTEXTCOLOR"}  # fmt: skip
WHITE = 16777215
CLIPS = {"SCROLLBOX"}  # what it holds is cut to its client area (docs/ui/old-gui.md "Scroll boxes")
FOLLOWS = ("1", "3", "4")  # the modes by which a child follows its parent's size: fill, centre, far edge


@dataclass(eq=False)
class Node:
    widget: Widget
    props: dict  # effective: own over the templates'
    rect: tuple[float, float, float, float]  # x, y, w, h in the reference space (absolute)
    text: str = ""
    background: str = ""
    look: Look | None = None
    visible: bool = True
    children: list[Node] = field(default_factory=list)
    tint: tuple[float, float, float] | None = None  # what the background's pictures are multiplied by
    text_color: tuple[int, int, int] | None = None  # None: the font's colour
    font: Font | None = None
    style: richtext.Style | None = None  # the style sheet's class its ``STYLESELECTOR`` names
    selector: str = ""  # that class's name: the outermost element its text's rules see
    icon: Tile | None = None
    icon_rect: tuple[float, float, float, float] | None = None  # absolute, as ``rect``
    design: tuple[float, float] = (0.0, 0.0)  # its size as stored: what its children's modes are measured against

    @property
    def name(self) -> str:
        return self.widget.path_id or self.widget.kind

    @property
    def kind(self) -> str:
        return self.widget.kind

    @property
    def around(self) -> tuple[richtext.Step, ...]:
        """What its text sits in for the sheet's rules: its class (the label's ``STYLESELECTOR``)."""
        return (("", (self.selector.lower(),)),) if self.selector else ()

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def at(self, x: float, y: float) -> Node | None:
        """The deepest visible widget under a point (the last drawn wins among siblings)."""
        inside = _within(self.rect, x, y)
        if self.kind in CLIPS and not _within(client(self), x, y):  # what it holds shows in its client area only
            return self if inside else None
        for c in reversed(self.children):
            hit = c.at(x, y) if c.visible else None
            if hit is not None:
                return hit
        return self if inside else None


def _within(rect: tuple[float, float, float, float], x: float, y: float) -> bool:
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def _flag(value, default: bool) -> bool:
    return default if value in (None, "") else str(value).strip() not in ("0", "false", "False")


def rest_look(files: OldGui, background: str, state: str = "Normal") -> Look | None:
    """The state ``state`` of a background, else the one it shows at rest."""
    states = files.background(background) if background else {}
    for name in (state, *STATE_ORDER):
        if name in states:
            return states[name]
    return next(iter(states.values()), None)


def text_of(files: OldGui, props: dict) -> str:
    setting = props.get("TEXTSETTING")
    ident = setting.get("TEXTGUID") if isinstance(setting, dict) else None
    if ident in (None, ""):
        ident = props.get("TEXT")
    return files.text(ident if isinstance(ident, (str, int)) else None)


def text_setting(props: dict) -> dict:
    setting = props.get("TEXTSETTING")
    return setting if isinstance(setting, dict) else {}


def tint_of(files: OldGui, props: dict, look: Look | None) -> tuple[float, float, float] | None:
    """The factors a widget's background is multiplied by: its state's ``m_Color``, its ``COLOR`` and ``RGBCOLOR``
    (``COLOROPERATION`` 1 and 2 are not named in the files and are taken as multiply too)."""
    out = [1.0, 1.0, 1.0]
    if look is not None and look.tint is not None:
        out = [a * b for a, b in zip(out, hsv_rgb(look.tint[0], look.tint[1], look.tint[2]), strict=True)]
    for key, unchanged in (("COLOR", "HSV;0;100;100"), ("RGBCOLOR", str(WHITE))):
        value = str(props.get(key) or "").strip()
        rgb = files.colour(value) if value and value != unchanged else None
        if rgb is not None:
            out = [a * c / 255 for a, c in zip(out, rgb, strict=True)]
    return None if out == [1.0, 1.0, 1.0] else (out[0], out[1], out[2])


def text_color_of(files: OldGui, props: dict, state: str) -> tuple[int, int, int] | None:
    """The text colour in ``state``: the state's own colour where the widget names one, else ``TEXTCOLOR``."""
    ts = text_setting(props)
    for part, key in STATE_TEXT.items():
        if part in state.split("+") and ts.get(key) not in (None, ""):
            found = files.colour(ts[key])
            if found is not None:
                return found
    return files.colour(ts.get("TEXTCOLOR"))


def icon_rect(props: dict, rect, icon: Tile) -> tuple[float, float, float, float]:
    """Where a widget's icon is drawn: the whole widget (``ICONSTRETCH``), else its ``ICONSPACE`` from the widget's top
    left (2070 names the fields ``ICONLEFT`` ..., 1404 and 1701 ``LEFT`` ...), at the icon's own size where it has none."""
    x, y, w, h = rect
    if str(props.get("ICONSTRETCH", "0")).strip() == "1":
        return rect
    space = props.get("ICONSPACE") if isinstance(props.get("ICONSPACE"), dict) else {}

    def get(name: str) -> float:
        return number(space.get(f"ICON{name}", space.get(name)), 0.0)

    width, height = get("WIDTH") or float(icon.w), get("HEIGHT") or float(icon.h)
    return x + get("LEFT"), y + get("TOP"), width, height


@dataclass
class Placing:
    """What placing one dialog needs besides the widgets: the files, the templates, the components, the state."""

    files: OldGui
    templates: Templates
    state: str
    measure: Measure
    components: dict[int, Widget] = field(default_factory=dict)
    sheet: richtext.StyleSheet = field(default_factory=richtext.StyleSheet)


def estimate(text: str, font: Font | None, width: float, style: richtext.Style | None = None) -> tuple[float, float]:
    """A text's size without a font measure: half a font size per character, lines 1.25 font sizes apart."""
    size = (style.size if style is not None else None) or (font.size if font is not None else 14.0)
    lines = richtext.plain(text).split("\n") or [""]
    per = max(1, int(width / (size * 0.55))) if width > 0 else 0
    count = sum(max(1, math.ceil(len(line) / per)) if per else 1 for line in lines)
    longest = max(len(line) for line in lines) * size * 0.55
    return (min(longest, width) if per else longest), count * size * 1.25


def build(files: OldGui, dialog: Widget, state: str = "Normal", screen: tuple[float, float] = REFERENCE,
          measure: Measure | None = None) -> Node:  # fmt: skip
    """The node tree of a dialog with its widgets in ``state`` (where their background has it), on a screen whose
    visible space is ``screen`` (``SCREENS``); ``measure`` sizes a text in a font (``estimate`` without one)."""
    run = Placing(files, files.templates(), state, measure or estimate, files.components(), files.sheet)
    root = _node(run, dialog, None, (0.0, 0.0, 0.0, 0.0))
    root.rect, root.design = _frame_rect(root.props, screen)
    root.visible = True  # the game shows a frame when it opens one: the preview is that moment
    _fill(run, root, dialog)
    return root


def _frame_rect(props: dict, screen: tuple[float, float]):
    """Where a frame sits on the screen, and the size it was designed at: fill and centre by its modes (a stale
    stored size is the reference's), otherwise its stored rectangle with its edges measured from the screen's near
    or far side as its edge anchors say (docs/ui/2070/layout.md "Frames")."""
    x, y, w, h = (float(v) for v in extent(props))
    rect, design = [x, y, w, h], [w, h]
    axes = (("LAYOUTMODEH", "ALIGNMENT_LEFTHORIZONTAL", "ALIGNMENT_RIGHTHORIZONTAL"),
            ("LAYOUTMODEV", "ALIGNMENT_TOPVERTICAL", "ALIGNMENT_BOTTOMVERTICAL"))  # fmt: skip
    for axis, (mode, near, far) in enumerate(axes):
        how, grown = str(props.get(mode, "0")).strip(), screen[axis] - REFERENCE[axis]
        if how == "1" or rect[axis + 2] < 1:
            rect[axis], rect[axis + 2], design[axis] = 0.0, screen[axis], REFERENCE[axis]
        elif how == "3":
            rect[axis] = (screen[axis] - rect[axis + 2]) / 2
        else:
            start = rect[axis] + (grown if str(props.get(near, "1")).strip() == "2" else 0.0)
            stop = rect[axis] + rect[axis + 2] + (grown if str(props.get(far, "1")).strip() == "2" else 0.0)
            rect[axis], rect[axis + 2] = start, max(0.0, stop - start)
    return (rect[0], rect[1], rect[2], rect[3]), (design[0], design[1])


def _resolved(run: Placing, widget: Widget) -> tuple[dict, list[Widget]]:
    """A widget's properties and children; an ``INSTANCE`` takes its component's under its own (its own win), the
    component's children before its own."""
    props = run.templates.effective(widget)
    ref = str(props.get("STRUCTTEMPLATEREF") or "").strip()
    component = run.components.get(int(ref)) if widget.kind == "INSTANCE" and ref.lstrip("-").isdigit() else None
    if component is None or component is widget:
        return props, widget.children
    base = run.templates.effective(component)
    merged = dict(base)
    for k, v in props.items():
        merged[k] = {**base[k], **v} if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return merged, [*component.children, *widget.children]


def _node(run: Placing, widget: Widget, parent: Node | None, rect) -> Node:
    files, state = run.files, run.state
    props, _ = _resolved(run, widget)
    bg = props.get("BACKGROUND")
    bg = bg if isinstance(bg, str) and bg not in ("(invalid)", "") else ""
    visible = _flag(props.get("VISIBLE"), True) and not _flag(props.get("HIDDEN"), False)
    look = rest_look(files, bg, state)
    node = Node(widget, props, rect, text_of(files, props), bg, look, visible)
    node.tint, node.text_color = tint_of(files, props, look), text_color_of(files, props, state)
    node.font = files.fonts.get(str(text_setting(props).get("FONT") or props.get("FONT") or ""))
    node.selector = str(text_setting(props).get("STYLESELECTOR") or props.get("STYLESELECTOR") or "").strip()
    node.style = run.sheet.get(node.selector)
    if node.style is not None and node.style.upper:
        node.text = node.text.upper()
    node.icon = files.icon(props.get("ICONGUID")) if str(props.get("ICONGUID", "-1")).strip() != "-1" else None
    node.design = (float(rect[2]), float(rect[3]))
    if parent is not None:
        parent.children.append(node)
    return node


def client(node: Node) -> tuple[float, float, float, float]:
    """The rectangle a node's children are placed in: its own, less the background's client area offsets."""
    x, y, w, h = node.rect
    left, top, right, bottom = node.look.offsets if node.look is not None else (0, 0, 0, 0)
    return x + left, y + top, max(0.0, w - left - right), max(0.0, h - top - bottom)


def _fill(run: Placing, node: Node, widget: Widget, seen: tuple = ()) -> None:
    cx, cy, _cw, _ch = client(node)
    grown = (node.rect[2] - node.design[0], node.rect[3] - node.design[1])
    kids = []
    for child in _resolved(run, widget)[1] if widget not in seen else ():  # a component in itself is not followed
        left, top, width, height = extent(_resolved(run, child)[0])
        kid = _node(run, child, node, (cx + left, cy + top, float(width), float(height)))
        if node.kind != "BOXLAYOUT":  # a box places its children itself
            _follow(kid, grown)
        kids.append((kid, child))
    kids.sort(key=lambda kc: _zorder(kc[0]))  # stable: ties keep the file's order
    node.children = [k for k, _ in kids]
    if node.kind == "CARDLAYOUT":
        for kid, _ in kids[1:]:
            kid.visible = False  # one page shows
    for kid, child in kids:
        _fill(run, kid, child, (*seen, widget))
    if node.kind == "BOXLAYOUT":  # after the children's own sizes: a box stacks them at the size they have
        _stack(node)
    placed = node.rect[2:]
    for kid in node.children:
        _resize_parent(node, kid)
    _autosize(node)
    _follow_size(node, (node.rect[2] - placed[0], node.rect[3] - placed[1]))
    _size_to_text(run, node)  # before its parent places it: a box stacks it at this size
    if node.icon is not None:
        node.icon_rect = icon_rect(node.props, node.rect, node.icon)


def _follow(kid: Node, grown: tuple[float, float]) -> None:
    """A child's ``LAYOUTMODEH`` / ``V`` when its parent is ``grown`` larger than stored: fill grows by it, centre
    moves by half, right / bottom by all of it."""
    x, y, w, h = kid.rect
    for axis, mode in ((0, "LAYOUTMODEH"), (1, "LAYOUTMODEV")):
        how, d = str(kid.props.get(mode, "0")).strip(), grown[axis]
        if not d:
            continue
        if how == "1":
            w, h = (w + d, h) if axis == 0 else (w, h + d)
        elif how in ("3", "4"):
            move = d / 2 if how == "3" else d
            x, y = (x + move, y) if axis == 0 else (x, y + move)
    kid.rect = (x, y, w, h)


def _resizes(label: Node) -> str:
    """A label's ``RESIZEPARENTWINDOW`` (1 width, 2 height, 3 both), "" where it takes no size from its text."""
    ts = text_setting(label.props)
    how = str(ts.get("RESIZEPARENTWINDOW") or label.props.get("RESIZEPARENTWINDOW") or "0").strip()
    return how if how in ("1", "2", "3") and label.visible and label.text else ""


def _size_to_text(run: Placing, label: Node) -> None:
    """A resizing label takes its text's size on its axes (in its font and class), wrapped at its stored width with
    ``ENABLE_LINEBREAK``."""
    how = _resizes(label)
    if not how:
        return
    ts = text_setting(label.props)
    wrap = _flag(ts.get("ENABLE_LINEBREAK") or label.props.get("ENABLE_LINEBREAK"), False)
    measured = run.measure(label.text, label.font, label.rect[2] if wrap else 0.0, label.style)
    rect = list(label.rect)
    for axis in (0, 1):
        if how in ("3", ("1", "2")[axis]):
            rect[axis + 2] = measured[axis]
    label.rect = (rect[0], rect[1], rect[2], rect[3])


def _resize_parent(node: Node, label: Node) -> None:
    """The parent of a label sized to its text follows it, keeping the stored margin at the far edge."""
    how, rect = _resizes(label), list(node.rect)
    for axis in (0, 1) if how else ():
        if how in ("3", ("1", "2")[axis]):
            margin = (node.rect[axis] + node.design[axis]) - (label.rect[axis] + label.design[axis])
            rect[axis + 2] = label.rect[axis] + label.rect[axis + 2] + max(0.0, margin) - rect[axis]
    node.rect = (rect[0], rect[1], rect[2], rect[3])


def _mode(node: Node, axis: int) -> str:
    return str(node.props.get(("LAYOUTMODEH", "LAYOUTMODEV")[axis], "0")).strip()


def _follow_size(node: Node, grown: tuple[float, float]) -> None:
    """The children of a widget whose size changed after they were placed (its autosize, a resizing label) follow
    the difference by their modes, as they follow a wider screen; a box places its children itself."""
    if node.kind == "BOXLAYOUT" or not any(grown):
        return
    for kid in node.children:
        for axis in (0, 1):
            how, d = _mode(kid, axis), grown[axis]
            if not d or how not in FOLLOWS or _resizes(kid) in ("3", ("1", "2")[axis]):  # it made the change
                continue
            if how == "1":
                x, y, w, h = kid.rect
                kid.rect = (x, y, w + d, h) if axis == 0 else (x, y, w, h + d)
                _follow_size(kid, (d, 0.0) if axis == 0 else (0.0, d))
            else:
                move = d / 2 if how == "3" else d
                _shift(kid, move if axis == 0 else 0.0, move if axis == 1 else 0.0)


def _autosize(node: Node) -> None:
    """``AUTOSIZE`` on one axis (``AUTOSIZEORIENTATION`` 1 vertical, 2 horizontal): the extent of the visible children
    that do not follow its size, plus the padding, bounded by ``MINSIZE`` / ``MAXSIZE``; ``AUTOSIZEALIGN_*`` 1 keeps
    the far end where it was."""
    p = node.props
    axis = {"1": 1, "2": 0}.get(str(p.get("AUTOSIZEORIENTATION", "0")).strip())
    # a child that follows the size on this axis does not make it (``_follow_size`` moves it after)
    kids = [k.rect for k in node.children if k.visible and (axis is None or _mode(k, axis) not in FOLLOWS)]
    if str(p.get("AUTOSIZE", "0")).strip() != "1" or axis is None or not kids:
        return
    rect = list(node.rect)
    end = "PADDINGRIGHT" if axis == 0 else "PADDINGBOTTOM"  # the children already sit past the client offsets
    size = max(r[axis] + r[axis + 2] for r in kids) - rect[axis] + number(p.get(end), 0.0)
    low, high = (_bounds(p.get("MINSIZE"))[axis], _bounds(p.get("MAXSIZE"))[axis])
    size = max(size, low) if low else size
    size = min(size, high) if high else size
    align = "AUTOSIZEALIGN_HORIZONTAL" if axis == 0 else "AUTOSIZEALIGN_VERTICAL"
    if str(p.get(align, "0")).strip() == "1":  # the far end stays: the widget and what it holds move the other way
        moved = rect[axis + 2] - size
        for kid in node.children:
            _shift(kid, moved if axis == 0 else 0.0, moved if axis == 1 else 0.0)
        rect[axis] += moved
    rect[axis + 2] = size
    node.rect = (rect[0], rect[1], rect[2], rect[3])


def _shift(node: Node, dx: float, dy: float) -> None:
    """Move a placed widget and everything it holds (its rectangles are absolute)."""
    for n in node.walk():
        x, y, w, h = n.rect
        n.rect = (x + dx, y + dy, w, h)
        if n.icon_rect is not None:
            ix, iy, iw, ih = n.icon_rect
            n.icon_rect = (ix + dx, iy + dy, iw, ih)


def _bounds(value) -> tuple[float, float]:
    """``"w; h"`` (``MINSIZE``, ``MAXSIZE``), 0 for no bound."""
    parts = [number(v, 0.0) for v in str(value or "").split(";")] + [0.0, 0.0]
    return parts[0], parts[1]


def _zorder(node: Node) -> int:
    try:
        return int(node.props.get("ZORDER") or 0)
    except (TypeError, ValueError):
        return 0


def _stack(box: Node) -> None:
    """A box layout places its children itself: line after line (or column after column), ``LINECOLUMNCOUNT`` to a
    line, ``XSPACE`` / ``YSPACE`` apart; a hidden child leaves no gap."""
    cx, cy, _cw, _ch = client(box)
    xs, ys = number(box.props.get("XSPACE"), 5), number(box.props.get("YSPACE"), 5)
    per = max(1, int(number(box.props.get("LINECOLUMNCOUNT"), 1)))
    by_line = str(box.props.get("LINE_ORDER", "1")) != "0"
    line_size, count, x, y = 0.0, 0, cx, cy
    for kid in (k for k in box.children if k.visible):
        w, h = kid.rect[2], kid.rect[3]
        _shift(kid, x - kid.rect[0], y - kid.rect[1])
        count += 1
        if by_line:
            x += w + xs
            line_size = max(line_size, h)
            if count % per == 0:
                x, y, line_size = cx, y + line_size + ys, 0.0
        else:
            y += h + ys
            line_size = max(line_size, w)
            if count % per == 0:
                x, y, line_size = x + line_size + xs, cy, 0.0


def tile_rects(look: Look, w: float, h: float) -> list[tuple[Tile, tuple[float, float, float, float]]]:
    """The tiles of a state with the rectangle each fills when the background is ``w`` x ``h``: a column or row
    with no stretched tile keeps its tile size, the others share what is left equally."""
    cols, rows = max(1, look.columns), max(1, look.rows)
    grid = [look.tiles[i] if i < len(look.tiles) else None for i in range(cols * rows)]

    def sizes(count: int, total: float, fixed, stretched) -> list[float]:
        out = [0.0] * count
        flex = [i for i in range(count) if stretched(i)]
        for i in range(count):
            if i not in flex:
                out[i] = fixed(i)
        left = max(0.0, total - sum(out))
        for i in flex:
            out[i] = left / len(flex)
        return out

    def col(i: int) -> list[Tile]:
        return [t for r in range(rows) if (t := grid[r * cols + i]) is not None]

    def row(i: int) -> list[Tile]:
        return [t for c in range(cols) if (t := grid[i * cols + c]) is not None]

    widths = sizes(cols, w, lambda i: max((t.w for t in col(i)), default=0), lambda i: any(t.stretch_x for t in col(i)))
    heights = sizes(
        rows, h, lambda i: max((t.h for t in row(i)), default=0), lambda i: any(t.stretch_y for t in row(i))
    )
    out, y = [], 0.0
    for r in range(rows):
        x = 0.0
        for c in range(cols):
            tile = grid[r * cols + c]
            if tile is not None:
                out.append((tile, (x, y, widths[c], heights[r])))
            x += widths[c]
        y += heights[r]
    return out
