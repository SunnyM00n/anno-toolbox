"""The asset index: every asset of a game's asset files by GUID, name, template, base asset and group, with the
byte range it fills in its file, built once by a scan of the files and kept in a cache; an asset is parsed only when
it is opened. Stdlib only.

``build_index`` scans (or loads the cached scan of) the files ``gamefiles.asset_files`` names; ``AssetIndex.element``
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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..sources.overlay import normalize
from .gamefiles import GameFiles
from .xmltree import parse

VERSION = 3  # of the cached scan: raise it when ``Entry`` or the scan changes
TOKEN = re.compile(rb"<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<(/?)([A-Za-z_][\w.:-]*)?(?:\s[^>]*?)?(/?)>", re.S)
WANTED = {
    (b"Template",): "template",
    (b"BaseAssetGUID",): "base",
    (b"Values", b"Standard", b"GUID"): "guid",
    (b"Values", b"Standard", b"Name"): "name",
}
REF = re.compile(rb"-?\d{3,10}")  # a leaf that may be a GUID (the games' GUIDs have 3 to 10 digits)


@dataclass
class Entry:
    guid: int
    name: str
    template: str
    base: int | None
    group: int  # into ``AssetIndex.groups``
    file: int  # into ``AssetIndex.files``
    start: int
    end: int


@dataclass
class AssetIndex:
    files: list[str]
    local: list[Path]
    groups: list[tuple[str, ...]]
    entries: list[Entry]
    refs: dict[int, list[int]] = field(default_factory=dict)  # GUID -> the assets whose values name it
    problems: list[str] = field(default_factory=list)
    group_ranges: dict[tuple[str, ...], tuple[int, int]] = field(default_factory=dict)  # a group's GUID range

    def __post_init__(self) -> None:
        self.by_guid = {e.guid: e for e in self.entries}  # a later file's definition wins, as in the game
        self._maps: dict[int, mmap.mmap] = {}

    def raw(self, guid: int) -> bytes:
        """The asset's bytes as its file has them."""
        e = self.by_guid[guid]
        return self._map(e.file)[e.start : e.end]

    def element(self, guid: int) -> ET.Element:
        """The ``<Asset>`` element of ``guid``, parsed now."""
        return parse(self.raw(guid))

    def element_of(self, entry: Entry) -> ET.Element:
        """The ``<Asset>`` element of one entry (a GUID two files define has an entry in each; ``element`` is the winner's)."""
        return parse(self._map(entry.file)[entry.start : entry.end])

    def references(self, guid: int) -> list[int]:
        return self.refs.get(guid, [])

    def children(self, guid: int) -> list[int]:
        """The assets that take ``guid`` as their base asset."""
        return [e.guid for e in self.entries if e.base == guid]

    def search(self, query: str, limit: int = 500) -> list[Entry]:
        """Assets whose GUID, name or template holds ``query`` (case-insensitive), a GUID match first."""
        q = query.strip().lower()
        if not q:
            return []
        exact = [self.by_guid[int(q)]] if q.isdigit() and int(q) in self.by_guid else []
        rest = [e for e in self.entries if q in e.name.lower() or q in e.template.lower() or q in str(e.guid)]
        return (exact + [e for e in rest if e not in exact])[:limit]

    def close(self) -> None:
        for m in self._maps.values():
            m.close()
        self._maps.clear()

    def _map(self, k: int) -> mmap.mmap:
        if k not in self._maps:
            with open(self.local[k], "rb") as f:
                self._maps[k] = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        return self._maps[k]


class _Scan:
    """The running state of one file's scan (``scan_file``)."""

    def __init__(
        self, file: int, groups: list[tuple[str, ...]], group_ids: dict, ranges: dict | None = None,
        prefix: tuple[str, ...] = (), links: list | None = None,
    ) -> None:  # fmt: skip
        self.file, self.groups, self.group_ids, self.ranges = file, groups, group_ids, ranges
        self.links = links  # collects (group path, file) for each Group that names a ``FileName``
        self.stack: list[bytes] = []
        self.group_path: list[str] = list(prefix)  # one name per open Group ("" while it has none), below the prefix
        self.group_files: list[str] = []  # the ``FileName`` of each open Group ("" when it names none)
        self.asset: dict | None = None
        self.entries: list[Entry] = []
        self.refs: list[tuple[int, int]] = []  # (named GUID, the asset's entry index)


def _group_id(scan: _Scan) -> int:
    key = tuple(n for n in scan.group_path if n)
    if key not in scan.group_ids:
        scan.group_ids[key] = len(scan.groups)
        scan.groups.append(key)
    return scan.group_ids[key]


def scan_file(
    data: bytes, file: int, groups: list, group_ids: dict, ranges: dict | None = None,
    prefix: tuple[str, ...] = (), links: list | None = None,
) -> tuple[list[Entry], list[tuple[int, int]]]:  # fmt: skip
    """The assets of one file's bytes (children of an ``Assets`` element) and the GUID-like leaves in their values;
    ``ranges`` collects the groups' ``GUIDRangeMin`` / ``GUIDRangeMax`` (2205 has them); ``prefix`` is the group the
    file hangs under (``mounts``), ``links`` collects the files its groups name."""
    s = _Scan(file, groups, group_ids, ranges, prefix, links)
    last_open: tuple[int, int] | None = None  # (stack depth, end of the open tag) when no tag followed it yet
    for m in TOKEN.finditer(data):
        closing, name, empty = m.group(1), m.group(2), m.group(3)
        if name is None and not closing:
            continue  # a comment, a declaration, CDATA
        if not closing:
            if empty:
                last_open = None
                continue
            _open(s, name, m.start())
            last_open = (len(s.stack), m.end())
            continue
        leaf = data[last_open[1] : m.start()] if last_open and last_open[0] == len(s.stack) else None
        last_open = None
        _close(s, leaf, m.end())
    return s.entries, s.refs


def _open(s: _Scan, name: bytes, at: int) -> None:
    if name == b"Group":
        s.group_path.append("")
        s.group_files.append("")
    elif name == b"Asset" and s.asset is None and s.stack and s.stack[-1] == b"Assets":
        s.asset = {"start": at, "depth": len(s.stack) + 1, "group": _group_id(s)}
    s.stack.append(name)


def _close(s: _Scan, leaf: bytes | None, at: int) -> None:
    name = s.stack.pop() if s.stack else b""
    a = s.asset
    if name == b"Name" and s.stack and s.stack[-1] == b"Group" and leaf is not None and s.group_path:
        s.group_path[-1] = leaf.decode("utf-8", "replace").strip()
    elif name in (b"GUIDRangeMin", b"GUIDRangeMax") and s.ranges is not None and s.stack and s.stack[-1] == b"Group":
        _group_range(s, name, leaf)
    elif name == b"FileName" and s.stack and s.stack[-1] == b"Group" and leaf is not None and s.group_files:
        s.group_files[-1] = leaf.decode("utf-8", "replace").strip()
    elif name == b"Group" and s.group_path:
        if s.links is not None and s.group_files and s.group_files[-1]:
            s.links.append((tuple(n for n in s.group_path if n), s.group_files[-1]))
        s.group_path.pop()
        if s.group_files:
            s.group_files.pop()
    if a is None:
        return
    if len(s.stack) >= a["depth"]:
        rel = tuple(s.stack[a["depth"] :]) + (name,)
        if leaf is not None:
            key = WANTED.get(rel)
            if key and key not in a:
                a[key] = leaf.decode("utf-8", "replace").strip()
            elif REF.fullmatch(leaf.strip()):
                a.setdefault("refs", []).append(int(leaf))
    elif name == b"Asset":
        _finish(s, a, at)
        s.asset = None


def _group_range(s: _Scan, name: bytes, leaf: bytes | None) -> None:
    key = tuple(n for n in s.group_path if n)
    if leaf is None or not key or not leaf.strip().lstrip(b"-").isdigit():
        return
    lo, hi = s.ranges.get(key, (0, 2**31 - 1))
    value = int(leaf)
    s.ranges[key] = (value, hi) if name == b"GUIDRangeMin" else (lo, value)


def _finish(s: _Scan, a: dict, end: int) -> None:
    try:
        guid = int(a.get("guid", ""))
    except ValueError:
        return  # an asset without a GUID (a template's sample): nothing can name it
    base = a.get("base", "")
    entry = Entry(guid, a.get("name", ""), a.get("template", ""), int(base) if base.lstrip("-").isdigit() else None,
                  a["group"], s.file, a["start"], end)  # fmt: skip
    s.entries.append(entry)
    s.refs += [(r, len(s.entries) - 1) for r in a.get("refs", ()) if r != guid]


def _inherit(entries: list[Entry]) -> None:
    """An asset made from a base asset names no template: it is its base's."""
    by_guid = {e.guid: e for e in entries}
    for e in entries:
        seen, node = set(), e
        while not node.template and node.base in by_guid and node.guid not in seen:
            seen.add(node.guid)
            node = by_guid[node.base]
        e.template = e.template or node.template


def mounts(paths: list[str], local: list[Path], limit: int = 8_000_000) -> dict[str, tuple[str, ...]]:
    """Where each asset file hangs in the group tree, for games whose asset list names its groups' files (2205: a
    group with a ``FileName`` is the root group of that file, which may name more): the group path of the file's
    parent, per file path (normalised). A file nobody names, and every file but the small ones that hold links, is a
    root (an empty prefix)."""
    wanted = {normalize(p): p for p in paths}
    relative: dict[str, list[tuple[tuple[str, ...], str]]] = {}
    for path, file in zip(paths, local, strict=True):
        if file.stat().st_size > limit:
            continue
        with open(file, "rb") as f:
            data = f.read()
        if b"<FileName>" not in data:
            continue
        found: list = []
        scan_file(data, 0, [], {}, None, (), found)
        relative[normalize(path)] = [(key, normalize(target)) for key, target in found if normalize(target) in wanted]
    named = {target for links in relative.values() for _key, target in links}
    out: dict[str, tuple[str, ...]] = {}
    todo = [(p, ()) for p in wanted if p not in named]
    while todo:
        path, prefix = todo.pop()
        if path in out:
            continue
        out[path] = prefix
        todo += [(target, (*prefix, *key)) for key, target in relative.get(path, ()) if target not in out]
    return out


def _cache_path(cache: Path, files: GameFiles, paths: list[str]) -> Path:
    digest = hashlib.sha1(f"{VERSION}|{files.stamp}|{'|'.join(paths)}".encode()).hexdigest()[:16]
    return cache / "asset-index" / f"{digest}.json.gz"


def _load(path: Path, files: list[str], local: list[Path]) -> AssetIndex | None:
    try:
        data = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, ValueError):
        return None
    entries = [Entry(*row) for row in data["entries"]]
    refs = {int(k): v for k, v in data["refs"].items()}
    ranges = {tuple(g): (lo, hi) for g, lo, hi in data.get("group_ranges", [])}
    return AssetIndex(files, local, [tuple(g) for g in data["groups"]], entries, refs, data.get("problems", []), ranges)


def _save(path: Path, index: AssetIndex) -> None:
    data = {
        "entries": [[e.guid, e.name, e.template, e.base, e.group, e.file, e.start, e.end] for e in index.entries],
        "groups": [list(g) for g in index.groups],
        "refs": {str(k): v for k, v in index.refs.items()},
        "problems": index.problems,
        "group_ranges": [[list(g), lo, hi] for g, (lo, hi) in index.group_ranges.items()],
    }
    write_atomic(path, gzip.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"), 3))


def build_index(
    files: GameFiles, paths: list[str], progress: Callable[[str], None] | None = None, follow_links: bool = False
) -> AssetIndex:
    """The index of the asset files ``paths`` of ``files``: the cached scan when the game is unchanged, else a new
    scan (and the cache written). ``follow_links``: the groups' ``FileName`` links join the files into one tree."""
    say = progress or (lambda _text: None)
    cache = _cache_path(files.cache, files, paths)
    local = [files.local(p) for p in paths]
    if cache.is_file():
        found = _load(cache, paths, local)
        if found is not None:
            return found
    groups: list[tuple[str, ...]] = []
    group_ids: dict = {}
    entries: list[Entry] = []
    pairs: list[tuple[int, int]] = []
    ranges: dict[tuple[str, ...], tuple[int, int]] = {}
    mounted = mounts(paths, local) if follow_links else {}
    for k, path in enumerate(local):
        say(f"indexing {paths[k]} ({path.stat().st_size // 1_000_000} MB)")
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            found, refs = scan_file(data, k, groups, group_ids, ranges, mounted.get(normalize(paths[k]), ()))
        offset = len(entries)
        entries += found
        pairs += [(r, i + offset) for r, i in refs]
    _inherit(entries)
    index = AssetIndex(paths, local, groups, entries, group_ranges=ranges)
    seen: dict[int, list[int]] = {}
    for named, i in pairs:
        if named in index.by_guid:
            users = seen.setdefault(named, [])
            if not users or users[-1] != entries[i].guid:
                users.append(entries[i].guid)
    index.refs = seen
    _save(cache, index)
    say(f"{len(entries)} assets in {len(paths)} files")
    return index
