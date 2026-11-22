"""A dialog of the old interface as placed rectangles: every widget with its effective properties (its own over its
templates' and its component's), its rectangle in the visible space of a screen (1024 high), its text and its
background.

``build(files, dialog, state, screen, measure)`` is the entry: a ``SNode`` tree in one of the ``STATES`` on one of
the ``SCREENS``. ``tile_rects(look, w, h)`` cuts a background state into the rectangles its tiles fill. Inferred rules
(a box layout's stacking, a card layout's first page, the colours, autosize, the modes, a label resizing its parent)
are in docs/ui/old-gui.md; the rules the game applies are docs/ui/2070/layout.md.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from . import richtext
from .files import COldGui, SFont, SLook, STile, hsv_rgb, number
from .gui2070 import REFERENCE_SIZE, CTemplates, SWidget, extent

REFERENCE = REFERENCE_SIZE
SCREENS = {"5:4": (1280.0, 1024.0), "4:3": (1365.0, 1024.0), "16:10": (1638.0, 1024.0), "16:9": (1820.0, 1024.0),
           "21:9": (2389.0, 1024.0)}  # fmt: skip  # the visible space: the scale is the screen's height / 1024
Measure = Callable[[str, SFont | None, float, richtext.SStyle | None], tuple[float, float]]  # wrap width or 0 -> size
STATE_ORDER = ("Normal", "EmptyLayout", "FR_Background", "PB_Background")  # the state drawn at rest, the first found
STATES = ("Normal", "Normal+MouseOver", "Normal+MouseButtonDown", "Normal+Disabled", "Checked", "Checked+MouseOver",
          "Checked+MouseButtonDown", "Checked+Disabled")  # fmt: skip
STATE_TEXT = {"MouseOver": "MOUSEOVERTEXTCOLOR", "MouseButtonDown": "PRESSEDBUTTONTEXTCOLOR",
              "Disabled": "DISABLEDTEXTCOLOR"}  # fmt: skip
WHITE = 16777215
CLIPS = {"SCROLLBOX"}  # what it holds is cut to its client area (docs/ui/old-gui.md "Scroll boxes")
FOLLOWS = ("1", "3", "4")  # the modes by which a child follows its parent's size: fill, centre, far edge


@dataclass(eq=False)
class SNode:
    widget: SWidget
    props: dict  # effective: own over the templates'
    rect: tuple[float, float, float, float]  # x, y, w, h in the reference space (absolute)
    text: str = ""
    background: str = ""
    look: SLook | None = None
    visible: bool = True
    children: list[SNode] = field(default_factory=list)
    tint: tuple[float, float, float] | None = None  # what the background's pictures are multiplied by
    text_color: tuple[int, int, int] | None = None  # None: the font's colour
    font: SFont | None = None
    style: richtext.SStyle | None = None  # the style sheet's class its ``STYLESELECTOR`` names
    selector: str = ""  # that class's name: the outermost element its text's rules see
    icon: STile | None = None
    icon_rect: tuple[float, float, float, float] | None = None  # absolute, as ``rect``
    design: tuple[float, float] = (0.0, 0.0)  # its size as stored: what its children's modes are measured against
    by_text: bool = True  # a label that resizes takes its text's size (off: ``build(text_sizes=False)``)

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
        for child in self.children:
            yield from child.walk()

    def at(self, x: float, y: float) -> SNode | None:
        """The deepest visible widget under a point (the last drawn wins among siblings)."""
        inside = _within(self.rect, x, y)
        if self.kind in CLIPS and not _within(client(self), x, y):  # what it holds shows in its client area only
            return self if inside else None
        for child in reversed(self.children):
            hit = child.at(x, y) if child.visible else None
            if hit is not None:
                return hit
        return self if inside else None


def _within(rect: tuple[float, float, float, float], x: float, y: float) -> bool:
    rect_x, rect_y, rect_width, rect_height = rect
    return rect_x <= x < rect_x + rect_width and rect_y <= y < rect_y + rect_height


def _flag(value, default: bool) -> bool:
    return default if value in (None, "") else str(value).strip() not in ("0", "false", "False")


def rest_look(files: COldGui, background: str, state: str = "Normal") -> SLook | None:
    """The state ``state`` of a background, else the one it shows at rest."""
    states = files.background(background) if background else {}
    for name in (state, *STATE_ORDER):
        if name in states:
            return states[name]
    return next(iter(states.values()), None)


def text_of(files: COldGui, props: dict) -> str:
    setting = props.get("TEXTSETTING")
    ident = setting.get("TEXTGUID") if isinstance(setting, dict) else None
    if ident in (None, ""):
        ident = props.get("TEXT")
    return files.text(ident if isinstance(ident, (str, int)) else None)


def text_setting(props: dict) -> dict:
    setting = props.get("TEXTSETTING")
    return setting if isinstance(setting, dict) else {}


def tint_of(files: COldGui, props: dict, look: SLook | None) -> tuple[float, float, float] | None:
    """The factors a widget's background is multiplied by: its state's ``m_Color``, its ``COLOR`` and ``RGBCOLOR``
    (``COLOROPERATION`` 1 and 2 are not named in the files and are taken as multiply too)."""
    out = [1.0, 1.0, 1.0]
    if look is not None and look.tint is not None:
        out = [
            channel * tint_channel
            for channel, tint_channel in zip(out, hsv_rgb(look.tint[0], look.tint[1], look.tint[2]), strict=True)
        ]
    for key, unchanged in (("COLOR", "HSV;0;100;100"), ("RGBCOLOR", str(WHITE))):
        value = str(props.get(key) or "").strip()
        rgb = files.colour(value) if value and value != unchanged else None
        if rgb is not None:
            out = [channel * rgb_channel / 255 for channel, rgb_channel in zip(out, rgb, strict=True)]
    return None if out == [1.0, 1.0, 1.0] else (out[0], out[1], out[2])


def text_color_of(files: COldGui, props: dict, state: str) -> tuple[int, int, int] | None:
    """The text colour in ``state``: the state's own colour where the widget names one, else ``TEXTCOLOR``."""
    text_settings = text_setting(props)
    for part, key in STATE_TEXT.items():
        if part in state.split("+") and text_settings.get(key) not in (None, ""):
            found = files.colour(text_settings[key])
            if found is not None:
                return found
    return files.colour(text_settings.get("TEXTCOLOR"))


def icon_rect(props: dict, rect, icon: STile) -> tuple[float, float, float, float]:
    """Where a widget's icon is drawn: the whole widget (``ICONSTRETCH``), else its ``ICONSPACE`` from the widget's top
    left (2070 names the fields ``ICONLEFT`` ..., 1404 and 1701 ``LEFT`` ...), at the icon's own size where it has none."""
    x, y, rect_width, rect_height = rect
    if str(props.get("ICONSTRETCH", "0")).strip() == "1":
        return rect
    space = props.get("ICONSPACE") if isinstance(props.get("ICONSPACE"), dict) else {}

    def get(name: str) -> float:
        return number(space.get(f"ICON{name}", space.get(name)), 0.0)

    width, height = get("WIDTH") or float(icon.w), get("HEIGHT") or float(icon.h)
    return x + get("LEFT"), y + get("TOP"), width, height


@dataclass
class SPlacing:
    """What placing one dialog needs besides the widgets: the files, the templates, the components, the state."""

    files: COldGui
    templates: CTemplates
    state: str
    measure: Measure
    components: dict[int, SWidget] = field(default_factory=dict)
    sheet: richtext.CStyleSheet = field(default_factory=richtext.CStyleSheet)
    text_sizes: bool = True  # a label sized to its text takes the size of the text the dialog holds


def estimate(
    text: str, font: SFont | None, width_px: float, style: richtext.SStyle | None = None
) -> tuple[float, float]:
    """A text's size without a font measure: half a font size per character, lines 1.25 font sizes apart."""
    size = (style.size if style is not None else None) or (font.size if font is not None else 14.0)
    lines = richtext.plain(text).split("\n") or [""]
    per = max(1, int(width_px / (size * 0.55))) if width_px > 0 else 0
    count = sum(max(1, math.ceil(len(line) / per)) if per else 1 for line in lines)
    longest = max(len(line) for line in lines) * size * 0.55
    return (min(longest, width_px) if per else longest), count * size * 1.25


def build(files: COldGui, dialog: SWidget, state: str = "Normal", screen: tuple[float, float] = REFERENCE,
          measure: Measure | None = None, text_sizes: bool = True) -> SNode:  # fmt: skip
    """The node tree of a dialog with its widgets in ``state`` (where their background has it), on a screen whose
    visible space is ``screen`` (``SCREENS``); ``measure`` sizes a text in a font (``estimate`` without one).
    ``text_sizes`` off places the dialog as designed: its texts are placeholders (``[SEL_HANDLE_...]`` tags the
    game fills in), so no widget takes its size from one."""
    run = SPlacing(files, files.templates(), state, measure or estimate, files.components(), files.sheet, text_sizes)
    root = _node(run, dialog, None, (0.0, 0.0, 0.0, 0.0))
    root.rect, root.design = _frame_rect(root.props, screen)
    root.visible = True  # the game shows a frame when it opens one: the preview is that moment
    _fill(run, root, dialog)
    return root


def _frame_rect(props: dict, screen: tuple[float, float]):
    """Where a frame sits on the screen, and the size it was designed at: fill and centre by its modes (a stale
    stored size is the reference's), otherwise its stored rectangle with its edges measured from the screen's near
    or far side as its edge anchors say (docs/ui/2070/layout.md "Frames")."""
    x, y, width, height = (float(value) for value in extent(props))
    rect, design = [x, y, width, height], [width, height]
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


def _resolved(run: SPlacing, widget: SWidget) -> tuple[dict, list[SWidget]]:
    """A widget's properties and children; an ``INSTANCE`` takes its component's under its own (its own win), the
    component's children before its own."""
    props = run.templates.effective(widget)
    ref = str(props.get("STRUCTTEMPLATEREF") or "").strip()
    component = run.components.get(int(ref)) if widget.kind == "INSTANCE" and ref.lstrip("-").isdigit() else None
    if component is None or component is widget:
        return props, widget.children
    base = run.templates.effective(component)
    merged = dict(base)
    for k, value in props.items():
        merged[k] = {**base[k], **value} if isinstance(value, dict) and isinstance(base.get(k), dict) else value
    return merged, [*component.children, *widget.children]


def _node(run: SPlacing, widget: SWidget, parent: SNode | None, rect) -> SNode:
    files, state = run.files, run.state
    props, _ = _resolved(run, widget)
    background = props.get("BACKGROUND")
    background = background if isinstance(background, str) and background not in ("(invalid)", "") else ""
    visible = _flag(props.get("VISIBLE"), True) and not _flag(props.get("HIDDEN"), False)
    look = rest_look(files, background, state)
    node = SNode(widget, props, rect, text_of(files, props), background, look, visible)
    node.tint, node.text_color = tint_of(files, props, look), text_color_of(files, props, state)
    node.font = files.fonts.get(str(text_setting(props).get("FONT") or props.get("FONT") or ""))
    node.selector = str(text_setting(props).get("STYLESELECTOR") or props.get("STYLESELECTOR") or "").strip()
    node.style = run.sheet.get(node.selector)
    if node.style is not None and node.style.upper:
        node.text = node.text.upper()
    node.icon = files.icon(props.get("ICONGUID")) if str(props.get("ICONGUID", "-1")).strip() != "-1" else None
    node.design = (float(rect[2]), float(rect[3]))
    node.by_text = run.text_sizes
    if parent is not None:
        parent.children.append(node)
    return node


def client(node: SNode) -> tuple[float, float, float, float]:
    """The rectangle a node's children are placed in: its own, less the background's client area offsets."""
    x, y, width, height = node.rect
    left, top, right, bottom = node.look.offsets if node.look is not None else (0, 0, 0, 0)
    return x + left, y + top, max(0.0, width - left - right), max(0.0, height - top - bottom)


def _fill(run: SPlacing, node: SNode, widget: SWidget, seen: tuple = ()) -> None:
    client_x, client_y, _cw, _ch = client(node)
    grown = (node.rect[2] - node.design[0], node.rect[3] - node.design[1])
    kids = []
    for child in _resolved(run, widget)[1] if widget not in seen else ():  # a component in itself is not followed
        left, top, width, height = extent(_resolved(run, child)[0])
        kid = _node(run, child, node, (client_x + left, client_y + top, float(width), float(height)))
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


def _follow(kid: SNode, grown: tuple[float, float]) -> None:
    """A child's ``LAYOUTMODEH`` / ``V`` when its parent is ``grown`` larger than stored: fill grows by it, centre
    moves by half, right / bottom by all of it."""
    x, y, width, height = kid.rect
    for axis, mode in ((0, "LAYOUTMODEH"), (1, "LAYOUTMODEV")):
        how, growth = str(kid.props.get(mode, "0")).strip(), grown[axis]
        if not growth:
            continue
        if how == "1":
            width, height = (width + growth, height) if axis == 0 else (width, height + growth)
        elif how in ("3", "4"):
            move = growth / 2 if how == "3" else growth
            x, y = (x + move, y) if axis == 0 else (x, y + move)
    kid.rect = (x, y, width, height)


def _resizes(label: SNode) -> str:
    """A label's ``RESIZEPARENTWINDOW`` (1 width, 2 height, 3 both), "" where it takes no size from its text."""
    text_settings = text_setting(label.props)
    how = str(text_settings.get("RESIZEPARENTWINDOW") or label.props.get("RESIZEPARENTWINDOW") or "0").strip()
    return how if how in ("1", "2", "3") and label.visible and label.text and label.by_text else ""


def _size_to_text(run: SPlacing, label: SNode) -> None:
    """A resizing label takes its text's size on its axes (in its font and class), wrapped at its stored width with
    ``ENABLE_LINEBREAK``."""
    how = _resizes(label)
    if not how:
        return
    text_settings = text_setting(label.props)
    wrap = _flag(text_settings.get("ENABLE_LINEBREAK") or label.props.get("ENABLE_LINEBREAK"), False)
    measured = run.measure(label.text, label.font, label.rect[2] if wrap else 0.0, label.style)
    rect = list(label.rect)
    for axis in (0, 1):
        if how in ("3", ("1", "2")[axis]):
            rect[axis + 2] = measured[axis]
    label.rect = (rect[0], rect[1], rect[2], rect[3])


def _resize_parent(node: SNode, label: SNode) -> None:
    """The parent of a label sized to its text follows it, keeping the stored margin at the far edge."""
    how, rect = _resizes(label), list(node.rect)
    for axis in (0, 1) if how else ():
        if how in ("3", ("1", "2")[axis]):
            margin = (node.rect[axis] + node.design[axis]) - (label.rect[axis] + label.design[axis])
            rect[axis + 2] = label.rect[axis] + label.rect[axis + 2] + max(0.0, margin) - rect[axis]
    node.rect = (rect[0], rect[1], rect[2], rect[3])


def _mode(node: SNode, axis: int) -> str:
    return str(node.props.get(("LAYOUTMODEH", "LAYOUTMODEV")[axis], "0")).strip()


def _follow_size(node: SNode, grown: tuple[float, float]) -> None:
    """The children of a widget whose size changed after they were placed (its autosize, a resizing label) follow
    the difference by their modes, as they follow a wider screen; a box places its children itself."""
    if node.kind == "BOXLAYOUT" or not any(grown):
        return
    for kid in node.children:
        for axis in (0, 1):
            how, growth = _mode(kid, axis), grown[axis]
            if not growth or how not in FOLLOWS or _resizes(kid) in ("3", ("1", "2")[axis]):  # it made the change
                continue
            if how == "1":
                x, y, width, height = kid.rect
                kid.rect = (x, y, width + growth, height) if axis == 0 else (x, y, width, height + growth)
                _follow_size(kid, (growth, 0.0) if axis == 0 else (0.0, growth))
            else:
                move = growth / 2 if how == "3" else growth
                _shift(kid, move if axis == 0 else 0.0, move if axis == 1 else 0.0)


def _autosize(node: SNode) -> None:
    """``AUTOSIZE`` on one axis (``AUTOSIZEORIENTATION`` 1 vertical, 2 horizontal): the extent of the visible children
    that do not follow its size, plus the padding, bounded by ``MINSIZE`` / ``MAXSIZE``; ``AUTOSIZEALIGN_*`` 1 keeps
    the far end where it was."""
    node_props = node.props
    axis = {"1": 1, "2": 0}.get(str(node_props.get("AUTOSIZEORIENTATION", "0")).strip())
    # a child that follows the size on this axis does not make it (``_follow_size`` moves it after)
    kids = [k.rect for k in node.children if k.visible and (axis is None or _mode(k, axis) not in FOLLOWS)]
    if str(node_props.get("AUTOSIZE", "0")).strip() != "1" or axis is None or not kids:
        return
    rect = list(node.rect)
    end = "PADDINGRIGHT" if axis == 0 else "PADDINGBOTTOM"  # the children already sit past the client offsets
    size = max(kid_rect[axis] + kid_rect[axis + 2] for kid_rect in kids) - rect[axis] + number(node_props.get(end), 0.0)
    low, high = (_bounds(node_props.get("MINSIZE"))[axis], _bounds(node_props.get("MAXSIZE"))[axis])
    size = max(size, low) if low else size
    size = min(size, high) if high else size
    align = "AUTOSIZEALIGN_HORIZONTAL" if axis == 0 else "AUTOSIZEALIGN_VERTICAL"
    if (
        str(node_props.get(align, "0")).strip() == "1"
    ):  # the far end stays: the widget and what it holds move the other way
        moved = rect[axis + 2] - size
        for kid in node.children:
            _shift(kid, moved if axis == 0 else 0.0, moved if axis == 1 else 0.0)
        rect[axis] += moved
    rect[axis + 2] = size
    node.rect = (rect[0], rect[1], rect[2], rect[3])


def _shift(node: SNode, offset_x: float, offset_y: float) -> None:
    """Move a placed widget and everything it holds (its rectangles are absolute)."""
    for child_node in node.walk():
        x, y, width, height = child_node.rect
        child_node.rect = (x + offset_x, y + offset_y, width, height)
        if child_node.icon_rect is not None:
            icon_x, icon_y, icon_width, icon_height = child_node.icon_rect
            child_node.icon_rect = (icon_x + offset_x, icon_y + offset_y, icon_width, icon_height)


def _bounds(value) -> tuple[float, float]:
    """``"w; h"`` (``MINSIZE``, ``MAXSIZE``), 0 for no bound."""
    parts = [number(part, 0.0) for part in str(value or "").split(";")] + [0.0, 0.0]
    return parts[0], parts[1]


def _zorder(node: SNode) -> int:
    try:
        return int(node.props.get("ZORDER") or 0)
    except (TypeError, ValueError):
        return 0


def _stack(box: SNode) -> None:
    """A box layout places its children itself: line after line (or column after column), ``LINECOLUMNCOUNT`` to a
    line, ``XSPACE`` / ``YSPACE`` apart, from its client area plus ``PADDINGLEFT`` / ``PADDINGTOP``; a hidden child
    leaves no gap."""
    client_x, client_y, _cw, _ch = client(box)
    client_x, client_y = (
        client_x + number(box.props.get("PADDINGLEFT"), 0),
        client_y + number(box.props.get("PADDINGTOP"), 0),
    )
    spacing_x, spacing_y = number(box.props.get("XSPACE"), 5), number(box.props.get("YSPACE"), 5)
    per = max(1, int(number(box.props.get("LINECOLUMNCOUNT"), 1)))
    by_line = (
        str(box.props.get("LINE_ORDER", "1")).strip() == "0"
    )  # 0: lines of ``LINECOLUMNCOUNT``; 1: columns (a row)
    line_size, count, x, y = 0.0, 0, client_x, client_y
    for kid in (k for k in box.children if k.visible):
        width, height = kid.rect[2], kid.rect[3]
        _shift(kid, x - kid.rect[0], y - kid.rect[1])
        count += 1
        if by_line:
            x += width + spacing_x
            line_size = max(line_size, height)
            if count % per == 0:
                x, y, line_size = client_x, y + line_size + spacing_y, 0.0
        else:
            y += height + spacing_y
            line_size = max(line_size, width)
            if count % per == 0:
                x, y, line_size = x + line_size + spacing_x, client_y, 0.0


def tile_rects(look: SLook, width_px: float, height_px: float) -> list[tuple[STile, tuple[float, float, float, float]]]:
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

    def col(i: int) -> list[STile]:
        return [grid_tile for row_index in range(rows) if (grid_tile := grid[row_index * cols + i]) is not None]

    def row(i: int) -> list[STile]:
        return [grid_tile for column_index in range(cols) if (grid_tile := grid[i * cols + column_index]) is not None]

    widths = sizes(
        cols,
        width_px,
        lambda i: max((grid_tile.w for grid_tile in col(i)), default=0),
        lambda i: any(grid_tile.stretch_x for grid_tile in col(i)),
    )
    heights = sizes(
        rows,
        height_px,
        lambda i: max((grid_tile.h for grid_tile in row(i)), default=0),
        lambda i: any(grid_tile.stretch_y for grid_tile in row(i)),
    )
    out, y = [], 0.0
    for row_index in range(rows):
        x = 0.0
        for column_index in range(cols):
            tile = grid[row_index * cols + column_index]
            if tile is not None:
                out.append((tile, (x, y, widths[column_index], heights[row_index])))
            x += widths[column_index]
        y += heights[row_index]
    return out
