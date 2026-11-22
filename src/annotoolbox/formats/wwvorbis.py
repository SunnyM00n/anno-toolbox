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


class CWemError(ValueError):
    """The file is not a Wwise Vorbis file this module reads, or the codebooks do not fit the library."""


def ilog(value: int) -> int:
    return value.bit_length()


class CBitReader:
    """Vorbis bit order: least significant bit first."""

    def __init__(self, data: bytes) -> None:
        self.m_value = int.from_bytes(data, "little")
        self.m_size = len(data) * 8
        self.m_pos = 0

    def read(self, bit_count: int) -> int:
        if self.m_pos + bit_count > self.m_size:
            raise CWemError("setup data ends early")
        value = (self.m_value >> self.m_pos) & ((1 << bit_count) - 1)
        self.m_pos += bit_count
        return value

    def left(self) -> int:
        return self.m_size - self.m_pos


class CBitWriter:
    def __init__(self) -> None:
        self.m_value = 0
        self.m_size = 0

    def write(self, value: int, bit_count: int) -> None:
        self.m_value |= (value & ((1 << bit_count) - 1)) << self.m_size
        self.m_size += bit_count

    def tobytes(self) -> bytes:
        return self.m_value.to_bytes((self.m_size + 7) // 8, "little")


# codebooks


def quant_values(entries: int, dimensions: int) -> int:
    """Vorbis ``lookup1_values``: the largest integer v with v ** dimensions <= entries."""
    if dimensions < 1:
        raise CWemError("a codebook lattice needs at least one dimension")
    value = round(entries ** (1.0 / dimensions))
    while value**dimensions > entries:
        value -= 1
    while (value + 1) ** dimensions <= entries:
        value += 1
    return value


@dataclass(frozen=True)
class SBook:
    """A codebook in the form both containers share: ``lengths`` has 0 for an unused entry."""

    dimensions: int
    entries: int
    lengths: tuple[int, ...]
    lookup: int  # 0 none, 1 lattice, 2 per-entry vectors
    minimum: int = 0  # float32 bit patterns, as stored
    delta: int = 0
    value_bits: int = 0
    sequence: int = 0
    values: tuple[int, ...] = ()


def read_lengths(reader: CBitReader, entries: int, is_standard: bool) -> tuple[int, ...]:
    lengths: list[int] = []
    if reader.read(1):  # ordered
        length_bits = reader.read(5) + 1
        while len(lengths) < entries:
            lengths += [length_bits] * reader.read(ilog(entries - len(lengths)))
            length_bits += 1
        return tuple(lengths)
    if is_standard:
        sparse = reader.read(1)
        width_bits = 5
    else:
        width_bits = reader.read(3)
        sparse = reader.read(1)
    return tuple((reader.read(width_bits) + 1) if (not sparse or reader.read(1)) else 0 for _ in range(entries))


def read_std_book(reader: CBitReader) -> SBook:
    if reader.read(24) != 0x564342:
        raise CWemError("codebook sync pattern missing")
    dimensions, entries = reader.read(16), reader.read(24)
    lengths = read_lengths(reader, entries, True)
    lookup = reader.read(4)
    if lookup not in (1, 2):
        return SBook(dimensions, entries, lengths, 0)
    minimum, delta, bits, sequence = reader.read(32), reader.read(32), reader.read(4) + 1, reader.read(1)
    count = quant_values(entries, dimensions) if lookup == 1 else entries * dimensions
    return SBook(
        dimensions,
        entries,
        lengths,
        lookup,
        minimum,
        delta,
        bits,
        sequence,
        tuple(reader.read(bits) for _ in range(count)),
    )


def write_std_book(writer: CBitWriter, book: SBook) -> None:
    writer.write(0x564342, 24)
    writer.write(book.dimensions, 16)
    writer.write(book.entries, 24)
    writer.write(0, 1)  # unordered
    sparse = 0 in book.lengths
    writer.write(int(sparse), 1)
    for length_bits in book.lengths:
        if sparse:
            writer.write(int(length_bits > 0), 1)
        if length_bits:
            writer.write(length_bits - 1, 5)
    writer.write(book.lookup, 4)
    if book.lookup:
        writer.write(book.minimum, 32)
        writer.write(book.delta, 32)
        writer.write(book.value_bits - 1, 4)
        writer.write(book.sequence, 1)
        for value in book.values:
            writer.write(value, book.value_bits)


class CLibrary:
    """ww2ogg's packed codebook library: the books the Wwise runtime knows, by id."""

    def __init__(self, data: bytes) -> None:
        table = struct.unpack_from("<I", data, len(data) - 4)[0]
        count = (len(data) - table) // 4 - 1
        self.m_data = data
        self.m_offsets = struct.unpack_from(f"<{count + 1}I", data, table)
        self.m_count = count
        self._m_ids: dict[SBook, int] | None = None

    @classmethod
    def find(cls) -> CLibrary | None:
        path = find_tool("wwcodebooks")
        return cls(path.read_bytes()) if path else None

    def book(self, book_id: int) -> SBook:
        reader = CBitReader(self.m_data[self.m_offsets[book_id] : self.m_offsets[book_id + 1]])
        dimensions, entries = reader.read(4), reader.read(14)
        lengths = read_lengths(reader, entries, False)
        if not reader.read(1):
            return SBook(dimensions, entries, lengths, 0)
        minimum, delta, bits, sequence = reader.read(32), reader.read(32), reader.read(4) + 1, reader.read(1)
        values = tuple(reader.read(bits) for _ in range(quant_values(entries, dimensions)))
        return SBook(dimensions, entries, lengths, 1, minimum, delta, bits, sequence, values)

    def id_of(self, book: SBook) -> int:
        if self._m_ids is None:  # built whole, then published: encoder threads share one library
            ids: dict[SBook, int] = {}
            for i in range(self.m_count):
                ids.setdefault(self.book(i), i)
            self._m_ids = ids
        try:
            return self._m_ids[book]
        except KeyError:
            raise CWemError(
                f"a codebook ({book.dimensions} dimensions, {book.entries} entries) is not in the library"
            ) from None


# setup packet


@dataclass
class SFloor:
    partition_class: list[int]
    classes: list[tuple[int, int, int, list[int]]]  # dimensions, subclass bits, master book, sub books (-1: none)
    multiplier: int
    range_bits: int
    xlist: list[int]


@dataclass
class SResidue:
    kind: int
    begin: int
    end: int
    partition_size: int
    classbook: int
    cascade: list[int]
    books: list[list[int]]  # per class, per pass: book or -1


@dataclass
class SMapping:
    submaps: int
    coupling: list[tuple[int, int]]
    mux: list[int]
    submap: list[tuple[int, int, int]]  # time, floor, residue


@dataclass
class SSetup:
    channels: int
    books: list[SBook] = field(default_factory=list)
    floors: list[SFloor] = field(default_factory=list)
    residues: list[SResidue] = field(default_factory=list)
    mappings: list[SMapping] = field(default_factory=list)
    modes: list[tuple[int, int]] = field(default_factory=list)  # blockflag, mapping

    @property
    def mode_bits(self) -> int:
        return ilog(len(self.modes) - 1)


def _read_floor(reader: CBitReader) -> SFloor:
    parts = reader.read(5)
    classes_of = [reader.read(4) for _ in range(parts)]
    classes = []
    for _ in range(max(classes_of) + 1 if classes_of else 0):
        dimensions, subclass_bits = reader.read(3) + 1, reader.read(2)
        master = reader.read(8) if subclass_bits else 0
        classes.append((dimensions, subclass_bits, master, [reader.read(8) - 1 for _ in range(1 << subclass_bits)]))
    multiplier, bits = reader.read(2) + 1, reader.read(4)
    x_positions = [reader.read(bits) for class_index in classes_of for _ in range(classes[class_index][0])]
    return SFloor(classes_of, classes, multiplier, bits, x_positions)


def _write_floor(writer: CBitWriter, floor: SFloor) -> None:
    writer.write(len(floor.partition_class), 5)
    for class_index in floor.partition_class:
        writer.write(class_index, 4)
    for dimensions, subclass_bits, master, books in floor.classes:
        writer.write(dimensions - 1, 3)
        writer.write(subclass_bits, 2)
        if subclass_bits:
            writer.write(master, 8)
        for book_index in books:
            writer.write(book_index + 1, 8)
    writer.write(floor.multiplier - 1, 2)
    writer.write(floor.range_bits, 4)
    for x_position in floor.xlist:
        writer.write(x_position, floor.range_bits)


def _read_residue(reader: CBitReader, kind: int) -> SResidue:
    begin, end, partition_size, count, classbook = (
        reader.read(24),
        reader.read(24),
        reader.read(24) + 1,
        reader.read(6) + 1,
        reader.read(8),
    )
    cascade = []
    for _ in range(count):
        low = reader.read(3)
        cascade.append(low | (reader.read(5) << 3 if reader.read(1) else 0))
    books = [[reader.read(8) if cascade_flags & (1 << k) else -1 for k in range(8)] for cascade_flags in cascade]
    return SResidue(kind, begin, end, partition_size, classbook, cascade, books)


def _write_residue(writer: CBitWriter, residue: SResidue, kind_bits: int) -> None:
    writer.write(residue.kind, kind_bits)
    writer.write(residue.begin, 24)
    writer.write(residue.end, 24)
    writer.write(residue.partition_size - 1, 24)
    writer.write(len(residue.cascade) - 1, 6)
    writer.write(residue.classbook, 8)
    for cascade_flags in residue.cascade:
        writer.write(cascade_flags & 7, 3)
        writer.write(int(cascade_flags > 7), 1)
        if cascade_flags > 7:
            writer.write(cascade_flags >> 3, 5)
    for cascade_flags, row in zip(residue.cascade, residue.books, strict=True):
        for k in range(8):
            if cascade_flags & (1 << k):
                writer.write(row[k], 8)


def _read_mapping(reader: CBitReader, channels: int) -> SMapping:
    submaps = reader.read(4) + 1 if reader.read(1) else 1
    coupling = []
    if reader.read(1):
        bits = ilog(channels - 1)
        coupling = [(reader.read(bits), reader.read(bits)) for _ in range(reader.read(8) + 1)]
    if reader.read(2):
        raise CWemError("reserved mapping bits are set")
    mux = [reader.read(4) for _ in range(channels)] if submaps > 1 else []
    return SMapping(submaps, coupling, mux, [(reader.read(8), reader.read(8), reader.read(8)) for _ in range(submaps)])


def _write_mapping(writer: CBitWriter, mapping: SMapping, channels: int) -> None:
    writer.write(int(mapping.submaps > 1), 1)
    if mapping.submaps > 1:
        writer.write(mapping.submaps - 1, 4)
    writer.write(int(bool(mapping.coupling)), 1)
    if mapping.coupling:
        writer.write(len(mapping.coupling) - 1, 8)
        for magnitude_channel, angle_channel in mapping.coupling:
            writer.write(magnitude_channel, ilog(channels - 1))
            writer.write(angle_channel, ilog(channels - 1))
    writer.write(0, 2)
    if mapping.submaps > 1:
        for submap_index in mapping.mux:
            writer.write(submap_index, 4)
    for time_type, floor_index, residue_index in mapping.submap:
        writer.write(time_type, 8)
        writer.write(floor_index, 8)
        writer.write(residue_index, 8)


def parse_std_setup(packet: bytes, channels: int) -> SSetup:
    """The Ogg Vorbis setup header packet (type 5)."""
    if packet[:7] != b"\x05vorbis":
        raise CWemError("not a Vorbis setup header")
    reader = CBitReader(packet[7:])
    setup = SSetup(channels)
    setup.books = [read_std_book(reader) for _ in range(reader.read(8) + 1)]
    for _ in range(reader.read(6) + 1):
        if reader.read(16):
            raise CWemError("time domain transforms are not used")
    for _ in range(reader.read(6) + 1):
        if reader.read(16) != 1:
            raise CWemError("only floor type 1 is read")
        setup.floors.append(_read_floor(reader))
    for _ in range(reader.read(6) + 1):
        kind = reader.read(16)
        if kind > 2:
            raise CWemError("unknown residue type")
        setup.residues.append(_read_residue(reader, kind))
    for _ in range(reader.read(6) + 1):
        if reader.read(16):
            raise CWemError("unknown mapping type")
        setup.mappings.append(_read_mapping(reader, channels))
    for _ in range(reader.read(6) + 1):
        flag = reader.read(1)
        if reader.read(16) or reader.read(16):
            raise CWemError("window or transform type is not 0")
        setup.modes.append((flag, reader.read(8)))
    return setup


def write_std_setup(setup: SSetup) -> bytes:
    writer = CBitWriter()
    writer.write(len(setup.books) - 1, 8)
    for book in setup.books:
        write_std_book(writer, book)
    writer.write(0, 6)  # one time domain transform: 0
    writer.write(0, 16)
    writer.write(len(setup.floors) - 1, 6)
    for floor in setup.floors:
        writer.write(1, 16)
        _write_floor(writer, floor)
    writer.write(len(setup.residues) - 1, 6)
    for residue in setup.residues:
        _write_residue(writer, residue, 16)
    writer.write(len(setup.mappings) - 1, 6)
    for mapping in setup.mappings:
        writer.write(0, 16)
        _write_mapping(writer, mapping, setup.channels)
    writer.write(len(setup.modes) - 1, 6)
    for flag, mapping_index in setup.modes:
        writer.write(flag, 1)
        writer.write(0, 32)
        writer.write(mapping_index, 8)
    writer.write(1, 1)  # framing
    return b"\x05vorbis" + writer.tobytes()


def write_wwise_setup(setup: SSetup, library: CLibrary) -> bytes:
    """The setup packet as Wwise stores it: codebooks by library id, residue kinds in 2 bits, no framing."""
    writer = CBitWriter()
    writer.write(len(setup.books) - 1, 8)
    for book in setup.books:
        writer.write(library.id_of(book), 10)
    writer.write(len(setup.floors) - 1, 6)
    for floor in setup.floors:
        _write_floor(writer, floor)
    writer.write(len(setup.residues) - 1, 6)
    for residue in setup.residues:
        _write_residue(writer, residue, 2)
    writer.write(len(setup.mappings) - 1, 6)
    for mapping in setup.mappings:
        _write_mapping(writer, mapping, setup.channels)
    writer.write(len(setup.modes) - 1, 6)
    for flag, mapping_index in setup.modes:
        writer.write(flag, 1)
        writer.write(mapping_index, 8)
    return writer.tobytes()


def parse_wwise_setup(body: bytes, channels: int, library: CLibrary) -> SSetup:
    reader = CBitReader(body)
    setup = SSetup(channels)
    setup.books = [library.book(reader.read(10)) for _ in range(reader.read(8) + 1)]
    setup.floors = [_read_floor(reader) for _ in range(reader.read(6) + 1)]
    setup.residues = [_read_residue(reader, reader.read(2)) for _ in range(reader.read(6) + 1)]
    setup.mappings = [_read_mapping(reader, channels) for _ in range(reader.read(6) + 1)]
    setup.modes = [(reader.read(1), reader.read(8)) for _ in range(reader.read(6) + 1)]
    if reader.left() >= 8:
        raise CWemError("the setup packet has data left over")
    return setup


# the file


@dataclass
class SWem:
    channels: int
    rate_hz: int
    sample_count: int
    block_pows: tuple[int, int]
    setup: bytes  # the setup packet body
    packets: list[bytes]  # audio packets as stored (size header removed)


def parse_wem(data: bytes) -> SWem:
    try:
        found = riff.chunks(data)
    except ValueError as error:
        raise CWemError(str(error)) from error
    format_chunk, payload = found.get(b"fmt "), found.get(b"data")
    if format_chunk is None or payload is None:
        raise CWemError("fmt or data chunk missing")
    tag, channels, rate_hz = struct.unpack_from("<HHI", format_chunk, 0)
    if tag != FMT_TAG or len(format_chunk) != 18 + 6 + VORB_SIZE:
        raise CWemError("not a Wwise Vorbis file of the 0x42-byte form")
    vorb = format_chunk[24:]
    samples, _mod, _size, _x, setup_at, audio_at = struct.unpack_from("<6I", vorb, 0)
    setup_size = struct.unpack_from("<H", payload, setup_at)[0]
    packets, offset_bytes = [], audio_at
    while offset_bytes + 2 <= len(payload):
        packet_size = struct.unpack_from("<H", payload, offset_bytes)[0]
        packets.append(payload[offset_bytes + 2 : offset_bytes + 2 + packet_size])
        offset_bytes += 2 + packet_size
    return SWem(
        channels, rate_hz, samples, (vorb[0x28], vorb[0x29]), payload[setup_at + 2 : setup_at + 2 + setup_size], packets
    )


def prefetch_size(data: bytes, audio_bytes: int) -> int:
    """How many leading bytes of a Wwise Vorbis file a bank holds as the prefetch of a streamed source: everything up
    to the end of the first audio packet at which at least ``audio_bytes`` of audio are in (the shipped speech cuts
    on a packet boundary the same way; docs/sound/banks-117.md "Streamed sources"). The whole file when it is shorter."""
    try:
        where = riff.spans(data)
    except ValueError as error:
        raise CWemError(str(error)) from error
    fmt_at, fmt_size = where.get(b"fmt ", (0, 0))
    format_chunk = data[fmt_at : fmt_at + fmt_size] if b"fmt " in where else None
    data_at, data_size = where.get(b"data", (None, 0))
    if format_chunk is None or data_at is None or len(format_chunk) != 18 + 6 + VORB_SIZE:
        raise CWemError("not a Wwise Vorbis file of the 0x42-byte form")
    audio_at = struct.unpack_from("<6I", format_chunk, 24)[5]
    end, taken = data_at + audio_at, 0
    while taken < audio_bytes and end - data_at + 2 <= data_size:
        end += 2 + struct.unpack_from("<H", data, end)[0]
        taken = end - data_at - audio_at
    return min(end, len(data))


def _crc_table() -> list[int]:
    """Ogg's CRC-32 (polynomial 0x04C11DB7, no reflection, no final xor), by byte."""
    out = []
    for i in range(256):
        remainder = i << 24
        for _ in range(8):
            remainder = ((remainder << 1) ^ 0x04C11DB7) if remainder & 0x80000000 else (remainder << 1)
        out.append(remainder & 0xFFFFFFFF)
    return out


_CRC = _crc_table()


def _ogg_crc(page: bytes) -> int:
    crc = 0
    for byte in page:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[(crc >> 24) ^ byte]
    return crc


def ogg_pages(packets: list[tuple[bytes, int]], serial: int = 1) -> bytes:
    """Pages from (packet, granule) pairs: each of the three headers gets a page, the audio packets share
    pages of up to 255 segments."""
    out, sequence_number, pending, lacing_values, first = [], 0, [], [], True

    def flush(granule: int, is_last: bool) -> None:
        nonlocal sequence_number, pending, lacing_values, first
        flags = (2 if first else 0) | (4 if is_last else 0)
        head = (
            b"OggS"
            + struct.pack("<BBqIII", 0, flags, granule, serial, sequence_number, 0)
            + bytes([len(lacing_values)])
            + bytes(lacing_values)
        )
        page = head + b"".join(pending)
        crc = _ogg_crc(page)
        out.append(page[:22] + struct.pack("<I", crc) + page[26:])
        sequence_number, pending, lacing_values, first = sequence_number + 1, [], [], False

    for i, (packet, granule) in enumerate(packets):
        lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
        if len(lacing_values) + len(lacing) > 255:
            flush(packets[i - 1][1], False)
        pending.append(packet)
        lacing_values += lacing
        if i == len(packets) - 1 or len(lacing_values) > 200 or i < 3:
            flush(granule, i == len(packets) - 1)
    return b"".join(out)


def _id_packet(channels: int, rate_hz: int, pows: tuple[int, int]) -> bytes:
    return b"\x01vorbis" + struct.pack("<IBIiii", 0, channels, rate_hz, 0, 0, 0) + bytes([pows[0] | (pows[1] << 4), 1])


def to_ogg(data: bytes, library: CLibrary) -> bytes:
    """The Ogg Vorbis stream of a Wwise Vorbis file, to decode it with any Vorbis decoder."""
    wem = parse_wem(data)
    setup = parse_wwise_setup(wem.setup, wem.channels, library)
    flags = [flag for flag, _mapping in setup.modes]
    bits = setup.mode_bits
    modes = [packet[0] & ((1 << bits) - 1) if packet else 0 for packet in wem.packets]
    out: list[tuple[bytes, int]] = [
        (_id_packet(wem.channels, wem.rate_hz, wem.block_pows), 0),
        (b"\x03vorbis" + struct.pack("<I", 0) + struct.pack("<I", 0) + b"\x01", 0),
        (write_std_setup(setup), 0),
    ]
    block_sizes = [1 << block_pow for block_pow in wem.block_pows]
    previous_long, granule, previous_samples = 0, 0, 0
    for i, packet in enumerate(wem.packets):
        long = flags[modes[i]]
        value = int.from_bytes(packet, "little")
        mode = value & ((1 << bits) - 1)
        rest = value >> bits
        if long:
            next_long = flags[modes[i + 1]] if i + 1 < len(modes) else 0
            head = (mode << 1) | (previous_long << (1 + bits)) | (next_long << (2 + bits))
            value = head | (rest << (3 + bits))
        else:
            value = (mode << 1) | (rest << (1 + bits))
        block_samples = block_sizes[long]
        granule += 0 if i == 0 else (previous_samples // 4 + block_samples // 4)
        previous_samples, previous_long = block_samples, long
        last = i == len(wem.packets) - 1
        out.append(
            (
                value.to_bytes((8 * len(packet) + 3 + 7) // 8, "little"),
                min(granule, wem.sample_count) if last else granule,
            )
        )
    return ogg_pages(out)


def decode(data: bytes, library: CLibrary, ffmpeg: Path) -> tuple[bytes, int, int]:
    """PCM (16 bit, interleaved) of a Wwise Vorbis file through ffmpeg: (pcm, channels, rate)."""
    wem = parse_wem(data)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        ogg_path = Path(temp_dir) / "in.ogg"
        ogg_path.write_bytes(to_ogg(data, library))
        done = subprocess.run(
            [str(ffmpeg), "-loglevel", "error", "-i", str(ogg_path), "-f", "s16le", "-"],
            capture_output=True,
            check=False,
            **NO_WINDOW,
        )
    if done.returncode:
        raise CWemError(done.stderr.decode("utf-8", "replace").strip())
    return done.stdout, wem.channels, wem.rate_hz


# encoder

SAMPLE_RATE_HZ = 48000  # every shipped sound effect is 48 kHz; the decoder-size table below is known for those setups
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


def decoder_sizes(setup: SSetup, library: CLibrary) -> tuple[int, int]:
    ids = [library.id_of(book) for book in setup.books]
    key = (setup.channels, len(ids), zlib.crc32(struct.pack(f"<{len(ids)}H", *ids)))
    try:
        return DECODER_SIZES[key]
    except KeyError:
        raise CWemError(f"no shipped file uses this codebook set {key}: its decoder sizes are unknown") from None


def ogg_packets(data: bytes) -> tuple[list[bytes], int]:
    """The packets of an Ogg stream and the granule position of its last page."""
    packets, current, offset_bytes, granule = [], b"", 0, 0
    while offset_bytes + 27 <= len(data):
        if data[offset_bytes : offset_bytes + 4] != b"OggS":
            raise CWemError("not an Ogg stream")
        granule = struct.unpack_from("<q", data, offset_bytes + 6)[0]
        count = data[offset_bytes + 26]
        lacing = data[offset_bytes + 27 : offset_bytes + 27 + count]
        offset_bytes += 27 + count
        for segment_length in lacing:
            current += data[offset_bytes : offset_bytes + segment_length]
            offset_bytes += segment_length
            if segment_length < 255:
                packets.append(current)
                current = b""
    return packets, granule


def vorbis_ogg(pcm: bytes, channels: int, rate_hz: int, ffmpeg: Path, quality: int = QUALITY) -> bytes:
    """16 bit PCM as an Ogg Vorbis stream at ``SAMPLE_RATE_HZ`` (ffmpeg's libvorbis)."""
    key = buildcache.digest("ogg", buildcache.file_digest(ffmpeg), SAMPLE_RATE_HZ, quality, rate_hz, channels, pcm)
    if (hit := buildcache.get(CACHE, key)) is not None:
        return hit
    command = [
        str(ffmpeg),
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(rate_hz),
        "-ac",
        str(channels),
        "-i",
        "pipe:0",
    ]
    command += ["-ar", str(SAMPLE_RATE_HZ), "-c:a", "libvorbis", "-q:a", str(quality), "-f", "ogg", "pipe:1"]
    done = subprocess.run(command, input=pcm, capture_output=True, check=False, **NO_WINDOW)
    if done.returncode or not done.stdout:
        raise CWemError("ffmpeg: " + done.stderr.decode("utf-8", "replace").strip()[:200])
    buildcache.put(CACHE, key, done.stdout)
    return done.stdout


def _wwise_packets(packets: list[bytes], setup: SSetup, pows: tuple[int, int]) -> tuple[list[bytes], int]:
    """The audio packets in Wwise's form (a 16-bit size, no packet type and window flags) and the number of
    samples they decode to."""
    flags, bits = [flag for flag, _mapping in setup.modes], setup.mode_bits
    sizes = [1 << block_pow for block_pow in pows]
    stored, decoded, previous = [], 0, 0
    for i, packet in enumerate(packets):
        value = int.from_bytes(packet, "little")
        if value & 1:
            raise CWemError("an audio packet has the header flag set")
        mode = (value >> 1) & ((1 << bits) - 1)
        long = flags[mode]
        value = mode | ((value >> (1 + bits + (2 if long else 0))) << bits)
        stored.append(struct.pack("<H", len(packet)) + value.to_bytes(len(packet), "little"))
        decoded += 0 if i == 0 else previous // 4 + sizes[long] // 4
        previous = sizes[long]
    return stored, decoded


def encode(
    pcm: bytes, channels: int, rate_hz: int, library: CLibrary, ffmpeg: Path, config: dict[int, int],
    quality: int = QUALITY,
) -> bytes:  # fmt: skip
    """A Wwise Vorbis file (48 kHz) of 16 bit interleaved PCM; ``config``: the channel word by channel count of the
    bank version the file is for (``bnkgraph.layout(version).vorbis_channels``)."""
    if channels not in config:
        raise CWemError(f"{channels} channels: only mono and stereo are written")
    packets, samples = ogg_packets(vorbis_ogg(pcm, channels, rate_hz, ffmpeg, quality))
    if len(packets) < 4 or samples <= 0:
        raise CWemError("ffmpeg wrote no audio")
    setup = parse_std_setup(packets[2], channels)
    body = write_wwise_setup(setup, library)
    first_decoder_size, second_decoder_size = decoder_sizes(setup, library)
    pows = (packets[0][28] & 15, packets[0][28] >> 4)
    stored, decoded = _wwise_packets(packets[3:], setup, pows)
    extra = decoded - samples
    if not 0 <= extra < 1 << 16:
        raise CWemError(f"the stream decodes {decoded} samples for a length of {samples}")
    setup_packet = struct.pack("<H", len(body)) + body
    data = setup_packet + b"".join(stored)
    longest = max(len(packet) for packet in packets[3:])
    average_bytes_per_second = round(len(data) * SAMPLE_RATE_HZ / samples)
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
        first_decoder_size,
        second_decoder_size,
        zlib.crc32(body),
        *pows,
    )
    format_chunk = (
        struct.pack(
            "<HHIIHHHHI", FMT_TAG, channels, SAMPLE_RATE_HZ, average_bytes_per_second, 0, 0, 0x30, 0, config[channels]
        )
        + vorb
    )
    body_chunks = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(format_chunk))
        + format_chunk
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    return b"RIFF" + struct.pack("<I", len(body_chunks) + (len(data) & 1)) + body_chunks + b"\0" * (len(data) & 1)
