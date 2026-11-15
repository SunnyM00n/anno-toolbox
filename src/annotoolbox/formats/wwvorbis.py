"""Wwise Vorbis (``.wem``, format tag 0xFFFF): libvorbis output rewrapped the way Wwise stores it.

Wwise's encoder is libvorbis; a file differs from an Ogg stream in three ways: the setup packet names its
codebooks by id in a library the runtime carries (``packed_codebooks_aoTuV_603.bin`` of ww2ogg) and drops
the unused fields, the audio packets drop the packet type and window flags and carry a 16-bit size, and
the container is RIFF with a ``vorb`` block. ``encode`` makes a file from PCM through ffmpeg's libvorbis;
``to_ogg`` is the way back, used to check a file by decoding it (docs/sounds.md, "The building sounds").
"""

from __future__ import annotations

import struct
import subprocess
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import buildcache
from ..infra.tools import NO_WINDOW, find_tool
from . import riff

CACHE = "vorbis"  # buildcache namespace: ffmpeg's Ogg Vorbis by the PCM, the settings and the ffmpeg build

LIBRARY_FILE = "packed_codebooks_aoTuV_603.bin"
FMT_TAG = 0xFFFF
VORB_SIZE = 0x2A  # the vorb block of the 0x42-byte format chunk


class WemError(ValueError):
    """The file is not a Wwise Vorbis file this module reads, or the codebooks do not fit the library."""


def ilog(v: int) -> int:
    return v.bit_length()


class BitReader:
    """Vorbis bit order: least significant bit first."""

    def __init__(self, data: bytes) -> None:
        self.value = int.from_bytes(data, "little")
        self.size = len(data) * 8
        self.pos = 0

    def read(self, n: int) -> int:
        if self.pos + n > self.size:
            raise WemError("setup data ends early")
        v = (self.value >> self.pos) & ((1 << n) - 1)
        self.pos += n
        return v

    def left(self) -> int:
        return self.size - self.pos


class BitWriter:
    def __init__(self) -> None:
        self.value = 0
        self.size = 0

    def write(self, v: int, n: int) -> None:
        self.value |= (v & ((1 << n) - 1)) << self.size
        self.size += n

    def tobytes(self) -> bytes:
        return self.value.to_bytes((self.size + 7) // 8, "little")


# codebooks


def quant_values(entries: int, dims: int) -> int:
    """Vorbis ``lookup1_values``: the largest integer v with v ** dims <= entries."""
    v = int(round(entries ** (1.0 / dims)))
    while v**dims > entries:
        v -= 1
    while (v + 1) ** dims <= entries:
        v += 1
    return v


@dataclass(frozen=True)
class Book:
    """A codebook in the form both containers share: ``lengths`` has 0 for an unused entry."""

    dims: int
    entries: int
    lengths: tuple[int, ...]
    lookup: int  # 0 none, 1 lattice, 2 per-entry vectors
    minimum: int = 0  # float32 bit patterns, as stored
    delta: int = 0
    value_bits: int = 0
    sequence: int = 0
    values: tuple[int, ...] = ()


def read_lengths(r: BitReader, entries: int, std: bool) -> tuple[int, ...]:
    lengths: list[int] = []
    if r.read(1):  # ordered
        length = r.read(5) + 1
        while len(lengths) < entries:
            lengths += [length] * r.read(ilog(entries - len(lengths)))
            length += 1
        return tuple(lengths)
    if std:
        sparse = r.read(1)
        width = 5
    else:
        width = r.read(3)
        sparse = r.read(1)
    return tuple((r.read(width) + 1) if (not sparse or r.read(1)) else 0 for _ in range(entries))


def read_std_book(r: BitReader) -> Book:
    if r.read(24) != 0x564342:
        raise WemError("codebook sync pattern missing")
    dims, entries = r.read(16), r.read(24)
    lengths = read_lengths(r, entries, True)
    lookup = r.read(4)
    if lookup not in (1, 2):
        return Book(dims, entries, lengths, 0)
    mn, delta, bits, seq = r.read(32), r.read(32), r.read(4) + 1, r.read(1)
    count = quant_values(entries, dims) if lookup == 1 else entries * dims
    return Book(dims, entries, lengths, lookup, mn, delta, bits, seq, tuple(r.read(bits) for _ in range(count)))


def write_std_book(w: BitWriter, b: Book) -> None:
    w.write(0x564342, 24)
    w.write(b.dims, 16)
    w.write(b.entries, 24)
    w.write(0, 1)  # unordered
    sparse = 0 in b.lengths
    w.write(int(sparse), 1)
    for n in b.lengths:
        if sparse:
            w.write(int(n > 0), 1)
        if n:
            w.write(n - 1, 5)
    w.write(b.lookup, 4)
    if b.lookup:
        w.write(b.minimum, 32)
        w.write(b.delta, 32)
        w.write(b.value_bits - 1, 4)
        w.write(b.sequence, 1)
        for v in b.values:
            w.write(v, b.value_bits)


class Library:
    """ww2ogg's packed codebook library: the books the Wwise runtime knows, by id."""

    def __init__(self, data: bytes) -> None:
        table = struct.unpack_from("<I", data, len(data) - 4)[0]
        count = (len(data) - table) // 4 - 1
        self.data = data
        self.offsets = struct.unpack_from(f"<{count + 1}I", data, table)
        self.count = count
        self._ids: dict[Book, int] | None = None

    @classmethod
    def find(cls) -> Library | None:
        path = find_tool("wwcodebooks")
        return cls(path.read_bytes()) if path else None

    def book(self, i: int) -> Book:
        r = BitReader(self.data[self.offsets[i] : self.offsets[i + 1]])
        dims, entries = r.read(4), r.read(14)
        lengths = read_lengths(r, entries, False)
        if not r.read(1):
            return Book(dims, entries, lengths, 0)
        mn, delta, bits, seq = r.read(32), r.read(32), r.read(4) + 1, r.read(1)
        values = tuple(r.read(bits) for _ in range(quant_values(entries, dims)))
        return Book(dims, entries, lengths, 1, mn, delta, bits, seq, values)

    def id_of(self, b: Book) -> int:
        if self._ids is None:  # built whole, then published: encoder threads share one library
            ids: dict[Book, int] = {}
            for i in range(self.count):
                ids.setdefault(self.book(i), i)
            self._ids = ids
        try:
            return self._ids[b]
        except KeyError:
            raise WemError(f"a codebook ({b.dims} dimensions, {b.entries} entries) is not in the library") from None


# setup packet


@dataclass
class Floor:
    partition_class: list[int]
    classes: list[tuple[int, int, int, list[int]]]  # dimensions, subclass bits, master book, sub books (-1: none)
    multiplier: int
    range_bits: int
    xlist: list[int]


@dataclass
class Residue:
    kind: int
    begin: int
    end: int
    partition_size: int
    classbook: int
    cascade: list[int]
    books: list[list[int]]  # per class, per pass: book or -1


@dataclass
class Mapping:
    submaps: int
    coupling: list[tuple[int, int]]
    mux: list[int]
    submap: list[tuple[int, int, int]]  # time, floor, residue


@dataclass
class Setup:
    channels: int
    books: list[Book] = field(default_factory=list)
    floors: list[Floor] = field(default_factory=list)
    residues: list[Residue] = field(default_factory=list)
    mappings: list[Mapping] = field(default_factory=list)
    modes: list[tuple[int, int]] = field(default_factory=list)  # blockflag, mapping

    @property
    def mode_bits(self) -> int:
        return ilog(len(self.modes) - 1)


def _read_floor(r: BitReader) -> Floor:
    parts = r.read(5)
    classes_of = [r.read(4) for _ in range(parts)]
    classes = []
    for _ in range(max(classes_of) + 1 if classes_of else 0):
        dim, sub = r.read(3) + 1, r.read(2)
        master = r.read(8) if sub else 0
        classes.append((dim, sub, master, [r.read(8) - 1 for _ in range(1 << sub)]))
    mult, bits = r.read(2) + 1, r.read(4)
    xs = [r.read(bits) for c in classes_of for _ in range(classes[c][0])]
    return Floor(classes_of, classes, mult, bits, xs)


def _write_floor(w: BitWriter, f: Floor) -> None:
    w.write(len(f.partition_class), 5)
    for c in f.partition_class:
        w.write(c, 4)
    for dim, sub, master, books in f.classes:
        w.write(dim - 1, 3)
        w.write(sub, 2)
        if sub:
            w.write(master, 8)
        for b in books:
            w.write(b + 1, 8)
    w.write(f.multiplier - 1, 2)
    w.write(f.range_bits, 4)
    for x in f.xlist:
        w.write(x, f.range_bits)


def _read_residue(r: BitReader, kind: int) -> Residue:
    begin, end, size, count, classbook = r.read(24), r.read(24), r.read(24) + 1, r.read(6) + 1, r.read(8)
    cascade = []
    for _ in range(count):
        low = r.read(3)
        cascade.append(low | (r.read(5) << 3 if r.read(1) else 0))
    books = [[r.read(8) if c & (1 << k) else -1 for k in range(8)] for c in cascade]
    return Residue(kind, begin, end, size, classbook, cascade, books)


def _write_residue(w: BitWriter, res: Residue, kind_bits: int) -> None:
    w.write(res.kind, kind_bits)
    w.write(res.begin, 24)
    w.write(res.end, 24)
    w.write(res.partition_size - 1, 24)
    w.write(len(res.cascade) - 1, 6)
    w.write(res.classbook, 8)
    for c in res.cascade:
        w.write(c & 7, 3)
        w.write(int(c > 7), 1)
        if c > 7:
            w.write(c >> 3, 5)
    for c, row in zip(res.cascade, res.books, strict=True):
        for k in range(8):
            if c & (1 << k):
                w.write(row[k], 8)


def _read_mapping(r: BitReader, channels: int) -> Mapping:
    submaps = r.read(4) + 1 if r.read(1) else 1
    coupling = []
    if r.read(1):
        bits = ilog(channels - 1)
        coupling = [(r.read(bits), r.read(bits)) for _ in range(r.read(8) + 1)]
    if r.read(2):
        raise WemError("reserved mapping bits are set")
    mux = [r.read(4) for _ in range(channels)] if submaps > 1 else []
    return Mapping(submaps, coupling, mux, [(r.read(8), r.read(8), r.read(8)) for _ in range(submaps)])


def _write_mapping(w: BitWriter, m: Mapping, channels: int) -> None:
    w.write(int(m.submaps > 1), 1)
    if m.submaps > 1:
        w.write(m.submaps - 1, 4)
    w.write(int(bool(m.coupling)), 1)
    if m.coupling:
        w.write(len(m.coupling) - 1, 8)
        for mag, ang in m.coupling:
            w.write(mag, ilog(channels - 1))
            w.write(ang, ilog(channels - 1))
    w.write(0, 2)
    if m.submaps > 1:
        for c in m.mux:
            w.write(c, 4)
    for t, f, r in m.submap:
        w.write(t, 8)
        w.write(f, 8)
        w.write(r, 8)


def parse_std_setup(packet: bytes, channels: int) -> Setup:
    """The Ogg Vorbis setup header packet (type 5)."""
    if packet[:7] != b"\x05vorbis":
        raise WemError("not a Vorbis setup header")
    r = BitReader(packet[7:])
    s = Setup(channels)
    s.books = [read_std_book(r) for _ in range(r.read(8) + 1)]
    for _ in range(r.read(6) + 1):
        if r.read(16):
            raise WemError("time domain transforms are not used")
    for _ in range(r.read(6) + 1):
        if r.read(16) != 1:
            raise WemError("only floor type 1 is read")
        s.floors.append(_read_floor(r))
    for _ in range(r.read(6) + 1):
        kind = r.read(16)
        if kind > 2:
            raise WemError("unknown residue type")
        s.residues.append(_read_residue(r, kind))
    for _ in range(r.read(6) + 1):
        if r.read(16):
            raise WemError("unknown mapping type")
        s.mappings.append(_read_mapping(r, channels))
    for _ in range(r.read(6) + 1):
        flag = r.read(1)
        if r.read(16) or r.read(16):
            raise WemError("window or transform type is not 0")
        s.modes.append((flag, r.read(8)))
    return s


def write_std_setup(s: Setup) -> bytes:
    w = BitWriter()
    w.write(len(s.books) - 1, 8)
    for b in s.books:
        write_std_book(w, b)
    w.write(0, 6)  # one time domain transform: 0
    w.write(0, 16)
    w.write(len(s.floors) - 1, 6)
    for f in s.floors:
        w.write(1, 16)
        _write_floor(w, f)
    w.write(len(s.residues) - 1, 6)
    for res in s.residues:
        _write_residue(w, res, 16)
    w.write(len(s.mappings) - 1, 6)
    for m in s.mappings:
        w.write(0, 16)
        _write_mapping(w, m, s.channels)
    w.write(len(s.modes) - 1, 6)
    for flag, mapping in s.modes:
        w.write(flag, 1)
        w.write(0, 32)
        w.write(mapping, 8)
    w.write(1, 1)  # framing
    return b"\x05vorbis" + w.tobytes()


def write_wwise_setup(s: Setup, library: Library) -> bytes:
    """The setup packet as Wwise stores it: codebooks by library id, residue kinds in 2 bits, no framing."""
    w = BitWriter()
    w.write(len(s.books) - 1, 8)
    for b in s.books:
        w.write(library.id_of(b), 10)
    w.write(len(s.floors) - 1, 6)
    for f in s.floors:
        _write_floor(w, f)
    w.write(len(s.residues) - 1, 6)
    for res in s.residues:
        _write_residue(w, res, 2)
    w.write(len(s.mappings) - 1, 6)
    for m in s.mappings:
        _write_mapping(w, m, s.channels)
    w.write(len(s.modes) - 1, 6)
    for flag, mapping in s.modes:
        w.write(flag, 1)
        w.write(mapping, 8)
    return w.tobytes()


def parse_wwise_setup(body: bytes, channels: int, library: Library) -> Setup:
    r = BitReader(body)
    s = Setup(channels)
    s.books = [library.book(r.read(10)) for _ in range(r.read(8) + 1)]
    s.floors = [_read_floor(r) for _ in range(r.read(6) + 1)]
    s.residues = [_read_residue(r, r.read(2)) for _ in range(r.read(6) + 1)]
    s.mappings = [_read_mapping(r, channels) for _ in range(r.read(6) + 1)]
    s.modes = [(r.read(1), r.read(8)) for _ in range(r.read(6) + 1)]
    if r.left() >= 8:
        raise WemError("the setup packet has data left over")
    return s


# the file


@dataclass
class Wem:
    channels: int
    rate: int
    samples: int
    block_pows: tuple[int, int]
    setup: bytes  # the setup packet body
    packets: list[bytes]  # audio packets as stored (size header removed)


def parse_wem(data: bytes) -> Wem:
    try:
        found = riff.chunks(data)
    except ValueError as exc:
        raise WemError(str(exc)) from exc
    fmt, payload = found.get(b"fmt "), found.get(b"data")
    if fmt is None or payload is None:
        raise WemError("fmt or data chunk missing")
    tag, channels, rate = struct.unpack_from("<HHI", fmt, 0)
    if tag != FMT_TAG or len(fmt) != 18 + 6 + VORB_SIZE:
        raise WemError("not a Wwise Vorbis file of the 0x42-byte form")
    vorb = fmt[24:]
    samples, _mod, _size, _x, setup_at, audio_at = struct.unpack_from("<6I", vorb, 0)
    sz = struct.unpack_from("<H", payload, setup_at)[0]
    packets, pos = [], audio_at
    while pos + 2 <= len(payload):
        n = struct.unpack_from("<H", payload, pos)[0]
        packets.append(payload[pos + 2 : pos + 2 + n])
        pos += 2 + n
    return Wem(channels, rate, samples, (vorb[0x28], vorb[0x29]), payload[setup_at + 2 : setup_at + 2 + sz], packets)


def prefetch_size(data: bytes, audio_bytes: int) -> int:
    """How many leading bytes of a Wwise Vorbis file a bank holds as the prefetch of a streamed source: everything up
    to the end of the first audio packet at which at least ``audio_bytes`` of audio are in (the shipped speech cuts
    on a packet boundary the same way; docs/sound/banks-117.md "Streamed sources"). The whole file when it is shorter."""
    try:
        where = riff.spans(data)
    except ValueError as exc:
        raise WemError(str(exc)) from exc
    fmt_at, fmt_size = where.get(b"fmt ", (0, 0))
    fmt = data[fmt_at : fmt_at + fmt_size] if b"fmt " in where else None
    data_at, data_size = where.get(b"data", (None, 0))
    if fmt is None or data_at is None or len(fmt) != 18 + 6 + VORB_SIZE:
        raise WemError("not a Wwise Vorbis file of the 0x42-byte form")
    audio_at = struct.unpack_from("<6I", fmt, 24)[5]
    end, taken = data_at + audio_at, 0
    while taken < audio_bytes and end - data_at + 2 <= data_size:
        end += 2 + struct.unpack_from("<H", data, end)[0]
        taken = end - data_at - audio_at
    return min(end, len(data))


def _crc_table() -> list[int]:
    """Ogg's CRC-32 (polynomial 0x04C11DB7, no reflection, no final xor), by byte."""
    out = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) if r & 0x80000000 else (r << 1)
        out.append(r & 0xFFFFFFFF)
    return out


_CRC = _crc_table()


def _ogg_crc(page: bytes) -> int:
    crc = 0
    for b in page:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[(crc >> 24) ^ b]
    return crc


def ogg_pages(packets: list[tuple[bytes, int]], serial: int = 1) -> bytes:
    """Pages from (packet, granule) pairs: each of the three headers gets a page, the audio packets share
    pages of up to 255 segments."""
    out, seq, pending, segs, first = [], 0, [], [], True

    def flush(granule: int, last: bool) -> None:
        nonlocal seq, pending, segs, first
        flags = (2 if first else 0) | (4 if last else 0)
        head = b"OggS" + struct.pack("<BBqIII", 0, flags, granule, serial, seq, 0) + bytes([len(segs)]) + bytes(segs)
        page = head + b"".join(pending)
        crc = _ogg_crc(page)
        out.append(page[:22] + struct.pack("<I", crc) + page[26:])
        seq, pending, segs, first = seq + 1, [], [], False

    for i, (pkt, granule) in enumerate(packets):
        lacing = [255] * (len(pkt) // 255) + [len(pkt) % 255]
        if len(segs) + len(lacing) > 255:
            flush(packets[i - 1][1], False)
        pending.append(pkt)
        segs += lacing
        if i == len(packets) - 1 or len(segs) > 200 or i < 3:
            flush(granule, i == len(packets) - 1)
    return b"".join(out)


def _id_packet(channels: int, rate: int, pows: tuple[int, int]) -> bytes:
    return b"\x01vorbis" + struct.pack("<IBIiii", 0, channels, rate, 0, 0, 0) + bytes([pows[0] | (pows[1] << 4), 1])


def to_ogg(data: bytes, library: Library) -> bytes:
    """The Ogg Vorbis stream of a Wwise Vorbis file, to decode it with any Vorbis decoder."""
    wem = parse_wem(data)
    setup = parse_wwise_setup(wem.setup, wem.channels, library)
    flags = [flag for flag, _mapping in setup.modes]
    bits = setup.mode_bits
    modes = [pkt[0] & ((1 << bits) - 1) if pkt else 0 for pkt in wem.packets]
    out: list[tuple[bytes, int]] = [
        (_id_packet(wem.channels, wem.rate, wem.block_pows), 0),
        (b"\x03vorbis" + struct.pack("<I", 0) + struct.pack("<I", 0) + b"\x01", 0),
        (write_std_setup(setup), 0),
    ]
    sizes = [1 << p for p in wem.block_pows]
    prev_long, granule, prev_size = 0, 0, 0
    for i, pkt in enumerate(wem.packets):
        long = flags[modes[i]]
        v = int.from_bytes(pkt, "little")
        mode = v & ((1 << bits) - 1)
        rest = v >> bits
        if long:
            nxt = flags[modes[i + 1]] if i + 1 < len(modes) else 0
            head = (mode << 1) | (prev_long << (1 + bits)) | (nxt << (2 + bits))
            v = head | (rest << (3 + bits))
        else:
            v = (mode << 1) | (rest << (1 + bits))
        size = sizes[long]
        granule += 0 if i == 0 else (prev_size // 4 + size // 4)
        prev_size, prev_long = size, long
        last = i == len(wem.packets) - 1
        out.append((v.to_bytes((8 * len(pkt) + 3 + 7) // 8, "little"), min(granule, wem.samples) if last else granule))
    return ogg_pages(out)


def decode(data: bytes, library: Library, ffmpeg: Path) -> tuple[bytes, int, int]:
    """PCM (16 bit, interleaved) of a Wwise Vorbis file through ffmpeg: (pcm, channels, rate)."""
    wem = parse_wem(data)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.ogg"
        src.write_bytes(to_ogg(data, library))
        done = subprocess.run(
            [str(ffmpeg), "-loglevel", "error", "-i", str(src), "-f", "s16le", "-"],
            capture_output=True,
            check=False,
            **NO_WINDOW,
        )
    if done.returncode:
        raise WemError(done.stderr.decode("utf-8", "replace").strip())
    return done.stdout, wem.channels, wem.rate


# encoder

RATE = 48000  # every shipped sound effect is 48 kHz; the decoder-size table below is known for those setups
QUALITY = 2  # libvorbis -q: the book set of the shipped "B" setup (35 books mono); 0 is the "A" setup
CHANNEL_CONFIG = {1: 0x4101, 2: 0x3102}  # the u32 after the format chunk's cbSize and a zero word (112's files: 4, 3)
# (channels, books, crc32 of the library ids) -> the two decoder sizes at vorb +0x1C and +0x20 of the shipped
# files with the same books (docs/sounds.md "The building sounds"); libvorbis' even qualities make these book sets
DECODER_SIZES = {
    (1, 32, 3345423403): (17892, 18292),
    (1, 35, 3193871255): (16192, 16616),
    (2, 34, 2675146631): (15116, 15532),
    (2, 38, 2021277508): (14316, 14764),
    (2, 42, 709354434): (16080, 16560),
}


def decoder_sizes(setup: Setup, library: Library) -> tuple[int, int]:
    ids = [library.id_of(b) for b in setup.books]
    key = (setup.channels, len(ids), zlib.crc32(struct.pack(f"<{len(ids)}H", *ids)))
    try:
        return DECODER_SIZES[key]
    except KeyError:
        raise WemError(f"no shipped file uses this codebook set {key}: its decoder sizes are unknown") from None


def ogg_packets(data: bytes) -> tuple[list[bytes], int]:
    """The packets of an Ogg stream and the granule position of its last page."""
    packets, current, pos, granule = [], b"", 0, 0
    while pos + 27 <= len(data):
        if data[pos : pos + 4] != b"OggS":
            raise WemError("not an Ogg stream")
        granule = struct.unpack_from("<q", data, pos + 6)[0]
        count = data[pos + 26]
        lacing = data[pos + 27 : pos + 27 + count]
        pos += 27 + count
        for n in lacing:
            current += data[pos : pos + n]
            pos += n
            if n < 255:
                packets.append(current)
                current = b""
    return packets, granule


def vorbis_ogg(pcm: bytes, channels: int, rate: int, ffmpeg: Path, quality: int = QUALITY) -> bytes:
    """16 bit PCM as an Ogg Vorbis stream at ``RATE`` (ffmpeg's libvorbis)."""
    key = buildcache.digest("ogg", buildcache.file_digest(ffmpeg), RATE, quality, rate, channels, pcm)
    if (hit := buildcache.get(CACHE, key)) is not None:
        return hit
    args = [str(ffmpeg), "-loglevel", "error", "-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-i", "pipe:0"]
    args += ["-ar", str(RATE), "-c:a", "libvorbis", "-q:a", str(quality), "-f", "ogg", "pipe:1"]
    done = subprocess.run(args, input=pcm, capture_output=True, check=False, **NO_WINDOW)
    if done.returncode or not done.stdout:
        raise WemError("ffmpeg: " + done.stderr.decode("utf-8", "replace").strip()[:200])
    buildcache.put(CACHE, key, done.stdout)
    return done.stdout


def _wwise_packets(packets: list[bytes], setup: Setup, pows: tuple[int, int]) -> tuple[list[bytes], int]:
    """The audio packets in Wwise's form (a 16-bit size, no packet type and window flags) and the number of
    samples they decode to."""
    flags, bits = [flag for flag, _mapping in setup.modes], setup.mode_bits
    sizes = [1 << p for p in pows]
    stored, decoded, previous = [], 0, 0
    for i, pkt in enumerate(packets):
        v = int.from_bytes(pkt, "little")
        if v & 1:
            raise WemError("an audio packet has the header flag set")
        mode = (v >> 1) & ((1 << bits) - 1)
        long = flags[mode]
        v = mode | ((v >> (1 + bits + (2 if long else 0))) << bits)
        stored.append(struct.pack("<H", len(pkt)) + v.to_bytes(len(pkt), "little"))
        decoded += 0 if i == 0 else previous // 4 + sizes[long] // 4
        previous = sizes[long]
    return stored, decoded


def encode(
    pcm: bytes, channels: int, rate: int, library: Library, ffmpeg: Path, config: dict[int, int],
    quality: int = QUALITY,
) -> bytes:  # fmt: skip
    """A Wwise Vorbis file (48 kHz) of 16 bit interleaved PCM; ``config``: the channel word by channel count of the
    bank version the file is for (``bnkgraph.layout(version).vorbis_channels``)."""
    if channels not in config:
        raise WemError(f"{channels} channels: only mono and stereo are written")
    packets, samples = ogg_packets(vorbis_ogg(pcm, channels, rate, ffmpeg, quality))
    if len(packets) < 4 or samples <= 0:
        raise WemError("ffmpeg wrote no audio")
    setup = parse_std_setup(packets[2], channels)
    body = write_wwise_setup(setup, library)
    f1c, f20 = decoder_sizes(setup, library)
    pows = (packets[0][28] & 15, packets[0][28] >> 4)
    stored, decoded = _wwise_packets(packets[3:], setup, pows)
    extra = decoded - samples
    if not 0 <= extra < 1 << 16:
        raise WemError(f"the stream decodes {decoded} samples for a length of {samples}")
    setup_packet = struct.pack("<H", len(body)) + body
    data = setup_packet + b"".join(stored)
    longest = max(len(p) for p in packets[3:])
    avg = round(len(data) * RATE / samples)
    vorb = struct.pack(
        "<IIIHHIIHHIIIBB",
        samples,
        len(setup_packet),
        len(data),
        0,
        extra,
        0,
        len(setup_packet),
        longest,
        extra,
        f1c,
        f20,
        zlib.crc32(body),
        *pows,
    )
    fmt = struct.pack("<HHIIHHHHI", FMT_TAG, channels, RATE, avg, 0, 0, 0x30, 0, config[channels]) + vorb
    body_chunks = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body_chunks) + (len(data) & 1)) + body_chunks + b"\0" * (len(data) & 1)
