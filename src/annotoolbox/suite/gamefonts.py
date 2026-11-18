"""A game's own font files as Qt families: read from the install at run time, loaded once for the session.

``FontRegistry(read)`` is one game's view (``read(path)`` gives a file's bytes or raises ``OSError`` / ``KeyError``):
``face(path)`` is the family a font file draws as, or the reason it cannot be had; ``notes()`` lists the reasons, one
line each, for a Checks list or a tooltip. ``load(data)`` registers font bytes with Qt, deduplicated by content, so
several windows and games share one registration. A font is never copied anywhere: it lives in Qt's font database of the
running process (docs/ui-editor.md, "Fonts").
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QFont, QFontDatabase

from ..infra.i18n import tr

_BY_CONTENT: dict[str, str | None] = {}  # sha1 of the font bytes -> the family Qt registered (None: not a font)


def load(data: bytes) -> str | None:
    """Register font bytes with Qt (once per distinct content); the family name, None when Qt reads no font in them."""
    key = hashlib.sha1(data).hexdigest()
    if key not in _BY_CONTENT:
        ident = QFontDatabase.addApplicationFontFromData(QByteArray(data))
        families = QFontDatabase.applicationFontFamilies(ident) if ident >= 0 else []
        _BY_CONTENT[key] = families[0] if families else None
    return _BY_CONTENT[key]


@dataclass(frozen=True)
class Face:
    """What a font file draws as: ``family`` is the game's own face (None where it could not be read) and ``reason`` why not."""

    family: str | None
    reason: str = ""

    @property
    def loaded(self) -> bool:
        return self.family is not None


class FontRegistry:
    """One game's font files, loaded on first use and remembered, with a reason for each one that did not load."""

    def __init__(self, read: Callable[[str], bytes | None]) -> None:
        self._read = read
        self._faces: dict[str, Face] = {}
        self._notes: dict[str, str] = {}

    def face(self, path: str) -> Face:
        """The face of a font file of the game (``path`` as the game names it, any case)."""
        path = path.replace("\\", "/").lower()
        if path not in self._faces:
            self._faces[path] = self._load(path)
        return self._faces[path]

    def _load(self, path: str) -> Face:
        try:
            data = self._read(path)
        except (OSError, KeyError, ValueError):
            data = None
        if not data:
            return self.fail(path, tr("common.font_not_in_install", font=path))
        family = load(data)
        return Face(family) if family else self.fail(path, tr("common.font_not_readable", font=path))

    def fail(self, name: str, reason: str) -> Face:
        """Record that ``name`` is drawn in a stand-in, and why."""
        self._notes.setdefault(name, reason)
        return Face(None, reason)

    def note(self, name: str, reason: str) -> None:
        """A reason for a font that loads but is not the one the game draws with (a weight stood in for another)."""
        self._notes.setdefault(name, reason)

    def notes(self) -> list[str]:
        """The reasons recorded so far, one line each, in the order they came up."""
        return list(self._notes.values())

    @property
    def loaded(self) -> int:
        return sum(1 for f in self._faces.values() if f.loaded)


def qfont(family: str | None, pixel_size: float, bold: bool = False, italic: bool = False, weight: int = 0) -> QFont:
    """A Qt font in ``family`` (a system default where None) at a pixel size; ``weight`` 100 to 900 overrides ``bold``."""
    font = QFont(family) if family else QFont()
    font.setPixelSize(max(1, round(pixel_size)))
    if weight:
        font.setWeight(QFont.Weight(max(100, min(900, weight))))
    else:
        font.setBold(bold)
    font.setItalic(italic)
    return font
