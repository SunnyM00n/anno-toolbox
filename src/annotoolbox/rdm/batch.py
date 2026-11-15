"""One command over many files: inputs (files or folders, searched recursively) paired with their outputs, each job
run on its own, nothing overwritten without ``force``, every failure reported with its reason.

``jobs`` pairs the inputs with outputs (a folder input keeps its tree under the output folder), ``run`` runs a
function that returns a job's files and notes, and writes them all or none; ``summary`` is the closing line.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Job:
    source: Path
    target: Path  # the main output file (a job may write more beside it)


@dataclass
class Result:
    job: Job
    status: str = "written"  # written, skipped (the work had nothing to write), exists, failed
    written: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""


def jobs(inputs: list[Path], out: Path | None, suffixes: tuple[str, ...], new_suffix: str) -> list[Job]:
    """Every input file with one of ``suffixes`` and the output it gets: ``out`` itself for a single file when it has
    a suffix, else a file named after the input in ``out`` (or beside the input), the folder tree kept."""
    found: list[Job] = []
    for path in inputs:
        if path.is_dir():
            files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)
            base = out if out is not None else path
            found += [Job(p, (base / p.relative_to(path)).with_suffix(new_suffix)) for p in files]
        elif path.is_file():
            if out is not None and out.suffix and len(inputs) == 1:
                found.append(Job(path, out))
            else:
                found.append(Job(path, (out or path.parent) / (path.stem + new_suffix)))
        else:
            raise FileNotFoundError(path)
    return found


Work = Callable[[Job], tuple[dict[Path, bytes], list[str]]]


def run(
    todo: list[Job], work: Work, force: bool = False, report: Callable[[Result], None] | None = None
) -> list[Result]:
    """Run ``work`` on every job; a job's files are written only when none of them exists (or ``force``)."""
    results = []
    for job in todo:
        r = Result(job)
        try:
            files, r.notes = work(job)
            there = [p for p in files if p.exists()]
            if not files:
                r.status = "skipped"
            elif there and not force:
                r.status, r.error = "exists", f"{there[0]} exists (--force overwrites)"
            else:
                for path, data in files.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                r.written = list(files)
        except (ValueError, OSError, KeyError, IndexError) as exc:
            r.status, r.error = "failed", str(exc) or type(exc).__name__
        results.append(r)
        if report:
            report(r)
    return results


def summary(results: list[Result]) -> str:
    counts = {s: sum(r.status == s for r in results) for s in ("written", "skipped", "exists", "failed")}
    head = f"{len(results)} files: {counts['written']} written, {counts['skipped']} skipped"
    return head + f", {counts['exists']} not overwritten, {counts['failed']} failed"
