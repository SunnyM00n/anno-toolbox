"""An RDA archive's layout kept as data, and the archive written again in it (docs/mod-import.md, section 16).

``read_layout`` records what a reader drops: the version, the blocks (flags, members each), the members in order,
their times, the zlib level, whether equal members share a copy. ``pack(layout, members, dest, original=...)`` writes
it again from the members' contents, taking an unchanged member's stored bytes and a block's directory from the
``Original`` (whatever compressed them); new members get a block at the end. ``differences`` names the rest.
"""

from __future__ import annotations

import hashlib
import zlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import rda

FORMAT = 1
LEVELS = (1, 6, 9, 0, 2, 3, 4, 5, 7, 8)  # tried in this order when the level of an archive is looked for
SAMPLE = 8  # compressed members compared to find the level
SAMPLE_BYTES = 1 << 20  # members above this are not used for it
Namer = Callable[[rda.RdaEntry], str]

# what ``differences`` can name
ORDER, BLOCKS, STAMPS, COMPRESSION, SHARING, FILES = "order", "blocks", "stamps", "compression", "sharing", "files"


@dataclass
class Layout:
    """The shape of one archive. ``blocks`` is ``(flags, members)`` per block in file order (flag 8 is a deleted block;
    a V1.1 archive has none: one entry whose flag 1 says "compressed"); ``order`` holds the members of all blocks in
    order, deleted paths included; ``default_flags`` are the flags of a block of members the layout does not know."""

    version: str
    blocks: list[tuple[int, int]] = field(default_factory=list)
    order: list[str] = field(default_factory=list)
    stamps: dict[str, int] = field(default_factory=dict)
    level: int = 1
    dedup: bool = False
    default_flags: int = rda.FLAG_COMPRESSED
    stored_sizes: dict[str, int] = field(default_factory=dict)  # as read; for ``differences``, not kept

    @property
    def deleted(self) -> list[str]:
        return [p for p, f in self._flagged() if f & rda.FLAG_DELETED]

    @property
    def live(self) -> list[str]:
        return [p for p, f in self._flagged() if not f & rda.FLAG_DELETED]

    def _flagged(self) -> Iterable[tuple[str, int]]:
        at = 0
        for flags, count in self.blocks:
            for path in self.order[at : at + count]:
                yield path, flags
            at += count

    def mapped(self, rename: Callable[[str], str | None]) -> Layout:
        """The layout with every path ``rename`` gives (None: the member is dropped, and its block shrinks); the
        first of two members that get one name stays."""
        out = Layout(self.version, [], [], {}, self.level, self.dedup, self.default_flags)
        seen: set[str] = set()
        at = 0
        for flags, count in self.blocks:
            kept = []
            for old in self.order[at : at + count]:
                new = rename(old)
                if new is None or new in seen:
                    continue
                seen.add(new)
                kept.append(new)
                out.stamps[new] = self.stamps.get(old, 0)
                if old in self.stored_sizes:
                    out.stored_sizes[new] = self.stored_sizes[old]
            if kept or not count:  # a block that lists nothing stays
                out.blocks.append((flags, len(kept)))
                out.order += kept
            at += count
        return out

    def to_json(self) -> dict:
        return {
            "format": FORMAT,
            "version": self.version,
            "blocks": [list(b) for b in self.blocks],
            "order": self.order,
            "stamps": [self.stamps.get(p, 0) for p in self.order],
            "level": self.level,
            "dedup": self.dedup,
            "default_flags": self.default_flags,
        }

    @classmethod
    def from_json(cls, data: Mapping) -> Layout:
        if data.get("format", FORMAT) > FORMAT:
            raise rda.RdaError("the archive layout was written by a newer version")
        order = list(data["order"])
        stamps = dict(zip(order, data["stamps"], strict=True))
        return cls(
            data["version"], [(int(f), int(n)) for f, n in data["blocks"]], order, stamps, int(data["level"]),
            bool(data["dedup"]), int(data["default_flags"]),
        )  # fmt: skip


def default_layout(version: rda.RdaVersion, flags: int) -> Layout:
    """The layout of an archive that has no original: no blocks, new members in blocks of ``flags``."""
    return Layout(version.name, default_flags=flags)


def detect_level(archive: rda.RdaArchive, entries: Iterable[rda.RdaEntry]) -> int:
    """The zlib level the compressed members were made with: the first of ``LEVELS`` that reproduces the stored bytes
    of a sample (1 when none does; ``differences`` then names the compression)."""
    seed, sample = archive.version.seed, []
    for e in entries:
        if e.flags & rda.FLAG_COMPRESSED and not e.contiguous and 0 < e.size <= SAMPLE_BYTES:
            raw = archive.stored_bytes(e)
            sample.append((archive.read_entry(e), rda.decrypt(raw, seed) if e.flags & rda.FLAG_ENCRYPTED else raw))
            if len(sample) == SAMPLE:
                break
    for level in LEVELS:
        if sample and all(zlib.compress(data, level) == packed for data, packed in sample):
            return level
    return 1


def read_layout(archive: rda.RdaArchive, name_of: Namer | None = None) -> Layout:
    """The layout of ``archive``; ``name_of(entry)`` gives a member's path (default: as stored). Where a later block
    holds a path again, the later one is the member (as the game reads it) and the earlier block lets it go."""
    name = name_of or (lambda e: e.path)
    version = archive.version
    if version is rda.V1_1:
        entries = list(archive.entries.values())
        packed = not entries or sum(bool(e.flags & rda.FLAG_COMPRESSED) for e in entries) * 2 >= len(entries)
        layout = Layout(
            version.name,
            [(int(packed), len(entries))] if entries else [],
            default_flags=rda.V1_FLAGS_ZLIB if packed else rda.V1_FLAGS_STORED,
        )
        for e in entries:
            layout.order.append(name(e))
            layout.stamps[name(e)] = e.mtime
            layout.stored_sizes[name(e)] = e.compressed_size
        layout.level = detect_level(archive, entries) if packed else 1
        return layout
    blocks: list[tuple[int, list[str]]] = []
    holder: dict[str, int] = {}
    by_path: dict[str, rda.RdaEntry] = {}
    for block, entries in archive.block_members():
        for e in entries:
            holder[name(e)] = len(blocks)
            if not block.flags & rda.FLAG_DELETED:
                by_path[name(e)] = e
            else:
                by_path.pop(name(e), None)
        blocks.append((block.flags, [name(e) for e in entries]))
    layout = Layout(version.name)
    seen: set[tuple[int, int]] = set()
    for index, (flags, paths) in enumerate(blocks):
        kept = [p for p in paths if holder[p] == index]
        if paths and not kept:
            continue  # every member of the block is in a later one
        layout.blocks.append((flags, len(kept)))
        layout.order += kept
        for p in kept:
            e = by_path.get(p)
            layout.stamps[p] = e.mtime if e else 0
            if e is not None:
                layout.stored_sizes[p] = e.compressed_size
                if not e.contiguous:
                    layout.dedup = layout.dedup or (e.offset, e.compressed_size) in seen
                    seen.add((e.offset, e.compressed_size))
    live = [f for f, n in layout.blocks if n and not f & rda.FLAG_DELETED]
    layout.default_flags = live[-1] if live else rda.FLAG_COMPRESSED
    layout.level = detect_level(archive, by_path.values())
    return layout


@dataclass
class Original:
    """The archive a project was made from, to take what nobody changed from: its entries by layout path and its
    directories (keyed as ``rda.RdaWriter`` looks them up)."""

    archive: rda.RdaArchive
    entries: dict[str, rda.RdaEntry]
    reuse: dict[tuple[str, bytes, int], bytes]  # directories and resident blobs, as ``rda.RdaWriter`` looks them up

    def stored(self, path: str, flags: int) -> rda.Stored | None:
        """The member as the archive stores it, when it lies there in a block of ``flags`` (not resident)."""
        e = self.entries.get(path)
        return rda.Stored(self.archive, e) if e is not None and not e.contiguous and e.flags == flags else None


def original_of(archive: rda.RdaArchive, name_of: Namer | None = None) -> Original:
    """``archive`` as an ``Original``; ``name_of`` names its members as ``read_layout`` did."""
    name = name_of or (lambda e: e.path)
    out = Original(archive, {name(e): e for e in archive.entries.values()}, {})
    if archive.version is not rda.V1_1:
        for block, entries in archive.block_members():
            if entries and not block.flags & rda.FLAG_DELETED:
                stored, decoded = archive.directory_of(block)
                out.reuse["directory", hashlib.blake2b(decoded, digest_size=16).digest(), block.flags] = stored
                if block.flags & rda.FLAG_CONTIGUOUS:
                    stored, decoded = archive.blob_of(block)
                    out.reuse["blob", hashlib.blake2b(decoded, digest_size=16).digest(), block.flags] = stored
    return out


def pack(
    layout: Layout,
    members: Mapping[str, bytes | Path],
    dest: str | Path,
    stamps: Mapping[str, int] | None = None,
    original: Original | None = None,
    unchanged: Callable[[str], bool] = lambda _p: False,
) -> Path:
    """The archive at ``dest`` (written whole or not at all) from ``members`` (path -> bytes or a file) in ``layout``.
    ``stamps`` gives the time of these members (else the layout's, else 0). A member for which ``unchanged(path)`` holds
    is taken from ``original`` as it is stored there; a directory that comes out as one of the original's too."""
    version, times = rda.version_named(layout.version), {**layout.stamps, **(stamps or {})}
    known = set(layout.live)  # a path of a deleted block that has a member again is a new member
    fresh = sorted(p for p in members if p not in known)

    def take(path: str, flags: int) -> tuple[str, rda.Member]:
        return path, (original and unchanged(path) and original.stored(path, flags)) or members[path]

    if version is rda.V1_1:
        flags = int(layout.default_flags != rda.V1_FLAGS_STORED)
        keep = [p for p in layout.live if p in members] + fresh
        return rda.write_v11_file(dest, [take(p, flags) for p in keep], times, compress=bool(flags))
    resident = any(f & rda.FLAG_CONTIGUOUS for f, _n in layout.blocks) or bool(
        layout.default_flags & rda.FLAG_CONTIGUOUS
    )
    reuse = original.reuse if original else None
    with rda.RdaWriter(
        dest, version, times, dedup=layout.dedup, allow_resident=resident, reuse=reuse, allow_empty=True
    ) as writer:
        at = 0
        for flags, count in layout.blocks:
            paths = layout.order[at : at + count]
            at += count
            if not count:
                writer.add_empty(flags)
            elif flags & rda.FLAG_DELETED:
                writer.add_deleted([p for p in paths if p not in members])
            else:
                writer.add_block([take(p, flags) for p in paths if p in members], flags, layout.level)
        if fresh:
            writer.add_block([take(p, layout.default_flags) for p in fresh], layout.default_flags, layout.level)
    return Path(dest)


def differences(original: Layout, rebuilt: Layout) -> list[str]:
    """What differs between two layouts, as ``ORDER``, ``BLOCKS``, ``STAMPS``, ``COMPRESSION``, ``SHARING``, ``FILES``
    (a member in one and not the other); empty when the layouts are the same."""
    out = []
    if set(original.order) != set(rebuilt.order):
        out.append(FILES)
    common = [p for p in original.order if p in set(rebuilt.order)]
    if common != [p for p in rebuilt.order if p in set(original.order)]:
        out.append(ORDER)
    if original.blocks != rebuilt.blocks:
        out.append(BLOCKS)
    if any(original.stamps.get(p) != rebuilt.stamps.get(p) for p in common):
        out.append(STAMPS)
    if any(original.stored_sizes.get(p) != rebuilt.stored_sizes.get(p) for p in common):
        out.append(COMPRESSION)
    if original.dedup != rebuilt.dedup:
        out.append(SHARING)
    return out
