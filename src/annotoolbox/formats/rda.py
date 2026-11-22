"""Reader and writer for Anno RDA archives.

"Resource File V2.0" (1404, 2070) and V2.2 (1404's History Edition archive, 2205, 1800, 117): blocks of zlib-compressed,
XOR-encrypted file tables, optionally one shared blob per block, deleted blocks. "Crypted Resource File V1.1" (1701):
one encrypted directory after the header. ``CRdaArchive`` reads, ``CRdaWriter`` / ``write_v*`` write. The byte layout,
the key stream, the seeds and the variants found in the games are in ``docs/rda-format.md``.
"""

from __future__ import annotations

import hashlib
import io
import os
import struct
import threading
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ..infra import piecejson

FILE_PATH_BYTES = 520

FLAG_COMPRESSED = 1
FLAG_ENCRYPTED = 2
FLAG_CONTIGUOUS = 4  # "memory resident": the block's file data is one blob
FLAG_DELETED = 8

INDEX_CACHE_VERSION = 3


class CRdaError(ValueError):
    pass


def _write_whole(dest: Path, data: bytes) -> None:
    """A cache file written whole or not at all (``buildcache.write_atomic``; imported here, as the cache module
    reads archives itself)."""
    from ..infra.buildcache import write_atomic

    write_atomic(dest, data, is_synchronous=False)


@dataclass(frozen=True)
class SRdaVersion:
    name: str
    magic: bytes
    padding: int
    uint: str  # struct code
    seed: int

    @property
    def uint_size(self) -> int:
        return struct.calcsize("<" + self.uint)

    @property
    def max_value(self) -> int:
        """The largest offset or size a field of this version holds (V2.0: 4 GiB - 1)."""
        return (1 << (8 * self.uint_size)) - 1

    @property
    def first_block_pos(self) -> int:
        return len(self.magic) + self.padding

    @property
    def block_header(self) -> struct.Struct:
        return struct.Struct("<II" + self.uint * 3)

    @property
    def file_header_tail(self) -> struct.Struct:
        return struct.Struct("<" + self.uint * 5)

    @property
    def file_header_size(self) -> int:
        return FILE_PATH_BYTES + self.file_header_tail.size

    @property
    def blob_info(self) -> struct.Struct:
        return struct.Struct("<" + self.uint * 2)


V2_0 = SRdaVersion("2.0", "Resource File V2.0".encode("utf-16-le"), 1008, "I", 0xA2C2A)
V2_2 = SRdaVersion("2.2", b"Resource File V2.2", 766, "Q", 0x71C71C71)
# 1701: a 256-byte magic field and the file count, encrypted as one stream with V2.0's seed
V1_1 = SRdaVersion("1.1", b"Crypted Resource File V1.1", 230, "I", 0xA2C2A)
VERSIONS = (V2_0, V2_2)
V1_HEADER = 260  # magic field, uint32 file count
V1_ENTRY = struct.Struct("<256s5I")  # ASCII path, data_offset, compressed_size, size, flags, mtime
V1_FLAGS_ZLIB = 7  # the flags 1701's archives carry: 7 with zlib, 6 stored
V1_FLAGS_STORED = 6
V1_TRAILER = 4  # zero bytes after the last file
V1_PATH_CHARS = 255  # the 256-byte path field keeps a terminator


def detect_version(head: bytes) -> SRdaVersion | None:
    for version in VERSIONS:
        if head.startswith(version.magic):
            return version
    if decrypt(head[: len(V1_1.magic) + 1], V1_1.seed).startswith(V1_1.magic):
        return V1_1
    return None


def version_named(name: str) -> SRdaVersion:
    """The version for ``"1.1"``, ``"2.0"`` or ``"2.2"``."""
    for version in (V1_1, *VERSIONS):
        if version.name == name:
            return version
    raise CRdaError(f"unknown RDA version {name!r} (known: 1.1, 2.0, 2.2)")


# Encryption

_LCG_MUL = 214013
_LCG_INC = 2531011
_g_keystreams: dict[int, tuple[bytearray, int]] = {}
_keystream_lock = threading.Lock()  # sound readers decrypt from thread pools


def _keystream_bytes(seed: int, byte_count: int) -> bytes:
    """Return the first ``n`` bytes of the XOR key stream for ``seed`` (grown lazily)."""
    with _keystream_lock:
        keystream, state = _g_keystreams.get(seed, (bytearray(), seed))
        need = (byte_count + 1) // 2
        have = len(keystream) // 2
        if need > have:
            out = bytearray()
            for _ in range(need - have):
                state = (_LCG_MUL * state + _LCG_INC) & 0xFFFFFFFF
                out += ((state >> 16) & 0x7FFF).to_bytes(2, "little")
            keystream.extend(out)
            _g_keystreams[seed] = (keystream, state)
        return bytes(keystream[:byte_count])


def decrypt(data: bytes, seed: int) -> bytes:
    """XOR ``data`` with the key stream (a trailing odd byte is left as is)."""
    even = len(data) & ~1
    if even == 0:
        return data
    keystream = _keystream_bytes(seed, even)
    body = (int.from_bytes(data[:even], "little") ^ int.from_bytes(keystream, "little")).to_bytes(even, "little")
    return body + data[even:]


def _decode(data: bytes, flags: int, seed: int, expected_size: int | None = None) -> bytes:
    if flags & FLAG_ENCRYPTED:
        data = decrypt(data, seed)
    if flags & FLAG_COMPRESSED:
        decompressor = zlib.decompressobj()
        try:
            data = decompressor.decompress(data) + decompressor.flush()
        except zlib.error as error:
            raise CRdaError(f"corrupt compressed data: {error}") from None
    if expected_size is not None and len(data) != expected_size:
        raise CRdaError(f"decoded {len(data)} bytes, expected {expected_size}")
    return data


def _encode(data: bytes, flags: int, level: int, seed: int) -> bytes:
    """The inverse of ``_decode``: compress, then encrypt (the XOR is its own inverse)."""
    if flags & FLAG_COMPRESSED:
        data = zlib.compress(data, level)
    return decrypt(data, seed) if flags & FLAG_ENCRYPTED else data


# Entries


@dataclass(frozen=True, slots=True)  # an install's index holds about a million: no dict each
class SRdaEntry:
    path: str  # as stored in the archive (forward slashes)
    flags: int
    offset: int  # absolute, or relative to the block blob when flags & FLAG_CONTIGUOUS
    compressed_size: int
    size: int
    mtime: int
    blob_offset: int = 0  # only for contiguous blocks
    blob_compressed_size: int = 0
    blob_size: int = 0

    @property
    def contiguous(self) -> bool:
        return bool(self.flags & FLAG_CONTIGUOUS)


@dataclass(frozen=True)
class SRdaBlock:
    """One block header and where its parts lie (V2 only)."""

    offset: int  # of the block header
    flags: int
    file_count: int
    headers_start: int  # the file headers sit before the block header (and before a resident block's two sizes)
    headers_size: int  # as stored
    headers_decoded_size: int
    next_offset: int
    blob_offset: int = 0  # resident blocks: the data blob before the file headers
    blob_stored_size: int = 0
    blob_size: int = 0


class CRdaArchive:
    """One ``.rda`` file, or the bytes of one (``data=``, ``from_bytes``). Parsing the index is lazy and cached on disk."""

    def __init__(self, path: str | Path, cache_dir: str | Path | None = None, *, data: bytes | None = None) -> None:
        self.m_path = Path(path)
        self._m_data = data
        if data is None and not self.m_path.is_file():
            raise FileNotFoundError(self.m_path)
        self.m_cache_dir = Path(cache_dir) if cache_dir and data is None else None
        self._m_version: SRdaVersion | None = None
        self._m_entries: dict[str, SRdaEntry] | None = None
        self._m_deleted: frozenset[str] = frozenset()
        self._m_blobs: dict[int, bytes] = {}

    @classmethod
    def from_bytes(cls, data: bytes, name: str = "<memory>") -> CRdaArchive:
        """An archive held in memory, such as an ``.a7m`` file read out of another archive."""
        return cls(name, data=data)

    def _open(self) -> BinaryIO:
        return io.BytesIO(self._m_data) if self._m_data is not None else self.m_path.open("rb")

    # Index

    @property
    def version(self) -> SRdaVersion:
        if self._m_version is None:
            with self._open() as file:
                head = file.read(64)
            detected = detect_version(head)
            if detected is None:
                raise CRdaError(f"{self.m_path}: not an RDA archive (unknown magic)")
            self._m_version = detected
        return self._m_version

    @property
    def entries(self) -> dict[str, SRdaEntry]:
        if self._m_entries is None:
            self._m_entries, self._m_deleted = self._load_cached_index() or self._parse_index()
        return self._m_entries

    @property
    def deleted(self) -> frozenset[str]:
        """The paths the archive lists in deleted blocks (flag 8): the game has no such file; ``entries`` lacks them."""
        _ = self.entries  # loads the index
        return self._m_deleted

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, path: str) -> bool:
        return path in self.entries

    def __iter__(self) -> Iterator[SRdaEntry]:
        return iter(self.entries.values())

    def _stamp(self) -> dict:
        file_stat = self.m_path.stat()
        return {"version": INDEX_CACHE_VERSION, "size": file_stat.st_size, "mtime_ns": file_stat.st_mtime_ns}

    def _index_cache_path(self) -> Path | None:
        if self.m_cache_dir is None:
            return None
        return self.m_cache_dir / f"{self.m_path.name}.index.json"

    def _load_cached_index(self) -> tuple[dict[str, SRdaEntry], frozenset[str]] | None:
        index_path = self._index_cache_path()
        if index_path is None or not index_path.is_file():
            return None
        try:
            doc = piecejson.loads(index_path.read_text(encoding="utf-8"))  # often read off the window's thread
            if doc.get("stamp") != self._stamp():
                return None
            return {row["path"]: SRdaEntry(**row) for row in doc["entries"]}, frozenset(doc["deleted"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):  # unreadable or foreign: parse again
            return None

    def _save_cached_index(self, entries: dict[str, SRdaEntry], deleted: frozenset[str]) -> None:
        index_path = self._index_cache_path()
        if index_path is None:
            return
        names = SRdaEntry.__slots__  # its fields, read directly: ``asdict`` copies deeply, four times slower
        rows = [{name: getattr(entry, name) for name in names} for entry in entries.values()]
        doc = {"stamp": self._stamp(), "entries": rows, "deleted": sorted(deleted)}
        try:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            _write_whole(index_path, piecejson.dumps(doc).encode("utf-8"))
        except OSError:
            pass  # the cache only saves time: a folder that cannot be written must not fail the read

    def _parse_index(self) -> tuple[dict[str, SRdaEntry], frozenset[str]]:
        if self.version is V1_1:
            entries, deleted = self._parse_v1(), frozenset()
        else:
            entries, dead = {}, set()
            with self._open() as file:
                for block in self._walk_blocks(file):
                    for entry in self._parse_block(file, block):  # later blocks override earlier ones
                        if block.flags & FLAG_DELETED:
                            entries.pop(entry.path, None)
                            dead.add(entry.path)
                        else:
                            dead.discard(entry.path)
                            entries[entry.path] = entry
            deleted = frozenset(dead)
        self._save_cached_index(entries, deleted)
        return entries, deleted

    def blocks(self) -> Iterator[SRdaBlock]:
        """Every block header in chain order (V2 archives; V1.1 has no blocks)."""
        if self.version is V1_1:
            raise CRdaError(f"{self.m_path}: a V1.1 archive has no blocks")
        with self._open() as file:
            yield from self._walk_blocks(file)

    def block_members(self) -> Iterator[tuple[SRdaBlock, list[SRdaEntry]]]:
        """Every block with the entries its directory lists, in chain order (V2 only; a deleted block's entries too)."""
        if self.version is V1_1:
            raise CRdaError(f"{self.m_path}: a V1.1 archive has no blocks")
        with self._open() as file:
            for block in self._walk_blocks(file):
                yield block, list(self._parse_block(file, block))

    def _walk_blocks(self, file: BinaryIO) -> Iterator[SRdaBlock]:
        version = self.version
        file_size = file.seek(0, os.SEEK_END)
        file.seek(version.first_block_pos)
        first = file.read(version.uint_size)
        if len(first) != version.uint_size:
            raise CRdaError(f"{self.m_path}: truncated archive header")
        (next_block,) = struct.unpack("<" + version.uint, first)
        seen: set[int] = set()
        while 0 < next_block < file_size and next_block not in seen:
            seen.add(next_block)
            file.seek(next_block)
            raw = file.read(version.block_header.size)
            if len(raw) != version.block_header.size:
                raise CRdaError(f"{self.m_path}: truncated block header at {next_block}")
            flags, file_count, stored, decoded, following = version.block_header.unpack(raw)
            headers_start, blob_offset, blob_stored, blob_size = next_block - stored, 0, 0, 0
            if flags & FLAG_CONTIGUOUS and file_count and not flags & FLAG_DELETED:
                sizes_at = next_block - version.blob_info.size
                file.seek(max(sizes_at, 0))
                sizes = file.read(version.blob_info.size)
                if sizes_at < 0 or len(sizes) != version.blob_info.size:
                    raise CRdaError(f"{self.m_path}: truncated resident block at {next_block}")
                blob_stored, blob_size = version.blob_info.unpack(sizes)
                headers_start -= version.blob_info.size
                blob_offset = headers_start - blob_stored
            if headers_start < 0 or blob_offset < 0 or (file_count and file_count * version.file_header_size > decoded):
                raise CRdaError(f"{self.m_path}: block at {next_block} states sizes that leave the file")
            yield SRdaBlock(
                next_block,
                flags,
                file_count,
                headers_start,
                stored,
                decoded,
                following,
                blob_offset,
                blob_stored,
                blob_size,
            )
            next_block = following

    def _parse_v1(self) -> dict[str, SRdaEntry]:
        """V1.1: the directory right after the header, one encrypted stream of ``V1_ENTRY`` records.
        Only bit 1 of an entry's flags is the zlib flag of V2; the data is never encrypted (1701
        sets 6 or 7 on every file)."""
        with self._open() as file:
            head = decrypt(file.read(V1_HEADER), V1_1.seed)
            if len(head) != V1_HEADER:
                raise CRdaError(f"{self.m_path}: truncated V1.1 header")
            (count,) = struct.unpack_from("<I", head, V1_HEADER - 4)
            table = decrypt(file.read(min(count * V1_ENTRY.size, 1 << 31)), V1_1.seed)
        if len(table) != count * V1_ENTRY.size:
            raise CRdaError(f"{self.m_path}: truncated V1.1 directory")
        entries = {}
        for raw_path, offset, compressed_size, size, flags, mtime in V1_ENTRY.iter_unpack(table):
            path = raw_path.split(b"\0", 1)[0].decode("latin-1").replace("\\", "/")
            entries[path] = SRdaEntry(path, flags & FLAG_COMPRESSED, offset, compressed_size, size, mtime)
        return entries

    def _parse_block(self, file: BinaryIO, block: SRdaBlock) -> Iterator[SRdaEntry]:
        """The entries of one block, a deleted block's too (their flags say so); empty blocks give none."""
        if not block.file_count:
            return
        version = self.version
        file.seek(block.headers_start)
        raw = file.read(block.headers_size)
        headers = _decode(
            raw, block.flags & (FLAG_COMPRESSED | FLAG_ENCRYPTED), version.seed, block.headers_decoded_size
        )
        record_size = version.file_header_size
        if len(headers) < block.file_count * record_size:
            raise CRdaError(f"{self.m_path}: block at {block.offset} has truncated file headers")
        for i in range(block.file_count):
            record = headers[i * record_size : (i + 1) * record_size]
            path = record[:FILE_PATH_BYTES].decode("utf-16-le", errors="replace").rstrip("\0")
            data_offset, compressed_size, size, mtime, _unused = version.file_header_tail.unpack(
                record[FILE_PATH_BYTES:]
            )
            blob = (block.blob_offset, block.blob_stored_size, block.blob_size)
            yield SRdaEntry(path, block.flags, data_offset, compressed_size, size, mtime, *blob)

    # Contents

    def read(self, path: str) -> bytes:
        entry = self.entries.get(path)
        if entry is None:
            raise FileNotFoundError(f"{self.m_path.name}:{path}")
        return self.read_entry(entry)

    def read_all(self, paths: Iterable[str] | None = None) -> dict[str, bytes]:
        """The bytes of every member (or of ``paths``, in that order); a missing path or bad data is an ``CRdaError``."""
        try:
            return {path: self.read(path) for path in (self.entries if paths is None else paths)}
        except FileNotFoundError as error:
            raise CRdaError(f"no member {error}") from None

    def read_entry(self, entry: SRdaEntry) -> bytes:
        if entry.contiguous:
            blob = self._blob(entry)
            data = blob[entry.offset : entry.offset + entry.size]
            if len(data) != entry.size:
                raise CRdaError(f"{self.m_path.name}:{entry.path}: blob slice out of range")
            return data
        with self._open() as file:
            raw = self._read_at(file, entry.offset, entry.compressed_size, entry.path)
        if len(raw) != entry.compressed_size:
            raise CRdaError(f"{self.m_path.name}:{entry.path}: truncated data")
        return _decode(raw, entry.flags, self.version.seed, entry.size)

    def _read_at(self, file: BinaryIO, offset: int, size: int, what: str) -> bytes:
        """``size`` bytes at ``offset``; a position or size the file system cannot take (a hostile 64-bit field) is a
        ``CRdaError``, not an ``OverflowError``."""
        try:
            file.seek(offset)
            return file.read(size)
        except (OverflowError, OSError, ValueError, MemoryError):
            raise CRdaError(f"{self.m_path.name}:{what}: {size} bytes at {offset} cannot be read") from None

    def stored_bytes(self, entry: SRdaEntry) -> bytes:
        """The member's bytes as they lie in the file (compressed, encrypted); not for a resident member."""
        with self._open() as file:
            return self._read_at(file, entry.offset, entry.compressed_size, entry.path)

    def directory_of(self, block: SRdaBlock) -> tuple[bytes, bytes]:
        """A block's file directory as ``(stored, decoded)``: the bytes in the file and what they decode to."""
        with self._open() as file:
            file.seek(block.headers_start)
            stored = file.read(block.headers_size)
        flags = block.flags & (FLAG_COMPRESSED | FLAG_ENCRYPTED)
        return stored, _decode(stored, flags, self.version.seed, block.headers_decoded_size)

    def blob_of(self, block: SRdaBlock) -> tuple[bytes, bytes]:
        """A resident block's data blob as ``(stored, decoded)``."""
        with self._open() as file:
            file.seek(block.blob_offset)
            stored = file.read(block.blob_stored_size)
        return stored, _decode(
            stored, block.flags & (FLAG_COMPRESSED | FLAG_ENCRYPTED), self.version.seed, block.blob_size
        )

    def _blob(self, entry: SRdaEntry) -> bytes:
        blob = self._m_blobs.get(entry.blob_offset)
        if blob is None:
            with self._open() as file:
                raw = self._read_at(file, entry.blob_offset, entry.blob_compressed_size, entry.path)
            blob = _decode(raw, entry.flags, self.version.seed, entry.blob_size)
            self._m_blobs[entry.blob_offset] = blob
        return blob

    def open_member(self, path: str) -> CRdaArchive:
        """A member that is an archive itself (an ``.a6m`` / ``.a7m`` / ``.a7t`` file, ``rd3d.data``), read in memory."""
        return CRdaArchive.from_bytes(self.read(path), f"{self.m_path.name}|{path.rsplit('/', 1)[-1]}")

    def extract(self, path: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_whole(dest, self.read(path))
        return dest


@dataclass(frozen=True)
class SStored:
    """A member taken as it lies in another archive: ``packed`` is its bytes as stored (compressed and encrypted as that
    block's flags say), ``size`` its size. A writer given one uses the bytes as they are, so a member nobody changed
    stays what it was whatever made the compression (not for a resident member)."""

    archive: CRdaArchive
    entry: SRdaEntry

    @property
    def size(self) -> int:
        return self.entry.size

    @property
    def packed(self) -> bytes:
        return self.archive.stored_bytes(self.entry)


Member = bytes | Path | SStored
Stamps = int | Mapping[str, int]  # one modification time for every file, or one per path


def _stamp(timestamp: Stamps, path: str) -> int:
    return timestamp.get(path, 0) if isinstance(timestamp, Mapping) else timestamp


WRITABLE_FLAGS = FLAG_COMPRESSED | FLAG_ENCRYPTED


def _check_flags(flags: int, allowed: int = WRITABLE_FLAGS) -> None:
    if flags & ~allowed:
        raise CRdaError(f"block flags {flags}: only flags {allowed} (stored, compressed, encrypted) are written here")


def write_v22(files: dict[str, bytes], timestamp: Stamps = 0, *, flags: int = FLAG_COMPRESSED) -> bytes:
    """A V2.2 archive in the layout of 117's island ``.a7m`` files: one block per file, file data
    and file headers zlib-compressed (flag 1), blocks chained, the last one pointing at the end.
    ``flags`` can be any mix of compressed and encrypted (2205 ships encrypted, uncompressed blocks)."""
    return _write(files, V2_2, flags, timestamp)


def write_v22_stored(files: dict[str, bytes], timestamp: Stamps = 0) -> bytes:
    """A V2.2 archive with nothing compressed or encrypted (flags 0), as 117's savegames are."""
    return _write(files, V2_2, 0, timestamp)


def write_v20(files: dict[str, bytes], timestamp: Stamps = 0, *, flags: int = WRITABLE_FLAGS) -> bytes:
    """A V2.0 archive in the layout of 2070's ``patch9.rda``: like ``write_v22``, with 32-bit
    fields and data and headers compressed, then encrypted (flags 3)."""
    return _write(files, V2_0, flags, timestamp)


def _v11_record(path: str, data: bytes | SStored, offset_bytes: int, flags: int, stamp: int) -> tuple[bytes, bytes]:
    """A V1.1 directory record and the bytes of the file as stored (a ``SStored`` member as it is)."""
    try:
        raw_path = path.encode("latin-1")
    except UnicodeEncodeError:
        raise CRdaError(f"path {path!r}: V1.1 paths are Latin-1") from None
    if len(raw_path) > V1_PATH_CHARS:
        raise CRdaError(f"path {path!r} is over {V1_PATH_CHARS} characters")
    if isinstance(data, SStored):
        return V1_ENTRY.pack(raw_path, offset_bytes, data.entry.compressed_size, data.size, flags, stamp), data.packed
    packed = zlib.compress(data, 1) if flags == V1_FLAGS_ZLIB else data
    return V1_ENTRY.pack(raw_path, offset_bytes, len(packed), len(data), flags, stamp), packed


def write_v11(files: dict[str, bytes], timestamp: Stamps = 0, *, compress: bool = True) -> bytes:
    """A V1.1 archive in the layout of 1701's ``data0.rda``: header and directory encrypted, file
    data zlib-compressed (flags 7) or stored (6), then the four trailing bytes the shipped archives end in."""
    head = V1_1.magic.ljust(V1_HEADER - 4, b"\0") + struct.pack("<I", len(files))
    flags = V1_FLAGS_ZLIB if compress else V1_FLAGS_STORED
    offset = V1_HEADER + len(files) * V1_ENTRY.size
    table, body = bytearray(), bytearray()
    for path, data in files.items():
        record, packed = _v11_record(path, data, offset + len(body), flags, _stamp(timestamp, path))
        table += record
        body += packed
    return decrypt(head, V1_1.seed) + decrypt(bytes(table), V1_1.seed) + bytes(body) + bytes(V1_TRAILER)


def write_v11_file(
    dest: str | Path, members: Iterable[tuple[str, Member]], timestamp: Stamps = 0, *, compress: bool = True
) -> Path:
    """``write_v11`` to disk, one member at a time (files are read when their turn comes), under ``<dest>.part``."""
    dest = Path(dest)
    members = list(members)  # the directory comes first, so the count must be known
    flags = V1_FLAGS_ZLIB if compress else V1_FLAGS_STORED
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with part.open("wb") as file:
            file.write(bytes(V1_HEADER + len(members) * V1_ENTRY.size))
            table = bytearray()
            for path, source in members:
                data = source if isinstance(source, bytes | SStored) else Path(source).read_bytes()
                record, packed = _v11_record(path, data, file.tell(), flags, _stamp(timestamp, path))
                table += record
                file.write(packed)
            file.write(bytes(V1_TRAILER))
            head = V1_1.magic.ljust(V1_HEADER - 4, b"\0") + struct.pack("<I", len(members))
            file.seek(0)
            file.write(decrypt(head, V1_1.seed) + decrypt(bytes(table), V1_1.seed))
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


MAX_PATH_CHARS = FILE_PATH_BYTES // 2 - 1  # the path field is UTF-16 with a terminator
BLOCK_MEMBERS = 10_000  # members per block: one block's directory stays a few MB however big the archive
RESIDENT_BYTES = 64 << 20  # a resident block's blob is built in memory: it is cut at this many bytes of members


def archive_path(path: str) -> str:
    """``path`` if the game's mount can find it, else ``CRdaError``: lower case, forward slashes, no leading ``./`` or
    ``/``, no blanks at the ends, at most 259 characters (docs/rda-format.md, "Paths the game finds")."""
    problem = None
    if not path or len(path) > MAX_PATH_CHARS:
        problem = f"empty or over {MAX_PATH_CHARS} characters"
    elif path != path.strip() or "\\" in path or path.startswith(("/", "./")):
        problem = "blanks at an end, a backslash or a leading ./ or /"
    elif any("A" <= character <= "Z" for character in path):
        problem = "a capital letter (the game looks up the lower-cased path)"
    if problem:
        raise CRdaError(f"path {path!r} cannot be found by the game: {problem}")
    return path


class CRdaWriter:
    """A V2.x archive written to disk member by member, many members per block (docs/rda-format.md, "Writing").

    ``add_block`` takes ``(path, data)`` pairs, ``flags`` any mix of ``FLAG_COMPRESSED`` and ``FLAG_ENCRYPTED`` (0 is
    stored), a zlib ``level`` per member. With ``allow_resident`` a block can also be ``FLAG_CONTIGUOUS`` (one shared
    blob; 117's game refuses such members). ``add_deleted`` lists paths in a deleted block. Equal content shares one
    copy, so two paths can name one offset. Written to ``<dest>.part``, moved into place by ``close``.
    """

    def __init__(
        self,
        dest: str | Path,
        version: SRdaVersion = V2_2,
        timestamp: Stamps = 0,
        *,
        dedup: bool = True,
        allow_resident: bool = False,
        reuse: Mapping[tuple[str, bytes, int], bytes] | None = None,
        allow_empty: bool = False,
    ) -> None:
        if version not in VERSIONS:
            raise CRdaError(f"cannot write version {version.name} with RdaWriter")
        self._m_reuse = (
            reuse or {}
        )  # ("directory" or "blob", blake2b-16 of the decoded bytes, flags) -> as an archive stored it
        self.m_dest = Path(dest)
        self.m_version = version
        self.m_timestamp = timestamp
        self.m_dedup = dedup
        self.m_allow_resident = allow_resident
        self.m_allow_empty = allow_empty  # the games' own archives hold a few members of size 0
        self.m_members = 0
        self._m_uint = struct.Struct("<" + version.uint)
        self._m_paths: set[str] = set()
        self._m_shared: dict[tuple[bytes, int, int], tuple[int, int]] = {}
        self._m_headers: list[int] = []  # file offset of each block header
        self._m_part = self.m_dest.with_name(self.m_dest.name + ".part")
        self.m_dest.parent.mkdir(parents=True, exist_ok=True)
        self._m_f = self._m_part.open("wb+")
        self._m_f.write(
            version.magic + b"\0" * (version.padding + self._m_uint.size)
        )  # first block's offset patched later

    def __enter__(self) -> CRdaWriter:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()

    @property
    def copies(self) -> int:
        """How many stored copies the members share out (with ``dedup``)."""
        return len(self._m_shared)

    def abort(self) -> None:
        if not self._m_f.closed:
            self._m_f.close()
        self._m_part.unlink(missing_ok=True)

    def _fits(self) -> None:
        if self._m_f.tell() > self.m_version.max_value:
            raise CRdaError(
                f"the archive passes the {self.m_version.max_value + 1} bytes a V{self.m_version.name} field holds"
            )

    def add_block(
        self,
        members: Iterable[tuple[str, Member]],
        flags: int = FLAG_COMPRESSED,
        level: int | Callable[[str], int] = 1,
    ) -> None:
        """The members as one or more blocks (see the class); ``level`` is one number or a function of the path
        (a resident block takes the value for its first path)."""
        resident = flags & FLAG_CONTIGUOUS
        if resident and not self.m_allow_resident:
            raise CRdaError(
                f"block flags {flags}: resident members (flag 4) are refused, the game finds none (allow_resident)"
            )
        _check_flags(flags & ~FLAG_CONTIGUOUS)
        chunk: list[tuple[str, Member]] = []
        weight = 0
        for member in members:
            if resident and chunk and weight >= RESIDENT_BYTES:
                self._write_resident(chunk, flags, level)
                chunk, weight = [], 0
            chunk.append(member)
            source = member[1]
            weight += (
                len(source)
                if isinstance(source, bytes)
                else source.size
                if isinstance(source, SStored)
                else Path(source).stat().st_size
            )
            if len(chunk) == BLOCK_MEMBERS:
                (self._write_resident if resident else self._write_block)(chunk, flags, level)
                chunk, weight = [], 0
        if chunk:
            (self._write_resident if resident else self._write_block)(chunk, flags, level)

    def add_deleted(self, paths: Iterable[str]) -> None:
        """A deleted block (flag 8) for ``paths``: the game has no such file, whatever an older archive holds."""
        todo = list(paths)
        for start in range(0, len(todo), BLOCK_MEMBERS):
            directory = bytearray()
            for path in todo[start : start + BLOCK_MEMBERS]:
                self._register(archive_path(path))
                directory += path.encode("utf-16-le").ljust(FILE_PATH_BYTES, b"\0")
                directory += self.m_version.file_header_tail.pack(0, 0, 0, 0, 0)
            self._finish_block(
                directory, bytes(directory), FLAG_DELETED, len(directory) // self.m_version.file_header_size
            )

    def add_empty(self, flags: int) -> None:
        """A block that lists no file (the games' tools leave some at the end of an archive): a header, no directory."""
        self._finish_block(b"", b"", flags, 0)

    def _register(self, path: str) -> None:
        if path in self._m_paths:
            raise CRdaError(f"path {path!r} is in the archive twice")
        self._m_paths.add(path)

    def _member(self, path: str, source: Member) -> bytes | SStored:
        archive_path(path)
        data = source if isinstance(source, bytes | SStored) else Path(source).read_bytes()
        if not data and not self.m_allow_empty:
            raise CRdaError(f"member {path!r} is empty (the game never finds an empty member)")
        self._register(path)
        return data

    def _finish_block(self, directory: bytes, stored: bytes, flags: int, count: int, resident: bytes = b"") -> None:
        """The directory (``stored`` is how it lies in the file), a resident block's two sizes, the block header."""
        file = self._m_f
        file.write(stored)
        file.write(resident)
        self._m_headers.append(file.tell())
        file.write(self.m_version.block_header.pack(flags, count, len(stored), len(directory), 0))
        self._fits()
        self.m_members += count

    def _store(self, data: bytes | SStored, flags: int, level: int) -> tuple[tuple[int, int], int]:
        """Write one member's bytes: ``((offset, stored size), size)``. Equal content shares one copy (``dedup``); a
        ``SStored`` member shares only as it did in its archive; a member that stores nothing lies at offset 0."""
        if isinstance(data, SStored):
            packed, size = data.packed, data.size
            place = f"{data.archive.m_path.resolve()}:{data.entry.offset}:{data.entry.compressed_size}"
            key = (place.encode(), flags, -1)
        else:
            packed, size, key = None, len(data), (hashlib.blake2b(data, digest_size=16).digest(), flags, level)
        where = self._m_shared.get(key) if self.m_dedup else None
        if where is None:
            packed = packed if packed is not None else _encode(data, flags, level, self.m_version.seed)
            where = (self._m_f.tell() if packed else 0, len(packed))
            self._m_f.write(packed)
            self._fits()
            self._m_shared[key] = where
        return where, size

    def _write_block(self, chunk: list, flags: int, level: int | Callable[[str], int]) -> None:
        version = self.m_version
        directory = bytearray()
        for path, source in chunk:
            data = self._member(path, source)
            member_level = level(path) if callable(level) else level
            where, size = self._store(data, flags, member_level)
            directory += path.encode("utf-16-le").ljust(FILE_PATH_BYTES, b"\0")
            directory += version.file_header_tail.pack(where[0], where[1], size, _stamp(self.m_timestamp, path), 0)
        digest = hashlib.blake2b(directory, digest_size=16).digest()
        stored = self._m_reuse.get(("directory", digest, flags)) or _encode(bytes(directory), flags, 1, version.seed)
        self._finish_block(directory, stored, flags, len(chunk))

    def _write_resident(self, chunk: list, flags: int, level: int | Callable[[str], int]) -> None:
        """One block whose file data is a single blob: blob, file headers, the blob's two sizes, the block header."""
        version = self.m_version
        blob, directory = bytearray(), bytearray()
        for path, source in chunk:
            data = self._member(path, source)
            if isinstance(data, SStored):
                raise CRdaError(
                    f"member {path!r}: a resident block holds one blob, its members cannot be taken as stored"
                )
            directory += path.encode("utf-16-le").ljust(FILE_PATH_BYTES, b"\0")
            directory += version.file_header_tail.pack(
                len(blob), len(data), len(data), _stamp(self.m_timestamp, path), 0
            )
            blob += data
        blob_key = ("blob", hashlib.blake2b(blob, digest_size=16).digest(), flags)
        packed = self._m_reuse.get(blob_key) or _encode(
            bytes(blob), flags, level(chunk[0][0]) if callable(level) else level, version.seed
        )
        self._m_f.write(packed)
        sizes = version.blob_info.pack(len(packed), len(blob))
        digest = hashlib.blake2b(directory, digest_size=16).digest()
        stored = self._m_reuse.get(("directory", digest, flags)) or _encode(bytes(directory), flags, 1, version.seed)
        self._finish_block(directory, stored, flags, len(chunk), sizes)

    def close(self) -> Path:
        """Chain the blocks, move the archive to its name, return it."""
        file, uint = self._m_f, self._m_uint
        end = file.seek(0, os.SEEK_END)
        for k, position in enumerate(self._m_headers):
            next_offset = self._m_headers[k + 1] if k + 1 < len(self._m_headers) else end
            file.seek(position + 8 + 2 * uint.size)
            file.write(uint.pack(next_offset))
        file.seek(self.m_version.first_block_pos)
        file.write(uint.pack(self._m_headers[0] if self._m_headers else end))
        file.close()
        os.replace(self._m_part, self.m_dest)
        return self.m_dest


def _write(files: dict[str, bytes], version: SRdaVersion, flags: int, timestamp: Stamps) -> bytes:
    _check_flags(flags)
    uint = struct.Struct("<" + version.uint)
    out = bytearray(version.magic + b"\0" * version.padding + b"\0" * uint.size)  # first-block offset patched below
    block_offsets = []
    for path, data in files.items():
        packed = _encode(data, flags, 1, version.seed)  # the vanilla files use the fastest level (header 78 01)
        data_offset = len(out)
        out += packed
        header = path.encode("utf-16-le").ljust(FILE_PATH_BYTES, b"\0")
        header += version.file_header_tail.pack(data_offset, len(packed), len(data), _stamp(timestamp, path), 0)
        packed_header = _encode(header, flags, 1, version.seed)
        out += packed_header
        block_offsets.append(len(out))
        out += version.block_header.pack(flags, 1, len(packed_header), len(header), 0)
    for k, block_offset in enumerate(block_offsets):
        next_offset = block_offsets[k + 1] if k + 1 < len(block_offsets) else len(out)
        uint.pack_into(out, block_offset + 8 + 2 * uint.size, next_offset)
    uint.pack_into(out, version.first_block_pos, block_offsets[0] if block_offsets else len(out))
    return bytes(out)
