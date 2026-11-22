"""The game's own fonts for the preview, and a label's text laid out as the label's fields ask: a text style's
localized font followed to its font file (``data/fonts/...``) and loaded into Qt, then lines broken at the text
plane's width, cut with an ellipsis or by character, spaced by the style's leading.

``CGameFonts(files)`` resolves a localized font (``family``); ``lay_out(text, style, width)`` gives the lines and
their size. docs/ui/fonts.md is the font chain, docs/ui/117/layout.md "Labels" the overflow modes.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QFont, QFontMetricsF

from ..formats import phoenixedit
from ..suite.gamefonts import CFontRegistry
from . import fields, gamelanguages
from .labels import STextStyle

FALLBACK = "Georgia"  # a system serif where the game's font cannot be read
ELLIPSIS = "…"


@dataclass(frozen=True)
class SFontFace:
    family: str
    scale: float = 1.0  # the localized font's ScalingFactor
    loaded: bool = False  # the game's own outlines, not a stand-in


class CGameFonts:
    """The localized fonts of a game's UI files, loaded on first use; ``family(guid, language)`` is the entry."""

    def __init__(self, files) -> None:
        self.m_files = files
        self.m_registry = CFontRegistry(files.m_tree.read)
        self._m_faces: dict[tuple[str, str], SFontFace] = {}

    def _load(self, path: str) -> str | None:
        """The family of a font file of the game, loaded once; None when it cannot be read."""
        return self.m_registry.face(path).family

    def notes(self) -> list[str]:
        """Why a font is drawn in a stand-in, one line each (for the Checks list)."""
        return self.m_registry.notes()

    def family(self, guid: str | None, language: str = "english") -> SFontFace:
        """The face a localized font (a ``LocalizedFont`` GUID) draws with in ``language``."""
        key = (guid or "", language)
        if key not in self._m_faces:
            self._m_faces[key] = self._resolve(guid, language)
        return self._m_faces[key]

    def lookup(self, guid: str | None, language: str = "english") -> tuple[str | None, float]:
        """(family, scale) for ``layout.Context.fonts``; the family is None where the game's font is not read."""
        face = self.family(guid, language)
        return (face.family if face.loaded else None), face.scale

    def stand_in(self) -> SFontFace:
        """The first font file of the game's font assets that loads: drawn where a style's font cannot be read
        (a system font may be missing, offscreen above all)."""
        if ("", "*") not in self._m_faces:
            family = None
            for entry in self.m_files.of_kind("FT"):
                font_asset = self.m_files.root(entry.key)
                path = fields.get(font_asset, "FontId") if font_asset is not None else None
                family = self._load(path) if path else None
                if family:
                    break
            self._m_faces[("", "*")] = SFontFace(family or FALLBACK, 1.0, family is not None)
        return self._m_faces[("", "*")]

    def _resolve(self, guid: str | None, language: str) -> SFontFace:
        language_font = self.m_files.root(guid) if guid else None
        if language_font is None:
            return self.stand_in()
        font, scale = (
            fields.get(language_font, "DefaultFont"),
            float(fields.get(language_font, "ScalingFactor", 1.0) or 1.0),
        )
        for exc in phoenixedit.objects(language_font, "LanguagesExceptions"):
            if gamelanguages.same(language, str(fields.get(exc, "Language", ""))):  # "Simplified Chinese"
                font = fields.get(exc, "FontException") or font
                scale = float(fields.get(exc, "ScalingFactor", scale) or scale)
        font_asset = self.m_files.root(font) if font else None
        path = fields.get(font_asset, "FontId") if font_asset is not None else None
        family = self._load(path) if path else None
        return SFontFace(family, scale, True) if family else self.stand_in()


def qfont(style: STextStyle) -> QFont:
    """The Qt font of a style: its family (the game's, else a stand-in), pixel size, weight and slant. The game's
    ``LetterSpacing`` (0 to 25) widens no text by a measurable amount (layout.md "Fonts"), so it is left out."""
    font = QFont(style.family or FALLBACK)
    font.setStyleHint(QFont.StyleHint.Serif)
    font.setPixelSize(max(1, round(style.size)))
    font.setBold(style.bold)
    font.setItalic(style.italic)
    return font


@dataclass
class SLines:
    lines: list[str]
    width_px: float  # the widest line
    height_px: float  # the first line's height, then a step for each further line
    line_height: float  # the step from one line to the next: the style's leading times its size
    first: float = 0.0  # the first line's height: the font's own (ascent plus descent)


def _wrap(text: str, metrics: QFontMetricsF, width_px: float) -> list[str]:
    """``text`` in lines no wider than ``width_px``: broken at blanks, and a word wider than the plane inside the word
    (the game breaks it too: layout.md "The text probe")."""
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}" if line else word
            if line and metrics.horizontalAdvance(trial) > width_px:
                out.append(line)
                trial = word
            *full, line = _pieces(trial, metrics, width_px)
            out += full
        out.append(line)
    return out


def _pieces(word: str, metrics: QFontMetricsF, width_px: float) -> list[str]:
    """A word as the pieces that fit ``width_px``, one character at least in each."""
    out = []
    while len(word) > 1 and metrics.horizontalAdvance(word) > width_px:
        cut = len(word) - 1
        while cut > 1 and metrics.horizontalAdvance(word[:cut]) > width_px:
            cut -= 1
        out.append(word[:cut])
        word = word[cut:]
    return [*out, word]


def _cut(line: str, metrics: QFontMetricsF, width_px: float, ellipsis: bool) -> str:
    if metrics.horizontalAdvance(line) <= width_px:
        return line
    tail = ELLIPSIS if ellipsis else ""
    while line and metrics.horizontalAdvance(line + tail) > width_px:
        line = line[:-1]
    return line + tail


def lay_out(text: str, style: STextStyle, width_px: float, height_px: float = 0.0) -> SLines:
    """``text`` broken and cut as the label's overflow modes say inside a plane of ``width_px`` (and ``height_px``)."""
    metrics = QFontMetricsF(qfont(style))
    first = metrics.height()  # the game: the font's own height, then leading x size a line (layout.md "Fonts")
    line_height = style.size * (style.leading or 1.2)
    if style.wrap and width_px > 0:
        lines = _wrap(text, metrics, width_px)
    else:
        lines = text.split("\n")
    if width_px > 0 and style.cut in ("character", "ellipsis"):
        lines = [_cut(line, metrics, width_px, style.cut == "ellipsis") for line in lines]
    if height_px > 0 and style.vcut and first + (len(lines) - 1) * line_height > height_px + 0.5:
        keep = max(1, int((height_px + 0.5 - first) // line_height) + 1)
        lines = lines[:keep]
        if style.vcut == "ellipsis":
            lines[-1] = _cut(lines[-1] + ELLIPSIS, metrics, width_px or 1e9, True)
    widest = max((metrics.horizontalAdvance(line) for line in lines), default=0.0)
    return SLines(lines, widest, first + line_height * max(0, len(lines) - 1), line_height, first)
