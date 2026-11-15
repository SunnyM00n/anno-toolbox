"""Sound events and banks named by their Wwise id: an ``Audio`` asset says ``<WwiseID>`` (the event's id, the FNV-1 hash
of its name) and a ``SoundBank`` asset the bank's id; the banks hold the events and the files they play.

``WwiseIndex(banks, exists, read)`` answers ``files_of(wwise_id)``: the bank file whose name is that id, or the bank
that holds the event and the media files the event plays. The banks are read once, on the first event asked for.
docs/references.md "Sound events by name".
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Iterable

from ..exporting import banks

BANK = ".bnk"


class WwiseIndex:
    def __init__(self, bank_files: Iterable[str], exists: Callable[[str], bool], read: Callable[[str], bytes]) -> None:
        self.exists, self.read = exists, read
        self.by_id: dict[str, list[str]] = {}  # a bank's id -> its files (one per language folder)
        for path in sorted(bank_files):
            self.by_id.setdefault(posixpath.basename(path)[: -len(BANK)], []).append(path)
        self._events: dict[int, tuple[str, banks.BankListing, banks.BankInfo]] | None = None
        self._kept: dict[int, list[str]] = {}

    def files_of(self, wwise_id: int) -> list[str]:
        """The files a Wwise id stands for: a bank's own files, or an event's bank and the media it plays; none for an
        id no bank has."""
        if wwise_id not in self._kept:
            self._kept[wwise_id] = self.by_id.get(str(wwise_id)) or self._event_files(wwise_id)
        return self._kept[wwise_id]

    def _event_files(self, event: int) -> list[str]:
        found = self._event_table().get(event)
        if found is None:
            return []
        bank, listing, info = found
        media = {banks.media_file(bank, i, info, self.exists) for i in _media_ids(listing, event)}
        return sorted({bank, *(p for p in media if p)})

    def _event_table(self) -> dict[int, tuple[str, banks.BankListing, banks.BankInfo]]:
        """Every event of every bank (the first bank, by path, that has it), read once."""
        if self._events is None:
            self._events = {}
            for files in self.by_id.values():
                try:
                    listing = banks.bank_listing(self.read(files[0]))
                    info = banks.read_info(files[0], self.exists, lambda p: self.read(p).decode("utf-8", "replace"))
                except (ValueError, KeyError, IndexError, OSError):
                    continue
                for event in listing.events:
                    self._events.setdefault(event, (files[0], listing, info))
        return self._events


def _media_ids(listing: banks.BankListing, event: int) -> set[int]:
    """The media ids of the sounds an event plays (a music track's sources are media ids already)."""
    found, _exact = banks.event_sounds(listing, event)
    return {s.media if isinstance(s, banks.BankSound) else s for s in found}
