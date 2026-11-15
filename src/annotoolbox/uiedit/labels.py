"""A label of the preview: its text (the running game's, a binding's, the stored one, the localized one), its style
from the label and its text style file, its size by its sizing mode.

``label(ctx, node, overrides)`` is the entry (``ctx`` is the preview's ``layout.Context``); the rules are
docs/ui/117/layout.md ("Labels") and docs/ui-editor.md ("The preview", text).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from . import fields

TEXT = 0x80000008  # a label's Text, the built-in a binding writes
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
    leading: float = 1.2  # line height in font sizes
    letter_spacing: float = 0.0  # thousandths of an em
    outline_size: float = 0.0  # in font sizes
    cut: str = ""  # past the plane's width: "", "character" or "ellipsis"
    vcut: str = ""  # past its height: "", "line" or "ellipsis"


def label(ctx, node: dict, overrides: dict) -> tuple[str, TextStyle, tuple[float, float]]:
    """A label's text, style and size: the plane's width or the shown text's, as its sizing mode says."""
    plane = overrides.get((0x8000000F,), fields.get(node, "TextPlane", (0.0, 0.0)))
    text, style = label_text(ctx, node, overrides), text_style(ctx, node)
    first = plane[0] if isinstance(plane, (tuple, list)) and plane else None
    width = float(first) if isinstance(first, (int, float)) else 0.0
    if style.wrap and 0 < width < NARROW * style.size and ctx.design:
        style.wrap, width = False, 0.0  # the game binds the width at run time; one word a line helps no one
    w, h = ctx.text_size(text, style, width)
    mode = int(fields.get(node, "HorizontalSizing", 0) or 0)
    shown = {1: w, 2: max(w, width), 3: min(w, width) if width else w, 4: w}.get(mode, width or w)
    return text, style, (shown or w, h)


def label_text(ctx, node: dict, overrides: dict) -> str:
    live = ctx.live_text.get(str(uuid.UUID(bytes_le=node["guid"]))) if node.get("guid") else None
    if live is not None:  # the game's own text, held from a live dump, in place of the sample value
        return live
    text = overrides.get((TEXT,))
    if text is None:
        text = fields.get(node, "Text") or ""
    if not text:
        loca = overrides.get((0x8000000A,)) or fields.get(node, "LocalizationId") or ""
        loca = loca if isinstance(loca, str) else ""
        text = (ctx.files.text_of(loca, ctx.language) or f"[{loca}]") if loca else ""
    return str(text)


def text_style(ctx, node: dict) -> TextStyle:
    style = TextStyle()
    ts = ctx.root(fields.get(node, "TextStyle"))
    if ts is not None:
        style.size = float(fields.get(ts, "Size", 24.0) or 24.0)
        style.color = ctx.tint(ts, "BaseColor")
        style.bold, style.italic = bool(fields.get(ts, "Bold")), bool(fields.get(ts, "Italic"))
        style.font = fields.get(ts, "LocalizedFont")
        style.leading = float(fields.get(ts, "Leading", 1.2) or 1.2)
        style.letter_spacing = float(fields.get(ts, "LetterSpacing", 0.0) or 0.0)
        if ctx.fonts is not None:
            style.family, factor = ctx.fonts(style.font, ctx.language)
            style.size *= factor
        if fields.get(ts, "ShadowEnabled"):
            style.shadow = (ctx.tint(ts, "ShadowColor"), float(fields.get(ts, "ShadowAlpha", 1.0)),
                            float(fields.get(ts, "ShadowDistance", 2.0)), float(fields.get(ts, "ShadowAngle", 45.0)))  # fmt: skip
        if fields.get(ts, "OutlineEnabled"):
            style.outline = (ctx.tint(ts, "OutlineColor"), float(fields.get(ts, "OutlineAlpha", 1.0)))
            style.outline_size = OUTLINE_SIZE
    style.align = fields.alignment_index(fields.get(node, "AlignmentX", 0))
    overflow = int(fields.get(node, "HorizontalOverflow", 0) or 0)
    style.wrap, style.clip = overflow == 1, overflow in (2, 3, 4)
    style.cut = {3: "character", 4: "ellipsis"}.get(overflow, "")
    style.vcut = {1: "ellipsis", 3: "line"}.get(int(fields.get(node, "VerticalOverflow", 0) or 0), "")
    style.letter_spacing += float(fields.get(node, "LetterSpacing", 0.0) or 0.0)
    style.leading += float(fields.get(node, "LineSpacing", 0.0) or 0.0) / max(style.size, 1.0)
    return style
