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

from .model import ADDED, CHANGED, REMOVED, CTarget, SConflict, SFileChange, SReport

HEADER = ("kind", "file", "key", "path", "before", "after", "mods", "detail")
SHOWN = "(none)"
CONFLICT_ROWS = 300  # the summary lists this many conflicts
TARGETS_PER_FILE, VALUES_PER_TARGET = 25, 8  # the summary lists this many targets of a file, values of a target
FILE_GROUPS, GROUP_DEPTH = 60, 3  # file groups listed (the largest first), folders deep
DROPPED_LINES = 500
WIDTH = 300  # the summary shortens a value to this many characters; the full report keeps it whole


def _cell(text: str | None) -> str:
    return (text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def _value(text: str | None, width_chars: int | None = None) -> str:
    if not text:
        return "-"
    if width_chars is not None and len(text) > width_chars:
        text = text[:width_chars] + "..."
    return "`" + text.replace("`", "'") + "`"


def _conflict_values(conflict: SConflict, width_chars: int | None = None) -> str:
    return "; ".join(
        f"{mod}: {_value(value, width_chars)}" for mod, value in zip(conflict.mods, conflict.values, strict=True)
    )


def _more(count: int, what: str) -> str:
    return f"... and {count:,} more {what} (the full report and the CSV have them all)"


def markdown(report: SReport, full: bool = False) -> str:
    """The comparison as Markdown text (``full``: every target, value and file; else the summary)."""
    return "".join(markdown_lines(report, full))


def markdown_lines(report: SReport, full: bool = False) -> Iterator[str]:
    """The Markdown line by line, each with its newline: the header and counts, the conflicts, the changes by file, the
    files, what the loader would drop."""
    counts = report.counts()
    yield from _lines([f"# {report.title}", "", f"- Game: {report.game}"])
    if report.left or report.right:
        yield f"- Compared: {report.left} -> {report.right}\n"
    yield "- Mods in load order: " + (", ".join(report.mods) or SHOWN) + "\n"
    shown = ", ".join(f"{count:,} {k}" for k, count in counts.items() if count)
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


def _conflicts(conflicts: list[SConflict], full: bool) -> Iterator[str]:
    if not conflicts:
        yield SHOWN + "\n"
        return
    yield "| Level | Kind | Target | Path | Mods and values in load order | Winner | Decided by |\n"
    yield "|---|---|---|---|---|---|---|\n"
    shown = conflicts if full else conflicts[:CONFLICT_ROWS]
    for conflict in shown:
        who = conflict.file if not conflict.key else f"{conflict.key} {conflict.name}".strip()
        values = _cell(_conflict_values(conflict, None if full else WIDTH))
        yield (
            f"| {conflict.level} | {conflict.kind} | {_cell(who)} | {_cell(conflict.path)} | {values} | {conflict.winner} | "
            f"{conflict.decided} |\n"
        )
    if len(shown) < len(conflicts):
        yield "\n" + _more(len(conflicts) - len(shown), "conflicts") + "\n"


def _changes(targets: list[CTarget], full: bool) -> Iterator[str]:
    if not targets:
        yield SHOWN + "\n"
    for file, group in groupby(targets, key=lambda target: target.m_file):
        found = list(group)
        kinds = Counter(target.m_kind for target in found)
        yield f"### `{file}` ({', '.join(f'{count:,} {k}' for k, count in kinds.items())})\n\n"
        shown = found if full else found[:TARGETS_PER_FILE]
        for target in shown:
            yield from _lines(target_lines(target, None if full else VALUES_PER_TARGET, None if full else WIDTH))
        if len(shown) < len(found):
            yield _more(len(found) - len(shown), "targets in this file") + "\n"
        yield "\n"


def target_lines(target: CTarget, values: int | None = None, width_chars: int | None = None) -> list[str]:
    """One target as Markdown lines: its title, kind and mods, then each value change (``values``: at most this many,
    each shortened to ``width`` characters)."""
    who = f" ({', '.join(target.m_mods)})" if target.m_mods else ""
    out = [f"- **{target.title}** {target.m_kind}{who}" + (f", {target.m_template}" if target.m_template else "")]
    shown = target.m_changes if values is None else target.m_changes[:values]
    for conflict in shown:
        out.append(
            f"  - `{conflict.path}`: {_value(conflict.before, width_chars)} -> {_value(conflict.after, width_chars)}"
        )
    if len(shown) < len(target.m_changes):
        out.append(f"  - ... and {len(target.m_changes) - len(shown):,} more values")
    return out


def file_groups(files: list[SFileChange], depth: int = GROUP_DEPTH) -> list[tuple[str, str, int, int]]:
    """The files by folder (``depth`` levels) and kind: ``(folder, kind, files, bytes)``, the most files first."""
    found: dict[tuple[str, str], list[int]] = {}
    for file_change in files:
        folder = (
            "/".join(file_change.path.split("/")[:depth])
            if file_change.path.count("/") >= depth
            else file_change.path.rpartition("/")[0]
        )
        slot = found.setdefault((folder + "/" if folder else "", file_change.kind), [0, 0])
        slot[0] += 1
        slot[1] += file_change.size_bytes
    return sorted(
        ((folder, k, count, byte_count) for (folder, k), (count, byte_count) in found.items()),
        key=lambda group: (-group[2], group[0], group[1]),
    )


def _size(byte_count: int) -> str:
    return (
        f"{byte_count / 1e6:,.1f} MB"
        if byte_count >= 1e6
        else f"{byte_count / 1e3:,.1f} kB"
        if byte_count >= 1e3
        else f"{byte_count} bytes"
    )


def _files(files: list[SFileChange], full: bool) -> Iterator[str]:
    if not files:
        yield SHOWN + "\n"
    elif full:
        for file_change in files:
            yield (
                f"- `{file_change.path}`: {file_change.kind}, {file_change.size_bytes} bytes"
                + (f" ({', '.join(file_change.mods)})" if file_change.mods else "")
                + "\n"
            )
    else:
        groups = file_groups(files)
        for folder, kind, count, size_bytes in groups[:FILE_GROUPS]:
            yield f"- `{folder or '.'}`: {count:,} file(s), {kind}, {_size(size_bytes)}\n"
        if len(groups) > FILE_GROUPS:
            yield _more(len(groups) - FILE_GROUPS, "folders") + "\n"


def csv_iter(report: SReport) -> Iterator[list[str]]:
    """One row per conflict, target, value change and file (``HEADER``): what a spreadsheet filters."""
    yield list(HEADER)
    for conflict in report.conflicts:
        yield [conflict.level, conflict.file, conflict.key, conflict.path, "", _conflict_values(conflict).replace("`", ""), ", ".join(conflict.mods),
               f"{conflict.kind}; winner {conflict.winner} ({conflict.decided})"]  # fmt: skip
    for target in report.targets:
        mods = ", ".join(target.m_mods)
        if target.m_kind in (ADDED, REMOVED) or not target.m_changes:
            yield [
                target.m_kind,
                target.m_file,
                target.m_key,
                "",
                "",
                "",
                mods,
                f"{target.m_name} {target.m_template}".strip(),
            ]
        for conflict in target.m_changes:
            yield ["value", target.m_file, target.m_key, conflict.path, conflict.before or "", conflict.after or "", mods,
                   target.m_kind if target.m_kind != CHANGED else target.m_name]  # fmt: skip
    for file_change in report.files:
        yield [
            "file",
            file_change.path,
            "",
            "",
            "",
            "",
            ", ".join(file_change.mods),
            f"{file_change.kind}; {file_change.size_bytes} bytes",
        ]
    for line in report.dropped:
        yield ["dropped", "", "", "", "", "", "", line]


def csv_rows(report: SReport) -> list[list[str]]:
    return list(csv_iter(report))


def write(report: SReport, path: Path, full: bool = False) -> None:
    """The report to ``path``: ``.csv`` as CSV (UTF-8 with a mark, so a spreadsheet reads it), else Markdown (``full``:
    every target and file; the CSV is always complete)."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream, lineterminator="\n").writerows(csv_iter(report))
    else:
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.writelines(markdown_lines(report, full))
