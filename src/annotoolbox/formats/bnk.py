"""Wwise sound bank (``.bnk``) container: chunks, the object list and the embedded media index, read and written.

A bank is a flat sequence of chunks (four-char tag, u32 size, payload): ``BKHD`` header, ``DIDX`` / ``DATA`` the
embedded media index and blob, ``HIRC`` the objects (``SHircObject``: type, id, raw payload; ``bnkmusic`` and
``bnksfx`` read the payloads), ``STID`` names, others kept opaque. Objects can be edited, added or removed and
everything else is written back untouched; an unmodified bank round-trips byte for byte (``tests/test_bnk.py``).
"""

from __future__ import annotations

import array
import struct
from dataclasses import dataclass, field

from . import riff
from .fnv import fnv

PCM_PLUGIN = 0x00010001  # AKCODECID_PCM << 16 | codec plugin type: the source plugin of a PCM .wem
VORBIS_PLUGIN = 0x00040001  # the same for Vorbis: what the shipped sound effects use
EVENT_HEADER = 7  # bytes before the action count of an Event object (all zero in the shipped banks, version 172)

HIRC_TYPE_NAMES = {
    1: "Settings",
    2: "Sound",
    3: "Action",
    4: "Event",
    5: "RanSeqCntr",
    6: "SwitchCntr",
    7: "ActorMixer",
    8: "Bus",
    9: "LayerCntr",
    10: "MusicSegment",
    11: "MusicTrack",
    12: "MusicSwitchCntr",
    13: "MusicRanSeqCntr",
    14: "Attenuation",
    15: "DialogueEvent",
    16: "FxShareSet",
    17: "FxCustom",
    18: "AuxBus",
    19: "LFO",
    20: "Envelope",
    21: "AudioDevice",
    22: "TimeMod",
}


class CBnkError(ValueError):
    """A sound bank that is empty, cut short or not shaped as a bank."""


@dataclass
class SChunk:
    tag: bytes  # 4 bytes
    data: bytes


@dataclass
class SHircObject:
    type: int
    id: int
    payload: bytes

    @property
    def type_name(self) -> str:
        return HIRC_TYPE_NAMES.get(self.type, str(self.type))


@dataclass
class SMediaEntry:
    id: int
    offset: int  # into DATA
    size: int


@dataclass
class SSoundBank:
    chunks: list[SChunk] = field(default_factory=list)  # in file order; HIRC/DIDX are placeholders re-rendered on write
    hirc: list[SHircObject] = field(default_factory=list)
    media: list[SMediaEntry] = field(default_factory=list)

    # Header
    @property
    def header(self) -> SChunk:
        for chunk in self.chunks:
            if chunk.tag == b"BKHD":
                return chunk
        raise CBnkError("the bank has no BKHD header chunk")

    @property
    def version(self) -> int:
        return struct.unpack_from("<I", self.header.data, 0)[0]

    @property
    def bank_id(self) -> int:
        return struct.unpack_from("<I", self.header.data, 4)[0]

    # Lookups
    def objects_of(self, type_id: int) -> list[SHircObject]:
        return [hirc_object for hirc_object in self.hirc if hirc_object.type == type_id]

    def find(self, obj_id: int) -> SHircObject | None:
        for hirc_object in self.hirc:
            if hirc_object.id == obj_id:
                return hirc_object
        return None


def parse(data: bytes) -> SSoundBank:
    bank = SSoundBank()
    offset = 0
    while offset < len(data):
        if offset + 8 > len(data):
            raise CBnkError(f"truncated chunk header at {offset}")
        tag = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        payload = data[offset + 8 : offset + 8 + size]
        if len(payload) != size:
            raise CBnkError(f"chunk {tag!r} at {offset} runs past the end of the file")
        bank.chunks.append(SChunk(tag, payload))
        offset += 8 + size
    if len(bank.header.data) < 8:
        raise CBnkError(f"BKHD of {len(bank.header.data)} bytes is shorter than version and bank id")
    for chunk in bank.chunks:
        if chunk.tag == b"HIRC":
            bank.hirc = _parse_hirc(chunk.data)
        elif chunk.tag == b"DIDX":
            if len(chunk.data) % 12:
                raise CBnkError(f"DIDX of {len(chunk.data)} bytes is not whole 12-byte entries")
            bank.media = [
                SMediaEntry(*struct.unpack_from("<III", chunk.data, i)) for i in range(0, len(chunk.data), 12)
            ]
    return bank


def _parse_hirc(data: bytes) -> list[SHircObject]:
    if len(data) < 4:
        raise CBnkError("HIRC is shorter than its object count")
    count = struct.unpack_from("<I", data, 0)[0]
    out: list[SHircObject] = []
    offset = 4
    for _ in range(count):
        if offset + 9 > len(data):
            raise CBnkError(f"HIRC ends inside object {len(out)} of {count}")
        type_id = data[offset]
        size = struct.unpack_from("<I", data, offset + 1)[0]
        obj_id = struct.unpack_from("<I", data, offset + 5)[0]
        payload = data[offset + 9 : offset + 5 + size]
        if len(payload) != size - 4:
            raise CBnkError(f"HIRC object {obj_id} (type {type_id}) is truncated")
        out.append(SHircObject(type_id, obj_id, payload))
        offset += 5 + size
    if offset != len(data):
        raise CBnkError(f"HIRC has {len(data) - offset} trailing bytes")
    return out


def render_hirc(objects: list[SHircObject]) -> bytes:
    parts = [struct.pack("<I", len(objects))]
    for hirc_object in objects:
        parts.append(struct.pack("<BII", hirc_object.type, len(hirc_object.payload) + 4, hirc_object.id))
        parts.append(hirc_object.payload)
    return b"".join(parts)


def render_didx(media: list[SMediaEntry]) -> bytes:
    return b"".join(struct.pack("<III", entry.id, entry.offset, entry.size) for entry in media)


def write(bank: SSoundBank) -> bytes:
    parts = []
    for chunk in bank.chunks:
        if chunk.tag == b"HIRC":
            data = render_hirc(bank.hirc)
        elif chunk.tag == b"DIDX":
            data = render_didx(bank.media)
        else:
            data = chunk.data
        parts.append(chunk.tag + struct.pack("<I", len(data)) + data)
    return b"".join(parts)


# ids and media


def fnv1_32(text: str) -> int:
    """Wwise's id of a name: FNV-1 (32 bit) over the lower-cased UTF-8."""
    return fnv(text.lower().encode("utf-8"), is_xor_first=False)


class CIdPool:
    """New object ids: a name's hash, the next free number when that is taken."""

    def __init__(self, taken: set[int]) -> None:
        self.m_taken = set(taken)

    def new(self, name: str) -> int:
        object_id = fnv1_32(name) or 1
        while object_id in self.m_taken or object_id == 0:
            object_id = (object_id + 1) & 0xFFFFFFFF
        self.m_taken.add(object_id)
        return object_id


def pcm_wem(samples: array.array, rate: int, channels: int) -> bytes:
    """A PCM .wem in the form the mods' files have (Wwise's own 24-byte format chunk); 16 bit samples."""
    data = samples.tobytes()
    if channels not in (1, 2):
        raise CBnkError(f"a PCM .wem of {channels} channels: only mono and stereo have a channel mask here")
    mask = {1: 0x4, 2: 0x3}[channels]
    format_chunk = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * 2 * channels, 2 * channels, 16, 6, 16, mask)
    return riff.build([(b"fmt ", format_chunk), (b"data", data)])


def read_pcm_wem(data: bytes) -> tuple[array.array, int, int]:
    """The 16-bit samples, rate and channel count of a PCM .wem (format tag 0xFFFE: ``pcm_wem``'s form, and the
    loose sounds 2205 ships with their cue, label and padding chunks); a Vorbis one is ``wwvorbis``'s."""
    found = riff.chunks(data)
    format_chunk, payload = found.get(b"fmt "), found.get(b"data")
    if format_chunk is None or payload is None or len(format_chunk) < 16:
        raise CBnkError("fmt or data chunk missing")
    tag, channels, rate, _rate_bytes, _align, bits = struct.unpack_from("<HHIIHH", format_chunk)
    if tag != 0xFFFE or bits != 16:
        raise CBnkError(f"not a 16-bit PCM .wem (format {tag:#x}, {bits} bit)")
    samples = array.array("h")
    samples.frombytes(payload[: len(payload) // 2 * 2])
    return samples, rate, channels
