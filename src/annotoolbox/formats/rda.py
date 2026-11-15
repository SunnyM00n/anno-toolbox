"""Reader and writer for Anno RDA archives.

"Resource File V2.0" (1404, 2070) and V2.2 (1404's History Edition archive, 2205, 1800, 117): blocks of zlib-compressed,
XOR-encrypted file tables, optionally one shared blob per block, deleted blocks. "Crypted Resource File V1.1" (1701):
one encrypted directory after the header. ``RdaArchive`` reads, ``RdaWriter`` / ``write_v*`` write. The byte layout,
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


class RdaError(ValueError):
    pass


def _write_whole(dest: Path, data: bytes) -> None:
    """A cache file written whole or not at all (``buildcache.write_atomic``; imported here, as the cache module
    reads archives itself)."""
    from ..infra.buildcache import write_atomic

    write_atomic(dest, data, sync=False)


@dataclass(frozen=True)
class RdaVersion:
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


V2_0 = RdaVersion("2.0", "Resource File V2.0".encode("utf-16-le"), 1008, "I", 0xA2C2A)
V2_2 = RdaVersion("2.2", b"Resource File V2.2", 766, "Q", 0x71C71C71)
# 1701: a 256-byte magic field and the file count, encrypted as one stream with V2.0's seed
V1_1 = RdaVersion("1.1", b"Crypted Resource File V1.1", 230, "I", 0xA2C2A)
VERSIONS = (V2_0, V2_2)
V1_HEADER = 260  # magic field, uint32 file count
V1_ENTRY = struct.Struct("<256s5I")  # ASCII path, data_offset, compressed_size, size, flags, mtime
V1_FLAGS_ZLIB = 7  # the flags 1701's archives carry: 7 with zlib, 6 stored
V1_FLAGS_STORED = 6
V1_TRAILER = 4  # zero bytes after the last file
V1_PATH_CHARS = 255  # the 256-byte path field keeps a terminator


def detect_version(head: bytes) -> RdaVersion | None:
    for v in VERSIONS:
        if head.startswith(v.magic):
            return v
    if decrypt(head[: len(V1_1.magic) + 1], V1_1.seed).startswith(V1_1.magic):
        return V1_1
    return None


def version_named(name: str) -> RdaVersion:
    """The version for ``"1.1"``, ``"2.0"`` or ``"2.2"``."""
    for v in (V1_1, *VERSIONS):
        if v.name == name:
            return v
    raise RdaError(f"unknown RDA version {name!r} (known: 1.1, 2.0, 2.2)")


# Encryption

_LCG_MUL = 214013
_LCG_INC = 2531011
_keystreams: dict[int, tuple[bytearray, int]] = {}
_keystream_lock = threading.Lock()  # sound readers decrypt from thread pools


def _keystream_bytes(seed: int, n: int) -> bytes:
    """Return the first ``n`` bytes of the XOR key stream for ``seed`` (grown lazily)."""
    with _keystream_lock:
        buf, x = _keystreams.get(seed, (bytearray(), seed))
        need = (n + 1) // 2
        have = len(buf) // 2
        if need > have:
            out = bytearray()
            for _ in range(need - have):
                x = (_LCG_MUL * x + _LCG_INC) & 0xFFFFFFFF
                out += ((x >> 16) & 0x7FFF).to_bytes(2, "little")
            buf.extend(out)
            _keystreams[seed] = (buf, x)
        return bytes(buf[:n])


def decrypt(data: bytes, seed: int) -> bytes:
    """XOR ``data`` with the key stream (a trailing odd byte is left as is)."""
    even = len(data) & ~1
    if even == 0:
        return data
    ks = _keystream_bytes(seed, even)
    body = (int.from_bytes(data[:even], "little") ^ int.from_bytes(ks, "little")).to_bytes(even, "little")
    return body + data[even:]


def _decode(data: bytes, flags: int, seed: int, expected_size: int | None = None) -> bytes:
    if flags & FLAG_ENCRYPTED:
        data = decrypt(data, seed)
    if flags & FLAG_COMPRESSED:
        d = zlib.decompressobj()
        try:
            data = d.decompress(data) + d.flush()
        except zlib.error as e:
            raise RdaError(f"corrupt compressed data: {e}") from None
    if expected_size is not None and len(data) != expected_size:
        raise RdaError(f"decoded {len(data)} bytes, expected {expected_size}")
    return data


def _encode(data: bytes, flags: int, level: int, seed: int) -> bytes:
    """The inverse of ``_decode``: compress, then encrypt (the XOR is its own inverse)."""
    if flags & FLAG_COMPRESSED:
        data = zlib.compress(data, level)
    return decrypt(data, seed) if flags & FLAG_ENCRYPTED else data


# Entries


@dataclass(frozen=True, slots=True)  # an install's index holds about a million: no dict each
class RdaEntry:
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
class RdaBlock:
    """One block header and where its parts lie (V2 only)."""

    offset: int  # of the block header
    flags: int
    num_files: int
    headers_start: int  # the file headers sit before the block header (and before a resident block's two sizes)
    headers_size: int  # as stored
    headers_decoded_size: int
    next_offset: int
    blob_offset: int = 0  # resident blocks: the data blob before the file headers
    blob_stored_size: int = 0
    blob_size: int = 0


class RdaArchive:
    """One ``.rda`` file, or the bytes of one (``data=``, ``from_bytes``). Parsing the index is lazy and cached on disk."""

    def __init__(self, path: str | Path, cache_dir: str | Path | None = None, *, data: bytes | None = None) -> None:
        self.path = Path(path)
        self._data = data
        if data is None and not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.cache_dir = Path(cache_dir) if cache_dir and data is None else None
        self._version: RdaVersion | None = None
        self._entries: dict[str, RdaEntry] | None = None
        self._deleted: frozenset[str] = frozenset()
        self._blobs: dict[int, bytes] = {}

    @classmethod
    def from_bytes(cls, data: bytes, name: str = "<memory>") -> RdaArchive:
        """An archive held in memory, such as an ``.a7m`` file read out of another archive."""
        return cls(name, data=data)

    def _open(self) -> BinaryIO:
        return io.BytesIO(self._data) if self._data is not None else self.path.open("rb")

    # Index

    @property
    def version(self) -> RdaVersion:
        if self._version is None:
            with self._open() as f:
                head = f.read(64)
            v = detect_version(head)
            if v is None:
                raise RdaError(f"{self.path}: not an RDA archive (unknown magic)")
            self._version = v
        return self._version

    @property
    def entries(self) -> dict[str, RdaEntry]:
        if self._entries is None:
            self._entries, self._deleted = self._load_cached_index() or self._parse_index()
        return self._entries

    @property
    def deleted(self) -> frozenset[str]:
        """The paths the archive lists in deleted blocks (flag 8): the game has no such file; ``entries`` lacks them."""
        _ = self.entries  # loads the index
        return self._deleted

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, path: str) -> bool:
        return path in self.entries

    def __iter__(self) -> Iterator[RdaEntry]:
        return iter(self.entries.values())

    def _stamp(self) -> dict:
        st = self.path.stat()
        return {"version": INDEX_CACHE_VERSION, "size": st.st_size, "mtime_ns": st.st_mtime_ns}

    def _index_cache_path(self) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{self.path.name}.index.json"

    def _load_cached_index(self) -> tuple[dict[str, RdaEntry], frozenset[str]] | None:
        p = self._index_cache_path()
        if p is None or not p.is_file():
            return None
        try:
            doc = piecejson.loads(p.read_text(encoding="utf-8"))  # often read off the window's thread
        except (OSError, ValueError):
            return None
        if doc.get("stamp") != self._stamp():
            return None
        return {e["path"]: RdaEntry(**e) for e in doc["entries"]}, frozenset(doc["deleted"])

    def _save_cached_index(self, entries: dict[str, RdaEntry], deleted: frozenset[str]) -> None:
        p = self._index_cache_path()
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        names = RdaEntry.__slots__  # its fields, read directly: ``asdict`` copies deeply, four times slower
        rows = [{n: getattr(e, n) for n in names} for e in entries.values()]
        doc = {"stamp": self._stamp(), "entries": rows, "deleted": sorted(deleted)}
        _write_whole(p, piecejson.dumps(doc).encode("utf-8"))

    def _parse_index(self) -> tuple[dict[str, RdaEntry], frozenset[str]]:
        if self.version is V1_1:
            entries, deleted = self._parse_v1(), frozenset()
        else:
            entries, dead = {}, set()
            with self._open() as f:
                for block in self._walk_blocks(f):
                    for entry in self._parse_block(f, block):  # later blocks override earlier ones
                        if block.flags & FLAG_DELETED:
                            entries.pop(entry.path, None)
                            dead.add(entry.path)
                        else:
                            dead.discard(entry.path)
                            entries[entry.path] = entry
            deleted = frozenset(dead)
        self._save_cached_index(entries, deleted)
        return entries, deleted

    def blocks(self) -> Iterator[RdaBlock]:
        """Every block header in chain order (V2 archives; V1.1 has no blocks)."""
        if self.version is V1_1:
            raise RdaError(f"{self.path}: a V1.1 archive has no blocks")
        with self._open() as f:
            yield from self._walk_blocks(f)

    def _walk_blocks(self, f: BinaryIO) -> Iterator[RdaBlock]:
        v = self.version
        file_size = f.seek(0, os.SEEK_END)
        f.seek(v.first_block_pos)
        (next_block,) = struct.unpack("<" + v.uint, f.read(v.uint_size))
        seen: set[int] = set()
        while 0 < next_block < file_size and next_block not in seen:
            seen.add(next_block)
            f.seek(next_block)
            raw = f.read(v.block_header.size)
            if len(raw) != v.block_header.size:
                raise RdaError(f"{self.path}: truncated block header at {next_block}")
            flags, num_files, stored, decoded, following = v.block_header.unpack(raw)
            headers_start, blob_offset, blob_stored, blob_size = next_block - stored, 0, 0, 0
            if flags & FLAG_CONTIGUOUS and num_files and not flags & FLAG_DELETED:
                f.seek(next_block - v.blob_info.size)
                blob_stored, blob_size = v.blob_info.unpack(f.read(v.blob_info.size))
                headers_start -= v.blob_info.size
                blob_offset = headers_start - blob_stored
            yield RdaBlock(
                next_block,
                flags,
                num_files,
                headers_start,
                stored,
                decoded,
                following,
                blob_offset,
                blob_stored,
                blob_size,
            )
            next_block = following

    def _parse_v1(self) -> dict[str, RdaEntry]:
        """V1.1: the directory right after the header, one encrypted stream of ``V1_ENTRY`` records.
        Only bit 1 of an entry's flags is the zlib flag of V2; the data is never encrypted (1701
        sets 6 or 7 on every file)."""
        with self._open() as f:
            head = decrypt(f.read(V1_HEADER), V1_1.seed)
            (count,) = struct.unpack_from("<I", head, V1_HEADER - 4)
            table = decrypt(f.read(count * V1_ENTRY.size), V1_1.seed)
        if len(table) != count * V1_ENTRY.size:
            raise RdaError(f"{self.path}: truncated V1.1 directory")
        entries = {}
        for raw_path, offset, comp_size, size, flags, mtime in V1_ENTRY.iter_unpack(table):
            path = raw_path.split(b"\0", 1)[0].decode("latin-1").replace("\\", "/")
            entries[path] = RdaEntry(path, flags & FLAG_COMPRESSED, offset, comp_size, size, mtime)
        return entries

    def _parse_block(self, f: BinaryIO, block: RdaBlock) -> Iterator[RdaEntry]:
        """The entries of one block, a deleted block's too (their flags say so); empty blocks give none."""
        if not block.num_files:
            return
        v = self.version
        f.seek(block.headers_start)
        raw = f.read(block.headers_size)
        headers = _decode(raw, block.flags & (FLAG_COMPRESSED | FLAG_ENCRYPTED), v.seed, block.headers_decoded_size)
        rec_size = v.file_header_size
        if len(headers) < block.num_files * rec_size:
            raise RdaError(f"{self.path}: block at {block.offset} has truncated file headers")
        for i in range(block.num_files):
            rec = headers[i * rec_size : (i + 1) * rec_size]
            path = rec[:FILE_PATH_BYTES].decode("utf-16-le", errors="replace").rstrip("\0")
            data_offset, comp_size, size, mtime, _unused = v.file_header_tail.unpack(rec[FILE_PATH_BYTES:])
            blob = (block.blob_offset, block.blob_stored_size, block.blob_size)
            yield RdaEntry(path, block.flags, data_offset, comp_size, size, mtime, *blob)

    # Contents

    def read(self, path: str) -> bytes:
        entry = self.entries.get(path)
        if entry is None:
            raise FileNotFoundError(f"{self.path.name}:{path}")
        return self.read_entry(entry)

    def read_all(self, paths: Iterable[str] | None = None) -> dict[str, bytes]:
        """The bytes of every member (or of ``paths``, in that order); a missing path or bad data is an ``RdaError``."""
        try:
            return {p: self.read(p) for p in (self.entries if paths is None else paths)}
        except FileNotFoundError as e:
            raise RdaError(f"no member {e}") from None

    def read_entry(self, entry: RdaEntry) -> bytes:
        if entry.contiguous:
            blob = self._blob(entry)
            data = blob[entry.offset : entry.offset + entry.size]
            if len(data) != entry.size:
                raise RdaError(f"{self.path.name}:{entry.path}: blob slice out of range")
            return data
        with self._open() as f:
            f.seek(entry.offset)
            raw = f.read(entry.compressed_size)
        if len(raw) != entry.compressed_size:
            raise RdaError(f"{self.path.name}:{entry.path}: truncated data")
        return _decode(raw, entry.flags, self.version.seed, entry.size)

    def _blob(self, entry: RdaEntry) -> bytes:
        blob = self._blobs.get(entry.blob_offset)
        if blob is None:
            with self._open() as f:
                f.seek(entry.blob_offset)
                raw = f.read(entry.blob_compressed_size)
            blob = _decode(raw, entry.flags, self.version.seed, entry.blob_size)
            self._blobs[entry.blob_offset] = blob
        return blob

    def open_member(self, path: str) -> RdaArchive:
        """A member that is an archive itself (an ``.a6m`` / ``.a7m`` / ``.a7t`` file, ``rd3d.data``), read in memory."""
        return RdaArchive.from_bytes(self.read(path), f"{self.path.name}|{path.rsplit('/', 1)[-1]}")

    def extract(self, path: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_whole(dest, self.read(path))
        return dest


Stamps = int | Mapping[str, int]  # one modification time for every file, or one per path


def _stamp(timestamp: Stamps, path: str) -> int:
    return timestamp.get(path, 0) if isinstance(timestamp, Mapping) else timestamp


WRITABLE_FLAGS = FLAG_COMPRESSED | FLAG_ENCRYPTED


def _check_flags(flags: int, allowed: int = WRITABLE_FLAGS) -> None:
    if flags & ~allowed:
        raise RdaError(f"block flags {flags}: only flags {allowed} (stored, compressed, encrypted) are written here")


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


def _v11_record(path: str, data: bytes, offset: int, flags: int, stamp: int) -> tuple[bytes, bytes]:
    """A V1.1 directory record and the bytes of the file as stored."""
    try:
        raw_path = path.encode("latin-1")
    except UnicodeEncodeError:
        raise RdaError(f"path {path!r}: V1.1 paths are Latin-1") from None
    if len(raw_path) > V1_PATH_CHARS:
        raise RdaError(f"path {path!r} is over {V1_PATH_CHARS} characters")
    packed = zlib.compress(data, 1) if flags == V1_FLAGS_ZLIB else data
    return V1_ENTRY.pack(raw_path, offset, len(packed), len(data), flags, stamp), packed


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
    dest: str | Path, members: Iterable[tuple[str, bytes | Path]], timestamp: Stamps = 0, *, compress: bool = True
) -> Path:
    """``write_v11`` to disk, one member at a time (files are read when their turn comes), under ``<dest>.part``."""
    dest = Path(dest)
    members = list(members)  # the directory comes first, so the count must be known
    flags = V1_FLAGS_ZLIB if compress else V1_FLAGS_STORED
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with part.open("wb") as f:
            f.write(bytes(V1_HEADER + len(members) * V1_ENTRY.size))
            table = bytearray()
            for path, source in members:
                data = source if isinstance(source, bytes) else Path(source).read_bytes()
                record, packed = _v11_record(path, data, f.tell(), flags, _stamp(timestamp, path))
                table += record
                f.write(packed)
            f.write(bytes(V1_TRAILER))
            head = V1_1.magic.ljust(V1_HEADER - 4, b"\0") + struct.pack("<I", len(members))
            f.seek(0)
            f.write(decrypt(head, V1_1.seed) + decrypt(bytes(table), V1_1.seed))
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


MAX_PATH_CHARS = FILE_PATH_BYTES // 2 - 1  # the path field is UTF-16 with a terminator
BLOCK_MEMBERS = 10_000  # members per block: one block's directory stays a few MB however big the archive
RESIDENT_BYTES = 64 << 20  # a resident block's blob is built in memory: it is cut at this many bytes of members


def archive_path(path: str) -> str:
    """``path`` if the game's mount can find it, else ``RdaError``: lower case, forward slashes, no leading ``./`` or
    ``/``, no blanks at the ends, at most 259 characters (docs/rda-format.md, "Paths the game finds")."""
    problem = None
    if not path or len(path) > MAX_PATH_CHARS:
        problem = f"empty or over {MAX_PATH_CHARS} characters"
    elif path != path.strip() or "\\" in path or path.startswith(("/", "./")):
        problem = "blanks at an end, a backslash or a leading ./ or /"
    elif any("A" <= c <= "Z" for c in path):
        problem = "a capital letter (the game looks up the lower-cased path)"
    if problem:
        raise RdaError(f"path {path!r} cannot be found by the game: {problem}")
    return path


class RdaWriter:
    """A V2.x archive written to disk member by member, many members per block (docs/rda-format.md, "Writing").

    ``add_block`` takes ``(path, data)`` pairs, ``flags`` any mix of ``FLAG_COMPRESSED`` and ``FLAG_ENCRYPTED`` (0 is
    stored), a zlib ``level`` per member. With ``allow_resident`` a block can also be ``FLAG_CONTIGUOUS`` (one shared
    blob; 117's game refuses such members). ``add_deleted`` lists paths in a deleted block. Equal content shares one
    copy, so two paths can name one offset. Written to ``<dest>.part``, moved into place by ``close``.
    """

    def __init__(
        self,
        dest: str | Path,
        version: RdaVersion = V2_2,
        timestamp: Stamps = 0,
        *,
        dedup: bool = True,
        allow_resident: bool = False,
    ) -> None:
        if version not in VERSIONS:
            raise RdaError(f"cannot write version {version.name} with RdaWriter")
        self.dest = Path(dest)
        self.version = version
        self.timestamp = timestamp
        self.dedup = dedup
        self.allow_resident = allow_resident
        self.members = 0
        self._uint = struct.Struct("<" + version.uint)
        self._paths: set[str] = set()
        self._shared: dict[tuple[bytes, int, int], tuple[int, int]] = {}
        self._headers: list[int] = []  # file offset of each block header
        self._part = self.dest.with_name(self.dest.name + ".part")
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        self._f = self._part.open("wb+")
        self._f.write(version.magic + b"\0" * (version.padding + self._uint.size))  # first block's offset patched later

    def __enter__(self) -> RdaWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()

    @property
    def copies(self) -> int:
        """How many stored copies the members share out (with ``dedup``)."""
        return len(self._shared)

    def abort(self) -> None:
        if not self._f.closed:
            self._f.close()
        self._part.unlink(missing_ok=True)

    def _fits(self) -> None:
        if self._f.tell() > self.version.max_value:
            raise RdaError(
                f"the archive passes the {self.version.max_value + 1} bytes a V{self.version.name} field holds"
            )

    def add_block(
        self,
        members: Iterable[tuple[str, bytes | Path]],
        flags: int = FLAG_COMPRESSED,
        level: int | Callable[[str], int] = 1,
    ) -> None:
        """The members as one or more blocks (see the class); ``level`` is one number or a function of the path
        (a resident block takes the value for its first path)."""
        resident = flags & FLAG_CONTIGUOUS
        if resident and not self.allow_resident:
            raise RdaError(
                f"block flags {flags}: resident members (flag 4) are refused, the game finds none (allow_resident)"
            )
        _check_flags(flags & ~FLAG_CONTIGUOUS)
        chunk: list[tuple[str, bytes | Path]] = []
        weight = 0
        for member in members:
            if resident and chunk and weight >= RESIDENT_BYTES:
                self._write_resident(chunk, flags, level)
                chunk, weight = [], 0
            chunk.append(member)
            weight += len(member[1]) if isinstance(member[1], bytes) else Path(member[1]).stat().st_size
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
                directory += self.version.file_header_tail.pack(0, 0, 0, 0, 0)
            self._finish_block(
                directory, bytes(directory), FLAG_DELETED, len(directory) // self.version.file_header_size
            )

    def _register(self, path: str) -> None:
        if path in self._paths:
            raise RdaError(f"path {path!r} is in the archive twice")
        self._paths.add(path)

    def _member(self, path: str, source: bytes | Path) -> bytes:
        archive_path(path)
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        if not data:
            raise RdaError(f"member {path!r} is empty (the game never finds an empty member)")
        self._register(path)
        return data

    def _finish_block(self, directory: bytes, stored: bytes, flags: int, count: int, resident: bytes = b"") -> None:
        """The directory (``stored`` is how it lies in the file), a resident block's two sizes, the block header."""
        f = self._f
        f.write(stored)
        f.write(resident)
        self._headers.append(f.tell())
        f.write(self.version.block_header.pack(flags, count, len(stored), len(directory), 0))
        self._fits()
        self.members += count

    def _write_block(self, chunk: list, flags: int, level: int | Callable[[str], int]) -> None:
        v, f = self.version, self._f
        directory = bytearray()
        for path, source in chunk:
            data = self._member(path, source)
            lvl = level(path) if callable(level) else level
            key = (hashlib.blake2b(data, digest_size=16).digest(), flags, lvl)
            where = self._shared.get(key) if self.dedup else None
            if where is None:
                packed = _encode(data, flags, lvl, v.seed)
                where = (f.tell(), len(packed))
                f.write(packed)
                self._fits()
                self._shared[key] = where
            directory += path.encode("utf-16-le").ljust(FILE_PATH_BYTES, b"\0")
            directory += v.file_header_tail.pack(where[0], where[1], len(data), _stamp(self.timestamp, path), 0)
        self._finish_block(directory, _encode(bytes(directory), flags, 1, v.seed), flags, len(chunk))

    def _write_resident(self, chunk: list, flags: int, level: int | Callable[[str], int]) -> None:
        """One block whose file data is a single blob: blob, file headers, the blob's two sizes, the block header."""
        v = self.version
        blob, directory = bytearray(), bytearray()
        for path, source in chunk:
            data = self._member(path, source)
            directory += path.encode("utf-16-le").ljust(FILE_PATH_BYTES, b"\0")
            directory += v.file_header_tail.pack(len(blob), len(data), len(data), _stamp(self.timestamp, path), 0)
            blob += data
        packed = _encode(bytes(blob), flags, level(chunk[0][0]) if callable(level) else level, v.seed)
        self._f.write(packed)
        sizes = v.blob_info.pack(len(packed), len(blob))
        self._finish_block(directory, _encode(bytes(directory), flags, 1, v.seed), flags, len(chunk), sizes)

    def close(self) -> Path:
        """Chain the blocks, move the archive to its name, return it."""
        f, uint = self._f, self._uint
        end = f.seek(0, os.SEEK_END)
        for k, pos in enumerate(self._headers):
            nxt = self._headers[k + 1] if k + 1 < len(self._headers) else end
            f.seek(pos + 8 + 2 * uint.size)
            f.write(uint.pack(nxt))
        f.seek(self.version.first_block_pos)
        f.write(uint.pack(self._headers[0] if self._headers else end))
        f.close()
        os.replace(self._part, self.dest)
        return self.dest


def _write(files: dict[str, bytes], v: RdaVersion, flags: int, timestamp: Stamps) -> bytes:
    _check_flags(flags)
    uint = struct.Struct("<" + v.uint)
    out = bytearray(v.magic + b"\0" * v.padding + b"\0" * uint.size)  # first-block offset patched below
    block_offsets = []
    for path, data in files.items():
        packed = _encode(data, flags, 1, v.seed)  # the vanilla files use the fastest level (header 78 01)
        data_offset = len(out)
        out += packed
        header = path.encode("utf-16-le").ljust(FILE_PATH_BYTES, b"\0")
        header += v.file_header_tail.pack(data_offset, len(packed), len(data), _stamp(timestamp, path), 0)
        packed_header = _encode(header, flags, 1, v.seed)
        out += packed_header
        block_offsets.append(len(out))
        out += v.block_header.pack(flags, 1, len(packed_header), len(header), 0)
    for k, off in enumerate(block_offsets):
        nxt = block_offsets[k + 1] if k + 1 < len(block_offsets) else len(out)
        uint.pack_into(out, off + 8 + 2 * uint.size, nxt)
    uint.pack_into(out, v.first_block_pos, block_offsets[0] if block_offsets else len(out))
    return bytes(out)
