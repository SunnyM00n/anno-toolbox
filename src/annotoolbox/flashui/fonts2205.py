"""The fonts of Anno 2205's movies for the preview: which Qt family and weight a font reference draws with.

A Roboto reference draws with the TrueType files the install ships for Light and Regular; a weight or family without
one stands in, with the reason in ``notes()`` (docs/formats/anno2205-ui-format.md "Font descriptors").
``Fonts2205(ui).choose(font_ref)`` is the entry; ``ui`` is an ``othergames.ui2205.UiFiles``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..formats import feuread, ffdread
from ..infra.i18n import tr
from ..othergames import ui2205
from ..suite.gamefonts import FontRegistry

CHROMIUM = ui2205.UI + "chromium/"
FILES = {
    400: CHROMIUM + "roboto-regular.ttf",
    300: CHROMIUM + "roboto-light.ttf",
}  # the weights the game ships as TrueType
WEIGHT_WORDS = {"thin": 100, "light": 300, "regular": 400, "medium": 500, "bold": 700}
SYSTEM = "Arial"  # the font of a text with no font reference


@dataclass(frozen=True)
class Choice:
    family: str
    weight: int
    real: bool  # the game's own face at this weight (a TrueType file of the install)


def split_name(name: str) -> tuple[str, int]:
    """A reference's family name as (family, weight): ``Roboto Light`` is ("Roboto", 300)."""
    words = name.split()
    if len(words) > 1 and words[-1].lower() in WEIGHT_WORDS:
        return " ".join(words[:-1]), WEIGHT_WORDS[words[-1].lower()]
    return name, 400


class Fonts2205:
    """The font references of the movies, resolved once each against the install's files."""

    def __init__(self, ui: ui2205.UiFiles) -> None:
        self.ui = ui
        self.registry = FontRegistry(ui.read)
        self._chosen: dict[tuple[str, str], Choice] = {}

    def choose(self, ref: feuread.FontRef | None) -> Choice:
        """The family and weight a font reference draws with, a system stand-in where the game's face is not shipped."""
        if ref is None:
            return Choice(SYSTEM, 400, False)
        key = (ref.file.lower(), ref.name)
        if key not in self._chosen:
            self._chosen[key] = self._choose(ref)
        return self._chosen[key]

    def _described(self, ref: feuread.FontRef) -> str:
        """The family the font's descriptor names (it agrees with the reference's name), else the reference's."""
        try:
            return ffdread.read(self.ui.file(ui2205.font_path(ref.file))).family
        except (OSError, KeyError, ValueError):
            return ref.name

    def _choose(self, ref: feuread.FontRef) -> Choice:
        name = self._described(ref) or ref.name
        family, weight = split_name(name)
        if family.lower() != "roboto":
            self.registry.note(name, tr("common.font_2205_no_truetype", font=name, stand_in=family))
            return Choice(family, weight, False)
        faces = {w: self.registry.face(path) for w, path in FILES.items()}
        loaded = next((f.family for f in faces.values() if f.family), None)
        if loaded is None:
            return Choice(family, weight, False)  # the registry has said why
        if weight not in FILES:
            near = min(FILES, key=lambda w: abs(w - weight))
            self.registry.note(name, tr("common.font_2205_weight_stood_in", font=name, weight=near))
        return Choice(loaded, weight, weight in FILES and faces[weight].loaded)

    def notes(self) -> list[str]:
        return self.registry.notes()
