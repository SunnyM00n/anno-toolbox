"""The names behind a summary's numbers: GUIDs, setup options and text ids, from the installed game's assets and texts
(``docs/save-editor.md``, "Names for GUIDs and options"). ``open_names(settings, game)`` opens them (slow the first
time: the asset index; off the window's thread), ``apply(summary, names)`` fills every ``Ref`` cell's label.

The reading is the asset editor's own (``assetedit.assetgame``): its index, its schema (a ``Setup...`` option's data
set) and its texts; nothing is read twice.
"""

from __future__ import annotations

from ..assetedit import assetgame
from .records import GOODS, GUID, OPTION, TEXT_ID, Ref, Summary

OPTION_TEMPLATE = "DifficultySetup"  # the asset template whose properties are the ``Setup...`` options
TEXT_PATHS = (  # where an asset keeps the text id of its name, first that has one
    "Values/Text/OasisId",
    "Values/Text/LineID",
    "Values/Portrait/PortraitName",
    "Values/MapTemplateType/Name",
)
NAMED_GAMES = ("anno117", "anno1800", "anno2205", "anno2070")  # the games whose assets the asset editor opens
FALLBACK = "english"  # every game's full text table


class GameNames:
    """Names from an opened ``assetgame.Game`` (any object with ``index``, ``schema`` and ``texts``), in ``language``
    (the interface's by default), a text that language lacks in English."""

    def __init__(self, game, language: str = "") -> None:
        self.game, self.language = game, language or assetgame.interface_language()
        self._cache: dict[int, str] = {}

    def text(self, text_id: str) -> str:
        key = str(text_id)
        found = self.game.texts(self.language).get(key, "")
        return found or (self.game.texts(FALLBACK).get(key, "") if self.language != FALLBACK else "")

    def asset(self, guid: int) -> str:
        """The asset's display name, else its internal name, else "" for a GUID the game has not."""
        if guid not in self._cache:
            self._cache[guid] = self._asset(guid)
        return self._cache[guid]

    def _asset(self, guid: int) -> str:
        entry = self.game.index.by_guid.get(guid)
        if entry is None:
            return ""
        element = self.game.index.element(guid)
        for path in TEXT_PATHS:
            found = element.find(path)
            if found is not None and found.text and (text := self.text(found.text.strip())):
                return text
        return self.text(str(guid)) or entry.name  # 2070 keeps an asset's text under its own GUID

    def option(self, tag: str, value: str) -> str:
        """The name of a setup option's value (the item of the option's data set with that id), "" if unknown."""
        spec = self.game.schema.spec_at((OPTION_TEMPLATE, tag))
        data_set = self.game.schema.data_sets.get(spec.data_set) if spec is not None else None
        return next((name for name, ident in data_set.items if ident == value), "") if data_set else ""

    def resolve(self, ref: Ref) -> str:
        """What the cell shows instead of its number ("" when nothing is known)."""
        if ref.kind == GUID:
            return ", ".join(f"{self.asset(g)} ({g})" if self.asset(g) else str(g) for g in ref.keys)
        if ref.kind == GOODS:
            return ", ".join(f"{self.asset(g) or g} {n:,}" for g, n in zip(ref.keys, ref.amounts, strict=True))
        if ref.kind == OPTION:
            name = self.option(*ref.keys)
            return f"{name} ({ref.keys[1]})" if name else ""
        if ref.kind == TEXT_ID:
            return self.text(ref.keys[0])
        return ""


def open_names(settings, key: str) -> GameNames | None:
    """The names of game ``key``'s installed assets; None for a game without a set install folder or assets."""
    if key not in NAMED_GAMES or assetgame.installed_folder(settings, key) is None:
        return None
    names = GameNames(assetgame.open_installed(settings, key))
    names.text("0")  # read the texts now, not when the window first asks
    return names


def apply(summary: Summary, names: GameNames) -> None:
    """Set the label of every ``Ref`` cell of the summary (its rows, named rows and tables)."""
    for section in summary.sections:
        cells = [value for _label, value in section.rows] + [value for _name, value in section.named]
        if section.table is not None:
            cells += [cell for row in section.table.rows for cell in row]
        for cell in cells:
            if isinstance(cell, Ref):
                cell.label = names.resolve(cell)
