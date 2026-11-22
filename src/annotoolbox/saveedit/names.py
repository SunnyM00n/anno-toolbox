"""The names behind a summary's numbers: GUIDs, setup options and text ids, from the installed game's assets and texts
(``docs/save-editor.md``, "Names for GUIDs and options"). ``open_names(settings, game)`` opens them (slow the first
time: the asset index; off the window's thread), ``apply(summary, names)`` fills every ``CRef`` cell's label.

The reading is the asset editor's own (``assetedit.assetgame``): its index, its schema (a ``Setup...`` option's data
set) and its texts; nothing is read twice.
"""

from __future__ import annotations

from ..assetedit import assetgame
from .records import GOODS, GUID, OPTION, TEXT_ID, CRef, CSummary

OPTION_TEMPLATE = "DifficultySetup"  # the asset template whose properties are the ``Setup...`` options
TEXT_PATHS = (  # where an asset keeps the text id of its name, first that has one
    "Values/Text/OasisId",
    "Values/Text/LineID",
    "Values/Portrait/PortraitName",
    "Values/MapTemplateType/Name",
)
NAMED_GAMES = ("anno117", "anno1800", "anno2205", "anno2070")  # the games whose assets the asset editor opens
FALLBACK = "english"  # every game's full text table


class CGameNames:
    """Names from an opened ``assetgame.CGame`` (any object with ``index``, ``schema`` and ``texts``), in ``language``
    (the interface's by default), a text that language lacks in English."""

    def __init__(self, game, language: str = "") -> None:
        self.m_game, self.m_language = game, language or assetgame.interface_language()
        self._m_cache: dict[int, str] = {}

    def text(self, text_id: str) -> str:
        key = str(text_id)
        found = self.m_game.texts(self.m_language).get(key, "")
        return found or (self.m_game.texts(FALLBACK).get(key, "") if self.m_language != FALLBACK else "")

    def asset(self, guid: int) -> str:
        """The asset's display name, else its internal name, else "" for a GUID the game has not."""
        if guid not in self._m_cache:
            self._m_cache[guid] = self._asset(guid)
        return self._m_cache[guid]

    def _asset(self, guid: int) -> str:
        entry = self.m_game.m_index.m_by_guid.get(guid)
        if entry is None:
            return ""
        element = self.m_game.m_index.element(guid)
        for path in TEXT_PATHS:
            found = element.find(path)
            if found is not None and found.text and (text := self.text(found.text.strip())):
                return text
        return self.text(str(guid)) or entry.name  # 2070 keeps an asset's text under its own GUID

    def option(self, tag: str, value: str) -> str:
        """The name of a setup option's value (the item of the option's data set with that id), "" if unknown."""
        spec = self.m_game.m_schema.spec_at((OPTION_TEMPLATE, tag))
        data_set = self.m_game.m_schema.data_sets.get(spec.data_set) if spec is not None else None
        return next((name for name, ident in data_set.items if ident == value), "") if data_set else ""

    def resolve(self, ref: CRef) -> str:
        """What the cell shows instead of its number ("" when nothing is known)."""
        if ref.m_kind == GUID:
            return ", ".join(f"{self.asset(guid)} ({guid})" if self.asset(guid) else str(guid) for guid in ref.m_keys)
        if ref.m_kind == GOODS:
            return ", ".join(
                f"{self.asset(guid) or guid} {amount:,}" for guid, amount in zip(ref.m_keys, ref.m_amounts, strict=True)
            )
        if ref.m_kind == OPTION:
            name = self.option(*ref.m_keys)
            return f"{name} ({ref.m_keys[1]})" if name else ""
        if ref.m_kind == TEXT_ID:
            return self.text(ref.m_keys[0])
        return ""


def open_names(settings, key: str) -> CGameNames | None:
    """The names of game ``key``'s installed assets; None for a game without a set install folder or assets."""
    if key not in NAMED_GAMES or assetgame.installed_folder(settings, key) is None:
        return None
    names = CGameNames(assetgame.open_installed(settings, key))
    names.text("0")  # read the texts now, not when the window first asks
    return names


def apply(summary: CSummary, names: CGameNames) -> None:
    """Set the label of every ``CRef`` cell of the summary (its rows, named rows and tables)."""
    for section in summary.m_sections:
        cells = [value for _label, value in section.rows] + [value for _name, value in section.named]
        if section.table is not None:
            cells += [cell for row in section.table.rows for cell in row]
        for cell in cells:
            if isinstance(cell, CRef):
                cell.m_label = names.resolve(cell)
