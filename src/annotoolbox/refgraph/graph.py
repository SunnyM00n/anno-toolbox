"""The reference graph of a game: which file names which, built once over the whole tree and kept beside the game.

``build(tree, cache, progress)`` is the entry (from the cache when it was made for the same stamp), ``CRefGraph`` answers
``referenced_by(path)``, ``references(path)`` and ``users(path)`` (the viewer's "Used by") and ``verdicts(paths)``
classes every file (``rules``: the whole chain from the entry points); ``unreferenced_csv`` writes the files no live
file names. docs/references.md.
"""

from __future__ import annotations

import bisect
import csv
import io
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..infra import buildcache
from ..models.fileusers import SAssetUse
from . import rules, scan
from .wwise import CWwiseIndex

VERSION = 2  # of the kept graph: bump when what is kept changes
Progress = Callable[[int, int], None]
READ_ERRORS = (OSError, ValueError, KeyError)  # a file the archives cannot give: skipped, the scan goes on


@dataclass
class CRefGraph:
    """The edges by source: a file (or ``file#guid``, an asset inside an asset file) -> the files it names (sorted).
    For an asset, ``names`` gives its name and ``fields`` the element each target is the value of (parallel to the
    targets; an asset none of whose names stand alone in an element has none)."""

    m_game: str = ""
    m_stamp: str = ""
    m_forward: dict[str, list[str]] = field(default_factory=dict)
    m_scanned: int = 0  # files read
    m_names: dict[str, str] = field(default_factory=dict)
    m_fields: dict[str, list[str]] = field(default_factory=dict)
    _m_reverse: dict[str, list[str]] | None = field(default=None, repr=False)
    _m_by_file: dict[str, list[str]] | None = field(default=None, repr=False)

    @property
    def reverse(self) -> dict[str, list[str]]:
        """The files named -> who names them."""
        if self._m_reverse is None:
            rev: dict[str, list[str]] = {}
            for source, targets in self.m_forward.items():
                for target in targets:
                    rev.setdefault(target, []).append(source)
            self._m_reverse = rev
        return self._m_reverse

    def referenced_by(self, path: str) -> list[str]:
        """What names ``path``: files, and ``file#guid`` for an asset inside an asset file."""
        return sorted(self.reverse.get(path, []))

    def references(self, path: str) -> list[str]:
        """The files ``path`` names (the assets inside it included)."""
        return sorted(self._targets(path))

    def _targets(self, path: str) -> set[str]:
        if self._m_by_file is None:  # a file -> its sources (itself and the assets inside it), once
            by_file: dict[str, list[str]] = {}
            for source in self.m_forward:
                by_file.setdefault(scan.split_source(source)[0], []).append(source)
            self._m_by_file = by_file
        return {target for member in self._m_by_file.get(path, []) for target in self.m_forward[member]}

    def users(self, path: str) -> tuple[list[str], list[SAssetUse]]:
        """Who names ``path``, for the viewer's "Used by": the files, and the assets (with the element they name it in)."""
        files, assets = [], []
        for source in self.referenced_by(path):
            file, guid = scan.split_source(source)
            if not guid:
                files.append(file)
                continue
            fields = self.m_fields.get(source)
            element = fields[bisect.bisect_left(self.m_forward[source], path)] if fields else ""
            assets.append(SAssetUse(int(guid), self.m_names.get(source, ""), element, file))
        return files, assets

    @property
    def named(self) -> set[str]:
        """The files some other file names."""
        return set(self.reverse)

    def verdicts(self, paths: Iterable[str]) -> dict[str, str]:
        """Each path's class (``rules.CLASSES``): what a live file names is referenced, what only unused files name is
        orphaned."""
        paths = list(paths)
        return rules.classify(paths, self.named, rules.reachable(paths, self._targets))

    def to_json(self) -> dict:
        return {"version": VERSION, "game": self.m_game, "stamp": self.m_stamp, "scanned": self.m_scanned,
                "forward": self.m_forward, "names": self.m_names, "fields": self.m_fields}  # fmt: skip

    @classmethod
    def from_json(cls, data: dict) -> CRefGraph:
        return cls(data.get("game", ""), data.get("stamp", ""), dict(data["forward"]), int(data.get("scanned", 0)),
                   dict(data.get("names", {})), dict(data.get("fields", {})))  # fmt: skip


def scan_tree(tree, progress: Progress | None = None) -> CRefGraph:
    """The graph of ``tree`` (a ``sources.gametree.CGameTree``): every carrier read once, every bank by its media ids."""
    graph = CRefGraph(tree.m_game, tree.m_stamp)
    files, bank_files = scan.carriers(tree.m_paths), scan.banks_of(tree.m_paths)
    wwise = CWwiseIndex(bank_files, tree.exists, tree.read)
    patterns, resolver = scan.name_patterns(tree.m_paths), scan.CResolver(tree.exists, tree.m_game, tree.m_paths, wwise)
    total = len(files) + len(bank_files)
    elements: dict[str, dict[str, str]] = {}  # asset -> target -> the element it is the value of
    for index, path in enumerate(files):
        try:
            data = tree.read(path)
        except READ_ERRORS:
            continue
        if len(data) > scan.SKIP_BYTES:
            continue
        graph.m_scanned += 1
        for ref in scan.file_refs(data, path, patterns, resolver):
            label = scan.asset_label(path, ref.guid)
            graph.m_forward.setdefault(label, []).append(ref.file)
            if ref.guid:
                graph.m_names[label] = ref.asset
                if ref.field:
                    elements.setdefault(label, {}).setdefault(ref.file, ref.field)
        if progress is not None and index % 1000 == 0:
            progress(index, total)
    for index, bank in enumerate(bank_files):
        try:
            found = scan.bank_refs(bank, tree.read(bank), tree.exists, tree.read)
        except READ_ERRORS:
            continue
        graph.m_scanned += 1
        if found:
            graph.m_forward[bank] = found
        if progress is not None:
            progress(len(files) + index, total)
    graph.m_forward = {source: sorted(set(targets)) for source, targets in graph.m_forward.items()}
    graph.m_fields = {
        asset: [target_elements.get(target, "") for target in graph.m_forward[asset]]
        for asset, target_elements in elements.items()
    }
    return graph


def cache_file(folder: Path, game: str) -> Path:
    """Where a game's kept graph lies: beside its archive index (``folder``)."""
    return Path(folder) / f"{game}-references.json"


def cached(tree, cache: Path | None) -> CRefGraph | None:
    """The kept graph of ``tree`` when ``cache`` (a JSON file) was made for the same stamp, else None."""
    if cache is None or not tree.m_stamp:
        return None
    data = buildcache.read_kept(cache, {"version": VERSION, "stamp": tree.m_stamp, "game": tree.m_game})
    if data is None:
        return None
    try:
        return CRefGraph.from_json(data)
    except (ValueError, TypeError, KeyError):
        return None


def build(tree, cache: Path | None = None, progress: Progress | None = None) -> CRefGraph:
    """The graph of ``tree``, from ``cache`` (a JSON file) when it was made for the same stamp, else scanned and kept."""
    kept = cached(tree, cache)
    if kept is not None:
        return kept
    graph = scan_tree(tree, progress)
    if cache is not None and tree.m_stamp:
        buildcache.write_kept(cache, graph.to_json())
    return graph


def unreferenced(graph: CRefGraph, paths: Iterable[str]) -> list[str]:
    """The paths no live file names (the classes ``rules.UNUSED``), sorted."""
    return unused(graph.verdicts(paths))


def unused(verdicts: dict[str, str]) -> list[str]:
    """The paths of the classes ``rules.UNUSED`` among ``verdicts``, sorted."""
    return sorted(path for path, verdict in verdicts.items() if verdict in rules.UNUSED)


def unreferenced_csv(tree, graph: CRefGraph, verdicts: dict[str, str] | None = None) -> str:
    """The list as CSV text: path, suffix, the archive it is read from, the class (unreferenced, or orphaned: only
    named by unused files). "No reference found in the data" only: the program may still load a file."""
    verdicts = verdicts if verdicts is not None else graph.verdicts(tree.m_paths)
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["path", "suffix", "archive", "class"])
    for path in unused(verdicts):
        writer.writerow([path, scan.suffix_of(path), tree.origin(path) or "", verdicts[path]])
    return out.getvalue()
