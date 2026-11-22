"""2070's rich text as the page draws it: the style sheet's classes, and a text's markup turned into Qt's rich text with
tables, styled spans and icons inline.

``CStyleSheet.read(files)`` reads ``textstyles.css`` (2070 only; empty for 1404 and 1701); ``to_html(text, sheet,
icon)`` turns a text's markup into rich text, ``icon(guid)`` naming the picture an ``<icon>`` is drawn with; ``plain``
is the text without its markup (for measuring). The rules: docs/ui/old-gui.md, "Style classes, tables, icons in texts".
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass

from .gui2070 import decode_text, parse_css

SHEET = "data/config/gui/textstyles.css"
KEPT = {"b", "i", "br", "p", "table", "tr", "td", "th", "colgroup", "col", "u"}  # drawn as Qt draws them
BLOCKS = {"p", "table"}  # kept elements that take a style's margins; a cell takes its padding
CELLS = {"td", "th"}
INLINE = {"b", "i", "u"}  # kept elements that take a style's colour, size and weight
VOID = {"br", "img", "icon", "hr", "col"}  # elements that hold nothing: never an ancestor
SIDES = ("top", "right", "bottom", "left")
EM = 14.0  # an ``em`` where the style sets no font size: the page's stand-in size
NAMED = {"white": (255, 255, 255), "black": (0, 0, 0)}  # the colour names the sheet uses
CELL_ATTRIBUTES = ("width", "colspan", "rowspan", "align", "valign")
_TAG = re.compile(r"<\s*(/?)\s*([A-Za-z][\w-]*)([^>]*?)(/?)\s*>")
_ATTR = re.compile(r"([\w-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_HEX = re.compile(r"#?([0-9a-fA-F]{6})")


@dataclass(frozen=True)
class SStyle:
    """What the page takes from a rule of the sheet (None: the rule does not set it)."""

    color: tuple[int, int, int] | None = None
    size: float | None = None  # reference pixels
    bold: bool | None = None
    italic: bool | None = None
    upper: bool = False
    width: float | None = None  # an icon's size where the class gives one
    height: float | None = None
    block: bool = False  # ``display: block``: drawn as a block of its own
    margin: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # top, right, bottom, left, in pixels
    padding: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    align: str | None = None  # text-align
    line_height: float | None = None  # pixels
    background: tuple[int, int, int] | None = None
    inline: bool = False  # ``display: inline-block``: a box of its own size inside the line
    picture: str | None = None  # ``background-image``: a background file (``.bkg``) behind the box
    share: float | None = None  # a ``width`` in percent, as a share of the width around it

    @property
    def boxed(self) -> bool:
        """Whether it is drawn as a box of its own: an inline block, or anything with a background picture."""
        return self.inline or self.picture is not None

    def box_css(self, cell: bool = False) -> str:
        """The style's box as Qt's rich text takes it: a block's margins with its padding added (nothing is drawn
        between them), a table cell's padding as its padding; the alignment, line height and background."""
        parts = []
        if cell:
            parts += [
                f"padding-{side}:{padding_size:g}px"
                for side, padding_size in zip(SIDES, self.padding, strict=True)
                if padding_size
            ]
        else:
            parts += [
                f"margin-{side}:{margin_size + padding_size:g}px"
                for side, margin_size, padding_size in zip(SIDES, self.margin, self.padding, strict=True)
                if margin_size or padding_size
            ]
        if self.align:
            parts.append(f"text-align:{self.align}")
        if self.line_height is not None:
            parts.append(f"line-height:{self.line_height:g}px")
        if self.background is not None:
            parts.append("background-color:#%02x%02x%02x" % self.background)
        return ";".join(parts)

    def css(self) -> str:
        """The style as Qt's rich text takes it (``style="..."``)."""
        parts = []
        if self.color is not None:
            parts.append("color:#%02x%02x%02x" % self.color)
        if self.size is not None:
            parts.append(f"font-size:{self.size:g}px")
        if self.bold is not None:
            parts.append(f"font-weight:{700 if self.bold else 400}")
        if self.italic is not None:
            parts.append(f"font-style:{'italic' if self.italic else 'normal'}")
        return ";".join(parts)


def _px(value: str | None, em: float | None = None) -> float | None:
    """A length in pixels: ``12px``, ``12``, or (with ``em``, the font size) ``0.6em``."""
    match = re.fullmatch(r"\s*(-?(?:\d+\.?\d*|\.\d+))\s*(px|em)?\s*", value or "")  # a stray "." is no number
    if match is None or (match.group(2) == "em" and em is None):
        return None
    return float(match.group(1)) * (em if match.group(2) == "em" else 1.0)


def _colour(value: str | None) -> tuple[int, int, int] | None:
    text = (value or "").strip()
    match = _HEX.fullmatch(text)
    if match is None:
        return NAMED.get(text.lower())
    return tuple(int(match.group(1)[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _picture(value: str | None) -> str | None:
    """A ``url("data\\...")``'s game path."""
    match = re.fullmatch(r"\s*url\(\s*[\"']?([^\"')]+)[\"']?\s*\)\s*", value or "")
    return re.sub(r"[\\/]+", "/", match.group(1)).lower() if match else None


def _share(value: str | None) -> float | None:
    match = re.fullmatch(r"\s*(\d+\.?\d*|\.\d+)\s*%\s*", value or "")
    return float(match.group(1)) / 100.0 if match else None


def _sides(decl: dict[str, str], name: str, em: float) -> tuple[float, float, float, float]:
    return tuple(_px(decl.get(f"{name}-{side}"), em) or 0.0 for side in SIDES)  # type: ignore[return-value]


def style_of(decl: dict[str, str]) -> SStyle:
    """One rule's declarations: a gradient (``gradient-color``) as the mean of its two colours."""
    color, gradient = _colour(decl.get("color")), _colour(decl.get("gradient-color"))
    if color is not None and gradient is not None:
        color = tuple(
            (channel + gradient_channel) // 2 for channel, gradient_channel in zip(color, gradient, strict=True)
        )  # type: ignore[assignment]
    weight = (decl.get("font-weight") or "").strip()
    bold = None if not weight else weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600)
    style, size = (decl.get("font-style") or "").strip(), _px(decl.get("font-size"))
    em = size or EM
    align, display = (decl.get("text-align") or "").strip() or None, (decl.get("display") or "").strip()
    return SStyle(color, size, bold, None if not style else style == "italic",
                 (decl.get("text-transform") or "").strip() == "uppercase", _px(decl.get("width"), em),
                 _px(decl.get("height"), em), display == "block", _sides(decl, "margin", em),
                 _sides(decl, "padding", em), align, _px(decl.get("line-height"), em),
                 _colour(decl.get("background-color")), display == "inline-block",
                 _picture(decl.get("background-image")), _share(decl.get("width")))  # fmt: skip


Step = tuple[str, tuple[str, ...]]  # an element of a text, or a part of a selector: its name ("" any) and classes


@dataclass(frozen=True)
class SRule:
    """A rule of the sheet: its selector's parts (the element it styles last), its declarations, its place."""

    steps: tuple[Step, ...]
    decl: tuple[tuple[str, str], ...]
    order: int

    @property
    def weight(self) -> tuple[int, int, int]:
        """CSS's order: more classes, then more elements, then the later rule win."""
        return sum(len(classes) for _, classes in self.steps), sum(bool(tag) for tag, _ in self.steps), self.order

    def matches(self, element: Step, ancestors: tuple[Step, ...]) -> bool:
        """Whether it styles ``element`` inside ``ancestors`` (outermost first): its last part the element, each
        earlier part an ancestor further out."""
        if not _fits(self.steps[-1], element):
            return False
        position = len(ancestors)
        for step in reversed(self.steps[:-1]):
            position = next((k for k in range(position - 1, -1, -1) if _fits(step, ancestors[k])), -1)
            if position < 0:
                return False
        return True


def _fits(step: Step, element: Step) -> bool:
    return (not step[0] or step[0] == element[0]) and set(step[1]) <= set(element[1])


def selector_steps(selector: str) -> tuple[Step, ...] | None:
    """``td.cell .note b`` as its parts, None for one the page does not read (a state such as ``:hover``)."""
    steps = []
    for part in selector.lower().split():
        match = re.fullmatch(r"([a-z][\w-]*)?((?:\.[a-z][\w-]*)*)", part)
        if match is None or not part:
            return None
        steps.append(
            (match.group(1) or "", tuple(class_name for class_name in match.group(2).split(".") if class_name))
        )
    return tuple(steps) or None


def classes_of(attrs: dict) -> tuple[str, ...]:
    """An element's classes (``class="a b"``), lower case."""
    return tuple((attrs.get("class") or "").lower().split())


class CStyleSheet:
    """The sheet's styles by name (``styles``: a class without its dot, an element name as it is, lower case) and all
    its rules (``rules``), which ``match`` applies to an element inside its ancestors."""

    def __init__(self, styles: dict[str, SStyle] | None = None, rules: list[SRule] | None = None) -> None:
        self.m_styles, self.m_rules = styles or {}, rules or []
        self._m_matched: dict[tuple, SStyle | None] = {}

    @classmethod
    def parse(cls, text: str) -> CStyleSheet:
        styles: dict[str, SStyle] = {}
        rules: list[SRule] = []
        for selectors, decl in parse_css(text):
            for sel in selectors:
                steps = selector_steps(sel)
                if steps is None:
                    continue
                rules.append(SRule(steps, tuple(decl.items()), len(rules)))
                if re.fullmatch(r"\.?[A-Za-z][\w-]*", sel):  # one class or one element
                    styles[sel.lstrip(".").lower()] = style_of(decl)
        return cls(styles, rules)

    @classmethod
    def read(cls, tree) -> CStyleSheet:
        return cls.parse(decode_text(tree.read(SHEET))) if tree.exists(SHEET) else cls()

    def get(self, name: str | None) -> SStyle | None:
        return self.m_styles.get(name.strip().lower()) if name else None

    def match(self, element: Step, ancestors: tuple[Step, ...] = ()) -> SStyle | None:
        """The style of ``element`` inside ``ancestors``: every rule that styles it, merged in CSS's order; None for
        none (a sheet built from ``styles`` alone looks its classes, then its name, up)."""
        key = (element, ancestors)
        if key not in self._m_matched:
            if not self.m_rules:
                found = next((self.get(class_name) for class_name in element[1] if self.get(class_name)), None)
                self._m_matched[key] = found or self.get(element[0])
            else:
                hits = sorted(
                    (rule for rule in self.m_rules if rule.matches(element, ancestors)), key=lambda rule: rule.weight
                )
                self._m_matched[key] = style_of({k: value for rule in hits for k, value in rule.decl}) if hits else None
        return self._m_matched[key]


def attributes(text: str) -> dict[str, str]:
    return {k.lower(): value.strip("\"'") for k, value in _ATTR.findall(text)}


Picture = tuple[str, float, float]  # a resource name, its width and height


Frame = tuple[str | None, tuple[float, float, float, float], list[str | None]]  # a table: picture, padding, cells'
Icon = Callable[[dict, SStyle | None], Picture | None]
Box = Callable[[SStyle, str, tuple[Step, ...]], Picture | None]


def to_html(text: str, sheet: CStyleSheet, icon: Icon, box: Box | None = None, frames: list[Frame] | None = None,
            around: tuple[Step, ...] = ()) -> str:  # fmt: skip
    """A text's markup as Qt's rich text. ``icon(attributes, style)`` gives (resource name, width, height) of an
    ``<icon>``'s or an ``<img>``'s picture, None for one not found (it is left out); ``box(style, markup, ancestors)``
    the picture of an element drawn as a box of its own (``SStyle.boxed``), None to draw it inline as Qt can;
    ``frames`` gets the text's tables in the order Qt lays them out (``Frame``); ``around``: the elements the text
    sits in (a label's class), outermost first."""
    run = _CMarkup(sheet, icon, box, frames, list(around))
    parts, i = re.split(r"(<[^<>]*>)", text), 0
    while i < len(parts):
        i = run.part(parts, i)
    return "".join(run.m_out + [f"</{drawn}>" for _, drawn in reversed(run.m_opened)])


class _CMarkup:
    """One text being turned into rich text: what is written, the elements open, the tables open."""

    def __init__(self, sheet: CStyleSheet, icon: Icon, box: Box | None, frames: list | None, path: list) -> None:
        self.m_sheet, self.m_icon, self.m_box, self.m_frames, self.m_path = sheet, icon, box, frames, path
        self.m_out: list[str] = []
        self.m_opened: list[tuple[str, str]] = []  # (element, the tag it was drawn as)
        self.m_tables: list[list] = []  # the cell lists of the tables open (``frames``)

    def part(self, parts: list[str], i: int) -> int:
        """Write part ``i`` (a piece of text or a tag); the index of the next part."""
        tag = _TAG.fullmatch(parts[i])
        if tag is None:
            self.m_out.append(html.escape(parts[i]).replace("\n", "<br>"))
            return i + 1
        closing, name, attrs, empty = tag.group(1), tag.group(2).lower(), attributes(tag.group(3)), tag.group(4)
        if closing:
            self._close(name)
            return i + 1
        element = (name, classes_of(attrs))
        style = self.m_sheet.match(element, tuple(self.m_path))
        holds = not empty and name not in VOID
        if name in ("icon", "img"):
            found = self.m_icon(attrs, style)
            if found is not None:
                size = f' width="{found[1]:g}" height="{found[2]:g}"' if found[1] and found[2] else ""
                self.m_out.append(f'<img src="{html.escape(found[0])}"{size}>')
            return i + 1
        if name in KEPT:
            self.m_out.append(self._kept(name, attrs, style, empty))
        elif name == "font" and _colour(attrs.get("color")) is not None:
            self._open(element, "span", SStyle(_colour(attrs.get("color"))).css())
            holds = False
        elif style is not None and (holds or name == "hr"):
            end = _closing(parts, i, name) if holds else i
            if style.boxed and self.m_box is not None and end is not None:
                found = self.m_box(style, "".join(parts[i + 1 : end]), (*self.m_path, element))
                if found is not None:
                    self.m_out.append(_boxed(style, found))
                    return end + 1
            if holds:
                css = ";".join(x for x in (style.box_css() if style.block else "", style.css()) if x)
                self._open(element, "div" if style.block else "span", css)
                holds = False
        if holds:
            self.m_path.append(element)
        return i + 1

    def _open(self, element: Step, drawn: str, css: str) -> None:
        self.m_out.append(f'<{drawn} style="{css}">')
        self.m_opened.append((element[0], drawn))
        self.m_path.append(element)

    def _close(self, name: str) -> None:
        """A closing tag: Qt's own element closed, a drawn span or block closed, the element no longer an ancestor."""
        if name == "br":
            self.m_out.append("<br>")
        elif name in KEPT:
            self.m_out.append(f"</{name}>")
            if name == "table" and self.m_tables:
                self.m_tables.pop()
        elif self.m_opened and self.m_opened[-1][0] == name:
            self.m_out.append(f"</{self.m_opened.pop()[1]}>")
        index = next((k for k in range(len(self.m_path) - 1, -1, -1) if self.m_path[k][0] == name), None)
        if index is not None:
            del self.m_path[index:]

    def _kept(self, name: str, attrs: dict, style: SStyle | None, empty: str) -> str:
        """An element Qt draws itself, with its cell attributes, (a block, a cell) the box of its style, (a table, a
        cell) its width from the sheet, (b, i, u) its style's font; a table and a cell are noted in ``frames``."""
        if name == "br":
            return "<br>"
        kept = "".join(f' {k}="{html.escape(value)}"' for k, value in attrs.items() if k in CELL_ATTRIBUTES)
        if style is not None and name in BLOCKS | CELLS | INLINE:
            css = ";".join(x for x in (style.box_css(cell=name in CELLS) if name not in INLINE else "", style.css())
                           if x)  # fmt: skip
            kept += f' style="{css}"' if css else ""
            if name in ("table", *CELLS) and "width" not in attrs and (style.width or style.share):
                outside = sum(style.margin[1::2]) + sum(style.padding[1::2]) if name == "table" else 0.0
                kept += f' width="{style.width + outside:g}"' if style.width else f' width="{style.share * 100:g}%"'
        if self.m_frames is not None and name == "table":
            self.m_frames.append((style.picture if style else None, style.padding if style else (0.0,) * 4, []))
            self.m_tables.append(self.m_frames[-1][2])
        elif self.m_tables and name in CELLS:
            self.m_tables[-1].append(style.picture if style else None)
        return f"<{name}{kept}{'/' if empty else ''}>"


def _boxed(style: SStyle, found: Picture) -> str:
    """A box drawn as its picture: inside the line, or (a block) on a line of its own with its margins."""
    picture = f'<img src="{html.escape(found[0])}" width="{found[1]:g}" height="{found[2]:g}">'
    if not style.block:
        return picture
    margins = ";".join(
        f"margin-{side}:{margin:g}px" for side, margin in zip(SIDES, style.margin, strict=True) if margin
    )
    return f'<div style="{margins}">{picture}</div>'


def _closing(parts: list[str], start: int, name: str) -> int | None:
    """The index of the part that closes the element opened at ``at`` (the same element nested inside counted)."""
    depth = 0
    for j in range(start + 1, len(parts)):
        tag = _TAG.fullmatch(parts[j])
        if tag is None or tag.group(2).lower() != name or tag.group(4):
            continue
        if not tag.group(1):
            depth += 1
        elif depth:
            depth -= 1
        else:
            return j
    return None


def icon_guids(text: str) -> list[str]:
    """The GUIDs of the ``<icon>`` elements of a text."""
    return [attributes(match.group(1)).get("guid", "") for match in re.finditer(r"<\s*icon\b([^>]*)>", text, re.I)]


def picture_paths(text: str) -> list[str]:
    """The pictures the ``<img>`` elements of a text name (a ``[TAG]`` the game fills is none)."""
    found = (attributes(match.group(1)).get("src", "") for match in re.finditer(r"<\s*img\b([^>]*)>", text, re.I))
    return [source for source in found if source and "[" not in source]


def box_pictures(text: str, sheet: CStyleSheet, around: tuple[Step, ...] = ()) -> list[str]:
    """The background pictures a text draws: of its boxes, tables and cells (``background-image``), as ``to_html``
    styles them."""
    found: list[str] = []

    def box(style: SStyle, inner: str, path: tuple[Step, ...]) -> None:
        found.append(style.picture or "")
        found.extend(box_pictures(inner, sheet, path))

    frames: list[Frame] = []
    to_html(text, sheet, lambda _a, _s: None, box, frames, around)
    found += [picture_path or "" for picture, _pad, cells in frames for picture_path in (picture, *cells)]
    return list(dict.fromkeys(picture_path for picture_path in found if picture_path))


def plain(text: str) -> str:
    """The text without its markup (a break as a new line), for measuring."""
    return html.unescape(re.sub(r"<[^<>]*>", "", re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.I)))
