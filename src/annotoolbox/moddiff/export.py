"""A comparison written out: Markdown (a report to attach to a bug or a mod page) or CSV (one row per value change,
conflict, file change and added or removed target, for a spreadsheet). ``docs/mod-diff.md`` "The views".

The Markdown is a **summary** by default (the conflicts, the first targets of each file, the files grouped by folder:
a mod that adds a hundred thousand assets and half a million files stays a page long) and **full** on request (every
target, value and file); the CSV is always complete. Both are written line by line (``markdown_lines``, ``csv_iter``), so
no text of the size of the report is ever held. ``write(report, path)`` picks the format by the extension (``.csv``, else
Markdown).
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Iterator
from itertools import groupby
from pathlib import Path

from .model import ADDED, CHANGED, REMOVED, Conflict, FileChange, Report, Target

HEADER = ("kind", "file", "key", "path", "before", "after", "mods", "detail")
SHOWN = "(none)"
CONFLICT_ROWS = 300  # the summary lists this many conflicts
TARGETS_PER_FILE, VALUES_PER_TARGET = 25, 8  # the summary lists this many targets of a file, values of a target
FILE_GROUPS, GROUP_DEPTH = 60, 3  # file groups listed (the largest first), folders deep
DROPPED_LINES = 500
WIDTH = 300  # the summary shortens a value to this many characters; the full report keeps it whole


def _cell(text: str | None) -> str:
    return (text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _value(text: str | None, width: int | None = None) -> str:
    if not text:
        return "-"
    if width is not None and len(text) > width:
        text = text[:width] + "..."
    return "`" + text.replace("`", "'") + "`"


def _conflict_values(c: Conflict, width: int | None = None) -> str:
    return "; ".join(f"{m}: {_value(v, width)}" for m, v in zip(c.mods, c.values, strict=True))


def _more(n: int, what: str) -> str:
    return f"... and {n:,} more {what} (the full report and the CSV have them all)"


def markdown(report: Report, full: bool = False) -> str:
    """The comparison as Markdown text (``full``: every target, value and file; else the summary)."""
    return "".join(markdown_lines(report, full))


def markdown_lines(report: Report, full: bool = False) -> Iterator[str]:
    """The Markdown line by line, each with its newline: the header and counts, the conflicts, the changes by file, the
    files, what the loader would drop."""
    counts = report.counts()
    yield from _lines([f"# {report.title}", "", f"- Game: {report.game}"])
    if report.left or report.right:
        yield f"- Compared: {report.left} -> {report.right}\n"
    yield "- Mods in load order: " + (", ".join(report.mods) or SHOWN) + "\n"
    shown = ", ".join(f"{n:,} {k}" for k, n in counts.items() if n)
    yield f"- Found: {shown or 'nothing'}\n"
    if not full:
        yield "- This is the summary; the full report lists every target, value and file\n"
    yield from _lines(["", "## Conflicts", ""])
    yield from _conflicts(report.conflicts, full)
    yield from _lines(["", "## Changes", ""])
    yield from _changes(report.targets, full)
    yield from _lines(["## Files", ""])
    yield from _files(report.files, full)
    yield from _lines(["", "## Dropped by the loader", ""])
    dropped = report.dropped if full else report.dropped[:DROPPED_LINES]
    yield from _lines([f"- {line}" for line in dropped] or [SHOWN])
    if len(dropped) < len(report.dropped):
        yield _more(len(report.dropped) - len(dropped), "lines") + "\n"
    yield from _lines([f"- {line}" for line in report.notes])


def _lines(lines: list[str]) -> Iterator[str]:
    for line in lines:
        yield line + "\n"


def _conflicts(conflicts: list[Conflict], full: bool) -> Iterator[str]:
    if not conflicts:
        yield SHOWN + "\n"
        return
    yield "| Level | Kind | Target | Path | Mods and values in load order | Winner | Decided by |\n"
    yield "|---|---|---|---|---|---|---|\n"
    shown = conflicts if full else conflicts[:CONFLICT_ROWS]
    for c in shown:
        who = c.file if not c.key else f"{c.key} {c.name}".strip()
        yield (
            f"| {c.level} | {c.kind} | {_cell(who)} | {_cell(c.path)} | {_cell(_conflict_values(c, None if full else WIDTH))} | {c.winner} | "
            f"{c.decided} |\n"
        )
    if len(shown) < len(conflicts):
        yield "\n" + _more(len(conflicts) - len(shown), "conflicts") + "\n"


def _changes(targets: list[Target], full: bool) -> Iterator[str]:
    if not targets:
        yield SHOWN + "\n"
    for file, group in groupby(targets, key=lambda t: t.file):
        found = list(group)
        kinds = Counter(t.kind for t in found)
        yield f"### `{file}` ({', '.join(f'{n:,} {k}' for k, n in kinds.items())})\n\n"
        shown = found if full else found[:TARGETS_PER_FILE]
        for t in shown:
            yield from _lines(target_lines(t, None if full else VALUES_PER_TARGET, None if full else WIDTH))
        if len(shown) < len(found):
            yield _more(len(found) - len(shown), "targets in this file") + "\n"
        yield "\n"


def target_lines(t: Target, values: int | None = None, width: int | None = None) -> list[str]:
    """One target as Markdown lines: its title, kind and mods, then each value change (``values``: at most this many,
    each shortened to ``width`` characters)."""
    who = f" ({', '.join(t.mods)})" if t.mods else ""
    out = [f"- **{t.title}** {t.kind}{who}" + (f", {t.template}" if t.template else "")]
    shown = t.changes if values is None else t.changes[:values]
    for c in shown:
        out.append(f"  - `{c.path}`: {_value(c.before, width)} -> {_value(c.after, width)}")
    if len(shown) < len(t.changes):
        out.append(f"  - ... and {len(t.changes) - len(shown):,} more values")
    return out


def file_groups(files: list[FileChange], depth: int = GROUP_DEPTH) -> list[tuple[str, str, int, int]]:
    """The files by folder (``depth`` levels) and kind: ``(folder, kind, files, bytes)``, the most files first."""
    found: dict[tuple[str, str], list[int]] = {}
    for f in files:
        folder = "/".join(f.path.split("/")[:depth]) if f.path.count("/") >= depth else f.path.rpartition("/")[0]
        slot = found.setdefault((folder + "/" if folder else "", f.kind), [0, 0])
        slot[0] += 1
        slot[1] += f.size
    return sorted(((d, k, n, b) for (d, k), (n, b) in found.items()), key=lambda g: (-g[2], g[0], g[1]))


def _size(n: int) -> str:
    return f"{n / 1e6:,.1f} MB" if n >= 1e6 else f"{n / 1e3:,.1f} kB" if n >= 1e3 else f"{n} bytes"


def _files(files: list[FileChange], full: bool) -> Iterator[str]:
    if not files:
        yield SHOWN + "\n"
    elif full:
        for f in files:
            yield f"- `{f.path}`: {f.kind}, {f.size} bytes" + (f" ({', '.join(f.mods)})" if f.mods else "") + "\n"
    else:
        groups = file_groups(files)
        for folder, kind, n, size in groups[:FILE_GROUPS]:
            yield f"- `{folder or '.'}`: {n:,} file(s), {kind}, {_size(size)}\n"
        if len(groups) > FILE_GROUPS:
            yield _more(len(groups) - FILE_GROUPS, "folders") + "\n"


def csv_iter(report: Report) -> Iterator[list[str]]:
    """One row per conflict, target, value change and file (``HEADER``): what a spreadsheet filters."""
    yield list(HEADER)
    for c in report.conflicts:
        yield [c.level, c.file, c.key, c.path, "", _conflict_values(c).replace("`", ""), ", ".join(c.mods),
               f"{c.kind}; winner {c.winner} ({c.decided})"]  # fmt: skip
    for t in report.targets:
        mods = ", ".join(t.mods)
        if t.kind in (ADDED, REMOVED) or not t.changes:
            yield [t.kind, t.file, t.key, "", "", "", mods, f"{t.name} {t.template}".strip()]
        for c in t.changes:
            yield ["value", t.file, t.key, c.path, c.before or "", c.after or "", mods,
                   t.kind if t.kind != CHANGED else t.name]  # fmt: skip
    for f in report.files:
        yield ["file", f.path, "", "", "", "", ", ".join(f.mods), f"{f.kind}; {f.size} bytes"]
    for line in report.dropped:
        yield ["dropped", "", "", "", "", "", "", line]


def csv_rows(report: Report) -> list[list[str]]:
    return list(csv_iter(report))


def write(report: Report, path: Path, full: bool = False) -> None:
    """The report to ``path``: ``.csv`` as CSV (UTF-8 with a mark, so a spreadsheet reads it), else Markdown (``full``:
    every target and file; the CSV is always complete)."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh, lineterminator="\n").writerows(csv_iter(report))
    else:
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(markdown_lines(report, full))
