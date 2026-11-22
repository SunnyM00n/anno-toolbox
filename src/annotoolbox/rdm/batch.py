"""One command over many files: inputs (files or folders, searched recursively) paired with their outputs, each job
run on its own, nothing overwritten without ``force``, every failure reported with its reason.

``jobs`` pairs the inputs with outputs (a folder input keeps its tree under the output folder), ``run`` runs a
function that returns a job's files and notes, and writes them all or none; ``summary`` is the closing line.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SJob:
    source: Path
    target: Path  # the main output file (a job may write more beside it)


@dataclass
class SResult:
    job: SJob
    status: str = "written"  # written, skipped (the work had nothing to write), exists, failed
    written: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""


def jobs(inputs: list[Path], out: Path | None, suffixes: tuple[str, ...], new_suffix: str) -> list[SJob]:
    """Every input file with one of ``suffixes`` and the output it gets: ``out`` itself for a single file when it has
    a suffix, else a file named after the input in ``out`` (or beside the input), the folder tree kept."""
    found: list[SJob] = []
    for path in inputs:
        if path.is_dir():
            files = sorted(
                file_path
                for file_path in path.rglob("*")
                if file_path.is_file() and file_path.suffix.lower() in suffixes
            )
            base = out if out is not None else path
            found += [
                SJob(file_path, (base / file_path.relative_to(path)).with_suffix(new_suffix)) for file_path in files
            ]
        elif path.is_file():
            if out is not None and out.suffix and len(inputs) == 1:
                found.append(SJob(path, out))
            else:
                found.append(SJob(path, (out or path.parent) / (path.stem + new_suffix)))
        else:
            raise FileNotFoundError(path)
    return found


Work = Callable[[SJob], tuple[dict[Path, bytes], list[str]]]


def write_all(files: dict[Path, bytes]) -> None:
    """Every file written, or none: all are staged in a temporary folder first, then moved to their places; a failed
    move removes what this call already placed (and the folders it made)."""
    with tempfile.TemporaryDirectory(prefix="rdm-batch-") as staging:
        staged = []
        for i, (path, data) in enumerate(files.items()):
            part = Path(staging) / str(i)
            part.write_bytes(data)
            staged.append((part, path))
        made: list[Path] = []
        placed: list[Path] = []
        try:
            for part, path in staged:
                for folder in reversed([path.parent, *path.parent.parents]):
                    if not folder.exists():
                        made.append(folder)
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(part, path)
                placed.append(path)
        except OSError:
            for path in placed:
                path.unlink(missing_ok=True)
            for folder in reversed(made):
                try:
                    folder.rmdir()
                except OSError:
                    pass
            raise


def run(
    todo: list[SJob], work: Work, force: bool = False, report: Callable[[SResult], None] | None = None
) -> list[SResult]:
    """Run ``work`` on every job; a job's files are written only when none of them exists (or ``force``)."""
    results = []
    for job in todo:
        result = SResult(job)
        try:
            files, result.notes = work(job)
            there = [path for path in files if path.exists()]
            if not files:
                result.status = "skipped"
            elif there and not force:
                result.status, result.error = "exists", f"{there[0]} exists (--force overwrites)"
            else:
                write_all(files)
                result.written = list(files)
        except (ValueError, OSError, KeyError, IndexError) as error:
            result.status, result.error = "failed", str(error) or type(error).__name__
        results.append(result)
        if report:
            report(result)
    return results


def summary(results: list[SResult]) -> str:
    counts = {
        status: sum(result.status == status for result in results)
        for status in ("written", "skipped", "exists", "failed")
    }
    head = f"{len(results)} files: {counts['written']} written, {counts['skipped']} skipped"
    return head + f", {counts['exists']} not overwritten, {counts['failed']} failed"
