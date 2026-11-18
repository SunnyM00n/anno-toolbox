"""The game's files as the comparison reads them: ``GameBase`` over an opened game (``assetedit.assetgame.Game``).

It is what ``modloader.engine.Session`` is given (``read_base``, ``lazy``: a game file's bytes, and the byte ranges of
the assets of an indexed asset file, so only the assets a mod touches are parsed) and what the comparison asks for
the game's own asset (``vanilla_asset``), its names and the keys of its asset files. ``docs/mod-diff.md``
"Where the data comes from".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable

from ..assetedit.assetdiff import text_entries
from ..assetedit.assetgame import TEXT_KEYS, Game
from ..modloader.patchdoc import PatchDoc
from .elementdata import asset_facts


class GameBase:
    """One game's files for a comparison; every path is lower case with forward slashes, as the loader's."""

    def __init__(self, game: Game) -> None:
        self.game = game
        self.layout = game.layout
        self.files = [p.lower() for p in game.index.files]  # the indexed asset files
        self.entries: dict[int, dict[int, object]] = {}  # file number -> GUID -> the index entry
        for entry in game.index.entries:
            self.entries.setdefault(entry.file, {})[entry.guid] = entry
        self._docs: dict[str, PatchDoc | None] = {}
        self.text_key = TEXT_KEYS[0] if self.layout.key == "anno117" else TEXT_KEYS[1]

    # what the session reads (the game's own readers: the Assets app's mod import reads through them too)
    def read_base(self, path: str) -> bytes | None:
        return self.game.read_base(path)

    def lazy(self, path: str) -> list | None:
        return self.game.lazy_ranges(path)

    def number(self, path: str) -> int | None:
        """The index number of an indexed asset file, None for any other path."""
        return self.game.file_number(path)

    # the game's own assets and files
    def exists(self, path: str) -> bool:
        return self.number(path) is not None or self.game.files.exists(path)

    def vanilla_guids(self, path: str) -> dict[str, int]:
        """The GUIDs the game's asset file ``path`` defines (as text), each with how many times."""
        k = self.number(path)
        if k is not None:
            return {str(g): 1 for g in self.entries.get(k, {})}
        doc = self.vanilla_doc(path)
        return {g: len(found) for g, found in doc.by_guid.items()} if doc is not None else {}

    def vanilla_asset(self, path: str, guid: str) -> ET.Element | None:
        """The ``<Asset>`` of the game's file ``path`` with ``guid``, parsed now; None where the game has none."""
        k = self.number(path)
        if k is not None:
            entry = self.entries.get(k, {}).get(int(guid)) if guid.lstrip("-").isdigit() else None
            return self.game.index.element_of(entry) if entry else None
        doc = self.vanilla_doc(path)
        found = doc.by_guid.get(guid) if doc is not None else None
        return found[-1] if found else None

    def vanilla_info(self, path: str, guid: str) -> tuple[str, str]:
        """``(name, template)`` of the game's asset."""
        k = self.number(path)
        if k is not None and guid.lstrip("-").isdigit():
            entry = self.entries.get(k, {}).get(int(guid))
            return (entry.name, entry.template) if entry else ("", "")
        return asset_facts(self.vanilla_asset(path, guid))

    def vanilla_doc(self, path: str) -> PatchDoc | None:
        """A game file read whole as a document (once): for the files that are not indexed. None for a file the game
        lacks and for one that is not XML (the run's session says so where a mod patches it)."""
        if path not in self._docs:
            data = self.read_base(path)
            try:
                self._docs[path] = PatchDoc.load(data) if data is not None else None
            except ET.ParseError:
                self._docs[path] = None
        return self._docs[path]

    def language_of(self) -> Callable[[str], str | None]:
        from ..assetedit.archiveimport import text_matcher

        return text_matcher(self.layout)

    def texts_of(self, doc: PatchDoc) -> dict[str, str]:
        """Text id -> text of a patched text file (the last entry of an id wins)."""
        out: dict[str, str] = {}
        for tid, entries in text_entries(doc.roots[0] if doc.roots else None, self.text_key).items():
            out[tid] = (entries[-1].findtext("Text") or "").strip()
        return out
