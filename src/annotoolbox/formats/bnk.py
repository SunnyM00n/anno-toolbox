"""Wwise sound bank (``.bnk``) container: chunks, the object list and the embedded media index, read and written.

A bank is a flat sequence of chunks (four-char tag, u32 size, payload): ``BKHD`` header, ``DIDX`` / ``DATA`` the
embedded media index and blob, ``HIRC`` the objects (``HircObject``: type, id, raw payload; ``bnkmusic`` and
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


@dataclass
class Chunk:
    tag: bytes  # 4 bytes
    data: bytes


@dataclass
class HircObject:
    type: int
    id: int
    payload: bytes

    @property
    def type_name(self) -> str:
        return HIRC_TYPE_NAMES.get(self.type, str(self.type))


@dataclass
class MediaEntry:
    id: int
    offset: int  # into DATA
    size: int


@dataclass
class SoundBank:
    chunks: list[Chunk] = field(default_factory=list)  # in file order; HIRC/DIDX are placeholders re-rendered on write
    hirc: list[HircObject] = field(default_factory=list)
    media: list[MediaEntry] = field(default_factory=list)

    # Header
    @property
    def header(self) -> Chunk:
        return next(c for c in self.chunks if c.tag == b"BKHD")

    @property
    def version(self) -> int:
        return struct.unpack_from("<I", self.header.data, 0)[0]

    @property
    def bank_id(self) -> int:
        return struct.unpack_from("<I", self.header.data, 4)[0]

    # Lookups
    def objects_of(self, type_id: int) -> list[HircObject]:
        return [o for o in self.hirc if o.type == type_id]

    def find(self, obj_id: int) -> HircObject | None:
        for o in self.hirc:
            if o.id == obj_id:
                return o
        return None


def parse(data: bytes) -> SoundBank:
    bank = SoundBank()
    off = 0
    while off < len(data):
        if off + 8 > len(data):
            raise ValueError(f"truncated chunk header at {off}")
        tag = data[off : off + 4]
        size = struct.unpack_from("<I", data, off + 4)[0]
        payload = data[off + 8 : off + 8 + size]
        if len(payload) != size:
            raise ValueError(f"chunk {tag!r} at {off} runs past the end of the file")
        bank.chunks.append(Chunk(tag, payload))
        off += 8 + size
    for c in bank.chunks:
        if c.tag == b"HIRC":
            bank.hirc = _parse_hirc(c.data)
        elif c.tag == b"DIDX":
            bank.media = [MediaEntry(*struct.unpack_from("<III", c.data, i)) for i in range(0, len(c.data), 12)]
    return bank


def _parse_hirc(data: bytes) -> list[HircObject]:
    count = struct.unpack_from("<I", data, 0)[0]
    out: list[HircObject] = []
    p = 4
    for _ in range(count):
        t = data[p]
        size = struct.unpack_from("<I", data, p + 1)[0]
        obj_id = struct.unpack_from("<I", data, p + 5)[0]
        payload = data[p + 9 : p + 5 + size]
        if len(payload) != size - 4:
            raise ValueError(f"HIRC object {obj_id} (type {t}) is truncated")
        out.append(HircObject(t, obj_id, payload))
        p += 5 + size
    if p != len(data):
        raise ValueError(f"HIRC has {len(data) - p} trailing bytes")
    return out


def render_hirc(objects: list[HircObject]) -> bytes:
    parts = [struct.pack("<I", len(objects))]
    for o in objects:
        parts.append(struct.pack("<BII", o.type, len(o.payload) + 4, o.id))
        parts.append(o.payload)
    return b"".join(parts)


def render_didx(media: list[MediaEntry]) -> bytes:
    return b"".join(struct.pack("<III", m.id, m.offset, m.size) for m in media)


def write(bank: SoundBank) -> bytes:
    parts = []
    for c in bank.chunks:
        if c.tag == b"HIRC":
            data = render_hirc(bank.hirc)
        elif c.tag == b"DIDX":
            data = render_didx(bank.media)
        else:
            data = c.data
        parts.append(c.tag + struct.pack("<I", len(data)) + data)
    return b"".join(parts)


# ids and media


def fnv1_32(text: str) -> int:
    """Wwise's id of a name: FNV-1 (32 bit) over the lower-cased UTF-8."""
    return fnv(text.lower().encode("utf-8"), xor_first=False)


class IdPool:
    """New object ids: a name's hash, the next free number when that is taken."""

    def __init__(self, taken: set[int]) -> None:
        self.taken = set(taken)

    def new(self, name: str) -> int:
        h = fnv1_32(name) or 1
        while h in self.taken or h == 0:
            h = (h + 1) & 0xFFFFFFFF
        self.taken.add(h)
        return h


def pcm_wem(samples: array.array, rate: int, channels: int) -> bytes:
    """A PCM .wem in the form the mods' files have (Wwise's own 24-byte format chunk); 16 bit samples."""
    data = samples.tobytes()
    mask = {1: 0x4, 2: 0x3}[channels]
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * 2 * channels, 2 * channels, 16, 6, 16, mask)
    return riff.build([(b"fmt ", fmt), (b"data", data)])


def read_pcm_wem(data: bytes) -> tuple[array.array, int, int]:
    """The 16-bit samples, rate and channel count of a PCM .wem (format tag 0xFFFE: ``pcm_wem``'s form, and the
    loose sounds 2205 ships with their cue, label and padding chunks); a Vorbis one is ``wwvorbis``'s."""
    found = riff.chunks(data)
    fmt, payload = found.get(b"fmt "), found.get(b"data")
    if fmt is None or payload is None or len(fmt) < 16:
        raise ValueError("fmt or data chunk missing")
    tag, channels, rate, _rate_bytes, _align, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag != 0xFFFE or bits != 16:
        raise ValueError(f"not a 16-bit PCM .wem (format {tag:#x}, {bits} bit)")
    samples = array.array("h")
    samples.frombytes(payload[: len(payload) // 2 * 2])
    return samples, rate, channels
