"""The balancing view's data and the table files: many assets against chosen values as rows and columns, written
as CSV and read back into the change set; the texts of one language the same way. Stdlib only.

``grid(doc, guids, columns)`` gives the rows (each value with where it comes from); ``export_csv`` / ``import_csv``
write and read the table (a cell that differs from the asset's value becomes an edit, an empty cell changes
nothing); ``export_texts`` / ``import_texts`` the texts. ``docs/tool-suite.md`` A6.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from ..infra.i18n import tr
from .assetdoc import AssetDocument


@dataclass(frozen=True)
class Cell:
    text: str
    origin: str  # values.OWN, BASE, TEMPLATE, DEFAULT, NONE
    was: str | None = None  # the game's own value, where this change set changed the cell


def grid(doc: AssetDocument, guids: list[int], columns: list[str]) -> list[tuple[int, str, list[Cell]]]:
    """(GUID, name, a cell per column) for each asset; a column is a ``Values``-relative path."""
    rows = []
    for guid in guids:
        layers = doc.layers(guid)
        original = doc.layers(guid, original=True) if guid in doc.changes.assets else None
        cells = []
        for col in columns:
            path = tuple(col.split("/"))
            found = layers.lookup(path)
            was = original.lookup(path).text if original is not None else found.text
            cells.append(Cell(found.text, found.origin, was if was != found.text else None))
        rows.append((guid, doc.name(guid), cells))
    return rows


def export_csv(doc: AssetDocument, guids: list[int], columns: list[str]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["GUID", "Name", *columns])
    for guid, name, cells in grid(doc, guids, columns):
        w.writerow([guid, name, *(c.text for c in cells)])
    return out.getvalue()


def import_csv(doc: AssetDocument, text: str) -> list[str]:
    """The table's differing cells as edits (one undo step each); returns what could not be taken."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0][:2] != ["GUID", "Name"]:
        return [tr("assets.first_row_must_guid_name")]
    columns, problems = rows[0][2:], []
    for n, row in enumerate(rows[1:], 2):
        try:
            guid = int(row[0])
        except (ValueError, IndexError):
            problems.append(tr("assets.row_no_guid", n=n))
            continue
        if not doc.exists(guid):
            problems.append(tr("assets.row_no_asset", n=n, guid=guid))
            continue
        layers = doc.layers(guid)
        for col, value in zip(columns, row[2:], strict=False):
            if value != "" and value != layers.lookup(tuple(col.split("/"))).text:
                doc.set_value(guid, col, value)
    return problems


def export_texts(texts: dict[str, str], edited: dict[str, str] | None = None) -> str:
    """Text id and text, the edits over the game's; a word count is the caller's (``words``)."""
    merged = {**texts, **(edited or {})}
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Id", "Text"])
    for tid in sorted(merged):
        w.writerow([tid, merged[tid]])
    return out.getvalue()


def import_texts(doc: AssetDocument, language: str, text: str, game_texts: dict[str, str]) -> int:
    """The texts that differ from the game's as edits; returns how many."""
    count = 0
    for row in list(csv.reader(io.StringIO(text)))[1:]:
        if len(row) >= 2 and row[0] and game_texts.get(row[0]) != row[1]:
            if doc.changes.texts.get(language, {}).get(row[0]) != row[1]:
                doc.set_text(language, row[0], row[1])
                count += 1
    return count


def words(texts: dict[str, str]) -> int:
    return sum(len(t.split()) for t in texts.values())
