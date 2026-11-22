"""The asset index: every asset of a game's asset files by GUID, name, template, base asset and group, with the
byte range it fills in its file, built once by a scan of the files and kept in a cache; an asset is parsed only when
it is opened. Stdlib only.

``build_index`` scans (or loads the cached scan of) the files ``gamefiles.asset_files`` names; ``CAssetIndex.element``
parses one asset, ``references`` lists the assets that name a GUID. 1800's two exports are about half a gigabyte:
the scan reads tags with one regular expression and never builds a tree (``docs/tool-suite.md`` A2, "Risks").
"""

from __future__ import annotations

import gzip
import hashlib
import json
import mmap
import re
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..sources.overlay import normalize
from .gamefiles import CGameFiles
from .xmltree import parse

VERSION = 3  # of the cached scan: raise it when ``SEntry`` or the scan changes
TOKEN = re.compile(rb"<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<(/?)([A-Za-z_][\w.:-]*)?(?:\s[^>]*?)?(/?)>", re.S)
WANTED = {
    (b"Template",): "template",
    (b"BaseAssetGUID",): "base",
    (b"Values", b"Standard", b"GUID"): "guid",
    (b"Values", b"Standard", b"Name"): "name",
}
REF = re.compile(rb"-?\d{3,10}")  # a leaf that may be a GUID (the games' GUIDs have 3 to 10 digits)


@dataclass
class SEntry:
    guid: int
    name: str
    template: str
    base: int | None
    group: int  # into ``CAssetIndex.m_groups``
    file: int  # into ``CAssetIndex.m_files``
    start: int
    end: int


@dataclass
class CAssetIndex:
    m_files: list[str]
    m_local: list[Path]
    m_groups: list[tuple[str, ...]]
    m_entries: list[SEntry]
    m_refs: dict[int, list[int]] = field(default_factory=dict)  # GUID -> the assets whose values name it
    m_problems: list[str] = field(default_factory=list)
    m_group_ranges: dict[tuple[str, ...], tuple[int, int]] = field(default_factory=dict)  # a group's GUID range

    def __post_init__(self) -> None:
        self.m_by_guid = {
            entry.guid: entry for entry in self.m_entries
        }  # a later file's definition wins, as in the game
        self._m_maps: dict[int, mmap.mmap] = {}

    def raw(self, guid: int) -> bytes:
        """The asset's bytes as its file has them."""
        entry = self.m_by_guid[guid]
        return self._map(entry.file)[entry.start : entry.end]

    def element(self, guid: int) -> ET.Element:
        """The ``<Asset>`` element of ``guid``, parsed now."""
        return parse(self.raw(guid))

    def element_of(self, entry: SEntry) -> ET.Element:
        """The ``<Asset>`` element of one entry (a GUID two files define has an entry in each; ``element`` is the winner's)."""
        return parse(self._map(entry.file)[entry.start : entry.end])

    def references(self, guid: int) -> list[int]:
        return self.m_refs.get(guid, [])

    def children(self, guid: int) -> list[int]:
        """The assets that take ``guid`` as their base asset."""
        return [entry.guid for entry in self.m_entries if entry.base == guid]

    def search(self, query: str, limit: int = 500) -> list[SEntry]:
        """Assets whose GUID, name or template holds ``query`` (case-insensitive), a GUID match first."""
        lowered_query = query.strip().lower()
        if not lowered_query:
            return []
        exact = (
            [self.m_by_guid[int(lowered_query)]]
            if lowered_query.isdecimal() and int(lowered_query) in self.m_by_guid
            else []
        )
        rest = [
            entry
            for entry in self.m_entries
            if lowered_query in entry.name.lower()
            or lowered_query in entry.template.lower()
            or lowered_query in str(entry.guid)
        ]
        return (exact + [entry for entry in rest if entry not in exact])[:limit]

    def close(self) -> None:
        for mapping in self._m_maps.values():
            mapping.close()
        self._m_maps.clear()

    def _map(self, k: int) -> mmap.mmap:
        if k not in self._m_maps:
            with open(self.m_local[k], "rb") as handle:
                self._m_maps[k] = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        return self._m_maps[k]


class _CScan:
    """The running state of one file's scan (``scan_file``)."""

    def __init__(
        self, file: int, groups: list[tuple[str, ...]], group_ids: dict, ranges: dict | None = None,
        prefix: tuple[str, ...] = (), links: list | None = None,
    ) -> None:  # fmt: skip
        self.m_file, self.m_groups, self.m_group_ids, self.m_ranges = file, groups, group_ids, ranges
        self.m_links = links  # collects (group path, file) for each Group that names a ``FileName``
        self.m_stack: list[bytes] = []
        self.m_group_path: list[str] = list(prefix)  # one name per open Group ("" while it has none), below the prefix
        self.m_group_files: list[str] = []  # the ``FileName`` of each open Group ("" when it names none)
        self.m_asset: dict | None = None
        self.m_entries: list[SEntry] = []
        self.m_refs: list[tuple[int, int]] = []  # (named GUID, the asset's entry index)


def _group_id(scan: _CScan) -> int:
    key = tuple(part for part in scan.m_group_path if part)
    if key not in scan.m_group_ids:
        scan.m_group_ids[key] = len(scan.m_groups)
        scan.m_groups.append(key)
    return scan.m_group_ids[key]


def scan_file(
    data: bytes, file: int, groups: list, group_ids: dict, ranges: dict | None = None,
    prefix: tuple[str, ...] = (), links: list | None = None,
) -> tuple[list[SEntry], list[tuple[int, int]]]:  # fmt: skip
    """The assets of one file's bytes (children of an ``Assets`` element) and the GUID-like leaves in their values;
    ``ranges`` collects the groups' ``GUIDRangeMin`` / ``GUIDRangeMax`` (2205 has them); ``prefix`` is the group the
    file hangs under (``mounts``), ``links`` collects the files its groups name."""
    scan = _CScan(file, groups, group_ids, ranges, prefix, links)
    last_open: tuple[int, int] | None = None  # (stack depth, end of the open tag) when no tag followed it yet
    for match in TOKEN.finditer(data):
        closing, name, empty = match.group(1), match.group(2), match.group(3)
        if name is None and not closing:
            continue  # a comment, a declaration, CDATA
        if not closing:
            if empty:
                last_open = None
                continue
            _open(scan, name, match.start())
            last_open = (len(scan.m_stack), match.end())
            continue
        leaf = data[last_open[1] : match.start()] if last_open and last_open[0] == len(scan.m_stack) else None
        last_open = None
        _close(scan, leaf, match.end())
    return scan.m_entries, scan.m_refs


def _open(scan: _CScan, name: bytes, offset: int) -> None:
    if name == b"Group":
        scan.m_group_path.append("")
        scan.m_group_files.append("")
    elif name == b"Asset" and scan.m_asset is None and scan.m_stack and scan.m_stack[-1] == b"Assets":
        scan.m_asset = {"start": offset, "depth": len(scan.m_stack) + 1, "group": _group_id(scan)}
    scan.m_stack.append(name)


def _close(scan: _CScan, leaf: bytes | None, offset: int) -> None:
    name = scan.m_stack.pop() if scan.m_stack else b""
    asset = scan.m_asset
    if name == b"Name" and scan.m_stack and scan.m_stack[-1] == b"Group" and leaf is not None and scan.m_group_path:
        scan.m_group_path[-1] = leaf.decode("utf-8", "replace").strip()
    elif (
        name in (b"GUIDRangeMin", b"GUIDRangeMax")
        and scan.m_ranges is not None
        and scan.m_stack
        and scan.m_stack[-1] == b"Group"
    ):
        _group_range(scan, name, leaf)
    elif (
        name == b"FileName"
        and scan.m_stack
        and scan.m_stack[-1] == b"Group"
        and leaf is not None
        and scan.m_group_files
    ):
        scan.m_group_files[-1] = leaf.decode("utf-8", "replace").strip()
    elif name == b"Group" and scan.m_group_path:
        if scan.m_links is not None and scan.m_group_files and scan.m_group_files[-1]:
            scan.m_links.append((tuple(part for part in scan.m_group_path if part), scan.m_group_files[-1]))
        scan.m_group_path.pop()
        if scan.m_group_files:
            scan.m_group_files.pop()
    if asset is None:
        return
    if len(scan.m_stack) >= asset["depth"]:
        relative_names = tuple(scan.m_stack[asset["depth"] :]) + (name,)
        if leaf is not None:
            key = WANTED.get(relative_names)
            if key and key not in asset:
                asset[key] = leaf.decode("utf-8", "replace").strip()
            elif REF.fullmatch(leaf.strip()):
                asset.setdefault("refs", []).append(int(leaf))
    elif name == b"Asset":
        _finish(scan, asset, offset)
        scan.m_asset = None


def _group_range(scan: _CScan, name: bytes, leaf: bytes | None) -> None:
    key = tuple(part for part in scan.m_group_path if part)
    if leaf is None or not key or not leaf.strip().lstrip(b"-").isdigit():
        return
    min_guid, max_guid = scan.m_ranges.get(key, (0, 2**31 - 1))
    value = int(leaf)
    scan.m_ranges[key] = (value, max_guid) if name == b"GUIDRangeMin" else (min_guid, value)


def _finish(scan: _CScan, asset: dict, end: int) -> None:
    try:
        guid = int(asset.get("guid", ""))
    except ValueError:
        return  # an asset without a GUID (a template's sample): nothing can name it
    base = asset.get("base", "")
    entry = SEntry(guid, asset.get("name", ""), asset.get("template", ""), int(base) if base.lstrip("-").isdigit() else None,
                  asset["group"], scan.m_file, asset["start"], end)  # fmt: skip
    scan.m_entries.append(entry)
    scan.m_refs += [(ref, len(scan.m_entries) - 1) for ref in asset.get("refs", ()) if ref != guid]


def _inherit(entries: list[SEntry]) -> None:
    """An asset made from a base asset names no template: it is its base's."""
    by_guid = {entry.guid: entry for entry in entries}
    for entry in entries:
        seen, node = set(), entry
        while not node.template and node.base in by_guid and node.guid not in seen:
            seen.add(node.guid)
            node = by_guid[node.base]
        entry.template = entry.template or node.template


def mounts(paths: list[str], local: list[Path], limit: int = 8_000_000) -> dict[str, tuple[str, ...]]:
    """Where each asset file hangs in the group tree, for games whose asset list names its groups' files (2205: a
    group with a ``FileName`` is the root group of that file, which may name more): the group path of the file's
    parent, per file path (normalised). A file nobody names, and every file but the small ones that hold links, is a
    root (an empty prefix)."""
    wanted = {normalize(wanted_path): wanted_path for wanted_path in paths}
    relative: dict[str, list[tuple[tuple[str, ...], str]]] = {}
    for path, file in zip(paths, local, strict=True):
        if file.stat().st_size > limit:
            continue
        with open(file, "rb") as handle:
            data = handle.read()
        if b"<FileName>" not in data:
            continue
        found: list = []
        scan_file(data, 0, [], {}, None, (), found)
        relative[normalize(path)] = [(key, normalize(target)) for key, target in found if normalize(target) in wanted]
    named = {target for links in relative.values() for _key, target in links}
    out: dict[str, tuple[str, ...]] = {}
    todo = [(wanted_path, ()) for wanted_path in wanted if wanted_path not in named]
    while todo:
        path, prefix = todo.pop()
        if path in out:
            continue
        out[path] = prefix
        todo += [(target, (*prefix, *key)) for key, target in relative.get(path, ()) if target not in out]
    return out


def _cache_path(cache: Path, files: CGameFiles, paths: list[str]) -> Path:
    digest = hashlib.sha1(f"{VERSION}|{files.m_stamp}|{'|'.join(paths)}".encode()).hexdigest()[:16]
    return cache / "asset-index" / f"{digest}.json.gz"


def _load(path: Path, files: list[str], local: list[Path]) -> CAssetIndex | None:
    try:
        data = json.loads(gzip.decompress(path.read_bytes()))
        entries = [SEntry(*row) for row in data["entries"]]
        refs = {int(k): targets for k, targets in data["refs"].items()}
        ranges = {tuple(group): (min_guid, max_guid) for group, min_guid, max_guid in data.get("group_ranges", [])}
        groups = [tuple(group) for group in data["groups"]]
        return CAssetIndex(files, local, groups, entries, refs, data.get("problems", []), ranges)
    except (OSError, ValueError, EOFError, zlib.error, KeyError, TypeError, AttributeError):
        return None  # a damaged cache file: the game is scanned again


def _save(path: Path, index: CAssetIndex) -> None:
    data = {
        "entries": [
            [entry.guid, entry.name, entry.template, entry.base, entry.group, entry.file, entry.start, entry.end]
            for entry in index.m_entries
        ],
        "groups": [list(group) for group in index.m_groups],
        "refs": {str(k): targets for k, targets in index.m_refs.items()},
        "problems": index.m_problems,
        "group_ranges": [
            [list(group), min_guid, max_guid] for group, (min_guid, max_guid) in index.m_group_ranges.items()
        ],
    }
    write_atomic(path, gzip.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"), 3))


def build_index(
    files: CGameFiles, paths: list[str], progress: Callable[[str], None] | None = None, follow_links: bool = False
) -> CAssetIndex:
    """The index of the asset files ``paths`` of ``files``: the cached scan when the game is unchanged, else a new
    scan (and the cache written). ``follow_links``: the groups' ``FileName`` links join the files into one tree."""
    say = progress or (lambda _text: None)
    cache = _cache_path(files.m_cache, files, paths)
    local = [files.local(file_path) for file_path in paths]
    if cache.is_file():
        found = _load(cache, paths, local)
        if found is not None:
            return found
    groups: list[tuple[str, ...]] = []
    group_ids: dict = {}
    entries: list[SEntry] = []
    pairs: list[tuple[int, int]] = []
    ranges: dict[tuple[str, ...], tuple[int, int]] = {}
    mounted = mounts(paths, local) if follow_links else {}
    for k, path in enumerate(local):
        say(f"indexing {paths[k]} ({path.stat().st_size // 1_000_000} MB)")
        if not path.stat().st_size:
            continue  # an empty file has no assets (and cannot be mapped)
        with open(path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            found, refs = scan_file(data, k, groups, group_ids, ranges, mounted.get(normalize(paths[k]), ()))
        offset = len(entries)
        entries += found
        pairs += [(ref, i + offset) for ref, i in refs]
    _inherit(entries)
    index = CAssetIndex(paths, local, groups, entries, m_group_ranges=ranges)
    seen: dict[int, list[int]] = {}
    for named, i in pairs:
        if named in index.m_by_guid:
            users = seen.setdefault(named, [])
            if not users or users[-1] != entries[i].guid:
                users.append(entries[i].guid)
    index.m_refs = seen
    _save(cache, index)
    say(f"{len(entries)} assets in {len(paths)} files")
    return index
