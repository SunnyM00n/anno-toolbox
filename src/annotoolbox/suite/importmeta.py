"""What an imported project remembers about the mod it came from (docs/mod-import.md, section 3.1): ``import.json``
(the source, the project's identity at the import, the nested mods, the counts), ``import-files.jsonl`` (one
``[path, size, sha256]`` line per file, read as a stream: a mod may hold half a million; the hash is "" when the import
was told not to read the files) and the original ``modinfo`` bytes. ``kept_modinfo(project)`` is what the build writes
instead of a regenerated ``modinfo``. Stdlib only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from . import modinfokeep

META_FILE = "import.json"
FILES_FILE = "import-files.jsonl"
MOD_FILES = "mod-files"  # the project's folder for the files no app edits: ``mod-files/<game>/<mod path>``
MODINFO_ORIGINAL = "modinfo.original"  # in ``mod-files``: the mod's own modinfo bytes
FORMAT = 1
IDENTITY = ("mod_id", "name", "version", "category", "description", "dependencies")  # the project's fields it keeps


@dataclass
class SMeta:
    """The record ``import.json``. ``identity``: the project's fields as the import set them, so a later edit is the
    difference to it. ``owners``: the files per owning app (recorded; phase 1 keeps them all in ``mod-files``)."""

    source: str = ""
    game: str = "anno117"
    modinfo: str = "modinfo.json"  # the file name the original had (``.jsonc`` too)
    identity: dict = field(default_factory=dict)
    nested: list = field(default_factory=list)  # {"path", "mod_id", "version"} of the mods inside the mod
    files: int = 0
    size: int = 0
    linked: int = 0
    copied: int = 0
    owners: dict = field(default_factory=dict)
    taken: dict = field(default_factory=dict)  # whole-file app -> files the import put into its own folder
    carried: dict = field(default_factory=dict)  # extension -> files, for the files no app takes
    not_understood: dict = field(default_factory=dict)  # extension -> files the sort has no kind for
    guid: dict = field(default_factory=dict)  # {"added": n, "range": [lo, hi], "detected": bool}
    format: int = FORMAT


def meta_path(folder: Path) -> Path:
    return Path(folder) / META_FILE


def read_meta(folder: Path) -> SMeta | None:
    """The record of an imported project, None for a project that was not made by an import."""
    try:
        data = json.loads(meta_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format", FORMAT) > FORMAT:
        return None
    return SMeta(
        **{field_name: value for field_name, value in data.items() if field_name in SMeta.__dataclass_fields__}
    )


def write_meta(folder: Path, meta: SMeta) -> None:
    write_atomic(meta_path(folder), (json.dumps(asdict(meta), indent=1, ensure_ascii=False) + "\n").encode("utf-8"))


def read_manifest(folder: Path) -> Iterator[tuple[str, int, str]]:
    """``(mod path, size, sha256)`` of every file the import took, one line at a time."""
    path = Path(folder) / FILES_FILE
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                relative_path, size, digest = json.loads(line)
                yield relative_path, size, digest


def identity_of(project) -> dict:
    """The fields of ``IDENTITY`` as ``project`` holds them now."""
    return {k: getattr(project, k) for k in IDENTITY}


def changes_of(project, meta: SMeta) -> dict[str, object]:
    """The ``modinfo`` fields the person edited since the import: ``modinfokeep.FIELDS`` -> new value."""
    base, now = meta.identity, identity_of(project)
    out: dict[str, object] = {}
    for field_name, key in (("ModID", "mod_id"), ("Version", "version"), *modinfokeep.TEXT_FIELDS.items()):
        if now[key] != base.get(key):
            out[field_name] = now[key]
    old, new = base.get("dependencies") or {}, now["dependencies"] or {}
    for kind in modinfokeep.DEPENDENCY_KINDS:
        if list(new.get(kind) or []) != list(old.get(kind) or []):
            out[kind] = list(new.get(kind) or [])
    return out


def kept_modinfo(project) -> tuple[str, bytes] | None:
    """``(file name, bytes)`` of the ``modinfo`` the build writes for an imported project: the original, with only
    the edited identity fields patched in; None for a project that was not imported."""
    meta = read_meta(project.folder)
    original = project.folder / MOD_FILES / MODINFO_ORIGINAL
    if meta is None or not original.is_file():
        return None
    return meta.modinfo, modinfokeep.patched(original.read_bytes(), changes_of(project, meta), project.game)
