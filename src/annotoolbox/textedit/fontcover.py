"""Which characters a game's fonts draw, so a translation can be checked for letters the game would show as its default
character (docs/text-editor.md, "The glyph check"). No Qt: the character table (``cmap``) of a TrueType or OpenType
file, or of a collection, is read by hand.

``covered(data)`` is the set of code points one file draws; ``FontCheck(files, game)`` knows which files a language of a
game draws with (``plan``; for 117 the chain of every text style, ``fontchain``), ``unsupported(language, text, style)``
says which characters of a text they lack and ``audit(language, texts, style)`` does the same over a whole language.
"""

from __future__ import annotations

import re
import struct
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from ..assetedit.gamefiles import GameFiles
from ..infra.i18n import tr
from .checks import HINT, WARNING, Problem
from .fontchain import FontChain, StyleFaces

_SILENT = {"Cc", "Cf", "Zs", "Zl", "Zp"}  # drawn as nothing in any font
_NOT_TEXT = re.compile(r"\{[^{}]*\}|\[[^\[\]\r\n]*\]|<[^<>]*>")  # placeholders and markup draw nothing
_FULL = ("data/fonts/*.ttf", "data/fonts/*.otf", "data/fonts/*.ttc")
CHAINED = ("anno117", "anno1800")  # the games whose text styles name a localized font in the UI files


@dataclass(frozen=True)
class Plan:
    """The files a language draws with. ``styles`` (117): the files each text style tries in order, a text in a style
    shows the default character for a character none of them has; ``others``: where the game's faces are not read, every
    font it has (a character is then only missing when none draws it)."""

    styles: tuple[StyleFaces, ...] = ()
    others: tuple[str, ...] = ()

    @property
    def faces(self) -> tuple[str, ...]:
        """The files of every style's chain, each once, in the order of the styles."""
        return tuple(dict.fromkeys(p for s in self.styles for p in s.faces))


LOOSE = {
    "anno2205": ("data/ui/chromium/*.ttf",),
    "anno2070": ("data/loca/*/fonts/*.ttf",),
    "anno1404": ("data/loca/*/fonts/*.ttf", "data/ui/studio/fonts/*.ttf"),
    "anno1701": ("data/loca/*/fonts/*.ttf", "data/ui/studio/fonts/*.ttf"),
}  # the games whose per-language faces are not read: every font file they ship


def _u16(data: bytes, at: int) -> int:
    return struct.unpack_from(">H", data, at)[0]


def _tables(data: bytes, at: int) -> dict[bytes, tuple[int, int]]:
    count = _u16(data, at + 4)
    return {
        data[at + 12 + 16 * i : at + 16 + 16 * i]: struct.unpack_from(">II", data, at + 20 + 16 * i)
        for i in range(count)
    }


def _format4(data: bytes, at: int, out: set[int]) -> None:
    segments = _u16(data, at + 6) // 2
    ends, starts = at + 14, at + 16 + 2 * segments
    deltas, ranges = starts + 2 * segments, starts + 4 * segments
    for i in range(segments):
        end, start = _u16(data, ends + 2 * i), _u16(data, starts + 2 * i)
        delta, offset = _u16(data, deltas + 2 * i), _u16(data, ranges + 2 * i)
        for code in range(start, min(end, 0xFFFE) + 1):
            if offset == 0:
                glyph = (code + delta) & 0xFFFF
            else:
                where = ranges + 2 * i + offset + 2 * (code - start)
                glyph = _u16(data, where) if where + 2 <= len(data) else 0
                glyph = (glyph + delta) & 0xFFFF if glyph else 0
            if glyph:
                out.add(code)


def _format12(data: bytes, at: int, out: set[int]) -> None:
    for i in range(struct.unpack_from(">I", data, at + 12)[0]):
        start, end, glyph = struct.unpack_from(">III", data, at + 16 + 12 * i)
        out.update(c for c in range(start, min(end, 0x10FFFF) + 1) if glyph + (c - start) > 0)


def _subtable(data: bytes, at: int, out: set[int]) -> None:
    kind = _u16(data, at)
    if kind == 4:
        _format4(data, at, out)
    elif kind in (12, 13):
        _format12(data, at, out)
    elif kind == 6:
        first, count = _u16(data, at + 6), _u16(data, at + 8)
        out.update(first + i for i in range(count) if _u16(data, at + 10 + 2 * i))
    elif kind == 0:
        out.update(i for i in range(256) if data[at + 6 + i])


def _font_cmap(data: bytes, at: int) -> set[int]:
    found = _tables(data, at).get(b"cmap")
    out: set[int] = set()
    if found is None:
        return out
    base = found[0]
    for i in range(_u16(data, base + 2)):
        platform, _encoding, offset = struct.unpack_from(">HHI", data, base + 4 + 8 * i)
        if platform in (0, 3):  # Unicode and Windows; the Mac tables are old code pages
            _subtable(data, base + offset, out)
    return out


def covered(data: bytes) -> frozenset[int]:
    """The code points a TrueType or OpenType file (or every font of a collection) has a glyph for."""
    try:
        if data[:4] == b"ttcf":
            count = struct.unpack_from(">I", data, 8)[0]
            offsets = struct.unpack_from(f">{count}I", data, 12)
            return frozenset().union(*(_font_cmap(data, o) for o in offsets))
        return frozenset(_font_cmap(data, 0))
    except (struct.error, IndexError) as exc:
        raise ValueError(tr("texts.not_font_file", error=exc)) from exc


def drawn(text: str) -> set[str]:
    """The characters of a text the game must draw: no markup, no placeholder, no white space, control or format
    character (the zero-width space is a break opportunity that 38,000 lines of the Asian texts hold)."""
    return {c for c in _NOT_TEXT.sub("", text) if not c.isspace() and unicodedata.category(c) not in _SILENT}


class FontCheck:
    """The fonts of one game, read on demand (the big CJK files take a moment: ``load`` runs off the window's thread)."""

    def __init__(self, files: GameFiles, game: str, chain: FontChain | None = None) -> None:
        self.files, self.game = files, game
        self._cover: dict[str, frozenset[int]] = {}
        self._paths: list[str] | None = None
        self._plans: dict[str, Plan] = {}
        self._chain = chain or (FontChain(files) if game in CHAINED else None)

    def paths(self) -> list[str]:
        """Every font file of the game, in its paths."""
        if self._paths is None:
            patterns = _FULL if self.game in CHAINED else LOOSE.get(self.game, ())
            self._paths = sorted({p for pattern in patterns for p in self.files.glob(pattern)})
        return self._paths

    def plan(self, language: str) -> Plan:
        """The text styles' chains for ``language`` (game 117, where the UI files are readable), else every font of the
        game; paths as the game has them."""
        if language not in self._plans:
            paths = self.paths()
            if self._chain is not None and self._chain.available:
                known = set(paths)
                found = [
                    StyleFaces(s.name, tuple(p for p in s.faces if p in known), s.styles, s.key)
                    for s in self._chain.styles(language)
                ]
                self._plans[language] = Plan(tuple(s for s in found if s.faces))
            else:
                self._plans[language] = Plan((), tuple(paths))
        return self._plans[language]

    def cover(self, path: str) -> frozenset[int]:
        if path not in self._cover:
            try:
                self._cover[path] = covered(self.files.read(path))
            except (OSError, KeyError, ValueError):
                self._cover[path] = frozenset()
        return self._cover[path]

    def load(self, language: str) -> Plan:
        """Read the character tables of a language's files (the slow step)."""
        found = self.plan(language)
        for path in (*found.faces, *found.others):
            self.cover(path)
        return found

    def styles(self, language: str) -> list[str]:
        """The names of the text styles' fonts a language is checked against (117), else none."""
        return [s.name for s in self.plan(language).styles]

    @property
    def chain(self) -> FontChain | None:
        return self._chain

    def lacking(
        self, language: str, chars: Iterable[str], style: str | tuple[str, ...] = ""
    ) -> dict[str, tuple[str, bool]]:
        """For each character a text style's chain lacks: (the names of those styles, whether another does draw it).
        ``style``: only the chains of these localized fonts (a name or an asset id, one or several; 117, 1800); without
        it every style counts, and where no style is read a character is missing when no font of the game has it."""
        found = self.plan(language)
        wanted = ({style} if isinstance(style, str) else set(style)) - {""}
        chains = [s for s in found.styles if not wanted or wanted & {s.name, s.key}]
        out: dict[str, tuple[str, bool]] = {}
        for ch in sorted(set(chars)):
            code = ord(ch)
            if chains:
                missing = [s.name for s in chains if not any(code in self.cover(p) for p in s.faces)]
                if missing:
                    out[ch] = (", ".join(missing), len(missing) < len(chains))
            elif not any(code in self.cover(p) for p in found.others):
                out[ch] = (tr("texts.any_font"), False)
        return out

    def unsupported(self, language: str, text: str, style: str | tuple[str, ...] = "") -> list[Problem]:
        """The problems of a text's characters for a language: a warning where every style (or the picked one) lacks a
        character, a hint where only some do."""
        out = []
        for ch, (names, elsewhere) in self.lacking(language, drawn(text), style).items():
            shown = f"{ch} (U+{ord(ch):04X})"
            if elsewhere:
                out.append(
                    Problem(
                        HINT,
                        "glyph",
                        tr("texts.not_fonts_other_styles_draw", char=shown, fonts=names),
                    )
                )
            else:
                out.append(
                    Problem(
                        WARNING,
                        "glyph",
                        tr(
                            "texts.none_fonts_game_draws_shows",
                            char=shown,
                        ),
                    )
                )
        return out

    def audit(self, language: str, texts: Iterable[str], style: str = "") -> list[tuple[str, int, bool]]:
        """(character, how many texts hold it, whether another style draws it) for every character of ``texts`` that a
        style's chain lacks, the most frequent first."""
        counts: Counter[str] = Counter()
        for text in texts:
            counts.update(drawn(text))
        found = self.lacking(language, counts, style)
        return sorted(((ch, counts[ch], found[ch][1]) for ch in found), key=lambda row: -row[1])
