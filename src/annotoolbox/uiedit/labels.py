"""A label of the preview: its text (one held from the running game, a binding's, the stored one, the localized one), its style
from the label and its text style file, its size by its sizing mode.

``label(ctx, node, overrides)`` is the entry (``ctx`` is the preview's ``layout.Context``); the rules are
docs/ui/117/layout.md ("Labels") and docs/ui-editor.md ("The preview", text).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import elements, fields

TEXT = 0x80000008  # a label's Text, the built-in a binding writes
PLANE = 0x8000000F  # its TextPlane
MODE_IDS = {  # the built-ins a binding writes a label's layout modes through
    "HorizontalSizing": 0x80000055,
    "VerticalSizing": 0x80000056,
    "AlignmentX": 0x80000061,
    "AlignmentY": 0x80000062,
    "HorizontalOverflow": 0x80000063,
    "VerticalOverflow": 0x80000064,
}
OUTLINE_SIZE = 0.04  # the fonts' OutlineSize in font sizes (all shipped fonts)
NARROW = 2.0  # a wrap width below this many font sizes is a run-time width the preview lacks


@dataclass
class TextStyle:
    size: float = 24.0
    color: tuple[int, int, int] = (255, 255, 255)
    bold: bool = False
    italic: bool = False
    shadow: tuple | None = None  # (rgb, alpha, distance, angle)
    outline: tuple | None = None  # (rgb, alpha)
    align: int = 0  # 0 left, 1 centre, 2 right, 3 justify
    wrap: bool = False
    clip: bool = False
    font: str | None = None  # the localized font's GUID
    family: str | None = None  # the Qt family drawing it (fonts.GameFonts), None: a stand-in
    leading: float = 1.2  # the step from one line to the next, in font sizes
    outline_size: float = 0.0  # in font sizes
    cut: str = ""  # past the plane's width: "", "character" or "ellipsis"
    vcut: str = ""  # past its height: "", "line" or "ellipsis"
    valign: int = 0  # AlignmentY: 0 top, 1 centre, 2 baseline, 3 bottom
    plane: tuple[float, float] = (0.0, 0.0)  # the rectangle the text aligns in, the label's own units
    origin: tuple[float, float] = (0.0, 0.0)  # where the label's box starts in the plane (``box_origin``)
    shown: tuple[float, float] = (0.0, 0.0)  # the shown text's size, the label's own units (its DisplayedSize)


def label(ctx, node: dict, overrides: dict) -> tuple[str, TextStyle, tuple[float, float]]:
    """A label's text, style and box: per axis the plane's size, the shown text's, the larger or the smaller, as its
    sizing mode says (``sized``); ``style.plane`` is the rectangle the text aligns in, ``style.origin`` where the box
    starts in it."""
    text, style = label_text(ctx, node, overrides), text_style(ctx, node, overrides)
    width, height = _pair(overrides.get((PLANE,), fields.get(node, "TextPlane")))
    if style.wrap and 0 < width < NARROW * style.size and ctx.design:
        style.wrap, width = False, 0.0  # the game binds the width at run time; one word a line helps no one
    w, h = ctx.text_size(text, style, width)
    if ctx.design:  # a plane of 0 is set at run time (a binding); the design view takes the shown text's size
        width, height = width or w, height or h
    style.plane, style.shown = (width, height), (w, h)
    modes = (mode(node, overrides, "HorizontalSizing"), mode(node, overrides, "VerticalSizing"))
    style.origin = box_origin(style, modes, (w, h))
    return text, style, (sized(modes[0], width, w), sized(modes[1], height, h))


def box_origin(style: TextStyle, modes: tuple, shown: tuple[float, float]) -> tuple[float, float]:
    """Where a label's box starts in its plane: on an axis whose box is the shown text's (``sized``) at the place
    the text is aligned to, else at the plane's origin (layout.md "Labels"). A
    baseline alignment needs the font's ascent, which is not known here: such a box stays at the origin."""
    free = (style.plane[0] - shown[0], style.plane[1] - shown[1])
    at = ({1: free[0] / 2, 2: free[0]}.get(style.align, 0.0), {1: free[1] / 2, 3: free[1]}.get(style.valign, 0.0))
    texts = (sized(m, p, s) == s and s != p for m, p, s in zip(modes, style.plane, shown, strict=True))
    return tuple(a if text else 0.0 for a, text in zip(at, texts, strict=True))


def sized(mode, plane: float, shown: float) -> float:
    """One axis of a label's box by its sizing mode (``TextBoxSizing``): 0 the plane, 1 the shown text, 2 the larger,
    3 the smaller (layout.md "Labels")."""
    return {1: shown, 2: max(plane, shown), 3: min(plane, shown)}.get(int(mode or 0), plane)


def text_top(valign: int, plane: float, height: float, ascent: float, first: float) -> float:
    """Where a text of ``height`` starts in a plane of ``plane`` by its ``AlignmentY``: 0 top, 1 centred, 2 the last
    line's baseline on the plane's bottom edge, 3 bottom; ``first`` is the height of the first line (the font's own),
    all in the label's own units (layout.md "The text probe")."""
    if valign == 1:
        return (plane - height) / 2
    if valign == 2:
        return plane - (height - first) - ascent
    return plane - height if valign == 3 else 0.0


def _pair(value) -> tuple[float, float]:
    if isinstance(value, (tuple, list)) and len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2]):
        return float(value[0]), float(value[1])
    return 0.0, 0.0


def label_text(ctx, node: dict, overrides: dict) -> str:
    live = elements.held_for(ctx.live_text, elements.Element(node, "label"))
    if live is not None:  # the game's own text, held by an optional panel, in place of the sample value
        return live
    text = overrides.get((TEXT,))
    if text is None:
        text = fields.get(node, "Text") or ""
    if not text:
        loca = overrides.get((0x8000000A,)) or fields.get(node, "LocalizationId") or ""
        loca = loca if isinstance(loca, str) else ""
        text = (ctx.files.text_of(loca, ctx.language) or f"[{loca}]") if loca else ""
    return str(text)


def mode(node: dict, overrides: dict | None, name: str) -> int:
    """One of a label's layout modes (alignment, sizing, overflow): what a binding writes, else what the label stores
    (the text files bind them all from what the placing file sets: layout.md "Labels"); a value outside the mode's
    entries is not one the game would write, the stored one stays."""
    stored = int(fields.get(node, name, 0) or 0)
    value = (overrides or {}).get((MODE_IDS[name],))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < len(fields.ENUMS[name]):
        return stored
    return int(value)


def text_style(ctx, node: dict, overrides: dict | None = None) -> TextStyle:
    style = TextStyle()
    ts = ctx.root(fields.get(node, "TextStyle"))
    if ts is not None:
        style.size = float(fields.get(ts, "Size", 24.0) or 24.0)
        style.color = ctx.tint(ts, "BaseColor")
        style.bold, style.italic = bool(fields.get(ts, "Bold")), bool(fields.get(ts, "Italic"))
        style.font = fields.get(ts, "LocalizedFont")
        style.leading = float(fields.get(ts, "Leading", 1.2) or 1.2)
        if ctx.fonts is not None:
            style.family, factor = ctx.fonts(style.font, ctx.language)
            style.size *= factor
        if fields.get(ts, "ShadowEnabled"):
            style.shadow = (ctx.tint(ts, "ShadowColor"), float(fields.get(ts, "ShadowAlpha", 1.0)),
                            float(fields.get(ts, "ShadowDistance", 2.0)), float(fields.get(ts, "ShadowAngle", 45.0)))  # fmt: skip
        if fields.get(ts, "OutlineEnabled"):
            style.outline = (ctx.tint(ts, "OutlineColor"), float(fields.get(ts, "OutlineAlpha", 1.0)))
            style.outline_size = OUTLINE_SIZE
    style.align = fields.alignment_index(mode(node, overrides, "AlignmentX"))
    style.valign = mode(node, overrides, "AlignmentY")
    overflow = mode(node, overrides, "HorizontalOverflow")
    style.wrap, style.clip = overflow == 1, overflow in (2, 3, 4)
    style.cut = {3: "character", 4: "ellipsis"}.get(overflow, "")
    # 117 cuts to whole lines in all three modes; its LineSpacing and LetterSpacing change no size (layout.md)
    style.vcut = {1: "ellipsis", 2: "line", 3: "line"}.get(mode(node, overrides, "VerticalOverflow"), "")
    if fields.get(node, "VerticalSizing") is None:  # an older game's label: its line spacing is still a factor
        style.leading *= float(fields.get(node, "LineSpacing", 1.0) or 1.0)
    return style
