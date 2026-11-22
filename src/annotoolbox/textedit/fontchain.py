"""Which font files a 117 text style draws a language with: the style's localized font, that font's face for the
language (an exception per language) and the fallback list behind it (docs/text-editor.md, "The glyph check"). No Qt.

``CFontChain(files).styles(language)`` lists, per localized font a text style uses, the files of the game in the order the
engine tries them: the face, then its fallbacks. A character no file of a style's chain has shows as the face's default
character (docs/ui/fonts.md has the chain of assets).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..assetedit.gamefiles import CGameFiles
from ..formats import phoenix, phoenixedit, phoenixread
from ..uiedit import fields

FALLBACKS = "5962aa9a"  # the unnamed list of a localized font and of each exception (its entries name a font family)
NAME = "8c00a6d0"  # the localized font's name, an unnamed field
SANS_SERIF = "sansseriflanguage"  # the exception a scanned font with no default face gives the other languages
KINDS = (b"TS", b"LF", b"FT", b"FF", b"EC")


EXCEPTION = bytes.fromhex("95a87446")  # the type hash of a language exception, which opens each one in a localized font
_EXCEPTION_NAME = re.compile(bytes.fromhex("7f80") + rb"(.)" + bytes([1]) + rb"([A-Za-z]+)", re.S)


@dataclass
class SRawLocalized:
    """A localized font that the schema does not decode (1800's four with a script of their own), read by the references
    it holds: ``base`` the asset ids before the first exception, ``exceptions`` language (lower case) -> its ids."""

    base: list[str] = field(default_factory=list)
    exceptions: dict[str, list[str]] = field(default_factory=dict)


def scan_localized(data: bytes, known: set[str]) -> SRawLocalized:
    """The asset references (``01 <type hash> <guid>``) of a localized font file, in order, split at its exceptions;
    only a guid of a font asset in ``known`` counts, so no stray bytes do."""
    starts = [match.start() for match in re.finditer(re.escape(EXCEPTION), data)]
    bounds = [0, *starts, len(data)]
    out = SRawLocalized()
    for k in range(len(bounds) - 1):
        piece = data[bounds[k] : bounds[k + 1]]
        found = [
            guid
            for i in range(len(piece) - 20)
            if piece[i] == 1 and (guid := str(uuid.UUID(bytes_le=piece[i + 5 : i + 21]))) in known
        ]
        if k == 0:
            out.base = found
            continue
        name = _EXCEPTION_NAME.search(piece, 0, 40)
        if name is not None:
            out.exceptions[name.group(2).decode().lower()] = found
    return out


@dataclass(frozen=True)
class SStyleFaces:
    """What the text styles of one localized font draw with for a language: ``faces`` in the order tried."""

    name: str
    faces: tuple[str, ...]
    styles: int = 1  # how many text styles use this localized font
    key: str = ""  # the localized font's asset id: what a text style (and so a label) names


class CFontChain:
    """The font assets of a game's UI files, read once on first use (about a second for 117)."""

    def __init__(self, files: CGameFiles) -> None:
        self.m_files = files
        self._m_assets: dict[bytes, dict[str, dict]] | None = None
        self._m_raw: dict[str, SRawLocalized] = {}  # localized fonts the schema cannot decode, by file name

    def _read(self) -> dict[bytes, dict[str, dict]]:
        if self._m_assets is None:
            tree, found = self.m_files.tree, {kind: {} for kind in KINDS}
            schema = phoenix.load_schema()
            for path in tree.under(phoenix.GENERATED):
                data = tree.read(path)
                kind = data[3:5] if data.startswith(b"PHX") else b""
                if kind in found:
                    name = path.rsplit("/", 1)[-1].lower()
                    try:
                        found[kind][name] = phoenixread.load(data, schema).m_root
                    except (ValueError, EOFError, KeyError, IndexError):
                        if kind == b"LF":
                            self._m_raw[name] = data
            self._m_assets = found
            self._m_raw = {name: scan_localized(data, self._font_guids()) for name, data in self._m_raw.items()}
        return self._m_assets

    def _font_guids(self) -> set[str]:
        """The ids of the font, family and fallback-group assets: what a scanned reference must name."""
        assets = self._m_assets or {}
        return {guid for kind in (b"FT", b"FF", b"EC") for guid in assets.get(kind, {})}

    def _chain_of(self, guids: list[str]) -> list[str]:
        """The files that a list of font, family and group references stand for, in order."""
        assets, out = self._read(), []
        for guid in guids:
            if guid in assets[b"FT"]:
                out.append(self._font_path(guid))
            elif guid in assets[b"FF"]:
                out.append(self._family(guid))
            elif guid in assets[b"EC"]:
                out += [
                    self._font_path(fields.get(entry, "Font"))
                    for entry in phoenixedit.objects(assets[b"EC"][guid], "FallbackFonts")
                ]
        return [path for path in out if path]

    def raw_faces(self, raw: SRawLocalized, language: str) -> tuple[str, ...]:
        """A scanned localized font's files for ``language``: its exception's, else its own."""
        wanted = language.replace("_", " ").lower()
        found = raw.exceptions.get(wanted) or raw.base or raw.exceptions.get(SANS_SERIF)
        return tuple(dict.fromkeys(self._chain_of(found)))

    @property
    def available(self) -> bool:
        return bool(self._read()[b"TS"] and self._read()[b"LF"])

    def _font_path(self, guid: str | None) -> str:
        font = self._read()[b"FT"].get(guid or "")
        path = fields.get(font, "FontId") if font is not None else None
        return str(path).replace("\\", "/").lower() if path else ""

    def _fallbacks(self, node: dict) -> list[str]:
        """The files a localized font, or one of its exceptions, falls back to: its list, then its group's."""
        out = [self._family(fields.get(entry, "FallbackFont")) for entry in phoenixedit.objects(node, FALLBACKS)]
        group = self._read()[b"EC"].get(fields.get(node, "FallbackFontGroup") or "")
        out += (
            [self._font_path(fields.get(entry, "Font")) for entry in phoenixedit.objects(group, "FallbackFonts")]
            if group
            else []
        )
        return [path for path in out if path]

    def _family(self, guid: str | None) -> str:
        family = self._read()[b"FF"].get(guid or "")
        return self._font_path(fields.get(family, "Font")) if family is not None else ""

    def faces(self, localized: dict, language: str) -> tuple[str, ...]:
        """The files a localized font draws ``language`` with: the exception's face and fallbacks where the font has an
        exception for the language, else its default face and fallbacks."""
        wanted = language.replace("_", " ").lower()
        node, face = localized, fields.get(localized, "DefaultFont")
        for exception in phoenixedit.objects(localized, "LanguagesExceptions"):
            if str(fields.get(exception, "Language", "")).lower() == wanted:
                node, face = exception, fields.get(exception, "FontException") or face
        chain = [self._font_path(face), *self._fallbacks(node)]
        return tuple(dict.fromkeys(path for path in chain if path))

    def _hieroglyph_faces(self) -> set[str]:
        """The faces that the ``Hieroglyphs`` exception of a localized font names: the font of a style that draws
        hieroglyph text, which no translation is written in."""
        return {
            fields.get(exception, "FontException")
            for localized_font in self._read()[b"LF"].values()
            for exception in phoenixedit.objects(localized_font, "LanguagesExceptions")
            if fields.get(exception, "Language") == "Hieroglyphs"
        }

    def styles(self, language: str) -> list[SStyleFaces]:
        """One entry per localized font that a text style uses (the hieroglyph font apart), with the files it draws
        ``language`` with."""
        assets, used, skip = self._read(), {}, self._hieroglyph_faces()
        raw_used: dict[str, int] = {}
        for style in assets[b"TS"].values():
            guid = fields.get(style, "LocalizedFont")
            if guid in self._m_raw:
                raw_used[guid] = raw_used.get(guid, 0) + 1
            elif guid in assets[b"LF"] and fields.get(assets[b"LF"][guid], "DefaultFont") not in skip:
                used[guid] = used.get(guid, 0) + 1
        out = [
            SStyleFaces(
                str(fields.get(assets[b"LF"][guid], NAME) or guid),
                self.faces(assets[b"LF"][guid], language),
                count,
                guid,
            )
            for guid, count in used.items()
            if guid in assets[b"LF"]
        ]
        for guid, count in raw_used.items():  # 1800 names no font: its first file does
            faces = self.raw_faces(self._m_raw[guid], language)
            out.append(SStyleFaces(f"{Path(faces[0]).stem} ({guid[:4]})" if faces else guid, faces, count, guid))
        return sorted(
            (style_faces for style_faces in out if style_faces.faces), key=lambda style_faces: -style_faces.styles
        )
