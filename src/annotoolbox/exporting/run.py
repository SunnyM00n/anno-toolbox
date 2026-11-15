"""Export game files: one file or many, each as stored and in the converted forms its kind has, with an index.

``export_path(source, path, formats, out)`` exports one file, ``export_many`` a list (and writes the index). A
``Source`` is anything that reads game paths (``sources.gametree.GameTree``, or ``FolderSource`` for loose files).
A file that cannot be exported is an ``Entry`` with status "failed" and the reason, never an exception: a batch goes
on. The per-kind work is in ``textures``, ``pictures``, ``sounds``, ``movies``, ``models`` and ``data``; each takes a
``Job`` and returns the files it makes (``Made``), this module writes them. docs/exporting.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..infra.buildcache import write_atomic
from . import kinds
from .index import write_index


class Source(Protocol):
    """Where game files are read from: ``read``, ``exists`` and ``origins`` (the archives that carry a path)."""

    def read(self, path: str) -> bytes: ...

    def exists(self, path: str) -> bool: ...

    def origins(self, path: str) -> list[str]: ...


class FolderSource:
    """A folder of loose files as a source; paths are relative to it, with forward slashes."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def exists(self, path: str) -> bool:
        return (self.root / path).is_file()

    def read(self, path: str) -> bytes:
        return (self.root / path).read_bytes()

    def origins(self, path: str) -> list[str]:
        return [str(self.root)] if self.exists(path) else []

    def under(self, prefix: str) -> list[str]:
        """The files below a folder (``a/b/``), as paths relative to the root, sorted."""
        base = self.root / prefix
        return (
            sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file()) if base.is_dir() else []
        )


@dataclass
class Options:
    """What the exports can be asked for beyond the forms. ``png_depth`` is 8 or 16 for a texture's ``png`` form;
    ``all_mips`` writes every mip level (else the largest), ``all_faces`` every face of a cube or array and every
    slice; ``hdr_form`` is "exr" or "hdr" for a float texture; ``exposure`` (stops) brightens a float texture's
    8-bit PNG; ``axes`` ("gltf" mirrors, "anno" keeps the stored values), ``strict``, ``lods``, ``with_textures`` and
    ``anims_folder`` are the model export's (docs/rdm-gltf.md); ``language`` limits language texts to one language;
    ``force`` overwrites files that exist."""

    png_depth: int = 8
    all_mips: bool = False
    all_faces: bool = True
    all_slices: bool = True
    hdr_form: str = "exr"
    exposure: float = 0.0
    axes: str = "gltf"
    strict: bool = False
    lods: bool = False
    with_textures: bool = True
    anims_folder: str = ""
    language: str = ""
    force: bool = False


@dataclass
class Entry:
    """What happened to one source file: the kind, the archive it came from, the files written (relative to the output
    folder), the size of the source, the status ("written", "skipped", "failed", "tool-missing") and notes."""

    source: str
    kind: str
    archive: str = ""
    files: list[str] = field(default_factory=list)
    size: int = 0
    status: str = "written"
    notes: list[str] = field(default_factory=list)


@dataclass
class Made:
    """What an exporter makes: files by name (relative to the output folder) and notes; ``status`` other than
    "written" says why a form was not made ("tool-missing", "failed")."""

    files: dict[str, bytes] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    status: str = "written"


@dataclass
class Job:
    """One file to export: its game path, its bytes, the forms asked for (keys), the options and the source (for the
    files beside it: a bank's media, a model's textures)."""

    source: Source
    path: str
    data: bytes
    formats: frozenset[str]
    options: Options

    def name(self, suffix: str, tag: str = "") -> str:
        """The output name of a form: the game path with its suffix replaced, ``tag`` added to the stem."""
        folder, _, file = self.path.rpartition("/")
        stem = file.rsplit(".", 1)[0] if "." in file else file
        return f"{folder + '/' if folder else ''}{stem}{tag}{suffix}"

    def folder(self) -> str:
        """The folder named like the file (its stem), for a form that makes many files."""
        return self.name("")


def _exporters() -> dict[str, Callable[[Job], Made]]:
    from . import data, models, movies, pictures, sounds, textures

    return {
        "texture": textures.export,
        "picture": pictures.export,
        "movie": movies.export,
        "sound": sounds.export_sound,
        "bank": sounds.export_bank,
        "model": models.export,
        "data": data.export_data,
        "text": data.export_text,
        "ui": data.export_ui,
        "effect": data.export_effect,
        "island": data.export_island,
    }


def _clean(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


@dataclass
class Written:
    """What ``_write`` did with one entry's files: names written, left alone (exist), and not written with why."""

    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _target(out: Path, name: str) -> Path:
    """The file an output name stands for; ``ValueError`` when it would land outside ``out`` (``..``, a drive)."""
    target = (out / name).resolve()
    if not target.is_relative_to(out.resolve()):
        raise ValueError("the name leaves the output folder")
    return target


def _put(target: Path, body: bytes) -> None:
    """Whole or not at all: a file that stops half way is later skipped as existing."""
    write_atomic(target, body)


def _write(out: Path, files: dict[str, bytes], force: bool, claimed: dict[str, str], source: str) -> Written:
    """Write ``files`` below ``out``. ``claimed`` maps the names this run already made to their source: two sources
    that make one name (``a.tga`` and ``a.png`` both give ``a.png``) must not overwrite each other."""
    done = Written()
    for name, body in files.items():
        other = claimed.get(name.lower())
        if other is not None and other != source:
            done.errors.append(f"{name}: not written, {other} made it already")
            continue
        try:
            target = _target(out, name)
            if target.exists() and not force:
                done.skipped.append(name)
            else:
                _put(target, body)
                done.written.append(name)
        except (OSError, ValueError) as exc:
            done.errors.append(f"{name}: not written ({exc})")
            continue
        claimed[name.lower()] = source
    return done


def export_path(
    source: Source,
    path: str,
    formats: set[str] | None,
    out: Path,
    options: Options | None = None,
    claimed: dict[str, str] | None = None,
) -> list[Entry]:
    """Export one game file into ``out``: as stored (always), and each other key of ``formats`` its kind knows. Keys
    its kind does not know are ignored; with none left only the stored file is written. One ``Entry``. ``claimed``:
    the output names a batch has made so far (``export_many`` keeps it)."""
    options, path = options or Options(), _clean(path)
    kind = kinds.kind_of(path)
    entry = Entry(path, kind)
    try:
        entry.archive = next(iter(source.origins(path)), "")
        data = source.read(path)
    except (OSError, KeyError, ValueError) as exc:
        entry.status, entry.notes = "failed", [f"not read: {exc}"]
        return [entry]
    entry.size = len(data)
    wanted = frozenset(f.key for f in kinds.formats_for(kind)) & (formats or frozenset())
    files: dict[str, bytes] = {path: data}
    exporter = _exporters().get(kind)
    if wanted - {"raw"} and exporter is not None:
        try:
            made = exporter(Job(source, path, data, wanted, options))
        except Exception as exc:  # one file's failure never stops a batch
            made = Made(notes=[f"{type(exc).__name__}: {exc}"], status="failed")
        files.update(made.files)
        entry.notes += made.notes
        entry.status = made.status
    done = _write(out, files, options.force, {} if claimed is None else claimed, path)
    entry.files = done.written
    if done.skipped:
        entry.notes.append(f"left as they were (exist; force overwrites): {', '.join(done.skipped)}")
        if not done.written and entry.status == "written":
            entry.status = "skipped"
    if done.errors:
        entry.notes += done.errors
        entry.status = "failed"
    return [entry]


def export_many(
    source: Source,
    paths: list[str],
    formats: set[str],
    out: Path,
    options: Options | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[Entry]:
    """Export every path, then write ``index.json`` and ``index.csv`` into ``out``. ``progress(done, total, path)`` is
    called before each file; ``cancelled()`` true stops the run (the entries so far are indexed)."""
    entries: list[Entry] = []
    claimed: dict[str, str] = {}
    try:
        for n, path in enumerate(paths):
            if cancelled is not None and cancelled():
                break
            if progress is not None:
                progress(n, len(paths), path)
            entries += export_path(source, path, formats, out, options, claimed)
    finally:  # a stop part way still leaves the index of what was done
        write_index(out, entries)
    return entries


def missing_tools(formats: set[str]) -> str:
    """ "" when every optional tool the forms need is there, else a message naming each missing tool and where it goes
    (the tools are never shipped; the stored files are always exported)."""
    from . import movies, sounds

    lines = []
    if formats & {"mp4", "webm"}:
        lines += movies.missing()
    if "ogg" in formats:
        lines += sounds.missing()
    return "\n".join(lines)
