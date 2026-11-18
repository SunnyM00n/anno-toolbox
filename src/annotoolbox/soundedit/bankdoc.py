"""A Wwise bank open for editing: its objects and media, the edits made to them on an undo stack, the bank written back.

``BankDoc(data, path, info)`` is the entry: ``do(name, **args)`` runs one edit (``soundedit.edits``, ``audioedits``),
``undo`` / ``redo`` walk the stack, ``to_bytes()`` writes the bank (byte for byte when nothing was done), ``replay``
makes the same edits to another copy (a localised bank lives in every language folder). docs/sound-editor.md.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace

from ..exporting.banks import BankInfo
from ..formats import bnk, bnkgraph, bnkmusic, bnksfx
from ..formats.bnk import HircObject, MediaEntry
from ..infra.i18n import tr
from . import audioedits, curveedits, edits, groupedits, musicedits  # noqa: F401  # register the edits
from .registry import EDITS, EditError

ALIGN = bnksfx.MEDIA_ALIGN


@dataclass(frozen=True)
class Loose:
    """A streamed ``.wem`` an edit made: the file, and whether each language folder keeps its own copy of it."""

    data: bytes
    language: bool = False


@dataclass
class _State:
    hirc: list[HircObject]
    media: list[MediaEntry]
    embedded: dict[int, bytes]
    removed: set[int]
    loose: dict[int, Loose]
    chunks: list[bnk.Chunk]


class BankDoc:
    def __init__(self, data: bytes, path: str = "", info: BankInfo | None = None) -> None:
        self.original, self.path, self.info = data, path, info or BankInfo()
        self.bank = bnk.parse(data)
        self.version, self.family = self.bank.version, bnkgraph.family(self.bank.version)
        self.blob = next((c.data for c in self.bank.chunks if c.tag == b"DATA"), b"")
        self.embedded: dict[int, bytes] = {}  # media replaced or added by edits (the rest is read from the blob)
        self.removed: set[int] = set()
        self.loose: dict[int, Loose] = {}
        self.log: list[tuple[str, dict]] = []
        self._saved: list[tuple[str, dict]] = []  # the log as last kept in the project
        self.unreadable: list[int] = []  # media of the index the last write could not read (left out of the bank)
        self.pool = bnk.IdPool({o.id for o in self.bank.hirc} | {m.id for m in self.bank.media})
        self._graph: dict[int, bnkgraph.Node] | None = None
        self._index: dict[int, int] | None = None
        self._undo: list[tuple[str, _State]] = []
        self._redo: list[tuple[str, dict, _State]] = []

    # reading
    @property
    def graph(self) -> dict[int, bnkgraph.Node]:
        if self._graph is None:
            self._graph = bnkgraph.read_graph(self.bank.hirc, self.version)
        return self._graph

    def _at(self, obj_id: int) -> int | None:
        """Where an object sits in the list (None: the bank has none of that id)."""
        if self._index is None:
            self._index = {o.id: i for i, o in enumerate(self.bank.hirc)}
        return self._index.get(obj_id)

    def obj(self, obj_id: int) -> HircObject:
        at = self._at(obj_id)
        if at is None:
            raise EditError(tr("sound.bank_no_object", obj_id=obj_id))
        return self.bank.hirc[at]

    def kind_of(self, obj_id: int) -> int:
        """The type of an object of the bank (0 for an id that is none: a group of the state manager, say)."""
        at = self._at(obj_id)
        return self.bank.hirc[at].type if at is not None else 0

    def objects_of(self, type_id: int) -> list[HircObject]:
        return self.bank.objects_of(type_id)

    def media(self, media_id: int) -> bytes | None:
        """A media file of the bank itself (an edited one, else the original); None when the bank has none."""
        if media_id in self.embedded:
            return self.embedded[media_id]
        if media_id in self.removed:
            return None
        for m in self.bank.media:
            if m.id == media_id and m.size and m.offset + m.size <= len(self.blob):
                return self.blob[m.offset : m.offset + m.size]
        return None

    def media_users(self, media_id: int) -> list[int]:
        """The Sounds and the music tracks of the bank that play a media file."""
        out = []
        for o in self.bank.objects_of(bnkgraph.SOUND):
            node = self.graph.get(o.id)
            if node is not None and node.source is not None and node.source.media == media_id:
                out.append(o.id)
        for o in self.bank.objects_of(bnkmusic.MUSIC_TRACK):
            try:
                if any(s.source_id & 0xFFFFFFFF == media_id for s in bnkmusic.parse_track(o).sources):
                    out.append(o.id)
            except (ValueError, struct.error):
                continue
        return out

    @property
    def changed(self) -> bool:
        return bool(self.log)

    @property
    def unsaved(self) -> bool:
        """Whether the edits differ from the ones last kept (``mark_saved``); an undo back to them is no change."""
        return self.log != self._saved

    def mark_saved(self) -> None:
        self._saved = list(self.log)

    # changing: the edits' helpers
    def put(self, obj: HircObject) -> None:
        """Replace the object of the same id (its place in the list kept)."""
        self.obj(obj.id)
        self.bank.hirc[self._index[obj.id]] = obj
        self._graph = None

    def splice(self, obj: HircObject, start: int, end: int, body: bytes) -> None:
        """Replace the bytes ``start`` to ``end`` of an object's payload with ``body`` and put it back."""
        self.put(HircObject(obj.type, obj.id, obj.payload[:start] + body + obj.payload[end:]))

    def add(self, obj: HircObject) -> None:
        """Add an object the way the shipped banks order them: children and containers before actions and events."""
        if any(o.id == obj.id for o in self.bank.hirc):
            raise EditError(tr("sound.bank_object_already", id=obj.id))
        late = obj.type in (bnkgraph.ACTION, bnkgraph.EVENT)
        first_late = next(
            (i for i, o in enumerate(self.bank.hirc) if o.type in (bnkgraph.ACTION, bnkgraph.EVENT)), None
        )
        at = len(self.bank.hirc) if late or first_late is None else first_late
        self.bank.hirc.insert(at, obj)
        self.pool.taken.add(obj.id)
        self._index = self._graph = None

    def new_id(self, name: str) -> int:
        return self.pool.new(name)

    def put_embedded(self, media_id: int, data: bytes) -> None:
        self.embedded[media_id] = data
        self.removed.discard(media_id)
        if all(m.id != media_id for m in self.bank.media):
            self.bank.media.append(MediaEntry(media_id, 0, len(data)))

    def drop_embedded(self, media_id: int) -> None:
        self.embedded.pop(media_id, None)
        self.removed.add(media_id)

    def chunk(self, tag: bytes) -> bytes | None:
        """The data of a chunk of the bank (None when it has none)."""
        return next((c.data for c in self.bank.chunks if c.tag == tag), None)

    def put_chunk(self, tag: bytes, data: bytes) -> None:
        """Replace the data of a chunk the bank has."""
        self.bank.chunks = [bnk.Chunk(c.tag, data) if c.tag == tag else c for c in self.bank.chunks]

    def put_loose(self, media_id: int, data: bytes, language: bool) -> None:
        self.loose[media_id] = Loose(data, language)

    # the stack
    def _state(self) -> _State:
        return _State(
            list(self.bank.hirc),
            [replace(m) for m in self.bank.media],
            dict(self.embedded),
            set(self.removed),
            dict(self.loose),
            list(self.bank.chunks),
        )

    def _restore(self, s: _State) -> None:
        self.bank.hirc, self.bank.media = list(s.hirc), [replace(m) for m in s.media]
        self.embedded, self.removed, self.loose = dict(s.embedded), set(s.removed), dict(s.loose)
        self.bank.chunks = list(s.chunks)
        self._graph = self._index = None

    def do(self, name: str, **args):
        """Run the edit ``name``; returns what it returns (the ids of an object it made). The bank is as it was when
        it raises an ``EditError``."""
        fn = EDITS.get(name)
        if fn is None:
            raise EditError(tr("sound.no_edit_name", name=repr(name)))
        before = self._state()
        try:
            result = fn(self, **args)
        except Exception as exc:  # any failure: the half-made edit must not stay in the bank
            self._restore(before)
            raise exc if isinstance(exc, EditError) else EditError(str(exc)) from exc
        self._undo.append((name, before))
        self._redo.clear()
        self.log.append((name, args))
        return result

    def undo(self) -> str:
        """Take back the last edit; its name ("" when there is none)."""
        if not self._undo:
            return ""
        name, before = self._undo.pop()
        args = self.log.pop()[1]
        self._redo.append((name, args, self._state()))
        self._restore(before)
        return name

    def redo(self) -> str:
        if not self._redo:
            return ""
        name, args, after = self._redo.pop()
        self._undo.append((name, self._state()))
        self.log.append((name, args))
        self._restore(after)
        return name

    @property
    def undo_name(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def replay(self, steps: list[tuple[str, dict]]) -> list[str]:
        """Make the edits of another copy of the bank to this one; the sentences of the ones that did not apply (an
        object this copy lacks)."""
        problems = []
        for name, args in steps:
            try:
                self.do(name, **args)
            except EditError as exc:
                problems.append(f"{name}: {exc}")
        return problems

    # writing
    def to_bytes(self) -> bytes:
        """The bank as bytes: the original when nothing changed, else the objects (and the media, when they changed) again."""
        if not self.log and not self.embedded and not self.removed:
            return self.original
        bank = bnk.SoundBank(list(self.bank.chunks), self.bank.hirc, list(self.bank.media))
        if self.embedded or self.removed:
            bank.media, blob = self._rebuilt_media()
            _put_chunks(bank, blob)
        return bnk.write(bank)

    def _rebuilt_media(self) -> tuple[list[MediaEntry], bytes]:
        entries, blob = [], bytearray()
        self.unreadable = []
        for m in self.bank.media:
            if m.id in self.removed:
                continue
            data = self.media(m.id)
            if data is None:
                if m.size:
                    self.unreadable.append(m.id)
                    continue
                data = b""  # an empty entry the shipped bank has: kept as it is
            blob += bytes(-len(blob) % ALIGN)
            entries.append(MediaEntry(m.id, len(blob), len(data)))
            blob += data
        return entries, bytes(blob)


def _put_chunks(bank: bnk.SoundBank, blob: bytes) -> None:
    """The DIDX and DATA chunks of a bank that gained its first media file or changed them (before HIRC, as shipped)."""
    tags = [c.tag for c in bank.chunks]
    if b"DIDX" not in tags:
        at = tags.index(b"HIRC") if b"HIRC" in tags else len(tags)
        bank.chunks[at:at] = [bnk.Chunk(b"DIDX", b""), bnk.Chunk(b"DATA", b"")]
    bank.chunks = [bnk.Chunk(c.tag, blob) if c.tag == b"DATA" else c for c in bank.chunks]
