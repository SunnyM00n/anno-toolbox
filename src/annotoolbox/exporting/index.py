"""The index an export writes beside its files: ``index.json`` (a list of records) and ``index.csv`` (one row a
source file): the source path, its kind, the archive it came from, its size, the files made, the status, the notes.

``write_index(out, entries)`` is the entry; a game file that is itself called ``index.json`` keeps its name and the
index is written as ``export-index.json``. docs/exporting.md "The index"."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

FIELDS = ("source", "kind", "archive", "size", "files", "status", "notes")


def record(entry) -> dict:
    """An entry as the plain dict the index files hold."""
    return {
        "source": entry.source,
        "kind": entry.kind,
        "archive": entry.archive,
        "size": entry.size,
        "files": list(entry.files),
        "status": entry.status,
        "notes": list(entry.notes),
    }


def index_name(entries) -> str:
    """``index``, or ``export-index`` when the export wrote a game file of that name at the output root."""
    taken = {f.lower() for e in entries for f in e.files}
    return "export-index" if taken & {"index.json", "index.csv"} else "index"


def write_index(out: Path, entries) -> None:
    """Write ``index.json`` and ``index.csv`` into ``out`` (UTF-8, LF)."""
    rows = [record(e) for e in entries]
    name = index_name(entries)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8", newline="\n")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(FIELDS)
    for r in rows:
        writer.writerow(
            [
                r["source"],
                r["kind"],
                r["archive"],
                r["size"],
                "; ".join(r["files"]),
                r["status"],
                " | ".join(r["notes"]),
            ]
        )
    (out / f"{name}.csv").write_text(buf.getvalue(), encoding="utf-8", newline="\n")
