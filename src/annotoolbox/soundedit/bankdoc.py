"""A Wwise bank open for editing: its objects and media, the edits made to them on an undo stack, the bank written back.

``CBankDoc(data, path, info)`` is the entry: ``do(name, **args)`` runs one edit (``soundedit.edits``, ``audioedits``),
``undo`` / ``redo`` walk the stack, ``to_bytes()`` writes the bank (byte for byte when nothing was done), ``replay``
makes the same edits to another copy (a localised bank lives in every language folder). docs/sound-editor.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace

from ..exporting.banks import SBankInfo
from ..formats import bnk, bnkgraph, bnkmusic, bnksfx
from ..formats.bnk import SHircObject, SMediaEntry
from ..infra.i18n import tr
from . import audioedits, curveedits, edits, groupedits, musicedits  # noqa: F401  # register the edits
from .registry import CEditError, g_edits

ALIGN = bnksfx.MEDIA_ALIGN


@dataclass(frozen=True)
class SLoose:
    """A streamed ``.wem`` an edit made: the file, and whether each language folder keeps its own copy of it."""

    data: bytes
    language: bool = False


@dataclass
class _SState:
    hirc: list[SHircObject]
    media: list[SMediaEntry]
    embedded: dict[int, bytes]
    removed: set[int]
    loose: dict[int, SLoose]
    chunks: list[bnk.SChunk]


class CBankDoc:
    def __init__(self, data: bytes, path: str = "", bank_info: SBankInfo | None = None) -> None:
        self.m_original, self.m_path, self.m_bank_info = data, path, bank_info or SBankInfo()
        self.m_bank = bnk.parse(data)
        self.m_version, self.m_family = self.m_bank.version, bnkgraph.family(self.m_bank.version)
        self.m_blob = next((chunk_entry.data for chunk_entry in self.m_bank.chunks if chunk_entry.tag == b"DATA"), b"")
        self.m_embedded: dict[int, bytes] = {}  # media replaced or added by edits (the rest is read from the blob)
        self.m_removed: set[int] = set()
        self.m_loose: dict[int, SLoose] = {}
        self.m_log: list[tuple[str, dict]] = []
        self._m_saved: list[tuple[str, dict]] = []  # the log as last kept in the project
        self.m_unreadable: list[int] = []  # media of the index the last write could not read (left out of the bank)
        self.m_pool = bnk.CIdPool(
            {hirc_object.id for hirc_object in self.m_bank.hirc} | {media_entry.id for media_entry in self.m_bank.media}
        )
        self._m_graph: dict[int, bnkgraph.SNode] | None = None
        self._m_index: dict[int, int] | None = None
        self._m_undo: list[tuple[str, _SState]] = []
        self._m_redo: list[tuple[str, dict, _SState]] = []

    # reading
    @property
    def graph(self) -> dict[int, bnkgraph.SNode]:
        if self._m_graph is None:
            self._m_graph = bnkgraph.read_graph(self.m_bank.hirc, self.m_version)
        return self._m_graph

    def _at(self, obj_id: int) -> int | None:
        """Where an object sits in the list (None: the bank has none of that id)."""
        if self._m_index is None:
            self._m_index = {hirc_object.id: i for i, hirc_object in enumerate(self.m_bank.hirc)}
        return self._m_index.get(obj_id)

    def hirc_object(self, obj_id: int) -> SHircObject:
        at = self._at(obj_id)
        if at is None:
            raise CEditError(tr("sound.bank_no_object", obj_id=obj_id))
        return self.m_bank.hirc[at]

    def kind_of(self, obj_id: int) -> int:
        """The type of an object of the bank (0 for an id that is none: a group of the state manager, say)."""
        at = self._at(obj_id)
        return self.m_bank.hirc[at].type if at is not None else 0

    def objects_of(self, type_id: int) -> list[SHircObject]:
        return self.m_bank.objects_of(type_id)

    def media(self, media_id: int) -> bytes | None:
        """A media file of the bank itself (an edited one, else the original); None when the bank has none."""
        if media_id in self.m_embedded:
            return self.m_embedded[media_id]
        if media_id in self.m_removed:
            return None
        for media_entry in self.m_bank.media:
            if (
                media_entry.id == media_id
                and media_entry.size
                and media_entry.offset + media_entry.size <= len(self.m_blob)
            ):
                return self.m_blob[media_entry.offset : media_entry.offset + media_entry.size]
        return None

    def media_users(self, media_id: int) -> list[int]:
        """The Sounds and the music tracks of the bank that play a media file."""
        out = []
        for sound in self.m_bank.objects_of(bnkgraph.SOUND):
            node = self.graph.get(sound.id)
            if node is not None and node.source is not None and node.source.media == media_id:
                out.append(sound.id)
        for sound in self.m_bank.objects_of(bnkmusic.MUSIC_TRACK):
            try:
                if any(source.source_id & 0xFFFFFFFF == media_id for source in bnkmusic.parse_track(sound).m_sources):
                    out.append(sound.id)
            except (ValueError, struct.error):
                continue
        return out

    @property
    def changed(self) -> bool:
        return bool(self.m_log)

    @property
    def unsaved(self) -> bool:
        """Whether the edits differ from the ones last kept (``mark_saved``); an undo back to them is no change."""
        return self.m_log != self._m_saved

    def mark_saved(self) -> None:
        self._m_saved = list(self.m_log)

    # changing: the edits' helpers
    def put(self, hirc_object: SHircObject) -> None:
        """Replace the object of the same id (its place in the list kept)."""
        self.hirc_object(hirc_object.id)
        self.m_bank.hirc[self._m_index[hirc_object.id]] = hirc_object
        self._m_graph = None

    def splice(self, hirc_object: SHircObject, start: int, end: int, body: bytes) -> None:
        """Replace the bytes ``start`` to ``end`` of an object's payload with ``body`` and put it back."""
        self.put(
            SHircObject(
                hirc_object.type, hirc_object.id, hirc_object.payload[:start] + body + hirc_object.payload[end:]
            )
        )

    def add(self, hirc_object: SHircObject) -> None:
        """Add an object the way the shipped banks order them: children and containers before actions and events."""
        if any(existing.id == hirc_object.id for existing in self.m_bank.hirc):
            raise CEditError(tr("sound.bank_object_already", id=hirc_object.id))
        late = hirc_object.type in (bnkgraph.ACTION, bnkgraph.EVENT)
        first_late = next(
            (i for i, existing in enumerate(self.m_bank.hirc) if existing.type in (bnkgraph.ACTION, bnkgraph.EVENT)),
            None,
        )
        at = len(self.m_bank.hirc) if late or first_late is None else first_late
        self.m_bank.hirc.insert(at, hirc_object)
        self.m_pool.m_taken.add(hirc_object.id)
        self._m_index = self._m_graph = None

    def new_id(self, name: str) -> int:
        return self.m_pool.new(name)

    def put_embedded(self, media_id: int, data: bytes) -> None:
        self.m_embedded[media_id] = data
        self.m_removed.discard(media_id)
        if all(media_entry.id != media_id for media_entry in self.m_bank.media):
            self.m_bank.media.append(SMediaEntry(media_id, 0, len(data)))

    def drop_embedded(self, media_id: int) -> None:
        self.m_embedded.pop(media_id, None)
        self.m_removed.add(media_id)

    def chunk(self, tag: bytes) -> bytes | None:
        """The data of a chunk of the bank (None when it has none)."""
        return next((chunk_entry.data for chunk_entry in self.m_bank.chunks if chunk_entry.tag == tag), None)

    def put_chunk(self, tag: bytes, data: bytes) -> None:
        """Replace the data of a chunk the bank has."""
        self.m_bank.chunks = [
            bnk.SChunk(chunk_entry.tag, data) if chunk_entry.tag == tag else chunk_entry
            for chunk_entry in self.m_bank.chunks
        ]

    def put_loose(self, media_id: int, data: bytes, language: bool) -> None:
        self.m_loose[media_id] = SLoose(data, language)

    # the stack
    def _state(self) -> _SState:
        return _SState(
            list(self.m_bank.hirc),
            [replace(media_entry) for media_entry in self.m_bank.media],
            dict(self.m_embedded),
            set(self.m_removed),
            dict(self.m_loose),
            list(self.m_bank.chunks),
        )

    def _restore(self, state: _SState) -> None:
        self.m_bank.hirc, self.m_bank.media = list(state.hirc), [replace(media_entry) for media_entry in state.media]
        self.m_embedded, self.m_removed, self.m_loose = dict(state.embedded), set(state.removed), dict(state.loose)
        self.m_bank.chunks = list(state.chunks)
        self._m_graph = self._m_index = None

    def do(self, name: str, **args):
        """Run the edit ``name``; returns what it returns (the ids of an object it made). The bank is as it was when
        it raises an ``CEditError``."""
        edit = g_edits.get(name)
        if edit is None:
            raise CEditError(tr("sound.no_edit_name", name=repr(name)))
        before = self._state()
        try:
            result = edit(self, **args)
        except Exception as exc:  # any failure: the half-made edit must not stay in the bank
            self._restore(before)
            if isinstance(exc, CEditError):
                raise
            raise CEditError(str(exc)) from exc
        self._m_undo.append((name, before))
        self._m_redo.clear()
        self.m_log.append((name, args))
        return result

    def undo(self) -> str:
        """Take back the last edit; its name ("" when there is none)."""
        if not self._m_undo:
            return ""
        name, before = self._m_undo.pop()
        args = self.m_log.pop()[1]
        self._m_redo.append((name, args, self._state()))
        self._restore(before)
        return name

    def redo(self) -> str:
        if not self._m_redo:
            return ""
        name, args, after = self._m_redo.pop()
        self._m_undo.append((name, self._state()))
        self.m_log.append((name, args))
        self._restore(after)
        return name

    @property
    def undo_name(self) -> str:
        return self._m_undo[-1][0] if self._m_undo else ""

    def replay(self, steps: list[tuple[str, dict]]) -> list[str]:
        """Make the edits of another copy of the bank to this one; the sentences of the ones that did not apply (an
        object this copy lacks)."""
        problems = []
        for name, args in steps:
            try:
                self.do(name, **args)
            except CEditError as exc:
                problems.append(f"{name}: {exc}")
        return problems

    # writing
    def to_bytes(self) -> bytes:
        """The bank as bytes: the original when nothing changed, else the objects (and the media, when they changed) again."""
        if not self.m_log and not self.m_embedded and not self.m_removed:
            return self.m_original
        bank = bnk.SSoundBank(list(self.m_bank.chunks), self.m_bank.hirc, list(self.m_bank.media))
        if self.m_embedded or self.m_removed:
            bank.media, blob = self._rebuilt_media()
            _put_chunks(bank, blob)
        return bnk.write(bank)

    def _rebuilt_media(self) -> tuple[list[SMediaEntry], bytes]:
        entries, blob = [], bytearray()
        self.m_unreadable = []
        for media_entry in self.m_bank.media:
            if media_entry.id in self.m_removed:
                continue
            data = self.media(media_entry.id)
            if data is None:
                if media_entry.size:
                    self.m_unreadable.append(media_entry.id)
                    continue
                data = b""  # an empty entry the shipped bank has: kept as it is
            blob += bytes(-len(blob) % ALIGN)
            entries.append(SMediaEntry(media_entry.id, len(blob), len(data)))
            blob += data
        return entries, bytes(blob)


def _put_chunks(bank: bnk.SSoundBank, blob: bytes) -> None:
    """The DIDX and DATA chunks of a bank that gained its first media file or changed them (before HIRC, as shipped)."""
    tags = [chunk_entry.tag for chunk_entry in bank.chunks]
    if b"DIDX" not in tags:
        at = tags.index(b"HIRC") if b"HIRC" in tags else len(tags)
        bank.chunks[at:at] = [bnk.SChunk(b"DIDX", b""), bnk.SChunk(b"DATA", b"")]
    bank.chunks = [
        bnk.SChunk(chunk_entry.tag, blob) if chunk_entry.tag == b"DATA" else chunk_entry for chunk_entry in bank.chunks
    ]
