"""A big import that is stopped or cut short is resumable, or it leaves nothing (docs/mod-import.md, section 14).

While an import runs the new project holds ``import-state.json`` (the source and the options) and the folder is listed
in a small registry file beside the Toolbox's settings. A normal end removes both. What is left after a stop that asked
to keep its work, or after the program ended in the middle, is found by ``pending`` at the next start: the person
resumes it (``resume``: the files already placed and listed in the manifest are not placed again) or removes it
(``discard``). Stdlib only.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from . import importmeta

JOURNAL = "import-state.json"
PENDING = "pending-imports.json"
STOPPED, RUNNING = "stopped", "importing"
Say = Callable[[str], None]


@dataclass
class Journal:
    """``import-state.json``: what a resume needs to run the same import again."""

    source: str = ""
    link: bool = True
    hashed: bool = True
    existed: bool = False  # the folder was there (empty) before the import: it stays when the import is removed
    state: str = RUNNING
    started: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class Pending:
    """A half-made import: its folder and journal."""

    folder: Path
    journal: Journal

    def line(self) -> str:
        return tr("common.import_pending", folder=self.folder, source=self.journal.source, started=self.journal.started)


def journal_path(folder: Path | str) -> Path:
    return Path(folder) / JOURNAL


def write_journal(folder: Path | str, journal: Journal) -> None:
    write_atomic(journal_path(folder), (json.dumps(asdict(journal), indent=1) + "\n").encode("utf-8"))


def read_journal(folder: Path | str) -> Journal | None:
    try:
        data = json.loads(journal_path(folder).read_text(encoding="utf-8"))
        return Journal(**{k: v for k, v in data.items() if k in Journal.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return None


def _listed(registry: Path | None) -> list[str]:
    try:
        data = json.loads(Path(registry).read_text(encoding="utf-8")) if registry else []
    except (OSError, ValueError):
        return []
    return [str(f) for f in data] if isinstance(data, list) else []


def _save_listed(registry: Path, folders: list[str]) -> None:
    try:
        Path(registry).parent.mkdir(parents=True, exist_ok=True)
        write_atomic(Path(registry), (json.dumps(folders, indent=1) + "\n").encode("utf-8"))
    except OSError:
        pass  # the journal in the folder still says it; only the offer at the next start is lost


def register(registry: Path | None, folder: Path | str) -> None:
    if registry and str(Path(folder).resolve()) not in _listed(registry):
        _save_listed(registry, [*_listed(registry), str(Path(folder).resolve())])


def unregister(registry: Path | None, folder: Path | str) -> None:
    now = str(Path(folder).resolve())
    if registry and now in _listed(registry):
        _save_listed(registry, [f for f in _listed(registry) if f != now])


def finish(folder: Path | str, registry: Path | None) -> None:
    """The import ended well: the journal and the registry line go."""
    try:
        journal_path(folder).unlink()
    except OSError:
        pass
    unregister(registry, folder)


def pending(registry: Path | None) -> list[Pending]:
    """The half-made imports the registry lists whose folder still holds its journal; the others are dropped."""
    found, keep = [], []
    for name in _listed(registry):
        journal = read_journal(name)
        if journal is not None:
            found.append(Pending(Path(name), journal))
            keep.append(name)
    if registry and keep != _listed(registry):
        _save_listed(registry, keep)
    return found


def valid_manifest(folder: Path | str) -> dict[str, tuple[int, str]]:
    """The manifest lines of a cut-short import that read whole (a last line cut in the middle is dropped and the file
    cut there): ``mod path -> (size, sha256)``."""
    path = Path(folder) / importmeta.FILES_FILE
    good: dict[str, tuple[int, str]] = {}
    keep = 0
    try:
        with path.open("rb") as fh:
            for raw in fh:
                try:
                    rel, size, digest = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    break
                if not raw.endswith(b"\n"):
                    break
                good[rel] = (size, digest)
                keep += len(raw)
        with path.open("r+b") as fh:
            fh.truncate(keep)
    except OSError:
        pass
    return good


def resume(folder: Path | str, settings=None, say: Say = lambda _t: None, context=None, resumable: bool = True):
    """Run the import of ``folder`` again where it stopped; returns the ``ImportReport``. Raises ``ValueError`` (a
    sentence) when the folder holds no journal or its source is gone."""
    from . import projectimport as pi

    folder = Path(folder)
    journal = read_journal(folder)
    if journal is None:
        raise pi.ImportRefused(tr("common.import_resume_none", folder=folder))
    source = Path(journal.source)
    if not source.exists():
        raise pi.ImportRefused(tr("common.import_source_missing", path=source))
    options = pi.Options(journal.link, journal.hashed, resumable, context, resume=True, existed=journal.existed)
    started = time.monotonic()
    report = pi.run(source, folder, settings, say, options)
    report.seconds = time.monotonic() - started
    return report


def discard(folder: Path | str, registry: Path | None = None) -> bool:
    """Remove a half-made import (only a folder that holds its journal: anything else is not ours to remove). The mod
    is never touched; links go with the folder. Returns whether something was removed."""
    from . import projectimport as pi

    journal = read_journal(folder)
    if journal is None:
        return False
    pi.clear(Path(folder), remove=not journal.existed)
    unregister(registry, folder)
    return True
