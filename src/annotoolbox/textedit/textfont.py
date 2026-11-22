"""The face the Texts app shows a text in: the game's own font, read from the install and registered with Qt.

``CFaces(files, check, game).family(language, style, linked)`` gives (family, reason): for 117 and 1800 the first face of the
text style picked (or of the styles of the labels that show the text, else the most used one); for the other games the
game's default font, named in ``DEFAULTS``. ``family`` is None, with the reason, where the font cannot be read.
"""

from __future__ import annotations

from ..assetedit.gamefiles import CGameFiles
from ..infra.i18n import tr
from ..suite.gamefonts import CFontRegistry
from .fontcover import CFontCheck

DEFAULTS = {  # the font the game draws its ordinary text with (docs/ui-editor.md, "Fonts")
    "anno2205": "data/ui/chromium/roboto-regular.ttf",
    "anno2070": "data/loca/*/fonts/dejavusanscondensed.ttf",
    "anno1404": "data/loca/*/fonts/georgia.ttf",
    "anno1701": "data/loca/*/fonts/georgia.ttf",
}
EVERY = "*"  # the picker's "every style"


class CFaces:
    def __init__(self, files: CGameFiles, check: CFontCheck, game: str) -> None:
        self.m_files, self.m_check, self.m_game = files, check, game
        self.m_registry = CFontRegistry(files.read)
        self._m_default: str | None = None  # the default font's file, looked up once: every shown row asks for it

    def _path(self, language: str, style: str, linked: tuple[str, ...]) -> str:
        styles = self.m_check.plan(language).styles
        if styles:
            wanted = (style,) if style and style != EVERY else linked
            found = next((style_faces for style_faces in styles if style_faces.key in wanted), styles[0])
            return found.faces[0]
        if self._m_default is None:
            pattern = DEFAULTS.get(self.m_game, "")
            self._m_default = next(iter(sorted(self.m_files.glob(pattern))), "") if pattern else ""
        return self._m_default

    def family(self, language: str, style: str = "", linked: tuple[str, ...] = ()) -> tuple[str | None, str]:
        """(the Qt family of the text's face, why it is not the game's face: empty when it is)."""
        path = self._path(language, style, linked)
        if not path:
            return None, tr("common.font_default_unknown")
        face = self.m_registry.face(path)
        return face.family, face.reason
