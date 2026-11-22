"""Which characters a game's fonts draw, so a translation can be checked for letters the game would show as its default
character (docs/text-editor.md, "The glyph check"). No Qt: the character table (``cmap``) of a TrueType or OpenType
file, or of a collection, is read by hand.

``covered(data)`` is the set of code points one file draws; ``CFontCheck(files, game)`` knows which files a language of a
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

from ..assetedit.gamefiles import CGameFiles
from ..infra.i18n import tr
from .checks import HINT, WARNING, SProblem
from .fontchain import CFontChain, SStyleFaces

_SILENT = {"Cc", "Cf", "Zs", "Zl", "Zp"}  # drawn as nothing in any font
_NOT_TEXT = re.compile(r"\{[^{}]*\}|\[[^\[\]\r\n]*\]|<[^<>]*>")  # placeholders and markup draw nothing
_FULL = ("data/fonts/*.ttf", "data/fonts/*.otf", "data/fonts/*.ttc")
CHAINED = ("anno117", "anno1800")  # the games whose text styles name a localized font in the UI files


@dataclass(frozen=True)
class SPlan:
    """The files a language draws with. ``styles`` (117): the files each text style tries in order, a text in a style
    shows the default character for a character none of them has; ``others``: where the game's faces are not read, every
    font it has (a character is then only missing when none draws it)."""

    styles: tuple[SStyleFaces, ...] = ()
    others: tuple[str, ...] = ()

    @property
    def faces(self) -> tuple[str, ...]:
        """The files of every style's chain, each once, in the order of the styles."""
        return tuple(dict.fromkeys(path for style_faces in self.styles for path in style_faces.faces))


LOOSE = {
    "anno2205": ("data/ui/chromium/*.ttf",),
    "anno2070": ("data/loca/*/fonts/*.ttf",),
    "anno1404": ("data/loca/*/fonts/*.ttf", "data/ui/studio/fonts/*.ttf"),
    "anno1701": ("data/loca/*/fonts/*.ttf", "data/ui/studio/fonts/*.ttf"),
}  # the games whose per-language faces are not read: every font file they ship


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _tables(data: bytes, table_offset: int) -> dict[bytes, tuple[int, int]]:
    count = _u16(data, table_offset + 4)
    return {
        data[table_offset + 12 + 16 * i : table_offset + 16 + 16 * i]: struct.unpack_from(
            ">II", data, table_offset + 20 + 16 * i
        )
        for i in range(count)
    }


def _format4(data: bytes, table_offset: int, out: set[int]) -> None:
    segments = _u16(data, table_offset + 6) // 2
    ends, starts = table_offset + 14, table_offset + 16 + 2 * segments
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


def _format12(data: bytes, table_offset: int, out: set[int]) -> None:
    for i in range(struct.unpack_from(">I", data, table_offset + 12)[0]):
        start, end, glyph = struct.unpack_from(">III", data, table_offset + 16 + 12 * i)
        out.update(
            code_point for code_point in range(start, min(end, 0x10FFFF) + 1) if glyph + (code_point - start) > 0
        )


def _subtable(data: bytes, table_offset: int, out: set[int]) -> None:
    kind = _u16(data, table_offset)
    if kind == 4:
        _format4(data, table_offset, out)
    elif kind in (12, 13):
        _format12(data, table_offset, out)
    elif kind == 6:
        first, count = _u16(data, table_offset + 6), _u16(data, table_offset + 8)
        out.update(first + i for i in range(count) if _u16(data, table_offset + 10 + 2 * i))
    elif kind == 0:
        out.update(i for i in range(256) if data[table_offset + 6 + i])


def _font_cmap(data: bytes, font_offset: int) -> set[int]:
    found = _tables(data, font_offset).get(b"cmap")
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
            return frozenset().union(*(_font_cmap(data, font_offset) for font_offset in offsets))
        return frozenset(_font_cmap(data, 0))
    except (struct.error, IndexError) as exc:
        raise ValueError(tr("texts.not_font_file", error=exc)) from exc


def drawn(text: str) -> set[str]:
    """The characters of a text the game must draw: no markup, no placeholder, no white space, control or format
    character (the zero-width space is a break opportunity that 38,000 lines of the Asian texts hold)."""
    return {
        character
        for character in _NOT_TEXT.sub("", text)
        if not character.isspace() and unicodedata.category(character) not in _SILENT
    }


class CFontCheck:
    """The fonts of one game, read on demand (the big CJK files take a moment: ``load`` runs off the window's thread)."""

    def __init__(self, files: CGameFiles, game: str, chain: CFontChain | None = None) -> None:
        self.m_files, self.m_game = files, game
        self._m_cover: dict[str, frozenset[int]] = {}
        self._m_paths: list[str] | None = None
        self._m_plans: dict[str, SPlan] = {}
        self._m_chain = chain or (CFontChain(files) if game in CHAINED else None)

    def paths(self) -> list[str]:
        """Every font file of the game, in its paths."""
        if self._m_paths is None:
            patterns = _FULL if self.m_game in CHAINED else LOOSE.get(self.m_game, ())
            self._m_paths = sorted({path for pattern in patterns for path in self.m_files.glob(pattern)})
        return self._m_paths

    def plan(self, language: str) -> SPlan:
        """The text styles' chains for ``language`` (game 117, where the UI files are readable), else every font of the
        game; paths as the game has them."""
        if language not in self._m_plans:
            paths = self.paths()
            if self._m_chain is not None and self._m_chain.available:
                known = set(paths)
                found = [
                    SStyleFaces(
                        style_faces.name,
                        tuple(path for path in style_faces.faces if path in known),
                        style_faces.styles,
                        style_faces.key,
                    )
                    for style_faces in self._m_chain.styles(language)
                ]
                self._m_plans[language] = SPlan(tuple(style_faces for style_faces in found if style_faces.faces))
            else:
                self._m_plans[language] = SPlan((), tuple(paths))
        return self._m_plans[language]

    def cover(self, path: str) -> frozenset[int]:
        if path not in self._m_cover:
            try:
                self._m_cover[path] = covered(self.m_files.read(path))
            except (OSError, KeyError, ValueError):
                self._m_cover[path] = frozenset()
        return self._m_cover[path]

    def load(self, language: str) -> SPlan:
        """Read the character tables of a language's files (the slow step)."""
        found = self.plan(language)
        for path in (*found.faces, *found.others):
            self.cover(path)
        return found

    def styles(self, language: str) -> list[str]:
        """The names of the text styles' fonts a language is checked against (117), else none."""
        return [style_faces.name for style_faces in self.plan(language).styles]

    @property
    def chain(self) -> CFontChain | None:
        return self._m_chain

    def lacking(
        self, language: str, characters: Iterable[str], style: str | tuple[str, ...] = ""
    ) -> dict[str, tuple[str, bool]]:
        """For each character a text style's chain lacks: (the names of those styles, whether another does draw it).
        ``style``: only the chains of these localized fonts (a name or an asset id, one or several; 117, 1800); without
        it every style counts, and where no style is read a character is missing when no font of the game has it."""
        found = self.plan(language)
        wanted = ({style} if isinstance(style, str) else set(style)) - {""}
        chains = [
            style_faces for style_faces in found.styles if not wanted or wanted & {style_faces.name, style_faces.key}
        ]
        out: dict[str, tuple[str, bool]] = {}
        for character in sorted(set(characters)):
            code = ord(character)
            if chains:
                missing = [
                    style_faces.name
                    for style_faces in chains
                    if not any(code in self.cover(path) for path in style_faces.faces)
                ]
                if missing:
                    out[character] = (", ".join(missing), len(missing) < len(chains))
            elif not any(code in self.cover(path) for path in found.others):
                out[character] = (tr("texts.any_font"), False)
        return out

    def unsupported(self, language: str, text: str, style: str | tuple[str, ...] = "") -> list[SProblem]:
        """The problems of a text's characters for a language: a warning where every style (or the picked one) lacks a
        character, a hint where only some do."""
        out = []
        for character, (names, elsewhere) in self.lacking(language, drawn(text), style).items():
            shown = f"{character} (U+{ord(character):04X})"
            if elsewhere:
                out.append(
                    SProblem(
                        HINT,
                        "glyph",
                        tr("texts.not_fonts_other_styles_draw", char=shown, fonts=names),
                    )
                )
            else:
                out.append(
                    SProblem(
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
        return sorted(
            ((character, counts[character], found[character][1]) for character in found), key=lambda row: -row[1]
        )
