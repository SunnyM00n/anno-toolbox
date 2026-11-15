"""The game's own fonts for the preview, and a label's text laid out as the label's fields ask: a text style's
localized font followed to its font file (``data/fonts/...``) and loaded into Qt, then lines broken at the text
plane's width, cut with an ellipsis or by character, spaced by the style's leading and letter spacing.

``GameFonts(files)`` resolves a localized font (``family``); ``lay_out(text, style, width)`` gives the lines and
their size. docs/ui/fonts.md is the font chain, docs/ui/117/layout.md "Labels" the overflow modes.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF

from ..formats import phoenixedit
from . import fields
from .labels import TextStyle

FALLBACK = "Georgia"  # a system serif where the game's font cannot be read
ELLIPSIS = "…"


@dataclass(frozen=True)
class FontFace:
    family: str
    scale: float = 1.0  # the localized font's ScalingFactor
    loaded: bool = False  # the game's own outlines, not a stand-in


class GameFonts:
    """The localized fonts of a game's UI files, loaded on first use; ``family(guid, language)`` is the entry."""

    def __init__(self, files) -> None:
        self.files = files
        self._faces: dict[tuple[str, str], FontFace] = {}
        self._by_path: dict[str, str | None] = {}

    def _load(self, path: str) -> str | None:
        """The family of a font file of the game, loaded once; None when it cannot be read."""
        path = path.replace("\\", "/").lower()
        if path not in self._by_path:
            family = None
            try:
                data = self.files.tree.read(path)
            except (OSError, KeyError):
                data = None
            if data:
                fid = QFontDatabase.addApplicationFontFromData(QByteArray(data))
                families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
                family = families[0] if families else None
            self._by_path[path] = family
        return self._by_path[path]

    def family(self, guid: str | None, language: str = "english") -> FontFace:
        """The face a localized font (a ``LocalizedFont`` GUID) draws with in ``language``."""
        key = (guid or "", language)
        if key not in self._faces:
            self._faces[key] = self._resolve(guid, language)
        return self._faces[key]

    def lookup(self, guid: str | None, language: str = "english") -> tuple[str | None, float]:
        """(family, scale) for ``layout.Context.fonts``; the family is None where the game's font is not read."""
        face = self.family(guid, language)
        return (face.family if face.loaded else None), face.scale

    def stand_in(self) -> FontFace:
        """The first font file of the game's font assets that loads: drawn where a style's font cannot be read
        (a system font may be missing, offscreen above all)."""
        if ("", "*") not in self._faces:
            family = None
            for entry in self.files.of_kind("FT"):
                ft = self.files.root(entry.key)
                path = fields.get(ft, "FontId") if ft is not None else None
                family = self._load(path) if path else None
                if family:
                    break
            self._faces[("", "*")] = FontFace(family or FALLBACK, 1.0, family is not None)
        return self._faces[("", "*")]

    def _resolve(self, guid: str | None, language: str) -> FontFace:
        lf = self.files.root(guid) if guid else None
        if lf is None:
            return self.stand_in()
        font, scale = fields.get(lf, "DefaultFont"), float(fields.get(lf, "ScalingFactor", 1.0) or 1.0)
        for exc in phoenixedit.objects(lf, "LanguagesExceptions"):
            if str(fields.get(exc, "Language", "")).lower() == language.lower():
                font = fields.get(exc, "FontException") or font
                scale = float(fields.get(exc, "ScalingFactor", scale) or scale)
        ft = self.files.root(font) if font else None
        path = fields.get(ft, "FontId") if ft is not None else None
        family = self._load(path) if path else None
        return FontFace(family, scale, True) if family else self.stand_in()


def qfont(style: TextStyle) -> QFont:
    """The Qt font of a style: its family (the game's, else a stand-in), pixel size, weight, slant and spacing."""
    font = QFont(style.family or FALLBACK)
    font.setStyleHint(QFont.StyleHint.Serif)
    font.setPixelSize(max(1, round(style.size)))
    font.setBold(style.bold)
    font.setItalic(style.italic)
    if style.letter_spacing:  # thousandths of an em, as the studio's tracking (inferred from the values 0 to 50)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, style.size * style.letter_spacing / 1000.0)
    return font


@dataclass
class Lines:
    lines: list[str]
    width: float  # the widest line
    height: float
    line_height: float


def _wrap(text: str, metrics: QFontMetricsF, width: float) -> list[str]:
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}" if line else word
            if line and metrics.horizontalAdvance(trial) > width:
                out.append(line)
                line = word
            else:
                line = trial
        out.append(line)
    return out


def _cut(line: str, metrics: QFontMetricsF, width: float, ellipsis: bool) -> str:
    if metrics.horizontalAdvance(line) <= width:
        return line
    tail = ELLIPSIS if ellipsis else ""
    while line and metrics.horizontalAdvance(line + tail) > width:
        line = line[:-1]
    return line + tail


def lay_out(text: str, style: TextStyle, width: float, height: float = 0.0) -> Lines:
    """``text`` broken and cut as the label's overflow modes say inside a plane of ``width`` (and ``height``)."""
    metrics = QFontMetricsF(qfont(style))
    line_height = style.size * (style.leading or 1.2)
    if style.wrap and width > 0:
        lines = _wrap(text, metrics, width)
    else:
        lines = text.split("\n")
    if width > 0 and style.cut in ("character", "ellipsis"):
        lines = [_cut(line, metrics, width, style.cut == "ellipsis") for line in lines]
    if height > 0 and style.vcut and len(lines) * line_height > height + 0.5:
        keep = max(1, int(height // line_height))
        lines = lines[:keep]
        if style.vcut == "ellipsis":
            lines[-1] = _cut(lines[-1] + ELLIPSIS, metrics, width or 1e9, True)
    widest = max((metrics.horizontalAdvance(line) for line in lines), default=0.0)
    return Lines(lines, widest, line_height * max(1, len(lines)), line_height)
