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
from .assetdoc import CAssetDocument


@dataclass(frozen=True)
class SCell:
    text: str
    origin: str  # values.OWN, BASE, TEMPLATE, DEFAULT, NONE
    was: str | None = None  # the game's own value, where this change set changed the cell


def grid(doc: CAssetDocument, guids: list[int], columns: list[str]) -> list[tuple[int, str, list[SCell]]]:
    """(GUID, name, a cell per column) for each asset; a column is a ``Values``-relative path."""
    rows = []
    for guid in guids:
        layers = doc.layers(guid)
        original = doc.layers(guid, original=True) if guid in doc.m_changes.m_assets else None
        cells = []
        for column in columns:
            path = tuple(column.split("/"))
            found = layers.lookup(path)
            was = original.lookup(path).text if original is not None else found.text
            cells.append(SCell(found.text, found.origin, was if was != found.text else None))
        rows.append((guid, doc.name(guid), cells))
    return rows


def export_csv(doc: CAssetDocument, guids: list[int], columns: list[str]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["GUID", "Name", *columns])
    for guid, name, cells in grid(doc, guids, columns):
        writer.writerow([guid, name, *(cell.text for cell in cells)])
    return out.getvalue()


def import_csv(doc: CAssetDocument, text: str) -> list[str]:
    """The table's differing cells as edits (one undo step each); returns what could not be taken."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or rows[0][:2] != ["GUID", "Name"]:
        return [tr("assets.first_row_must_guid_name")]
    columns, problems = rows[0][2:], []
    for line_number, row in enumerate(rows[1:], 2):
        try:
            guid = int(row[0])
        except (ValueError, IndexError):
            problems.append(tr("assets.row_no_guid", n=line_number))
            continue
        if not doc.exists(guid):
            problems.append(tr("assets.row_no_asset", n=line_number, guid=guid))
            continue
        layers = doc.layers(guid)
        for column, value in zip(columns, row[2:], strict=False):
            if value != "" and value != layers.lookup(tuple(column.split("/"))).text:
                doc.set_value(guid, column, value)
    return problems


def export_texts(texts: dict[str, str], edited: dict[str, str] | None = None) -> str:
    """Text id and text, the edits over the game's; a word count is the caller's (``words``)."""
    merged = {**texts, **(edited or {})}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Id", "Text"])
    for text_id in sorted(merged):
        writer.writerow([text_id, merged[text_id]])
    return out.getvalue()


def import_texts(doc: CAssetDocument, language: str, text: str, game_texts: dict[str, str]) -> int:
    """The texts that differ from what the document shows now (an edit, else the game's) as edits; returns how many.
    A text put back to the game's wording replaces the earlier edit."""
    count = 0
    for row in list(csv.reader(io.StringIO(text)))[1:]:
        if len(row) >= 2 and row[0]:
            shown = doc.m_changes.m_texts.get(language, {}).get(row[0], game_texts.get(row[0]))
            if shown != row[1]:
                doc.set_text(language, row[0], row[1])
                count += 1
    return count


def words(texts: dict[str, str]) -> int:
    return sum(len(text.split()) for text in texts.values())
