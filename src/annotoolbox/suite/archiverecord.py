"""What an imported archive mod's project remembers about its archive, and the archive written again from the
project's files (docs/mod-import.md, section 16). Stdlib only.

``import-archive.json`` holds the archive's name and layout (``formats.rdalayout``), the stored paths that were
normalized, and what the import found about a rebuild; ``archive-original/`` holds a link to (or copy of) the archive
itself, so a member nobody changed is written back as it was stored. ``pack(project folder, record, files, destination)`` is
the one function that writes the archive, for the build and for the import's own check.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..formats import rda, rdalayout
from ..infra.buildcache import write_atomic
from . import importmeta
from .buildout import CFileRef

RECORD = "import-archive.json"
ORIGINAL_DIR = "archive-original"  # in the project: the archive as it was imported
FORMAT = 1
Source = CFileRef | Path | bytes


@dataclass
class SRecord:
    """``archive``: the file name the build writes (the imported one's, or ``<mod id>.rda`` for a folder of files);
    ``layout``: ``rdalayout.SLayout.to_json``; ``renamed``: layout path -> the path stored in the archive, for the
    members whose stored path was normalized; ``original``: whether ``archive-original/<archive>`` exists;
    ``identical`` and ``differences``: what the import's rebuild gave (``None``: nothing to compare with)."""

    archive: str
    layout: dict
    renamed: dict = field(default_factory=dict)
    original: bool = False
    identical: bool | None = None
    differences: list = field(default_factory=list)
    left_out: list = field(default_factory=list)  # [path, reason] of what the import did not take
    format: int = FORMAT


def record_path(folder: Path | str) -> Path:
    return Path(folder) / RECORD


def read_record(folder: Path | str) -> SRecord | None:
    """The record of an imported archive project, None for any other project."""
    try:
        data = json.loads(record_path(folder).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format", FORMAT) > FORMAT:
            return None
        return SRecord(
            **{field_name: value for field_name, value in data.items() if field_name in SRecord.__dataclass_fields__}
        )
    except (OSError, ValueError, TypeError):
        return None


def write_record(folder: Path | str, record: SRecord) -> None:
    write_atomic(record_path(folder), (json.dumps(asdict(record), ensure_ascii=False) + "\n").encode("utf-8"))


def original_path(folder: Path | str, record: SRecord) -> Path:
    return Path(folder) / ORIGINAL_DIR / record.archive


def layout_of(record: SRecord) -> rdalayout.SLayout:
    return rdalayout.SLayout.from_json(record.layout)


def original_of(folder: Path | str, record: SRecord) -> rdalayout.SOriginal | None:
    """The imported archive to take unchanged members from; None when it is not there or does not read any more."""
    path = original_path(folder, record)
    if not record.original or not path.is_file():
        return None
    inverse = {old: new for new, old in record.renamed.items()}
    try:
        return rdalayout.original_of(rda.CRdaArchive(path), lambda entry: inverse.get(entry.path, entry.path))
    except (rda.CRdaError, OSError):
        return None


def _digest(source: Source) -> str:
    if isinstance(source, bytes):
        return hashlib.sha256(source).hexdigest()
    with open(source, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def pack(
    folder: Path | str,
    record: SRecord,
    files: Mapping[str, Source],
    destination: Path,
    original: rdalayout.SOriginal | None = None,
) -> tuple[Path, int]:
    """The archive at ``destination`` from the project's ``files`` (game path -> bytes or a file) in the recorded layout; a
    member whose bytes are still the imported ones (the import's manifest) is taken from ``original``, the others are
    compressed again and get the time of the build. Returns ``(destination, members compressed again)``."""
    base = {relative_path.lower(): digest for relative_path, _size, digest in importmeta.read_manifest(Path(folder))}
    changed = {
        path for path, source in files.items() if not base.get(path.lower()) or _digest(source) != base[path.lower()]
    }
    now = int(time.time())
    members = {path: (source if isinstance(source, bytes) else Path(source)) for path, source in files.items()}
    rdalayout.pack(
        layout_of(record), members, destination, dict.fromkeys(changed, now), original, lambda path: path not in changed
    )
    return destination, len(changed)
