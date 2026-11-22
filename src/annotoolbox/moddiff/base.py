"""The game's files as the comparison reads them: ``CGameBase`` over an opened game (``assetedit.assetgame.CGame``).

It is what ``modloader.engine.CSession`` is given (``read_base``, ``lazy``: a game file's bytes, and the byte ranges of
the assets of an indexed asset file, so only the assets a mod touches are parsed) and what the comparison asks for
the game's own asset (``vanilla_asset``), its names and the keys of its asset files. ``docs/mod-diff.md``
"Where the data comes from".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable

from ..assetedit.assetdiff import text_entries
from ..assetedit.assetgame import TEXT_KEYS, CGame
from ..modloader.patchdoc import CPatchDoc
from .elementdata import asset_facts


class CGameBase:
    """One game's files for a comparison; every path is lower case with forward slashes, as the loader's."""

    def __init__(self, game: CGame) -> None:
        self.m_game = game
        self.m_layout = game.m_layout
        self.m_files = [file_path.lower() for file_path in game.m_index.m_files]  # the indexed asset files
        self.m_entries: dict[int, dict[int, object]] = {}  # file number -> GUID -> the index entry
        for entry in game.m_index.m_entries:
            self.m_entries.setdefault(entry.file, {})[entry.guid] = entry
        self._m_docs: dict[str, CPatchDoc | None] = {}
        self.m_text_key = TEXT_KEYS[0] if self.m_layout.key == "anno117" else TEXT_KEYS[1]

    # what the session reads (the game's own readers: the Assets app's mod import reads through them too)
    def read_base(self, path: str) -> bytes | None:
        return self.m_game.read_base(path)

    def lazy(self, path: str) -> list | None:
        return self.m_game.lazy_ranges(path)

    def number(self, path: str) -> int | None:
        """The index number of an indexed asset file, None for any other path."""
        return self.m_game.file_number(path)

    # the game's own assets and files
    def exists(self, path: str) -> bool:
        return self.number(path) is not None or self.m_game.m_files.exists(path)

    def vanilla_asset_counts(self, path: str) -> dict[str, int]:
        """The GUIDs the game's asset file ``path`` defines (as text), each with how many times."""
        k = self.number(path)
        if k is not None:
            return {str(guid): 1 for guid in self.m_entries.get(k, {})}
        doc = self.vanilla_doc(path)
        return {guid: len(found) for guid, found in doc.m_by_guid.items()} if doc is not None else {}

    def vanilla_asset(self, path: str, guid: str) -> ET.Element | None:
        """The ``<Asset>`` of the game's file ``path`` with ``guid``, parsed now; None where the game has none."""
        k = self.number(path)
        if k is not None:
            entry = self.m_entries.get(k, {}).get(int(guid)) if guid.lstrip("-").isdigit() else None
            return self.m_game.m_index.element_of(entry) if entry else None
        doc = self.vanilla_doc(path)
        found = doc.m_by_guid.get(guid) if doc is not None else None
        return found[-1] if found else None

    def vanilla_info(self, path: str, guid: str) -> tuple[str, str]:
        """``(name, template)`` of the game's asset."""
        k = self.number(path)
        if k is not None and guid.lstrip("-").isdigit():
            entry = self.m_entries.get(k, {}).get(int(guid))
            return (entry.name, entry.template) if entry else ("", "")
        return asset_facts(self.vanilla_asset(path, guid))

    def vanilla_doc(self, path: str) -> CPatchDoc | None:
        """A game file read whole as a document (once): for the files that are not indexed. None for a file the game
        lacks and for one that is not XML (the run's session says so where a mod patches it)."""
        if path not in self._m_docs:
            data = self.read_base(path)
            try:
                self._m_docs[path] = CPatchDoc.load(data) if data is not None else None
            except ET.ParseError:
                self._m_docs[path] = None
        return self._m_docs[path]

    def language_of(self) -> Callable[[str], str | None]:
        from ..assetedit.archiveimport import text_matcher

        return text_matcher(self.m_layout)

    def texts_of(self, doc: CPatchDoc) -> dict[str, str]:
        """Text id -> text of a patched text file (the last entry of an id wins)."""
        out: dict[str, str] = {}
        for tid, entries in text_entries(doc.roots[0] if doc.roots else None, self.m_text_key).items():
            out[tid] = (entries[-1].findtext("Text") or "").strip()
        return out
